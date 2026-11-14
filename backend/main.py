"""Local Chat - FastAPI backend (Phase 2: message features + branching)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import json
import mimetypes
import re
import shutil
import time
import uuid
from contextlib import aclosing, asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, dashboards, db, embeddings, entity, examiner, gpu, importer, learning, ocr, plugins, skills, verify, vision, voice, library, llm, mcp_client, n8n, nlp, research, tools
from .agent import est_tokens, strip_think
from .skills.workflows import analyze as analyze_wf

FRONTEND_DIR = db.ROOT / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    task = asyncio.create_task(mcp_client.sync(db.get_settings()))  # connect MCP servers without delaying start-up
    yield
    task.cancel()
    await mcp_client.stop_all()


app = FastAPI(title="Local Chat", lifespan=lifespan)


class LocalOnly:
    """Pure-ASGI guard: only accept requests addressed to localhost and block
    cross-site POST/PUT/PATCH/DELETE (protects against DNS rebinding and CSRF)."""

    ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            host = headers.get("host", "")
            hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
            bad = hostname.lower() not in self.ALLOWED_HOSTS
            origin = headers.get("origin")
            if not bad and origin and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
                bad = urlparse(origin).netloc != host
            if bad:
                resp = JSONResponse({"detail": "Forbidden"}, status_code=403)
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(LocalOnly)


# ------------------------------------------------------------------ helpers
def clean_title(raw: str) -> str:
    t = strip_think(raw)
    t = t.splitlines()[0] if t else ""
    t = t.strip().strip("\"'`*# ").rstrip(".").strip()
    return t[:60]


def provisional_title(text: str) -> str:
    t = " ".join(text.split())
    return (t[:48] + "…") if len(t) > 48 else (t or "New chat")


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", name).strip()[:80] or "chat"


def chat_to_markdown(chat: dict) -> str:
    lines = [f"# {chat['title']}", ""]
    for m in chat["messages"]:
        who = "You" if m["role"] == "user" else "Assistant"
        lines += [f"**{who}:**", "", m["content"], ""]
    return "\n".join(lines)


def ev(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False) + "\n"


def err_event(code: str, message: str, hint: str = "", retry_ok: bool = False) -> str:
    return ev({"type": "error", "code": code, "message": message, "hint": hint, "retry_ok": retry_ok})


# ----------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    s = db.get_settings()
    try:  # passwords and tokens of MCP servers / plugins stay on the server; the browser only ever sees a mask
        s = dict(s, mcp_servers=json.dumps([mcp_client.mask(c) for c in mcp_client.parse_servers(s)]))
    except Exception:  # noqa: BLE001 - never block the settings screen over this
        s = dict(s, mcp_servers="[]")
    return s


@app.put("/api/settings")
def put_settings(patch: dict[str, Any]):
    return db.save_settings(patch)


# ------------------------------------------------------------------- models
@app.get("/api/models")
async def models():
    s = db.get_settings()
    base = {"provider": s["provider"], "url": llm._base(s)}
    try:
        return {**base, "ok": True, "models": await llm.list_models(s)}
    except llm.LLMError as e:
        return {**base, "ok": False, "models": [], "code": e.code, "error": e.message, "hint": e.hint}


# -------------------------------------------------------------------- chats
class ChatCreate(BaseModel):
    model: Optional[str] = None
    project_id: Optional[str] = None
    assistant_id: Optional[str] = None


class ChatPatch(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
    project_id: Optional[str] = None  # "" clears
    assistant_id: Optional[str] = None  # "" clears


@app.get("/api/chats")
def list_chats(q: str = "", archived: bool = False):
    return db.list_chats(q.strip(), archived)


@app.post("/api/chats")
def create_chat(body: ChatCreate):
    assistant = library.get_assistant(body.assistant_id)
    chat = db.create_chat(body.model or (assistant or {}).get("model") or None)
    fields: dict[str, Any] = {}
    if assistant:
        fields["assistant_id"] = assistant["id"]
    if body.project_id and library.get_project(body.project_id):
        fields["project_id"] = body.project_id
    return db.update_chat(chat["id"], **fields) if fields else chat


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    chat = db.export_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    return chat


@app.patch("/api/chats/{chat_id}")
def patch_chat(chat_id: str, body: ChatPatch):
    if not db.get_chat(chat_id):
        raise HTTPException(404, "Chat not found")
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title.strip()[:120] or "Untitled"
        fields["title_state"] = 2
    if body.model is not None:
        fields["model"] = body.model
    if body.pinned is not None:
        fields["pinned"] = int(body.pinned)
    if body.archived is not None:
        fields["archived"] = int(body.archived)
    if "assistant_id" in body.model_fields_set:
        fields["assistant_id"] = body.assistant_id if library.get_assistant(body.assistant_id) else None
    if "project_id" in body.model_fields_set:
        fields["project_id"] = body.project_id if library.get_project(body.project_id) else None
    return db.update_chat(chat_id, **fields)


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    db.delete_chat(chat_id)
    return {"ok": True}


@app.delete("/api/chats")
def delete_all():
    db.delete_all_chats()
    return {"ok": True}


# ----------------------------------------------------- branches and feedback
class LeafBody(BaseModel):
    message_id: int


@app.put("/api/chats/{chat_id}/leaf")
def select_branch(chat_id: str, body: LeafBody):
    msg = db.get_message(body.message_id)
    if not db.get_chat(chat_id) or not msg or msg["chat_id"] != chat_id:
        raise HTTPException(404, "Message not found")
    db.set_leaf(chat_id, body.message_id)
    return db.export_chat(chat_id)


class FeedbackBody(BaseModel):
    value: Optional[str] = None  # "up" | "down" | null
    note: str = ""


@app.put("/api/messages/{message_id}/feedback")
def message_feedback(message_id: int, body: FeedbackBody):
    if body.value not in (None, "up", "down"):
        raise HTTPException(400, "value must be 'up', 'down' or null")
    if not db.get_message(message_id):
        raise HTTPException(404, "Message not found")
    note = body.note.strip()[:500] if body.value else None
    db.update_message(message_id, feedback=body.value, feedback_note=note or None)
    return {"ok": True}


# -------------------------------------------------------------- send / stream
class SendBody(BaseModel):
    content: Optional[str] = None
    model: Optional[str] = None
    retry: bool = False  # re-run the last user message (no assistant reply exists yet)
    edit_id: Optional[int] = None  # user message being edited -> new sibling branch
    regenerate_id: Optional[int] = None  # assistant message -> new sibling answer
    continue_id: Optional[int] = None  # assistant message to extend in place
    think: Optional[bool] = None  # override the "thinking" setting
    web: bool = False  # allow web_search / fetch_page for this turn
    research: bool = False  # run the five-stage deep research workflow
    attachments: Optional[list[str]] = None  # uploaded file ids
    skill_run: Optional[str] = None  # continue this skill run (answers to its questions, a revision, "next lesson")
    skill_answers: Optional[dict] = None


NEEDS_WEB = re.compile(
    r"https?://|\bwww\.|\b(?:latest|today|tonight|yesterday|right now|currently|breaking|news|headline|score|weather|"
    r"stock price|exchange rate|election|this (?:week|month|year)|20(?:2[5-9]|3\d))\b",
    re.I,
)

ASKS_MAP = re.compile(r"\b(?:maps?|routes?|itinerar\w+|on a map|where (?:is|are)|locations? of|directions?)\b", re.I)
CONVERT_HINT = re.compile(r"\b(convert|conversion|pdf|docx?|xlsx?|pptx?|odt|ods|odp|libreoffice|word document|excel|powerpoint)\b", re.I)
N8N_HINT = re.compile(r"\b(n8n|automation|automate|workflow)\b", re.I)
ASKS_CHART = re.compile(
    r"\b(?:charts?|graphs?|plots?|histograms?|heat ?maps?|tree ?maps?|sankey|funnel|waterfall|candlestick|box ?plots?|"
    r"radar|scatter\w*|pie|donut|doughnut|bubble chart|visuali[sz]\w*|infographic)\b",
    re.I,
)

ASKS_ANALYSIS = re.compile(r"\b(?:analy[sz]e|analy[sz]ing|analysis|statistics|stats|trends?|insights?|compare)\b", re.I)
_NUMBER = re.compile(r"(?<![\w.])[₹$€£]?\d[\d,]*(?:\.\d+)?\s?(?:%|k|m|bn|crore|lakh|million|billion)?(?![\w])", re.I)


def stats_to_analyse(text: str) -> bool:
    """'Analyze …' with at least 4 figures typed into the message: statistics are involved, so the answer gets a chart."""
    return bool(ASKS_ANALYSIS.search(text) and len(_NUMBER.findall(text)) >= 4)


CONTINUE_PROMPT = (
    "Continue your previous response exactly where it stopped. Do not repeat anything you "
    "already wrote and do not add any preamble. Output only the continuation."
)


def resolve_attachments(ids: Optional[list[str]]) -> list[dict]:
    out = []
    for fid in ids or []:
        f = db.get_file(fid)
        if f:
            out.append(public_file(f))
    return out


@app.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: SendBody):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")

    async def gen():
        s = db.get_settings()
        model = body.model or chat["model"] or s["default_model"]
        if not model:
            yield err_event(
                "no_model",
                "No model selected.",
                "Pick a model from the dropdown at the top. If the list is empty, "
                "install one with `ollama pull llama3.2`.",
            )
            return

        path = db.get_path(chat_id)
        user_id: Optional[int] = None
        target: Optional[dict] = None  # message being continued
        mode = "send"

        def owned(mid: Optional[int], role: str) -> Optional[dict]:
            m = db.get_message(mid) if mid is not None else None
            return m if m and m["chat_id"] == chat_id and m["role"] == role else None

        if body.continue_id is not None:
            mode = "continue"
            target = owned(body.continue_id, "assistant")
            if not target or not path or path[-1]["id"] != target["id"]:
                yield err_event("bad_request", "Only the latest reply can be continued.")
                return
            history = path
        elif body.regenerate_id is not None:
            mode = "regenerate"
            old = owned(body.regenerate_id, "assistant")
            if not old or old["parent_id"] is None:
                yield err_event("bad_request", "That reply can't be regenerated.")
                return
            db.update_chat(chat_id, leaf_id=old["parent_id"])
            history = db.get_path(chat_id)
        elif body.edit_id is not None:
            mode = "edit"
            old = owned(body.edit_id, "user")
            content = (body.content or "").strip()
            if not old or not content:
                yield err_event("bad_request", "Nothing to edit.")
                return
            atts = resolve_attachments(body.attachments) if body.attachments is not None else old["attachments"]
            user_id = db.add_message(chat_id, "user", content, parent_id=old["parent_id"], attachments=atts)
            history = db.get_path(chat_id)
        elif body.retry:
            mode = "retry"
            if not path or path[-1]["role"] != "user":
                yield err_event("bad_request", "Nothing to retry.")
                return
            history = path
        else:
            content = (body.content or "").strip()
            atts = resolve_attachments(body.attachments)
            if not content and not atts:
                yield err_event("empty", "Message is empty.")
                return
            user_id = db.add_message(
                chat_id, "user", content or "(see attached files)",
                parent_id=path[-1]["id"] if path else None, attachments=atts,
            )
            history = db.get_path(chat_id)

        fields: dict[str, Any] = {"model": model}
        if mode == "send" and chat["title_state"] == 0:
            fields["title"] = provisional_title(history[-1]["content"])
            fields["title_state"] = 1
        updated = db.update_chat(chat_id, **fields) or chat
        yield ev(
            {
                "type": "start",
                "mode": mode,
                "user_id": user_id,
                "model": model,
                "title": updated["title"],
                "title_state": updated["title_state"],
            }
        )

        # ---- memory: "remember that ..." / "forget ..." are handled by the app itself, not left to the model
        last_user = history[-1]["content"] if history and history[-1]["role"] == "user" else ""
        if s.get("memory_enabled", True) and mode in ("send", "edit") and last_user:
            fact = library.detect_remember(last_user)
            if fact:
                row, status = library.add_memory(fact, "user")
                yield ev({"type": "memory", "status": status, "text": (row or {}).get("text") or fact})
            else:
                what = library.detect_forget(last_user)
                if what:
                    removed = library.forget_matching(what)
                    yield ev({"type": "memory", "status": "forgotten" if removed else "not_found", "text": what,
                              "count": len(removed)})
        memory_handled = bool(last_user and (library.detect_remember(last_user) or library.detect_forget(last_user)))
        extras = library.prompt_extras(updated, s)
        if extras.get("memories") and last_user and mode != "continue":
            extras["memories"] = await recall_memories(last_user, extras["memories"])
        wants_dashboard = bool(mode != "continue" and last_user and dashboards.ASKS_DASHBOARD.search(last_user))
        if wants_dashboard:
            # look at the whole recent conversation, so "make it a dashboard" still finds the topic
            recent_text = " ".join(m["content"] for m in history[-4:] if m["role"] == "user")
            extras["dashboard"] = dashboards.detect_template(recent_text)

        # ---- entity cards: the app decides (rules + Wikipedia/Wikidata check), not the model
        last_ent = entity.last_entity(history)
        ent_plan = None
        if (mode in ("send", "edit", "retry", "regenerate") and last_user and body.web and s.get("entity_cards", True) and not wants_dashboard
                and not memory_handled and not body.research and not (history[-1].get("attachments") if history else None)):
            ent_plan = entity.plan(last_user, last_ent)

        # ---- skills: /planning /compare /analyze /create /learn (and answers to their questions)
        skill_turn = None
        if mode in ("send", "edit", "retry", "regenerate") and last_user and not memory_handled:
            # a data file attached to THIS message + "dashboard"/"analyze" in plain words: the app computes it from the file
            table_now = any(Path(f["name"]).suffix.lower() in analyze_wf.TABLE_EXT for a in (history[-1].get("attachments") or []) for f in [db.get_file(a["id"])] if f)
            skill_turn = skills.detect(s, last_user, chat_id, body.skill_run, body.skill_answers, table_attached=table_now)
            if skill_turn:
                ent_plan, wants_dashboard = None, False
                skill_turn["files"] = [dict(f, ext=Path(f["name"]).suffix.lower()) for m in history for a in m.get("attachments") or [] for f in [db.get_file(a["id"])] if f]
                skill_turn["history"] = history

        # ---- tools available for this turn
        has_files = any(m.get("attachments") for m in history) or bool((extras.get("project") or {}).get("files"))
        # create_chart is always available (the app draws the chart); everything else follows the switches/settings
        names = [] if mode == "continue" else tools.enabled_tools(
            s, web=body.web, has_files=has_files,
            convert=bool(CONVERT_HINT.search(last_user)) or has_files, n8n_on=bool(N8N_HINT.search(last_user)),
            knowledge=bool((extras.get("project") or {}).get("files")) and embeddings.available(),
            maps=bool(ASKS_MAP.search(last_user)),
        )
        if wants_dashboard and "create_dashboard" in names and "create_chart" in names:
            names.remove("create_chart")  # offered both, a reasoning model takes the simpler chart tool and never builds the dashboard
        if "current_datetime" in names:
            names.remove("current_datetime")  # the system prompt already has the date
        if set(names) - {"create_chart", "create_dashboard", "save_memory", "run_python"} and s.get("tool_search_chat", True) and len(history) >= 3:
            names.append("search_chat")  # lets the model look things up in earlier messages
        if memory_handled and "save_memory" in names:
            names.remove("save_memory")  # the app already handled this request; the model must not save it a second time
        research_mode = bool(body.research) and mode != "continue" and bool(last_user) and not skill_turn
        if research_mode:
            names = [n for n in ("web_search", "fetch_page") if s.get(f"tool_{n}", True)]
            if len(names) < 2:
                research_mode = False
                names = tools.enabled_tools(s, web=body.web, has_files=has_files, convert=False, n8n_on=False)
                yield ev({"type": "notice", "text": "Deep research needs “Web search” and “Read web pages” enabled in Settings → Tools."})
        web_on = bool({"web_search", "fetch_page"} & set(names))
        web_state = "on" if web_on else ("disabled" if body.web else "off")
        has_images = any(a.get("kind") == "image" for m in history for a in m.get("attachments") or [])
        caps = await llm.capabilities(s, model) if (names or has_images) else {}
        vision = caps.get("vision") if caps.get("vision") is not None else s["provider"] == "openai"

        if (not web_on and history[-1]["role"] == "user" and NEEDS_WEB.search(history[-1]["content"])
                and not wants_dashboard and not ASKS_CHART.search(history[-1]["content"])):
            yield ev(
                {
                    "type": "notice",
                    "text": "Web search is off, so I can't open links or look up current information. "
                    "Turn on “Web search” in the + menu and resend."
                    if web_state == "off"
                    else "Web tools are disabled in Settings → Tools, so I can't look this up.",
                }
            )
        if "run_python" in names:
            files = [db.get_file(a["id"]) for m in history for a in m.get("attachments") or []]
            tools.sync_attachments(chat_id, [f for f in files if f])
        prompt, trimmed = agent.build_messages(s, history, names, bool(vision), web_state, extras)
        if mode == "continue":
            prompt.append({"role": "user", "content": CONTINUE_PROMPT})
        if trimmed:
            yield ev(
                {
                    "type": "notice",
                    "text": f"Older messages ({trimmed}) were left out to fit the model's context window.",
                }
            )
        think = False if mode == "continue" else (body.think if body.think is not None else s["thinking"])
        ctx = agent.Ctx(s, model, chat_id, prompt, think, names, caps)
        ctx.wants_chart = mode != "continue" and bool(ASKS_CHART.search(history[-1]["content"] or "") or stats_to_analyse(history[-1]["content"] or "")) if history[-1]["role"] == "user" else False
        lowered = last_user.lower()
        ctx.wants_mcp = bool(
            mode != "continue" and any(n.startswith(mcp_client.PREFIX) for n in names)
            and any(c["name"].lower() in lowered for c in mcp_client.parse_servers(s) if c["enabled"])
        )
        ctx.wants_dashboard = wants_dashboard
        if last_ent and not ent_plan:
            ctx.prompt[0]["content"] += entity.followup_hint(last_ent)
        ctx.dash_template = extras.get("dashboard", "general")
        if wants_dashboard:
            ctx.wants_chart = False
        st = ctx.state
        error: Optional[llm.LLMError] = None
        stopped = False
        aid: Optional[int] = None
        try:
            async def with_entity():
                async with aclosing(entity.prelude(ctx, ent_plan)) as pre:
                    async for pe in pre:
                        yield pe
                async with aclosing(agent.run_agent(ctx)) as main_events:
                    async for me in main_events:
                        yield me

            ground = bool(
                s.get("ground_first", True) and not ent_plan and not skill_turn and not research_mode and "web_search" in names and not wants_dashboard
                and mode in ("send", "edit", "retry", "regenerate") and last_user and not memory_handled
                and not (history[-1].get("attachments") if history else None) and verify.should_ground(last_user))

            async def with_ground():
                q = await verify.search_query(s, model, last_user, history)
                async with aclosing(verify.prefetch(ctx, q, pages=int(s.get("ground_pages", 1)))) as pre:
                    async for pe in pre:
                        yield pe
                async with aclosing(agent.run_agent(ctx)) as main_events:
                    async for me in main_events:
                        yield me

            async def with_skill():
                orig = list(ctx.tool_names)
                ctx.skill_fallthrough = ctx.skill_done = False
                async with aclosing(skills.engine.drive(ctx, skill_turn)) as dr:
                    async for se in dr:
                        yield se
                if ctx.skill_fallthrough:
                    ctx.tool_names = orig  # the message was not an answer after all: ordinary chat
                elif not ctx.skill_done:
                    return  # questions were asked; nothing more to write
                async with aclosing(agent.run_agent(ctx)) as main_events:
                    async for me in main_events:
                        yield me

            runner = (with_skill() if skill_turn else research.run_research(ctx, last_user) if research_mode else
                      with_entity() if ent_plan else with_ground() if ground else agent.run_agent(ctx))
            async with aclosing(runner) as events:
                async for e in events:
                    yield ev(e)
            vmode = s.get("verify_answers", "fix")
            if vmode in ("flag", "fix") and mode != "continue" and last_user and not ent_plan:
                async with aclosing(verify.postcheck(ctx, last_user, vmode)) as checks:
                    async for ce in checks:
                        yield ev(ce)
        except llm.LLMError as exc:
            error = exc
        except (asyncio.CancelledError, GeneratorExit):
            stopped = True
            raise
        finally:
            acc = st["acc"]
            meta = agent.meta_for_storage(st) if mode != "continue" else None
            if acc.strip() or meta:
                status = (
                    "stopped" if stopped else "error" if error else "length" if st["reason"] == "length" else "done"
                )
                t_in = st["in"] or sum(est_tokens(m["content"]) for m in prompt if isinstance(m.get("content"), str))
                t_out = st["out"] or est_tokens(acc)
                if target is not None:
                    aid = target["id"]
                    db.update_message(
                        aid,
                        content=target["content"] + acc,
                        status=status,
                        tokens_in=(target["tokens_in"] or 0) + t_in,
                        tokens_out=(target["tokens_out"] or 0) + t_out,
                    )
                else:
                    think_ms = None
                    if st["t_open"] is not None:
                        think_ms = int(((st["t_close"] or time.monotonic()) - st["t_open"]) * 1000)
                    aid = db.add_message(
                        chat_id,
                        "assistant",
                        acc,
                        parent_id=history[-1]["id"],
                        model=model,
                        status=status,
                        think_ms=think_ms,
                        tokens_in=t_in,
                        tokens_out=t_out,
                        meta=meta,
                    )

        if (not error and not stopped and mode != "continue" and not strip_think(st["acc"]).strip()
                and not st["tools"] and not research_mode):
            # a model that answers with nothing must not make the message vanish without a word
            yield ev({"type": "error", "code": "empty_reply", "message": "The model returned an empty reply.",
                      "hint": "Try again, or pick a different model.", "retry_ok": not aid, "id": aid})
            return
        if error:
            yield ev(
                {
                    "type": "error",
                    "code": error.code,
                    "message": error.message,
                    "hint": error.hint,
                    "retry_ok": not st["acc"].strip() and mode != "continue" and not st["tools"],
                    "id": aid,
                }
            )
            return
        if s.get("memory_auto") and s.get("memory_enabled", True) and mode != "continue" and aid and last_user and st["acc"].strip():
            asyncio.create_task(auto_memory(s, model, last_user, strip_think(st["acc"])))
        final = db.get_chat(chat_id) or chat
        message = db.get_message(aid) if aid else None
        message = message or {}
        yield ev(
            {
                "type": "done",
                "id": aid,
                "status": message.get("status"),
                "think_ms": message.get("think_ms"),
                "tokens_in": message.get("tokens_in"),
                "tokens_out": message.get("tokens_out"),
                "title_state": final["title_state"],
            }
        )

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def auto_memory(s: dict, model: str, user_text: str, reply: str) -> None:
    """Opt-in: ask the model whether the user stated a lasting fact about themselves; save what it finds."""
    known = "\n".join("- " + t for t in library.memory_lines(800)) or "(nothing yet)"
    msgs = [
        {"role": "system", "content": (
            "Decide whether the USER's message states a lasting fact about themselves worth remembering for future "
            "chats (name, job, location, preferences, tools they use, ongoing projects). Ignore one-off requests, "
            "questions, secrets and anything the assistant said. Reply with ONLY JSON: {\"facts\": [\"short sentence\"]} "
            "(at most 2, empty list if none). Already remembered:\n" + known)},
        {"role": "user", "content": "USER: " + user_text[:1200] + "\n\nASSISTANT REPLY (context only): " + reply[:400]},
    ]
    try:
        raw = await llm.complete(s, model, msgs, max_tokens=160, json_mode=True, num_ctx=3072)
        m = re.search(r"\{[\s\S]*\}", raw or "")
        facts = json.loads(m.group(0)).get("facts", []) if m else []
        for f in facts[:2] if isinstance(facts, list) else []:
            if isinstance(f, str):
                library.add_memory(f, "auto")
    except Exception:
        pass  # memory is a convenience; never let it break a chat


class ConfirmBody(BaseModel):
    approve: bool
    always: bool = False  # MCP tools only: stop asking for this tool


@app.post("/api/confirm/{cid}")
async def confirm_tool(cid: str, body: ConfirmBody):
    fut = agent.CONFIRMS.get(cid)
    if not fut or fut.done():
        raise HTTPException(404, "No pending request")
    if body.approve and body.always:
        name = agent.CONFIRM_TOOL.get(cid, "")
        if name.startswith(mcp_client.PREFIX):
            mcp_client.allow_always(db.get_settings(), name)
    fut.set_result(body.approve)
    return {"ok": True}


# ---------------------------------------------------------------- files
MAX_UPLOAD = 250 * 1024 * 1024
SAFE_INLINE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".log", ".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".html", ".htm", ".css",
    ".js", ".ts", ".tsx", ".jsx", ".py", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
    ".sh", ".bat", ".ps1", ".sql", ".r", ".swift", ".kt", ".lua", ".tex", ".rst", ".env", ".cfg",
}


def classify(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in tools.IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in (".csv", ".tsv", ".xlsx", ".xlsm", ".xls"):
        return "sheet"
    if ext in TEXT_EXT:
        return "text"
    return "file"


def public_file(f: dict) -> dict:
    return {"id": f["id"], "name": f["name"], "mime": f["mime"], "size": f["size"], "kind": f["kind"]}


def file_response(path: Path, name: str) -> FileResponse:
    """Serve user/generated files safely: only real raster images render inline; everything else
    downloads, so an uploaded .html or .svg can never run on this origin."""
    ext = path.suffix.lower()
    inline = ext in SAFE_INLINE
    return FileResponse(
        path,
        media_type=SAFE_INLINE.get(ext, "application/octet-stream"),
        filename=name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@app.post("/api/files")
async def upload_file(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\- ()]+", "_", Path(file.filename or "file").name).strip()[:120] or "file"
    fid = uuid.uuid4().hex[:12]
    folder = db.UPLOAD_DIR / fid
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(413, "File too large (250 MB max).")
            out.write(chunk)
    mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    return public_file(db.add_file(name, mime, size, classify(name), str(dest), fid))


@app.get("/api/files/{file_id}")
def get_uploaded_file(file_id: str):
    f = db.get_file(file_id)
    if not f or not Path(f["path"]).exists():
        raise HTTPException(404, "File not found")
    return file_response(Path(f["path"]), f["name"])


@app.get("/api/chats/{chat_id}/work/{rel:path}")
def get_work_file(chat_id: str, rel: str):
    if not db.get_chat(chat_id):
        raise HTTPException(404, "Chat not found")
    root = db.work_dir(chat_id).resolve()
    target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found")
    return file_response(target, target.name)


@app.post("/api/chats/{chat_id}/title")
async def generate_title(chat_id: str):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    if chat["title_state"] != 1:
        return {"title": chat["title"], "title_state": chat["title_state"]}
    msgs = db.get_path(chat_id)
    first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
    first_ai = next((strip_think(m["content"]) for m in msgs if m["role"] == "assistant"), "")
    s = db.get_settings()
    model = chat["model"] or s["default_model"]
    title = ""
    if model and first_user:
        try:
            raw = await llm.complete(
                s,
                model,
                [
                    {
                        "role": "system",
                        "content": "You write short chat titles. Reply with ONLY a title of at most "
                        "6 words that summarizes the conversation. No quotes, no punctuation at the end.",
                    },
                    {
                        "role": "user",
                        "content": f"User: {first_user[:600]}\n\nAssistant: {first_ai[:600]}\n\nTitle:",
                    },
                ],
            )
            title = clean_title(raw)
        except llm.LLMError:
            title = ""
    if not title:
        return {"title": chat["title"], "title_state": 1}
    updated = db.update_chat(chat_id, title=title, title_state=2)
    return {"title": updated["title"], "title_state": 2}  # type: ignore[index]


# ------------------------------------------------------------ export / stats
@app.get("/api/chats/{chat_id}/export")
def export_chat(chat_id: str, format: str = "md"):
    chat = db.export_chat(chat_id, tree=(format == "json"))
    if not chat:
        raise HTTPException(404, "Chat not found")
    name = safe_filename(chat["title"])
    if format == "json":
        body, mime, ext = json.dumps(chat, indent=2, ensure_ascii=False), "application/json", "json"
    else:
        body, mime, ext = chat_to_markdown(chat), "text/markdown", "md"
    return Response(
        body,
        media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'},
    )


@app.get("/api/export")
def export_everything(format: str = "json"):
    chats = db.export_all()
    if format == "md":
        # Markdown export follows each chat's selected branch.
        body = "\n\n---\n\n".join(
            chat_to_markdown({**c, "messages": db.get_path(c["id"])}) for c in chats
        )
        mime, ext = "text/markdown", "md"
    else:
        body = json.dumps(chats, indent=2, ensure_ascii=False)
        mime, ext = "application/json", "json"
    return Response(
        body,
        media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="local-chat-export.{ext}"'},
    )


@app.post("/api/import")
async def import_chats(file: UploadFile = File(...)):
    raw = await file.read(60 * 1024 * 1024 + 1)
    if len(raw) > 60 * 1024 * 1024:
        raise HTTPException(413, "That file is larger than 60 MB.")
    try:
        return importer.import_data(importer.parse(raw))
    except importer.ImportError_ as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/library/export")
def export_library():
    return Response(
        json.dumps(importer.export_library(), indent=2, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="local-chat-library.json"'},
    )


@app.get("/api/stats")
def stats():
    return db.stats()


# --------------------------------------------------------- memory / library
class MemoryBody(BaseModel):
    text: str


@app.get("/api/memories")
def get_memories():
    return library.list_memories()


@app.post("/api/memories")
def add_memory(body: MemoryBody):
    row, status = library.add_memory(body.text, "user")
    if status in ("empty", "secret", "full"):
        raise HTTPException(400, {"empty": "Write a short fact.", "secret": "Memory never stores passwords or keys.",
                                  "full": "Memory is full - delete some entries."}[status])
    return {"memory": row, "status": status}


@app.put("/api/memories/{mid}")
def edit_memory(mid: int, body: MemoryBody):
    row = library.update_memory(mid, body.text)
    if not row:
        raise HTTPException(400, "Could not save that text.")
    return row


@app.delete("/api/memories/{mid}")
def remove_memory(mid: int):
    library.delete_memory(mid)
    return {"ok": True}


@app.delete("/api/memories")
def remove_all_memories():
    library.clear_memories()
    return {"ok": True}


class AssistantBody(BaseModel):
    name: str
    description: str = ""
    prompt: str = ""
    model: Optional[str] = None


@app.get("/api/assistants")
def get_assistants():
    return library.list_assistants()


@app.post("/api/assistants")
def create_assistant(body: AssistantBody):
    row = library.save_assistant(body.model_dump())
    if not row:
        raise HTTPException(400, "Give the assistant a name.")
    return row


@app.put("/api/assistants/{aid}")
def edit_assistant(aid: str, body: AssistantBody):
    if not library.get_assistant(aid):
        raise HTTPException(404, "Assistant not found")
    row = library.save_assistant(body.model_dump(), aid)
    if not row:
        raise HTTPException(400, "Give the assistant a name.")
    return row


@app.delete("/api/assistants/{aid}")
def remove_assistant(aid: str):
    library.delete_assistant(aid)
    return {"ok": True}


class ProjectBody(BaseModel):
    name: str
    instructions: str = ""


@app.get("/api/projects")
def get_projects():
    return library.list_projects()


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = library.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@app.post("/api/projects")
def create_project(body: ProjectBody):
    row = library.save_project(body.model_dump())
    if not row:
        raise HTTPException(400, "Give the project a name.")
    return row


@app.put("/api/projects/{pid}")
def edit_project(pid: str, body: ProjectBody):
    if not library.get_project(pid):
        raise HTTPException(404, "Project not found")
    row = library.save_project(body.model_dump(), pid)
    if not row:
        raise HTTPException(400, "Give the project a name.")
    return row


@app.delete("/api/projects/{pid}")
def remove_project(pid: str):
    library.delete_project(pid)
    return {"ok": True}


class ProjectFileBody(BaseModel):
    file_id: str


@app.post("/api/projects/{pid}/files")
def attach_project_file(pid: str, body: ProjectFileBody):
    if not library.get_project(pid) or not db.get_file(body.file_id):
        raise HTTPException(404, "Project or file not found")
    library.add_project_file(pid, body.file_id)
    asyncio.create_task(index_project_file(pid, body.file_id))  # semantic index, in the background
    return library.get_project(pid)


@app.delete("/api/projects/{pid}/files/{file_id}")
def detach_project_file(pid: str, file_id: str):
    library.remove_project_file(pid, file_id)
    embeddings.remove(f"project:{pid}", file_id)
    return library.get_project(pid)


async def index_project_file(pid: str, file_id: str) -> None:
    """Chunk + embed a project file so the model can search it. Never raises: indexing is a bonus, not a requirement."""
    try:
        if not embeddings.available():
            return
        f = db.get_file(file_id)
        if not f or f.get("kind") == "image":
            return
        text = await asyncio.to_thread(tools._extract, Path(f["path"]))
        if text and text.strip():
            await asyncio.to_thread(embeddings.index_text, f"project:{pid}", file_id, text, f["name"], {"file_id": file_id})
    except Exception as exc:  # noqa: BLE001
        print(f"[embeddings] indexing {file_id} failed: {exc}")


# ------------------------------------------------------ voice, OCR, vision
MAX_MEDIA_BYTES = 60 * 1024 * 1024


async def _read_upload(file: UploadFile, limit: int = MAX_MEDIA_BYTES) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is too large (limit {limit // (1024 * 1024)} MB).")
    if not data:
        raise HTTPException(400, "The file is empty.")
    return data


def _temp_media(data: bytes, suffix: str) -> Path:
    """Write an upload to a temp file in the data folder and return its path (handle closed, so Windows can delete it)."""
    import os
    import tempfile
    fd, name = tempfile.mkstemp(suffix=suffix, dir=str(db.DATA_DIR))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return Path(name)


@app.get("/api/media/status")
def media_status():
    return {"speech": voice.available(), "ocr": ocr.available(), "speech_models": list(voice.SIZES), "vision_via": "the selected chat model, when it supports images"}


@app.post("/api/transcribe")
async def api_transcribe(file: UploadFile = File(...), language: str = Form(""), hinglish: Optional[bool] = Form(None),
                         cleanup: Optional[bool] = Form(None), size: str = Form(""), device: str = Form("auto")):
    s = db.get_settings()
    data = await _read_upload(file)
    suffix = Path(file.filename or "audio.webm").suffix[:8] or ".webm"
    tmp = _temp_media(data, suffix)
    try:
        return await asyncio.wait_for(asyncio.to_thread(
            voice.transcribe, str(tmp), language=language or s["stt_language"], hinglish=s["stt_hinglish"] if hinglish is None else hinglish,
            cleanup=s["stt_cleanup"] if cleanup is None else cleanup, size=size or s["stt_model"], prefer=device), timeout=600)
    except voice.VoiceError as exc:
        raise HTTPException(400, str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(504, "Transcription took too long.")
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/ocr")
async def api_ocr(file: UploadFile = File(...), formulas: bool = Form(False)):
    data = await _read_upload(file)
    name = (file.filename or "").lower()
    try:
        if name.endswith(".pdf") or data[:5] == b"%PDF-":
            tmp = _temp_media(data, ".pdf")
            try:
                res = await asyncio.wait_for(asyncio.to_thread(ocr.ocr_pdf, str(tmp)), timeout=600)
            finally:
                tmp.unlink(missing_ok=True)
            res["text"] = "\n\n".join(p["text"] for p in res["pages"])
        else:
            res = await asyncio.wait_for(asyncio.to_thread(ocr.ocr_image_bytes, data), timeout=300)
        res["device"] = "cpu"
        if formulas:
            res["formulas"] = ocr.find_formulas(res["text"])
        return res
    except ocr.OcrError as exc:
        raise HTTPException(400, str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(504, "OCR took too long.")


@app.post("/api/vision")
async def api_vision(file: UploadFile = File(...), task: str = Form("describe"), model: str = Form(""), question: str = Form("")):
    data = await _read_upload(file, 20 * 1024 * 1024)
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "image/png"
    if not mime.startswith("image/"):
        raise HTTPException(400, "Upload an image file.")
    try:
        return await asyncio.wait_for(vision.analyze(db.get_settings(), model, data, mime, task, question), timeout=300)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc))
    except asyncio.TimeoutError:
        raise HTTPException(504, "The vision request timed out.")


# ------------------------------------------------------ semantic search + GPU status
class SearchBody(BaseModel):
    query: str
    scopes: list[str] = ["memory"]
    limit: int = 5
    threshold: float = 0.25


@app.get("/api/embeddings/status")
def embeddings_status():
    return {"available": embeddings.available(), "model": embeddings.model_name(), "chunks": embeddings.count()}


@app.post("/api/search")
async def semantic_search(body: SearchBody):
    if not embeddings.available():
        raise HTTPException(400, "Semantic search needs the sentence-transformers package (see README).")
    if "memory" in body.scopes:
        await asyncio.to_thread(sync_memory_index)
    try:
        return await asyncio.wait_for(asyncio.to_thread(embeddings.search, body.query, body.scopes, body.limit, body.threshold), timeout=120)
    except asyncio.TimeoutError:
        raise HTTPException(504, "Search timed out.")
    except embeddings.EmbeddingsUnavailable as exc:
        raise HTTPException(400, str(exc))


def sync_memory_index() -> None:
    """Index memories that aren't in the vector store yet (and drop ones that were deleted)."""
    library.sync_index()


