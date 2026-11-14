"""Plugins: one-click presets on top of MCP. A plugin is an MCP server shipped in plugins/ plus what the screen needs to set it up: plain
steps, the fields to fill, and a statement of what it can and cannot do. Installing one writes an ordinary MCP server entry, so it also
appears in Connections, has a per-chat switch, and its tools follow the same approval rules.

Security, all in code that a test covers:
 * every bundled plugin is read-only by construction (the tool list is asserted);
 * passwords and addresses are encrypted at rest with the operating system (secretbox) and never sent back to the browser;
 * a plugin is tested with the saved details before they are kept, so a wrong password is reported on the form;
 * the assistant asks once per chat before it first uses a plugin, and a Pause switch hides every plugin from the model;
 * every plugin call is written to an activity log (tool and a short argument summary, never the content that was read)."""
from __future__ import annotations

import imaplib
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import db, mcp_client, secretbox


def urlopen_trusted(req, timeout=20):
    """urllib.request.urlopen with one safe fallback: on Windows the system store may lack a root certificate that Windows itself would
    fetch on demand, so Python reports "self-signed certificate in chain" for a perfectly normal site. On exactly that error the call is
    retried with the standard Mozilla list of trusted roots (certifi). Certificates are still fully verified, never switched off."""
    import ssl
    import urllib.error
    import urllib.request
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - callers pass fixed https addresses
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
            import certifi
            return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where()))  # noqa: S310
        raise

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins"

