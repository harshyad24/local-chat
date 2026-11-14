"""A book (or any long document) as a study source for /learn: extracted once, split into page-tagged passages, embedded locally
(the same embeddings store as project files), and searched per lesson so the model teaches FROM the book instead of from memory.
Everything here is deterministic app code; the model only receives the retrieved passages.

Retrieval is scoped by the book's own structure: a PDF's bookmarks give each chapter's page range and its section titles, and a lesson
gets one passage per section of its chapter (dense + keyword scoring), not the three best matches from anywhere in the book."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

from .. import db, embeddings, tools, vectorstore

DOC_EXT = {".pdf", ".docx", ".txt", ".md"}
PAGE_RX = re.compile(r"^(?:--- page (\d+) ---|\[Page (\d+)\])[ \t]*$", re.M)
HEADING_RX = re.compile(r"^\s*((?:chapter|unit|part|module|section|lesson)\s+(?:\d+|[ivxlc]+)\b.{0,80}|\d{1,2}(?:\.\d{1,2})?\s+[A-Z][^\n.]{3,70})\s*$", re.I | re.M)
BLOCK_CHARS = 2400  # about one to two pages per indexed block
MAX_CHUNKS = 20000  # a 3,000-page textbook is about 12,000 passages; the old cap of 6,000 silently cut such a book in half
MIN_BOOK_CHARS = 6000  # shorter documents are read whole by the normal tools; indexing is for books


def scope_of(file_id: str) -> str:
    return f"book2:{file_id}"  # v2 = embedded with a 256-token window (v1 only saw the first 128 tokens of every passage)


def pick(files: list[dict]) -> Optional[dict]:
    """The newest attached document that can serve as a study book."""
    for f in reversed(files or []):
        if f.get("ext") in DOC_EXT:
            return {"id": f["id"], "name": f["name"], "path": f["path"], "ext": f["ext"]}
    return None


def pages_of(text: str) -> list[tuple[int, str]]:
    """[(page number, text)] from the page markers the extractor writes; one pseudo-page per ~3000 chars when there are none."""
    marks = list(PAGE_RX.finditer(text))
    if not marks:
        return [(i // BLOCK_CHARS + 1, text[i:i + BLOCK_CHARS]) for i in range(0, len(text), BLOCK_CHARS)]
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((int(m.group(1) or m.group(2)), text[m.end():end].strip()))
    return out


def passages(text: str) -> list[tuple[str, dict]]:
    """Page-tagged passages: consecutive pages are merged to ~BLOCK_CHARS, then cut on paragraph/sentence boundaries."""
    out: list[tuple[str, dict]] = []
    buf, first, last = "", None, None

    def flush():
        nonlocal buf, first, last
        for ch in embeddings.chunk_text(buf):
            out.append((ch, {"page": first, "page_end": last}))
        buf, first, last = "", None, None

    for pg, t in pages_of(text):
        if not t.strip():
            continue
        if first is None:
            first = pg
        buf = (buf + "\n\n" + t).strip()
        last = pg
        if len(buf) >= BLOCK_CHARS:
            flush()
    if buf:
        flush()
    return out[:MAX_CHUNKS]


def outline(text: str, limit: int = 45) -> list[str]:
    """Chapter-looking lines in reading order (deduplicated); the model designs the course from these instead of guessing."""
    seen, out = set(), []
    for m in HEADING_RX.finditer(text):
        h = re.sub(r"\s+", " ", m.group(1)).strip(" .")
        key = h.lower()
        if key in seen or len(h) < 5 or re.search(r"\.{4,}|\s\d+$", h) and len(h) > 60:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def sample(chunks: list[tuple[str, dict]], n: int = 12) -> list[str]:
    """Evenly spaced openings, for books with no recognisable chapter headings."""
    if not chunks:
        return []
    step = max(1, len(chunks) // n)
    return [f"p.{c[1].get('page')}: {re.sub(chr(10), ' ', c[0])[:160]}" for c in chunks[::step][:n]]


def extract(path: str) -> str:
    return tools._extract(Path(path))


def indexed(file_id: str) -> int:
    try:
        return embeddings.count(scope_of(file_id))
    except Exception:  # noqa: BLE001 - embeddings are optional
        return 0


async def prepare(f: dict, progress=None) -> dict:
    """Extract + index the book once (later lessons reuse the index). Returns {ok, chunks, pages, outline, sample, reason}."""
    if not embeddings.available():
        return {"ok": False, "reason": "The local embedding model is not installed, so the book cannot be searched."}
    try:
        text = await asyncio.to_thread(extract, f["path"])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"Could not read the book: {exc.__class__.__name__}: {str(exc)[:160]}"}
    if len(text.strip()) < MIN_BOOK_CHARS:
        return {"ok": False, "reason": "The attached file is short or has no readable text (a scan needs OCR), so it was not indexed."}
    chunks = await asyncio.to_thread(passages, text)
    pages = max((c[1].get("page_end") or 0 for c in chunks), default=0)
    have = indexed(f["id"])
    if have != len(chunks):
        step = 1500
        for i in range(0, len(chunks), step):
            if progress:
                await progress(f"Indexing the book: {min(i + step, len(chunks)):,} of {len(chunks):,} passages (first time only)")
            await asyncio.to_thread(embeddings.index_chunks, scope_of(f["id"]), f["id"], chunks[i:i + step], f["name"], 32, i, i == 0)
        await asyncio.to_thread(embeddings.remove, f"book:{f['id']}")  # the old, truncated index
    return {"ok": True, "chunks": len(chunks), "pages": pages, "outline": outline(text), "sample": sample(chunks), "reused": have == len(chunks),
            "truncated": len(chunks) >= MAX_CHUNKS}


# ---------------------------------------------------------------- the book's own table of contents
_STOP = {"a", "an", "the", "of", "and", "to", "in", "for", "with", "on", "from"}
_SKIP_SECTION = re.compile(r"^(?:summary|exercises?|discussion|resources and further reading|further reading|references|bibliography|index|notes?|contents|preface)$", re.I)


def toc(path: str) -> list[tuple[int, str, int]]:
    """[(level, title, page)] from a PDF's bookmarks, which carry the real chapter and section structure with page numbers."""
    if not str(path).lower().endswith(".pdf"):
        return []
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore
        with fitz.open(path) as d:
            return [(int(lv), re.sub(r"\s+", " ", t).strip(), int(pg)) for lv, t, pg in d.get_toc() if pg and int(pg) > 0 and str(t).strip()]
    except Exception:  # noqa: BLE001 - no bookmarks / unreadable: retrieval simply runs over the whole book
        return []