RECALL_MIN = 8  # with fewer memories than this they all fit in the prompt anyway


def _recall_sync(query: str, fallback: list[str]) -> list[str]:
    mems = library.list_memories()  # newest first
    if len(mems) < RECALL_MIN or not embeddings.available():
        return fallback
    sync_memory_index()
    hits = embeddings.search(query, "memory", limit=8, threshold=0.28)["results"]
    chosen, used = [], 0
    # the most relevant first, then the three newest (recent context matters even when it isn't similar), within the same budget
    for text in [h["text"] for h in hits] + [m["text"] for m in mems[:3]]:
        if text in chosen:
            continue
        used += len(text) + 3
        if used > library.PROMPT_BUDGET_CHARS:
            break
        chosen.append(text)
    return chosen or fallback


async def recall_memories(query: str, fallback: list[str]) -> list[str]:
    """Pick the memories that matter for this message instead of just the newest ones. Any problem -> the old behaviour."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_recall_sync, query, fallback), timeout=20)
    except Exception:  # noqa: BLE001
        return fallback


def sync_chat_index(limit: int = 400) -> int:
    """Index chat messages that aren't in the vector store yet (newest first, at most `limit` per call). Returns how many."""
    embeddings._init()
    with db.conn() as c:
        have = {r["source"] for r in c.execute("SELECT DISTINCT source FROM chunks WHERE scope='chats'")}
        rows = c.execute(
            "SELECT m.id, m.chat_id, m.role, m.content FROM messages m JOIN chats c ON c.id=m.chat_id "
            "WHERE m.role IN ('user','assistant') ORDER BY m.id DESC").fetchall()
        live = {str(r["id"]) for r in rows}
    for gone in have - live:
        embeddings.remove("chats", gone)
    done = 0
    for r in rows:
        if str(r["id"]) in have:
            continue
        text = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "", r["content"] or "").strip()
        if len(text) >= 20:
            embeddings.index_text("chats", str(r["id"]), text, r["chat_id"], {"chat": r["chat_id"], "role": r["role"]})
        else:  # too short to be worth a vector; remember that we looked
            embeddings.index_text("chats", str(r["id"]), "-", r["chat_id"], {"chat": r["chat_id"], "role": r["role"], "skip": True})
        done += 1
        if done >= limit:
            break
    return done


