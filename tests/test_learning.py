"""Learning hub: SM-2 scheduling, cards, streaks, past-quiz selection, maths generation/checking, the code lab and verified exercises."""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, learning as L  # noqa: E402

from test_agent import TempDB  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def make_course(state=None, subject="Python"):
    now = time.time()
    rid = "run1"
    chat_id = db.create_chat("fake")["id"]
    state = state if state is not None else {"path": [{"title": "Basics", "goal": "", "lessons": []}, {"title": "Loops", "goal": "", "lessons": []}], "current": 1,
                                             "learn_progress": ["m0"], "quizzes": {}, "quiz_history": []}
    with db.conn() as c:
        c.execute("INSERT INTO skill_runs(id, chat_id, skill, workflow, status, request, slots, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (rid, chat_id, "learn", "learn", "done", "/learn python", json.dumps({"subject": subject, "level": "beginner"}), json.dumps(state), now, now))
    return rid


class Scheduling(unittest.TestCase):
    card = {"ease": 2.5, "reps": 0, "interval": 0, "lapses": 0}

    def test_first_good_review_is_tomorrow_then_six_days(self):
        a = L.sm2(self.card, "good")
        self.assertEqual((a["reps"], a["interval"]), (1, 1))
        b = L.sm2({**self.card, **a}, "good")
        self.assertEqual((b["reps"], b["interval"]), (2, 6))

    def test_interval_grows_with_ease_and_easy_beats_good(self):
        c = {"ease": 2.5, "reps": 2, "interval": 6, "lapses": 0}
        good, easy, hard = L.sm2(c, "good"), L.sm2(c, "easy"), L.sm2(c, "hard")
        self.assertGreater(good["interval"], 6)
        self.assertGreater(easy["interval"], good["interval"])
        self.assertLess(hard["interval"], good["interval"])
        self.assertLess(hard["ease"], good["ease"])

    def test_again_resets_and_returns_within_minutes(self):
        c = {"ease": 2.5, "reps": 5, "interval": 30, "lapses": 0}
        r = L.sm2(c, "again")
        self.assertEqual((r["reps"], r["interval"], r["lapses"]), (0, 0, 1))
        self.assertLess(r["due"] - time.time(), 700)
        self.assertLess(r["ease"], 2.5)

    def test_ease_never_drops_below_floor(self):
        c = {"ease": 1.3, "reps": 3, "interval": 10, "lapses": 0}
        for _ in range(5):
            c.update(L.sm2(c, "again"))
        self.assertGreaterEqual(c["ease"], 1.3)


class Cards(TempDB):
    def test_add_dedupes_and_validates(self):
        rid = make_course()
        n = L.add_cards(rid, "Basics", [{"front": "What is a variable?", "back": "A named value"}, {"front": "what is a  variable", "back": "again"},
                                        {"front": "x", "back": "too short front"}, {"front": "Define loop", "back": ""}])
        self.assertEqual(n, 1)
        self.assertEqual(L.card_counts(rid)["total"], 1)

    def test_valid_cards_rejects_bad_shapes(self):
        self.assertEqual(L.valid_cards({"cards": [{"front": "a" * 5, "back": "b"}, {"front": 3, "back": "x"}, "nope"]}), [{"kind": "basic", "front": "aaaaa", "back": "b", "hint": ""}])
        self.assertEqual(L.valid_cards(None), [])

    def test_new_cards_are_due_and_grading_moves_them_out_and_logs_activity(self):
        rid = make_course()
        L.add_cards(rid, "Basics", [{"front": "What is a list?", "back": "An ordered collection"}])
        due = L.due_cards()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["subject"], "Python")
        r = L.grade_card(due[0]["id"], "good")
        self.assertEqual(r["interval"], 1)
        self.assertEqual(L.due_cards(), [])  # tomorrow
        self.assertEqual(L.today_summary()["card"]["n"], 1)
        self.assertIsNone(L.grade_card(due[0]["id"], "bogus"))

    def test_again_keeps_the_card_due_today(self):
        rid = make_course()
        L.add_cards(rid, "m", [{"front": "Capital of France?", "back": "Paris"}])
        cid = L.due_cards()[0]["id"]
        L.grade_card(cid, "again")
        self.assertEqual(len(L.due_cards()), 1)


