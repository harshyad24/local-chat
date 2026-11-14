"""Tests for the built-in chart tool (data -> spec) and how the agent loop uses it.

Run:  python -m unittest discover -s tests -v
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import agent, charts, db, main, tools  # noqa: E402

from test_agent import FakeLLM, call, make_ctx, run, run_agent  # noqa: E402


class BuildSpec(unittest.TestCase):
    def test_simple_bar_from_labels_and_values(self):
        spec, summary = charts.build({"type": "bar_chart", "title": "T", "labels": ["A", "B"], "values": [1, "1,200"]})
        self.assertEqual(spec["visualization_type"], "bar_chart")
        self.assertEqual([d["y"] for d in spec["series"][0]["data"]], [1, 1200])
        self.assertIn("2 values", summary)

    def test_aliases_and_unknown_types(self):
        self.assertEqual(charts.build({"type": "pie", "labels": ["a", "b"], "values": [1, 2]})[0]["visualization_type"], "pie_chart")
        with self.assertRaises(charts.ChartError) as cm:
            charts.build({"type": "hologram", "labels": ["a"], "values": [1]})
        self.assertIn("Valid types", str(cm.exception))

    def test_length_mismatch_gives_actionable_error(self):
        with self.assertRaises(charts.ChartError) as cm:
            charts.build({"type": "bar_chart", "labels": ["A", "B", "C"], "values": [1, 2]})
        self.assertIn("same length", str(cm.exception))

    def test_non_numeric_values_rejected(self):
        with self.assertRaises(charts.ChartError):
            charts.build({"type": "bar_chart", "labels": ["A"], "values": ["lots"]})

    def test_negative_pie_rejected(self):
        with self.assertRaises(charts.ChartError):
            charts.build({"type": "pie_chart", "labels": ["A", "B"], "values": [3, -1]})

    def test_multi_series(self):
        spec, _ = charts.build({"type": "line_chart", "labels": ["Q1", "Q2"], "series": [{"name": "a", "values": [1, 2]}, {"name": "b", "values": [3, 4]}]})
        self.assertEqual(len(spec["series"]), 2)

    def test_scatter_and_bubble_points(self):
        s, _ = charts.build({"type": "scatter_plot", "series": [{"name": "p", "points": [{"x": 1, "y": 2}, [3, 4]]}]})
        self.assertEqual(s["series"][0]["data"][1], {"x": 3, "y": 4})
        with self.assertRaises(charts.ChartError):
            charts.build({"type": "bubble_chart", "series": [{"name": "p", "points": [{"x": 1, "y": 2}]}]})

    def test_graph_infers_nodes_from_edges(self):
        spec, summary = charts.build({"type": "network_graph", "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}]})
        self.assertEqual(spec["nodes"], ["a", "b", "c"])
        self.assertIn("3 nodes", summary)

    def test_sankey_needs_positive_values(self):
        with self.assertRaises(charts.ChartError):
            charts.build({"type": "sankey_diagram", "edges": [{"source": "a", "target": "b", "value": 0}]})
        spec, _ = charts.build({"type": "sankey_diagram", "edges": [{"source": "a", "target": "b", "value": "5"}]})
        self.assertEqual(spec["edges"][0]["value"], 5)

    def test_candlestick_consistency_checked(self):
        with self.assertRaises(charts.ChartError):
            charts.build({"type": "candlestick_chart", "ohlc": [{"label": "d1", "open": 10, "high": 9, "low": 8, "close": 9}]})

    def test_box_plot_groups(self):
        spec, _ = charts.build({"type": "box_plot", "groups": [{"name": "a", "values": [1, 2, 3, 4, 100]}]})
        self.assertEqual(spec["groups"][0]["name"], "a")

    def test_gauge(self):
        spec, _ = charts.build({"type": "gauge_chart", "values": [72], "min": 0, "max": 100})
        self.assertEqual(spec["value"], 72)

    def test_every_listed_type_has_a_working_minimal_example(self):
        """Guards against a type being advertised to the model but rejected by the builder."""
        examples = {
            "gauge_chart": {"values": [5], "max": 10},
            "box_plot": {"groups": [{"name": "g", "values": [1, 2, 3, 4]}]},
            "candlestick_chart": {"ohlc": [{"label": "a", "open": 1, "high": 3, "low": 0, "close": 2}]},
            "sankey_diagram": {"edges": [{"source": "a", "target": "b", "value": 1}]},
            "scatter_plot": {"series": [{"name": "s", "points": [{"x": 1, "y": 1}]}]},
            "bubble_chart": {"series": [{"name": "s", "points": [{"x": 1, "y": 1, "r": 2}]}]},
            "histogram": {"values": [1, 2, 3, 4, 5]},
            "slope_chart": {"labels": ["2020", "2024"], "values": [1, 2]},
        }
        for t in charts.TYPES:
            if t in charts._GRAPHS:
                args = {"nodes": ["a", "b"], "edges": [{"source": "a", "target": "b"}]}
            else:
                args = examples.get(t, {"labels": ["a", "b", "c"], "values": [1, 2, 3]})
            spec, _ = charts.build({"type": t, **args})
            self.assertEqual(spec["visualization_type"], t)


class ToolWiring(unittest.TestCase):
    def test_chart_and_code_tools_are_built_in_and_always_on(self):
        names = tools.enabled_tools(db.DEFAULT_SETTINGS, web=False, has_files=False)
        self.assertIn("create_chart", names)
        self.assertIn("run_python", names)
        self.assertIn("run_python", tools.IMPLS)
        self.assertNotIn("use_code", db.DEFAULT_SETTINGS, "no per-chat code switch any more")

    def test_code_runs_in_the_sandbox_and_network_is_blocked(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            saved = db.DATA_DIR
            db.DATA_DIR = Path(tmp)
            try:
                code = (
                    "import socket\n"
                    "print(sum(range(10)))\n"
                    "try:\n"
                    "    socket.create_connection(('example.com', 80), 2)\n"
                    "except OSError:\n"
                    "    print('blocked')\n"
                )
                res = run(tools.run_python(db.DEFAULT_SETTINGS, "c1", {"code": code}))
            finally:
                db.DATA_DIR = saved
        self.assertTrue(res.ok, res.content)
        self.assertIn("45", res.content)
        self.assertIn("blocked", res.content)

    def test_tool_result_carries_the_spec_and_tells_model_not_to_repeat_data(self):
        res = run(tools.create_chart({}, "c", {"type": "bar_chart", "title": "Sales", "labels": ["a", "b"], "values": [1, 2]}))
        self.assertTrue(res.ok)
        self.assertEqual(res.detail["spec"]["title"], "Sales")
        self.assertIn("displayed to the user", res.content)

    def test_bad_arguments_become_a_tool_error_the_model_can_fix(self):
        with self.assertRaises(tools.ToolError) as cm:
            run(tools.create_chart({}, "c", {"type": "bar_chart", "labels": ["a", "b"], "values": [1]}))
        self.assertIn("call create_chart again", str(cm.exception))

    def test_prompt_mentions_the_tool_but_not_chart_json(self):
        p = agent.system_prompt(db.DEFAULT_SETTINGS, ["create_chart"], "off")
        self.assertIn("create_chart", p)
        self.assertNotIn("visualization_type", p)

    def test_plain_chat_names_do_not_trigger_the_narration_detector(self):
        self.assertFalse(agent.looks_unexecuted("I will search the web for that.", ["create_chart"]))

    def test_chart_request_regex(self):
        for t in ("make a bar chart of sales", "draw a network graph", "visualize this", "show a heatmap", "plot y=x"):
            self.assertTrue(main.ASKS_CHART.search(t), t)
        for t in ("explain photosynthesis", "what's the capital of France"):
            self.assertFalse(main.ASKS_CHART.search(t), t)


class AgentUsesChartTool(unittest.TestCase):
    NAMES = ["create_chart"]

    def test_successful_chart_is_not_reported_as_task_state_noise(self):
        ctx = make_ctx(self.NAMES)
        args = {"type": "bar_chart", "title": "T", "labels": ["a", "b"], "values": [1, 2]}
        turns = [{"calls": [call("create_chart", **args)]}, {"text": "Bars show b is double a."}]
        events, _ = run_agent(ctx, turns, {})
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertTrue(results[0]["ok"])
        self.assertIn("spec", results[0]["detail"])
        self.assertIsNone(ctx.state["state"])

    def test_model_fixes_a_bad_chart_after_the_error_comes_back(self):
        ctx = make_ctx(self.NAMES)
        bad = {"type": "bar_chart", "labels": ["a", "b", "c"], "values": [1, 2]}
        good = {"type": "bar_chart", "labels": ["a", "b", "c"], "values": [1, 2, 3]}
        turns = [{"calls": [call("create_chart", **bad)]}, {"calls": [call("create_chart", **good)]}, {"text": "Done."}]
        events, fake = run_agent(ctx, turns, {})
        oks = [e["ok"] for e in events if e["type"] == "tool_result"]
        self.assertEqual(oks, [False, True])
        self.assertIn("same length", fake.prompts[1][-1]["content"])
        self.assertIsNone(ctx.state["state"], "a corrected retry that drew the chart is not a failed run")

    def test_chart_that_never_gets_drawn_is_reported_as_failed(self):
        ctx = make_ctx(self.NAMES)
        bad = {"type": "bar_chart", "labels": ["a", "b", "c"], "values": [1, 2]}
        run_agent(ctx, [{"calls": [call("create_chart", **bad)]}, {"text": "Sorry."}], {})
        self.assertEqual(ctx.state["state"], "failed")

    def test_model_that_answers_in_text_is_nudged_once_to_use_the_chart_tool(self):
        ctx = make_ctx(self.NAMES)
        ctx.wants_chart = True
        good = {"type": "pie_chart", "labels": ["a", "b"], "values": [1, 2]}
        turns = [{"text": "Here is an ASCII bar: ####"}, {"calls": [call("create_chart", **good)]}, {"text": "Shown."}]
        events, fake = run_agent(ctx, turns, {})
        kinds = [e["type"] for e in events]
        self.assertIn("truncate", kinds)
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertIn("create_chart tool NOW", fake.prompts[1][-1]["content"])
        self.assertNotIn("ASCII", ctx.state["acc"])

    def test_no_nudge_when_the_user_did_not_ask_for_a_chart(self):
        ctx = make_ctx(self.NAMES)
        events, _ = run_agent(ctx, [{"text": "Photosynthesis turns light into sugar."}], {})
        self.assertNotIn("truncate", [e["type"] for e in events])

    def test_a_mermaid_flowchart_counts_as_a_visual_answer(self):
        ctx = make_ctx(self.NAMES)
        ctx.wants_chart = True
        events, _ = run_agent(ctx, [{"text": "```mermaid\nflowchart TD\nA-->B\n```"}], {})
        self.assertNotIn("truncate", [e["type"] for e in events])


class RescuedChart(unittest.TestCase):
    def test_json_block_instead_of_a_tool_call_becomes_a_real_chart(self):
        ctx = make_ctx(["create_chart"])
        ctx.wants_chart = True
        text = 'Here you go:\n```json\n{"type": "pie_chart", "title": "T", "labels": ["a", "b"], "values": [1, 2]}\n```'
        events, _ = run_agent(ctx, [{"text": text}, {"text": "Shown."}], {})
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] and "spec" in e["detail"] for e in events))
        self.assertNotIn("```json", ctx.state["acc"])

    def test_invalid_json_block_is_not_rescued(self):
        self.assertIsNone(agent.rescue_chart_call('```json\n{"type": "pie_chart", "labels": ["a"], "values": [1, 2]}\n```'))
        self.assertIsNone(agent.rescue_chart_call('```json\n{"name": "x"}\n```'))


class ExtractedChart(unittest.TestCase):
    """Models like deepseek-r1 write fake chart text instead of calling the tool; the app asks for JSON only."""

    def _run(self, reply):
        ctx = make_ctx(["create_chart"])
        ctx.wants_chart = True
        ctx.prompt.append({"role": "user", "content": "bar chart of sales Q1 120, Q2 150"})

        async def fake_complete(*a, **kw):
            self.assertTrue(kw.get("json_mode"))
            return reply

        original = agent.llm.complete
        agent.llm.complete = fake_complete
        try:
            return run_agent(ctx, [{"text": "```chart\ncreate_chart\ntype: bar\n```"}, {"text": "Shown."}], {})
        finally:
            agent.llm.complete = original

    def test_fake_chart_text_becomes_a_real_chart(self):
        events, _ = self._run('{"type": "bar", "labels": ["Q1", "Q2"], "values": [120, 150]}')
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] and "spec" in e["detail"] for e in events))
        self.assertNotIn("create_chart\ntype", agent.strip_think(events[-1].get("text", "")))

    def test_no_data_or_junk_falls_back_to_the_nudge(self):
        events, fake = self._run('{"error": "no data"}')
        self.assertFalse(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertTrue(any(e["type"] == "notice" and "chart tool" in e["text"] for e in events))


class MalformedToolCall(unittest.TestCase):
    NAMES = ["create_chart", "run_python"]
    GOOD = {"type": "bar_chart", "labels": ["a", "b"], "values": [1, 2]}

    def test_classifier_recognises_the_ollama_xml_error(self):
        e = agent.llm._classify(db.DEFAULT_SETTINGS, "m", 500, '{"error":"XML syntax error on line 24: element <function> closed by </parameter>"}')
        self.assertEqual(e.code, "bad_tool_call")

    def test_one_bad_call_is_retried_and_the_run_succeeds(self):
        ctx = make_ctx(self.NAMES)
        turns = [{"text": "partial", "raise": "bad_tool_call"}, {"calls": [call("create_chart", **self.GOOD)]}, {"text": "Shown."}]
        events, fake = run_agent(ctx, turns, {})
        kinds = [e["type"] for e in events]
        self.assertIn("truncate", kinds)
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertIn("malformed", fake.prompts[1][-1]["content"])
        self.assertNotIn("partial", ctx.state["acc"])

    def test_second_failure_switches_to_text_tool_format(self):
        ctx = make_ctx(self.NAMES)
        turns = [{"raise": "bad_tool_call"}, {"raise": "bad_tool_call"},
                 {"text": '<tool_call>{"name": "create_chart", "arguments": {"type": "bar_chart", "labels": ["a","b"], "values": [1,2]}}</tool_call>'},
                 {"text": "Shown."}]
        events, fake = run_agent(ctx, turns, {})
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertIn("<tool_call>", fake.prompts[2][0]["content"])

    def test_endless_failures_surface_the_error_instead_of_looping(self):
        ctx = make_ctx(self.NAMES)
        with self.assertRaises(agent.llm.LLMError):
            run_agent(ctx, [{"raise": "bad_tool_call"}] * 6, {})


if __name__ == "__main__":
    unittest.main()
