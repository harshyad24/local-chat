"""Local embedding service + semantic search over documents, project files and memories.

* Model: sentence-transformers (default: a small multilingual model that handles English and Hindi).
  Loaded lazily, placed on CUDA only when gpu.choose_device says it is safe, otherwise CPU; unloaded when idle.
* Storage: SQLite table `chunks` (float32 vectors as BLOBs). Search is a numpy cosine over the candidate rows,
  which is plenty fast for the tens of thousands of chunks a personal app holds.
* Everything degrades: without sentence-transformers installed the functions return None / raise EmbeddingsUnavailable
  and callers fall back to keyword search.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Iterable, Optional

from . import db, gpu

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_VRAM_MB = 600
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
MAX_CHUNKS_PER_SOURCE = 800


class EmbeddingsUnavailable(RuntimeError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,          -- e.g. "project:<id>", "memory", "chat:<id>", "research:<id>"
    source TEXT NOT NULL,         -- file id / memory id / url
    label TEXT NOT NULL DEFAULT '',   -- human readable (file name)
    ord INTEGER NOT NULL DEFAULT 0,   -- chunk position within the source
    text TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(scope, source);
"""
_ready_for = None


def _init():
    global _ready_for
    if _ready_for != str(db.DB_PATH):  # tests (and a moved data dir) switch databases
        with db.conn() as c:
            c.executescript(SCHEMA)
        _ready_for = str(db.DB_PATH)


# ------------------------------------------------------------------ model
def available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


def model_name() -> str:
    try:
        return db.get_settings().get("embed_model") or DEFAULT_MODEL
    except Exception:
        return DEFAULT_MODEL


def _load(name: str, device: str):
    key = f"embed:{name}"
    obj, dev = gpu.get(key)
    if obj is not None and (dev == device or device == "cpu" and dev == "cpu"):
        return obj, dev
    if obj is not None:
        gpu.unload(key)
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise EmbeddingsUnavailable(f"sentence-transformers is not installed ({exc})")
    m = SentenceTransformer(name, device=device)
    try:  # the default max is 128 tokens, so the second half of a ~900-character chunk was never embedded
        if (m.max_seq_length or 0) < 256:
            m.max_seq_length = 256
    except Exception:  # noqa: BLE001 - a model without this attribute keeps its own limit
        pass
    if device == "cuda":
        m.half()  # Turing GPUs: fp16 is fast and halves VRAM; there is no bf16
    gpu.register(key, m, device, MODEL_VRAM_MB if device == "cuda" else 0)
    return m, device


