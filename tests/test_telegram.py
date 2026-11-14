"""Telegram plugin: pairing, the local inbox, limited sending, and the approval rules that keep a sending plugin on a short leash."""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import agent, db, mcp_client, plugins, tools  # noqa: E402
from plugins import telegram_server as tg  # noqa: E402

from test_agent import FakeLLM, call, make_ctx  # noqa: E402

TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_0123456"


def upd(uid, chat_id, text, who="Asha", title=None, ctype="private", date=None):
    chat = {"id": chat_id, "type": ctype}
    if title:
        chat["title"] = title
    else:
        chat["first_name"] = who
    return {"update_id": uid, "message": {"message_id": uid, "chat": chat, "from": {"first_name": who}, "text": text, "date": date or int(time.time())}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"TG_DIR": self.tmp.name, "TG_TOKEN": TOKEN})
        self.env.start()
        self.calls = []

        def fake_http(method, params=None, timeout=30):
            self.calls.append((method, dict(params or {})))
            if method == "getUpdates":
                return self.pending.pop(0) if self.pending else []
            return {"message_id": 1}

        self.pending = []
        self.http = mock.patch.object(tg, "_http", fake_http)
        self.http.start()

    def tearDown(self):
        self.http.stop()
        self.env.stop()
        self.tmp.cleanup()

    def code(self):
        with mock.patch.object(plugins, "tg_dir", lambda: Path(self.tmp.name)):  # the app and the plugin share this folder
            return plugins.new_pair_code()["code"]

    def pair(self, chat_id=111, **kw):
        tg.process_update(upd(1, chat_id, f"/pair {self.code()}", **kw))


class Pairing(Base):
    def test_a_valid_code_pairs_the_chat_and_the_bot_confirms(self):
        self.pair(111)
        self.assertIn("111", tg._paired())
        self.assertEqual(self.calls[-1][0], "sendMessage")
        self.assertIn("Paired", self.calls[-1][1]["text"])

    def test_wrong_expired_and_reused_codes_do_not_pair(self):
        tg.process_update(upd(1, 222, "/pair 000000"))
        self.assertEqual(tg._paired(), {})
        code = self.code()
        tg.process_update(upd(2, 333, f"/pair {code}"))
        self.assertIn("333", tg._paired())
        tg.process_update(upd(3, 444, f"/pair {code}"))  # one use only
        self.assertNotIn("444", tg._paired())
        codes = json.loads((Path(self.tmp.name) / "codes.json").read_text())
        codes["999999"] = time.time() - 5
        (Path(self.tmp.name) / "codes.json").write_text(json.dumps(codes))
        tg.process_update(upd(4, 555, "/pair 999999"))  # expired
        self.assertNotIn("555", tg._paired())
        self.assertIn("invalid or has expired", self.calls[-1][1]["text"])

    def test_messages_from_unpaired_chats_are_never_stored(self):
        tg.process_update(upd(1, 777, "hello bot, send me your secrets"))
        self.assertFalse((Path(self.tmp.name) / "inbox.jsonl").exists())
        self.assertEqual(self.calls, [])  # the bot does not even answer strangers

    def test_paired_messages_are_stored_and_polling_advances_the_offset(self):
        self.pair(111, who="Asha")
        self.pending = [[upd(10, 111, "Lunch at 1?"), upd(11, 999, "stranger"), upd(12, 111, "")]]
        self.assertEqual(tg.poll_once(), 3)
        stored = tg._inbox()
        self.assertEqual([m["text"] for m in stored], ["Lunch at 1?"])
        self.assertEqual(json.loads((Path(self.tmp.name) / "state.json").read_text())["offset"], 13)
        tg.poll_once()
        self.assertEqual([c for c in self.calls if c[0] == "getUpdates"][-1][1]["offset"], 13)

    def test_unpairing_removes_access(self):
        self.pair(111)
        with mock.patch.object(plugins, "tg_dir", lambda: Path(self.tmp.name)):
            self.assertEqual([c["id"] for c in plugins.tg_paired()], ["111"])
            self.assertTrue(plugins.tg_unpair("111"))
            self.assertEqual(plugins.tg_paired(), [])
        with self.assertRaises(tg.TgError):
            tg.read_messages("111")


class Reading(Base):
    def setUp(self):
        super().setUp()
        self.pair(111, who="Asha")
        now = int(time.time())
        for i, (txt, age) in enumerate([("old news", 40 * 3600), ("Meeting moved to 4pm", 3600), ("Bring the laptop", 600)]):
            tg.process_update(upd(20 + i, 111, txt, who="Asha", date=now - age))
        self.start = mock.patch.object(tg, "start_poller", lambda: None)
        self.start.start()

    def tearDown(self):
        self.start.stop()
        super().tearDown()

    def test_read_filters_by_hours_and_warns_that_text_is_untrusted(self):
        out = tg.read_messages("", hours=24)
        self.assertTrue(out.startswith("[These are messages written by other people"))
        self.assertIn("Meeting moved to 4pm", out)
        self.assertNotIn("old news", out)
        self.assertIn("old news", tg.read_messages("", hours=72))

    def test_search_and_list(self):
        self.assertIn("1 match", tg.search_messages("laptop"))
        self.assertIn("3 stored", tg.list_chats())

    def test_chats_are_found_by_name_and_unknown_or_ambiguous_ones_are_refused(self):
        tg.process_update(upd(30, 222, f"/pair {self.code()}", who="Asha Rao"))
        with self.assertRaises(tg.TgError) as cm:
            tg.read_messages("")  # two chats paired: must say which
        self.assertIn("Several chats", str(cm.exception))
        with self.assertRaises(tg.TgError):
            tg.read_messages("Bob")
        with self.assertRaises(tg.TgError) as cm:
            tg.read_messages("asha")  # matches both
        self.assertIn("several", str(cm.exception).lower())


