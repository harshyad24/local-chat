"""Deep research: a fixed five-stage workflow that the APP runs, so a small local model only has to do small jobs.

 1. Research planning   - break the question into sub-questions with search queries
 2. Source discovery    - search the web for every sub-question
 3. Investigation       - read the best pages, take notes per sub-question, then check the gaps once
 4. Synthesis           - organise the notes, find patterns, weigh opportunities
 5. Final report        - a cited report with evidence, conclusions and recommendations

Each stage is a short, bounded model call (or tool call); nothing relies on the model deciding to keep going.
"""
from __future__ import annotations

import json
import re
from contextlib import aclosing
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from . import agent, llm

STAGES = ["Research planning", "Source discovery", "Investigation", "Synthesis", "Final report"]
MAX_QUESTIONS = 5
MAX_QUERIES = 2
MAX_PAGES = 8


def _json_object(raw: str) -> Optional[dict]:
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


async def _ask(ctx, system: str, user: str, max_tokens: int, json_mode: bool = False) -> str:
    try:
        return await llm.complete(
            ctx.s, ctx.model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, json_mode=json_mode, num_ctx=min(int(ctx.s["num_ctx"]), 8192),
        )
    except llm.LLMError:
        return ""


def plan_questions(obj: Optional[dict], question: str) -> list[dict]:
    """Validate the model's plan; always returns at least one usable sub-question."""
    out: list[dict] = []
    for item in (obj or {}).get("questions", []) if isinstance((obj or {}).get("questions"), list) else []:
        if isinstance(item, str):
            item = {"q": item, "queries": [item]}
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or item.get("question") or "").strip()
        queries = [str(x).strip() for x in (item.get("queries") or []) if str(x).strip()][:MAX_QUERIES]
        if q:
            out.append({"q": q[:200], "queries": queries or [q[:120]]})
    if not out:
        out = [{"q": question[:200], "queries": [question[:150]]}]
    return out[:MAX_QUESTIONS]


async def _run_tool(ctx, name: str, args: dict, bag: list):
    """Run one web tool through the agent's executor (so sources are numbered and the UI shows a normal tool row)."""
    st = ctx.state
    entry = {"id": f"{len(st['tools'])}", "name": name, "args": args, "status": "running"}
    st["tools"].append(entry)
    call = {"id": f"call_r{entry['id']}", "name": name, "arguments": args}
    yield {"type": "tool_start", "id": entry["id"], "name": name, "args": args}
    async for ev in agent._execute(ctx, call, entry):
        yield ev
    bag.append(entry)


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + " …"


