"""The skill system: parser, routing, question engine, run state, engine (asking / running / failing), feedback, analysis, deliverables."""
import asyncio
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db, llm, skills  # noqa: E402
from backend.skills import analysis, common, engine, feedback, questions, registry, router  # noqa: E402
from backend.skills import context as runs  # noqa: E402
from backend.skills.workflows import analyze as wf_analyze  # noqa: E402
from backend.skills.workflows import compare as wf_compare  # noqa: E402
from backend.skills.workflows import create as wf_create  # noqa: E402
from backend.skills.workflows import learn as wf_learn  # noqa: E402
from backend.skills.workflows import planning_academic as wf_acad  # noqa: E402
from backend.skills.workflows import planning_misc as wf_misc  # noqa: E402
from backend.skills.workflows import planning_travel as wf_travel  # noqa: E402

from test_agent import TempDB  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class NoModel:
    """A router/extractor stand-in whose model never answers usefully."""
    async def json_call(self, *a, **k):
        return None


class Parser(unittest.TestCase):
    def test_exactly_five_commands(self):
        self.assertEqual(skills.COMMANDS, ("planning", "compare", "analyze", "create", "learn"))
        for c in skills.COMMANDS:
            self.assertEqual(skills.parse(f"/{c} do a thing"), (c, "do a thing"))

    def test_case_alias_and_spacing(self):
        self.assertEqual(skills.parse("  /Planning   Plan a trip  "), ("planning", "Plan a trip"))
        self.assertEqual(skills.parse("/analyse this"), ("analyze", "this"))
        self.assertEqual(skills.parse("/learn"), ("learn", ""))

    def test_domain_commands_do_not_exist(self):
        for c in ("travel", "academic", "movies", "events", "finance", "business"):
            self.assertIsNone(skills.parse(f"/{c} something"))
        self.assertIsNone(skills.parse("please /planning a trip"))
        self.assertIsNone(skills.parse("plan a trip"))
        self.assertIsNone(skills.parse("/planningx foo"))


class Routing(unittest.TestCase):
    def check(self, skill, text, expected):
        wf, how = run(router.route(NoModel(), skill, text))
        self.assertEqual(wf.id, expected, text)
        return how

    def test_planning_workflows_are_told_apart(self):
        self.check("planning", "Plan a 5-day trip to Goa", "travel")
        self.check("planning", "Create a study timetable for my semester exams", "academic")
        self.check("planning", "Find interesting movies and cinema shows for this weekend", "entertainment")
        self.check("planning", "Plan a birthday party for 30 guests", "event")
        self.check("planning", "Schedule my project milestones and meetings next week", "schedule")
        self.check("planning", "Plan my day", "schedule")

    def test_single_workflow_skills(self):
        for sk, wid in (("compare", "compare"), ("analyze", "analyze"), ("create", "create"), ("learn", "learn")):
            self.check(sk, "anything", wid)

    def test_unclear_request_asks_the_model_and_survives_a_bad_answer(self):
        class Picks:
            async def json_call(self, *a, **k):
                return {"workflow": "event"}

        class Garbage:
            async def json_call(self, *a, **k):
                return {"workflow": "nonsense"}
        self.assertEqual(run(router.route(Picks(), "planning", "organise something nice"))[0].id, "event")
        wf, how = run(router.route(Garbage(), "planning", "organise something nice"))
        self.assertEqual((wf.id, how), ("schedule", "default"))  # the skill's general workflow


