"""Reusable parts for workflows: grounded extraction, deep links (search pages, never bookings), ICS calendars, Word / PowerPoint builders."""
from __future__ import annotations

import io
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote, quote_plus

from .. import verify


# ------------------------------------------------------------------ grounded extraction
def _in_text(value: Any, text_norm: str) -> bool:
    """A value the model reports must appear in the page it was read from (numbers as whole tokens). Otherwise it is dropped."""
    v = str(value or "").strip()
    if not v:
        return False
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", v)
    if nums:
        return all(re.search(r"(?<![\d.])" + re.escape(n.replace(",", "")) + r"(?![\d])", text_norm) for n in nums)
    return verify._norm(v) in text_norm


async def extract_items(rc, text: str, instruction: str, fields: dict[str, str], check: list[str], limit: int = 8, max_tokens: int = 900) -> list[dict]:
    """Ask the local model for a JSON list of items found in `text` and keep only the items whose `check` fields really occur in the text.
    fields: name -> description. Values of the other fields that don't occur in the text are blanked, never invented."""
    if len(text.strip()) < 80:
        return []
    schema = ", ".join(f'"{k}": {d}' for k, d in fields.items())
    data = await rc.json_call(
        f"{instruction}\nUse ONLY the text below. Copy names, prices and times exactly as written there; use null when the text does not say. "
        f'Reply with JSON only: {{"items": [{{{schema}}}]}} with at most {limit} items.\n\nTEXT:\n{text[:7000]}', max_tokens=max_tokens)
    items = data.get("items") if isinstance(data, dict) else data if isinstance(data, list) else []
    tn = verify._norm(text)
    out = []
    for it in (items or [])[:limit * 2]:
        if not isinstance(it, dict):
            continue
        if not all(_in_text(it.get(k), tn) for k in check):
            continue
        clean = {k: (it.get(k) if it.get(k) not in ("", "null", "N/A", "n/a") and (k in check or _in_text(it.get(k), tn)) else None) for k in fields}
        out.append(clean)
    return out[:limit]


# ------------------------------------------------------------------ links: pre-filled search pages on the provider's own site
def link_flights(origin: str, dest: str, when: str = "") -> str:
    return "https://www.google.com/travel/flights?q=" + quote_plus(f"Flights from {origin} to {dest}" + (f" on {when}" if when else ""))


