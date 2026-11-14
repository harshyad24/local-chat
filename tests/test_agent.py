"""Agent reliability tests. Offline and deterministic: the model is a scripted fake.

Run:  python -m unittest discover -s tests -v
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from backend import agent, db, tools  # noqa: E402

SIX_URLS = [
    "https://apnews.com/hub/election-2026",
    "https://www.nbcnews.com/politics/2026-elections",
    "https://www.politico.com/2026-election/results",
    "https://www.nytimes.com/interactive/2026/11/03/us/elections/results.html",
    "https://www.cnn.com/election/2026/results",
    "https://www.reuters.com/world/us/2026-midterms/",
]


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """Replaces llm.stream_chat. Each scripted turn: {"text": str, "calls": [..]}"""

    def __init__(self, turns):
        self.turns = list(turns)
        self.prompts = []

    def __call__(self, s, model, messages, think=None, tools=None):
        self.prompts.append([dict(m) for m in messages])
        turn = self.turns.pop(0) if self.turns else {"text": "(script exhausted)"}

        async def gen():
            if turn.get("raise"):
                if turn.get("text"):
                    yield {"type": "text", "text": turn["text"]}
                raise agent.llm.LLMError(turn["raise"], "XML syntax error on line 24: element <function> closed by </parameter>")
            if turn.get("text"):
                yield {"type": "text", "text": turn["text"]}
            if turn.get("calls"):
                yield {"type": "tool_calls", "calls": turn["calls"]}
            yield {"type": "finish", "reason": "stop"}
            yield {"type": "usage", "in": 1, "out": 1}

        return gen()


def call(name, **args):
    return {"id": "call_0", "name": name, "arguments": args}


def make_ctx(names, prompt=None):
    s = dict(db.DEFAULT_SETTINGS)
    prompt = prompt or [{"role": "system", "content": "sys"}, {"role": "user", "content": "do it"}]
    return agent.Ctx(s, "fake", "chat1", prompt, None, names, {"tools": True})


def run_agent(ctx, turns, impls):
    fake = FakeLLM(turns)
    with mock.patch.object(agent.llm, "stream_chat", fake), mock.patch.dict(agent.tools.IMPLS, impls):
        async def go():
            return [e async for e in agent.run_agent(ctx)]
        events = run(go())
    return events, fake


def ok_fetch(url_to_text):
    async def impl(s, chat_id, args):
        url = args["url"]
        if url not in url_to_text:
            raise tools.ToolError("The site answered HTTP 404.")
        return tools.ToolResult(
            True, url_to_text[url], urlparse_host(url),
            {"url": url, "title": "T", "chars": len(url_to_text[url]), "excerpt": url_to_text[url][:50]},
            sources=[{"title": "T " + url, "url": url}],
        )
    return impl


def urlparse_host(u):
    return u.split("/")[2]


class TempDB(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._saved = (db.DATA_DIR, db.UPLOAD_DIR, db.DB_PATH)
        db.DATA_DIR, db.UPLOAD_DIR, db.DB_PATH = root, root / "uploads", root / "t.db"
        db.init_db()

    def tearDown(self):
        db.DATA_DIR, db.UPLOAD_DIR, db.DB_PATH = self._saved
        self._tmp.cleanup()


# ---------------------------------------------------------------- the observed failures
class ContextRetrieval(TempDB):
    def _chat_with_urls(self):
        chat = db.create_chat("fake")
        u1 = db.add_message(chat["id"], "user", "Sources for the election audit:\n" + "\n".join(SIX_URLS), parent_id=None)
        a1 = db.add_message(chat["id"], "assistant", "Got them. What should I do?", parent_id=u1)
        u2 = db.add_message(chat["id"], "user", "Now audit those sources.", parent_id=a1)
        return chat["id"], db.get_path(chat["id"])

    def test_search_chat_returns_all_six_urls_without_asking_user(self):
        chat_id, _ = self._chat_with_urls()
        res = run(tools.search_chat({}, chat_id, {"query": "urls"}))
        self.assertTrue(res.ok)
        for u in SIX_URLS:
            self.assertIn(u, res.content)
        self.assertEqual(res.detail["urls"], SIX_URLS)

    def test_search_chat_keyword_search_finds_the_message(self):
        chat_id, _ = self._chat_with_urls()
        res = run(tools.search_chat({}, chat_id, {"query": "politico"}))
        self.assertIn("politico", res.content)

    def test_urls_survive_context_trimming_because_they_live_in_the_system_prompt(self):
        _, history = self._chat_with_urls()
        filler = [{"role": "user", "content": "x" * 3000, "attachments": [], "meta": {}},
                  {"role": "assistant", "content": "y" * 3000, "attachments": [], "meta": {}}] * 4
        long_history = history[:1] + filler + history[1:]
        s = db.DEFAULT_SETTINGS | {"num_ctx": 2048}  # tiny window forces trimming
        prompt, trimmed = agent.build_messages(s, long_history, ["web_search", "fetch_page"], False, "on")
        self.assertGreater(trimmed, 0, "history should have been trimmed")
        system = prompt[0]["content"]
        for u in SIX_URLS:
            self.assertIn(u, system)
        self.assertIn("never ask them to paste again", system)


class InternetAwareness(unittest.TestCase):
    def test_prompt_says_yes_when_web_tools_are_on(self):
        p = agent.system_prompt(db.DEFAULT_SETTINGS, ["web_search", "fetch_page"], "on")
        self.assertIn("INTERNET ACCESS: YES", p)
        self.assertIn("Never say that you cannot browse", p)

    def test_prompt_says_no_and_how_to_fix_when_chip_is_off(self):
        p = agent.system_prompt(db.DEFAULT_SETTINGS, [], "off")
        self.assertIn("INTERNET ACCESS: NO", p)
        self.assertIn("Web search", p)

    def test_prompt_explains_disabled_tools(self):
        self.assertIn("disabled in Settings", agent.system_prompt(db.DEFAULT_SETTINGS, [], "disabled"))


class PlanningVersusExecution(unittest.TestCase):
    NAMES = ["web_search", "fetch_page"]

    def test_detector(self):
        d = agent.looks_unexecuted
        self.assertTrue(d("I will search the web for the latest results and then verify each source.", self.NAMES))
        self.assertTrue(d("Let me fetch each of those URLs first.", self.NAMES))
        self.assertTrue(d("I cannot browse the internet or open links.", self.NAMES))
        self.assertTrue(d("I don't have access to the links you supplied earlier.", self.NAMES))
        self.assertFalse(d("Let me know if you want me to search again.", self.NAMES))
        self.assertFalse(d("Democrats lead 254 to 162 [1]. " * 40, self.NAMES))
        self.assertFalse(d("I will search the web.", []), "no tools enabled -> nothing to nudge toward")

    def test_narration_is_retracted_nudged_and_then_the_tool_really_runs(self):
        fetched = []

        async def fetch(s, chat_id, args):
            fetched.append(args["url"])
            return await ok_fetch({SIX_URLS[0]: "AP: Democrats 254, Republicans 164 (called seats)."})(s, chat_id, args)

        ctx = make_ctx(self.NAMES)
        turns = [
            {"text": "I will fetch the first source and verify the figures."},          # talk, no action
            {"text": "", "calls": [call("fetch_page", url=SIX_URLS[0])]},               # after the nudge: acts
            {"text": "AP reports 254 Democratic seats called [1]."},
        ]
        events, fake = run_agent(ctx, turns, {"fetch_page": fetch})
        kinds = [e["type"] for e in events]
        self.assertEqual(fetched, [SIX_URLS[0]], "the tool must actually have run")
        self.assertIn("truncate", kinds, "the narration must be removed from the visible answer")
        self.assertIn("tool_start", kinds)
        self.assertEqual(ctx.state["state"], "completed")
        self.assertNotIn("I will fetch", ctx.state["acc"])
        self.assertIn("254", ctx.state["acc"])
        nudge = [m for m in fake.prompts[1] if m["role"] == "user"][-1]["content"]
        self.assertIn("call the appropriate tool RIGHT NOW", nudge)
        self.assertIn("live internet access", nudge)

    def test_model_that_never_acts_is_reported_not_completed(self):
        ctx = make_ctx(self.NAMES)
        turns = [{"text": "I will search the web now."}] * 5
        events, _ = run_agent(ctx, turns, {})
        self.assertEqual(ctx.state["state"], "unexecuted")
        self.assertTrue(any(e["type"] == "notice" and "never ran it" in e["text"] for e in events))
        self.assertEqual(ctx.state["tools"], [])


class ResearchWorkflow(unittest.TestCase):
    NAMES = ["web_search", "fetch_page"]

    def test_multiple_browser_actions_execute_in_order_and_sources_are_numbered(self):
        pages = {SIX_URLS[0]: "AP page text", SIX_URLS[1]: "NBC page text"}
        ctx = make_ctx(self.NAMES)
        turns = [
            {"calls": [call("fetch_page", url=SIX_URLS[0]), call("fetch_page", url=SIX_URLS[1])]},
            {"text": "AP says A [1]; NBC says B [2]."},
        ]
        events, fake = run_agent(ctx, turns, {"fetch_page": ok_fetch(pages)})
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual([r["ok"] for r in results], [True, True])
        self.assertEqual([s["n"] for s in ctx.state["sources"]], [1, 2])
        self.assertEqual(ctx.state["state"], "completed")
        tool_msgs = [m["content"] for m in fake.prompts[1] if m["role"] == "tool"]
        self.assertTrue(tool_msgs[0].startswith("[1]") and tool_msgs[1].startswith("[2]"))

    def test_unreachable_source_is_reported_honestly_and_state_is_partial(self):
        pages = {SIX_URLS[0]: "AP page text"}  # NBC missing -> 404
        ctx = make_ctx(self.NAMES)
        turns = [
            {"calls": [call("fetch_page", url=SIX_URLS[0]), call("fetch_page", url=SIX_URLS[1])]},
            {"text": "I could read AP [1] but NBC could not be opened (HTTP 404)."},
        ]
        events, fake = run_agent(ctx, turns, {"fetch_page": ok_fetch(pages)})
        results = [e for e in events if e["type"] == "tool_result"]
        self.assertEqual([r["ok"] for r in results], [True, False])
        self.assertEqual(ctx.state["state"], "partial")
        failed_msg = [m["content"] for m in fake.prompts[1] if m["role"] == "tool"][1]
        self.assertTrue(failed_msg.startswith("Error:"), "the model must be told the call failed")
        state_ev = [e for e in events if e["type"] == "state"][0]
        self.assertEqual((state_ev["ok"], state_ev["failed"]), (1, 1))

    def test_every_tool_failing_is_state_failed(self):
        ctx = make_ctx(self.NAMES)
        turns = [{"calls": [call("fetch_page", url=SIX_URLS[2])]}, {"text": "I couldn't open it."}]
        events, _ = run_agent(ctx, turns, {"fetch_page": ok_fetch({})})
        self.assertEqual(ctx.state["state"], "failed")

    def test_tool_crashing_mid_run_does_not_kill_the_run(self):
        async def boom(s, chat_id, args):
            raise RuntimeError("kaboom")

        ctx = make_ctx(self.NAMES)
        turns = [{"calls": [call("web_search", query="x")]}, {"text": "The search tool crashed, so I can't confirm this."}]
        events, fake = run_agent(ctx, turns, {"web_search": boom})
        res = [e for e in events if e["type"] == "tool_result"][0]
        self.assertFalse(res["ok"])
        self.assertIn("RuntimeError", [m["content"] for m in fake.prompts[1] if m["role"] == "tool"][0])
        self.assertEqual(ctx.state["state"], "failed")
        self.assertIn("can't confirm", ctx.state["acc"])

    def test_two_sources_with_different_numbers_both_reach_the_model_with_their_own_numbers(self):
        async def search(s, chat_id, args):
            items = [
                {"title": "AP", "url": "https://ap.example/a", "snippet": "Democrats 254, Republicans 162 (called)"},
                {"title": "NBC", "url": "https://nbc.example/b", "snippet": "Democrats 254, Republicans 164 (leading)"},
            ]
            return tools.ToolResult(True, "", "2 results", {"results": items}, sources=[{"title": i["title"], "url": i["url"]} for i in items])

        ctx = make_ctx(self.NAMES)
        turns = [{"calls": [call("web_search", query="house seats")]},
                 {"text": "AP counts 162 called [1]; NBC counts 164 leading [2]. They measure different things."}]
        _, fake = run_agent(ctx, turns, {"web_search": search})
        seen = [m["content"] for m in fake.prompts[1] if m["role"] == "tool"][0]
        self.assertIn("[1] AP", seen)
        self.assertIn("(called)", seen)
        self.assertIn("[2] NBC", seen)
        self.assertIn("(leading)", seen)

    def test_unsupported_citation_is_flagged(self):
        async def search(s, chat_id, args):
            return tools.ToolResult(True, "", "1", {"results": [{"title": "A", "url": "https://a.example/", "snippet": "s"}]},
                                    sources=[{"title": "A", "url": "https://a.example/"}])
        ctx = make_ctx(self.NAMES)
        turns = [{"calls": [call("web_search", query="q")]}, {"text": "Claim one [1]. Claim two [4]."}]
        events, _ = run_agent(ctx, turns, {"web_search": search})
        warn = [e for e in events if e["type"] == "notice" and "[4]" in e["text"]]
        self.assertTrue(warn, "citing [4] with only one source must raise a warning")
        self.assertEqual(agent.check_citations("ok [1] and `code[9]`", 1), [])

    def test_loop_is_bounded(self):
        ctx = make_ctx(self.NAMES)
        turns = [{"calls": [call("web_search", query=str(i))]} for i in range(30)]

        async def search(s, chat_id, args):
            return tools.ToolResult(True, "", "0", {"results": []})
        events, fake = run_agent(ctx, turns, {"web_search": search})
        self.assertLessEqual(len(fake.prompts), agent.MAX_ITERATIONS)
        self.assertTrue(any("limit reached" in e.get("text", "") for e in events))


# ------------------------------------------------------------------------- fetch_page itself
class FetchPage(unittest.TestCase):
    HTML = "<html><head><title>Results</title></head><body><article><p>Last updated: Nov 5, 2026 11:42 PM EST. " + "Votes counted. " * 30 + "</p></article></body></html>"
    NODATE = "<html><head><title>Results</title></head><body><article><p>" + "Votes counted. " * 40 + "</p></article></body></html>"

    def _fetch(self, html):
        async def dl(url):
            return html.encode(), "text/html", url
        with mock.patch.object(tools, "_download", dl):
            return run(tools.fetch_page(db.DEFAULT_SETTINGS, "c", {"url": "https://example.com/r"}))

    def test_stated_timestamp_is_extracted_and_passed_to_the_model(self):
        r = self._fetch(self.HTML)
        self.assertEqual(r.detail["page_date"], "Nov 5, 2026 11:42 PM EST")
        self.assertIn("Date stated on the page: Nov 5, 2026 11:42 PM EST", r.content)

    def test_missing_date_is_never_invented(self):
        r = self._fetch(self.NODATE)
        self.assertEqual(r.detail["page_date"], "")
        self.assertIn("none found (do not guess one)", r.content)

    def test_unreachable_site_retries_once_then_raises_a_clear_error(self):
        calls = []

        async def dl(url):
            calls.append(url)
            raise httpx.ConnectError("nope")

        async def no_sleep(_):
            return None

        with mock.patch.object(tools, "_download", dl), mock.patch.object(tools.asyncio, "sleep", no_sleep):
            with self.assertRaises(tools.ToolError) as cm:
                run(tools.fetch_page(db.DEFAULT_SETTINGS, "c", {"url": "https://example.com/r"}))
        self.assertEqual(len(calls), 2)
        self.assertIn("2 attempts", str(cm.exception))

    def test_private_addresses_are_refused(self):
        with self.assertRaises(tools.ToolError):
            run(tools.fetch_page(db.DEFAULT_SETTINGS, "c", {"url": "http://127.0.0.1:8000/api/settings"}))


if __name__ == "__main__":
    unittest.main()
