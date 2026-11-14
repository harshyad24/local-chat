"""Accuracy layer. The app, not the model, decides when to look things up and checks the answer against what it found.

1. ground-first  - factual / time-sensitive questions get a web search (and the top page) run by the app *before* the model
                   answers, instead of hoping a small model chooses to call the tool.
2. claim check   - after the answer is written, every checkable sentence (numbers, names, dates) is compared with the text the
                   tools actually returned. Sentences with no support are re-judged by the model, and the confirmed ones are
                   flagged (and optionally removed by a constrained rewrite).
Everything here is best effort: any failure leaves the original answer untouched.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from . import llm

# ------------------------------------------------------------------ 1. when to look something up
_SKIP = re.compile(
    r"^\s*(?:hi|hello|hey|thanks?|thank you|ok(?:ay)?|cool|great|yes|no|lol)\b[\s!.?]*$"
    r"|\b(?:write|compose|draft|rewrite|translate|summari[sz]e (?:this|the following|my)|proofread|brainstorm|"
    r"poem|story|essay|email|cover letter|joke|code|function|script|regex|sql|debug|refactor|implement|"
    r"explain (?:this|the) (?:code|error)|flow ?chart|diagram|chart|graph|plot|dashboard)\b",
    re.I)
_MATH = re.compile(r"^[\s\d+\-*/^().=%,x×÷]+$|\d\s*[-+*/^×÷%]\s*\d|\d%\s+of|(?:factorial|square root|prime number)|\b(?:calculate|solve|integral|derivative|equation|simplify)\b", re.I)
_FACT = re.compile(
    r"\b(?:who|when|where|which|what(?:'s| is| are| was| were)|how (?:many|much|old|tall|long|far|big)|"
    r"latest|current(?:ly)?|today|now|recent(?:ly)?|new(?:est)?|price|cost|release|released|launch|winner|won|"
    r"population|capital|ceo|founder|record|score|result|version|rank|top \d+|best|tallest|largest|highest|"
    r"net worth|age of|born|died|date of|stock|weather|news|20(?:2\d|3\d))\b", re.I)


def should_ground(question: str) -> bool:
    q = (question or "").strip()
    if len(q) < 8 or _SKIP.search(q) or _MATH.search(q) or re.search(r"https?://|www\.", q):
        return False  # a message with a link names its own source: the model reads that page instead of a search
    if len([n for n in re.findall(r"\d+(?:[.:]\d+)?", q) if not re.fullmatch(r"(?:19|20)\d\d", n)]) >= 2:
        return False  # "a train leaves at 3:45 and travels 2h50" is a calculation, not a lookup
    return bool(_FACT.search(q)) or q.endswith("?")


async def search_query(s: dict, model: str, question: str, history: list[dict]) -> str:
    """The user's wording is a fine query for a standalone question; a follow-up ("and her age?") needs the topic spelled out."""
    q = re.sub(r"\s+", " ", question).strip()
    prior = [m["content"] for m in history[:-1] if m["role"] in ("user", "assistant")][-3:]
    followup = bool(prior) and (len(q.split()) < 7 or re.search(r"\b(?:he|she|it|they|them|his|her|its|their|that|this|those|there|one)\b", q, re.I))
    if not followup:
        return q[:200]
    try:
        out = await llm.complete(
            s, model,
            [{"role": "user", "content": "Conversation so far:\n" + "\n".join(p[:300] for p in prior) +
              f"\n\nLatest message: {q}\n\nWrite ONE self-contained web search query (max 12 words) for the latest message. Output only the query."}],
            max_tokens=40, num_ctx=int(s.get("num_ctx") or 8192))
        out = re.sub(r"[\"“”\n].*$", "", out.strip().strip("\"'")).strip()
        return out[:200] or q[:200]
    except Exception:  # noqa: BLE001
        return q[:200]


_WORD = re.compile(r"[a-z0-9]{3,}")
_COMMON = {"the", "and", "for", "who", "what", "when", "where", "which", "how", "was", "were", "are", "is", "did", "does", "has", "have",
           "with", "from", "that", "this", "about", "into", "after", "before", "tell", "give"}


def focus(text: str, question: str, limit: int = 6000) -> str:
    """Keep the passages of a long page that talk about the question (paragraph overlap), in page order."""
    if len(text) <= limit:
        return text
    terms = {w for w in _WORD.findall(question.lower()) if w not in _COMMON}
    paras = [p for p in text.split(chr(10)) if p.strip()]
    scored = []
    for i, para in enumerate(paras):
        words = set(_WORD.findall(para.lower()))
        hit = len(terms & words)
        scored.append((hit * 2 + (1 if re.search(r"\d", para) else 0) - (i * 0.002), i))
    keep, used = set(), 0
    for _, i in sorted(scored, reverse=True):
        if used + len(paras[i]) + 1 > limit:
            continue
        keep.add(i)
        used += len(paras[i]) + 1
    return chr(10).join(paras[i] for i in sorted(keep))