class Progress(TempDB):
    def test_course_listing_counts_modules_and_quiz_scores(self):
        rid = make_course({"path": [{"title": "A"}, {"title": "B"}, {"title": "C"}], "current": 1, "learn_progress": ["m0"],
                           "quizzes": {"m0": [{}]}, "quiz_history": [{"module": "m0", "right": 2, "total": 5, "missed": [0, 1, 2]}, {"module": "m0", "right": 4, "total": 5, "missed": [0]}]})
        c = L.courses()[0]
        self.assertEqual((c["total"], c["done"], c["avg_quiz"]), (3, 1, 80))
        self.assertEqual(c["modules"][0]["attempts"], 2)
        self.assertTrue(c["modules"][0]["done"])
        self.assertEqual(L.weak_topics(L.courses()), [])  # last attempt 80%

    def test_a_course_disappears_with_its_chat(self):
        make_course()
        self.assertEqual(len(L.courses()), 1)
        with db.conn() as c:
            c.execute("DELETE FROM chats")
        self.assertEqual(L.courses(), [])

    def test_runs_without_a_path_are_not_courses(self):
        make_course({"foo": 1})
        self.assertEqual(L.courses(), [])

    def test_weak_topics_use_the_latest_attempt(self):
        make_course({"path": [{"title": "A"}], "current": 0, "quizzes": {}, "quiz_history": [{"module": "m0", "right": 1, "total": 5, "missed": [0]}]})
        w = L.weak_topics(L.courses())
        self.assertEqual((w[0]["module"], w[0]["pct"]), ("A", 20))

    def test_streak_counts_consecutive_days_ending_today_or_yesterday(self):
        make_course()
        self.assertEqual(L.streak()["current"], 0)
        day = 86400
        with db.conn() as c:
            for back in (0, 1, 2, 4):
                c.execute("INSERT INTO learn_events(course_id, kind, module, right_n, total_n, detail, ts) VALUES('run1','lesson','',0,0,'{}',?)", (time.time() - back * day,))
        s = L.streak()
        self.assertEqual((s["current"], s["best"], s["today"]), (3, 3, True))

    def test_overview_has_everything_the_hub_needs(self):
        make_course()
        L.log_event("run1", "quiz", "m0", 4, 5)
        o = L.overview()
        for k in ("courses", "cards", "streak", "today", "activity", "weak"):
            self.assertIn(k, o)
        self.assertEqual(o["today"]["quiz"]["right"], 4)


class PastQuiz(TempDB):
    def _qs(self, n):
        return [{"q": f"Q{i}", "options": ["a", "b", "c", "d"], "answer": 0, "explain": ""} for i in range(n)]

    def test_missed_questions_come_first_then_older_ones(self):
        make_course({"path": [{"title": "A"}, {"title": "B"}], "current": 1, "quizzes": {"m0": self._qs(5), "m1": self._qs(5)},
                     "quiz_history": [{"module": "m0", "right": 3, "total": 5, "missed": [1, 3]}]})
        r = L.past_quiz("run1", n=6, seed=1)
        qs = r["questions"]
        self.assertEqual(len(qs), 6)
        self.assertEqual({q["q"] for q in qs if q["was_missed"]}, {"Q1", "Q3"})
        self.assertTrue(all(q["module_title"] for q in qs))
        self.assertEqual(r["missed_pool"], 2)

    def test_unknown_course_or_no_quizzes_is_empty(self):
        self.assertEqual(L.past_quiz("nope")["questions"], [])
        make_course()
        self.assertEqual(L.past_quiz("run1")["questions"], [])