CATALOG: dict[str, dict[str, Any]] = {
    "mail": {
        "name": "Mail", "script": "mail_server.py", "icon": "mail",
        "summary": "Search and read your email.",
        "description": "Works with Gmail, Outlook, Yahoo and any other IMAP mailbox. The assistant can look, never send.",
        "can": ["List your newest messages", "Search messages by word", "Read one message (it stays unread)"],
        "cannot": ["Send, reply or forward", "Delete, move or flag anything"],
        "steps": [
            {"text": "Open your Google Account security page and make sure **2-Step Verification** is on.", "link": {"label": "Open security page", "url": "https://myaccount.google.com/security"}},
            {"text": "Open **App passwords**, type the name **Local Chat** and press Create.", "link": {"label": "Open App passwords", "url": "https://myaccount.google.com/apppasswords"}},
            {"text": "Google shows a 16-letter code. Copy it. This is **not** your normal password, and you can delete it there at any time."},
            {"text": "Paste it below with your full email address. Spaces in the code are fine."},
            {"text": "Press **Test & connect**. The app logs in once to check the details before saving them."},
        ],
        "note": "Using Outlook or Yahoo? Pick the server from the list in the first box. They also need an app-specific password.",
        "fields": [
            {"key": "MAIL_HOST", "label": "Mail server", "default": "imap.gmail.com", "options": ["imap.gmail.com", "imap-mail.outlook.com", "imap.mail.yahoo.com"]},
            {"key": "MAIL_USER", "label": "Email address", "placeholder": "you@gmail.com"},
            {"key": "MAIL_PASSWORD", "label": "App password", "secret": True, "placeholder": "16-letter code"},
        ],
    },
    "calendar": {
        "name": "Calendar", "script": "calendar_server.py", "icon": "calendar",
        "summary": "See what is on your day or week.",
        "description": "Reads an iCal (.ics) calendar address, so there is no sign-in. The assistant can look, never change.",
        "can": ["List upcoming events", "List the events on one date", "Understand repeating events"],
        "cannot": ["Create, edit or delete events"],
        "steps": [
            {"text": "Open Google Calendar settings.", "link": {"label": "Open Calendar settings", "url": "https://calendar.google.com/calendar/u/0/r/settings"}},
            {"text": "In the left list click **your calendar's name**, then scroll to **Integrate calendar**."},
            {"text": "Copy **Secret address in iCal format**. Anyone who has this address can read your calendar, so keep it private."},
            {"text": "Paste it below. A path to an .ics file on your computer also works."},
            {"text": "Press **Test & connect**. The app downloads the calendar once and counts the events."},
        ],
        "note": "Outlook and Apple Calendar can publish an .ics address too: use that address here.",
        "fields": [{"key": "CAL_ICS", "label": "iCal address", "secret": True, "placeholder": "https://calendar.google.com/calendar/ical/…/basic.ics"}],
    },
    "telegram": {
        "name": "Telegram", "script": "telegram_server.py", "icon": "telegram",
        "summary": "Two-way: read your chats and send messages (with your OK).",
        "description": "Uses Telegram's official Bot API. The only plugin that can send, and only inside tight limits.",
        "can": ["Read messages from chats you paired", "Search them", "Send a plain-text message to a paired chat, after you approve it"],
        "cannot": ["Message anyone you have not paired", "Send without your approval (it can never be set to “always allow”)", "Send files, edit or delete messages", "Send more than 10 messages an hour"],
        "steps": [
            {"text": "Open Telegram and search for **@BotFather** (the official bot with a blue tick). Send it **/newbot** and follow the questions."},
            {"text": "BotFather replies with a token like 123456:ABC-DEF. **Copy it** and keep it private: it controls the bot.", "link": {"label": "About BotFather", "url": "https://core.telegram.org/bots#how-do-i-create-a-bot"}},
            {"text": "Paste the token below and press **Test & connect**. The app checks it with Telegram."},
            {"text": "Press **Generate pairing code**. In Telegram open your new bot and send **/pair** followed by the 6-digit code. The bot answers “Paired”. Codes work once and expire in 10 minutes."},
            {"text": "Optional, for a group: add the bot to the group, send /pair CODE there, and in BotFather use **/setprivacy → Disable** so the bot can read group messages."},
            {"text": "Try: “Show my latest Telegram messages”, or “Send ‘running late’ to my Telegram”. You will be asked to approve the message before it is sent."},
        ],
        "note": "Messages from paired chats are stored in a local file on this computer so they are not lost (Telegram keeps them for only 24 hours). Disconnect and Unpair remove access; delete the bot in BotFather to cut it off for good.",
        "fields": [{"key": "TG_TOKEN", "label": "Bot token", "secret": True, "placeholder": "123456:ABC-DEF…"}],
    },
    "folder": {
        "name": "Folder", "script": "files_server.py", "icon": "folder",
        "summary": "Let the assistant read notes in one folder.",
        "description": "Text files, CSVs and code in a single folder you choose. Nothing outside it can be reached.",
        "can": ["List files", "Read text files", "Search names and contents"],
        "cannot": ["Write, move or delete anything", "See outside the chosen folder"],
        "steps": [
            {"text": "Choose a folder that holds the notes or documents you want to ask about."},
            {"text": "In File Explorer hold **Shift**, right-click the folder and choose **Copy as path**."},
            {"text": "Paste it below. Quotation marks are removed for you."},
            {"text": "Press **Test & connect**. The app checks that the folder exists and counts its files."},
        ],
        "note": "Only text files are read (txt, md, csv, json, code). Word and PDF files are not.",
        "fields": [{"key": "FILES_ROOT", "label": "Folder path", "placeholder": r"C:\Users\you\Documents\Notes"}],
    },
}
mcp_client.PLUGIN_SERVERS.update(p["name"] for p in CATALOG.values())


def _server_name(pid: str) -> str:
    return CATALOG[pid]["name"]


def _secret_keys(pid: str) -> set[str]:
    return {f["key"] for f in CATALOG[pid]["fields"] if f.get("secret")}


def public(pid: str) -> dict:
    p = CATALOG[pid]
    return {k: p[k] for k in ("name", "icon", "summary", "description", "can", "cannot", "steps", "note", "fields")} | {"id": pid}


def state(s: dict) -> dict:
    """Everything the Plugins screen shows. Secret values are never included."""
    have = {c["name"]: c for c in mcp_client.parse_servers(s)}
    out = []
    for pid in CATALOG:
        cfg = have.get(_server_name(pid))
        values = {}
        if cfg:
            for f in CATALOG[pid]["fields"]:
                values[f["key"]] = "" if f.get("secret") else cfg["env"].get(f["key"], "")
        extra = {"paired": tg_paired(), "bot": (cfg["env"].get("TG_BOT", "") if cfg else "")} if pid == "telegram" else {}
        out.append({**public(pid), "installed": bool(cfg), "enabled": bool(cfg and cfg["enabled"]), "values": values,
                    "activity": db.plugin_activity(_server_name(pid), 8), **extra})
    return {"plugins": out, "paused": bool(s.get("plugins_paused")), "consent": bool(s.get("plugin_consent", True)),
            "protected": secretbox.available()}


