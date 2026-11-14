"""Skill execution. One entry point, drive(): decide the workflow, collect missing facts (asking only what is needed), run the workflow
with the existing tools, and hand the result to the browser as one `skill` tool result. The local model only interprets, extracts
and writes text; calculations, schedules and file creation are deterministic code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
from contextlib import aclosing
from pathlib import Path
from typing import Any, Optional

from .. import agent, db, llm
from . import context as runs
from . import feedback, questions, registry, router
from .parser import SUGGESTIONS

log = logging.getLogger("skills")
_SENTINEL = object()
WORKFLOW_TIMEOUT = 900  # seconds: a whole workflow, several searches + model calls


def parse_json(raw: str) -> Optional[Any]:
    """The first JSON object/array in a model reply, tolerating code fences and trailing commas. None if there isn't one."""
    s = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, s)
        if m:
            txt = m.group(0)
            for attempt in (txt, re.sub(r",\s*([}\]])", r"\1", txt)):
                try:
                    return json.loads(attempt)
                except ValueError:
                    continue
    return None


class RunCtx:
    """What a workflow gets: the app's own tools (with the same visible tool events), validated model calls, and file output."""

    def __init__(self, ctx, run: dict, wf: registry.Workflow, prefs: dict, files: list[dict], queue: asyncio.Queue, history: Optional[list] = None):
        self.ctx, self.run, self.wf, self.prefs, self.files, self.q, self.history = ctx, run, wf, prefs, files, queue, history or []
        self.s, self.model, self.chat_id = ctx.s, ctx.model, ctx.chat_id
        self.slots: dict = dict(run["slots"])
        self.evidence: list[str] = []
        self.notes: list[str] = []  # honest caveats shown to the user (assumptions, failed lookups)
        self.facts: list[str] = []
        self.action: Optional[str] = None  # 'rerun' (revise) | 'next' (learn: next lesson) | None

    # ---- events
    async def emit(self, ev: dict) -> None:
        await self.q.put(ev)

    async def progress(self, i: int, n: int, label: str) -> None:
        await self.emit({"type": "skill_progress", "step": i, "total": n, "label": label})

    # ---- model calls (bounded, validated, never raise)
    async def json_call(self, prompt: str, max_tokens: int = 600, system: str = "") -> Optional[Any]:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        for attempt in (1, 2):
            try:
                raw = await llm.complete(self.s, self.model, msgs, max_tokens=max_tokens, json_mode=True, num_ctx=int(self.s["num_ctx"]),
                                         timeout=max(120.0, max_tokens * 0.4))  # a local 9B model writes ~10-25 tokens/s: long outputs need minutes
            except llm.LLMError as exc:
                log.warning("model call failed: %s", exc)
                return None
            data = parse_json(raw)
            if data is not None:
                return data
            msgs = msgs + [{"role": "assistant", "content": raw[:300]}, {"role": "user", "content": "That was not valid JSON. Reply with the JSON only."}]
        return None

    async def text_call(self, prompt: str, max_tokens: int = 900, system: str = "") -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        try:
            return (await llm.complete(self.s, self.model, msgs, max_tokens=max_tokens, num_ctx=int(self.s["num_ctx"]),
                                       timeout=max(120.0, max_tokens * 0.4))).strip()
        except llm.LLMError as exc:
            log.warning("model call failed: %s", exc)
            return ""

    # ---- tools, through the same executor the chat uses (visible tool rows, numbered sources, approvals)
    async def _tool(self, name: str, args: dict):
        if name not in self.ctx.tool_names:
            return None
        st = self.ctx.state
        entry = {"id": f"{len(st['tools'])}", "name": name, "args": args, "status": "running", "auto": True}
        st["tools"].append(entry)
        await self.emit({"type": "tool_start", "id": entry["id"], "name": name, "args": args})
        async for ev in agent._execute(self.ctx, {"name": name, "arguments": args}, entry):
            await self.emit(ev)
        return entry if entry.get("ok") else None

    async def search(self, query: str) -> list[dict]:
        e = await self._tool("web_search", {"query": query[:200]})
        if not e:
            self.notes.append(f"The web search for “{query[:60]}” failed or is switched off.")
            return []
        return list((e.get("detail") or {}).get("results") or [])

    async def fetch(self, url: str, query: str = "") -> Optional[dict]:
        e = await self._tool("fetch_page", {"url": url, "query": query[:200]})
        if not e:
            return None
        text = e.get("model_text") or ""
        self.evidence.append(text)
        d = e.get("detail") or {}
        return {"text": text, "title": d.get("title") or "", "url": d.get("url") or url}

    async def research(self, query: str, pages: int = 2, focus: str = "") -> list[dict]:
        """Search, then read the best pages. -> [{url,title,text,snippet}] (snippets are kept when a page can't be read)."""
        from .. import verify
        results = await self.search(query)
        out = []
        for r in [x for x in results if not any(b in x["url"] for b in verify._BAD_HOSTS)][: pages + 1]:
            if len([o for o in out if o.get("text")]) >= pages:
                break
            page = await self.fetch(r["url"], focus or query)
            out.append({"url": r["url"], "title": r.get("title", ""), "snippet": r.get("snippet", ""), "text": (page or {}).get("text", "")})
        for r in results:
            self.evidence.append(f'{r.get("title", "")} {r.get("snippet", "")}')
        return out

    # ---- files
    def out_file(self, name: str, data: bytes | str) -> dict:
        name = re.sub(r"[^\w.\- ]+", "_", name)[:80] or "output.txt"
        path = db.work_dir(self.chat_id) / name
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8", newline="")
        else:
            path.write_bytes(data)
        return {"name": name, "url": f"/api/chats/{self.chat_id}/work/{name}", "mime": mimetypes.guess_type(name)[0] or "application/octet-stream"}

    def pref(self, key: str, default: Any = None) -> Any:
        return self.prefs.get(key, default)