@app.get("/api/search/chats")
async def search_chats(q: str, limit: int = 8):
    """Find chats by meaning (not just by words): best matching message per chat."""
    q = q.strip()
    if len(q) < 3 or not embeddings.available():
        return {"results": [], "available": embeddings.available()}
    try:
        await asyncio.wait_for(asyncio.to_thread(sync_chat_index, 300), timeout=90)
        res = await asyncio.wait_for(asyncio.to_thread(embeddings.search, q, "chats", 40, 0.35), timeout=60)
    except Exception:  # noqa: BLE001
        return {"results": [], "available": True}
    best: dict[str, dict] = {}
    for h in res["results"]:
        if h["meta"].get("skip"):
            continue
        cid = h["meta"].get("chat")
        if cid and cid not in best:
            best[cid] = h
    out = []
    for cid, h in list(best.items())[: max(1, min(limit, 20))]:
        chat = db.get_chat(cid)
        if chat and not chat["archived"]:
            out.append({"chat_id": cid, "title": chat["title"], "message_id": int(h["source"]), "role": h["meta"].get("role"),
                        "snippet": h["text"][:220], "score": h["score"]})
    return {"results": out, "available": True}


# ------------------------------------------------------ skills
@app.get("/api/skills")
def skills_info():
    return {"enabled": db.get_settings().get("skills_enabled", True), "commands": skills.SUGGESTIONS}