def _clean(pid: str, key: str, v: str) -> str:
    v = v.strip()
    if key == "FILES_ROOT":
        v = v.strip('"\'').strip()
    if key == "MAIL_USER":
        v = v.lower()
    return v


def install(s: dict, pid: str, values: dict[str, str]) -> list[dict]:
    """-> the full MCP server list with this plugin added or updated. Raises ValueError with a message the user can act on."""
    if pid not in CATALOG:
        raise ValueError("Unknown plugin.")
    p = CATALOG[pid]
    servers = mcp_client.parse_servers(s)
    old = next((c for c in servers if c["name"] == _server_name(pid)), None)
    env: dict[str, str] = {}
    for f in p["fields"]:
        v = _clean(pid, f["key"], str(values.get(f["key"]) or ""))
        if not v and old:
            v = old["env"].get(f["key"], "")  # leaving a hidden field blank keeps what was saved
        if not v:
            v = str(f.get("default") or "")
        if not v:
            raise ValueError(f"“{f['label']}” is required.")
        env[f["key"]] = v
    plain = {k: secretbox.reveal(v) for k, v in env.items()}
    if pid == "folder" and not Path(plain["FILES_ROOT"]).is_dir():
        raise ValueError("That folder does not exist on this computer. Check the path, or use Shift + right-click → Copy as path.")
    if pid == "calendar" and not plain["CAL_ICS"].lower().startswith(("http://", "https://", "webcal://")) and not Path(plain["CAL_ICS"]).is_file():
        raise ValueError("Paste the https:// calendar address, or the path of an .ics file.")
    if pid == "mail" and "@" not in plain["MAIL_USER"]:
        raise ValueError("Enter your full email address, for example you@gmail.com.")
    if pid == "telegram" and not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{20,}", plain["TG_TOKEN"]):
        raise ValueError("That does not look like a bot token. It looks like 123456:ABC-DEF… and comes from @BotFather after /newbot.")
    stored = {k: (secretbox.protect(v) if k in _secret_keys(pid) else v) for k, v in env.items()}
    if pid == "telegram":
        stored["TG_DIR"] = str(tg_dir())  # where the plugin keeps its local inbox (not a secret)
        if old and old["env"].get("TG_BOT"):
            stored["TG_BOT"] = old["env"]["TG_BOT"]
    cfg = {"name": _server_name(pid), "transport": "stdio", "command": sys.executable, "args": [str(PLUGIN_DIR / p["script"])],
           "env": stored, "url": "", "headers": {}, "enabled": True, "always_allow": []}
    return [c for c in servers if c["name"] != cfg["name"]] + [cfg]


def uninstall(s: dict, pid: str) -> list[dict]:
    if pid not in CATALOG:
        raise ValueError("Unknown plugin.")
    return [c for c in mcp_client.parse_servers(s) if c["name"] != _server_name(pid)]


def check(pid: str, env: dict[str, str]) -> tuple[bool, str]:
    """Try the details once. -> (works, a sentence for the screen): what was found, or what is wrong in plain words."""
    env = {k: secretbox.reveal(v) for k, v in env.items()}
    if pid == "mail":
        return _check_mail(env["MAIL_HOST"], env["MAIL_USER"], env["MAIL_PASSWORD"])
    if pid == "calendar":
        return _check_calendar(env["CAL_ICS"])
    if pid == "telegram":
        return _check_telegram(env["TG_TOKEN"])
    if pid == "folder":
        root = Path(env["FILES_ROOT"])
        n = sum(1 for x in root.rglob("*") if x.is_file()) if root.is_dir() else 0
        return (True, f"Connected. {n:,} file{'' if n == 1 else 's'} found in this folder.") if root.is_dir() else (False, "That folder does not exist.")
    return True, "Connected."


