"""Feedback -> stored preferences. This does NOT train the model: it saves small, readable preferences that later runs read
(defaults for questions, level of detail, number of charts) and that the user can review, edit and delete."""
from __future__ import annotations

import re
import time
from typing import Optional

from .. import db

KEY_OK = re.compile(r"^[a-z][a-z0-9_.]{2,40}$")
# (pattern, key, value): deterministic rules for the common requests, so feedback works even if the model is slow or wrong
RULES = [
    (re.compile(r"\b(?:shorter|brief|concise|less text|too long|summary only)\b", re.I), "style.detail", "brief"),
    (re.compile(r"\b(?:more detail|detailed|in[- ]depth|thorough|too short|longer)\b", re.I), "style.detail", "detailed"),
    (re.compile(r"\b(?:more|extra|include)\s+(?:charts?|graphs?|visuals?|visuali[sz]ations?)\b", re.I), "output.charts", "more"),
    (re.compile(r"\b(?:fewer|less|no)\s+(?:charts?|graphs?|visuals?)\b", re.I), "output.charts", "fewer"),
    (re.compile(r"\bprefer\s+(?:to\s+)?(?:travel(?:l?ing)?\s+(?:by\s+)?)?(trains?|flights?|planes?|buses|bus|car|road)\b", re.I), "travel.transport", None),
    (re.compile(r"\b(?:prefer|like|want)\s+(?:budget|cheap|luxury|premium|mid[- ]range)\s+(?:hotels?|stays?|accommodation)\b|\b(budget|luxury|mid[- ]range)\s+(?:hotels?|stays?)\b", re.I), "travel.stay", None),
    (re.compile(r"\bbudget\b.{0,40}\b(?:inaccurate|too low|too high|wrong|off|underestimat\w+)\b", re.I), "budget.buffer_pct", "15"),
    (re.compile(r"\b(?:use|show|prefer)\s+(?:km|kilometres|kilometers)\b", re.I), "units.distance", "km"),
    (re.compile(r"\bbeginner[- ]friendly|explain (?:it )?simply|simpler\b", re.I), "learn.pace", "simple"),
    (re.compile(r"\b(?:harder|more challenging|advanced)\s+(?:exercises?|questions?|quizzes)\b", re.I), "learn.difficulty", "harder"),
]
NORMALIZE = {"train": "train", "trains": "train", "flight": "flight", "flights": "flight", "plane": "flight", "planes": "flight",
             "bus": "bus", "buses": "bus", "car": "car", "road": "car"}


def _upsert(skill: str, key: str, value: str, source: str) -> dict:
    with db.conn() as c:
        c.execute("INSERT INTO skill_prefs(skill, key, value, source, created_at) VALUES(?,?,?,?,?) "
                  "ON CONFLICT(skill, key) DO UPDATE SET value=excluded.value, source=excluded.source, created_at=excluded.created_at",
                  (skill, key, value[:160], source[:300], time.time()))
        return dict(c.execute("SELECT * FROM skill_prefs WHERE skill=? AND key=?", (skill, key)).fetchone())


def rule_extract(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for rx, key, value in RULES:
        m = rx.search(text or "")
        if m:
            v = value
            if v is None:
                raw = next((g for g in m.groups() if g), "").lower()
                v = NORMALIZE.get(raw, raw)
            out[key] = v
    return out


async def learn(rc_like, skill: str, text: str, use_model: bool = True) -> list[dict]:
    """Save the preferences found in a piece of feedback. Returns the saved rows."""
    text = (text or "").strip()
    if len(text) < 4:
        return []
    found = rule_extract(text)
    if not found and use_model and rc_like is not None:
        data = await rc_like.json_call(
            "A user gave feedback about an AI assistant's result. Extract REUSABLE preferences as JSON: "
            '{"preferences": [{"key": "area.name", "value": "short value"}]} using lowercase keys like style.detail, output.charts, '
            "travel.transport. Return an empty list if the text states no lasting preference.\n\nFEEDBACK: " + text[:500], max_tokens=200)
        for p in (data or {}).get("preferences", [])[:4] if isinstance(data, dict) else []:
            if isinstance(p, dict) and KEY_OK.match(str(p.get("key", ""))) and 0 < len(str(p.get("value", ""))) <= 120:
                found[str(p["key"])] = str(p["value"])
    rows = [_upsert("*" if key.split(".")[0] in ("style", "units", "output") else skill, key, v, text) for key, v in found.items()]
    if not rows:  # nothing structured: keep the note itself so the feedback is not lost, and visible for the user to edit or delete
        rows = [_upsert(skill, f"note.{int(time.time())}", text[:160], text)]
    return rows


def list_prefs() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM skill_prefs ORDER BY skill, key")]


def delete_pref(pid: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM skill_prefs WHERE id=?", (pid,))


def update_pref(pid: int, value: str) -> Optional[dict]:
    with db.conn() as c:
        c.execute("UPDATE skill_prefs SET value=? WHERE id=?", (value.strip()[:160], pid))
        r = c.execute("SELECT * FROM skill_prefs WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def prefs_for(skill: str) -> dict[str, str]:
    """Global preferences overlaid by the ones for this skill."""
    out: dict[str, str] = {}
    with db.conn() as c:
        for r in c.execute("SELECT skill, key, value FROM skill_prefs WHERE skill IN ('*', ?) ORDER BY skill='*' DESC", (skill,)):
            out[r["key"]] = r["value"]
    return out