def _run_or_404(rid: str) -> dict:
    run = skills.runs.get(rid)
    if not run:
        raise HTTPException(404, "Skill run not found")
    return run


@app.get("/api/skills/runs/{rid}")
def skill_run(rid: str):
    return _run_or_404(rid)


class SkillStateBody(BaseModel):
    key: str
    value: object = None


@app.put("/api/skills/runs/{rid}/state")
def skill_state(rid: str, body: SkillStateBody):
    """Progress the browser keeps for a run: ticked study sessions, checklist items, quiz results."""
    _run_or_404(rid)
    if not re.fullmatch(r"[\w.\-]{1,60}", body.key):
        raise HTTPException(400, "Bad key")
    if len(json.dumps(body.value, default=str)) > 20000:
        raise HTTPException(413, "Too much data")
    return skills.runs.set_state(rid, body.key, body.value)["state"]


class SkillFeedbackBody(BaseModel):
    rating: Optional[str] = None  # up | down
    text: str = ""


@app.post("/api/skills/runs/{rid}/feedback")
async def skill_feedback(rid: str, body: SkillFeedbackBody):
    """Feedback on a result -> saved preferences (reviewable under Library). It does not retrain the model."""
    run = _run_or_404(rid)
    s = db.get_settings()
    chat = db.get_chat(run["chat_id"]) or {}
    skills.runs.set_state(rid, "feedback", {"rating": body.rating, "text": body.text[:500]})
    text = body.text.strip() or ("The result was helpful." if body.rating == "up" else "")
    saved: list[dict] = []
    if body.text.strip():
        probe = skills.engine.RunCtx(SimpleNamespace(s=s, model=chat.get("model") or s.get("default_model") or "", chat_id=run["chat_id"], state={"tools": []}, tool_names=[]),
                                     run, skills.registry.general_of(run["skill"]), {}, [], asyncio.Queue())
        saved = await skills.feedback.learn(probe, run["skill"], body.text)
    return {"saved": saved, "preferences": skills.feedback.list_prefs()}