def _check_mail(host: str, user: str, pw: str) -> tuple[bool, str]:
    gmail = bool(re.search(r"gmail|google", host, re.I))
    if gmail:
        pw = re.sub(r"\s+", "", pw)
    try:
        m = imaplib.IMAP4_SSL(host, 993, timeout=20)
        try:
            m.login(user, pw)
            typ, data = m.select("INBOX", readonly=True)
            n = int(data[0]) if typ == "OK" and data and data[0] else 0
        finally:
            try:
                m.logout()
            except Exception:  # noqa: BLE001 - a failed login leaves nothing to log out of
                pass
    except imaplib.IMAP4.error as exc:
        return False, ("The mail server rejected this login. "
                       + ("Gmail needs an **App password**, not your normal password: turn on 2-Step Verification, then create one at myaccount.google.com/apppasswords. " if gmail
                          else "Many providers need an app-specific password instead of your normal one. ")
                       + f"Check the address too. Server said: {str(exc)[:120]}")
    except (socket.gaierror, TimeoutError, OSError) as exc:
        return False, f"Could not reach the mail server “{host}” ({exc.__class__.__name__}). Check the server name and your internet connection."
    return True, f"Connected as {user}. {n:,} message{'' if n == 1 else 's'} in your inbox."


def _check_calendar(src: str) -> tuple[bool, str]:
    try:
        if src.lower().startswith(("http://", "https://", "webcal://")):
            req = urllib.request.Request(src.replace("webcal://", "https://", 1), headers={"User-Agent": "local-chat-calendar-plugin"})
            with urlopen_trusted(req, 20) as r:
                text = r.read(5_000_000).decode("utf-8", errors="replace")
        else:
            text = Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"Could not open that calendar ({exc.__class__.__name__}). Check the address: it should end in .ics and be the *secret* iCal address."
    if "BEGIN:VCALENDAR" not in text:
        return False, "That address did not return a calendar. Use the “Secret address in iCal format”, not the public link or the calendar ID."
    n = text.count("BEGIN:VEVENT")
    return True, f"Connected. {n:,} event{'' if n == 1 else 's'} found in this calendar."


# ---------------------------------------------------------------- Telegram: token check and chat pairing
def _check_telegram(token: str) -> tuple[bool, str]:
    import json
    try:
        with urlopen_trusted(f"https://api.telegram.org/bot{token}/getMe", 20) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return False, "Telegram rejected this token. Copy it again from @BotFather (send /mybots, pick your bot, API Token)." if exc.code in (401, 404) else f"Telegram answered HTTP {exc.code}."
    except OSError as exc:
        return False, f"Could not reach Telegram ({exc.__class__.__name__}). Check your internet connection."
    if not data.get("ok"):
        return False, "Telegram rejected this token."
    return True, f"Connected to the bot @{data['result'].get('username', '?')}. Now press Generate pairing code to connect a chat."


def tg_dir() -> Path:
    d = db.DATA_DIR / "telegram"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tg_read(name: str, default):
    import json
    try:
        return json.loads((tg_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _tg_write(name: str, data) -> None:
    import json
    tmp = tg_dir() / (name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(tg_dir() / name)


def tg_paired() -> list[dict]:
    return [{"id": cid, "title": v.get("title", cid), "type": v.get("type", "")} for cid, v in _tg_read("allowed.json", {}).items()]


def new_pair_code(minutes: int = 10) -> dict:
    """A one-time 6-digit code (valid for a few minutes). The user sends '/pair CODE' to the bot from the chat to be connected."""
    import secrets
    import time
    code = f"{secrets.randbelow(1_000_000):06d}"
    live = {c: e for c, e in _tg_read("codes.json", {}).items() if e > time.time()}
    live[code] = time.time() + minutes * 60
    _tg_write("codes.json", live)
    return {"code": code, "minutes": minutes}


def tg_unpair(chat_id: str) -> bool:
    allowed = _tg_read("allowed.json", {})
    if chat_id not in allowed:
        return False
    del allowed[chat_id]
    _tg_write("allowed.json", allowed)
    return True