def _tokens(title: str) -> set[str]:
    t = re.sub(r"^\s*(?:chapter|unit|part|module|section|lesson)?\s*\d+(?:\.\d+)*[.):\s-]*", "", title.lower().strip())
    return {w[:6] for w in re.findall(r"[a-z0-9]+", t) if w not in _STOP}


def match_toc(title: str, entries: list[tuple[int, str, int]]) -> Optional[tuple[int, str, int]]:
    """The earliest bookmark whose title is (nearly) the module title; None when nothing is close enough."""
    a = _tokens(title)
    if not a:
        return None
    best, best_score = None, 0.0
    for e in entries:
        b = _tokens(e[1])
        if not b:
            continue
        score = len(a & b) / max(len(a), len(b))
        if score > best_score + 1e-9 or (abs(score - best_score) < 1e-9 and best and e[2] < best[2]):
            best, best_score = e, score
    return best if best and best_score >= 0.67 else None


def module_ranges(modules: list[dict], entries: list[tuple[int, str, int]], default_len: int = 110) -> dict[int, tuple[int, int]]:
    """{module index: (first page, last page)} - a module runs from its bookmark to the next module's, capped at a chapter's usual length."""
    starts = []
    for m in modules:
        e = match_toc(str(m.get("title", "")), entries)
        starts.append(e[2] if e else None)
    known = sorted({s for s in starts if s})
    out: dict[int, tuple[int, int]] = {}
    for i, s in enumerate(starts):
        if not s:
            continue
        later = [x for x in known if x > s]
        out[i] = (s, min(s + default_len - 1, later[0] - 1) if later else s + default_len - 1)
    return out


