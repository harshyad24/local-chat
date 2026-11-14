"""Local OCR (RapidOCR / PP-OCR models on ONNX Runtime, CPU) for images and PDFs, plus formula extraction.

Why CPU: the OCR models are small (about 15 MB) and fast enough on the CPU (a page takes a second or two), and it keeps the
6 GB of VRAM free for the chat model. PDFs use the embedded text layer when there is one and only OCR the pages that don't.
Formulas: lines that look mathematical are picked out, and (when a vision-capable chat model is available) converted to LaTeX
by that model, with the result clearly marked as model-generated. Hindi/Devanagari OCR is not supported by these models.
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path
from typing import Optional

MAX_PAGES = 40
MAX_PIXELS = 4000  # longest side we OCR; bigger pages are scaled down
_engine = None


class OcrError(RuntimeError):
    pass


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:
            raise OcrError(f"OCR needs the rapidocr-onnxruntime package ({exc}).")
        _engine = RapidOCR()
    return _engine


def release() -> None:
    global _engine
    _engine = None


# ---------------------------------------------------------------- layout: boxes -> reading-order text
def lines_to_text(items: list[dict]) -> str:
    """Group boxes into rows by vertical position; boxes far apart on a row become " | "-separated cells (tables)."""
    if not items:
        return ""
    heights = sorted(i["h"] for i in items)
    med = heights[len(heights) // 2] or 10
    rows: list[list[dict]] = []
    for it in sorted(items, key=lambda i: (i["cy"], i["x0"])):
        if rows and abs(it["cy"] - (sum(r["cy"] for r in rows[-1]) / len(rows[-1]))) < med * 0.6:
            rows[-1].append(it)
        else:
            rows.append([it])
    out = []
    for row in rows:
        row.sort(key=lambda i: i["x0"])
        parts, prev = [], None
        for it in row:
            if prev is not None:
                gap = it["x0"] - prev["x1"]
                parts.append(" | " if gap > med * 2.2 else " ")
            parts.append(it["text"])
            prev = it
        out.append("".join(parts).strip())
    return "\n".join(out)


def ocr_image_bytes(data: bytes) -> dict:
    """OCR one image. Returns {"text", "lines": [{text, confidence, box}], "confidence", "ms"}."""
    import numpy as np
    from PIL import Image
    t0 = time.perf_counter()
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise OcrError("That doesn't look like an image the app can open.")
    img = img.convert("RGB")
    scale = 1.0
    if max(img.size) > MAX_PIXELS:
        scale = MAX_PIXELS / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    result, _ = _get_engine()(np.array(img)[:, :, ::-1])
    items = []
    for box, text, conf in result or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        items.append({"text": str(text), "confidence": round(float(conf), 3), "box": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))],
                      "x0": min(xs), "x1": max(xs), "cy": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys)})
    confs = [i["confidence"] for i in items]
    return {
        "text": lines_to_text(items), "lines": [{k: i[k] for k in ("text", "confidence", "box")} for i in items],
        "confidence": round(sum(confs) / len(confs), 3) if confs else 0.0, "ms": round((time.perf_counter() - t0) * 1000),
    }


def ocr_pdf(path: str, max_pages: int = MAX_PAGES, dpi: int = 170) -> dict:
    try:
        import pymupdf as fitz  # PyMuPDF
    except Exception as exc:
        raise OcrError(f"PDF OCR needs PyMuPDF ({exc}).")
    t0 = time.perf_counter()
    try:
        doc = fitz.open(path)
    except Exception:
        raise OcrError("Could not open that PDF.")
    pages = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            layer = page.get_text("text").strip()
            if len(layer) >= 40:
                pages.append({"page": i + 1, "text": layer, "source": "text-layer", "confidence": 1.0})
                continue
            pix = page.get_pixmap(dpi=dpi)
            r = ocr_image_bytes(pix.tobytes("png"))
            pages.append({"page": i + 1, "text": r["text"], "source": "ocr", "confidence": r["confidence"]})
        count = len(doc)
    finally:
        doc.close()  # Windows can't delete a file that is still open
    return {"pages": pages, "page_count": count, "truncated": count > max_pages, "ms": round((time.perf_counter() - t0) * 1000)}


# ---------------------------------------------------------------- formulas
_MATH_CHARS = re.compile(r"[∑∫√±≤≥≈∞∂∇α-ωΔΣΠΩ×÷≠∈→]")
_EQUATION = re.compile(r"[A-Za-z0-9)\]²³]\s*(?:=|\^|_)\s*[-A-Za-z0-9(\\]")


def looks_math(line: str) -> bool:
    line = line.strip()
    if len(line) < 3 or len(line) > 160:
        return False
    if _MATH_CHARS.search(line):
        return True
    letters = sum(c.isalpha() for c in line)
    ops = sum(c in "=+-*/^<>" for c in line)
    return bool(_EQUATION.search(line)) and ops >= 1 and letters <= len(line) * 0.7


def find_formulas(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if looks_math(ln)][:20]


LATEX_PROMPT = (
    "Transcribe the mathematical formula or equation in this image into LaTeX. Output ONLY the LaTeX source, no explanation, "
    "no surrounding $ signs. If there are several formulas, put each on its own line. If you cannot read it, output UNREADABLE."
)


def clean_latex(raw: str) -> list[str]:
    raw = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", (raw or "").strip(), flags=re.I)
    out = []
    for ln in raw.splitlines():
        ln = ln.strip().strip("$").strip()
        ln = re.sub(r"^\\\[|\\\]$|^\\\(|\\\)$", "", ln).strip()
        if ln and ln.upper() != "UNREADABLE":
            out.append(ln)
    return out[:20]