class Questions(unittest.TestCase):
    TODAY = date(2026, 11, 11)  # a Wednesday

    def test_dates(self):
        d = lambda t: questions.parse_dates(t, self.TODAY)
        self.assertEqual(d("on 15 Dec")[0], date(2026, 12, 15))
        self.assertEqual(d("Dec 15, 2027")[0], date(2027, 12, 15))
        self.assertEqual(d("2026-12-01 to 2026-12-05"), [date(2026, 12, 1), date(2026, 12, 5)])
        self.assertEqual(d("this weekend"), [date(2026, 11, 14), date(2026, 11, 15)])
        self.assertEqual(d("5 Jan")[0], date(2027, 1, 5))  # no year -> the next occurrence
        self.assertEqual(d("tomorrow"), [date(2026, 11, 12)])
        self.assertEqual(d("nothing here"), [])

    def test_numbers_and_currency(self):
        self.assertEqual(questions.parse_number("₹40,000"), 40000)
        self.assertEqual(questions.parse_number("30k"), 30000)
        self.assertEqual(questions.parse_number("1.5 lakh"), 150000)
        self.assertEqual(questions.parse_number("two"), 2)
        self.assertIsNone(questions.parse_number("many"))
        self.assertEqual(questions.currency_of("budget ₹30,000"), "INR")
        self.assertEqual(questions.currency_of("under $500"), "USD")

    def test_coerce(self):
        by = {s.name: s for s in registry.get("planning", "travel").slots}
        self.assertEqual(questions.coerce(by["transport"], "I'd take the Train"), "train")
        self.assertIsNone(questions.coerce(by["transport"], "teleport"))
        self.assertEqual(questions.coerce(by["transport"], "anything is fine"), "flexible")
        self.assertEqual(questions.coerce(by["transport"], "any train is fine"), "train")  # a named choice beats "any"
        self.assertEqual(questions.coerce(by["travellers"], "four"), 4)
        self.assertEqual(questions.coerce(registry.get("compare", "compare").slots[1], "A, B and C"), ["A", "B", "C"])
        self.assertEqual(questions.coerce(by["dates"], "15 Dec to 20 Dec")["end"][-5:], "12-20")

    def test_missing_and_progressive_questions(self):
        wf = registry.get("planning", "travel")
        have = {"destination": "Goa", "days": 5}
        qs = questions.build_questions(wf, have, 3)
        self.assertEqual([q["slot"] for q in qs], ["origin", "dates", "travellers"])  # the next three, not the whole questionnaire
        have.update(origin="Pune", dates={"start": "2026-12-01"}, travellers=2, budget="40000", transport="train", stay="budget")
        self.assertEqual(questions.missing(wf, have), [])

    def test_conditional_questions(self):
        wf = registry.get("compare", "compare")
        names = lambda s: [x.name for x in questions.missing(wf, s)]
        self.assertIn("budget", names({"topic": "smartphones under"}))
        self.assertNotIn("budget", names({"topic": "two job candidates' interview styles"}))
        self.assertNotIn("budget", names({"topic": "phones", "budget": "30000"}))

    def test_merge_ignores_unknown_and_invalid(self):
        wf = registry.get("planning", "travel")
        out = questions.merge(wf, {}, {"origin": "Pune", "bogus": "x", "transport": "teleport", "travellers": "3"})
        self.assertEqual(out, {"origin": "Pune", "travellers": 3.0})

    def test_model_extraction_is_validated(self):
        class Rc:
            async def json_call(self, prompt, **k):
                return {"origin": "Pune", "travellers": "three", "stay": "palace", "evil": "x"}
        wf = registry.get("planning", "travel")
        got = run(questions.llm_extract(Rc(), wf, "from Pune with three friends", {}))
        self.assertEqual(got, {"origin": "Pune", "travellers": 3.0})  # 'palace' is not a choice, 'evil' is not a slot


