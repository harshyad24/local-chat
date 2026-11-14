"""Vector storage behind one small interface, with two interchangeable backends.

* SqliteStore - the app's own store: vectors are float32 BLOBs in the `chunks` table and search is an exact dot product over every
  row in the scope (vectors are L2-normalised, so the dot product is the cosine). Exact, dependency-free, fast enough for tens of
  thousands of passages.
* ChromaStore - a real vector database (Chroma, HNSW index on disk, metadata filtering). Optional: needs `pip install chromadb`.

Both return the same shape: [{page, page_end, text, score}] best first. The embeddings are always computed by embeddings.py and are
identical in both stores, so a comparison between them measures the store, not the model."""
from __future__ import annotations

import re
from typing import Any, Optional

from . import db, embeddings


class SqliteStore:
    name = "sqlite"

    def available(self) -> bool:
        return True

    def count(self, scope: str) -> int:
        return embeddings.count(scope)

    def remove(self, scope: str) -> None:
        embeddings.remove(scope)

    def search(self, scope: str, qvec, k: int = 8, span: Optional[tuple[int, int]] = None) -> list[dict]:
        import json
        import numpy as np
        embeddings._init()
        with db.conn() as c:
            rows = c.execute("SELECT text, meta, dim, vec FROM chunks WHERE scope=? AND model=?", (scope, embeddings.model_name())).fetchall()
        out = []
        for r in rows:
            m = json.loads(r["meta"] or "{}")
            lo, hi = m.get("page") or 0, m.get("page_end") or m.get("page") or 0
            if span and not (hi >= span[0] and lo <= span[1]):
                continue
            if r["dim"] != len(qvec):
                continue
            out.append({"page": lo, "page_end": hi, "text": r["text"], "score": float(np.frombuffer(r["vec"], dtype="float32") @ qvec)})
        return sorted(out, key=lambda h: -h["score"])[:k]


class ChromaStore:
    name = "chroma"

    def __init__(self, path: Optional[str] = None, client: Any = None) -> None:
        self._path, self._client = path, client

    def _chroma(self):
        if self._client is None:
            import chromadb  # noqa: WPS433 - optional dependency, imported on first use
            self._client = chromadb.PersistentClient(path=self._path or str(db.DATA_DIR / "chroma"))
        return self._client

    def available(self) -> bool:
        try:
            self._chroma()
            return True
        except Exception:  # noqa: BLE001 - not installed, or the OS blocked one of its libraries
            return False

    @staticmethod
    def _col_name(scope: str) -> str:
        n = re.sub(r"[^A-Za-z0-9_-]", "_", scope)
        return (n if len(n) >= 3 else n + "___")[:63]

    def _col(self, scope: str):
        return self._chroma().get_or_create_collection(self._col_name(scope), metadata={"hnsw:space": "cosine"})

    def count(self, scope: str) -> int:
        return self._col(scope).count()

    def remove(self, scope: str) -> None:
        try:
            self._chroma().delete_collection(self._col_name(scope))
        except Exception:  # noqa: BLE001 - nothing to delete
            pass

    def add(self, scope: str, items: list[tuple[str, dict, Any]], start: int = 0) -> None:
        col = self._col(scope)
        for i in range(0, len(items), 500):
            part = items[i:i + 500]
            col.add(ids=[f"{scope}:{start + i + j}" for j in range(len(part))], documents=[t for t, _, _ in part],
                    embeddings=[[float(x) for x in v] for _, _, v in part],
                    metadatas=[{"page": int(m.get("page") or 0), "page_end": int(m.get("page_end") or m.get("page") or 0)} for _, m, _ in part])

    def search(self, scope: str, qvec, k: int = 8, span: Optional[tuple[int, int]] = None) -> list[dict]:
        where = {"$and": [{"page_end": {"$gte": span[0]}}, {"page": {"$lte": span[1]}}]} if span else None
        res = self._col(scope).query(query_embeddings=[[float(x) for x in qvec]], n_results=max(1, int(k)), where=where)
        docs, metas, dists = (res.get("documents") or [[]])[0], (res.get("metadatas") or [[]])[0], (res.get("distances") or [[]])[0]
        return [{"page": m.get("page"), "page_end": m.get("page_end"), "text": d, "score": 1.0 - float(x)} for d, m, x in zip(docs, metas, dists)]


_CHROMA: Optional[ChromaStore] = None


def get_store(name: Optional[str] = None):
    """The backend chosen in settings (vector_backend); falls back to SQLite when Chroma cannot run, so search never breaks."""
    global _CHROMA
    want = name or (db.get_settings().get("vector_backend") or "sqlite")
    if want == "chroma":
        _CHROMA = _CHROMA or ChromaStore()
        if _CHROMA.available():
            return _CHROMA
    return SqliteStore()


def mirror(scope: str, target: ChromaStore) -> int:
    """Copy a scope's stored vectors from SQLite into another store without embedding anything again. -> passages copied."""
    import json
    import numpy as np
    embeddings._init()
    with db.conn() as c:
        rows = c.execute("SELECT text, meta, vec FROM chunks WHERE scope=? AND model=? ORDER BY ord", (scope, embeddings.model_name())).fetchall()
    if target.count(scope) == len(rows):
        return 0
    target.remove(scope)
    target.add(scope, [(r["text"], json.loads(r["meta"] or "{}"), np.frombuffer(r["vec"], dtype="float32")) for r in rows])
    return len(rows)
