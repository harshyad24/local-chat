"""AI skills: /planning /compare /analyze /create /learn. See engine.py for the flow and workflows/ for the internal workflows."""
from __future__ import annotations

import re
from typing import Optional

from . import context as runs
from .parser import COMMANDS, SUGGESTIONS, parse  # noqa: F401
from . import registry  # noqa: F401
from . import engine, feedback  # noqa: F401


def load_workflows() -> None:
    """Import every module in workflows/ so each registers itself (adding a workflow = adding a file)."""
    import importlib
    import pkgutil
    from . import workflows
    for m in pkgutil.iter_modules(workflows.__path__):
        importlib.import_module(f"{__name__}.workflows.{m.name}")


_WANTS_DASH = re.compile(r"\b(?:dashboards?|kpis?|scorecards?)\b", re.I)
_WANTS_ANALYSIS = re.compile(r"\b(?:analy[sz]e|analy[sz]ing|analysis|insights?|statistics|stats|trends?|anomal\w+|correlat\w+|summari[sz]e (?:this|the) (?:data|sheet|file|dataset))\b", re.I)


def auto_route(text: str, table_attached: bool) -> Optional[tuple[str, str]]:
    """A plain message (no slash command) that asks for a dashboard or an analysis of a data file attached to it goes to the skill that
    computes it from the file. Small local models write dashboard code, or nothing, instead of using the file."""
    t = (text or "").strip()
    if not table_attached or not t or t.startswith("/"):
        return None
    if _WANTS_DASH.search(t):
        return "create", t
    if _WANTS_ANALYSIS.search(t):
        return "analyze", t
    return None


def detect(s: dict, text: str, chat_id: str, run_id: Optional[str] = None, answers: Optional[dict] = None, table_attached: bool = False) -> Optional[dict]:
    """Is this message a skill turn? -> the turn description for engine.drive(), or None for an ordinary chat message."""
    if not s.get("skills_enabled", True):
        return None
    if run_id:
        run = runs.get(run_id)
        if run and run["chat_id"] == chat_id:
            return {"skill": run["skill"], "request": run["request"], "text": text, "run": run, "answers": answers or {},
                    "action": (answers or {}).get("_action")}
    p = parse(text)
    auto = False
    if not p:
        p = auto_route(text, table_attached)
        auto = bool(p)
    if p:
        return {"skill": p[0], "request": p[1], "text": p[1], "run": None, "answers": {}, "action": None, "auto": auto}
    run = runs.latest(chat_id, ("asking",))
    if run and text.strip() and not text.lstrip().startswith("/"):
        return {"skill": run["skill"], "request": run["request"], "text": text, "run": run, "answers": {}, "action": None, "implicit": True}
    return None


load_workflows()