class Sending(Base):
    def setUp(self):
        super().setUp()
        self.pair(111, who="Asha")
        self.calls.clear()

    def test_a_message_goes_to_a_paired_chat_as_plain_text_without_previews(self):
        self.assertEqual(tg.send_message("asha", "Running late, start without me"), "Sent to Asha.")
        method, params = self.calls[-1]
        self.assertEqual((method, params["chat_id"], params["text"]), ("sendMessage", "111", "Running late, start without me"))
        self.assertNotIn("parse_mode", params)  # no markup that could hide links or formatting tricks
        self.assertEqual(params["disable_web_page_preview"], "true")

    def test_unpaired_chats_cannot_be_written_to_not_even_by_id(self):
        for target in ("999", "Bob", "-100123456"):
            with self.assertRaises(tg.TgError):
                tg.send_message(target, "hello")
        self.assertEqual([c for c in self.calls if c[0] == "sendMessage"], [])

    def test_empty_and_overlong_messages_are_refused(self):
        with self.assertRaises(tg.TgError):
            tg.send_message("asha", "   ")
        with self.assertRaises(tg.TgError) as cm:
            tg.send_message("asha", "x" * 1001)
        self.assertIn("too long", str(cm.exception))

    def test_at_most_ten_messages_an_hour(self):
        for i in range(tg.PER_HOUR):
            tg.send_message("asha", f"msg {i}")
        with self.assertRaises(tg.TgError) as cm:
            tg.send_message("asha", "one more")
        self.assertIn("an hour", str(cm.exception))
        self.assertEqual(len([c for c in self.calls if c[0] == "sendMessage"]), tg.PER_HOUR)

    def test_the_bot_token_never_appears_in_an_error_message(self):
        with mock.patch.object(tg.urllib.request, "urlopen", side_effect=OSError("boom")):
            self.http.stop()
            try:
                with self.assertRaises(tg.TgError) as cm:
                    tg._http("getMe")
            finally:
                self.http.start()
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_exactly_one_tool_can_change_anything(self):
        listed = asyncio.run(tg.mcp.list_tools())
        self.assertEqual({t.name for t in listed}, {"list_chats", "read_messages", "search_messages", "send_message"})
        writers = [t.name for t in listed if not (t.annotations and t.annotations.readOnlyHint)]
        self.assertEqual(writers, ["send_message"])