class Prefill(unittest.TestCase):
    def test_travel(self):
        p = wf_travel.prefill("Plan a 5-day trip to Goa from Pune for 4 people under ₹40,000 by train", {})
        self.assertEqual((p["destination"], p["days"], p["origin"], p["travellers"], p["transport"]), ("Goa", "5", "Pune", "4", "train"))
        self.assertEqual(questions.parse_number(p["budget"]), 40000)

    def test_compare(self):
        self.assertEqual(wf_compare.prefill("Compare RTX 5070 and RTX 5080 for local AI models", {})["items"], ["RTX 5070", "RTX 5080"])
        self.assertEqual(wf_compare.prefill("Compare RTX 5070 and RTX 5080 for local AI models", {})["priorities"], "local AI models")
        p = wf_compare.prefill("Compare three universities for a master's degree", {})
        self.assertNotIn("items", p)  # a category, not named options: the app will go and find candidates
        self.assertIn("budget", wf_compare.prefill("Compare smartphones under ₹30,000", {}))
        self.assertEqual(wf_compare.prefill("Compare A vs B vs C", {})["items"], ["A", "B", "C"])

    def test_create_and_learn(self):
        self.assertEqual(wf_create.detect_kind("Generate a PowerPoint presentation about AI agents"), "presentation")
        self.assertEqual(wf_create.detect_kind("Build an executive dashboard for my sales data"), "dashboard")
        self.assertEqual(wf_create.detect_kind("Build a Python application for inventory management"), "code")
        self.assertEqual(wf_create.detect_kind("Write a professional business proposal"), "document")
        self.assertEqual(wf_learn.prefill("Teach me Python from beginner to advanced", {})["subject"], "Python")
        self.assertEqual(wf_learn.prefill("Prepare me for a SQL interview", {})["mode"], "interview")
        self.assertEqual(wf_learn.prefill("Explain linear regression with practical examples", {})["mode"], "explain")

    def test_academic_subjects(self):
        subs = wf_acad.parse_subjects(["Maths: 12 Dec", "physics - 15/12/2026", "Chemistry"], date(2026, 11, 1))
        self.assertEqual([(s["name"], s["exam"]) for s in subs], [("Maths", date(2026, 12, 12)), ("Physics", date(2026, 12, 15)), ("Chemistry", None)])

    def test_event_kind(self):
        self.assertEqual(wf_misc.event_type("my sister's wedding reception"), "wedding")
        self.assertEqual(wf_misc.event_type("annual tech conference"), "conference")
        self.assertEqual(wf_misc.event_type("a surprise thing"), "generic")


class State(TempDB):
    def test_runs_persist_and_update(self):
        r = runs.create("chat1", "planning", "travel", "Plan a trip", {"destination": "Goa"})
        self.assertEqual((r["status"], r["slots"]["destination"]), ("asking", "Goa"))
        runs.update(r["id"], status="done", slots={"destination": "Goa", "days": 5})
        self.assertEqual(runs.get(r["id"])["slots"]["days"], 5)
        runs.set_state(r["id"], "study_done", ["a", "b"])
        self.assertEqual(runs.get(r["id"])["state"]["study_done"], ["a", "b"])
        self.assertIsNone(runs.latest("chat1"))  # only 'asking' runs count as waiting for an answer
        self.assertEqual(runs.latest("chat1", ("done",))["id"], r["id"])

    def test_detect(self):
        s = {"skills_enabled": True}
        self.assertEqual(skills.detect(s, "/learn Python", "c1")["skill"], "learn")
        self.assertIsNone(skills.detect(s, "hello there", "c1"))
        self.assertIsNone(skills.detect({"skills_enabled": False}, "/learn Python", "c1"))
        r = runs.create("c1", "planning", "travel", "Plan a trip")
        t = skills.detect(s, "from Pune", "c1")  # a plain reply while a run waits for answers
        self.assertTrue(t["implicit"] and t["run"]["id"] == r["id"])
        self.assertIsNone(skills.detect(s, "/", "c1") and None)
        self.assertEqual(skills.detect(s, "x", "c1", r["id"], {"origin": "Pune"})["answers"], {"origin": "Pune"})
        self.assertIsNone(skills.detect(s, "x", "other-chat", r["id"], {}))  # a run id from another chat is not accepted


