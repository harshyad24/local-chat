"""GPU resource manager for application-owned models (embeddings, OCR, speech...).

Rules it enforces:
  * Ollama / LM Studio and everything else on the GPU are *external*: we only read their VRAM use (via nvidia-smi),
    never unload or kill them.
  * Only one heavy application model task runs at a time (a lock + a small priority queue), so we never pile
    several models into 6 GB.
  * A task asks for a device with an estimated VRAM need. It gets "cuda" only if CUDA really works and enough VRAM
    is free right now (minus a safety margin); otherwise it gets "cpu" and the reason is recorded.
  * Models the app loads are registered, can be listed, and are unloaded after an idle timeout or on demand.
"""
from __future__ import annotations

import gc
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager

SAFETY_MB = 700          # always leave this much VRAM for the desktop / Ollama growth
IDLE_UNLOAD_S = 300      # unload app-managed models that haven't been used for this long

_lock = threading.RLock()            # one heavy task at a time
_state_lock = threading.Lock()
_loaded: dict[str, dict] = {}        # name -> {"obj", "device", "vram_mb", "last_used", "loaded_at"}
_current: dict = {"task": None, "device": None, "since": None}
_waiting = 0
_history: list[dict] = []
_torch_info: dict | None = None


# ---------------------------------------------------------------- detection
def _smi(query: str) -> list[list[str]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, f"--query-gpu={query}", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return []
        return [[c.strip() for c in line.split(",")] for line in out.stdout.strip().splitlines() if line.strip()]
    except Exception:
        return []


def _smi_apps() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-compute-apps=process_name,used_memory", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        rows = []
        for line in out.stdout.strip().splitlines():
            if "," in line:
                n, m = line.rsplit(",", 1)
                rows.append({"process": n.strip(), "vram_mb": int(m.strip()) if m.strip().isdigit() else None})
        return rows
    except Exception:
        return []


def torch_cuda() -> dict:
    """Does PyTorch exist and can it actually run on CUDA? (cached; verified with a real kernel, not just is_available())."""
    global _torch_info
    if _torch_info is not None:
        return _torch_info
    info = {"installed": False, "cuda": False, "version": None, "cuda_version": None, "error": None}
    try:
        import torch
        info.update(installed=True, version=torch.__version__, cuda_version=torch.version.cuda)
        if torch.cuda.is_available():
            x = torch.ones(256, 256, device="cuda")
            torch.cuda.synchronize()
            info["cuda"] = bool((x @ x).sum().item() > 0)
        else:
            info["error"] = "torch.cuda.is_available() is False (CPU-only PyTorch build, or driver mismatch)"
    except ImportError:
        info["error"] = "PyTorch is not installed"
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    _torch_info = info
    return info


def status() -> dict:
    rows = _smi("name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,driver_version")
    gpu = None
    if rows and len(rows[0]) >= 7:
        n, tot, used, free, util, temp, drv = rows[0][:7]
        num = lambda v: int(float(v)) if v.replace(".", "", 1).isdigit() else None  # noqa: E731
        gpu = {"name": n, "total_mb": num(tot), "used_mb": num(used), "free_mb": num(free), "utilization": num(util), "temp_c": num(temp), "driver": drv}
    own_mb = sum(m["vram_mb"] or 0 for m in _loaded.values() if m["device"] == "cuda")
    with _state_lock:
        return {
            "gpu": gpu,
            "torch": torch_cuda(),
            "app_models": [{"name": k, "device": v["device"], "vram_mb": v["vram_mb"], "idle_s": round(time.time() - v["last_used"])} for k, v in _loaded.items()],
            "app_vram_mb": own_mb,
            "external": _smi_apps(),  # Ollama, LM Studio, browsers... read-only, we never touch these
            "task": dict(_current), "queued": _waiting,
            "recent": _history[-8:],
        }


def free_vram_mb() -> int | None:
    rows = _smi("memory.free")
    try:
        return int(float(rows[0][0])) if rows else None
    except Exception:
        return None


# ---------------------------------------------------------------- device choice
def choose_device(need_mb: int, prefer: str = "auto") -> tuple[str, str]:
    """('cuda'|'cpu', reason). 'cpu' preference is always honoured; 'cuda' falls back to cpu when it can't be used safely."""
    if prefer == "cpu":
        return "cpu", "CPU requested"
    t = torch_cuda()
    if not t["cuda"]:
        return "cpu", t["error"] or "CUDA unavailable"
    free = free_vram_mb()
    if free is None:
        return "cpu", "could not read free VRAM"
    if free - SAFETY_MB < need_mb:
        return "cpu", f"only {free} MB VRAM free (need ~{need_mb} MB + {SAFETY_MB} MB margin)"
    return "cuda", f"{free} MB VRAM free"


# ---------------------------------------------------------------- model registry
def register(name: str, obj, device: str, vram_mb: int = 0) -> None:
    with _state_lock:
        _loaded[name] = {"obj": obj, "device": device, "vram_mb": vram_mb, "last_used": time.time(), "loaded_at": time.time()}


def get(name: str):
    with _state_lock:
        m = _loaded.get(name)
        if m:
            m["last_used"] = time.time()
            return m["obj"], m["device"]
    return None, None


def unload(name: str | None = None) -> list[str]:
    """Drop one (or every) application-managed model and free its memory. Never touches external servers."""
    with _state_lock:
        names = [name] if name else list(_loaded)
        gone = [n for n in names if _loaded.pop(n, None) is not None]
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return gone


def unload_idle(max_idle_s: float = IDLE_UNLOAD_S) -> list[str]:
    now = time.time()
    with _state_lock:
        stale = [n for n, m in _loaded.items() if now - m["last_used"] > max_idle_s]
    out = []
    for n in stale:
        out += unload(n)
    return out


def unload_cuda_models(except_name: str | None = None) -> list[str]:
    """Free GPU models of ours to make room for another task."""
    with _state_lock:
        names = [n for n, m in _loaded.items() if m["device"] == "cuda" and n != except_name]
    out = []
    for n in names:
        out += unload(n)
    return out


# ---------------------------------------------------------------- scheduling
@contextmanager
def task(name: str, need_mb: int = 0, prefer: str = "auto", timeout: float = 120.0):
    """Run one heavy task. Serialises with other GPU tasks; yields a dict {"device", "reason"} describing where it runs.

        with gpu.task("embed", need_mb=600) as t:
            model = load(t["device"]) ...
    """
    global _waiting
    with _state_lock:
        _waiting += 1
    got = _lock.acquire(timeout=timeout)
    with _state_lock:
        _waiting -= 1
    if not got:
        raise TimeoutError(f"The GPU queue is busy (waited {timeout:.0f}s for “{name}”).")
    t0 = time.perf_counter()
    info = {"device": "cpu", "reason": ""}
    try:
        device, reason = choose_device(need_mb, prefer)
        if device == "cpu" and prefer != "cpu" and torch_cuda()["cuda"] and _loaded:
            # maybe our own idle models are what's using the VRAM: free them and look again
            if unload_cuda_models():
                device, reason = choose_device(need_mb, prefer)
        info.update(device=device, reason=reason)
        with _state_lock:
            _current.update(task=name, device=device, since=time.time())
        yield info
    finally:
        ms = round((time.perf_counter() - t0) * 1000)
        with _state_lock:
            _current.update(task=None, device=None, since=None)
            _history.append({"task": name, "device": info["device"], "ms": ms, "reason": info["reason"], "at": time.time()})
            del _history[:-50]
        _lock.release()