_BAD_HOSTS = ("youtube.com", "youtu.be", "facebook.com", "instagram.com", "tiktok.com", "twitter.com", "x.com", "pinterest.", "reddit.com/r/")


async def prefetch(ctx, query: str, pages: int = 1):
    """Run web_search (and fetch the best page) as ordinary tool calls the user can see, then hand the text to the model."""
    from . import agent
    st = ctx.state
    done: list[str] = []
    searched = None
    for name, args in [("web_search", {"query": query})]:
        entry = {"id": f"{len(st['tools'])}", "name": name, "args": args, "status": "running", "auto": True}
        st["tools"].append(entry)
        yield {"type": "tool_start", "id": entry["id"], "name": name, "args": args}
        async for ev in agent._execute(ctx, {"name": name, "arguments": args}, entry):
            yield ev
        if entry.get("ok"):
            searched = entry
            done.append(entry["model_text"])
    if not searched:
        return
    urls = [r["url"] for r in (searched.get("detail") or {}).get("results", []) if not any(b in r["url"] for b in _BAD_HOSTS)]
    for url in urls[:pages]:
        entry = {"id": f"{len(st['tools'])}", "name": "fetch_page", "args": {"url": url, "query": query}, "status": "running", "auto": True}
        st["tools"].append(entry)
        yield {"type": "tool_start", "id": entry["id"], "name": "fetch_page", "args": entry["args"]}
        async for ev in agent._execute(ctx, {"name": "fetch_page", "arguments": entry["args"]}, entry):
            yield ev
        if entry.get("ok"):
            done.append(focus(entry["model_text"], query, 6000))
    if done:
        ctx.prompt[0]["content"] += (
            "\n\nGROUNDING (the app already searched the web for this question; results below). Answer from this material. "
            "If it conflicts with what you remember, the material is newer: trust it. Cite as [n]. If the material does not contain the answer, say so plainly instead of guessing from memory.\n\n"
            + "\n\n".join(done))


# ------------------------------------------------------------------ 2. claim check
_STOP_CAPS = {"The", "This", "That", "These", "Those", "It", "Its", "In", "On", "At", "He", "She", "They", "We", "You", "I", "A", "An",
              "As", "And", "But", "Or", "If", "For", "With", "However", "Also", "Note", "Based", "According", "Here", "There", "So", "While",
              "When", "Where", "What", "Who", "Which", "His", "Her", "Their", "One", "Some", "Many", "Most", "No", "Not", "Yes", "Source", "Sources"}
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
_CAP = re.compile(r"\b[A-Z][a-zA-Z0-9&'\-]{2,}(?:\s+[A-Z][a-zA-Z0-9&'\-]+)*")
_CITE = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.lower().replace(",", "").replace("’", "'"))


def split_claims(answer: str) -> list[str]:
    text = re.sub(r"```[\s\S]*?```|<think>[\s\S]*?</think>", " ", answer)
    out = []
    for block in re.split(r"\n+", text):
        block = re.sub(r"^\s*(?:[-*•]|\d+[.)]|#{1,6})\s+", "", block).strip()
        if not block or block.startswith("|") or len(block) < 12:
            continue
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(\[])", block):
            sent = sent.strip()
            if len(sent) >= 12:
                out.append(sent)
    return out


def checkable(sentence: str, question: str = "") -> dict:
    """The parts of a sentence that can be wrong in a checkable way: numbers and proper names."""
    s = _CITE.sub("", sentence)
    qn = _norm(question)
    nums = [n.rstrip("%") for n in _NUM.findall(s)]
    nums = [n.replace(",", "") for n in nums]
    nums = [n for n in nums if not re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d])", qn)]
    names = []
    for m in _CAP.finditer(s):
        w = re.sub(r"['’]s$", "", m.group(0).strip())
        first = w.split()[0]
        if m.start() == 0 and len(w.split()) == 1 and not re.search(r"\d|[a-z][A-Z]", w):
            continue  # "Let me know" / "Overall" - only the capital letter, not a name
        if m.start() == 0 and first in _STOP_CAPS:
            w = " ".join(w.split()[1:])
        elif first in _STOP_CAPS and len(w.split()) == 1:
            continue
        if w and w.split()[0] not in _STOP_CAPS and _norm(w) not in qn:
            names.append(w)
    return {"numbers": nums, "names": names}


def support(sentence: str, evidence_norm: str, question: str = "") -> dict:
    c = checkable(sentence, question)
    miss_n = [n for n in c["numbers"] if not re.search(r"(?<![\d.])" + re.escape(n) + r"(?![\d])", evidence_norm)]
    miss_w = [w for w in c["names"] if _norm(w) not in evidence_norm and not all(_norm(p) in evidence_norm for p in w.split())]
    total = len(c["numbers"]) + len(c["names"])
    return {"checkable": total, "missing_numbers": miss_n, "missing_names": miss_w,
            "score": 1.0 if not total else 1 - (len(miss_n) + len(miss_w)) / total}