def embed(texts: list[str], batch_size: int = 32, prefer: str = "auto") -> dict:
    """Embed texts. Returns {"vectors": ndarray(n, d) float32 L2-normalised, "device", "model", "ms"}."""
    import numpy as np
    if not texts:
        return {"vectors": np.zeros((0, 0), dtype="float32"), "device": "cpu", "model": model_name(), "ms": 0}
    t0 = time.perf_counter()
    name = model_name()
    with gpu.task("embeddings", need_mb=MODEL_VRAM_MB, prefer=prefer) as info:
        try:
            model, device = _load(name, info["device"])
            vecs = model.encode(texts, batch_size=max(1, int(batch_size)), normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        except Exception as exc:
            if info["device"] != "cuda":
                raise
            # CUDA run failed (out of memory, driver issue): drop the GPU copy and retry once on CPU
            gpu.unload(f"embed:{name}")
            model, device = _load(name, "cpu")
            vecs = model.encode(texts, batch_size=max(1, int(batch_size) // 2), normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            info["reason"] = f"CUDA failed ({type(exc).__name__}); used CPU"
    return {"vectors": np.asarray(vecs, dtype="float32"), "device": device, "model": name, "reason": info["reason"],
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


def similarity(a: str, b: str) -> Optional[dict]:
    """Cosine similarity of two texts, or None if the embedding model isn't available (callers then use TF-IDF)."""
    if not available():
        return None
    r = embed([a, b])
    v = r["vectors"]
    return {"score": float(v[0] @ v[1]), "method": "embeddings", "device": r["device"]}


# ------------------------------------------------------------------ chunking
def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split on paragraph/sentence boundaries into ~size-character chunks with a little overlap."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    units = [u.strip() for u in re.split(r"\n{2,}|(?<=[.!?।])\s+(?=[A-Zऀ-ॿ0-9])", text) if u.strip()]
    chunks, cur = [], ""
    for u in units:
        while len(u) > size * 1.5:  # a monster paragraph: hard split
            cut = u.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                chunks.append(cur); cur = ""
            chunks.append(u[:cut].strip()); u = u[cut:].strip()
        if cur and len(cur) + len(u) + 1 > size:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap else ""
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail
            cur = (tail + " " + u).strip()
        else:
            cur = (cur + " " + u).strip() if cur else u
    if cur:
        chunks.append(cur)
    return chunks[:MAX_CHUNKS_PER_SOURCE]


# ------------------------------------------------------------------ index / search
def index_text(scope: str, source: str, text: str, label: str = "", meta: Optional[dict] = None, batch_size: int = 32) -> dict:
    """(Re)index one source. Replaces any earlier chunks for the same scope+source. Returns counts and timing."""
    _init()
    chunks = chunk_text(text)
    if not chunks:
        remove(scope, source)
        return {"chunks": 0, "device": "cpu", "ms": 0}
    r = embed(chunks, batch_size=batch_size)
    now = time.time()
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    with db.conn() as c:
        c.execute("DELETE FROM chunks WHERE scope=? AND source=?", (scope, source))
        c.executemany(
            "INSERT INTO chunks(scope, source, label, ord, text, meta, model, dim, vec, created) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(scope, source, label, i, ch, meta_json, r["model"], int(r["vectors"].shape[1]), r["vectors"][i].tobytes(), now) for i, ch in enumerate(chunks)],
        )
    return {"chunks": len(chunks), "device": r["device"], "model": r["model"], "ms": r["ms"]}


def index_chunks(scope: str, source: str, items: list[tuple[str, dict]], label: str = "", batch_size: int = 32,
                 start: int = 0, replace: bool = True) -> dict:
    """Index ready-made chunks [(text, meta)] for one source (e.g. a book with page numbers). Replaces earlier chunks of the source
    unless replace=False (used to add a long source in batches; `start` continues the numbering)."""
    _init()
    items = [(t, m) for t, m in items if t and t.strip()]
    if not items:
        if replace:
            remove(scope, source)
        return {"chunks": 0, "device": "cpu", "ms": 0}
    r = embed([t for t, _ in items], batch_size=batch_size)
    now = time.time()
    with db.conn() as c:
        if replace:
            c.execute("DELETE FROM chunks WHERE scope=? AND source=?", (scope, source))
        c.executemany(
            "INSERT INTO chunks(scope, source, label, ord, text, meta, model, dim, vec, created) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(scope, source, label, start + i, t, json.dumps(m, ensure_ascii=False), r["model"], int(r["vectors"].shape[1]), r["vectors"][i].tobytes(), now)
             for i, (t, m) in enumerate(items)],
        )
    return {"chunks": len(items), "device": r["device"], "model": r["model"], "ms": r["ms"]}


def remove(scope: str, source: Optional[str] = None) -> int:
    _init()
    with db.conn() as c:
        cur = c.execute("DELETE FROM chunks WHERE scope=?" + (" AND source=?" if source else ""), (scope, source) if source else (scope,))
        return cur.rowcount


def count(scope: Optional[str] = None) -> int:
    _init()
    with db.conn() as c:
        row = c.execute("SELECT COUNT(*) n FROM chunks" + (" WHERE scope=?" if scope else ""), (scope,) if scope else ()).fetchone()
    return int(row["n"])


def search(query: str, scopes: Iterable[str] | str, limit: int = 5, threshold: float = 0.25, meta_filter: Optional[dict] = None) -> dict:
    """Semantic search. Returns {"results": [{score, text, source, label, scope, ord, meta}], "device", "ms", "searched"}."""
    import numpy as np
    _init()
    if isinstance(scopes, str):
        scopes = [scopes]
    scopes = list(scopes)
    if not scopes or not query.strip():
        return {"results": [], "searched": 0, "ms": 0, "device": "cpu"}
    name = model_name()
    marks = ",".join("?" * len(scopes))
    with db.conn() as c:
        rows = c.execute(f"SELECT id, scope, source, label, ord, text, meta, dim, vec FROM chunks WHERE scope IN ({marks}) AND model=?", (*scopes, name)).fetchall()
    if not rows:
        return {"results": [], "searched": 0, "ms": 0, "device": "cpu"}
    q = embed([query])
    mats = [(r, np.frombuffer(r["vec"], dtype="float32")) for r in rows if r["dim"] == q["vectors"].shape[1]]
    if meta_filter:
        def ok(r):
            m = json.loads(r["meta"] or "{}")
            return all(m.get(k) == v for k, v in meta_filter.items())
        mats = [(r, v) for r, v in mats if ok(r)]
    if not mats:
        return {"results": [], "searched": 0, "ms": q["ms"], "device": q["device"]}
    M = np.vstack([v for _, v in mats])
    scores = M @ q["vectors"][0]
    order = np.argsort(-scores)[: max(1, min(int(limit), 50))]
    out = []
    for i in order:
        if scores[i] < threshold:
            break
        r = mats[i][0]
        out.append({"score": round(float(scores[i]), 4), "text": r["text"], "source": r["source"], "label": r["label"], "scope": r["scope"],
                    "ord": r["ord"], "meta": json.loads(r["meta"] or "{}")})
    return {"results": out, "searched": len(mats), "ms": q["ms"], "device": q["device"], "model": name}


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]
