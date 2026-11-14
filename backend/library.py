"""Memory, assistants and projects: the things that make the app remember and specialise.

Memory is deliberately not left to the model: the app saves "remember that …" itself, injects the saved facts into
every chat, and lets the user view, edit and delete them. A model can also save one through the save_memory tool.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Optional

from . import db

MAX_MEMORY_CHARS = 300
MAX_MEMORIES = 200
PROMPT_BUDGET_CHARS = 1600  # how much memory goes into one prompt
SAME_FACT_SIMILARITY = 0.93  # embedding cosine above which a new fact is a rewording / refinement of an old one
# Measured with the bundled MiniLM: rewordings and refinements score 0.96-0.99, but "I like tea" vs "I like coffee" scores 0.77 and
# "I live in Pune" vs "I moved to Mumbai" 0.64, so embeddings alone cannot tell a contradiction from a different fact. Anything
# below the threshold is therefore kept as a separate memory instead of risking the loss of a real fact.

_SECRET = re.compile(
    r"(?:password|passcode|api[_ -]?key|secret|token|credit card|card number|cvv|ssn|social security|private key)",
    re.I,
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


_FILLER = {"a", "an", "the", "is", "are", "am", "and", "user", "users", "i", "my", "me", "that", "has", "have", "to"}


def _tokens(text: str) -> set[str]:
    """Words reduced to a rough stem, so 'prefers' and 'prefer' match and filler words don't count."""
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _norm(text).split() if w not in _FILLER}


def _same_fact(a: str, b: str) -> bool:
    if _norm(a) == _norm(b):
        return True
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.75


# ------------------------------------------------------------------ memory
def list_memories() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM memories ORDER BY id DESC")]


def sync_index() -> None:
    """Make the vector index match the memories table (index new ones, drop deleted ones)."""
    from . import embeddings
    embeddings._init()
    with db.conn() as c:
        have = {r["source"] for r in c.execute("SELECT DISTINCT source FROM chunks WHERE scope='memory'")}
        want = {str(r["id"]): dict(r) for r in c.execute("SELECT * FROM memories")}
    for gone in have - set(want):
        embeddings.remove("memory", gone)
    for mid in set(want) - have:
        embeddings.index_text("memory", mid, want[mid]["text"], "memory")


def _reindex(row: dict) -> None:
    try:
        from . import embeddings
        if embeddings.available():
            embeddings.remove("memory", str(row["id"]))
            embeddings.index_text("memory", str(row["id"]), row["text"], "memory")
    except Exception:  # noqa: BLE001 - the index is a convenience; the memory itself is already saved
        pass


def _reworded(text: str, rows: list[dict]) -> Optional[dict]:
    """The existing memory that says (almost) the same thing in other words, found by meaning."""
    try:
        from . import embeddings
        if not rows or not embeddings.available():
            return None
        sync_index()
        hits = embeddings.search(text, "memory", limit=1, threshold=SAME_FACT_SIMILARITY)["results"]
        return next((r for r in rows if hits and str(r["id"]) == hits[0]["source"]), None)
    except Exception:  # noqa: BLE001
        return None


def add_memory(text: str, source: str = "user") -> tuple[Optional[dict], str]:
    """Returns (row, status) where status is 'saved' | 'duplicate' | 'empty' | 'secret' | 'full'.
    A fact that only rewords or refines an existing one replaces it ('saved', same id) instead of piling up."""
    text = re.sub(r"\s+", " ", (text or "")).strip().strip("\"'")[:MAX_MEMORY_CHARS]
    if len(text) < 3:
        return None, "empty"
    if _SECRET.search(text):
        return None, "secret"  # never store credentials, even when asked
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM memories")]
    for r in rows:
        if _same_fact(r["text"], text):
            if _tokens(text) > _tokens(r["text"]):  # the new wording adds detail ("engineer" -> "senior engineer"): keep the richer one
                return update_memory(r["id"], text) or r, "saved"
            return r, "duplicate"
    near = _reworded(text, rows)  # outside the connection: the embedding code opens its own
    if near is not None:
        if len(text) <= len(near["text"]) and _norm(text) in _norm(near["text"]):
            return near, "duplicate"
        row = update_memory(near["id"], text)
        if row:
            return row, "saved"
    if len(rows) >= MAX_MEMORIES:
        return None, "full"
    with db.conn() as c:
        cur = c.execute("INSERT INTO memories(text, source, created_at) VALUES(?,?,?)", (text, source, time.time()))
        row = dict(c.execute("SELECT * FROM memories WHERE id=?", (cur.lastrowid,)).fetchone())
    return row, "saved"


def update_memory(mid: int, text: str) -> Optional[dict]:
    text = re.sub(r"\s+", " ", (text or "")).strip()[:MAX_MEMORY_CHARS]
    if len(text) < 3 or _SECRET.search(text):
        return None
    with db.conn() as c:
        c.execute("UPDATE memories SET text=? WHERE id=?", (text, mid))
        row = c.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
    if row:
        _reindex(dict(row))
    return dict(row) if row else None