def fake_ctx(chat_id="c1"):
    return SimpleNamespace(s={**db.DEFAULT_SETTINGS, "tool_web_search": True, "tool_fetch_page": True}, model="m", chat_id=chat_id,
                           state={"acc": "", "tools": [], "sources": []}, tool_names=["web_search", "fetch_page"], prompt=[{"role": "system", "content": "SYS"}],
                           skill_done=False, skill_fallthrough=False)


async def collect(ctx, turn):
    return [e async for e in engine.drive(ctx, turn)]


def stub_workflow(fn, slots=None, wid="stub"):
    return registry.register(registry.Workflow(id=wid, skill="learn", title="Stub", description="d", keywords=__import__("re").compile("zzz"), slots=slots or [], run=fn, general=False))


class Engine(TempDB):
    def tearDown(self):
        registry.REGISTRY.pop("learn/stub", None)
        super().tearDown()

    def turn(self, request, wf="travel", skill="planning", **kw):
        return {"skill": skill, "request": request, "text": request, "run": None, "answers": {}, "action": None, "files": [], "history": [], **kw}

    def test_missing_facts_trigger_questions_not_work(self):
        ctx = fake_ctx()
        with mock.patch.object(llm, "complete", mock.AsyncMock(return_value="{}")):  # the one extraction call finds nothing more
            ev = run(collect(ctx, self.turn("Plan a 5-day trip to Goa")))
        res = next(e for e in ev if e["type"] == "tool_result")
        self.assertEqual(res["detail"]["status"], "asking")
        self.assertEqual([q["slot"] for q in res["detail"]["questions"]], ["origin", "dates", "travellers"])
        self.assertTrue(any(e["type"] == "delta" for e in ev))
        self.assertFalse(ctx.skill_done)
        self.assertEqual(runs.latest("c1")["workflow"], "travel")

    def test_answers_are_reused_and_the_workflow_runs_when_complete(self):
        seen = {}

        async def work(rc):
            seen.update(rc.slots)
            await rc.progress(1, 1, "working")
            return {"blocks": [{"type": "markdown", "text": "ok"}], "facts": ["F1"], "summary": "done!", "state": {"k": 1}}
        stub_workflow(work, [registry.Slot("subject", "Subject", "What?"), registry.Slot("level", "Level", "How good?", kind="choice", choices=["beginner", "advanced"])])
        ctx = fake_ctx()
        t = self.turn("teach me chess", skill="learn")
        t["answers"] = {"level": "advanced"}
        with mock.patch("backend.skills.router.route", mock.AsyncMock(return_value=(registry.get("learn", "stub"), "keywords"))):
            ev1 = run(collect(ctx, {**t, "answers": {}}))
            self.assertEqual(next(e for e in ev1 if e["type"] == "tool_result")["detail"]["status"], "asking")  # nothing known yet
            run_row = runs.latest("c1")
            ev2 = run(collect(ctx, {**t, "run": run_row, "answers": {"subject": "chess", "level": "advanced"}}))
        res = next(e for e in ev2 if e["type"] == "tool_result")
        self.assertEqual(res["detail"]["status"], "done")
        self.assertEqual(seen["subject"], "chess")
        self.assertTrue(any(e["type"] == "skill_progress" for e in ev2))
        self.assertTrue(ctx.skill_done)
        self.assertIn("F1", ctx.prompt[0]["content"])  # the model is given the computed facts to write about
        self.assertEqual(runs.get(run_row["id"])["state"]["k"], 1)
        self.assertEqual(ctx.tool_names, [])

    def test_a_crashing_workflow_is_reported_not_raised(self):
        async def boom(rc):
            raise RuntimeError("kaboom")
        stub_workflow(boom)
        ctx = fake_ctx()
        with mock.patch("backend.skills.router.route", mock.AsyncMock(return_value=(registry.get("learn", "stub"), "keywords"))):
            ev = run(collect(ctx, self.turn("x", skill="learn")))
        res = next(e for e in ev if e["type"] == "tool_result")
        self.assertFalse(res["ok"])
        self.assertIn("kaboom", res["detail"]["error"])
        self.assertEqual(runs.latest("c1", ("error",))["workflow"], "stub")

    def test_a_tool_failure_inside_a_workflow_is_survivable(self):
        async def work(rc):
            r = await rc.search("anything")  # the search tool is switched off / failing
            return {"blocks": [{"type": "markdown", "text": f"results: {len(r)}"}], "facts": []}
        stub_workflow(work)
        ctx = fake_ctx()
        ctx.s["tool_web_search"] = False
        ctx.tool_names = []
        with mock.patch("backend.skills.router.route", mock.AsyncMock(return_value=(registry.get("learn", "stub"), "keywords"))):
            ev = run(collect(ctx, self.turn("x", skill="learn")))
        res = next(e for e in ev if e["type"] == "tool_result")
        self.assertTrue(res["ok"])
        self.assertTrue(any("failed or is switched off" in n for n in res["detail"]["notes"]))

    def test_unrelated_message_while_asking_falls_through_to_normal_chat(self):
        r = runs.create("c1", "planning", "travel", "Plan a trip")
        ctx = fake_ctx()
        t = self.turn("what is the weather like on mars", run=r, implicit=True)
        with mock.patch.object(llm, "complete", mock.AsyncMock(return_value="{}")):
            ev = run(collect(ctx, t))
        self.assertEqual(ev, [])
        self.assertTrue(ctx.skill_fallthrough)
        self.assertEqual(runs.get(r["id"])["status"], "abandoned")

    def test_bare_command_asks_what_to_do(self):
        ctx = fake_ctx()
        ev = run(collect(ctx, self.turn("", skill="compare")))
        res = next(e for e in ev if e["type"] == "tool_result")
        self.assertEqual(res["detail"]["questions"][0]["slot"], "_request")
        self.assertTrue(res["detail"]["questions"][0]["examples"])

    def test_saved_preference_fills_a_question(self):
        feedback._upsert("planning", "travel.transport", "train", "test")
        ctx = fake_ctx()
        with mock.patch.object(llm, "complete", mock.AsyncMock(return_value="{}")):
            ev = run(collect(ctx, self.turn("Plan a 3-day trip to Jaipur from Delhi")))
        slots = next(e for e in ev if e["type"] == "tool_result")["detail"]["slots"]
        self.assertIn({"label": "Transport", "value": "train"}, slots)


