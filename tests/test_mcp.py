"""MCP client, n8n templates and LibreOffice conversion. The MCP tests start a real stdio MCP server (tests/mcp_echo_server.py).

Run:  python -m unittest discover -s tests -v
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from backend import agent, db, mcp_client, n8n, tools  # noqa: E402

from test_agent import FakeLLM, TempDB, call, make_ctx  # noqa: E402

SERVER = str(Path(__file__).resolve().parent / "mcp_echo_server.py")


def settings(enabled=True, always=()):
    s = dict(db.DEFAULT_SETTINGS)
    s["mcp_servers"] = json.dumps([{
        "name": "Echo Server", "transport": "stdio", "command": sys.executable, "args": [SERVER],
        "enabled": enabled, "always_allow": list(always),
    }])
    return s


def in_loop(coro_fn):
    """Run an async test body, always stopping the MCP servers afterwards."""
    def wrapper(self):
        async def go():
            try:
                await coro_fn(self)
            finally:
                await mcp_client.stop_all()
        asyncio.run(go())
    return wrapper


class Config(unittest.TestCase):
    def test_names_are_safe_and_namespaced(self):
        self.assertEqual(mcp_client.tool_name("Echo Server", "add"), "mcp__echo_server__add")
        self.assertLessEqual(len(mcp_client.tool_name("x" * 90, "y" * 90)), 64)

    def test_secrets_are_masked_for_the_browser_and_kept_when_unchanged(self):
        old = mcp_client.parse_servers({"mcp_servers": json.dumps([{"name": "a", "transport": "http", "url": "http://x", "headers": {"Authorization": "Bearer SECRET"}}])})
        shown = mcp_client.mask(old[0])
        self.assertEqual(shown["headers"]["Authorization"], mcp_client.MASK)
        saved = mcp_client.merge_secrets([json.loads(json.dumps(shown))], old)
        self.assertEqual(saved[0]["headers"]["Authorization"], "Bearer SECRET")

    def test_junk_entries_are_ignored(self):
        got = mcp_client.parse_servers({"mcp_servers": json.dumps([{"name": ""}, "x", {"name": "ok", "transport": "weird"}, {"name": "OK"}])})
        self.assertEqual([c["name"] for c in got], ["ok"])
        self.assertEqual(got[0]["transport"], "stdio")
        self.assertEqual(mcp_client.parse_servers({"mcp_servers": "not json"}), [])


class RealServer(TempDB):
    @in_loop
    async def test_connects_lists_and_calls_tools(self):
        s = settings()
        await mcp_client.sync(s)
        st = mcp_client.state(s)[0]
        self.assertEqual(st["status"], "ready", st)
        self.assertEqual({t["name"] for t in st["tools"]}, {"add", "write_note", "boom"})
        self.assertIn("mcp__echo_server__add", tools.SPECS)
        names = tools.enabled_tools(s, web=False, has_files=False)
        self.assertIn("mcp__echo_server__add", names)
        res = await tools.IMPLS["mcp__echo_server__add"](s, "c", {"a": 2, "b": 3})
        self.assertEqual(res.content, "5")
        with self.assertRaises(tools.ToolError) as cm:
            await tools.IMPLS["mcp__echo_server__boom"](s, "c", {})
        self.assertIn("kaboom", str(cm.exception))

    @in_loop
    async def test_disabling_removes_the_tools(self):
        await mcp_client.sync(settings())
        self.assertIn("mcp__echo_server__add", tools.SPECS)
        await mcp_client.sync(settings(enabled=False))
        self.assertNotIn("mcp__echo_server__add", tools.SPECS)
        self.assertEqual(mcp_client.enabled_tool_names(settings(enabled=False)), [])

    @in_loop
    async def test_a_broken_server_reports_an_error_instead_of_crashing(self):
        s = dict(db.DEFAULT_SETTINGS)
        s["mcp_servers"] = json.dumps([{"name": "bad", "transport": "stdio", "command": "definitely-not-a-real-command-xyz"}])
        await mcp_client.sync(s)
        st = mcp_client.state(s)[0]
        self.assertEqual(st["status"], "error")
        self.assertTrue(st["error"])

    @in_loop
    async def test_read_only_tools_run_freely_others_need_approval(self):
        await mcp_client.sync(settings())
        self.assertFalse(mcp_client.needs_approval("mcp__echo_server__add"))
        self.assertTrue(mcp_client.needs_approval("mcp__echo_server__write_note"))
        await mcp_client.sync(settings(always=["write_note"]))
        self.assertFalse(mcp_client.needs_approval("mcp__echo_server__write_note"))

    @in_loop
    async def test_agent_asks_before_a_non_read_only_tool(self):
        s = settings()
        await mcp_client.sync(s)
        for approve, expect_ok in ((True, True), (False, False)):
            ctx = make_ctx(["mcp__echo_server__write_note"])
            ctx.s = s
            fake = FakeLLM([{"calls": [call("mcp__echo_server__write_note", text="hi")]}, {"text": "Done."}])

            async def answer():
                for _ in range(200):
                    if agent.CONFIRMS:
                        agent.CONFIRMS[next(iter(agent.CONFIRMS))].set_result(approve)
                        return
                    await asyncio.sleep(0.02)

            with mock.patch.object(agent.llm, "stream_chat", fake):
                task = asyncio.create_task(answer())
                events = [e async for e in agent.run_agent(ctx)]
                await task
            confirm = [e for e in events if e["type"] == "confirm"]
            self.assertEqual(len(confirm), 1)
            self.assertEqual(confirm[0]["kind"], "tool")
            self.assertTrue(confirm[0]["always"])
            self.assertIn("hi", confirm[0]["code"])
            result = [e for e in events if e["type"] == "tool_result"][0]
            self.assertEqual(result["ok"], expect_ok)

    @in_loop
    async def test_always_allow_is_saved(self):
        s = settings()
        db.save_settings({"mcp_servers": s["mcp_servers"]})
        await mcp_client.sync(db.get_settings())
        self.assertTrue(mcp_client.allow_always(db.get_settings(), "mcp__echo_server__write_note"))
        self.assertIn("write_note", mcp_client.parse_servers(db.get_settings())[0]["always_allow"])
        self.assertFalse(mcp_client.needs_approval("mcp__echo_server__write_note"))


def mock_n8n(handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = transport
        return real(*a, **kw)
    return mock.patch.object(n8n.httpx, "AsyncClient", factory)


class N8n(unittest.TestCase):
    S = {**db.DEFAULT_SETTINGS, "n8n_url": "http://n8n.test:5678/", "n8n_api_key": "KEY123"}

    def test_templates_connect_every_tool_to_the_mcp_trigger(self):
        for tid in n8n.TEMPLATES:
            wf, path = n8n.build(tid)
            names = {n["name"] for n in wf["nodes"]}
            self.assertIn("MCP Server", names)
            tool_nodes = names - {"MCP Server"}
            self.assertTrue(tool_nodes)
            self.assertEqual(set(wf["connections"]), tool_nodes)
            for link in wf["connections"].values():
                self.assertEqual(link["ai_tool"][0][0], {"node": "MCP Server", "type": "ai_tool", "index": 0})
            self.assertTrue(path.startswith("local-chat-"))
            self.assertNotIn("active", wf)  # created as a draft

    def test_sending_mail_is_opt_in(self):
        plain, _ = n8n.build("mail_tools")
        sending, _ = n8n.build("mail_tools", fields={"allow_send": True})
        self.assertNotIn("Send email", {n["name"] for n in plain["nodes"]})
        self.assertIn("Send email", {n["name"] for n in sending["nodes"]})

    def test_token_option_and_unknown_template(self):
        wf, _ = n8n.build("calendar_tools", token=True)
        self.assertEqual(wf["nodes"][0]["parameters"]["authentication"], "bearerAuth")
        with self.assertRaises(n8n.N8nError):
            n8n.build("nope")

    def test_create_workflow_sends_the_key_and_returns_the_id(self):
        seen = {}

        def handler(request):
            seen.update(method=request.method, url=str(request.url), key=request.headers.get("x-n8n-api-key"), body=json.loads(request.content))
            return httpx.Response(200, json={"id": "42"})

        wf, _ = n8n.build("calendar_tools")
        with mock_n8n(handler):
            made = asyncio.run(n8n.create_workflow(self.S, wf))
        self.assertEqual(made["id"], "42")
        self.assertEqual((seen["method"], seen["url"], seen["key"]), ("POST", "http://n8n.test:5678/api/v1/workflows", "KEY123"))
        self.assertEqual(seen["body"]["name"], wf["name"])

    def test_errors_are_explained(self):
        for status, word in ((401, "API key"), (404, "public API")):
            with mock_n8n(lambda r, st=status: httpx.Response(st)):
                with self.assertRaises(n8n.N8nError) as cm:
                    asyncio.run(n8n.ping(self.S))
            self.assertIn(word, str(cm.exception))
        with self.assertRaises(n8n.N8nError) as cm:
            asyncio.run(n8n.ping(db.DEFAULT_SETTINGS))
        self.assertIn("not set up", str(cm.exception))

    def test_tool_only_offered_when_n8n_is_configured_and_asked_for(self):
        self.assertIn("build_n8n_workflow", tools.enabled_tools(self.S, web=False, has_files=False, n8n_on=True))
        self.assertNotIn("build_n8n_workflow", tools.enabled_tools(self.S, web=False, has_files=False, n8n_on=False))
        self.assertNotIn("build_n8n_workflow", tools.enabled_tools(db.DEFAULT_SETTINGS, web=False, has_files=False, n8n_on=True))

    def test_creating_a_workflow_needs_approval(self):
        ask = agent.approval_needed("build_n8n_workflow", {"template": "mail_tools"}, self.S)
        self.assertEqual(ask[0], "tool")


@unittest.skipUnless(tools.find_soffice(), "LibreOffice is not installed")
class LibreOffice(TempDB):
    def test_real_conversion_docx_to_pdf(self):
        from docx import Document

        chat = db.create_chat("m")
        work = db.work_dir(chat["id"])
        doc = Document()
        doc.add_paragraph("Hello from Local Chat")
        doc.save(work / "hello.docx")
        res = asyncio.run(tools.convert_document(dict(db.DEFAULT_SETTINGS), chat["id"], {"file": "hello.docx", "to": "pdf"}))
        self.assertTrue(res.ok)
        self.assertEqual(res.files[0]["name"], "hello.pdf")
        self.assertEqual((work / "hello.pdf").read_bytes()[:4], b"%PDF")

    def test_bad_requests_are_explained(self):
        chat = db.create_chat("m")
        s = dict(db.DEFAULT_SETTINGS)
        with self.assertRaises(tools.ToolError):
            asyncio.run(tools.convert_document(s, chat["id"], {"file": "missing.docx", "to": "pdf"}))
        with self.assertRaises(tools.ToolError):
            asyncio.run(tools.convert_document(s, chat["id"], {"file": "x", "to": "exe"}))

    def test_offered_only_when_relevant(self):
        s = dict(db.DEFAULT_SETTINGS)
        self.assertNotIn("convert_document", tools.enabled_tools(s, web=False, has_files=False))
        self.assertIn("convert_document", tools.enabled_tools(s, web=False, has_files=False, convert=True))


if __name__ == "__main__":
    unittest.main()


class NamedServerNudge(unittest.TestCase):
    def test_model_that_skips_a_named_servers_tool_is_nudged(self):
        ctx = make_ctx(["mcp__echo__add"])
        ctx.wants_mcp = True

        async def impl(s, chat_id, args):
            return tools.ToolResult(True, "42", "Echo · add", {"server": "Echo", "tool": "add", "text": "42"})

        fake = FakeLLM([{"text": "The answer is 32."}, {"calls": [call("mcp__echo__add", a=17, b=25)]}, {"text": "42."}])
        spec = {"type": "function", "function": {"name": "mcp__echo__add", "description": "add", "parameters": {"type": "object", "properties": {}}}}
        with mock.patch.object(agent.llm, "stream_chat", fake), mock.patch.dict(tools.IMPLS, {"mcp__echo__add": impl}), \
                mock.patch.dict(tools.SPECS, {"mcp__echo__add": spec}), \
                mock.patch.object(mcp_client, "needs_approval", lambda n: False):
            async def go():
                return [e async for e in agent.run_agent(ctx)]
            events = asyncio.run(go())
        self.assertTrue(any(e["type"] == "tool_result" and e["ok"] for e in events))
        self.assertTrue(any(e["type"] == "notice" and "instead of doing it" in e["text"] for e in events))
        self.assertNotIn("32", ctx.state["acc"])
