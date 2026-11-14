"""Agent loop: send tools to the model, run the calls it makes, feed results back, repeat.

`run_agent` is an async generator of event dicts that main.py streams to the browser:
  delta, tool_start, confirm, tool_result, sources, notice
It mutates `ctx.state` as it goes so a partially finished run can still be saved.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from contextlib import aclosing
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse

from . import charts, dashboards, db, library, llm, mcp_client, tools

MAX_ITERATIONS = 8  # model turns (the last one is always a plain answer)
MAX_CALLS_PER_TURN = 4
MAX_IMAGE_BYTES = 10_000_000
CONFIRMS: dict[str, asyncio.Future] = {}  # pending "run this code / use this tool?" prompts
CONFIRM_TOOL: dict[str, str] = {}  # prompt id -> tool name (so "always allow" knows which tool)


def approval_needed(name: str, args: dict, s: dict, chat_id: str = "") -> Optional[tuple[str, str, str]]:
    """-> (kind, title, text shown on the approval card) when the user must approve this call first."""
    if chat_id and name.startswith(mcp_client.PREFIX) and not mcp_client.is_plugin_write(name):  # a sending tool gets its own, stricter card below
        plugin = mcp_client.consent_needed(s, chat_id, name)
        if plugin:  # first use of a plugin in this chat: ask once, whatever the tool does
            _, text = mcp_client.describe_call(name, args)
            return "tool", f"Let the assistant use your {plugin} in this chat?", text
    if name == "run_python" and s.get("confirm_code", True):
        return "code", "Run this Python code on your computer?", str(args.get("code", ""))
    if name.startswith(mcp_client.PREFIX) and mcp_client.needs_approval(name):
        title, text = mcp_client.describe_call(name, args)
        return "tool", title, text
    if name == "build_n8n_workflow":
        return "tool", "Create a draft workflow in n8n?", json.dumps(args, indent=2)
    return None
ACTION_TOOLS = {"web_search", "fetch_page", "read_file", "search_chat"}  # tools whose use the model may narrate instead of doing


# ----------------------------------------------------------------- helpers
def est_tokens(text: str) -> int:
    return len(text) // 3 + 4


def strip_think(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?(</think>|$)", "", text).strip()


def domain_of(url: str) -> str:
    host = urlparse(url).hostname or url
    return host[4:] if host.startswith("www.") else host


class ThinkStripper:
    """Drops <think>…</think> from a text stream (used for turns after the first)."""

    def __init__(self) -> None:
        self.inside = False
        self.buf = ""

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while True:
            if not self.inside:
                # deepseek-r1 sometimes writes a lone "</think>" into its answer after its reasoning was split off
                orphan, opener = self.buf.find("</think>"), self.buf.find("<think>")
                if orphan >= 0 and (opener < 0 or orphan < opener):
                    out += self.buf[:orphan]
                    self.buf = self.buf[orphan + len("</think>"):]
                    continue
            tag = "</think>" if self.inside else "<think>"
            i = self.buf.find(tag)
            if i >= 0:
                if not self.inside:
                    out += self.buf[:i]
                self.buf = self.buf[i + len(tag):]
                self.inside = not self.inside
                continue
            keep = 0
            for k in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-k:]):
                    keep = k
                    break
            if not self.inside:
                out += self.buf[: len(self.buf) - keep]
            self.buf = self.buf[len(self.buf) - keep:]
            return out

    def flush(self) -> str:
        out = "" if self.inside else self.buf
        self.buf = ""
        return out


class CallHider:
    """Hides everything from '<tool_call' onwards (text-mode tool calls are not for the user)."""

    TAG = "<tool_call"

    def __init__(self) -> None:
        self.buf = ""
        self.hidden = False

    def feed(self, text: str) -> str:
        if self.hidden:
            return ""
        self.buf += text
        i = self.buf.find(self.TAG)
        if i >= 0:
            out, self.buf, self.hidden = self.buf[:i], "", True
            return out
        keep = 0
        for k in range(min(len(self.TAG) - 1, len(self.buf)), 0, -1):
            if self.TAG.startswith(self.buf[-k:]):
                keep = k
                break
        out = self.buf[: len(self.buf) - keep]
        self.buf = self.buf[len(self.buf) - keep:]
        return out

    def flush(self) -> str:
        out = "" if self.hidden else self.buf
        self.buf = ""
        return out


# ------------------------------------------------------------ system prompt
CHART_GUIDE = (
    "CHARTS (built in and always available): to draw ANY chart or graph — bar, line, pie, scatter, histogram, "
    "heatmap, treemap, sankey, box plot, radar, network, tree, ER diagram and more — call the create_chart tool with "
    "the data; the app draws it for you. Never write chart JSON, ASCII charts or plotting code. Chart only real data "
    "(from the user, a tool result, or your own arithmetic) and say where it came from; if the data you need is "
    "missing, say what you need instead of inventing it. After the chart appears, add 1-3 sentences on what it shows "
    "and do not repeat its numbers. Use ```mermaid only for flowcharts, sequence diagrams and state machines. "
    "For a dashboard use create_dashboard (never write dashboard code)."
)


ARTIFACT_GUIDE = (
    "SIDE PANEL: when asked to build a web page, small app, game or interactive visual, write ONE complete, self-contained "
    "```html block (inline CSS and JS; libraries only from https CDNs) — the app previews it beside the chat. Use ```svg for "
    "vector images and ```markdown for long documents the user may want to save. Do not split one page across several blocks."
)

DASHBOARD_GUIDE = (
    "DASHBOARDS (built in): when the user asks for a dashboard, call create_dashboard ONCE with the data; the app lays it out "
    "in a fixed professional pattern (header, KPI cards, charts, table). You only pick the template and supply real "
    "numbers — never write dashboard HTML or code. Use the KPI and chart roles of the template. Every number must come "
    "from the user, a tool result or your own arithmetic on those. Do NOT add estimated, sample or placeholder charts to "
    "fill a slot, and never repeat the same data in two charts: fewer, real charts are better. If you lack the data, ask for it."
)


def internet_statement(web_state: str) -> str:
    """Tell the model plainly whether it is online. (Models otherwise assume they are offline.)"""
    if web_state == "on":
        return (
            "INTERNET ACCESS: YES. You are connected to the live internet in this conversation through the "
            "web_search and fetch_page tools. Never say that you cannot browse, cannot access links, or have no "
            "internet access, and never answer questions about recent events or supplied links from memory — "
            "call the tools."
        )
    if web_state == "disabled":
        return (
            "INTERNET ACCESS: NO. The user switched Web search on, but the web tools are disabled in Settings → Tools. "
            "You cannot browse right now; tell the user to enable them if they need current information."
        )
    return (
        "INTERNET ACCESS: NO, not for this message. The user's Web search switch is OFF, so you cannot open links or "
        "look anything up, and your knowledge may be out of date. If the user needs current information or gives "
        "you links to read, tell them to switch on \"Web search\" in the + menu next to the message box. "
        "Do not pretend to have browsed."
    )


def tool_guidance(names: list[str]) -> list[str]:
    g: list[str] = []
    if ACTION_TOOLS & set(names):
        g.append(
            "TOOL RULES: when a task needs a tool, CALL it immediately with real arguments — do not describe what you "
            "are going to do, do not write a plan, do not ask permission. Keep going until you have what you need, "
            "then give the user the result. Only report an action as done if its tool result says it succeeded; if a "
            "tool fails, say exactly what failed, then try another way (different query, other URL) before giving up. "
            "Never invent a URL, quotation, statistic or date."
        )
    if "web_search" in names or "fetch_page" in names:
        g.append(
            "Search first; if snippets are not enough, fetch the 1-3 most relevant pages. When the user gives you URLs, "
            "fetch those exact URLs. Cite sources inline with the numbers shown in tool results, like [1] or [2][3], "
            "right after the claim they support. Only cite numbers that were returned; never invent sources or "
            "numbers. Do not add a separate list of sources — the app shows it. "
            "For numeric or fact-checking research: for every number say WHAT it measures (e.g. seats won vs. seats "
            "called vs. candidates leading vs. projected vs. total composition) and which source and date it comes from; "
            "do not merge numbers that measure different things; when two sources disagree, show both and explain the "
            "likely reason instead of picking one silently. Use the date a page states about itself (shown in the page "
            "metadata); if none is shown, say the date is unknown."
        )
    if "search_chat" in names:
        g.append(
            "Before saying you lack something the user told you earlier (links, files, numbers), call search_chat. "
            "Links from earlier in this conversation are also listed below."
        )
    if "run_python" in names:
        g.append(
            "CODE (built in, always available): use run_python for exact calculations, data analysis of attached files and "
            "anything that needs real computation instead of guessing. Always print() the values you need. Never claim output "
            "you have not seen. For a plain chart of numbers you already have, prefer create_chart."
        )
    if "create_map" in names:
        g.append("MAPS: call create_map with place names (the app finds coordinates). Use route=true with the places in visiting order for an itinerary.")
    if "read_file" in names:
        g.append("The user attached files. Read them with read_file before answering questions about them.")
    if "current_datetime" in names:
        g.append("Use current_datetime if you need the exact current time.")
    return g


def system_prompt(s: dict, names: list[str], web_state: str = "off", urls: Optional[list[str]] = None,
                  extras: Optional[dict] = None) -> str:
    extras = extras or {}
    parts = [
        "You are a helpful, knowledgeable assistant running locally on the user's computer. "
        "Answer accurately and clearly. Format with Markdown. Put code in fenced blocks with a "
        "language tag. Write math in LaTeX ($...$ inline, $$...$$ for display). "
                "Give the user results, not a running commentary of your own planning.",
        f"Current date and time: {datetime.now().strftime('%A, %B %d, %Y, %I:%M %p')}.",
        internet_statement(web_state),
    ]
    parts += tool_guidance(names)
    parts.append(CHART_GUIDE)
    parts.append(ARTIFACT_GUIDE)
    if extras.get("dashboard") and "create_dashboard" in names:
        parts.append(DASHBOARD_GUIDE + " " + dashboards.blueprint(extras["dashboard"]))
    if "save_memory" in names:
        parts.append(
            "MEMORY: the app keeps a list of lasting facts about the user. When the user asks you to remember something, "
            "or states a stable fact about themselves (name, job, preferences, ongoing projects), call save_memory with one "
            "short sentence. Never save passwords, keys, or one-off details."
        )
    if urls:
        parts.append(
            "Links that appeared earlier in this conversation (use these when the user refers to 'the links', "
            "'those sources' or 'the URLs I sent' — never ask them to paste again):\n"
            + "\n".join(f"{i}. {u}" for i, u in enumerate(urls, 1))
        )
    lang = (s.get("language") or "Auto").strip()
    if lang and lang.lower() != "auto":
        parts.append(f"Respond in {lang} unless the user asks for another language.")
    if extras.get("assistant"):
        a = extras["assistant"]
        parts.append(f"You are acting as the custom assistant \"{a['name']}\". Follow these instructions:\n{a['prompt']}")
    if extras.get("project"):
        p = extras["project"]
        block = f"This chat belongs to the project \"{p['name']}\"."
        if p.get("instructions"):
            block += "\nProject instructions (follow them):\n" + p["instructions"]
        if p.get("files"):
            block += "\nProject files (read them with read_file when relevant, using the file id):\n" + "\n".join(
                f"- {f['name']} (file id: {f['id']})" for f in p["files"]
            )
        parts.append(block)
    if extras.get("memories"):
        parts.append(
            "What you remember about the user from earlier conversations (use it naturally when relevant; don't "
            "mention that you have a memory unless asked):\n" + "\n".join(f"- {t}" for t in extras["memories"])
        )
    if s.get("about_you", "").strip():
        parts.append("About the user:\n" + s["about_you"].strip())
    if s.get("response_style", "").strip():
        parts.append("How the user wants you to respond:\n" + s["response_style"].strip())
    return "\n\n".join(parts)


# ---- detecting "talked about doing it" instead of doing it
PLAN_RE = re.compile(
    r"\b(?:i(?:'ll| will| am going to|'m going to| need to| should| must)|let me(?! know)|first,? i|next,? i|to do this,? i|"
    r"i(?:'ll| will) now)\b[^.\n]{0,110}?\b(search|browse|look (?:it |this |that |them )?up|fetch|open|visit|check|read|"
    r"retrieve|pull|run|analy[sz]e|verify|gather|find|audit|review)",
    re.I,
)
REFUSAL_RE = re.compile(
    r"\b(?:can(?:no|')t|cannot|unable to|not able to|don't have|do not have|no ability to|lack(?:s)? (?:the )?(?:ability|access))\b"
    r"[^.\n]{0,70}?\b(browse|internet|the web|online|real-time|live (?:data|information)|access(?: to)? (?:the |any |those |these |your )?"
    r"(?:link|url|page|site|website)s?|open (?:the |any |those |these |your )?(?:link|url)s?|retrieve (?:the |those |these |your )?(?:link|url)s?)",
    re.I,
)
MAX_NUDGES = 2
BAD_CALL_NUDGE = (
    "Your last tool call was malformed and could not be run. Call the tool again with valid arguments. "
    "Keep it short: if it contains code, make the code as brief as possible."
)
# a mermaid block is a legitimate answer for process diagrams, but not for data charts (sankey, pie, xychart …)
MERMAID_PROCESS = re.compile(
    r"```mermaid\s*(?:flowchart|graph|sequenceDiagram|stateDiagram|classDiagram|erDiagram|gantt|journey|mindmap|timeline|gitGraph)", re.I
)
REASONER = re.compile(r"deepseek-r1|\br1\b|qwq|reasoner", re.I)  # reasoning models: slow free-form turns, unreliable tool choice
CHART_NUDGE = (
    "The user asked for a chart or graph. Call the create_chart tool NOW with the real data (labels and values, or "
    "nodes and edges). If you do not have the data, say exactly which data you need instead."
)
DASHBOARD_NUDGE = (
    "The user asked for a dashboard. Call the create_dashboard tool NOW with the template, KPI cards and charts built from "
    "real data. If you do not have the data, say exactly which data you need instead."
)
NUDGE = (
    "You described an action instead of doing it. Do not explain or plan — call the appropriate tool RIGHT NOW with "
    "real arguments. {web}If it is truly impossible, say so in one sentence."
)


def looks_unexecuted(text: str, names: list[str]) -> bool:
    t = (text or "").strip()
    if not t or not (ACTION_TOOLS & set(names)):
        return False
    if ("web_search" in names or "fetch_page" in names) and REFUSAL_RE.search(t[:800]):
        return True
    return len(t) < 700 and bool(PLAN_RE.search(t[:500]))


_CITE_RE = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")


def check_citations(answer: str, n_sources: int) -> list[int]:
    """Citation numbers used in the answer that don't correspond to any retrieved source."""
    body = re.sub(r"```[\s\S]*?```|`[^`\n]*`", "", answer)
    used = {int(x) for grp in _CITE_RE.findall(body) for x in re.split(r"\s*,\s*", grp)}
    return sorted(n for n in used if n < 1 or n > n_sources)


