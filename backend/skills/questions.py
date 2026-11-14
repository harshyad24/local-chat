"""The question engine: work out which facts are still missing and ask only for those.

Order of sources for a slot value: (1) deterministic parsing of the request, (2) what is already known (earlier answers, uploaded files,
saved preferences), (3) one validated JSON extraction call to the local model, (4) a question to the user. Answers like "skip" or
"anything is fine" fill a slot with 'flexible' so the workflow can go ahead.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .registry import Slot, Workflow

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
FLEX = re.compile(r"^\s*(?:skip|any(?:thing)?|flexible|no preference|doesn'?t matter|up to you|you choose|not sure|none|n/?a|whatever|whichever|don'?t care|surprise me|no idea)\b[\w\s',.!-]{0,24}$", re.I)


def _mon(s: str) -> int:
    return MONTHS[s[:3].lower()]


def parse_dates(text: str, today: Optional[date] = None) -> list[date]:
    """Every date mentioned: ISO, '15 Dec', 'Dec 15, 2026', '15/12/2026', 'tomorrow', 'this/next weekend'. Dates without a year are the next occurrence."""
    today = today or date.today()
    t = (text or "").lower()
    out: list[date] = []

    def add(y: Optional[int], m: int, d: int) -> None:
        try:
            dt = date(y or today.year, m, d)
            if y is None and dt < today:
                dt = date(today.year + 1, m, d)
            out.append(dt)
        except ValueError:
            pass

    for m in re.finditer(r"\b(20\d\d)-(\d\d)-(\d\d)\b", t):
        add(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"(?:[a-z]*)\.?(?:,?\s+(20\d\d))?\b", t):
        add(int(m.group(3)) if m.group(3) else None, _mon(m.group(2)), int(m.group(1)))
    for m in re.finditer(r"\b" + _MON + r"[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?\b", t):
        add(int(m.group(3)) if m.group(3) else None, _mon(m.group(1)), int(m.group(2)))
    for m in re.finditer(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d\d)\b", t):
        add(int(m.group(3)), int(m.group(2)), int(m.group(1)))  # day first (the user is in India)
    if "tomorrow" in t:
        out.append(today + timedelta(days=1))
    if re.search(r"\btoday\b|\btonight\b", t):
        out.append(today)
    sat = today + timedelta(days=(5 - today.weekday()) % 7)
    if re.search(r"\bnext weekend\b", t):
        out += [sat + timedelta(days=7), sat + timedelta(days=8)]
    elif re.search(r"\b(?:this )?weekend\b", t):
        if today.weekday() == 6:
            out += [today]
        else:
            out += [sat, sat + timedelta(days=1)]
    if re.search(r"\bnext week\b", t):
        mon = today + timedelta(days=7 - today.weekday())
        out += [mon, mon + timedelta(days=6)]
    return sorted(set(out))


def parse_number(text: Any) -> Optional[float]:
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text or "").lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|lakhs?|lacs?|l\b|m\b|million|cr(?:ores?)?)?", t)
    if not m:
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a couple": 2, "couple": 2}
        for w, v in words.items():
            if re.search(rf"\b{w}\b", t):
                return float(v)
        return None
    v = float(m.group(1))
    unit = (m.group(2) or "")
    mult = {"k": 1e3, "l": 1e5, "m": 1e6}.get(unit[:1], 1)
    if unit.startswith(("lakh", "lac")):
        mult = 1e5
    elif unit.startswith(("million",)):
        mult = 1e6
    elif unit.startswith("cr"):
        mult = 1e7
    return v * mult


def currency_of(text: str) -> str:
    t = text or ""
    for sym, code in (("₹", "INR"), ("rs", "INR"), ("inr", "INR"), ("$", "USD"), ("usd", "USD"), ("€", "EUR"), ("eur", "EUR"), ("£", "GBP"), ("gbp", "GBP")):
        if sym in t.lower():
            return code
    return ""


def split_list(text: Any) -> list[str]:
    if isinstance(text, list):
        return [str(x).strip() for x in text if str(x).strip()]
    parts = re.split(r"\s*(?:,|;|\n|\band\b|&)\s*", str(text or ""))
    return [p.strip(" .-•*") for p in parts if p.strip(" .-•*")]


def coerce(slot: Slot, value: Any) -> Any:
    """A value in the slot's type, or None when it can't be understood (so the question is asked again instead of guessing)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    k = slot.kind
    if k == "choice" and isinstance(value, str):  # "any train is fine" names a choice: that wins over the "any" at the start
        v = value.strip().lower()
        for c in slot.choices:
            if c.lower() != "flexible" and (v == c.lower() or c.lower() in v):
                return c
    if isinstance(value, str) and FLEX.match(value):
        return "flexible"
    if k == "number":
        n = parse_number(value)
        return n if n is not None and n >= 0 else None
    if k == "choice":
        v = str(value).strip().lower()
        for c in slot.choices:
            if v == c.lower() or v in c.lower() or c.lower() in v:
                return c
        return None
    if k == "list":
        items = split_list(value)
        return items or None
    if k == "date":
        ds = parse_dates(str(value))
        if ds:
            return {"start": ds[0].isoformat(), "end": ds[-1].isoformat()}
        return str(value).strip()[:60] or None
    if k == "bool":
        return str(value).strip().lower() in ("yes", "y", "true", "1", "yeah", "sure", "ok")
    if k == "file":
        return value if isinstance(value, (dict, str)) else None
    return str(value).strip()[:400]