@app.get("/api/skills/prefs")
def skill_prefs():
    return skills.feedback.list_prefs()


class PrefBody(BaseModel):
    value: str


@app.put("/api/skills/prefs/{pid}")
def skill_pref_update(pid: int, body: PrefBody):
    row = skills.feedback.update_pref(pid, body.value)
    if not row:
        raise HTTPException(404, "Preference not found")
    return row


@app.delete("/api/skills/prefs/{pid}")
def skill_pref_delete(pid: int):
    skills.feedback.delete_pref(pid)
    return {"ok": True}


# ------------------------------------------------------------------ learning hub: courses, flashcards, progress, labs
@app.get("/api/learn/overview")
def learn_overview():
    return learning.overview()


@app.get("/api/learn/review")
def learn_review(course: str = "", limit: int = 30):
    cards = learning.due_cards(course or None, max(1, min(limit, 100)))
    return {"cards": cards, "counts": learning.card_counts(course or None), "stats": learning.card_stats(course or None)}


@app.get("/api/learn/cards/stats")
def learn_card_stats(course: str = ""):
    return learning.card_stats(course or None)


class GradeBody(BaseModel):
    grade: str


@app.post("/api/learn/cards/{cid}/grade")
def learn_grade(cid: int, body: GradeBody):
    r = learning.grade_card(cid, body.grade)
    if not r:
        raise HTTPException(404, "Card not found or unknown grade")
    return r


