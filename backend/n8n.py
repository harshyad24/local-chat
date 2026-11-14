"""n8n: create starter workflows from templates through n8n's public API.

The model never writes n8n workflow JSON (small models get node names and parameters wrong). It picks a template and
fills a few fields; the app builds the workflow and creates it INACTIVE, so you open it in n8n, attach your own
credentials (Gmail, Google Calendar, ...) and review it before switching it on. Credentials never pass through this app.

Each template is an "MCP Server Trigger" workflow: its tool nodes become tools that this app's MCP client can use.
The node parameters below follow n8n's documented node types; they could not be run against a live n8n in this
build, which is why workflows are created as drafts.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)


class N8nError(Exception):
    pass


def base_url(s: dict) -> str:
    return (s.get("n8n_url") or "").strip().rstrip("/")


def configured(s: dict) -> bool:
    return bool(base_url(s) and (s.get("n8n_api_key") or "").strip())


def _headers(s: dict) -> dict:
    return {"X-N8N-API-KEY": s["n8n_api_key"].strip(), "Accept": "application/json"}


async def _request(s: dict, method: str, path: str, **kw: Any) -> Any:
    if not configured(s):
        raise N8nError("n8n is not set up. Add its URL and API key in Settings → Connections.")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.request(method, base_url(s) + "/api/v1" + path, headers=_headers(s), **kw)
    except httpx.ConnectError:
        raise N8nError(f"Could not reach n8n at {base_url(s)}. Is it running?") from None
    except httpx.HTTPError as exc:
        raise N8nError(f"n8n request failed: {exc.__class__.__name__}") from None
    if r.status_code in (401, 403):
        raise N8nError("n8n rejected the API key. Create one in n8n → Settings → n8n API.")
    if r.status_code == 404:
        raise N8nError("n8n's public API was not found. Enable it (n8n → Settings → n8n API) and check the URL.")
    if r.status_code >= 400:
        raise N8nError(f"n8n answered HTTP {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


async def ping(s: dict) -> dict:
    data = await _request(s, "GET", "/workflows", params={"limit": 1})
    return {"ok": True, "workflows": len(data.get("data", []))}


async def create_workflow(s: dict, workflow: dict) -> dict:
    return await _request(s, "POST", "/workflows", json=workflow)


# --------------------------------------------------------------- templates
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "tools"


def _ai(name: str, desc: str, kind: str = "string") -> str:
    return "={{ $fromAI('" + name + "', '" + desc.replace("'", "") + "', '" + kind + "') }}"


def _workflow(title: str, path: str, tool_nodes: list[dict], token: bool) -> dict:
    trigger = {
        "parameters": {"path": path, "authentication": "bearerAuth" if token else "none"},
        "type": "@n8n/n8n-nodes-langchain.mcpTrigger",
        "typeVersion": 1,
        "position": [0, 0],
        "name": "MCP Server",
        "webhookId": path,
    }
    nodes = [trigger]
    connections: dict[str, Any] = {}
    for i, node in enumerate(tool_nodes):
        node = {**node, "position": [320, -200 + i * 200]}
        nodes.append(node)
        connections[node["name"]] = {"ai_tool": [[{"node": "MCP Server", "type": "ai_tool", "index": 0}]]}
    return {"name": title, "nodes": nodes, "connections": connections, "settings": {"executionOrder": "v1"}}


def _mail(p: dict) -> dict:
    nodes = [
        {"name": "Search emails", "type": "n8n-nodes-base.gmailTool", "typeVersion": 2.1,
         "parameters": {"resource": "message", "operation": "getAll", "returnAll": False, "limit": 10,
                        "filters": {"q": _ai("search", "Gmail search query, e.g. is:unread from:alice")}}},
        {"name": "Read email", "type": "n8n-nodes-base.gmailTool", "typeVersion": 2.1,
         "parameters": {"resource": "message", "operation": "get", "messageId": _ai("message_id", "ID of the email to read")}},
        {"name": "Create draft", "type": "n8n-nodes-base.gmailTool", "typeVersion": 2.1,
         "parameters": {"resource": "draft", "operation": "create",
                        "subject": _ai("subject", "Subject line"), "message": _ai("body", "Email body"),
                        "options": {"sendTo": _ai("to", "Recipient email address")}}},
    ]
    if p.get("allow_send"):
        nodes.append({"name": "Send email", "type": "n8n-nodes-base.gmailTool", "typeVersion": 2.1,
                      "parameters": {"resource": "message", "operation": "send", "sendTo": _ai("to", "Recipient email address"),
                                     "subject": _ai("subject", "Subject line"), "message": _ai("body", "Email body")}})
    return _workflow(p["name"], p["path"], nodes, p["token"])


def _calendar(p: dict) -> dict:
    cal = {"__rl": True, "mode": "list", "value": "primary"}
    nodes = [
        {"name": "List events", "type": "n8n-nodes-base.googleCalendarTool", "typeVersion": 1.3,
         "parameters": {"operation": "getAll", "calendar": cal, "returnAll": False, "limit": 20,
                        "timeMin": _ai("from", "Start of the range, ISO date-time"), "timeMax": _ai("to", "End of the range, ISO date-time")}},
        {"name": "Create event", "type": "n8n-nodes-base.googleCalendarTool", "typeVersion": 1.3,
         "parameters": {"operation": "create", "calendar": cal,
                        "start": _ai("start", "Start, ISO date-time"), "end": _ai("end", "End, ISO date-time"),
                        "additionalFields": {"summary": _ai("title", "Event title")}}},
    ]
    return _workflow(p["name"], p["path"], nodes, p["token"])


TEMPLATES: dict[str, dict] = {
    "mail_tools": {
        "title": "Mail tools (Gmail)",
        "description": "Search and read email, and create drafts. Sending is off unless you ask for it.",
        "fields": [{"key": "allow_send", "label": "Also allow sending email", "default": False}],
        "build": _mail,
        "needs": "a Gmail credential",
    },
    "calendar_tools": {
        "title": "Calendar tools (Google Calendar)",
        "description": "List events in a date range and create events.",
        "fields": [],
        "build": _calendar,
        "needs": "a Google Calendar credential",
    },
}


def build(template: str, name: str = "", fields: dict | None = None, token: bool = False) -> tuple[dict, str]:
    """-> (workflow JSON, MCP path). Raises N8nError for an unknown template."""
    t = TEMPLATES.get(template)
    if not t:
        raise N8nError(f"Unknown template '{template}'. Choose one of: {', '.join(TEMPLATES)}.")
    title = (name or "").strip()[:80] or f"Local Chat · {t['title']}"
    path = "local-chat-" + _slug(template)
    params = {"name": title, "path": path, "token": token, **{k: bool(v) for k, v in (fields or {}).items() if k == "allow_send"}}
    return t["build"](params), path


def mcp_url_guess(s: dict, path: str) -> str:
    """Where the MCP Server Trigger probably listens. The exact URL is shown in the node's panel in n8n."""
    return f"{base_url(s)}/mcp/{path}"


def spec_for_model() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "build_n8n_workflow",
            "description": (
                "Create a draft automation in the user's n8n from a ready-made template (the user approves it, then attaches "
                "credentials in n8n). Templates: " + "; ".join(f"{k} = {v['description']}" for k, v in TEMPLATES.items())
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "template": {"type": "string", "enum": sorted(TEMPLATES)},
                    "name": {"type": "string", "description": "optional workflow name"},
                    "allow_send": {"type": "boolean", "description": "mail_tools only: also allow sending email"},
                },
                "required": ["template"],
            },
        },
    }