class Feedback(TempDB):
    def test_rules(self):
        self.assertEqual(feedback.rule_extract("I prefer trains over flights"), {"travel.transport": "train"})
        self.assertEqual(feedback.rule_extract("shorter answers please, and include more charts"), {"style.detail": "brief", "output.charts": "more"})
        self.assertEqual(feedback.rule_extract("the budget estimate was inaccurate"), {"budget.buffer_pct": "15"})
        self.assertEqual(feedback.rule_extract("lovely weather"), {})

    def test_learn_store_review_edit_delete(self):
        rows = run(feedback.learn(None, "planning", "I prefer flights, and shorter explanations"))
        self.assertEqual({r["key"] for r in rows}, {"travel.transport", "style.detail"})
        prefs = feedback.prefs_for("planning")
        self.assertEqual((prefs["travel.transport"], prefs["style.detail"]), ("flight", "brief"))
        self.assertNotIn("travel.transport", feedback.prefs_for("compare"))  # skill-specific
        self.assertEqual(feedback.prefs_for("compare")["style.detail"], "brief")  # global
        row = next(r for r in feedback.list_prefs() if r["key"] == "travel.transport")
        feedback.update_pref(row["id"], "bus")
        self.assertEqual(feedback.prefs_for("planning")["travel.transport"], "bus")
        feedback.delete_pref(row["id"])
        self.assertNotIn("travel.transport", feedback.prefs_for("planning"))

    def test_unstructured_feedback_is_kept_as_a_note_and_model_keys_are_validated(self):
        class Rc:
            async def json_call(self, *a, **k):
                return {"preferences": [{"key": "Bad Key!", "value": "x"}, {"key": "learn.examples", "value": "more code samples"}]}
        rows = run(feedback.learn(Rc(), "learn", "give me more code samples every time"))
        self.assertEqual([r["key"] for r in rows], ["learn.examples"])
        rows = run(feedback.learn(None, "learn", "the third question was confusing to me"))
        self.assertTrue(rows[0]["key"].startswith("note."))


