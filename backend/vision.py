"""Optional local vision: route an uploaded image to the right operation and return a structured result.

No separate vision model is loaded. If the chat model can see images (qwen3.5 can) it is asked directly, so no extra VRAM is
used; text extraction always goes through the small OCR engine first, and its text is handed to the model as extra evidence.
Without a vision-capable model the result is OCR text only, and says so.
"""
from __future__ import annotations

import asyncio
import base64
import time

from . import llm, ocr

TASKS = {
    "describe": "Describe this image in 3-5 sentences: the main subject, setting, notable details and colours. Be factual; do not guess names of people.",
    "screenshot": "This is a screenshot. Say which application or website it appears to be, what is on screen, and any error messages or key values. Quote text exactly.",
    "diagram": "This is a diagram or flowchart. List its components and how they connect, in reading order, as a short numbered list.",
    "chart": "This is a chart or graph. State the chart type, the axes and units, the series, and the main trend with approximate values. Say when a value is hard to read.",
    "document": "This is a document image. Summarise what kind of document it is and its key fields (dates, names, totals). Quote values exactly as written.",
    "objects": "List the distinct objects and visual elements you can see, most prominent first, as a comma-separated list.",
    "formula": ocr.LATEX_PROMPT,
}


async def analyze(s: dict, model: str, data: bytes, mime: str, task: str = "describe", question: str = "") -> dict:
    t0 = time.perf_counter()
    if task not in TASKS:
        raise ValueError(f"Unknown task. Use one of: {', '.join(TASKS)}")
    out: dict = {"task": task, "ocr_text": "", "answer": "", "used_model": False, "notes": []}
    if ocr.available():
        try:
            r = await asyncio.to_thread(ocr.ocr_image_bytes, data)
            out["ocr_text"] = r["text"]
            out["ocr_confidence"] = r["confidence"]
        except ocr.OcrError as exc:
            out["notes"].append(str(exc))
    caps = await llm.capabilities(s, model) if model else {}
    sees = caps.get("vision") if caps.get("vision") is not None else s.get("provider") == "openai"
    if sees:
        prompt = question.strip() or TASKS[task]
        if out["ocr_text"] and task != "formula":
            prompt += "\n\n(Text detected in the image by OCR, which may contain mistakes:\n" + out["ocr_text"][:1500] + ")"
        b64 = base64.b64encode(data).decode()
        msg = llm.user_message(s, prompt, [(mime or "image/png", b64)])
        out["answer"] = (await llm.complete(s, model, [msg], max_tokens=500, num_ctx=4096)).strip()
        out["used_model"] = True
        if task == "formula":
            out["latex"] = ocr.clean_latex(out["answer"])
            out["notes"].append("LaTeX was produced by the vision model, not a dedicated formula recogniser: check it before relying on it.")
    else:
        out["notes"].append("The selected model can't view images, so only the OCR text is available. Pick a vision-capable model for descriptions.")
        if task == "formula":
            out["latex"] = []
            out["formula_lines"] = ocr.find_formulas(out["ocr_text"])
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out
