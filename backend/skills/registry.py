"""Internal workflow definitions. A workflow is data (slots, keywords) plus one async function; the five commands never change
when a workflow is added: put a module in backend/skills/workflows/ that calls register(...)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass
class Slot:
    name: str
    label: str
    question: str
    kind: str = "text"            # text | number | choice | list | date | file | bool
    choices: list[str] = field(default_factory=list)
    required: bool = True
    default: Any = None
    hint: str = ""                # shown under the question and given to the extractor
    pref: str = ""                # preference key that can pre-fill it (e.g. "travel.transport")
    ask_if: Optional[Callable[[dict], bool]] = None  # only relevant when this is true for the slots collected so far


@dataclass
class Workflow:
    id: str
    skill: str
    title: str
    description: str
    keywords: re.Pattern
    slots: list[Slot]
    run: Callable[..., Awaitable[dict]]
    general: bool = False         # the fallback workflow of its skill
    uses_web: bool = False
    prefill: Optional[Callable[[str, dict], dict]] = None  # deterministic slot extraction from the request text (+ context)


REGISTRY: dict[str, Workflow] = {}


def register(wf: Workflow) -> Workflow:
    REGISTRY[f"{wf.skill}/{wf.id}"] = wf
    return wf


def for_skill(skill: str) -> list[Workflow]:
    return [w for k, w in REGISTRY.items() if w.skill == skill]


def get(skill: str, wid: str) -> Optional[Workflow]:
    return REGISTRY.get(f"{skill}/{wid}")


def general_of(skill: str) -> Optional[Workflow]:
    ws = for_skill(skill)
    return next((w for w in ws if w.general), ws[0] if ws else None)