def sections_in(entries: list[tuple[int, str, int]], lo: int, hi: int, limit: int = 8) -> list[tuple[str, int]]:
    """The book's own section titles inside a page range, in order, thinned evenly when there are many."""
    inside = [(lv, t, p) for lv, t, p in entries if lo < p <= hi and not _SKIP_SECTION.match(t.strip())]
    top = [(t, p) for lv, t, p in inside if lv == 1]
    pick_from = top if len(top) >= 3 else [(t, p) for _, t, p in inside]
    seen, uniq = set(), []
    for t, p in pick_from:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append((t, p))
    if len(uniq) > limit:
        step = len(uniq) / limit
        uniq = [uniq[int(i * step)] for i in range(limit)]
    return uniq


# ---------------------------------------------------------------- retrieval: dense + keyword, scoped to the chapter, one passage per section
def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9_]{2,}", text.lower()) if w not in _STOP]


def _load_rows(file_id: str) -> list[dict]:
    import json as _json
    import numpy as np
    embeddings._init()
    with db.conn() as c:
        rows = c.execute("SELECT ord, text, meta, dim, vec FROM chunks WHERE scope=? AND model=? ORDER BY ord", (scope_of(file_id), embeddings.model_name())).fetchall()
    out = []
    for r in rows:
        m = _json.loads(r["meta"] or "{}")
        out.append({"page": m.get("page") or 0, "page_end": m.get("page_end") or m.get("page") or 0, "text": r["text"], "vec": np.frombuffer(r["vec"], dtype="float32")})
    return out


def _retrieve(file_id: str, query: str, span: Optional[tuple[int, int]], sections: list[tuple[str, int]], limit: int) -> list[dict]:
    import numpy as np
    rows = _load_rows(file_id)
    if span:
        rows = [r for r in rows if r["page_end"] >= span[0] and r["page"] <= span[1]]
    rows = [r for r in rows if len(r["text"].strip()) >= 150]  # headings, page furniture and empty figure pages carry nothing to teach from
    if not rows:
        return []
    M = np.vstack([r["vec"] for r in rows])
    titles = [t for t, _ in sections]
    vecs = embeddings.embed([query] + titles)["vectors"]
    dense = M @ vecs[0]
    try:
        from rank_bm25 import BM25Okapi
        bm = np.asarray(BM25Okapi([_words(r["text"]) or ["_"] for r in rows]).get_scores(_words(query) or ["_"]), dtype="float32")
    except Exception:  # noqa: BLE001 - keyword scoring is a bonus; dense scores alone still work
        bm = np.zeros(len(rows), dtype="float32")
    bmn = bm / bm.max() if bm.max() > 0 else bm
    chosen: list[int] = []
    for k, (title, pg) in enumerate(sections):
        end = sections[k + 1][1] - 1 if k + 1 < len(sections) else (span[1] if span else pg + 20)
        idx = [i for i, r in enumerate(rows) if r["page_end"] >= pg and r["page"] <= max(end, pg)]
        if not idx:
            continue
        score = 0.5 * dense[idx] + 0.5 * (M[idx] @ vecs[1 + k]) + 0.15 * bmn[idx]
        best = idx[int(np.argmax(score))]
        if best not in chosen:
            chosen.append(best)
    rank_d = {int(i): n for n, i in enumerate(np.argsort(-dense))}
    rank_b = {int(i): n for n, i in enumerate(np.argsort(-bm))}
    for i in sorted(range(len(rows)), key=lambda i: -(1 / (60 + rank_d[i]) + 1 / (60 + rank_b[i]))):
        if len(chosen) >= limit:
            break
        if i not in chosen:
            chosen.append(i)
    hits = [{"page": rows[i]["page"], "page_end": rows[i]["page_end"], "text": rows[i]["text"], "score": float(dense[i])} for i in chosen[:limit]]
    return sorted(hits, key=lambda h: (h["page"] or 0))