def link_trains(origin: str, dest: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(f"trains from {origin} to {dest}")


def link_buses(origin: str, dest: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(f"buses from {origin} to {dest} book")


def link_hotels(dest: str, checkin: str = "", checkout: str = "", adults: int = 2) -> str:
    q = f"https://www.booking.com/searchresults.html?ss={quote(dest)}&group_adults={adults}"
    return q + (f"&checkin={checkin}&checkout={checkout}" if checkin and checkout else "")


def link_maps(place: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(place)


def link_movies(city: str) -> str:
    return "https://in.bookmyshow.com/explore/movies-" + re.sub(r"[^a-z0-9]+", "-", city.lower()).strip("-")


def link_search(q: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(q)


# ------------------------------------------------------------------ calendar (.ics works in Google, Outlook and Apple calendars)
def _ics_escape(t: str) -> str:
    return str(t).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def make_ics(events: list[dict], name: str = "Plan") -> str:
    """events: {title, start: datetime|date, end: datetime|date, location?, description?}. A date (not datetime) makes an all-day event."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LocalChat//Skills//EN", "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_ics_escape(name)}"]
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for e in events:
        s, en = e["start"], e.get("end") or e["start"]
        lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4().hex}@localchat", f"DTSTAMP:{stamp}"]
        if isinstance(s, datetime):
            lines += [f"DTSTART:{s.strftime('%Y%m%dT%H%M%S')}", f"DTEND:{(en if isinstance(en, datetime) else s + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{s.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{((en if isinstance(en, date) else s) + timedelta(days=1)).strftime('%Y%m%d')}"]
        lines.append(f"SUMMARY:{_ics_escape(e['title'])}")
        if e.get("location"):
            lines.append(f"LOCATION:{_ics_escape(e['location'])}")
        if e.get("description"):
            lines.append(f"DESCRIPTION:{_ics_escape(e['description'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def csv_text(header: list[str], rows: list[list[Any]]) -> str:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


# ------------------------------------------------------------------ documents
def build_docx(title: str, sections: list[dict], subtitle: str = "") -> bytes:
    """sections: [{heading, paragraphs: [str], bullets: [str], table?: {columns, rows}}]"""
    from docx import Document
    from docx.shared import Pt
    d = Document()
    d.styles["Normal"].font.name = "Calibri"
    d.styles["Normal"].font.size = Pt(11)
    d.add_heading(title, 0)
    if subtitle:
        d.add_paragraph(subtitle).italic = True
    for sec in sections:
        if sec.get("heading"):
            d.add_heading(str(sec["heading"]), 1)
        for p in sec.get("paragraphs") or []:
            d.add_paragraph(str(p))
        for b in sec.get("bullets") or []:
            d.add_paragraph(str(b), style="List Bullet")
        tb = sec.get("table")
        if tb and tb.get("columns"):
            t = d.add_table(rows=1, cols=len(tb["columns"]))
            t.style = "Light Grid Accent 1"
            for i, c in enumerate(tb["columns"]):
                t.rows[0].cells[i].text = str(c)
            for r in tb.get("rows", [])[:60]:
                cells = t.add_row().cells
                for i, v in enumerate(r[: len(cells)]):
                    cells[i].text = str(v)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def build_pptx(title: str, slides: list[dict], subtitle: str = "") -> bytes:
    """slides: [{title, bullets: [str], notes?: str}]. Clean 16:9 layout, readable on a projector."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    dark, accent = RGBColor(0x1F, 0x2A, 0x44), RGBColor(0x2E, 0x86, 0xDE)

    def textbox(slide, x, y, w, h, text, size, bold=False, color=dark):
        tf = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = text
        p.font.size, p.font.bold, p.font.color.rgb = Pt(size), bold, color
        return tf

    s0 = prs.slides.add_slide(prs.slide_layouts[6])
    bg = s0.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = dark
    bg.line.fill.background()
    textbox(s0, 0.9, 2.5, 11.5, 1.8, title, 44, True, RGBColor(255, 255, 255))
    if subtitle:
        textbox(s0, 0.9, 4.4, 11.5, 1, subtitle, 20, False, RGBColor(0xB8, 0xC4, 0xDC))
    for sl in slides:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        bar = s.shapes.add_shape(1, 0, 0, Inches(0.25), prs.slide_height)
        bar.fill.solid()
        bar.fill.fore_color.rgb = accent
        bar.line.fill.background()
        textbox(s, 0.8, 0.5, 11.8, 1.1, str(sl.get("title", ""))[:90], 32, True)
        tf = textbox(s, 0.9, 1.8, 11.5, 5, "", 20)
        bullets = [str(b)[:180] for b in (sl.get("bullets") or [])][:7]
        for i, b in enumerate(bullets):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = "• " + b
            p.font.size, p.font.color.rgb = Pt(22 if len(bullets) <= 5 else 19), dark
            p.space_after = Pt(10)
        if sl.get("notes"):
            s.notes_slide.notes_text_frame.text = str(sl["notes"])
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def md_from_sections(title: str, sections: list[dict]) -> str:
    out = [f"# {title}", ""]
    for s in sections:
        if s.get("heading"):
            out += [f"## {s['heading']}", ""]
        out += [str(p) + "\n" for p in s.get("paragraphs") or []]
        out += ["- " + str(b) for b in s.get("bullets") or []]
        out.append("")
    return "\n".join(out)


def fmt_money(v: Optional[float], cur: str = "") -> str:
    if v is None:
        return "—"
    sym = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}.get(cur, "")
    return f"{sym}{v:,.0f}" + ("" if sym or not cur else f" {cur}")


def parse_price(text: Any) -> Optional[float]:
    """'₹1,299 per night' -> 1299.0; None when there is no number."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", str(text or ""))
    return float(m.group(1).replace(",", "")) if m else None