async def run_research(ctx, question: str) -> AsyncIterator[dict]:
    s, st = ctx.s, ctx.state
    sources_before = len(st["sources"])

    def progress(i: int, detail: str = "") -> dict:
        return {"type": "progress", "stage": i + 1, "of": len(STAGES), "label": STAGES[i], "detail": detail}

    # ---- 1. planning
    yield progress(0)
    raw = await _ask(
        ctx,
        "You plan web research. Break the user's question into 3-5 specific sub-questions that together cover it "
        "(for a business question think: market size, competitors, technology, business models, opportunities and risks). "
        "For each give 1-2 short web search queries. Reply with ONLY JSON: "
        '{"questions": [{"q": "sub-question", "queries": ["search query"]}]}',
        question[:1500], 500, json_mode=True,
    )
    questions = plan_questions(_json_object(raw), question)
    entry = {"id": f"{len(st['tools'])}", "name": "research_plan", "args": {"question": question[:300]}, "status": "running"}
    st["tools"].append(entry)
    yield {"type": "tool_start", "id": entry["id"], "name": "research_plan", "args": entry["args"]}
    detail = {"questions": [{"q": x["q"], "queries": x["queries"]} for x in questions], "stages": STAGES}
    entry.update(status="done", ok=True, summary=f"{len(questions)} sub-questions", detail=detail, files=[], model_text="")
    yield {"type": "tool_result", "id": entry["id"], "ok": True, "summary": entry["summary"], "detail": detail,
           "files": [], "declined": False}

    # ---- 2. discovery
    found: list[list[dict]] = []  # per sub-question: [{n,title,url,snippet}]
    searches: list[dict] = []
    for qi, item in enumerate(questions):
        yield progress(1, f"{qi + 1}/{len(questions)}: {item['q']}")
        results: list[dict] = []
        for query in item["queries"]:
            bag: list = []
            async for ev in _run_tool(ctx, "web_search", {"query": query}, bag):
                yield ev
            e = bag[0] if bag else {}
            if e.get("ok"):
                results += [r for r in (e.get("detail") or {}).get("results", []) if r.get("url")]
            searches.append(e)
        found.append(results)
    if not any(found):
        msg = ("I could not retrieve any web results for this question, so I have nothing to base a report on. "
               "Check that Web search works (Settings → Tools) and try again, or rephrase the question.")
        st["acc"] += msg
        yield {"type": "delta", "text": msg}
        for ev in agent.finalize(ctx, False):
            yield ev
        return

    # ---- 3. investigation: read the best pages (round-robin so every sub-question gets evidence)
    pages: dict[str, dict] = {}  # url -> {"n", "title", "text", "qi"}
    queues = [list(results) for results in found]
    order: list[tuple[int, dict]] = []
    chosen: set[str] = set()
    while len(order) < MAX_PAGES and any(queues):
        for qi, queue in enumerate(queues):
            while queue and queue[0]["url"] in chosen:
                queue.pop(0)
            if queue and len(order) < MAX_PAGES:
                r = queue.pop(0)
                chosen.add(r["url"])
                order.append((qi, r))
    for i, (qi, r) in enumerate(order):
        yield progress(2, f"reading {i + 1}/{len(order)}: {r.get('title') or r['url']}")
        bag = []
        async for ev in _run_tool(ctx, "fetch_page", {"url": r["url"]}, bag):
            yield ev
        e = bag[0] if bag else {}
        if e.get("ok"):
            pages[r["url"]] = {"n": (e.get("detail") or {}).get("n"), "title": r.get("title"), "text": e.get("model_text", ""), "qi": qi}

    notes: list[dict] = []  # {"q", "text"}

    async def take_notes(label: str, results: list[dict], extra_urls: list[str]) -> str:
        parts = []
        for r in results[:6]:
            n = r.get("n")
            if n:
                parts.append(f"[{n}] {r.get('title', '')}: {_clip(r.get('snippet', ''), 260)}")
        for url in extra_urls[:3]:
            p = pages.get(url)
            if p and p["n"]:
                body = re.sub(r"^\[\d+\][^\n]*\n\n", "", p["text"])
                parts.append(f"[{p['n']}] (full page) {p['title'] or ''}\n{_clip(body, 2200)}")
        if not parts:
            return ""
        return await _ask(
            ctx,
            "Extract the facts relevant to the QUESTION from the SOURCES. One short line per fact, each ending with its "
            "source number like [3]. Keep figures exact and say what each number measures and its date. Note where "
            "sources disagree. If the sources contain nothing relevant, reply exactly: No evidence found.",
            f"QUESTION: {label}\n\nSOURCES:\n" + "\n\n".join(parts), 450,
        )

    for qi, item in enumerate(questions):
        yield progress(2, f"taking notes {qi + 1}/{len(questions)}: {item['q']}")
        urls = [u for u, p in pages.items() if p["qi"] == qi]
        text = agent.strip_think(await take_notes(item["q"], found[qi], urls))
        notes.append({"q": item["q"], "text": text or "No evidence found."})

    # one gap check: what is missing or contradictory? search once more for it
    yield progress(2, "checking for gaps and conflicts")
    gap_raw = await _ask(
        ctx,
        "You review research notes. Name the 1-2 most important gaps or conflicting claims and give one web search query "
        'for each that would settle it. Reply with ONLY JSON: {"queries": ["search query"]}. Empty list if the notes are solid.',
        "QUESTION: " + question[:400] + "\n\nNOTES:\n" + "\n".join(f"- {x['q']}: {_clip(x['text'], 500)}" for x in notes), 160,
        json_mode=True,
    )
    gap_queries = [str(x).strip() for x in ((_json_object(gap_raw) or {}).get("queries") or []) if str(x).strip()][:2]
    gap_results: list[dict] = []
    gap_urls: list[str] = []
    for gq in gap_queries:
        bag = []
        async for ev in _run_tool(ctx, "web_search", {"query": gq}, bag):
            yield ev
        e = bag[0] if bag else {}
        res = [r for r in (e.get("detail") or {}).get("results", []) if r.get("url")] if e.get("ok") else []
        gap_results += res
        known = {r["url"] for results in found for r in results}
        fresh = [r for r in res if r["url"] not in known]
        if fresh:
            bag = []
            async for ev in _run_tool(ctx, "fetch_page", {"url": fresh[0]["url"]}, bag):
                yield ev
            f = bag[0] if bag else {}
            if f.get("ok"):
                pages[fresh[0]["url"]] = {"n": (f.get("detail") or {}).get("n"), "title": fresh[0].get("title"),
                                          "text": f.get("model_text", ""), "qi": -1}
                gap_urls.append(fresh[0]["url"])
    if gap_results:
        text = agent.strip_think(await take_notes("Gap and conflict checks: " + "; ".join(gap_queries), gap_results, gap_urls))
        if text and "no evidence found" not in text.lower():
            notes.append({"q": "Gap and conflict checks", "text": text})

    # ---- 4. synthesis: put the notes in front of the writer, trimmed to fit the context window
    yield progress(3, "organising findings")
    budget = max(3500, int(int(s["num_ctx"]) * 0.45 * 3.5))
    per = max(500, budget // max(1, len(notes)))
    dossier = "\n\n".join(f"### {x['q']}\n{_clip(x['text'], per)}" for x in notes)
    sources_list = "\n".join(f"[{x['n']}] {x['title']} ({x['domain']})" for x in st["sources"][sources_before:] or st["sources"])

    # ---- 5. final report
    yield progress(4, "writing the report")
    system = (
        "You are a research analyst writing a report for the user from the NOTES below, which were gathered from the web "
        "just now. Use ONLY the notes; if the notes do not cover something, say so instead of filling the gap from "
        "memory. Cite every factual claim inline with its source number like [2] or [2][5] — only numbers that appear in the "
        "notes. Do not add a list of sources (the app shows it). When sources disagree, show both and say why they may "
        "differ. State what each number measures and its date.\n"
        "Structure the report with Markdown headings: Executive summary; Key findings (one section per sub-question, "
        "with the evidence); Patterns and comparison (what the findings have in common, where they conflict, a small "
        "table when it helps); Opportunities and risks; Conclusions and recommendations (concrete and prioritised); "
        "Limits of this research (what is missing or uncertain).\n"
        "Today's date: " + datetime.now().strftime("%B %d, %Y") + "."
    )
    user = f"QUESTION: {question}\n\nNOTES:\n{dossier}\n\nSOURCES:\n{sources_list}\n\nWrite the report now."
    prompt = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    raw_len = 0
    async with aclosing(llm.stream_chat(s, ctx.model, prompt, ctx.think, None)) as stream:
        async for e in stream:
            if e["type"] == "text":
                raw_len += len(e["text"])
                st["acc"] += e["text"]
                agent._track_think(st)
                yield {"type": "delta", "text": e["text"]}
            elif e["type"] == "usage":
                st["in"] += e["in"]
                st["out"] += e["out"]
            elif e["type"] == "finish":
                st["reason"] = e["reason"]
    for ev in agent.finalize(ctx, False):
        yield ev