def lexical_check(answer: str, evidence: str, question: str = "") -> dict:
    ev = _norm(evidence)
    checked, flagged = 0, []
    for sent in split_claims(answer):
        r = support(sent, ev, question)
        if not r["checkable"]:
            continue
        checked += 1
        if r["missing_numbers"] or r["missing_names"]:
            flagged.append({"text": sent, "missing": r["missing_numbers"] + r["missing_names"]})
    return {"checked": checked, "flagged": flagged}


JUDGE = (
    "You are a strict fact checker. EVIDENCE is the only source of truth.\n\nEVIDENCE:\n{evidence}\n\n"
    "Below are numbered sentences from an answer. For each, decide if the EVIDENCE supports it (paraphrase is fine; a number, name "
    "or date that does not appear in the evidence or contradicts it is NOT supported). Reply with JSON only: "
    '{{"unsupported": [numbers of the sentences that are not supported]}}.\n\n{sentences}')


async def judge(s: dict, model: str, evidence: str, flagged: list[dict]) -> list[dict]:
    """Second opinion on the sentences the cheap check flagged, to avoid false alarms from paraphrase."""
    if not flagged:
        return []
    listing = "\n".join(f"{i + 1}. {f['text']}" for i, f in enumerate(flagged))
    try:
        raw = await llm.complete(s, model, [{"role": "user", "content": JUDGE.format(evidence=evidence[:9000], sentences=listing)}],
                                 max_tokens=120, json_mode=True, num_ctx=int(s.get("num_ctx") or 8192))
        data = json.loads(re.search(r"\{[\s\S]*\}", raw).group(0))
        bad = {int(x) for x in data.get("unsupported", []) if str(x).isdigit()}
        return [f for i, f in enumerate(flagged) if (i + 1) in bad]
    except Exception:  # noqa: BLE001 - if the judge fails keep the lexical verdict
        return flagged


REVISE = (
    "Rewrite the ANSWER so that it contains only information supported by the EVIDENCE. Remove or soften the listed unsupported "
    "statements (say the sources do not confirm them if that matters). Keep everything else, the wording, the formatting and the "
    "[n] citations exactly as they are. Output only the rewritten answer.\n\nEVIDENCE:\n{evidence}\n\nUNSUPPORTED STATEMENTS:\n{bad}\n\nANSWER:\n{answer}")


async def revise(s: dict, model: str, evidence: str, answer: str, bad: list[dict]) -> Optional[str]:
    try:
        out = await llm.complete(
            s, model, [{"role": "user", "content": REVISE.format(evidence=evidence[:9000], answer=answer, bad="\n".join("- " + b["text"] for b in bad))}],
            max_tokens=min(1800, max(300, len(answer) // 2)), num_ctx=int(s.get("num_ctx") or 8192))
        out = out.strip()
        return out if len(out) > len(answer) * 0.4 else None  # a rewrite that gutted the answer is worse than the flagged original
    except Exception:  # noqa: BLE001
        return None


def evidence_of(ctx, question: str = "") -> str:
    parts = [e.get("model_text") or "" for e in ctx.state["tools"] if e.get("ok") and e["name"] in ("web_search", "fetch_page", "read_file", "search_knowledge", "search_chat")]
    if ctx.state.get("evidence"):
        parts.append(ctx.state["evidence"])  # skills keep what their workflow read here
    return "\n\n".join(p for p in parts if p)


async def postcheck(ctx, question: str, mode: str = "fix"):
    """Check the finished answer. Yields a `verification` event (and a replacement answer when mode == "fix")."""
    st = ctx.state
    answer = re.sub(r"<think>[\s\S]*?</think>", "", st["acc"]).strip()
    if len(answer) < 40:
        return
    evidence = evidence_of(ctx, question)
    if len(evidence) < 200:
        return
    terms = {w for w in _WORD.findall(question.lower()) if w not in _COMMON}
    if terms and len(terms & set(_WORD.findall(evidence.lower()))) / len(terms) < 0.4:
        return  # the sources are not about this question, so they cannot confirm or contradict the answer
    lex = await asyncio.to_thread(lexical_check, answer, evidence + "\n" + question, question)
    if not lex["checked"]:
        return
    bad = await judge(ctx.s, ctx.model, evidence, lex["flagged"]) if lex["flagged"] else []
    result = {"checked": lex["checked"], "unsupported": [{"text": b["text"], "missing": b.get("missing", [])} for b in bad], "fixed": False}
    if bad and mode == "fix":
        new = await revise(ctx.s, ctx.model, evidence, answer, bad)
        if new:
            m = re.match(r"\s*<think>[\s\S]*?</think>", st["acc"])
            keep = len(m.group(0)) if m else 0
            st["acc"] = st["acc"][:keep] + ("\n\n" if keep else "") + new
            yield {"type": "truncate", "length": keep}
            yield {"type": "delta", "text": ("\n\n" if keep else "") + new}
            result["fixed"] = True
    st["verification"] = result
    yield {"type": "verification", **result}