def slot_defs(wf: registry.Workflow, slots: dict) -> list[dict]:
    return [{"name": s.name, "label": s.label, "kind": s.kind, "choices": s.choices, "value": slots.get(s.name), "required": s.required}
            for s in wf.slots if s.kind != "file"]


def _display(wf: registry.Workflow, slots: dict) -> list[dict]:
    out = []
    for s in wf.slots:
        v = slots.get(s.name)
        if v in (None, "", []):
            continue
        if isinstance(v, dict) and "start" in v:
            v = v["start"] + (f" → {v['end']}" if v.get("end") and v["end"] != v["start"] else "")
        elif isinstance(v, dict):
            v = v.get("name", "file")
        out.append({"label": s.label, "value": ", ".join(v) if isinstance(v, list) else (f"{v:g}" if isinstance(v, float) else str(v))})
    return out


NARRATIVE = (
    "\n\nSKILL RESULT (the app already did this work and the user is looking at the full interactive result right above your reply: {what}). "
    "Write ONE short reply of {size}. Use only the FACTS below, never add numbers, names or prices of your own. Do not repeat tables or lists. "
    "State the key takeaways, any assumptions, and what the user should verify (prices and times found on web pages can be out of date). "
    "Never write code, HTML, or a new dashboard or chart yourself: the app has already built it. {style}Do not ask questions.\n\nFACTS:\n{facts}")