_JSON_FENCE = re.compile(r"```(?:json|chart)?\s*(\{[\s\S]*?\})\s*```", re.I)


def rescue_chart_call(text: str) -> Optional[dict]:
    """A small model sometimes writes the chart data as a JSON block instead of calling create_chart.
    If that block is a valid chart, the app makes the call on its behalf."""
    for m in _JSON_FENCE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if not isinstance(obj, dict) or not (obj.get("type") or obj.get("visualization_type") or obj.get("chart_type")):
            continue
        try:
            charts.build(obj)
        except charts.ChartError:
            continue
        return {"id": "call_rescued", "name": "create_chart", "arguments": obj}
    return None


def rescue_dashboard_call(text: str) -> Optional[dict]:
    """A JSON block that already looks like a dashboard becomes a real create_dashboard call."""
    for m in _JSON_FENCE.finditer(text or ""):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if not isinstance(obj, dict) or not obj.get("kpis") or not obj.get("charts"):
            continue
        try:
            dashboards.build(obj)
        except dashboards.DashboardError:
            continue
        return {"id": "call_rescued", "name": "create_dashboard", "arguments": obj}
    return None


EXTRACT_DASH_SYSTEM = (
    "You turn a chat into ONE dashboard specification. Reply with ONLY a JSON object, no prose, no code fences. "
    "Use only numbers and names that appear in the conversation; never invent data. If there is no usable data reply "
    "{\"error\": \"no data\"}. Fields: template (one of: %s), title, period, kpis [{label, value, unit, change}] "
    "(4-6 cards), charts [{type, title, labels, values}] (2-6 charts; type one of line_chart, bar_chart, "
    "horizontal_bar_chart, donut_chart, area_chart, funnel_chart, waterfall_chart, histogram, scatter_plot, gauge_chart; "
    "use series [{name, values}] for several lines), table {title, columns, rows}. %s"
)