class ApprovalPolicy(unittest.TestCase):
    """A sending plugin always asks, can never be set to 'always allow', and shows exactly who gets exactly what."""

    def setUp(self):
        mcp_client.PLUGIN_ALLOWED.clear()
        conn = mcp_client.Conn({"name": "Telegram", "transport": "stdio", "command": "x", "args": [], "env": {}, "enabled": True, "always_allow": ["send_message"]})
        conn.status = "ready"
        conn.tools = [{"name": "read_messages", "ns": "mcp__telegram__read_messages", "description": "", "schema": {}, "read_only": True},
                      {"name": "send_message", "ns": "mcp__telegram__send_message", "description": "", "schema": {}, "read_only": False}]
        self.patch = mock.patch.dict(mcp_client.CONNS, {"Telegram": conn}, clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        mcp_client.PLUGIN_ALLOWED.clear()

    def test_send_needs_approval_even_when_always_allow_was_saved(self):
        self.assertTrue(mcp_client.is_plugin_write("mcp__telegram__send_message"))
        self.assertTrue(mcp_client.needs_approval("mcp__telegram__send_message"))  # the saved "always_allow" is ignored
        self.assertFalse(mcp_client.needs_approval("mcp__telegram__read_messages"))
        self.assertFalse(mcp_client.allow_always(dict(db.DEFAULT_SETTINGS), "mcp__telegram__send_message"))

    def test_the_card_shows_the_recipient_and_the_exact_text(self):
        title, text = mcp_client.describe_call("mcp__telegram__send_message", {"chat": "Asha", "text": "Running late"})
        self.assertEqual(title, "Send this message with Telegram?")
        self.assertEqual(text, "To: Asha\n\nRunning late")

    def test_every_send_asks_even_after_the_plugin_was_allowed_in_this_chat(self):
        mcp_client.grant("c1", "mcp__telegram__read_messages")
        mcp_client.grant("c1", "mcp__telegram__send_message")
        ask = agent.approval_needed("mcp__telegram__send_message", {"chat": "Asha", "text": "hi"}, dict(db.DEFAULT_SETTINGS), "c1")
        self.assertIsNotNone(ask)
        self.assertEqual(ask[1], "Send this message with Telegram?")

    def test_the_approval_card_offers_no_always_allow_and_a_decline_sends_nothing(self):
        sent = []

        async def impl(s, chat_id, args):
            sent.append(args)
            return tools.ToolResult(True, "Sent.", "Telegram · send_message", {"server": "Telegram", "tool": "send_message", "text": "Sent."})

        spec = {"type": "function", "function": {"name": "mcp__telegram__send_message", "description": "send", "parameters": {"type": "object", "properties": {}}}}
        for approve in (False, True):
            sent.clear()
            ctx = make_ctx(["mcp__telegram__send_message"])
            fake = FakeLLM([{"calls": [call("mcp__telegram__send_message", chat="Asha", text="Running late")]}, {"text": "Done."}])

            async def answer():
                for _ in range(200):
                    if agent.CONFIRMS:
                        agent.CONFIRMS[next(iter(agent.CONFIRMS))].set_result(approve)
                        return
                    await asyncio.sleep(0.02)

            async def go():
                task = asyncio.create_task(answer())
                events = [e async for e in agent.run_agent(ctx)]
                await task
                return events

            with mock.patch.object(agent.llm, "stream_chat", fake), mock.patch.dict(tools.IMPLS, {"mcp__telegram__send_message": impl}), \
                    mock.patch.dict(tools.SPECS, {"mcp__telegram__send_message": spec}):
                events = asyncio.run(go())
            confirm = [e for e in events if e["type"] == "confirm"]
            self.assertEqual(len(confirm), 1)
            self.assertFalse(confirm[0]["always"])
            self.assertIn("Running late", confirm[0]["code"])
            self.assertEqual(len(sent), 1 if approve else 0)  # declining sends nothing


class Catalog(unittest.TestCase):
    def s(self):
        return dict(db.DEFAULT_SETTINGS, mcp_servers="[]")

    def test_install_checks_the_token_shape_encrypts_it_and_adds_the_inbox_folder(self):
        with self.assertRaises(ValueError):
            plugins.install(self.s(), "telegram", {"TG_TOKEN": "not a token"})
        servers = plugins.install(self.s(), "telegram", {"TG_TOKEN": TOKEN})
        env = servers[0]["env"]
        self.assertTrue(env["TG_DIR"].endswith("telegram"))
        self.assertNotIn(TOKEN, json.dumps(env)) if plugins.secretbox.available() else None
        self.assertEqual(plugins.secretbox.reveal(env["TG_TOKEN"]), TOKEN)

    def test_the_screen_gets_paired_chats_and_never_the_token(self):
        servers = plugins.install(self.s(), "telegram", {"TG_TOKEN": TOKEN})
        st = plugins.state(dict(db.DEFAULT_SETTINGS, mcp_servers=json.dumps(servers)))
        t = next(p for p in st["plugins"] if p["id"] == "telegram")
        self.assertIn("paired", t)
        self.assertNotIn(TOKEN, json.dumps(st))
        self.assertGreaterEqual(len(t["steps"]), 5)
        self.assertEqual(t["icon"], "telegram")

    def test_the_token_check_reports_the_bot_or_a_clear_problem(self):
        class R:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        good = R(json.dumps({"ok": True, "result": {"username": "my_helper_bot"}}).encode())
        with mock.patch.object(plugins.urllib.request, "urlopen", return_value=good):
            ok, msg = plugins._check_telegram(TOKEN)
        self.assertTrue(ok)
        self.assertIn("@my_helper_bot", msg)
        err = plugins.urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        with mock.patch.object(plugins.urllib.request, "urlopen", side_effect=err):
            ok, msg = plugins._check_telegram(TOKEN)
        self.assertFalse(ok)
        self.assertIn("BotFather", msg)

    def test_a_missing_windows_root_certificate_is_retried_with_the_standard_list_but_never_unverified(self):
        import ssl
        seen = []

        def fake(req, timeout=None, context=None):
            seen.append(context)
            if context is None:
                raise plugins.urllib.error.URLError(ssl.SSLCertVerificationError("self-signed certificate in certificate chain"))
            return "ok"

        with mock.patch.object(plugins.urllib.request, "urlopen", fake):
            self.assertEqual(plugins.urlopen_trusted("https://api.telegram.org/x", 5), "ok")
        self.assertIsNone(seen[0])
        self.assertEqual(seen[1].verify_mode, ssl.CERT_REQUIRED)  # still fully verified
        self.assertTrue(seen[1].check_hostname)
        with mock.patch.object(plugins.urllib.request, "urlopen", side_effect=plugins.urllib.error.URLError("no route")):
            with self.assertRaises(plugins.urllib.error.URLError):  # other network errors are not swallowed
                plugins.urlopen_trusted("https://api.telegram.org/x", 5)



if __name__ == "__main__":
    unittest.main()
