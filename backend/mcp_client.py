"""MCP client: connect to MCP servers (local command, SSE or streamable HTTP) and offer their tools to the model.

Each server runs in its own background task that owns the connection (the MCP library needs the connection to be opened
and closed by the same task). Tools are namespaced `mcp__<server>__<tool>` and registered into tools.SPECS / tools.IMPLS,
so the normal agent loop, nudges and text-mode fallback work unchanged.

Safety: a tool that is not marked read-only by its server asks for approval before every call, unless the user chose
"always allow" for that tool.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from contextlib import AsyncExitStack
from typing import Any, Optional

from . import db

MAX_TOOLS_PER_TURN = 24  # small models get confused by long tool lists
MASK = "••••"
PREFIX = "mcp__"


PLUGIN_SERVERS: set[str] = set()  # names of the bundled plugins (filled in by plugins.py)
PLUGIN_ALLOWED: set[tuple[str, str]] = set()  # (chat id, plugin) pairs the user has OK'd; forgotten when the app restarts


def slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text or "").strip("_").lower()[:24] or "x"


def tool_name(server: str, tool: str) -> str:
    return (PREFIX + slug(server) + "__" + re.sub(r"[^a-zA-Z0-9_-]+", "_", tool))[:64]


RETIRED_SCRIPTS = ("whatsapp_server.py",)  # plugins that were removed: a saved entry for one is ignored (and dropped the next time servers are saved)


def parse_servers(s: dict) -> list[dict]:
    try:
        raw = json.loads(s.get("mcp_servers") or "[]")
    except ValueError:
        return []
    out, seen = [], set()
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict) or not str(x.get("name", "")).strip():
            continue
        name = str(x["name"]).strip()[:40]
        if any(str(a).replace("\\", "/").endswith(RETIRED_SCRIPTS) for a in (x.get("args") or [])):
            continue
        if slug(name) in seen:
            continue
        seen.add(slug(name))
        out.append({
            "name": name,
            "transport": x.get("transport") if x.get("transport") in ("stdio", "http", "sse") else "stdio",
            "command": str(x.get("command") or "").strip(),
            "args": [str(a) for a in (x.get("args") or [])][:40],
            "env": {str(k): str(v) for k, v in (x.get("env") or {}).items()},
            "url": str(x.get("url") or "").strip(),
            "headers": {str(k): str(v) for k, v in (x.get("headers") or {}).items()},
            "enabled": bool(x.get("enabled", True)),
            "always_allow": [str(t) for t in (x.get("always_allow") or [])],
        })
    return out


def mask(cfg: dict) -> dict:
    """Server config for the browser: secret values are hidden (the app keeps them)."""
    out = dict(cfg)
    out["env"] = {k: MASK for k in cfg.get("env", {})}
    out["headers"] = {k: MASK for k in cfg.get("headers", {})}
    return out


def merge_secrets(new: list[dict], old: list[dict]) -> list[dict]:
    """A saved value of MASK means 'unchanged': take the real value from the stored config."""
    by_name = {o["name"]: o for o in old}
    for n in new:
        prev = by_name.get(n.get("name"), {})
        for field in ("env", "headers"):
            vals = n.get(field) or {}
            n[field] = {k: (prev.get(field, {}).get(k, "") if v == MASK else v) for k, v in vals.items()}
    return new


def _clean_schema(schema: Any) -> dict:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    out = {"type": "object", "properties": schema.get("properties") or {}}
    if schema.get("required"):
        out["required"] = schema["required"]
    return out


class Conn:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.status = "connecting"  # connecting | ready | error
        self.error = ""
        self.tools: list[dict] = []  # [{name, ns, description, schema, read_only}]
        self.session: Any = None
        self.ready = asyncio.Event()
        self._stop = asyncio.Event()
        self.task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return self.cfg["name"]

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.sse import sse_client
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamablehttp_client

            cfg = self.cfg
            async with AsyncExitStack() as stack:
                if cfg["transport"] == "stdio":
                    if not cfg["command"]:
                        raise ValueError("No command set.")
                    exe = shutil.which(cfg["command"]) or cfg["command"]  # finds npx.cmd etc. on Windows
                    from . import secretbox
                    env = {k: secretbox.reveal(v) for k, v in (cfg["env"] or {}).items()} or None  # saved secrets are encrypted at rest
                    params = StdioServerParameters(command=exe, args=cfg["args"], env=env)
                    r, w = await stack.enter_async_context(stdio_client(params))
                else:
                    if not cfg["url"].startswith(("http://", "https://")):
                        raise ValueError("The URL must start with http:// or https://")
                    if cfg["transport"] == "sse":
                        r, w = await stack.enter_async_context(sse_client(cfg["url"], headers=cfg["headers"] or None))
                    else:
                        r, w, _ = await stack.enter_async_context(streamablehttp_client(cfg["url"], headers=cfg["headers"] or None))
                sess = await stack.enter_async_context(ClientSession(r, w))
                await asyncio.wait_for(sess.initialize(), 40)
                listed = await asyncio.wait_for(sess.list_tools(), 30)
                self.tools = [
                    {
                        "name": t.name,
                        "ns": tool_name(cfg["name"], t.name),
                        "description": (t.description or t.title or t.name)[:400],
                        "schema": _clean_schema(t.inputSchema),
                        "read_only": bool(getattr(t.annotations, "readOnlyHint", False)) if t.annotations else False,
                    }
                    for t in listed.tools
                ]
                self.session = sess
                self.status = "ready"
                self.ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # includes ExceptionGroup from the MCP task groups
            self.status = "error"
            self.error = _explain(exc)
        finally:
            self.session = None
            self.ready.set()

    async def call(self, tool: str, args: dict, timeout: float = 90) -> Any:
        if not self.session:
            raise RuntimeError(f"MCP server '{self.name}' is not connected.")
        return await asyncio.wait_for(self.session.call_tool(tool, args or {}), timeout)

    async def stop(self) -> None:
        self._stop.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, 8)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self.task.cancel()


def _explain(exc: BaseException) -> str:
    inner = exc
    while getattr(inner, "exceptions", None):  # unwrap ExceptionGroup
        inner = inner.exceptions[0]
    if isinstance(inner, FileNotFoundError):
        return "Command not found. Check the command name and that it is installed."
    if isinstance(inner, asyncio.TimeoutError):
        return "Timed out while connecting."
    text = str(inner) or inner.__class__.__name__
    return text[:300]


# ------------------------------------------------------------------ manager
CONNS: dict[str, Conn] = {}
_locks: dict[int, asyncio.Lock] = {}


def _lock_for_loop() -> asyncio.Lock:
    """One lock per event loop (tests and the server each run their own loop)."""
    return _locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())


async def sync(s: dict) -> None:
    """Make the running connections match the saved settings (start new/changed servers, stop removed/disabled ones)."""
    from . import tools

    async with _lock_for_loop():
        wanted = {c["name"]: c for c in parse_servers(s) if c["enabled"]}
        for name in list(CONNS):
            if name not in wanted or CONNS[name].cfg != wanted[name]:
                await _drop(name, tools)
        for name, cfg in wanted.items():
            if name not in CONNS:
                conn = Conn(cfg)
                CONNS[name] = conn
                conn.start()
        for conn in list(CONNS.values()):
            if not conn.ready.is_set():
                try:
                    await asyncio.wait_for(conn.ready.wait(), 45)
                except asyncio.TimeoutError:
                    conn.status, conn.error = "error", "Timed out while connecting."
            _register(conn, tools)


async def _drop(name: str, tools_mod) -> None:
    conn = CONNS.pop(name, None)
    if conn:
        for t in conn.tools:
            tools_mod.SPECS.pop(t["ns"], None)
            tools_mod.IMPLS.pop(t["ns"], None)
        await conn.stop()


async def stop_all() -> None:
    from . import tools

    for name in list(CONNS):
        await _drop(name, tools)


async def restart(s: dict, name: str) -> None:
    from . import tools

    async with _lock_for_loop():
        await _drop(name, tools)
    await sync(s)


def _register(conn: Conn, tools_mod) -> None:
    if conn.status != "ready":
        return
    for t in conn.tools:
        tools_mod.SPECS[t["ns"]] = {
            "type": "function",
            "function": {"name": t["ns"], "description": f"[{conn.name}] {t['description']}", "parameters": t["schema"]},
        }
        tools_mod.IMPLS[t["ns"]] = _make_impl(conn, t, tools_mod)


def _make_impl(conn: Conn, t: dict, tools_mod):
    async def impl(s: dict, chat_id: str, args: dict):
        def note(ok: bool, chars: int = 0) -> None:
            if conn.name in PLUGIN_SERVERS:
                try:
                    db.log_plugin_call(conn.name, t["name"], args if isinstance(args, dict) else {}, ok, chars)
                except Exception:  # noqa: BLE001 - the log must never break a tool call
                    pass

        try:
            res = await conn.call(t["name"], args if isinstance(args, dict) else {})
        except asyncio.TimeoutError:
            note(False)
            raise tools_mod.ToolError(f"{conn.name}: the tool timed out.") from None
        except Exception as exc:
            note(False)
            raise tools_mod.ToolError(f"{conn.name}: {_explain(exc)}") from None
        parts = []
        for c in res.content or []:
            kind = getattr(c, "type", "")
            if kind == "text":
                parts.append(c.text)
            elif kind == "resource" and getattr(c.resource, "text", None):
                parts.append(c.resource.text)
            else:
                parts.append(f"[{kind or 'binary'} content not shown]")
        text = "\n".join(parts).strip() or "(no output)"
        limit = int(s.get("page_char_limit") or 8000)
        shown = text[:limit] + ("\n…[output shortened]" if len(text) > limit else "")
        note(not res.isError, len(text))
        if res.isError:
            raise tools_mod.ToolError(f"{conn.name}/{t['name']} reported an error: {shown[:600]}")
        return tools_mod.ToolResult(True, shown, f"{conn.name} · {t['name']}", {"server": conn.name, "tool": t["name"], "text": shown[:1500]})

    return impl


# ---------------------------------------------------------- what the agent uses
def enabled_tool_names(s: dict) -> list[str]:
    """Namespaced tools of connected, enabled servers (capped so the model isn't flooded)."""
    names: list[str] = []
    for cfg in parse_servers(s):
        conn = CONNS.get(cfg["name"])
        if s.get("plugins_paused") and cfg["name"] in PLUGIN_SERVERS:
            continue  # the Pause switch: plugins are invisible to the model
        if cfg["enabled"] and conn and conn.status == "ready":
            names += [t["ns"] for t in conn.tools]
    return names[:MAX_TOOLS_PER_TURN]


def lookup(ns: str) -> Optional[tuple[Conn, dict]]:
    for conn in CONNS.values():
        for t in conn.tools:
            if t["ns"] == ns:
                return conn, t
    return None


def is_plugin_write(ns: str) -> bool:
    """A bundled plugin's tool that can change something (send a message). Always asks; it can never be set to "always allow"."""
    found = lookup(ns)
    return bool(found and found[0].name in PLUGIN_SERVERS and not found[1]["read_only"])


def needs_approval(ns: str) -> bool:
    found = lookup(ns)
    if not found:
        return True
    conn, t = found
    if is_plugin_write(ns):
        return True  # whatever the saved settings say
    return not (t["read_only"] or t["name"] in conn.cfg.get("always_allow", []))


def consent_needed(s: dict, chat_id: str, ns: str) -> Optional[str]:
    """The plugin's name when the user has not yet allowed it in this chat (and asks to be asked), else None."""
    found = lookup(ns)
    if not found or found[0].name not in PLUGIN_SERVERS or not s.get("plugin_consent", True):
        return None
    return None if (chat_id, found[0].name) in PLUGIN_ALLOWED else found[0].name


def grant(chat_id: str, ns: str) -> None:
    found = lookup(ns)
    if found:
        PLUGIN_ALLOWED.add((chat_id, found[0].name))


def describe_call(ns: str, args: dict) -> tuple[str, str]:
    """-> (title, readable arguments) for the approval card."""
    found = lookup(ns)
    title = f"Use “{found[1]['name']}” from {found[0].name}?" if found else f"Use {ns}?"
    if is_plugin_write(ns) and isinstance(args, dict) and "text" in args:
        # a message about to leave this computer: show exactly who gets exactly what
        return f"Send this message with {found[0].name}?", f"To: {args.get('chat') or '(the only paired chat)'}\n\n{str(args['text'])[:1500]}"
    return title, json.dumps(args or {}, indent=2, ensure_ascii=False)[:3000]


def allow_always(s: dict, ns: str) -> bool:
    """Remember 'always allow' for one tool (saved in the settings)."""
    found = lookup(ns)
    if not found or is_plugin_write(ns):
        return False  # a plugin that can send never gets a standing permission
    conn, t = found
    servers = parse_servers(s)
    for cfg in servers:
        if cfg["name"] == conn.name and t["name"] not in cfg["always_allow"]:
            cfg["always_allow"].append(t["name"])
    db.save_settings({"mcp_servers": json.dumps(servers)})
    conn.cfg["always_allow"] = next(c["always_allow"] for c in servers if c["name"] == conn.name)
    return True


def state(s: dict) -> list[dict]:
    """What the Connections screen shows."""
    out = []
    for cfg in parse_servers(s):
        conn = CONNS.get(cfg["name"])
        out.append({
            **mask(cfg),
            "status": "off" if not cfg["enabled"] else (conn.status if conn else "connecting"),
            "error": conn.error if conn and conn.status == "error" else "",
            "tools": [
                {"name": t["name"], "description": t["description"], "read_only": t["read_only"],
                 "allowed": t["read_only"] or t["name"] in cfg["always_allow"]}
                for t in (conn.tools if conn else [])
            ],
        })
    return out