EXTRACT_SYSTEM = (
    "You turn a chat into ONE chart specification. Reply with ONLY a JSON object, no prose, no code fences. "
    "Use only numbers and names that appear in the conversation; never invent data. If the conversation has no usable "
    "data, reply {\"error\": \"no data\"}. Fields: type (one of: %s), title, labels (list of text), values (list of "
    "numbers) OR series [{name, values}]. Other shapes: scatter/bubble series [{name, points:[{x,y,r}]}]; histogram "
    "values; gauge values [v] + min,max; box_plot groups [{name, values}]; candlestick ohlc "
    "[{label,open,high,low,close}]; sankey edges [{source,target,value}]; graphs/trees nodes + edges "
    "[{source,target,label}]."
)


async def extract_chart_call(ctx, draft: str, tool: str = "create_chart") -> Optional[dict]:
    """Fallback for models that won't call create_chart (e.g. deepseek-r1): ask the same model for ONLY the chart JSON in
    one short request, validate it, and make the call on its behalf. The model still supplies the data; the app draws it."""
    recent = [m for m in ctx.prompt if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)][-4:]
    convo = "\n\n".join(f"{m['role'].upper()}: {strip_think(m['content'])[:1500]}" for m in recent)
    if draft and draft not in convo:
        convo += f"\n\nASSISTANT (draft): {draft[:1500]}"
    msgs = [
        {"role": "system", "content": (
            EXTRACT_DASH_SYSTEM % (", ".join(sorted(dashboards.TEMPLATES)), dashboards.blueprint(getattr(ctx, "dash_template", "general")))
            if tool == "create_dashboard" else EXTRACT_SYSTEM % ", ".join(sorted(charts.TYPES)))},
        {"role": "user", "content": convo + "\n\nWrite the chart JSON now."},
    ]
    dash = tool == "create_dashboard"
    ctx.extract_error = ""
    if getattr(ctx, "s", {}).get("agent_engine") == "langgraph":
        try:
            from .integrations import langgraph_flows
            return await langgraph_flows.extract(ctx, msgs, dash, tool)
        except ImportError:
            pass  # LangGraph not installed: the built-in loop below does the same job
    for attempt in range(2):  # the second attempt shows the model exactly what was wrong with its first JSON
        try:
            raw = await llm.complete(ctx.s, ctx.model, msgs, max_tokens=1800 if dash else 900, json_mode=True,
                                     num_ctx=6144 if dash else 4096, timeout=900.0 if dash else 400.0)  # a 32B model on a laptop/desktop GPU needs minutes
        except llm.LLMError as exc:
            ctx.extract_error = f"the model did not answer ({str(exc)[:120]})"
            return None
        obj, problem, give_up = check_extraction(raw, dash)
        if obj:
            ctx.extract_error = ""
            return {"id": "call_extracted", "name": tool, "arguments": obj}
        ctx.extract_error = problem
        if give_up:
            return None
        msgs = msgs + [{"role": "assistant", "content": (raw or "")[:1500]},
                       {"role": "user", "content": f"That was rejected: {problem}. Fix it and reply with ONLY the corrected JSON object."}]
    return None