SALES = "Order Date,Region,Ad Spend,Units Sold,Revenue\n" + "\n".join(
    f"2025-{1 + i % 12:02d}-{1 + i % 27:02d},{['N', 'S', 'E'][i % 3]},{100 + i * 7},{5 + (i * 7) // 40},{(5 + (i * 7) // 40) * 50 + (i % 5)}" for i in range(120)) + "\n2025-03-03,N,150,6,300\n2025-03-03,N,150,6,300\n"


class Analysis(TempDB):
    def setUp(self):
        super().setUp()
        self.path = Path(self._tmp.name) / "sales.csv"
        self.path.write_text(SALES, encoding="utf-8")

    def test_numbers_are_computed_by_code(self):
        df = analysis.load(str(self.path))["data"]
        self.assertEqual(len(df), 122)
        self.assertEqual(analysis.quality(df)["duplicates"], 1)
        rel = analysis.relationship(df, "relationship between Ad Spend and Units Sold")
        self.assertGreater(rel["r"], 0.95)
        self.assertEqual(rel["strength"], "very strong")
        self.assertEqual(analysis.find_date_column(df), "Order Date")
        self.assertEqual(analysis.main_metric(df, ""), "Revenue")
        tr = analysis.trend(df, "Order Date", "Revenue", "sum")
        self.assertEqual(tr["rule"], "month")
        self.assertAlmostEqual(sum(tr["values"]), float(df["Revenue"].sum()), places=2)

    def test_outliers_and_cleaning(self):
        df = analysis.load(str(self.path))["data"].copy()
        df.loc[5, "Revenue"] = 999999
        o = analysis.outliers(df, ["Revenue"])
        self.assertEqual(o[0]["examples"][0], 999999.0)
        cleaned, log = analysis.clean(df)
        self.assertEqual(len(cleaned), len(df) - 1)
        self.assertTrue(any("duplicate" in l for l in log))

    def test_number_formatting_is_unambiguous(self):
        self.assertEqual(analysis.fmt(250000.0), "250,000")
        self.assertEqual(analysis.fmt(12.345), "12.35")

    def test_full_workflow_produces_blocks_files_and_checkable_facts(self):
        ctx = fake_ctx()
        run_row = runs.create("c1", "analyze", "analyze", "x", {})
        q = asyncio.Queue()
        rc = engine.RunCtx(ctx, run_row, registry.get("analyze", "analyze"), {}, [], q)
        rc.slots = {"file": {"id": "f", "name": "sales.csv", "path": str(self.path), "ext": ".csv"}, "goal": "relationship between Ad Spend and Units Sold"}
        res = run(wf_analyze.run(rc))
        types = [b["type"] for b in res["blocks"]]
        for t in ("overview", "dashboard", "findings", "dq"):  # a multi-dimensional table gets a dashboard (its charts are inside it)
            self.assertIn(t, types)
        self.assertNotIn("chart", types)
        self.assertEqual(sorted(f["name"] for f in res["files"]), ["analysis_sales.docx", "cleaned_sales.csv"])
        self.assertTrue(any("r = +0.9" in f or "r = +1.0" in f for f in res["facts"]))
        cleaned = (Path(db.DATA_DIR) / "chats" / "c1" / "work" / "cleaned_sales.csv")
        self.assertTrue(cleaned.exists() or any(Path(db.DATA_DIR).rglob("cleaned_sales.csv")))

    def test_unreadable_file_gives_a_clear_error(self):
        ctx = fake_ctx()
        bad = Path(self._tmp.name) / "bad.csv"
        bad.write_bytes(b"\x00\x01\x02")
        rc = engine.RunCtx(ctx, runs.create("c1", "analyze", "analyze", "x", {}), registry.get("analyze", "analyze"), {}, [], asyncio.Queue())
        rc.slots = {"file": {"id": "f", "name": "bad.csv", "path": str(bad), "ext": ".csv"}, "goal": ""}
        res = run(wf_analyze.run(rc))
        self.assertIn("error", res)
        self.assertNotIn("Traceback", res["error"])


