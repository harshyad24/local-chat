"""Deterministic schedulers (no model involved): exam-prep timetables and general task scheduling. Pure functions, easy to test."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

LEVEL_WEIGHT = {"weak": 1.6, "average": 1.2, "strong": 0.9}


@dataclass
class Subject:
    name: str
    exam: date
    level: str = "average"      # weak | average | strong
    hard: bool = False
    topics: Optional[list[str]] = None


def _t(start: datetime, minutes: int) -> str:
    return f"{start.strftime('%H:%M')}–{(start + timedelta(minutes=minutes)).strftime('%H:%M')}"


def study_plan(subjects: list[Subject], hours_per_day: float, start: date, session_min: int = 60, day_start: time = time(9, 0),
               rest_weekday: Optional[int] = 6) -> dict:
    """-> {slots: [...], skipped: [...], days: n}. Slots: {id, date, time, start, end, subject, kind(study|revision|mock|rest), topic}.
    Rules: more sessions go to weaker / harder / nearer-exam subjects; the last 2 days before an exam are revision for that subject; one
    mock test about 4 days before each exam (and one mid-way for long preparations); one rest day a week when there is time."""
    subjects = [s for s in subjects if s.exam >= start]
    if not subjects:
        return {"slots": [], "skipped": [], "days": 0}
    per_day = max(1, round(hours_per_day * 60 / session_min))
    last = max(s.exam for s in subjects)
    days = [start + timedelta(days=i) for i in range((last - start).days + 1)]
    weight = {s.name: LEVEL_WEIGHT.get(s.level, 1.2) * (1.3 if s.hard else 1.0) * math.sqrt(max(1, min((s.exam - start).days, 30))) for s in subjects}
    # which days can be used for studying at all
    free = []
    for d in days:
        nearest = min((s.exam - d).days for s in subjects if s.exam >= d) if any(s.exam >= d for s in subjects) else 0
        if rest_weekday is not None and d.weekday() == rest_weekday and nearest > 3 and (d - start).days >= 1 and len(days) >= 10:
            continue
        free.append(d)
    total_sessions = len(free) * per_day
    wsum = sum(weight.values())
    target = {s.name: total_sessions * weight[s.name] / wsum for s in subjects}
    mocks: dict[date, str] = {}
    for s in subjects:
        span = (s.exam - start).days
        if span >= 7 and s.exam - timedelta(days=4) >= start:
            mocks[s.exam - timedelta(days=4)] = s.name
        if span >= 21:
            mocks.setdefault(start + timedelta(days=span // 2), s.name)
    slots, topic_i = [], {s.name: 0 for s in subjects}
    for d in days:
        if d not in free:
            slots.append({"id": f"{d}-rest", "date": d.isoformat(), "time": "All day", "subject": "Rest day", "kind": "rest", "topic": "Light revision only if you want to"})
            continue
        live = [s for s in subjects if s.exam > d]
        exams_today = [s for s in subjects if s.exam == d]
        for s in exams_today:
            slots.append({"id": f"{d}-exam-{s.name}", "date": d.isoformat(), "time": "Exam day", "subject": s.name, "kind": "exam", "topic": "Exam"})
        if not live:
            continue
        cursor = datetime.combine(d, day_start)
        prev: list[str] = []
        for i in range(per_day):
            if i == 3:
                cursor += timedelta(minutes=45)  # lunch
            mock = mocks.get(d)
            if mock and i == 0 and any(s.name == mock and s.exam > d for s in live):
                subj, kind, dur = mock, "mock", min(session_min * 2, 120)
                topic = "Timed mock test, then review mistakes"
            else:
                def score(s: Subject) -> float:
                    left = max(1, (s.exam - d).days)
                    pen = 0.5 if prev[-2:] == [s.name, s.name] else 1.0
                    return pen * max(target[s.name], 0.05) / left
                s_ = max(live, key=score)
                subj, dur = s_.name, session_min
                kind = "revision" if (s_.exam - d).days <= 2 else "study"
                tl = s_.topics or []
                topic = tl[topic_i[s_.name] % len(tl)] if tl and kind == "study" else ("Revise key notes and past questions" if kind == "revision" else "")
                if tl and kind == "study":
                    topic_i[s_.name] += 1
                target[s_.name] -= 1
            slots.append({"id": f"{d}-{i}", "date": d.isoformat(), "time": _t(cursor, dur), "start": cursor.isoformat(), "end": (cursor + timedelta(minutes=dur)).isoformat(),
                          "subject": subj, "kind": kind, "topic": topic})
            prev.append(subj)
            cursor += timedelta(minutes=dur + 15)
    return {"slots": slots, "skipped": [], "days": len(days)}


def group_days(slots: list[dict], limit_days: int = 14) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for s in slots:
        by.setdefault(s["date"], []).append(s)
    rows = []
    for d in sorted(by)[:limit_days]:
        dt = date.fromisoformat(d)
        rows.append({"date": d, "day": dt.strftime("%A"), "slots": [{k: s[k] for k in ("id", "time", "subject", "kind", "topic")} for s in by[d]],
                     "note": "Exam day" if any(s["kind"] == "exam" for s in by[d]) else ""})
    return rows


# ------------------------------------------------------------------ general task scheduling
@dataclass
class Task:
    name: str
    minutes: int
    deadline: Optional[date] = None
    priority: int = 2           # 1 high .. 3 low


def schedule_tasks(tasks: list[Task], start: date, end: date, work_start: time = time(9, 0), work_end: time = time(18, 0), buffer_min: int = 15,
                   skip_weekends: bool = True, chunk_min: int = 120) -> dict:
    """Earliest-deadline-first, then priority. Long tasks are split into chunks of at most `chunk_min` across days. -> {events, unscheduled}."""
    order = sorted(tasks, key=lambda t: (t.deadline or date.max, t.priority, -t.minutes))
    day = start
    cursor = datetime.combine(day, work_start)
    events, unscheduled = [], []

    def next_day(d: date) -> date:
        d += timedelta(days=1)
        while skip_weekends and d.weekday() >= 5:
            d += timedelta(days=1)
        return d

    while skip_weekends and day.weekday() >= 5 and day <= end:
        day += timedelta(days=1)
    cursor = datetime.combine(day, work_start)
    for t in order:
        left = max(5, int(t.minutes))
        part = 0
        while left > 0:
            if day > end:
                unscheduled.append({"name": t.name, "minutes_left": left, "reason": "no room before the end date"})
                break
            if t.deadline and day > t.deadline:
                unscheduled.append({"name": t.name, "minutes_left": left, "reason": f"it would finish after its deadline ({t.deadline.isoformat()})"})
                break
            day_end = datetime.combine(day, work_end)
            free = int((day_end - cursor).total_seconds() // 60)
            if free < 30:
                day = next_day(day)
                if day > end:
                    continue
                cursor = datetime.combine(day, work_start)
                continue
            take = min(left, chunk_min, free)
            events.append({"name": t.name + (f" (part {part + 1})" if (left > take or part) else ""), "start": cursor, "end": cursor + timedelta(minutes=take), "priority": t.priority,
                           "deadline": t.deadline.isoformat() if t.deadline else ""})
            cursor += timedelta(minutes=take + buffer_min)
            left -= take
            part += 1
    return {"events": events, "unscheduled": unscheduled}
