"""Slash-command parser. Exactly five commands exist; everything else about the task is understood from the request."""
from __future__ import annotations

import re
from typing import Optional

COMMANDS = ("planning", "compare", "analyze", "create", "learn")
_RX = re.compile(r"^\s*/(planning|compare|analy[sz]e|create|learn)\b[\s:,-]*(?P<rest>.*)$", re.I | re.S)

SUGGESTIONS = [
    {"command": "/planning", "hint": "Trips, study timetables, outings, events, schedules and routines",
     "examples": ["Plan a 5-day trip to Goa", "Create a study timetable for my semester exams", "Find movies and cinema shows this weekend"]},
    {"command": "/compare", "hint": "Compare products, universities, offers or options side by side",
     "examples": ["Compare smartphones under ₹30,000", "Compare three universities for a master's degree"]},
    {"command": "/analyze", "hint": "Analyze an attached spreadsheet, dataset or document",
     "examples": ["Analyze this Excel sales dataset", "Find trends and anomalies in this CSV file"]},
    {"command": "/create", "hint": "Create documents, presentations, dashboards, code and more",
     "examples": ["Create an executive business dashboard", "Generate a PowerPoint presentation about AI agents"]},
    {"command": "/learn", "hint": "A personalised course, explanation, quiz or interview preparation",
     "examples": ["Teach me Python from beginner to advanced", "Prepare me for a SQL interview"]},
]


def parse(text: str) -> Optional[tuple[str, str]]:
    """'/Planning plan a trip' -> ('planning', 'plan a trip'). None when the message doesn't start with one of the five commands."""
    m = _RX.match(text or "")
    if not m:
        return None
    cmd = m.group(1).lower().replace("analyse", "analyze")
    return cmd, m.group("rest").strip()