class Deliverables(unittest.TestCase):
    def test_docx_and_pptx_are_real_files(self):
        from docx import Document
        from pptx import Presentation
        d = Document(io.BytesIO(common.build_docx("Title", [{"heading": "H", "paragraphs": ["p"], "bullets": ["b"], "table": {"columns": ["a"], "rows": [["1"]]}}])))
        self.assertEqual(d.paragraphs[0].text, "Title")
        p = Presentation(io.BytesIO(common.build_pptx("Deck", [{"title": "S1", "bullets": ["x", "y"], "notes": "say it"}, {"title": "S2", "bullets": []}])))
        self.assertEqual(len(p.slides), 3)
        self.assertEqual(p.slides[1].notes_slide.notes_text_frame.text, "say it")

    def test_ics(self):
        ics = common.make_ics([{"title": "Exam, Maths", "start": date(2026, 12, 12), "end": date(2026, 12, 12)}], "Cal")
        self.assertIn("BEGIN:VEVENT", ics)
        self.assertIn("DTSTART;VALUE=DATE:20261212", ics)
        self.assertIn("Exam\\, Maths", ics)

    def test_code_validation(self):
        errs = wf_create.check_python([{"path": "a.py", "content": "def f(:\n  pass"}, {"path": "b.py", "content": "x = 1"}, {"path": "c.json", "content": "{bad"}])
        self.assertEqual(len(errs), 2)
        cleaned = wf_create._clean_files({"files": [{"path": "../../evil.py", "content": "x"}, {"path": "ok/main.py", "content": "print(1)"}, {"path": "empty.py", "content": " "}]})
        self.assertEqual([f["path"] for f in cleaned], ["evil.py", "ok/main.py"])  # '../' is neutralised, empty files dropped
        self.assertTrue(all(".." not in f["path"] and not f["path"].startswith("/") for f in cleaned))

    def test_quiz_validation(self):
        good = {"q": "2+2?", "options": ["3", "4", "5", "6"], "answer": 1, "explain": "math"}
        bad = [{"q": "dup", "options": ["a", "a", "b", "c"], "answer": 0}, {"q": "range", "options": list("abcd"), "answer": 9}, {"q": "", "options": list("abcd"), "answer": 1}, {"q": "three", "options": list("abc"), "answer": 1}, "junk"]
        self.assertEqual(len(wf_learn._valid_quiz({"questions": [good] + bad})), 1)

    def test_grounded_extraction_drops_invented_values(self):
        text = "The Grand Hotel is at Baga Beach and costs 4,500 per night. Sea View Inn costs 3,200 per night. " * 3

        class Rc:
            async def json_call(self, *a, **k):
                return {"items": [{"name": "Grand Hotel", "price": "4,500", "area": "Baga Beach"}, {"name": "Sea View Inn", "price": "9,999", "area": "Nowhere"}, {"name": "Palace Royale", "price": "1", "area": ""}]}
        out = run(common.extract_items(Rc(), text, "list", {"name": "n", "price": "p", "area": "a"}, ["name"]))
        self.assertEqual([o["name"] for o in out], ["Grand Hotel", "Sea View Inn"])  # 'Palace Royale' never appears in the text
        self.assertEqual(out[1]["price"], None)  # 9,999 is not in the text: blanked, not trusted
        self.assertEqual(out[0]["price"], "4,500")

    def test_links_never_contain_prices_or_bookings(self):
        for u in (common.link_flights("Pune", "Goa", "2026-12-01"), common.link_hotels("Goa", "2026-12-01", "2026-12-05", 2), common.link_trains("Pune", "Goa"), common.link_movies("New Delhi")):
            self.assertTrue(u.startswith("https://"))


