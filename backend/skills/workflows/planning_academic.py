"""/planning -> academic: an exam study timetable built by the scheduler (code), with priorities, revision, mock tests, a progress tracker,
an exam countdown and downloads (.ics for any calendar app, .csv, .docx). The model is only used to pull topics out of an uploaded syllabus."""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import common, questions, scheduler
from ..registry import Slot, Workflow, register
from ... import tools

_ITEM = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z0-9 &+/\-\.]{1,40}?)\s*(?:[:\-–(]\s*(?P<date>.+?)\)?)?\s*$")


def parse_subjects(items: list[str], today: date | None = None) -> list[dict]:
    """['Maths: 12 Dec', 'Physics - 15/12/2026', 'Chemistry'] -> [{name, exam?}]"""
    out = []
    for it in items:
        m = _ITEM.match(it)
        if not m:
            continue
        name = m.group("name").strip(" .")
        ds = questions.parse_dates(m.group("date") or "", today) if m.group("date") else []
        out.append({"name": name.title() if name.islower() else name, "exam": ds[0] if ds else None})
    return out


def prefill(text: str, ctx: dict) -> dict:
    out: dict = {}
    m = re.search(r"\b(?:subjects?|exams? in|for)\s*[:\-]?\s*((?:[A-Z][A-Za-z ]+(?:,|\band\b|&)\s*)+[A-Z][A-Za-z ]+)", text)
    if m:
        out["subjects"] = questions.split_list(m.group(1))
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\s*(?:a|per|each|/)?\s*day", text, re.I)
    if h:
        out["hours"] = h.group(1)
    for f in ctx.get("files") or []:
        if f.get("ext") in (".pdf", ".docx", ".txt", ".md"):
            out["syllabus"] = {"id": f["id"], "name": f["name"], "path": f["path"], "ext": f["ext"]}
    return out


async def syllabus_topics(rc, names: list[str], f: dict) -> dict[str, list[str]]:
    text = await asyncio.to_thread(tools._extract, Path(f["path"]))
    if not text or len(text) < 80:
        return {}
    d = await rc.json_call(f'From this syllabus, list the main topics/chapters for each of these subjects: {", ".join(names)}. Use only headings that appear in the text. '
                           'Reply with JSON only: {"subjects": {"<subject>": ["topic", "..."]}} with at most 12 topics per subject.\n\nSYLLABUS:\n' + text[:6000], max_tokens=900)
    out = {}
    tn = common.verify._norm(text)
    for k, v in ((d or {}).get("subjects") or {}).items() if isinstance(d, dict) else []:
        if isinstance(v, list):
            keep = [str(t)[:60] for t in v if common.verify._norm(str(t)) in tn][:12]
            if keep:
                out[str(k).lower()] = keep
    return out