def _retrieve_store(store, file_id: str, query: str, span: Optional[tuple[int, int]], sections: list[tuple[str, int]], limit: int) -> list[dict]:
    """The same idea through a vector database: a metadata-filtered query per section, then the best matches anywhere in the chapter.
    Dense scores only (no keyword scoring), so it is the "plain vector DB" baseline for the comparison in scripts/eval_retrieval.py."""
    import numpy as np
    scope = scope_of(file_id)
    vectorstore.mirror(scope, store)  # copies the stored vectors across once; nothing is embedded again
    V = embeddings.embed([query] + [t for t, _ in sections])["vectors"]
    chosen: list[dict] = []
    seen: set = set()

    def take(h: dict) -> bool:
        key = (h["page"], h["text"][:60])
        if key in seen or len(h["text"].strip()) < 150:
            return False
        seen.add(key)
        chosen.append(h)
        return True

    for k, (title, pg) in enumerate(sections):
        end = sections[k + 1][1] - 1 if k + 1 < len(sections) else (span[1] if span else pg + 20)
        v = V[0] + V[1 + k]
        v = v / (np.linalg.norm(v) or 1.0)  # half the question, half the section title
        for h in store.search(scope, v, 3, (pg, max(end, pg))):
            if take(h):
                break
    for h in store.search(scope, V[0], limit * 3, span):
        if len(chosen) >= limit:
            break
        take(h)
    return sorted(chosen[:limit], key=lambda h: (h["page"] or 0))


async def retrieve(file_id: str, query: str, span: Optional[tuple[int, int]] = None, sections: Optional[list[tuple[str, int]]] = None, limit: int = 8) -> list[dict]:
    """Best passages for a lesson: [{page, page_end, text, score}] in reading order. Inside a chapter's page range, one passage per section
    of the chapter (so the lesson covers the chapter, not three paragraphs of it); the rest of the slots go to the best dense+keyword matches.
    With Settings -> vector_backend = chroma the dense search runs in Chroma instead (when it is installed and allowed to run)."""
    try:
        store = vectorstore.get_store()
        if store.name != "sqlite":
            return await asyncio.to_thread(_retrieve_store, store, file_id, query, span, sections or [], limit)
        return await asyncio.to_thread(_retrieve, file_id, query, span, sections or [], limit)
    except Exception:  # noqa: BLE001
        return []


async def find(file_id: str, query: str, limit: int = 5) -> list[dict]:
    """Unscoped search over the whole book (used when the book has no usable table of contents)."""
    return await retrieve(file_id, query, None, None, limit)


_CITE = re.compile(r"\s*\(\s*(?:pp?\.|pages?)\s*(\d+)(?:\s*[-–,]\s*(?:pp?\.\s*)?\d+)*\s*\)", re.I)


def check_citations(text: str, hits: list[dict]) -> tuple[str, list[int]]:
    """Page citations in a lesson that do not come from the passages the model was given are removed; returns (clean text, removed pages)."""
    ok: set[int] = set()
    for h in hits:
        if h.get("page"):
            ok.update(range(int(h["page"]), int(h.get("page_end") or h["page"]) + 1))
    bad: list[int] = []

    def fix(m):
        n = int(m.group(1))
        if n in ok:
            return m.group(0)
        bad.append(n)
        return ""

    return _CITE.sub(fix, text), bad


def pages_label(hits: list[dict]) -> str:
    pgs = sorted({h["page"] for h in hits if h.get("page")})
    return ", ".join(str(p) for p in pgs)


def as_prompt(hits: list[dict], per: int = 900) -> str:
    return "\n\n".join(f"[p. {h['page']}] {h['text'][:per]}" for h in hits)