def check_extraction(raw: str, dash: bool) -> tuple[Optional[dict], str, bool]:
    """Validate the JSON a model wrote for a chart/dashboard. -> (spec or None, what was wrong, stop trying)."""
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None, "no JSON object in the reply", False
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None, "the reply was not valid JSON", False
    if not isinstance(obj, dict):
        return None, "the reply was not a JSON object", False
    if obj.get("error"):
        return None, "the conversation has no numbers to chart", True
    try:
        (dashboards.build if dash else charts.build)(obj)
    except (charts.ChartError, dashboards.DashboardError) as exc:
        return None, str(exc)[:300], False
    return obj, "", False


def text_mode_instructions(specs: list[dict]) -> str:
    lines = [
        "You can use tools. To call one, reply with ONLY this block and nothing else:",
        '<tool_call>{"name": "TOOL_NAME", "arguments": {"param": "value"}}</tool_call>',
        "The tool result will be sent back to you; then continue. Call one tool per reply. "
        "If you do not need a tool, just answer normally. Available tools:",
    ]
    for spec in specs:
        fn = spec["function"]
        params = ", ".join(f"{k}: {v.get('type', 'string')}" for k, v in fn["parameters"]["properties"].items())
        lines.append(f"- {fn['name']}({params}) — {fn['description']}")
    return "\n".join(lines)


