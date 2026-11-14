"""Flashcard quality and study tools for the learning hub. Everything here is deterministic code (no model call):

* quality: cards that depend on "the text", are too long, or whose answer is not in the lesson they came from are dropped;
* cloze cards (a sentence with the key term blanked out) are normalised from the shapes models write;
* each card is tagged with the book page of the sentence it came from;
* scheduling previews ("Good: 6 days"), undo, leech detection, a daily limit on new cards, and the statistics shown on the Cards tab.
The SM-2 scheduling itself stays in learning.py."""
from __future__ import annotations

import re
from typing import Any

META = re.compile(r"\b(?:the (?:text|passage|lesson|author|book|chapter|section) (?:says?|states?|mentions?|describes?|explains?|notes?|argues?|claims?)|according to the (?:text|passage|lesson)|"
                  r"as (?:mentioned|described|stated|discussed) (?:above|earlier|in the (?:text|lesson))|in this (?:lesson|passage|text))\b", re.I)
CLOZE_TAG = re.compile(r"\{\{c\d+::(.+?)(?:::[^}]*)?\}\}")
BLANK = "[…]"
LEECH_LAPSES = 4
MAX_FRONT, MAX_BACK_WORDS = 220, 28


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9]{3,}", (text or "").lower())}


def normalize(item: Any) -> dict | None:
    """One card in the shape the app stores: {kind, front, back, hint}. Accepts {{c1::term}} cloze markup and '____' blanks."""
    if not isinstance(item, dict) or not isinstance(item.get("front"), str):
        return None
    front, back = item["front"].strip(), str(item.get("back") or "").strip()
    hint = str(item.get("hint") or "").strip()[:120]
    kind = "cloze" if str(item.get("type") or item.get("kind") or "").lower() == "cloze" or CLOZE_TAG.search(front) else "basic"
    if CLOZE_TAG.search(front):
        answers = CLOZE_TAG.findall(front)
        back = back or ", ".join(a.strip() for a in answers)
        front = CLOZE_TAG.sub(BLANK, front)
    elif kind == "cloze":
        front = re.sub(r"_{3,}", BLANK, front)
        if BLANK not in front:
            return None  # a "cloze" card with no blank is just a statement
    if not front or not back:
        return None
    return {"kind": kind, "front": front[:MAX_FRONT * 2 if kind == "cloze" else MAX_FRONT], "back": back[:420], "hint": hint}


def grounded(card: dict, source: str, need: float = 0.5) -> bool:
    """Is the card's answer made of words that appear in the lesson it came from? (Cheap check against a model inventing facts.)"""
    words = _words(card["back"])
    if len(words) < 2:
        return True  # a one-word answer is judged by its question, which must overlap the lesson instead
    return len(words & _words(source)) / len(words) >= need


def quality(cards: list[dict], source: str) -> tuple[list[dict], list[str]]:
    """-> (cards worth keeping, reasons for the ones dropped)."""
    keep, dropped = [], []
    src_words = _words(source)
    for c in cards:
        card = normalize(c)
        if not card:
            dropped.append("malformed")
        elif META.search(card["front"] + " " + card["back"]):
            dropped.append("depends on 'the text'")
        elif len(card["back"].split()) > MAX_BACK_WORDS:
            dropped.append("answer too long")
        elif len(card["front"]) < 8 or card["front"].lower().strip(" ?") == card["back"].lower().strip(" ."):
            dropped.append("question too short or same as answer")
        elif not grounded(card, source):
            dropped.append("answer not found in the lesson")
        elif card["kind"] == "basic" and not (_words(card["front"]) & src_words):
            dropped.append("question unrelated to the lesson")
        else:
            keep.append(card)
    return keep, dropped


def find_page(source: str, card: dict) -> int | None:
    """The page of the lesson sentence that best matches the card, from the '(p. 12)' citations the lesson carries."""
    want = _words(card["back"] + " " + card["front"])
    best, best_n = None, 0
    protected = re.sub(r"\(\s*pp?\.\s*(\d+)", r"(p#\1", source)  # "(p. 12)" must not be cut in half by the sentence splitter
    for sent in re.split(r"(?<=[.!?])\s+|\n+", protected):
        m = re.search(r"\(\s*p#(\d+)", sent)
        if not m:
            continue
        n = len(want & _words(sent))
        if n > best_n:
            best, best_n = int(m.group(1)), n
    return best if best_n >= 2 else None


def fmt_interval(seconds: float) -> str:
    s = max(0.0, seconds)
    if s < 3600:
        return f"{max(1, round(s / 60))} min"
    if s < 86400:
        return f"{round(s / 3600)} h"
    d = round(s / 86400)
    if d < 14:
        return f"{d} day{'' if d == 1 else 's'}"
    if d < 60:
        return f"{round(d / 7)} weeks"
    if d < 540:
        return f"{round(d / 30)} months"
    return f"{d / 365:.1f} years"


def is_leech(card: dict) -> bool:
    return int(card.get("lapses") or 0) >= LEECH_LAPSES


def maturity(card: dict) -> str:
    if int(card.get("suspended") or 0):
        return "suspended"
    if int(card.get("reps") or 0) == 0 and not card.get("last_reviewed"):
        return "new"
    iv = int(card.get("interval") or 0)
    return "learning" if iv < 7 else "young" if iv < 21 else "mature"
