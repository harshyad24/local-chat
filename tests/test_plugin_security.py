"""Plugin security: encrypted secrets, first-use consent, the Pause switch, the activity log and the plugin checks."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import agent, db, mcp_client, plugins, secretbox  # noqa: E402

from test_agent import TempDB  # noqa: E402
from test_plugins import ICS, FakeImap  # noqa: E402


def settings(servers=()):
    s = dict(db.DEFAULT_SETTINGS)
    s["mcp_servers"] = json.dumps(list(servers))
    return s


class Catalog(unittest.TestCase):
    def test_mail_secret_is_encrypted_never_returned_and_kept_when_left_blank(self):
        servers = plugins.install(settings(), "mail", {"MAIL_USER": "Me@Example.com", "MAIL_PASSWORD": "app-pass"})
        self.assertEqual(servers[0]["env"]["MAIL_HOST"], "imap.gmail.com")
        self.assertEqual(servers[0]["env"]["MAIL_USER"], "me@example.com")
        self.assertEqual(secretbox.reveal(servers[0]["env"]["MAIL_PASSWORD"]), "app-pass")
        if secretbox.available():
            self.assertNotIn("app-pass", json.dumps(servers))  # what is saved is not readable text
        st = plugins.state(settings(servers))
        mail = next(p for p in st["plugins"] if p["id"] == "mail")
        self.assertTrue(mail["installed"])
        self.assertEqual(mail["values"]["MAIL_PASSWORD"], "")
        self.assertNotIn("app-pass", json.dumps(st))
        again = plugins.install(settings(servers), "mail", {"MAIL_USER": "me@example.com", "MAIL_PASSWORD": ""})
        self.assertEqual(secretbox.reveal(again[0]["env"]["MAIL_PASSWORD"]), "app-pass")

    def test_every_plugin_explains_itself_with_steps_and_limits(self):
        for p in plugins.state(settings())["plugins"]:
            self.assertGreaterEqual(len(p["steps"]), 4, p["id"])
            self.assertTrue(p["can"] and p["cannot"] and p["summary"], p["id"])
            self.assertTrue(all(f["label"] for f in p["fields"]))

    def test_pasted_folder_paths_lose_their_quotation_marks(self):
        with tempfile.TemporaryDirectory() as d:
            servers = plugins.install(settings(), "folder", {"FILES_ROOT": f'"{d}"'})
        self.assertEqual(servers[0]["env"]["FILES_ROOT"], d)

    def test_the_login_check_reports_a_refusal_and_accepts_a_good_login(self):
        env = {"MAIL_HOST": "imap.gmail.com", "MAIL_USER": "me@gmail.com", "MAIL_PASSWORD": secretbox.protect("x")}

        class Refusing(FakeImap):
            def login(self, u, p):
                raise plugins.imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

        with mock.patch.object(plugins.imaplib, "IMAP4_SSL", Refusing):
            ok, msg = plugins.check("mail", env)
        self.assertFalse(ok)
        self.assertIn("App password", msg)
        with mock.patch.object(plugins.imaplib, "IMAP4_SSL", FakeImap):
            ok, msg = plugins.check("mail", env)
        self.assertTrue(ok)
        self.assertIn("me@gmail.com", msg)

    def test_folder_check_counts_files(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("x")
            (Path(d) / "b.txt").write_text("y")
            ok, msg = plugins.check("folder", {"FILES_ROOT": d})
        self.assertTrue(ok)
        self.assertIn("2 files", msg)

    def test_calendar_check_counts_events_and_rejects_a_non_calendar(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d) / "g.ics", Path(d) / "b.ics"
            good.write_text(ICS, encoding="utf-8")
            bad.write_text("<html>not a calendar</html>", encoding="utf-8")
            ok, msg = plugins.check("calendar", {"CAL_ICS": str(good)})
            self.assertTrue(ok)
            self.assertIn("4 events", msg)
            ok, msg = plugins.check("calendar", {"CAL_ICS": str(bad)})
            self.assertFalse(ok)
            self.assertIn("Secret address", msg)


@unittest.skipUnless(secretbox.available(), "Windows DPAPI only")
class SecretBox(unittest.TestCase):
    def test_roundtrip_and_old_plain_values_still_work(self):
        t = "abcd efgh ijkl mnop"
        p = secretbox.protect(t)
        self.assertTrue(p.startswith("dpapi:"))
        self.assertNotIn("abcd", p)
        self.assertEqual(secretbox.reveal(p), t)
        self.assertEqual(secretbox.protect(p), p)  # never encrypted twice
        self.assertEqual(secretbox.reveal("saved-before-encryption-existed"), "saved-before-encryption-existed")

    def test_a_value_that_cannot_be_decrypted_gives_nothing_not_garbage(self):
        self.assertEqual(secretbox.reveal("dpapi:AAAA"), "")


class Consent(unittest.TestCase):
    """The assistant asks once per chat before it first uses a plugin."""

    def setUp(self):
        mcp_client.PLUGIN_ALLOWED.clear()
        mail = mcp_client.Conn({"name": "Mail", "transport": "stdio", "command": "x", "args": [], "env": {}, "enabled": True, "always_allow": []})
        mail.status, mail.tools = "ready", [{"name": "list_recent", "ns": "mcp__mail__list_recent", "description": "", "schema": {}, "read_only": True}]
        echo = mcp_client.Conn({"name": "Echo", "transport": "stdio", "command": "x", "args": [], "env": {}, "enabled": True, "always_allow": []})
        echo.status, echo.tools = "ready", [{"name": "add", "ns": "mcp__echo__add", "description": "", "schema": {}, "read_only": True}]
        self.patch = mock.patch.dict(mcp_client.CONNS, {"Mail": mail, "Echo": echo}, clear=True)
        self.patch.start()
        self.s = settings()

    def tearDown(self):
        self.patch.stop()
        mcp_client.PLUGIN_ALLOWED.clear()

    def test_first_use_asks_even_for_a_read_only_tool_then_not_again_in_that_chat(self):
        ask = agent.approval_needed("mcp__mail__list_recent", {"limit": 5}, self.s, "chat1")
        self.assertEqual(ask[0], "tool")
        self.assertIn("Mail", ask[1])
        mcp_client.grant("chat1", "mcp__mail__list_recent")
        self.assertIsNone(agent.approval_needed("mcp__mail__list_recent", {}, self.s, "chat1"))
        self.assertIsNotNone(agent.approval_needed("mcp__mail__list_recent", {}, self.s, "chat2"))  # another chat asks again

    def test_it_can_be_switched_off_and_other_servers_are_not_affected(self):
        self.s["plugin_consent"] = False
        self.assertIsNone(agent.approval_needed("mcp__mail__list_recent", {}, self.s, "c"))
        self.s["plugin_consent"] = True
        self.assertIsNone(agent.approval_needed("mcp__echo__add", {"a": 1}, self.s, "c"))  # a normal MCP server keeps its own rules

    def test_pause_hides_every_plugin_from_the_model_but_not_other_servers(self):
        self.s["mcp_servers"] = json.dumps([{"name": "Mail", "transport": "stdio", "command": "x", "enabled": True},
                                            {"name": "Echo", "transport": "stdio", "command": "x", "enabled": True}])
        self.assertEqual(sorted(mcp_client.enabled_tool_names(self.s)), ["mcp__echo__add", "mcp__mail__list_recent"])
        self.s["plugins_paused"] = True
        self.assertEqual(mcp_client.enabled_tool_names(self.s), ["mcp__echo__add"])


class ActivityLog(TempDB):
    def test_only_the_request_is_logged_never_the_content(self):
        db.log_plugin_call("Mail", "read_mail", {"id": "123", "max_chars": 4000, "note": "x" * 500}, True, 8000)
        row = db.plugin_activity("Mail")[0]
        self.assertEqual((row["tool"], row["ok"], row["chars"]), ("read_mail", 1, 8000))
        self.assertIn("id=123", row["args"])
        self.assertLessEqual(len(row["args"]), 160)
        self.assertEqual(set(row), {"ts", "server", "tool", "args", "ok", "chars"})  # there is no column that could hold message text

    def test_the_log_keeps_only_the_latest_500(self):
        for i in range(520):
            db.log_plugin_call("Folder", "list_files", {"i": i}, True, 1)
        self.assertEqual(len(db.plugin_activity(None, 1000)), 500)

    def test_a_real_plugin_call_is_logged_through_the_apps_own_client(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "todo.txt").write_text("buy milk", encoding="utf-8")
            s = settings(plugins.install(settings(), "folder", {"FILES_ROOT": d}))

            async def go():
                await mcp_client.sync(s)
                try:
                    conn = mcp_client.CONNS["Folder"]
                    for _ in range(100):
                        if conn.status != "connecting":
                            break
                        await asyncio.sleep(0.2)
                    from backend import tools as tools_mod
                    await tools_mod.IMPLS["mcp__folder__read_text_file"](s, "chat1", {"path": "todo.txt"})
                finally:
                    await mcp_client.stop_all()

            asyncio.run(go())
        row = db.plugin_activity("Folder")[0]
        self.assertEqual(row["tool"], "read_text_file")
        self.assertNotIn("buy milk", json.dumps(row))


@unittest.skipUnless(secretbox.available(), "Windows DPAPI only")
class EncryptedSecretsReachTheServer(unittest.TestCase):
    def test_a_protected_value_is_decrypted_only_when_the_plugin_starts(self):
        with tempfile.TemporaryDirectory() as d:
            ics = Path(d) / "c.ics"
            ics.write_text(ICS, encoding="utf-8")
            servers = plugins.install(settings(), "calendar", {"CAL_ICS": str(ics)})
            self.assertTrue(servers[0]["env"]["CAL_ICS"].startswith("dpapi:"))  # what is saved
            s = settings(servers)

            async def go():
                await mcp_client.sync(s)
                try:
                    conn = mcp_client.CONNS["Calendar"]
                    for _ in range(100):
                        if conn.status != "connecting":
                            break
                        await asyncio.sleep(0.2)
                    self.assertEqual(conn.status, "ready", conn.error)
                    return str(await conn.call("events_on", {"day": "2026-11-15"}))
                finally:
                    await mcp_client.stop_all()

            self.assertIn("Room 4", asyncio.run(go()))


if __name__ == "__main__":
    unittest.main()