class UndoBody(BaseModel):
    prev: dict
    event: int = 0


@app.post("/api/learn/cards/{cid}/undo")
def learn_undo(cid: int, body: UndoBody):
    if not learning.undo_grade(cid, body.prev, body.event):
        raise HTTPException(400, "Could not undo that review")
    return {"ok": True}


class EditBody(BaseModel):
    front: str
    back: str


@app.put("/api/learn/cards/{cid}")
def learn_card_edit(cid: int, body: EditBody):
    if not learning.update_card(cid, body.front, body.back):
        raise HTTPException(400, "A card needs a question of at least 4 characters and an answer")
    return {"ok": True}


class SuspendBody(BaseModel):
    suspended: bool = True


@app.post("/api/learn/cards/{cid}/suspend")
def learn_card_suspend(cid: int, body: SuspendBody):
    if not learning.set_suspended(cid, body.suspended):
        raise HTTPException(404, "Card not found")
    return {"ok": True}


@app.delete("/api/learn/cards/{cid}")
def learn_card_delete(cid: int):
    learning.delete_card(cid)
    return {"ok": True}


@app.get("/api/learn/courses/{rid}/cards")
def learn_cards(rid: str):
    return {"cards": learning.list_cards(rid)}


class CardBody(BaseModel):
    front: str
    back: str
    module: str = ""