async def drive(ctx, turn: dict):
    """Async generator of UI events for one skill turn.
    turn: {skill, request, text, run (existing run dict or None), answers (dict), action (str|None), files (list), history}"""
    st = ctx.state
    skill = turn["skill"]
    queue: asyncio.Queue = asyncio.Queue()
    prefs = feedback.prefs_for(skill)

    run = turn.get("run")
    if not run and not turn["request"].strip():
        sug = next((x for x in SUGGESTIONS if x["command"] == "/" + skill), {})
        entry = {"id": f"{len(st['tools'])}", "name": "skill", "args": {"skill": skill}, "status": "running"}
        st["tools"].append(entry)
        detail = {"run_id": "", "skill": skill, "workflow": "", "title": skill.title(), "description": sug.get("hint", ""), "status": "asking", "slots": [],
                  "questions": [{"slot": "_request", "label": "Request", "question": f"What would you like to {skill}?", "kind": "text", "choices": [],
                                 "hint": "", "examples": sug.get("examples", [])}], "slot_defs": []}
        entry.update(status="done", ok=True, summary=f"/{skill}", detail=detail, files=[])
        yield {"type": "tool_start", "id": entry["id"], "name": "skill", "args": entry["args"]}
        yield {"type": "tool_result", "id": entry["id"], "ok": True, "summary": entry["summary"], "detail": detail, "files": [], "declined": False}
        yield {"type": "delta", "text": f"**/{skill}** — {sug.get('hint', '')}. Tell me what you need, for example: “{(sug.get('examples') or [''])[0]}”."}
        ctx.skill_done = False
        return
    probe = RunCtx(ctx, run or {"slots": {}}, registry.general_of(skill), prefs, turn.get("files", []), queue, turn.get("history"))  # model access for routing/extraction
    how = "continued"
    if run:
        wf = registry.get(run["skill"], run["workflow"]) or registry.general_of(skill)
    else:
        wf, how = await router.route(probe, skill, turn["request"])
    assert wf is not None
    slots = dict(run["slots"]) if run else {}
    probe.wf = wf

    # 1) what do we already know?  (request text, answers, uploaded files, preferences, then one model extraction)
    text = turn.get("text") or ""
    before = dict(slots)
    if turn.get("answers"):
        slots = questions.merge(wf, slots, {k: v for k, v in turn["answers"].items() if not k.startswith("_")})
    if wf.prefill and text:
        slots = questions.merge(wf, slots, wf.prefill(text, {"files": turn.get("files", []), "slots": slots, "today": None}))
    elif wf.prefill and not run:
        slots = questions.merge(wf, slots, wf.prefill(turn["request"], {"files": turn.get("files", []), "slots": slots, "today": None}))
    for s in wf.slots:
        if slots.get(s.name) in (None, "", []) and s.pref and prefs.get(s.pref):
            c = questions.coerce(s, prefs[s.pref])
            if c is not None:
                slots[s.name] = c
    if text and not turn.get("answers") and questions.missing(wf, slots):
        got = await questions.llm_extract(probe, wf, text, slots)
        slots = questions.merge(wf, slots, got)
    if turn.get("implicit") and not turn.get("answers") and slots == before:
        runs.update(run["id"], status="abandoned")  # the user moved on to something else: this is an ordinary chat message
        ctx.skill_fallthrough = True
        return
    if not run:
        run = runs.create(ctx.chat_id, skill, wf.id, turn["request"], slots)
    runs.update(run["id"], slots=slots)

    entry = {"id": f"{len(st['tools'])}", "name": "skill", "args": {"skill": skill, "workflow": wf.id, "title": wf.title}, "status": "running"}
    st["tools"].append(entry)
    yield {"type": "tool_start", "id": entry["id"], "name": "skill", "args": entry["args"]}
    base = {"run_id": run["id"], "skill": skill, "workflow": wf.id, "title": wf.title, "description": wf.description, "route": how}

    need = questions.missing(wf, slots)
    if need:
        qs = questions.build_questions(wf, slots, 3)
        detail = {**base, "status": "asking", "slots": _display(wf, slots), "questions": qs, "slot_defs": slot_defs(wf, slots)}
        runs.update(run["id"], status="asking", slots=slots)
        entry.update(status="done", ok=True, summary=f"{wf.title}: a few questions", detail=detail, files=[])
        yield {"type": "tool_result", "id": entry["id"], "ok": True, "summary": entry["summary"], "detail": detail, "files": [], "declined": False}
        known = _display(wf, slots)
        yield {"type": "delta", "text": f"This looks like **{wf.title.lower()}**." + (" I already have: " + "; ".join(f"{k['label'].lower()} {k['value']}" for k in known[:5]) + "." if known else "")
               + " To do it properly I need " + ("one detail" if len(qs) == 1 else f"{len(qs)} quick details") + " from you — answer in the card below (or just reply in your own words)."}
        ctx.skill_done = False
        return

    slots = questions.apply_defaults(wf, slots)
    runs.update(run["id"], status="running", slots=slots)
    run = runs.get(run["id"])
    rc = RunCtx(ctx, run, wf, prefs, turn.get("files", []), queue, turn.get("history"))
    rc.slots = slots
    rc.action = turn.get("action")
    rc.ctx.tool_names = [n for n in ("web_search", "fetch_page") if ctx.s.get(f"tool_{n}", True)] if wf.uses_web else []

    async def work():
        try:
            return await asyncio.wait_for(wf.run(rc), WORKFLOW_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - a workflow bug or tool failure must not take the chat down
            log.exception("skill %s/%s failed", skill, wf.id)
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        finally:
            await queue.put(_SENTINEL)

    task = asyncio.create_task(work())
    try:
        while True:
            ev = await queue.get()
            if ev is _SENTINEL:
                break
            yield ev
        result = task.result()
    except (asyncio.CancelledError, GeneratorExit):
        task.cancel()
        runs.update(run["id"], status="abandoned")
        raise
    ctx.tool_names = []  # the narrative reply below needs no tools

    if not result or result.get("error") or not result.get("blocks"):
        msg = (result or {}).get("error") or "The workflow produced no result."
        runs.update(run["id"], status="error")
        detail = {**base, "status": "error", "error": msg, "slots": _display(wf, slots), "slot_defs": slot_defs(wf, slots)}
        entry.update(status="error", ok=False, summary=f"{wf.title} failed", detail=detail, files=[])
        yield {"type": "tool_result", "id": entry["id"], "ok": False, "summary": entry["summary"], "detail": detail, "files": [], "declined": False}
        yield {"type": "delta", "text": f"I couldn't finish **{wf.title.lower()}**: {msg}. Your answers are saved: use Revise on the card to try again."}
        return

    state = run["state"]
    if result.get("state"):
        state.update(result["state"])
    runs.update(run["id"], status="done", state=state)
    detail = {**base, "status": "done", "slots": _display(wf, slots), "slot_defs": slot_defs(wf, slots), "blocks": result["blocks"],
              "files": result.get("files", []), "notes": rc.notes + result.get("notes", []), "state": state, "feedback": None}
    entry.update(status="done", ok=True, summary=result.get("summary") or wf.title, detail=detail, files=result.get("files", []))
    yield {"type": "tool_result", "id": entry["id"], "ok": True, "summary": entry["summary"], "detail": detail, "files": result.get("files", []), "declined": False}
    if rc.evidence:  # lets the answer checker compare the narrative with what was actually found (kept in memory only)
        st["evidence"] = (chr(10) * 2).join(rc.evidence)[:60000]
    d = prefs.get("style.detail")
    style = ("Keep it very short (3 sentences). " if d == "brief" else "Be thorough: a few short paragraphs. " if d == "detailed" else "")
    ctx.prompt[0]["content"] += NARRATIVE.format(what=result.get("what", wf.title), size=result.get("size", "4-7 sentences"), style=style,
                                                 facts="\n".join(result.get("facts", rc.facts))[:6000])
    if turn.get("auto"):  # a plain-words request ("make a dashboard of this") must not read as still unanswered
        for m in reversed(ctx.prompt):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                m["content"] += ("\n\n(The app has already done this from the attached file; the result is shown above. "
                                 "Reply only with the short summary the system message describes. Write no code.)")
                break
    ctx.skill_done = True
