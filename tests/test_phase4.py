"""Phase 4 tests: memory, assistants, projects, dashboards.

Run:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import agent, dashboards, db, library, tools  # noqa: E402

from test_agent import FakeLLM, TempDB, call, make_ctx, run, run_agent  # noqa: E402


class Detect(unittest.TestCase):
    def test_remember_phrases(self):
        for text, fact in [
            ("Remember that I prefer metric units", "I prefer metric units"),
            ("please remember my dog is called Biscuit.", "my dog is called Biscuit"),
            ("Don't forget I work night shifts", "I work night shifts"),
        ]:
            self.assertEqual(library.detect_remember(text), fact, text)

    def test_not_a_memory_request(self):
        self.assertIsNone(library.detect_remember("remember to buy milk"))
        self.assertIsNone(library.detect_remember("Can you remember what we said yesterday?"))
        self.assertIsNone(library.detect_remember("I can't remember my password"))

    def test_forget_phrase(self):
        self.assertEqual(library.detect_forget("forget that I like jazz"), "I like jazz")
        self.assertIsNone(library.detect_forget("Don't forget the milk"))


class Memory(TempDB):
    def test_save_dedupe_and_secrets(self):
        row, status = library.add_memory("Prefers metric units")
        self.assertEqual(status, "saved")
        self.assertEqual(library.add_memory("prefers  METRIC units!")[1], "duplicate")
        self.assertEqual(library.add_memory("my password is hunter2")[1], "secret")
        self.assertEqual(library.add_memory("  ")[1], "empty")
        self.assertEqual(len(library.list_memories()), 1)

    def test_forget_matches_by_words(self):
        library.add_memory("The user's dog is called Biscuit")
        library.add_memory("The user likes jazz")
        removed = library.forget_matching("dog is called Biscuit")
        self.assertEqual(len(removed), 1)
        self.assertEqual([m["text"] for m in library.list_memories()], ["The user likes jazz"])

    def test_memory_goes_into_the_system_prompt_and_can_be_switched_off(self):
        library.add_memory("Lives in Pune")
        chat = db.create_chat("fake")
        s = dict(db.DEFAULT_SETTINGS)
        extras = library.prompt_extras(chat, s)
        self.assertIn("Lives in Pune", agent.system_prompt(s, ["create_chart"], "off", None, extras))
        s["memory_enabled"] = False
        self.assertNotIn("Lives in Pune", agent.system_prompt(s, [], "off", None, library.prompt_extras(chat, s)))

    def test_save_memory_tool(self):
        res = run(tools.save_memory(dict(db.DEFAULT_SETTINGS), "c", {"fact": "Works as a nurse"}))
        self.assertTrue(res.ok)
        self.assertEqual([m["text"] for m in library.list_memories()], ["Works as a nurse"])
        with self.assertRaises(tools.ToolError):
            run(tools.save_memory(dict(db.DEFAULT_SETTINGS), "c", {"fact": "api key is abc"}))

    def test_tool_is_listed_only_when_memory_is_on(self):
        s = dict(db.DEFAULT_SETTINGS)
        self.assertIn("save_memory", tools.enabled_tools(s, web=False, has_files=False))
        s["memory_enabled"] = False
        self.assertNotIn("save_memory", tools.enabled_tools(s, web=False, has_files=False))


class AssistantsAndProjects(TempDB):
    def test_assistant_prompt_reaches_the_system_prompt(self):
        a = library.save_assistant({"name": "Pirate", "prompt": "Answer like a pirate.", "model": "m1"})
        chat = db.create_chat("fake")
        db.update_chat(chat["id"], assistant_id=a["id"])
        s = dict(db.DEFAULT_SETTINGS)
        text = agent.system_prompt(s, [], "off", None, library.prompt_extras(db.get_chat(chat["id"]), s))
        self.assertIn("Answer like a pirate.", text)
        library.delete_assistant(a["id"])
        self.assertIsNone(db.get_chat(chat["id"])["assistant_id"])

    def test_project_instructions_and_files(self):
        p = library.save_project({"name": "Thesis", "instructions": "Use APA citations."})
        db.add_file("notes.txt", "text/plain", 5, "text", "x", "f1")
        library.add_project_file(p["id"], "f1")
        chat = db.create_chat("fake")
        db.update_chat(chat["id"], project_id=p["id"])
        s = dict(db.DEFAULT_SETTINGS)
        extras = library.prompt_extras(db.get_chat(chat["id"]), s)
        text = agent.system_prompt(s, ["read_file"], "off", None, extras)
        self.assertIn("Use APA citations.", text)
        self.assertIn("notes.txt", text)
        self.assertIn("f1", text)
        self.assertEqual(library.list_projects()[0]["chat_count"], 1)

    def test_name_required(self):
        self.assertIsNone(library.save_assistant({"name": " "}))
        self.assertIsNone(library.save_project({"name": ""}))


GOOD_DASH = {
    "template": "sales",
    "title": "Sales 2025",
    "period": "2025",
    "kpis": [
        {"label": "Total Revenue", "value": 1250000, "unit": "$", "change": 8.2},
        {"label": "Orders", "value": 4200},
        {"label": "Profit Margin", "value": 18.5, "unit": "%", "change": -1.1},
    ],
    "charts": [
        {"type": "line", "title": "Revenue", "labels": ["Jan", "Feb", "Mar"], "values": [10, 12, 15]},
        {"type": "bar", "title": "By category", "labels": ["A", "B"], "values": [3, 4]},
        {"type": "donut", "title": "By region", "labels": ["N", "S"], "values": [60, 40]},
    ],
    "table": {"columns": ["Product", "Revenue"], "rows": [["A", 10], ["B", 5]]},
}


class Dashboards(unittest.TestCase):
    def test_build_normalises_kpis_and_lays_out_charts(self):
        spec, summary, warnings = dashboards.build(GOOD_DASH)
        self.assertEqual([k["value"] for k in spec["kpis"]], ["$1.25M", "4.2K", "18.5%"])
        self.assertEqual(spec["kpis"][2]["change"], -1.1)
        self.assertEqual(len(spec["charts"]), 3)
        self.assertTrue(spec["charts"][0]["wide"])          # the trend takes the full row
        self.assertEqual([c["wide"] for c in spec["charts"]], [True, False, False])  # the other two sit side by side
        four = dashboards.build({**GOOD_DASH, "charts": GOOD_DASH["charts"] + [{"type": "area", "title": "More", "labels": ["a", "b"], "values": [5, 6]}]})[0]
        self.assertEqual([c["wide"] for c in four["charts"]], [True, False, False, True])  # no hole in the last row
        self.assertEqual(spec["table"]["rows"][0], ["A", "10"])
        self.assertEqual(warnings, [])
        self.assertIn("3 KPIs", summary)

    def test_template_guess_and_aliases(self):
        self.assertEqual(dashboards.detect_template("a dashboard for employee attrition"), "hr")
        self.assertEqual(dashboards.detect_template("campaign ROAS dashboard"), "marketing")
        self.assertEqual(dashboards.detect_template("a dashboard of my stuff"), "general")
        self.assertEqual(dashboards.build({**GOOD_DASH, "template": "finance"})[0]["template"], "financial")
        self.assertEqual(dashboards.build({**GOOD_DASH, "template": "nonsense"})[0]["template"], "general")

    def test_too_few_kpis_or_charts_is_actionable(self):
        with self.assertRaises(dashboards.DashboardError) as cm:
            dashboards.build({**GOOD_DASH, "kpis": GOOD_DASH["kpis"][:1]})
        self.assertIn("at least 3 KPI", str(cm.exception))
        with self.assertRaises(dashboards.DashboardError):
            dashboards.build({**GOOD_DASH, "charts": GOOD_DASH["charts"][:1]})

    def test_a_bad_chart_is_skipped_not_fatal(self):
        bad = {"type": "bar", "labels": ["A", "B", "C"], "values": [1, 2]}
        spec, _, warnings = dashboards.build({**GOOD_DASH, "charts": [bad, *GOOD_DASH["charts"]]})
        self.assertEqual(len(spec["charts"]), 3)
        self.assertEqual(len(warnings), 1)

    def test_pareto_and_map_become_drawable_charts(self):
        pareto = {"type": "pareto", "title": "Defects", "labels": ["a", "b", "c"], "values": [1, 5, 3]}
        spec, _, _ = dashboards.build({**GOOD_DASH, "charts": [pareto, GOOD_DASH["charts"][0]]})
        self.assertEqual(spec["charts"][0]["spec"]["visualization_type"], "bar_chart")
        self.assertEqual(spec["charts"][0]["spec"]["series"][0]["data"][0]["y"], 5)  # largest first

    def test_blueprint_names_the_pattern(self):
        text = dashboards.blueprint("sales")
        self.assertIn("Total Revenue", text)
        self.assertIn("Monthly revenue trend", text)


class DashboardInAgent(unittest.TestCase):
    def _ctx(self):
        ctx = make_ctx(["create_chart", "create_dashboard"])
        ctx.wants_dashboard = True
        ctx.dash_template = "sales"
        return ctx

    def test_tool_call_draws_a_dashboard(self):
        ctx = self._ctx()
        events, _ = run_agent(ctx, [{"calls": [call("create_dashboard", **GOOD_DASH)]}, {"text": "Revenue is up."}], {})
        res = [e for e in events if e["type"] == "tool_result"]
        self.assertTrue(res[0]["ok"])
        self.assertEqual(res[0]["detail"]["spec"]["kind"], "dashboard")
        self.assertEqual(ctx.state["state"], None)  # a drawn dashboard is not "work to report"

    def test_json_block_instead_of_a_call_is_rescued(self):
        import json
        ctx = self._ctx()
        text = "Here:\n```json\n" + json.dumps(GOOD_DASH) + "\n```"
        events, _ = run_agent(ctx, [{"text": text}, {"text": "Done."}], {})
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertNotIn("```json", ctx.state["acc"])

    def test_fallback_extraction_for_models_that_skip_the_tool(self):
        import json
        ctx = self._ctx()
        ctx.prompt.append({"role": "user", "content": "sales dashboard: revenue Jan 10 Feb 12 Mar 15"})

        async def fake_complete(*a, **kw):
            self.assertTrue(kw.get("json_mode"))
            return json.dumps(GOOD_DASH)

        with mock.patch.object(agent.llm, "complete", fake_complete):
            events, _ = run_agent(ctx, [{"text": "Dashboard idea: KPIs and charts"}, {"text": "Done."}], {})
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] and e["detail"]["spec"]["kind"] == "dashboard" for e in events))

    def test_system_prompt_pins_the_template(self):
        s = dict(db.DEFAULT_SETTINGS)
        text = agent.system_prompt(s, ["create_dashboard"], "off", None, {"dashboard": "hr"})
        self.assertIn("Attrition Rate", text)
        self.assertNotIn("Attrition Rate", agent.system_prompt(s, ["create_dashboard"], "off", None, {}))


class DeepResearch(unittest.TestCase):
    """The five-stage workflow with a scripted model and fake web tools."""

    def _impls(self):
        async def web_search(s, chat_id, args):
            q = args["query"]
            items = [{"title": f"{q} result {i}", "url": f"https://site{i}.example/{abs(hash(q)) % 999}", "snippet": f"snippet {i} about {q}"}
                     for i in range(3)]
            return tools.ToolResult(True, "", f"{len(items)} results", {"query": q, "results": items},
                                    sources=[{"title": x["title"], "url": x["url"]} for x in items])

        async def fetch_page(s, chat_id, args):
            return tools.ToolResult(True, "Page text " * 40, "page", {"url": args["url"], "title": "T", "chars": 400},
                                    sources=[{"title": "T " + args["url"], "url": args["url"]}])
        return {"web_search": web_search, "fetch_page": fetch_page}

    def test_all_stages_run_and_the_report_streams(self):
        import json
        from backend import research

        ctx = make_ctx(["web_search", "fetch_page"])
        plan = {"questions": [{"q": "How big is the market?", "queries": ["market size"]},
                              {"q": "Who are the competitors?", "queries": ["competitors"]}]}
        calls = []

        async def fake_complete(s, model, messages, max_tokens=48, json_mode=False, num_ctx=2048):
            calls.append(messages[0]["content"][:30])
            if "plan web research" in messages[0]["content"]:
                return json.dumps(plan)
            if "review research notes" in messages[0]["content"]:
                return json.dumps({"queries": ["market size 2025 forecast"]})
            return "The market is large [1]."

        async def go():
            return [e async for e in research.run_research(ctx, "Evaluate the e-bike market")]

        fake = FakeLLM([{"text": "## Executive summary\nBig market [1]."}])
        with mock.patch.object(agent.llm, "complete", fake_complete), \
                mock.patch.object(agent.llm, "stream_chat", fake), mock.patch.dict(tools.IMPLS, self._impls()):
            events = run(go())

        stages = [e["label"] for e in events if e["type"] == "progress"]
        for label in research.STAGES:
            self.assertIn(label, stages)
        names = [e["name"] for e in events if e["type"] == "tool_start"]
        self.assertEqual(names[0], "research_plan")
        self.assertGreaterEqual(names.count("web_search"), 3)      # 2 sub-questions + 1 gap check
        self.assertGreaterEqual(names.count("fetch_page"), 2)
        self.assertIn("Executive summary", ctx.state["acc"])
        self.assertTrue(ctx.state["sources"])
        # the writer was handed the notes and the numbered sources, and told to use only them
        user = fake.prompts[0][1]["content"]
        self.assertIn("NOTES:", user)
        self.assertIn("[1]", user)

    def test_no_search_results_gives_an_honest_message(self):
        from backend import research

        ctx = make_ctx(["web_search", "fetch_page"])

        async def empty_search(s, chat_id, args):
            raise tools.ToolError("Search failed")

        async def fake_complete(*a, **kw):
            return "{}"

        async def go():
            return [e async for e in research.run_research(ctx, "anything")]

        with mock.patch.object(agent.llm, "complete", fake_complete), mock.patch.dict(tools.IMPLS, {"web_search": empty_search}):
            run(go())
        self.assertIn("could not retrieve any web results", ctx.state["acc"])

    def test_plan_is_always_usable(self):
        from backend import research
        self.assertEqual(research.plan_questions(None, "q?")[0]["queries"], ["q?"])
        plan = research.plan_questions({"questions": ["a", {"q": "b", "queries": ["x", "y", "z"]}, 5]}, "q?")
        self.assertEqual([p["q"] for p in plan], ["a", "b"])
        self.assertEqual(plan[1]["queries"], ["x", "y"])


if __name__ == "__main__":
    unittest.main()


class Importing(TempDB):
    CHATGPT = [{
        "title": "Trip planning",
        "current_node": "a2",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["sys"]},
            "sys": {"id": "sys", "message": {"author": {"role": "system"}, "content": {"parts": [""]}}, "parent": "root", "children": ["u1"]},
            "u1": {"id": "u1", "message": {"author": {"role": "user"}, "content": {"parts": ["Plan Rome"]}}, "parent": "sys", "children": ["a1", "a2"]},
            "a1": {"id": "a1", "message": {"author": {"role": "assistant"}, "content": {"parts": ["Draft one"]}}, "parent": "u1", "children": []},
            "a2": {"id": "a2", "message": {"author": {"role": "assistant"}, "content": {"parts": ["Draft two"]}}, "parent": "u1", "children": []},
        },
    }]

    def test_chatgpt_tree_keeps_branches_and_selected_leaf(self):
        from backend import importer
        out = importer.import_data(self.CHATGPT)
        self.assertEqual(out["chats"], 1)
        chat = db.list_chats()[0]
        self.assertEqual(chat["title"], "Trip planning")
        msgs = db.get_messages(chat["id"])
        self.assertEqual([m["content"] for m in msgs], ["Plan Rome", "Draft one", "Draft two"])
        self.assertEqual(msgs[1]["parent_id"], msgs[0]["id"])   # system node skipped, branch preserved
        self.assertEqual(msgs[2]["parent_id"], msgs[0]["id"])
        self.assertEqual([m["content"] for m in db.get_path(chat["id"])], ["Plan Rome", "Draft two"])

    def test_claude_export(self):
        from backend import importer
        out = importer.import_data([{"name": "Hello", "chat_messages": [
            {"sender": "human", "text": "hi"}, {"sender": "assistant", "text": "hello!"}]}])
        self.assertEqual(out["chats"], 1)
        self.assertEqual([m["role"] for m in db.get_path(db.list_chats()[0]["id"])], ["user", "assistant"])

    def test_own_export_round_trip(self):
        from backend import importer
        chat = db.create_chat("m")
        u = db.add_message(chat["id"], "user", "q", parent_id=None)
        a = db.add_message(chat["id"], "assistant", "a1", parent_id=u)
        b = db.add_message(chat["id"], "assistant", "a2", parent_id=u)
        exported = db.export_all()
        importer.import_data(exported)
        copies = [c for c in db.list_chats() if c["id"] != chat["id"]]
        self.assertEqual(len(copies), 1)
        self.assertEqual(len(db.get_messages(copies[0]["id"])), 3)

    def test_library_round_trip_and_rejects(self):
        from backend import importer
        library.add_memory("Likes tea")
        library.save_assistant({"name": "Coach", "prompt": "Be brief."})
        library.save_project({"name": "P1", "instructions": "x"})
        blob = importer.export_library()
        db.DB_PATH.unlink()
        db.init_db()
        out = importer.import_data(blob)
        self.assertEqual((out["memories"], out["assistants"], out["projects"]), (1, 1, 1))
        with self.assertRaises(importer.ImportError_):
            importer.import_data([{"nothing": 1}])
        with self.assertRaises(importer.ImportError_):
            importer.parse(b"not json")


class Guards(TempDB):
    def test_estimated_and_duplicate_charts_are_refused(self):
        est = {"type": "area", "title": "Weekly Sales Trend (Estimated)", "labels": ["W1", "W2"], "values": [1, 2]}
        dup = dict(GOOD_DASH["charts"][1])
        spec, _, warnings = dashboards.build({**GOOD_DASH, "charts": [*GOOD_DASH["charts"], est, dup]})
        self.assertEqual(len(spec["charts"]), 3)
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("never estimates" in w for w in warnings))
        self.assertTrue(any("repeats" in w for w in warnings))

    def test_table_numbers_are_formatted(self):
        spec, _, _ = dashboards.build(GOOD_DASH | {"table": {"columns": ["A", "B"], "rows": [["x", 400000], ["y", 37.456]]}})
        self.assertEqual(spec["table"]["rows"], [["x", "400,000"], ["y", "37.46"]])

    def test_near_duplicate_memories_are_merged(self):
        library.add_memory("my name is Alex and I prefer metric units")
        self.assertEqual(library.add_memory("Name is Alex and prefers metric units")[1], "duplicate")
        self.assertEqual(library.add_memory("Likes tea")[1], "saved")
        self.assertEqual(library.add_memory("Likes jazz")[1], "saved")


class ExtractAfterNudge(unittest.TestCase):
    def test_second_extraction_attempt_runs_when_the_nudged_reply_is_empty(self):
        import json
        ctx = make_ctx(["create_chart", "create_dashboard"])
        ctx.wants_dashboard, ctx.dash_template = True, "sales"
        ctx.prompt.append({"role": "user", "content": "sales dashboard"})
        replies = iter(['{"error": "no data"}', json.dumps(GOOD_DASH)])

        async def fake_complete(*a, **kw):
            return next(replies)

        with mock.patch.object(agent.llm, "complete", fake_complete):
            events, _ = run_agent(ctx, [{"text": "Here is my plan"}, {"text": ""}, {"text": "Done."}], {})
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertTrue(any(e["type"] == "notice" and "dashboard tool" in e["text"] for e in events))


class DashboardForModelsWithoutTools(unittest.TestCase):
    """deepseek-r1 style models: no native tool calling. The app asks for the dashboard data first instead of waiting for a free-form reply."""

    def _ctx(self):
        ctx = make_ctx(["create_chart", "create_dashboard"])
        ctx.caps = {"tools": False}
        ctx.wants_dashboard, ctx.dash_template = True, "sales"
        ctx.prompt.append({"role": "user", "content": "make a dashboard: revenue Jan 10 Feb 12 Mar 15"})
        return ctx

    def test_dashboard_is_built_before_the_model_writes_anything_else(self):
        import json
        ctx = self._ctx()

        async def fake_complete(*a, **kw):
            return json.dumps(GOOD_DASH)

        with mock.patch.object(agent.llm, "complete", fake_complete):
            events, fake = run_agent(ctx, [{"text": "Revenue is rising each month."}], {})
        kinds = [e["type"] for e in events]
        self.assertLess(kinds.index("tool_result"), kinds.index("delta"))
        self.assertTrue([e for e in events if e["type"] == "tool_result"][0]["ok"])
        self.assertEqual(len(fake.prompts), 1)  # one model turn: the insight, not a long free-form draft first
        self.assertIn("tool_result", fake.prompts[0][-1]["content"])

    def test_a_bad_first_json_is_repaired_with_the_error_shown_to_the_model(self):
        import json
        ctx = self._ctx()
        seen = []
        replies = iter(['{"template": "sales", "kpis": [], "charts": []}', json.dumps(GOOD_DASH)])

        async def fake_complete(s, model, msgs, **kw):
            seen.append(msgs[-1]["content"])
            return next(replies)

        with mock.patch.object(agent.llm, "complete", fake_complete):
            events, _ = run_agent(ctx, [{"text": "Done."}], {})
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertIn("rejected", seen[1])

    def test_failure_is_reported_instead_of_silently_ignored(self):
        ctx = self._ctx()

        async def fake_complete(*a, **kw):
            return "I cannot do that."

        with mock.patch.object(agent.llm, "complete", fake_complete):
            events, _ = run_agent(ctx, [{"text": "Here is some HTML code"}] * 4, {})
        self.assertFalse(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertTrue(any(e["type"] == "notice" and "could not be built" in e["text"] for e in events))


class DashboardForReasoningModelsWithTools(unittest.TestCase):
    def test_reasoning_model_with_native_tools_still_gets_the_dashboard_built_first(self):
        import json
        ctx = make_ctx(["create_dashboard"])  # create_chart is withheld on dashboard requests (main.py)
        ctx.model = "deepseek-r1:32b"
        ctx.caps = {"tools": True}
        ctx.wants_dashboard, ctx.dash_template = True, "sales"
        ctx.prompt.append({"role": "user", "content": "make a dashboard: revenue Jan 10 Feb 12 Mar 15"})

        async def fake_complete(*a, **kw):
            return json.dumps(GOOD_DASH)

        with mock.patch.object(agent.llm, "complete", fake_complete):
            events, fake = run_agent(ctx, [{"text": "Revenue is rising."}], {})
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] and e["detail"]["spec"]["kind"] == "dashboard" for e in events))
        self.assertEqual(len(fake.prompts), 1)
