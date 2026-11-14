"""Intent detection inside a skill: keywords first (instant, no model), one validated model call only when the keywords can't decide."""
from __future__ import annotations

import re
from typing import Optional

from .registry import Workflow, for_skill, general_of


def score(wf: Workflow, text: str) -> int:
    return len(wf.keywords.findall(text or ""))


def rank(skill: str, text: str) -> list[tuple[int, Workflow]]:
    return sorted(((score(w, text), w) for w in for_skill(skill)), key=lambda x: -x[0])


async def route(rc, skill: str, text: str) -> tuple[Workflow, str]:
    """-> (workflow, how) where how is 'keywords' | 'model' | 'default'."""
    ws = for_skill(skill)
    if len(ws) == 1:
        return ws[0], "keywords"
    ranked = rank(skill, text)
    top, runner = ranked[0], ranked[1]
    if top[0] > 0 and top[0] > runner[0]:
        return top[1], "keywords"
    ids = [w.id for w in ws]
    desc = "\n".join(f"- {w.id}: {w.description}" for w in ws)
    data = await rc.json_call(
        f'Which kind of task is this request? Reply with JSON only: {{"workflow": "<id>"}} using one id from the list.\n\n{desc}\n\nREQUEST: {text[:400]}', max_tokens=40)
    wid = str(data.get("workflow", "")).strip().lower() if isinstance(data, dict) else ""
    if wid in ids:
        return next(w for w in ws if w.id == wid), "model"
    return general_of(skill), "default"  # type: ignore[return-value]