def missing(wf: Workflow, slots: dict) -> list[Slot]:
    out = []
    for s in wf.slots:
        if not s.required or slots.get(s.name) not in (None, "", []):
            continue
        if s.ask_if and not s.ask_if(slots):
            continue
        out.append(s)
    return out


def build_questions(wf: Workflow, slots: dict, limit: int = 3) -> list[dict]:
    return [{"slot": s.name, "label": s.label, "question": s.question, "kind": s.kind, "choices": s.choices, "hint": s.hint,
             "default": s.default} for s in missing(wf, slots)[:limit]]


def apply_defaults(wf: Workflow, slots: dict) -> dict:
    out = dict(slots)
    for s in wf.slots:
        if out.get(s.name) in (None, "", []) and s.default is not None:
            out[s.name] = s.default
    return out


def merge(wf: Workflow, slots: dict, new: dict) -> dict:
    out = dict(slots)
    by = {s.name: s for s in wf.slots}
    for k, v in (new or {}).items():
        if k in by:
            c = coerce(by[k], v)
            if c is not None:
                out[k] = c
    return out


EXTRACT = (
    "Extract facts the user already gave. Reply with JSON only: an object whose keys are chosen from the list below and whose values are "
    "exactly what the user said (short). Leave out any key the text does not answer. Never guess.\n\nKEYS:\n{keys}\n\nTEXT:\n{text}")


async def llm_extract(rc, wf: Workflow, text: str, have: dict, only: Optional[list[Slot]] = None) -> dict:
    """One bounded JSON call to the local model; anything that isn't a known key or doesn't fit the slot type is dropped."""
    targets = [s for s in (only or wf.slots) if have.get(s.name) in (None, "", [])]
    if not targets or not (text or "").strip():
        return {}
    keys = "\n".join(f'- {s.name}: {s.label}' + (f" (one of: {', '.join(s.choices)})" if s.choices else "") + (f" - {s.hint}" if s.hint else "") for s in targets)
    data = await rc.json_call(EXTRACT.format(keys=keys, text=text[:1500]), max_tokens=300)
    out = {}
    if isinstance(data, dict):
        by = {s.name: s for s in targets}
        for k, v in data.items():
            if k in by:
                c = coerce(by[k], v)
                if c is not None:
                    out[k] = c
    return out


def answer_summary(wf: Workflow, answers: dict) -> str:
    by = {s.name: s for s in wf.slots}
    parts = []
    for k, v in answers.items():
        if k.startswith("_") or k not in by:
            continue
        if isinstance(v, dict) and "start" in v:
            v = v["start"] + (f" to {v['end']}" if v.get("end") and v["end"] != v["start"] else "")
        parts.append(f"{by[k].label}: {', '.join(v) if isinstance(v, list) else v}")
    return " · ".join(parts)
