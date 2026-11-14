"""Offline speech-to-text (faster-whisper). Dictation only: audio in, editable text out.

* The model is loaded for one request and released afterwards (Settings: stt_keep), so it never sits in VRAM.
* Device comes from the GPU manager: CUDA only if CUDA really works and enough VRAM is free *now* (Ollama keeps its
  models; we never touch them), otherwise CPU with int8. A CUDA failure at load/run time retries on CPU.
* Language is auto-detected unless a hint is given. "Hinglish" mode asks for Hindi but biases Whisper toward Latin letters.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from . import gpu

SIZES = {"tiny": 400, "base": 500, "small": 1100, "medium": 2400, "large-v3-turbo": 2600}  # rough fp16 VRAM need (MB)
DEFAULT_SIZE = "small"
HINGLISH_PROMPT = "Aaj mausam bahut accha hai, chalo bahar chalte hain. Mujhe ek report chahiye, kal tak bhej do."
MAX_SECONDS = 600


class VoiceError(RuntimeError):
    pass


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


_FILLER = re.compile(r"(?i)(?:,\s*)?(?<![\w'])(?:u+m+|u+h+|e+r+m*|h+m+|m+h*m+|a+h+m+|you know|i mean)(?![\w'])[,.]?\s*")


def clean_text(text: str) -> str:
    """Remove filler words and tidy spacing/capitalisation. Conservative: only clearly non-lexical fillers."""
    t = _FILLER.sub(" ", text)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    return t


def _add_cuda_dlls() -> None:
    """ctranslate2 needs cuBLAS/cuDNN; PyTorch's CUDA wheel ships them, so point Windows' loader at torch/lib."""
    try:
        import torch
        lib = Path(torch.__file__).parent / "lib"
        if lib.exists() and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def _load(size: str, device: str):
    from faster_whisper import WhisperModel
    key = f"whisper:{size}:{device}"
    obj, _ = gpu.get(key)
    if obj is not None:
        return obj, key
    if device == "cuda":
        _add_cuda_dlls()
    m = WhisperModel(size, device=device, compute_type="float16" if device == "cuda" else "int8", cpu_threads=max(2, (os.cpu_count() or 4) // 2))
    gpu.register(key, m, device, SIZES.get(size, 1100) if device == "cuda" else 0)
    return m, key


def _run(model, path: str, language: Optional[str], hinglish: bool):
    segs, info = model.transcribe(
        path, language=language, task="transcribe", vad_filter=True, beam_size=5,
        initial_prompt=HINGLISH_PROMPT if hinglish else None, condition_on_previous_text=False,
    )
    return list(segs), info  # generator: decoding actually happens here


def transcribe(path: str, *, language: str = "auto", hinglish: bool = False, cleanup: bool = False,
               size: str = DEFAULT_SIZE, prefer: str = "auto", keep: bool = False) -> dict:
    """Blocking (run it in a thread). Returns the structured result; raises VoiceError for user-facing problems."""
    if not available():
        raise VoiceError("Speech recognition needs the faster-whisper package (see README).")
    if size not in SIZES:
        raise VoiceError(f"Unknown speech model “{size}”. Choose one of: {', '.join(SIZES)}.")
    p = Path(path)
    if not p.exists() or p.stat().st_size < 200:
        raise VoiceError("The recording is empty.")
    lang = None if language in ("", "auto", None) else language
    if hinglish:
        lang = "hi"
    t0 = time.perf_counter()
    with gpu.task("speech-to-text", need_mb=SIZES[size], prefer=prefer, timeout=300) as info:
        device, reason = info["device"], info["reason"]
        try:
            model, key = _load(size, device)
            segs, meta = _run(model, str(p), lang, hinglish)
        except Exception as exc:
            gpu.unload(f"whisper:{size}:{device}")
            if device != "cuda":
                raise VoiceError(f"Could not transcribe: {type(exc).__name__}: {exc}")
            gpu.unload(f"whisper:{size}:cuda")  # CUDA path failed (missing DLLs, out of memory...) -> CPU
            device, reason = "cpu", f"CUDA failed ({type(exc).__name__}: {str(exc)[:80]}); used CPU"
            try:
                model, key = _load(size, "cpu")
                segs, meta = _run(model, str(p), lang, hinglish)
            except Exception as exc2:
                gpu.unload(f"whisper:{size}:cpu")
                raise VoiceError(f"Could not transcribe: {type(exc2).__name__}: {exc2}")
        if not keep:
            gpu.unload(key)  # release memory right away
        info["device"], info["reason"] = device, reason
    text = " ".join(s.text.strip() for s in segs).strip()
    if cleanup:
        text = clean_text(text)
    return {
        "text": text, "language": meta.language, "language_probability": round(float(meta.language_probability or 0), 3),
        "duration_s": round(float(meta.duration or 0), 2), "model": size, "device": device, "reason": reason,
        "segments": [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segs],
        "ms": round((time.perf_counter() - t0) * 1000), "cleaned": bool(cleanup),
    }