async def run(rc) -> dict:
    s = rc.slots
    today = date.today()
    subs = parse_subjects([str(x) for x in (s.get("subjects") or [])], today)
    dates_text = "" if s.get("exam_dates") in (None, "flexible") else str(s["exam_dates"])
    pairs = []
    for line in re.split(r"[\n;]|,(?=\s*[A-Za-z])", dates_text):
        pairs += parse_subjects([line], today)
    by_name = {p["name"].lower(): p["exam"] for p in pairs if p["exam"]}
    for sub in subs:
        if not sub["exam"]:
            sub["exam"] = by_name.get(sub["name"].lower())
    if len(by_name) == 1 and sum(1 for x in subs if not x["exam"]) > 1:  # one date for all
        only = next(iter(by_name.values()))
        for sub in subs:
            sub["exam"] = sub["exam"] or only
    missing = [x["name"] for x in subs if not x["exam"]]
    if missing:
        return {"error": f"I could not find exam dates for: {', '.join(missing)}. Use Revise to add them (for example “Maths: 12 Dec, Physics: 15 Dec”)."}
    hours = float(s.get("hours") or 3)
    level_raw = str(s.get("level") or "average").lower()
    level = "weak" if "weak" in level_raw or "beginner" in level_raw else "strong" if "strong" in level_raw or "good" in level_raw else "average"
    hard = {x.lower() for x in questions.split_list(s.get("hard") if s.get("hard") not in (None, "flexible") else [])}
    start = date.today()
    sd = s.get("start")
    if isinstance(sd, dict) and sd.get("start"):
        start = max(start, date.fromisoformat(sd["start"]))
    await rc.progress(1, 4, "Reading the syllabus" if s.get("syllabus") else "Preparing your subjects")
    topics = await syllabus_topics(rc, [x["name"] for x in subs], s["syllabus"]) if isinstance(s.get("syllabus"), dict) else {}
    objs = [scheduler.Subject(x["name"], x["exam"], level, x["name"].lower() in hard or any(h in x["name"].lower() for h in hard), topics.get(x["name"].lower())) for x in subs]
    await rc.progress(2, 4, "Building the timetable")
    plan = scheduler.study_plan(objs, hours, start)
    if not plan["slots"]:
        return {"error": "All the exam dates are in the past, so there is nothing to schedule."}
    slots = plan["slots"]
    await rc.progress(3, 4, "Preparing calendar and downloads")
    counts = {}
    for sl in slots:
        if sl["kind"] in ("study", "revision", "mock"):
            counts[sl["subject"]] = counts.get(sl["subject"], 0) + 1
    total_h = sum(counts.values())
    first = min(o.exam for o in objs)
    cdown = [{"label": o.name, "date": o.exam.isoformat(), "days": (o.exam - today).days} for o in sorted(objs, key=lambda o: o.exam)]
    events = []
    for sl in slots:
        if sl["kind"] in ("study", "revision", "mock"):
            events.append({"title": f"{sl['kind'].title()}: {sl['subject']}" + (f" – {sl['topic']}" if sl["topic"] and sl["kind"] == "study" else ""),
                           "start": datetime.fromisoformat(sl["start"]), "end": datetime.fromisoformat(sl["end"])})
        elif sl["kind"] == "exam":
            events.append({"title": f"EXAM: {sl['subject']}", "start": date.fromisoformat(sl["date"]), "end": date.fromisoformat(sl["date"])})
    files = [rc.out_file("study_timetable.ics", common.make_ics(events, "Study timetable")),
             rc.out_file("study_timetable.csv", common.csv_text(["Date", "Time", "Subject", "Type", "Topic"], [[s_["date"], s_["time"], s_["subject"], s_["kind"], s_["topic"]] for s_ in slots])),
             rc.out_file("study_timetable.docx", common.build_docx("Study timetable", [
                 {"heading": "Exam dates", "bullets": [f"{c['label']}: {c['date']} ({c['days']} days)" for c in cdown]},
                 {"heading": "Sessions per subject", "bullets": [f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]},
                 {"heading": "Timetable", "table": {"columns": ["Date", "Time", "Subject", "Type", "Topic"], "rows": [[x["date"], x["time"], x["subject"], x["kind"], x["topic"]] for x in slots if x["kind"] != "rest"]}}]))]
    prio = sorted(objs, key=lambda o: -scheduler.LEVEL_WEIGHT[o.level] * (1.3 if o.hard else 1) / max(1, (o.exam - today).days))
    cal_events = [{"date": sl["date"], "title": (sl["subject"] if sl["kind"] != "mock" else "Mock: " + sl["subject"]), "kind": sl["kind"]} for sl in slots if sl["kind"] != "rest"]
    blocks = [{"type": "overview", "title": f"Study plan: {len(objs)} subjects, {plan['days']} days", "text": f"{hours:g} h/day from {start.isoformat()} until the last exam ({max(o.exam for o in objs).isoformat()}). Preparation level: {level}.",
               "kpis": [{"label": "Days to first exam", "value": (first - today).days}, {"label": "Study sessions", "value": total_h}, {"label": "Subjects", "value": len(objs)}, {"label": "Mock tests", "value": sum(1 for x in slots if x["kind"] == "mock")}]},
              {"type": "countdown", "items": cdown},
              {"type": "table", "title": "Subject priorities", "columns": ["Priority", "Subject", "Exam", "Sessions", "Why"],
               "rows": [[i + 1, o.name, o.exam.isoformat(), counts.get(o.name, 0), ", ".join(filter(None, [o.level + " preparation", "marked difficult" if o.hard else "", f"{(o.exam - today).days} days left"]))] for i, o in enumerate(prio)]},
              {"type": "timetable", "title": "Your next two weeks (tick sessions as you finish them)", "key": "study_done", "rows": scheduler.group_days(slots, 14)},
              {"type": "calendar", "title": "Calendar view", "events": cal_events}]
    notes = ["The timetable is a plan, not a guarantee: move sessions freely and re-run with Revise if your exam dates or free hours change."]
    if topics:
        notes.append("Topics were read from your uploaded syllabus (only headings that appear in it are used).")
    elif not s.get("syllabus"):
        notes.append("No syllabus was given, so sessions name subjects only. Attach a syllabus (PDF/Word) and use Revise to get topic-by-topic sessions.")
    return {"blocks": blocks, "files": files, "notes": notes, "summary": f"Study timetable for {len(objs)} subjects",
            "facts": [f"Subjects and exam dates: " + "; ".join(f"{c['label']} {c['date']} ({c['days']} days)" for c in cdown), f"{hours:g} study hours per day, {total_h} sessions in total, preparation level {level}.",
                      "Sessions per subject: " + ", ".join(f"{k} {v}" for k, v in counts.items()), "Last two days before each exam are revision; one mock test about four days before each exam; one rest day a week."],
            "what": "a study timetable, priorities, exam countdown, calendar view and downloads (.ics, .csv, .docx)", "size": "4-6 sentences: how the plan is organised, which subjects get the most time and why, and how to use the tracker"}


register(Workflow(
    id="academic", skill="planning", title="Study timetable", description="Exam preparation: study timetable, revision calendar, mock tests and progress tracker",
    keywords=re.compile(r"\b(?:exams?|timetable|study|semester|syllabus|revision|revise|test|quiz|assignment|homework|school|college|subjects?|prepare for)\b", re.I), prefill=prefill, run=run,
    slots=[Slot("subjects", "Subjects", "Which subjects are you preparing? (separate with commas; add the exam date after a colon if you know it, e.g. “Maths: 12 Dec”)", kind="list"),
           Slot("exam_dates", "Exam dates", "What are the exam dates for each subject?", hint="e.g. Maths 12 Dec, Physics 15 Dec, Chemistry 18 Dec",
                ask_if=lambda s: any(not parse_subjects([str(x)])[0]["exam"] for x in (s.get("subjects") or []) if parse_subjects([str(x)]))),
           Slot("hours", "Study hours per day", "How many hours a day can you realistically study?", kind="number"),
           Slot("level", "Preparation level", "How prepared do you feel overall?", kind="choice", choices=["weak", "average", "strong"]),
           Slot("hard", "Difficult subjects", "Which subjects feel the hardest? (they get more time)", kind="list"),
           Slot("syllabus", "Syllabus", "", kind="file", required=False), Slot("start", "Start date", "", kind="date", required=False)]))