@app.post("/api/learn/courses/{rid}/cards")
def learn_card_add(rid: str, body: CardBody):
    if not learning.get_course(rid):
        raise HTTPException(404, "Course not found")
    n = learning.add_cards(rid, body.module, [{"front": body.front, "back": body.back}], source="manual")
    return {"added": n}


@app.get("/api/learn/courses/{rid}/past-quiz")
def learn_past_quiz(rid: str, n: int = 8):
    return learning.past_quiz(rid, max(3, min(n, 20)))


class EventBody(BaseModel):
    course_id: Optional[str] = None
    kind: str
    module: str = ""
    right: int = 0
    total: int = 0
    detail: Optional[dict] = None


@app.post("/api/learn/events")
def learn_event(body: EventBody):
    if body.kind not in ("quiz", "lab", "math", "lesson", "review"):
        raise HTTPException(400, "Unknown event kind")
    learning.log_event(body.course_id, body.kind, body.module, body.right, body.total, body.detail)
    return {"ok": True}


@app.get("/api/learn/courses/{rid}/tests")
def learn_tests(rid: str):
    return learning.module_tests(rid)


@app.post("/api/learn/courses/{rid}/modules/{idx}/plan")
async def learn_plan(rid: str, idx: int):
    return await examiner.plan_module(db.get_settings(), rid, idx)


@app.get("/api/learn/courses/{rid}/modules/{idx}/quiz")
def learn_module_quiz(rid: str, idx: int):
    return learning.module_quiz(rid, idx)


class ExamQBody(BaseModel):
    course_id: Optional[str] = None
    module: Optional[int] = None
    topic: str = ""
    kind: str = "concept"
    avoid: list[str] = []


@app.post("/api/learn/exam/question")
async def exam_question(body: ExamQBody):
    return await examiner.make_question(db.get_settings(), body.course_id, body.module, body.topic[:200], body.kind, body.avoid[-8:])


class ExamGradeBody(BaseModel):
    course_id: Optional[str] = None
    module: Optional[int] = None
    topic: str = ""
    kind: str = "concept"
    question: str
    answer: str
    key_points: list[str] = []
    history: list[dict] = []
    sources: str = "auto"


@app.post("/api/learn/exam/grade")
async def exam_grade(body: ExamGradeBody):
    return await examiner.grade(db.get_settings(), body.model_dump())


class ExamFinishBody(BaseModel):
    course_id: Optional[str] = None
    module_title: str = ""
    topic: str = ""
    kind: str = "concept"
    rounds: list[dict] = []


@app.post("/api/learn/exam/finish")
def exam_finish(body: ExamFinishBody):
    return examiner.finish(body.course_id, body.module_title[:120], body.topic[:200], body.kind, body.rounds[:8])


@app.get("/api/learn/practice/math")
def learn_math(topic: str = "arithmetic", level: int = 1, n: int = 6):
    return {"topic": topic, "level": level, "topics": list(learning.TOPICS), "problems": learning.math_problems(topic, level, max(1, min(n, 20)))}


class CheckBody(BaseModel):
    kind: str = "number"
    expected: str
    given: str
    tol: float = 0.0


@app.post("/api/learn/practice/check")
def learn_check(body: CheckBody):
    return learning.check_answer(body.kind, body.expected, body.given, body.tol)


class LabBody(BaseModel):
    code: str
    tests: Optional[list[str]] = None


@app.post("/api/lab/run")
async def lab_run(body: LabBody):
    return await learning.run_python(body.code, body.tests)


@app.get("/api/gpu")
def gpu_status():
    return gpu.status()


@app.post("/api/gpu/unload")
def gpu_unload():
    """Free the models this app loaded. Never touches Ollama / LM Studio."""
    return {"unloaded": gpu.unload()}


# ------------------------------------------------------ connections: MCP + n8n
def connections_state(s: dict) -> dict:
    return {
        "servers": mcp_client.state(s),
        "libreoffice": bool(tools.find_soffice()),
        "n8n": {
            "configured": n8n.configured(s),
            "templates": [
                {"id": k, "title": v["title"], "description": v["description"], "fields": v["fields"], "needs": v["needs"]}
                for k, v in n8n.TEMPLATES.items()
            ],
        },
    }


class McpBody(BaseModel):
    servers: list[dict]


@app.get("/api/connections")
def get_connections():
    return connections_state(db.get_settings())


