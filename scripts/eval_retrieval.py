"""Measure retrieval quality on a real book, with no hand-made labels.

The PDF's own table of contents is the answer key: the question is a section title ("Broadcasting Mechanism"), and a retrieved passage
is correct when its page lies inside that section's page range. For each strategy the script reports hit@3 and hit@8 (did at least one
of the top-N passages come from the right section?) and the average time per question.

    python scripts/eval_retrieval.py --file-id <id of the indexed book> [--n 100] [--seed 1]

The book must already be indexed (run /learn on it once). Strategies compared (Chroma only when it can start):
    dense / SQLite      exact cosine over every stored vector
    dense / Chroma      the same vectors, searched through Chroma's HNSW index
    LangChain dense     LangChain StoreRetriever over the SQLite store (checks the wrapper adds nothing wrong)
    hybrid / SQLite     dense + BM25 keyword scoring fused (the app's own method, without chapter scoping)
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKIP = re.compile(r"^(?:summary|exercises?|discussion|resources and further reading|further reading|references|preface|index)$", re.I)


def sample_sections(toc, n: int, seed: int = 1):
    """[(title, first page, last page)] for level-2 entries (real sub-sections), skipping boilerplate; reproducible."""
    spans = []
    for i, (lv, title, pg) in enumerate(toc):
        if lv != 2 or SKIP.match(title.strip()) or len(title) < 6:
            continue
        nxt = next((p for _, _, p in toc[i + 1:] if p > pg), pg + 1)
        spans.append((title, pg, max(pg, nxt - 1)))
    random.Random(seed).shuffle(spans)
    return spans[:n]


def hit(pages, lo: int, hi: int, n: int) -> bool:
    """Is any of the first n retrieved passages inside the section [lo, hi]? (A passage may span several pages: any overlap counts.)"""
    return any(p is not None and lo <= p[1] and p[0] <= hi for p in pages[:n])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file-id", required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    import asyncio

    from backend import db, embeddings, vectorstore
    from backend.integrations import langchain_rag
    from backend.skills import book

    f = db.get_file(a.file_id)
    if not f:
        sys.exit("No such file id.")
    scope = book.scope_of(a.file_id)
    if not book.indexed(a.file_id):
        sys.exit("This book is not indexed yet: run /learn on it once, then try again.")
    sections = sample_sections(book.toc(f["path"]), a.n, a.seed)
    if not sections:
        sys.exit("The PDF has no usable bookmarks, so there is no answer key.")
    print(f"{f['name']}: {len(sections)} sections as questions (seed {a.seed}), {book.indexed(a.file_id):,} passages\n")

    sq = vectorstore.SqliteStore()
    strategies = {"dense / SQLite": lambda q, v, k: [(h["page"], h["page_end"]) for h in sq.search(scope, v, k)]}
    ch = vectorstore.ChromaStore()
    if ch.available():
        vectorstore.mirror(scope, ch)
        strategies["dense / Chroma"] = lambda q, v, k: [(h["page"], h["page_end"]) for h in ch.search(scope, v, k)]
    else:
        print("(Chroma is not available here, so it is left out.)\n")
    lc = langchain_rag.StoreRetriever(scope=scope, backend=sq, k=8)
    strategies["LangChain dense"] = lambda q, v, k: [(d.metadata["page"], d.metadata["page_end"]) for d in lc.invoke(q)[:k]]
    strategies["hybrid / SQLite"] = lambda q, v, k: [(h["page"], h["page_end"]) for h in asyncio.run(book.retrieve(a.file_id, q, None, None, k))] \
        if vectorstore.get_store("sqlite").name == "sqlite" else []

    vecs = embeddings.embed([t for t, _, _ in sections])["vectors"]
    rows = []
    for name, fn in strategies.items():
        h3 = h8 = 0
        t0 = time.perf_counter()
        for (title, lo, hi), v in zip(sections, vecs):
            pages = fn(title, v, 8)
            h3 += hit(pages, lo, hi, 3)
            h8 += hit(pages, lo, hi, 8)
        ms = (time.perf_counter() - t0) * 1000 / len(sections)
        rows.append((name, h3 / len(sections), h8 / len(sections), ms))
    print("| strategy | hit@3 | hit@8 | ms / question |\n|---|---|---|---|")
    for name, a3, a8, ms in rows:
        print(f"| {name} | {a3:.0%} | {a8:.0%} | {ms:.0f} |")


if __name__ == "__main__":
    main()