class MathPractice(unittest.TestCase):
    def test_every_generated_problem_accepts_its_own_answer(self):
        for topic in L.TOPICS:
            for lv in (1, 2, 3):
                for p in L.math_problems(topic, lv, 8, seed=11):
                    self.assertTrue(L.check_answer(p["kind"], p["answer"], p["answer"], p.get("tol", 0))["correct"], (topic, lv, p))

    def test_same_seed_same_problems(self):
        a = [p["q"] for p in L.math_problems("algebra", 2, 5, seed=3)]
        b = [p["q"] for p in L.math_problems("algebra", 2, 5, seed=3)]
        self.assertEqual(a, b)

    def test_checking(self):
        self.assertTrue(L.check_answer("number", "3/4", "0.75")["correct"])
        self.assertFalse(L.check_answer("number", "3/4", "0.76")["correct"])
        self.assertTrue(L.check_answer("number", "33.3333", "33.33", 0.01)["correct"])
        self.assertTrue(L.check_answer("set", "2, 5", "5 and 2")["correct"])
        self.assertFalse(L.check_answer("set", "2, 5", "2")["correct"])
        self.assertTrue(L.check_answer("expr", "6*x**2 + 3", "3 + 6x^2")["correct"])
        self.assertFalse(L.check_answer("expr", "2*x", "2*x + 1")["correct"])

    def test_hostile_input_is_rejected_not_evaluated(self):
        r = L.check_answer("expr", "2*x", "__import__('os').system('echo hi')")
        self.assertFalse(r["correct"])
        self.assertFalse(L.check_answer("number", "5", "")["correct"])

    def test_topic_detection(self):
        self.assertEqual(L.math_topic("Teach me calculus"), "calculus")
        self.assertEqual(L.math_topic("Algebra basics"), "algebra")
        self.assertIsNone(L.math_topic("Python programming"))
        self.assertIsNone(L.math_topic("French cooking"))

    def test_algebra_problems_have_the_stated_solution(self):
        import re
        import sympy as sp
        x = sp.Symbol("x")
        for seed in range(30):
            p = L.math_problems("algebra", 2, 1, seed=seed)[0]
            lhs, rhs = p["q"].split(": ")[1].replace("−", "-").split(" = ")
            f = lambda t: sp.sympify(re.sub(r"(\d)x", r"\1*x", re.sub(r"(^| )x", r"\g<1>1*x", t)))  # noqa: E731
            self.assertEqual(sp.solve(f(lhs) - f(rhs), x), [int(p["answer"])], p)


class CodeLab(unittest.TestCase):
    def test_reports_each_test(self):
        r = run(L.run_python("def f(a, b):\n    return a + b\nprint('hi')", ["assert f(1, 2) == 3", "assert f(2, 2) == 5, 'wrong sum'"]))
        self.assertEqual((r["passed"], r["total"]), (1, 2))
        self.assertIn("hi", r["stdout"])
        self.assertNotIn("@@TESTS@@", r["stdout"])
        self.assertIn("wrong sum", r["tests"][1]["error"])

    def test_errors_timeouts_and_network_are_contained(self):
        self.assertIn("ZeroDivisionError", run(L.run_python("print(1/0)"))["stderr"])
        self.assertTrue(run(L.run_python("while True: pass", timeout=2))["timed_out"])
        self.assertIn("Network access is disabled", run(L.run_python("import socket; socket.create_connection(('example.com', 80))"))["stderr"])

    def test_empty_code_is_a_friendly_error(self):
        self.assertFalse(run(L.run_python("  "))["ok"])

    def test_only_exercises_whose_solution_passes_their_tests_survive(self):
        items = {"exercises": [
            {"task": "square", "solution": "def sq(n):\n    return n * n", "tests": ["assert sq(3) == 9", "assert sq(0) == 0"], "starter": "def sq(n):\n    pass"},
            {"task": "wrong solution", "solution": "def sq(n):\n    return n + n", "tests": ["assert sq(3) == 9", "assert sq(0) == 0"]},
            {"task": "one test only", "solution": "x = 1", "tests": ["assert x == 1"]},
            {"task": "not an assert", "solution": "x = 1", "tests": ["print(1)", "print(2)"]},
            "junk"]}
        good = run(L.verified_exercises(items))
        self.assertEqual([g["task"] for g in good], ["square"])
        self.assertEqual(good[0]["starter"], "def sq(n):\n    pass")


if __name__ == "__main__":
    unittest.main()