@app.put("/api/connections/servers")
async def save_servers(body: McpBody):
    s = db.get_settings()
    old = mcp_client.parse_servers(s)
    new = mcp_client.parse_servers({"mcp_servers": json.dumps(mcp_client.merge_secrets(body.servers, old))})
    for cfg in new:
        if cfg["enabled"] and cfg["transport"] == "stdio" and not cfg["command"]:
            raise HTTPException(400, f"“{cfg['name']}” needs a command.")
        if cfg["enabled"] and cfg["transport"] != "stdio" and not cfg["url"].startswith(("http://", "https://")):
            raise HTTPException(400, f"“{cfg['name']}” needs a URL that starts with http:// or https://.")
    s = db.save_settings({"mcp_servers": json.dumps(new)})
    await mcp_client.sync(s)
    return connections_state(s)


class ToggleBody(BaseModel):
    enabled: bool


@app.post("/api/connections/servers/{name}/enabled")
async def toggle_server(name: str, body: ToggleBody):
    s = db.get_settings()
    servers = mcp_client.parse_servers(s)
    hit = [c for c in servers if c["name"] == name]
    if not hit:
        raise HTTPException(404, "Server not found")
    hit[0]["enabled"] = body.enabled
    s = db.save_settings({"mcp_servers": json.dumps(servers)})
    await mcp_client.sync(s)
    return connections_state(s)


@app.post("/api/connections/servers/{name}/restart")
async def restart_server(name: str):
    s = db.get_settings()
    await mcp_client.restart(s, name)
    return connections_state(s)


class AllowBody(BaseModel):
    tool: str
    allow: bool


@app.post("/api/connections/servers/{name}/allow")
async def allow_tool(name: str, body: AllowBody):
    s = db.get_settings()
    servers = mcp_client.parse_servers(s)
    hit = [c for c in servers if c["name"] == name]
    if not hit:
        raise HTTPException(404, "Server not found")
    cur = set(hit[0]["always_allow"])
    (cur.add if body.allow else cur.discard)(body.tool)
    hit[0]["always_allow"] = sorted(cur)
    s = db.save_settings({"mcp_servers": json.dumps(servers)})
    conn = mcp_client.CONNS.get(name)
    if conn:
        conn.cfg["always_allow"] = hit[0]["always_allow"]
    return connections_state(s)


class PluginBody(BaseModel):
    values: dict[str, str] = {}


def _plugin_payload(s: dict, **extra) -> dict:
    return {**plugins.state(s), "servers": mcp_client.state(s), **extra}


@app.get("/api/plugins")
def get_plugins():
    return _plugin_payload(db.get_settings())


@app.get("/api/plugins/activity")
def plugin_activity(limit: int = 40):
    return {"activity": db.plugin_activity(None, max(1, min(limit, 200)))}


@app.post("/api/plugins/{pid}/install")
async def install_plugin(pid: str, body: PluginBody):
    s = db.get_settings()
    try:
        servers = plugins.install(s, pid, body.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    cfg = next(c for c in servers if c["name"] == plugins.CATALOG[pid]["name"])
    ok, message = await asyncio.to_thread(plugins.check, pid, cfg["env"])  # a wrong password is reported on the form, not later inside a chat
    if not ok:
        raise HTTPException(400, message)
    if pid == "telegram":
        m = re.search(r"@(\w+)", message)
        cfg["env"]["TG_BOT"] = m.group(1) if m else ""  # shown in the pairing instructions
    s = db.save_settings({"mcp_servers": json.dumps(servers)})
    await mcp_client.sync(s)
    return _plugin_payload(s, message=message)


@app.post("/api/plugins/{pid}/test")
async def test_plugin(pid: str):
    """Re-test a plugin with the details already saved (nothing is sent back to the browser)."""
    s = db.get_settings()
    cfg = next((c for c in mcp_client.parse_servers(s) if pid in plugins.CATALOG and c["name"] == plugins.CATALOG[pid]["name"]), None)
    if not cfg:
        raise HTTPException(404, "Not connected yet.")
    ok, message = await asyncio.to_thread(plugins.check, pid, cfg["env"])
    return {"ok": ok, "message": message}


@app.post("/api/plugins/telegram/pair-code")
def telegram_pair_code():
    """A one-time code to pair a Telegram chat with the bot (send '/pair CODE' to the bot from that chat)."""
    if not any(c["name"] == "Telegram" for c in mcp_client.parse_servers(db.get_settings())):
        raise HTTPException(400, "Connect your bot token first.")
    return plugins.new_pair_code()


@app.delete("/api/plugins/telegram/paired/{chat_id}")
def telegram_unpair(chat_id: str):
    if not plugins.tg_unpair(chat_id):
        raise HTTPException(404, "That chat is not paired.")
    return {"ok": True}


@app.delete("/api/plugins/{pid}")
async def remove_plugin(pid: str):
    s = db.get_settings()
    try:
        servers = plugins.uninstall(s, pid)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    s = db.save_settings({"mcp_servers": json.dumps(servers)})
    await mcp_client.sync(s)
    return _plugin_payload(s)


@app.post("/api/n8n/test")
async def n8n_test():
    try:
        return await n8n.ping(db.get_settings())
    except n8n.N8nError as exc:
        raise HTTPException(400, str(exc))


class N8nCreateBody(BaseModel):
    name: str = ""
    allow_send: bool = False
    token: bool = False


@app.post("/api/n8n/templates/{template}")
async def n8n_create(template: str, body: N8nCreateBody):
    s = db.get_settings()
    try:
        wf, path = n8n.build(template, body.name, {"allow_send": body.allow_send}, body.token)
        made = await n8n.create_workflow(s, wf)
    except n8n.N8nError as exc:
        raise HTTPException(400, str(exc))
    return {"id": made.get("id"), "name": wf["name"], "url": f"{n8n.base_url(s)}/workflow/{made.get('id', '')}",
            "mcp_url": n8n.mcp_url_guess(s, path), "needs": n8n.TEMPLATES[template]["needs"]}


# ------------------------------------------------------------------ NLP toolkit
class NlpBody(BaseModel):
    text: str
    other: str = ""
    labels: list[str] = []
    limit: int = 10
    sentences: int = 3


@app.get("/api/nlp")
def nlp_operations():
    return {"operations": sorted(nlp.OPERATIONS)}


@app.post("/api/nlp/{operation}")
async def nlp_run(operation: str, body: NlpBody):
    fn = nlp.OPERATIONS.get(operation)
    if not fn:
        raise HTTPException(404, f"Unknown operation. Use one of: {', '.join(sorted(nlp.OPERATIONS))}")
    kwargs = {"similarity": {"other": body.other}, "classify": {"labels": body.labels},
              "keywords": {"limit": body.limit}, "summarize": {"sentences": body.sentences}}.get(operation, {})
    try:  # CPU work: keep it off the event loop, and bound it
        return await asyncio.wait_for(asyncio.to_thread(fn, body.text, **kwargs), timeout=60)
    except asyncio.TimeoutError:
        raise HTTPException(504, "The NLP operation timed out.")


# ------------------------------------------------------------------ frontend
class FreshStatic(StaticFiles):
    """Pages and scripts are revalidated on every load (cheap 304 via ETag), so a code change never leaves a stale cached copy in the browser."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", FreshStatic(directory=FRONTEND_DIR, html=True), name="frontend")