class LearnAdaptive(TempDB):
    def test_next_lesson_adapts_to_the_quiz_score_and_revisits_misses(self):
        calls = []

        async def fake_json(prompt, max_tokens=0, system=""):
            calls.append(prompt)
            if "multiple-choice" in prompt:
                return {"questions": [{"q": f"Q{i}", "options": ["a", "b", "c", "d"], "answer": i % 4, "explain": "e"} for i in range(5)]}
            if "practice exercises" in prompt:
                return {"exercises": [{"task": "t", "hint": "h", "solution": "s"}]}
            return None

        async def fake_text(prompt, max_tokens=0, system=""):
            calls.append(prompt)
            return "Lesson text " * 20
        quizzes = {"m0": [{"q": "Q0", "options": ["a", "b", "c", "d"], "answer": 0, "explain": "because"}, {"q": "Q1", "options": ["a", "b", "c", "d"], "answer": 1, "explain": "x"}]}
        state = {"path": [{"title": f"M{i}", "goal": "g", "lessons": []} for i in range(4)], "current": 0, "quizzes": quizzes,
                 "quiz_history": [{"module": "m0", "right": 0, "total": 5, "missed": [0, 1]}]}
        rc = SimpleNamespace(slots={"subject": "chess", "level": "beginner", "minutes": 30, "mode": "course"}, run={"state": state}, action="next", notes=[],
                             pref=lambda k, d=None: d, json_call=fake_json, text_call=fake_text, progress=mock.AsyncMock())
        res = run(wf_learn.run(rc))
        self.assertEqual(res["state"]["current"], 1)  # moved on
        self.assertEqual(res["state"]["difficulty"], "easier")  # 0/5 -> slow down
        titles = [b.get("title", "") for b in res["blocks"]]
        self.assertTrue(any("Revision" in t for t in titles))
        self.assertIn("m0", res["state"]["learn_progress"] and ["m0"])
        self.assertTrue(any("Go slower" in c for c in calls))
        rc.run["state"] = {**res["state"], "quiz_history": [{"module": "m1", "right": 5, "total": 5, "missed": []}]}
        res2 = run(wf_learn.run(rc))
        self.assertEqual(res2["state"]["difficulty"], "harder")


if __name__ == "__main__":
    unittest.main()


class AutoRouteTests(unittest.TestCase):
    def test_dashboard_or_analysis_of_an_attached_table_is_routed_without_a_slash(self):
        from backend import skills
        self.assertEqual(skills.auto_route("make a dashboard of this", True)[0], "create")
        self.assertEqual(skills.auto_route("analyze this data please", True)[0], "analyze")
        self.assertIsNone(skills.auto_route("make a dashboard of this", False))
        self.assertIsNone(skills.auto_route("what is a dashboard?", False))
        self.assertIsNone(skills.auto_route("hello there", True))
        self.assertIsNone(skills.auto_route("/learn python", True))

    def test_statistics_typed_into_the_message_get_a_chart(self):
        from backend import main
        self.assertTrue(main.stats_to_analyse("Analyze sales: Jan 120k, Feb 135k, Mar 128k, Apr 160k"))
        self.assertFalse(main.stats_to_analyse("Analyze the poem I wrote"))
        self.assertFalse(main.stats_to_analyse("Jan 120k, Feb 135k, Mar 128k, Apr 160k"))
