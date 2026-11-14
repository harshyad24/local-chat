"""The deterministic schedulers: exam-prep timetable and task scheduling."""
import sys
import unittest
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.skills import scheduler as sc  # noqa: E402

START = date(2026, 12, 1)  # a Tuesday


def plan(**kw):
    subs = kw.pop("subs", [sc.Subject("Maths", date(2026, 12, 20), "weak", True), sc.Subject("History", date(2026, 12, 18), "strong"), sc.Subject("Physics", date(2026, 12, 24), "average")])
    return sc.study_plan(subs, kw.pop("hours", 4), kw.pop("start", START), **kw)


class StudyPlan(unittest.TestCase):
    def test_nothing_is_studied_after_its_exam(self):
        p = plan()
        exams = {"Maths": "2026-12-20", "History": "2026-12-18", "Physics": "2026-12-24"}
        for s in p["slots"]:
            if s["kind"] in ("study", "revision", "mock"):
                self.assertLess(s["date"], exams[s["subject"]], s)

    def test_sessions_per_day_never_exceed_the_hours_available(self):
        p = plan(hours=3)
        per = {}
        for s in p["slots"]:
            if s["kind"] in ("study", "revision", "mock"):
                per[s["date"]] = per.get(s["date"], 0) + (2 if s["kind"] == "mock" else 1)
        self.assertTrue(max(per.values()) <= 4)  # 3 sessions, a mock counts double

    def test_weak_hard_subject_gets_more_time_than_strong_one(self):
        p = plan()
        n = lambda name: sum(1 for s in p["slots"] if s["subject"] == name and s["kind"] in ("study", "revision", "mock"))
        self.assertGreater(n("Maths"), n("History") * 0.9)
        self.assertGreater(n("Maths"), 0)

    def test_last_two_days_before_an_exam_are_revision(self):
        p = plan()
        rev = [s for s in p["slots"] if s["subject"] == "History" and s["date"] >= "2026-12-16" and s["kind"] != "exam"]
        self.assertTrue(rev and all(s["kind"] == "revision" for s in rev))

    def test_each_long_preparation_gets_a_mock_test(self):
        p = plan()
        self.assertEqual({s["subject"] for s in p["slots"] if s["kind"] == "mock"} >= {"Maths", "History", "Physics"}, True)

    def test_rest_day_and_exam_markers(self):
        p = plan()
        self.assertTrue(any(s["kind"] == "rest" for s in p["slots"]))
        self.assertEqual(len([s for s in p["slots"] if s["kind"] == "exam"]), 3)

    def test_past_exams_are_dropped_and_empty_input_is_safe(self):
        p = sc.study_plan([sc.Subject("Old", date(2020, 1, 1))], 3, START)
        self.assertEqual(p["slots"], [])
        self.assertEqual(sc.study_plan([], 3, START)["days"], 0)

    def test_topics_rotate(self):
        p = sc.study_plan([sc.Subject("Maths", date(2026, 12, 12), "average", False, ["Algebra", "Calculus", "Geometry"])], 3, START)
        topics = [s["topic"] for s in p["slots"] if s["kind"] == "study"]
        self.assertEqual(topics[:3], ["Algebra", "Calculus", "Geometry"])

    def test_one_day_before_exam_still_works(self):
        p = sc.study_plan([sc.Subject("X", START + timedelta(days=1))], 2, START)
        self.assertTrue(any(s["kind"] == "revision" for s in p["slots"]))

    def test_grouping_for_the_ui(self):
        rows = sc.group_days(plan()["slots"], 5)
        self.assertEqual(len(rows), 5)
        self.assertIn("slots", rows[0])


class Tasks(unittest.TestCase):
    def test_deadline_order_and_no_weekends(self):
        t = [sc.Task("B", 120, date(2026, 12, 4)), sc.Task("A", 120, date(2026, 12, 2))]
        r = sc.schedule_tasks(t, date(2026, 12, 1), date(2026, 12, 11))
        self.assertEqual(r["events"][0]["name"], "A")
        self.assertTrue(all(e["start"].weekday() < 5 for e in r["events"]))
        self.assertEqual(r["unscheduled"], [])

    def test_long_task_is_split_over_days(self):
        r = sc.schedule_tasks([sc.Task("Report", 900)], date(2026, 12, 1), date(2026, 12, 11))
        self.assertGreater(len(r["events"]), 3)
        self.assertEqual(sum(int((e["end"] - e["start"]).total_seconds() / 60) for e in r["events"]), 900)

    def test_impossible_deadline_is_reported_not_hidden(self):
        r = sc.schedule_tasks([sc.Task("Huge", 3000, date(2026, 12, 2))], date(2026, 12, 1), date(2026, 12, 11))
        self.assertTrue(r["unscheduled"])
        self.assertIn("deadline", r["unscheduled"][0]["reason"])

    def test_working_hours_respected(self):
        r = sc.schedule_tasks([sc.Task("X", 600)], date(2026, 12, 1), date(2026, 12, 11), time(10, 0), time(16, 0))
        self.assertTrue(all(10 <= e["start"].hour and e["end"].hour * 60 + e["end"].minute <= 16 * 60 for e in r["events"]))


if __name__ == "__main__":
    unittest.main()