def delete_memory(mid: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM memories WHERE id=?", (mid,))


def clear_memories() -> None:
    with db.conn() as c:
        c.execute("DELETE FROM memories")


def forget_matching(query: str) -> list[dict]:
    """Delete the memories that best match a phrase like 'my dog's name'. Returns what was removed."""
    words = {w for w in _norm(query).split() if len(w) > 2}
    if not words:
        return []
    removed = []
    for m in list_memories():
        mw = set(_norm(m["text"]).split())
        # every meaningful word of the request appears in the memory (or the memory is mostly the request)
        if words <= mw or (len(words & mw) >= max(2, int(len(words) * 0.8))):
            removed.append(m)
    for m in removed:
        delete_memory(m["id"])
    return removed


def memory_lines(budget: int = PROMPT_BUDGET_CHARS) -> list[str]:
    """Most recent first, within a character budget, returned oldest-first for reading."""
    out, used = [], 0
    for m in list_memories():
        used += len(m["text"]) + 3
        if used > budget:
            break
        out.append(m["text"])
    return list(reversed(out))


_REMEMBER = re.compile(
    r"^\s*(?:(?:please|pls|hey|ok(?:ay)?|and)[,\s]+)*(?:remember|memori[sz]e|keep in mind|don'?t forget)"
    r"(?:\s+(?:that|this))?\s*[:,\-]?\s+(?P<fact>.{3,})$",
    re.I | re.S,
)
_FORGET = re.compile(
    r"^\s*(?:(?:please|pls|hey|ok(?:ay)?)[,\s]+)*forget\s+(?:about\s+|that\s+)?(?P<what>.{3,})$",
    re.I | re.S,
)


def _clean_fact(fact: str) -> str:
    fact = fact.strip().split("\n")[0].strip()
    fact = re.sub(r"\s*(?:thanks|thank you)\W*$", "", fact, flags=re.I)
    return fact.rstrip(".! ")


def detect_remember(message: str) -> Optional[str]:
    """'Remember that I prefer metric units' -> 'I prefer metric units'. Only matches a message that starts with it."""
    m = _REMEMBER.match(message or "")
    if not m:
        return None
    fact = _clean_fact(m.group("fact"))
    # "remember to buy milk" is a reminder, not a fact about the user, and the app has no reminders
    return None if len(fact) < 3 or re.match(r"to\s", fact, re.I) else fact


def detect_forget(message: str) -> Optional[str]:
    m = _FORGET.match(message or "")
    return _clean_fact(m.group("what")) if m else None


# --------------------------------------------------------------- assistants
def list_assistants() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM assistants ORDER BY name COLLATE NOCASE")]


def get_assistant(aid: Optional[str]) -> Optional[dict]:
    if not aid:
        return None
    with db.conn() as c:
        row = c.execute("SELECT * FROM assistants WHERE id=?", (aid,)).fetchone()
    return dict(row) if row else None


def save_assistant(data: dict, aid: Optional[str] = None) -> Optional[dict]:
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return None
    fields = (
        name,
        (data.get("description") or "").strip()[:200],
        (data.get("prompt") or "").strip()[:6000],
        (data.get("model") or "").strip() or None,
    )
    with db.conn() as c:
        if aid and c.execute("SELECT 1 FROM assistants WHERE id=?", (aid,)).fetchone():
            c.execute("UPDATE assistants SET name=?, description=?, prompt=?, model=? WHERE id=?", (*fields, aid))
        else:
            aid = uuid.uuid4().hex[:12]
            c.execute(
                "INSERT INTO assistants(id, name, description, prompt, model, created_at) VALUES(?,?,?,?,?,?)",
                (aid, *fields, time.time()),
            )
    return get_assistant(aid)


def delete_assistant(aid: str) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM assistants WHERE id=?", (aid,))
        c.execute("UPDATE chats SET assistant_id=NULL WHERE assistant_id=?", (aid,))


# ----------------------------------------------------------------- projects
def list_projects() -> list[dict]:
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")]
        for r in rows:
            r["chat_count"] = c.execute("SELECT COUNT(*) FROM chats WHERE project_id=?", (r["id"],)).fetchone()[0]
    return rows


def get_project(pid: Optional[str]) -> Optional[dict]:
    if not pid:
        return None
    with db.conn() as c:
        row = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        p = dict(row)
        p["files"] = [
            dict(r)
            for r in c.execute(
                "SELECT f.id, f.name, f.mime, f.size, f.kind FROM project_files pf JOIN files f ON f.id = pf.file_id "
                "WHERE pf.project_id=? ORDER BY f.name",
                (pid,),
            )
        ]
    return p


def save_project(data: dict, pid: Optional[str] = None) -> Optional[dict]:
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return None
    instructions = (data.get("instructions") or "").strip()[:8000]
    with db.conn() as c:
        if pid and c.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
            c.execute("UPDATE projects SET name=?, instructions=? WHERE id=?", (name, instructions, pid))
        else:
            pid = uuid.uuid4().hex[:12]
            c.execute(
                "INSERT INTO projects(id, name, instructions, created_at) VALUES(?,?,?,?)",
                (pid, name, instructions, time.time()),
            )
    return get_project(pid)


def delete_project(pid: str) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM project_files WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
        c.execute("UPDATE chats SET project_id=NULL WHERE project_id=?", (pid,))


def add_project_file(pid: str, file_id: str) -> None:
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO project_files(project_id, file_id) VALUES(?,?)", (pid, file_id))


def remove_project_file(pid: str, file_id: str) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM project_files WHERE project_id=? AND file_id=?", (pid, file_id))


# ------------------------------------------------- what goes into the prompt
def prompt_extras(chat: Optional[dict], s: dict) -> dict:
    """Everything the system prompt needs besides the settings: memory, assistant persona, project."""
    extras: dict = {}
    if s.get("memory_enabled", True):
        lines = memory_lines()
        if lines:
            extras["memories"] = lines
    if chat:
        a = get_assistant(chat.get("assistant_id"))
        if a and a["prompt"]:
            extras["assistant"] = {"name": a["name"], "prompt": a["prompt"]}
        p = get_project(chat.get("project_id"))
        if p:
            extras["project"] = {"name": p["name"], "instructions": p["instructions"], "files": p["files"]}
    return extras