_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)", re.S)
_FENCE_RE = re.compile(r"```(?:json|tool_call)?\s*(\{.*?\})\s*```", re.S)


def parse_text_calls(text: str, allowed: set[str]) -> list[dict]:
    """Find tool calls written as text by models without native tool calling."""
    calls: list[dict] = []
    for m in list(_CALL_RE.finditer(text)) + list(_FENCE_RE.finditer(text)):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        name = obj.get("name") or obj.get("tool")
        args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
        if name in allowed and isinstance(args, dict):
            calls.append({"id": f"call_{len(calls)}", "name": name, "arguments": args})
    return calls[:1]


# ---------------------------------------------------------- prompt building
def _attachment_text(a: dict, vision: bool) -> tuple[str, Optional[tuple[str, str]]]:
    f = db.get_file(a["id"])
    if not f or not Path(f["path"]).exists():
        return f"- {a['name']} (file is no longer available)", None
    if a.get("kind") == "image":
        if vision and f["size"] <= MAX_IMAGE_BYTES:
            data = base64.b64encode(Path(f["path"]).read_bytes()).decode()
            return f"- {a['name']} (image, shown to you)", (f["mime"] or "image/png", data)
        return f"- {a['name']} (image — this model can't view images; tell the user if they ask about it)", None
    kb = max(1, (f["size"] or 0) // 1024)
    return f"- {a['name']} (file id: {f['id']}, {a.get('kind', 'file')}, {kb} KB)", None


def build_messages(
    s: dict, history: list[dict], names: list[str], vision: bool, web_state: str = "off",
    extras: Optional[dict] = None,
) -> tuple[list[dict], int]:
    """Trim the oldest turns so the prompt fits ~75% of the context window. Links from the whole
    conversation are kept in the system prompt, so trimming can never lose them."""
    urls = tools.conversation_urls(history)
    system = {"role": "system", "content": system_prompt(s, names, web_state, urls, extras)}
    budget = int(s["num_ctx"] * 0.75) - est_tokens(system["content"])
    kept: list[dict] = []
    used = 0
    for m in reversed(history):
        if m["role"] == "assistant":
            msg = {"role": "assistant", "content": strip_think(m["content"])}
        else:
            text = m["content"]
            notes, images = [], []
            for a in m.get("attachments") or []:
                note, img = _attachment_text(a, vision)
                notes.append(note)
                if img:
                    images.append(img)
            if notes:
                text += "\n\n[Attached files]\n" + "\n".join(notes)
            msg = llm.user_message(s, text, images)
            used += 400 * len(images)
        cost = est_tokens(msg["content"] if isinstance(msg["content"], str) else m["content"])
        if kept and used + cost > budget:
            break
        used += cost
        kept.append(msg)
    kept.reverse()
    trimmed = len(history) - len(kept)
    while len(kept) > 1 and kept[0]["role"] != "user":
        kept.pop(0)
        trimmed += 1
    return [system, *kept], trimmed


def shrink_prompt(prompt: list[dict], limit_tokens: int) -> None:
    """Keep the in-flight conversation inside the context window by clipping old tool results."""
    def total() -> int:
        return sum(est_tokens(m["content"]) if isinstance(m.get("content"), str) else 400 for m in prompt)

    for m in prompt[1:-2]:
        if total() <= limit_tokens:
            return
        c = m.get("content")
        is_result = m["role"] == "tool" or (isinstance(c, str) and c.startswith("<tool_result"))
        if is_result and isinstance(c, str) and len(c) > 600:
            m["content"] = c[:500] + "\n…[older tool output shortened to save context]"


# ----------------------------------------------------------------- the loop
class Ctx:
    def __init__(self, s: dict, model: str, chat_id: str, prompt: list[dict], think: Optional[bool],
                 tool_names: list[str], caps: dict) -> None:
        self.s = s
        self.model = model
        self.chat_id = chat_id
        self.prompt = prompt
        self.think = think
        self.tool_names = tool_names
        self.caps = caps
        self.extract_error = ""
        self.state: dict[str, Any] = {
            "acc": "", "tools": [], "sources": [], "in": 0, "out": 0,
            "reason": "stop", "t_open": None, "t_close": None,
        }


def _add_source(state: dict, title: str, url: str) -> int:
    for src in state["sources"]:
        if src["url"] == url:
            return src["n"]
    n = len(state["sources"]) + 1
    state["sources"].append({"n": n, "title": (title or domain_of(url))[:200], "url": url, "domain": domain_of(url)})
    return n


def _track_think(st: dict) -> None:
    if st["t_open"] is None and "<think>" in st["acc"]:
        st["t_open"] = time.monotonic()
    if st["t_open"] is not None and st["t_close"] is None and "</think>" in st["acc"]:
        st["t_close"] = time.monotonic()


async def _execute(ctx: Ctx, call: dict, entry: dict) -> AsyncIterator[dict]:
    """Run one tool call. Yields UI events; sets entry['model_text'] for the model."""
    name, args = call["name"], call["arguments"]
    s, st = ctx.s, ctx.state
    result: Optional[tools.ToolResult] = None

    if name not in ctx.tool_names or name not in tools.IMPLS:
        result = tools.ToolResult(False, f"Error: unknown or disabled tool '{name}'.", "Unavailable")
    elif (ask := approval_needed(name, args, s, ctx.chat_id)) is not None:
        kind, title, text = ask
        cid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        CONFIRMS[cid] = fut
        CONFIRM_TOOL[cid] = name
        entry["status"] = "confirm"
        yield {"type": "confirm", "id": entry["id"], "cid": cid, "code": text, "kind": kind, "title": title,
               "always": name.startswith(mcp_client.PREFIX) and not mcp_client.is_plugin_write(name)}
        try:
            approved = await asyncio.wait_for(fut, timeout=300)
        except asyncio.TimeoutError:
            approved = False
        finally:
            CONFIRMS.pop(cid, None)
            CONFIRM_TOOL.pop(cid, None)
        if approved and name.startswith(mcp_client.PREFIX):
            mcp_client.grant(ctx.chat_id, name)  # remembered for this chat (until the app restarts)
        if not approved:
            result = tools.ToolResult(
                False, "The user declined this action. Do not do it; answer without it or ask what they prefer.",
                "Declined",
            )
            entry["declined"] = True
    if result is None:
        try:
            result = await tools.IMPLS[name](s, ctx.chat_id, args)
        except tools.ToolError as exc:
            result = tools.ToolResult(False, f"Error: {exc}", str(exc)[:120])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a tool bug must not kill the chat
            result = tools.ToolResult(False, f"Error: {exc.__class__.__name__}: {str(exc)[:200]}", "Error")

    # number the sources and build what the model sees
    new_sources = False
    if result.ok and result.sources:
        before = len(st["sources"])
        numbered = [(_add_source(st, x["title"], x["url"]), x) for x in result.sources]
        new_sources = len(st["sources"]) > before
        if name == "web_search":
            lines = [f'Web results for "{args.get("query", "")}":', ""]
            for (n, _), item in zip(numbered, result.detail["results"]):
                item["n"] = n
                lines.append(f"[{n}] {item['title']}\nURL: {item['url']}\n{item['snippet']}\n")
            lines.append("Cite what you use as [n]. Use fetch_page to read a result in full.")
            result.content = "\n".join(lines)
        elif name == "fetch_page":
            n = numbered[0][0]
            result.detail["n"] = n
            result.content = f"[{n}] {result.detail.get('title') or result.summary} ({result.detail['url']})\n\n{result.content}"
    elif name == "web_search" and result.ok:
        result.content = result.content or "No results."

    entry.update(
        status="done" if result.ok else "error",
        ok=result.ok, summary=result.summary, detail=result.detail, files=result.files,
        model_text=result.content,
    )
    event = {
        "type": "tool_result", "id": entry["id"], "ok": result.ok, "summary": result.summary,
        "detail": result.detail, "files": result.files, "declined": bool(entry.get("declined")),
    }
    yield event
    if new_sources:
        yield {"type": "sources", "sources": st["sources"]}


async def run_agent(ctx: Ctx) -> AsyncIterator[dict]:
    s, st = ctx.s, ctx.state
    specs = [tools.SPECS[n] for n in ctx.tool_names]
    native = bool(specs) and ctx.caps.get("tools") is not False
    text_mode = bool(specs) and not native
    allowed = set(ctx.tool_names)

    def enable_text_mode() -> None:
        ctx.prompt[0]["content"] += "\n\n" + text_mode_instructions(specs)

    if text_mode:
        enable_text_mode()

    # A model without native tool calling (deepseek-r1 and friends) is asked for a dashboard: waiting for it to think for minutes and
    # then (usually) write dashboard code wastes the turn. The app asks it once, in JSON mode, for just the numbers and lays them out.
    if (text_mode or REASONER.search(ctx.model or "")) and getattr(ctx, "wants_dashboard", False) and "create_dashboard" in allowed:
        yield {"type": "notice", "text": "This kind of model often skips the dashboard tool, so the app is asking it for the dashboard data and building the layout itself."}
        pre = await extract_chart_call(ctx, "", "create_dashboard")
        if pre:
            entry = {"id": f"{len(st['tools'])}", "name": pre["name"], "args": pre["arguments"], "status": "running"}
            st["tools"].append(entry)
            yield {"type": "tool_start", "id": entry["id"], "name": pre["name"], "args": pre["arguments"]}
            async for ev in _execute(ctx, dict(pre, id=f"call_{entry['id']}"), entry):
                yield ev
            ctx.prompt.append({"role": "assistant", "content": "(I built the dashboard.)"})
            ctx.prompt.append({"role": "user", "content": f'<tool_result name="create_dashboard">\n{entry.get("model_text", "")}\n</tool_result>\n\nNow write 2-4 sentences of insight about it.'})

    turn = 0
    guard = 0
    nudges = 0
    bad_calls = 0
    chart_nudged = False
    extract_tries = 0
    unexecuted = False
    web_on = "web_search" in allowed or "fetch_page" in allowed
    while turn < MAX_ITERATIONS and guard < MAX_ITERATIONS + 2 * MAX_NUDGES + 3:
        guard += 1
        final = turn == MAX_ITERATIONS - 1 or not specs
        use_tools = bool(specs) and not final
        if final and turn > 0 and ctx.prompt[-1]["role"] != "user":
            ctx.prompt.append({"role": "user", "content": "(Tool limit reached. Write your final answer now using what you have gathered.)"})
        shrink_prompt(ctx.prompt, int(s["num_ctx"] * 0.85))

        later = turn > 0 or nudges > 0  # reasoning is only shown for the very first model call
        acc_before = len(st["acc"])
        stripper = ThinkStripper() if later else None
        hider = CallHider() if (text_mode and use_tools) else None
        raw = ""
        calls: list[dict] = []
        emitted = False

        def emit(text: str) -> Optional[dict]:
            nonlocal emitted
            if not text:
                return None
            if not emitted and later and st["acc"] and not st["acc"].endswith("\n"):
                text = "\n\n" + text
            emitted = True
            st["acc"] += text
            if not later:
                _track_think(st)
            return {"type": "delta", "text": text}

        try:
            api_tools = specs if (native and use_tools) else None
            async with aclosing(llm.stream_chat(s, ctx.model, ctx.prompt, ctx.think, api_tools)) as stream:
                async for e in stream:
                    if e["type"] == "text":
                        raw += e["text"]
                        out = e["text"]
                        if stripper:
                            out = stripper.feed(out)
                        if hider:
                            out = hider.feed(out)
                        ev = emit(out)
                        if ev:
                            yield ev
                    elif e["type"] == "tool_calls":
                        calls = e["calls"]
                    elif e["type"] == "usage":
                        st["in"] += e["in"]
                        st["out"] += e["out"]
                    elif e["type"] == "finish":
                        st["reason"] = e["reason"]
        except llm.LLMError as exc:
            if exc.code == "no_tools" and native:
                native, text_mode = False, True  # fall back to the JSON-instruction format
                enable_text_mode()
                continue
            if exc.code == "bad_tool_call" and use_tools and bad_calls < 2:
                # The server couldn't parse the tool call the model wrote. Drop the partial output and try again;
                # on the second failure switch to the JSON text format, which doesn't depend on that parser.
                bad_calls += 1
                st["acc"] = st["acc"][:acc_before]
                yield {"type": "truncate", "length": acc_before}
                yield {"type": "notice", "text": "The model wrote a malformed tool call — retrying."}
                if bad_calls == 2 and native:
                    native, text_mode = False, True
                    enable_text_mode()
                ctx.prompt.append({"role": "user", "content": BAD_CALL_NUDGE})
                continue
            raise

        tail = ""
        if stripper:
            tail = stripper.flush()
        if hider:
            tail = hider.flush() if not stripper else hider.feed(tail) + hider.flush()
        ev = emit(tail)
        if ev:
            yield ev
        turn += 1

        if text_mode and use_tools and not calls:
            calls = parse_text_calls(strip_think(raw), allowed)
        calls = [c for c in calls if c.get("name")]
        visible_now = strip_think(raw)
        rescued = False
        want = (
            "create_dashboard" if getattr(ctx, "wants_dashboard", False) and "create_dashboard" in allowed
            else "create_chart" if getattr(ctx, "wants_chart", False) and "create_chart" in allowed else None
        )
        if not calls and use_tools and ("create_chart" in allowed or "create_dashboard" in allowed):
            rescue = (rescue_dashboard_call(visible_now) if want == "create_dashboard" else None) or rescue_chart_call(visible_now)
            if rescue:
                calls, rescued = [rescue], True
                m = re.match(r"\s*<think>[\s\S]*?</think>", st["acc"][acc_before:])
                keep = acc_before + (len(m.group(0)) if m else 0)
                st["acc"] = st["acc"][:keep]  # the JSON text is replaced by the real chart
                yield {"type": "truncate", "length": keep}
        missing = bool(
            not calls and use_tools and want is not None
            and not any(e["name"] == want and e.get("ok") for e in st["tools"])
            and not MERMAID_PROCESS.search(visible_now)
        )
        no_chart = missing and not chart_nudged
        if missing and not rescued and extract_tries < 2:
            extract_tries += 1  # tried again after a nudge too: the nudged reply is often empty on small models
            extracted = await extract_chart_call(ctx, visible_now, want)
            if extracted:
                calls, rescued, no_chart = [extracted], True, False
                m = re.match(r"\s*<think>[\s\S]*?</think>", st["acc"][acc_before:])
                keep = acc_before + (len(m.group(0)) if m else 0)
                st["acc"] = st["acc"][:keep]
                yield {"type": "truncate", "length": keep}
        # the user named a connected server ("use the Echo tool…") but the model answered from its head
        mcp_missing = bool(
            not calls and use_tools and getattr(ctx, "wants_mcp", False)
            and not any(e["name"].startswith(mcp_client.PREFIX) for e in st["tools"])
        )
        narrated = bool(not calls and use_tools and (looks_unexecuted(visible_now, ctx.tool_names) or mcp_missing))
        if narrated or no_chart:
            if nudges < MAX_NUDGES:
                nudges += 1
                chart_nudged = chart_nudged or no_chart
                turn -= 1  # a nudge must not use up one of the tool turns
                m = re.match(r"\s*<think>[\s\S]*?</think>", st["acc"][acc_before:])
                keep = acc_before + (len(m.group(0)) if m else 0)
                st["acc"] = st["acc"][:keep]  # drop the narration, keep any reasoning block
                yield {"type": "truncate", "length": keep}
                yield {"type": "notice", "text": (
                    "The model described an action instead of doing it — asking it to run the tool." if narrated
                    else "The model didn't build the dashboard — asking it to use the dashboard tool." if want == "create_dashboard"
                    else "The model didn't draw the chart — asking it to use the chart tool.")}
                ctx.prompt.append({"role": "assistant", "content": visible_now})
                ctx.prompt.append({
                    "role": "user",
                    "content": NUDGE.format(web="You DO have live internet access through web_search and fetch_page. " if web_on else "")
                    if narrated else DASHBOARD_NUDGE if want == "create_dashboard" else CHART_NUDGE,
                })
                continue
            unexecuted = narrated
        if (not calls or not use_tools) and want in ("create_dashboard", "create_chart") and not MERMAID_PROCESS.search(visible_now) and not any(e["name"] == want and e.get("ok") for e in st["tools"]):
            what = "dashboard" if want == "create_dashboard" else "chart"
            why = ctx.extract_error or "the model did not give usable data"
            yield {"type": "notice", "text": f"The {what} could not be built: {why}. Send the numbers in the message (or attach a CSV/Excel file) and ask again."}
        if not calls or not use_tools:
            break

        visible = "" if rescued else strip_think(raw)
        if text_mode:
            ctx.prompt.append({"role": "assistant", "content": visible})
        else:
            ctx.prompt.append(llm.assistant_tool_message(s, visible, calls[:MAX_CALLS_PER_TURN]))

        results_text: list[str] = []
        for call in calls[:MAX_CALLS_PER_TURN]:
            if not isinstance(call["arguments"], dict):
                call["arguments"] = {}
            entry = {"id": f"{len(st['tools'])}", "name": call["name"], "args": call["arguments"], "status": "running"}
            call["id"] = call.get("id") or f"call_{entry['id']}"
            st["tools"].append(entry)
            yield {"type": "tool_start", "id": entry["id"], "name": call["name"], "args": call["arguments"]}
            async for ev in _execute(ctx, call, entry):
                yield ev
            if text_mode:
                results_text.append(f'<tool_result name="{call["name"]}">\n{entry["model_text"]}\n</tool_result>')
            else:
                ctx.prompt.append(llm.tool_result_message(s, call, entry["model_text"]))
        if text_mode:
            ctx.prompt.append({"role": "user", "content": "\n\n".join(results_text) + "\n\nContinue."})
        if turn == MAX_ITERATIONS - 1:
            yield {"type": "notice", "text": "Tool-call limit reached — answering with what was gathered."}

    for event in finalize(ctx, unexecuted):
        yield event


def finalize(ctx: Ctx, unexecuted: bool) -> list[dict]:
    """Task state + honesty checks, as events. Never marks a run 'completed' just because text came back."""
    st = ctx.state
    events: list[dict] = []
    drawn = {n for n in ("create_chart", "create_dashboard", "create_map") if any(e["name"] == n and e.get("ok") for e in st["tools"])}
    # a chart/dashboard that got drawn (even after a corrected retry) is not "work to report"; one that never got drawn is
    finished = [e for e in st["tools"] if e.get("status") in ("done", "error") and e["name"] not in drawn
                and not (e["name"] == "save_memory" and e.get("ok"))]
    failed = [e for e in finished if e.get("ok") is False]
    state = None
    if unexecuted:
        state = "unexecuted"
        events.append({"type": "notice", "text": "⚠ The model described an action but never ran it, so this answer is not backed by any lookup."})
    elif finished:
        state = "failed" if len(failed) == len(finished) else "partial" if failed else "completed"
    st["state"] = state
    if state:
        events.append({"type": "state", "state": state, "ok": len(finished) - len(failed), "failed": len(failed)})
    if {"web_search", "fetch_page"} & set(ctx.tool_names):
        bad = check_citations(st["acc"].split("</think>")[-1], len(st["sources"]))
        if bad:
            marks = ", ".join(f"[{n}]" for n in bad)
            events.append({"type": "notice", "text": f"⚠ The answer cites {marks}, which doesn't match any source that was actually retrieved."})
    return events


def meta_for_storage(state: dict) -> Optional[dict]:
    """Tools + sources saved with the assistant message (internal fields removed)."""
    done = []
    for e in state["tools"]:
        if e.get("status") in ("running", "confirm"):
            e = {**e, "ok": False, "summary": "Interrupted", "status": "error"}
        done.append({k: v for k, v in e.items() if k != "model_text"})
    if not done and not state["sources"] and not state.get("state") and not state.get("verification"):
        return None
    return {"tools": done, "sources": state["sources"], "state": state.get("state"), "verification": state.get("verification")}
