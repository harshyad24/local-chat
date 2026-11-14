"""SQLite storage: chats, message tree, settings.

Messages form a tree (messages.parent_id). Each chat stores `leaf_id`, the tip of the
branch that is currently selected; the conversation shown/sent to the model is the
path from the root down to that leaf.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "chat.db"

DEFAULT_SETTINGS: dict[str, Any] = {
    # general
    "theme": "system",
    "font_size": 16,
    "language": "Auto",
    "send_on_enter": True,
    "display_name": "",
    # models
    "provider": "ollama",  # "ollama" | "openai"
    "ollama_url": "http://localhost:11434",
    "openai_url": "http://localhost:1234/v1",
    "openai_key": "",
    "default_model": "",
    "temperature": 0.7,
    "top_p": 0.9,
    "num_ctx": 8192,
    "max_tokens": 0,  # 0 = no limit
    "keep_alive": "5m",
    "thinking": True,  # ask reasoning models to think (Ollama `think` parameter)
    # tools
    "use_web": False,  # "+" menu switch: let the model search the web
    "use_research": False,  # "+" menu switch: run the deep research workflow for the next message (resets after sending)
    "tool_web_search": True,
    "tool_fetch_page": True,
    "tool_read_file": True,
    "tool_run_python": True,  # built-in code execution (always available; no composer button)
    "tool_datetime": True,
    "tool_search_chat": True,  # let the model search earlier messages of the same chat
    "search_provider": "ddgs",  # ddgs | searxng | tavily | brave
    "searxng_url": "",
    "tavily_key": "",
    "brave_key": "",
    "search_results": 6,
    "page_char_limit": 8000,
    "code_timeout": 30,
    "confirm_code": True,  # ask before every run
    # connections
    "mcp_servers": "[]",  # JSON list of MCP servers (see backend/mcp_client.py)
    "n8n_url": "http://localhost:5678",
    "n8n_api_key": "",
    "tool_convert": True,  # LibreOffice document conversion (only offered when LibreOffice is installed)
    # skills (/planning /compare /analyze /create /learn)
    "skills_enabled": True,
    "skill_max_items": 4,  # most things compared at once (each one costs a search and a model call)
    # accuracy
    "ground_first": True,  # factual / time-sensitive questions: the app searches the web before the model answers
    "js_render": "auto",  # "auto" | "always" | "off": read JavaScript-built / bot-blocked pages with Crawl4AI in your installed Edge/Chrome
    "ground_pages": 1,  # how many top pages the app reads in addition to the result snippets
    "verify_answers": "fix",  # "off" | "flag" (mark unsupported claims) | "fix" (also rewrite without them)
    # local AI
    "entity_cards": True,  # visual knowledge cards for named people / products / places (needs web search on)
    "stt_model": "small",  # faster-whisper size: tiny, base, small, medium
    "stt_language": "auto",
    "stt_cleanup": False,  # remove filler words (um, uh) from dictation
    "stt_hinglish": False,  # Hindi spoken, text written in Latin letters
    "embed_model": "",  # empty = the default multilingual MiniLM
    "cards_new_per_day": 15,  # how many brand-new flashcards a day the review session adds to your due ones
    "plugins_paused": False,  # security switch: while on, no plugin tool is offered to the model
    "plugin_consent": True,  # security: ask once per chat before the assistant first uses a plugin (Mail, Calendar, Folder)
    "vector_backend": "sqlite",  # "sqlite" (built in, exact search) | "chroma" (vector database; pip install chromadb)
    "agent_engine": "builtin",  # who runs the chart/dashboard builder flow: "builtin" loop | "langgraph" graph (same rules, same results)
    # memory
    "memory_enabled": True,  # remembered facts are added to every chat; "remember that …" saves one
    "memory_auto": False,  # also let the model note lasting facts after each reply (costs one short extra call)
    # personalization
    "about_you": "",
    "response_style": "",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    title_state INTEGER NOT NULL DEFAULT 0,   -- 0 default, 1 provisional, 2 final
    model TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    project_id TEXT,
    leaf_id INTEGER,                          -- tip of the selected branch
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    parent_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'done',      -- done | stopped | length | error
    think_ms INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    feedback TEXT,                            -- 'up' | 'down' | NULL
    feedback_note TEXT,
    attachments TEXT,                         -- JSON list of file metadata
    meta TEXT,                                -- JSON: {"tools": [...], "sources": [...]}
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mime TEXT,
    size INTEGER,
    kind TEXT,
    path TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',      -- user | model | auto | import
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assistants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    model TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_files (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    PRIMARY KEY (project_id, file_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_runs (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    skill TEXT NOT NULL,                      -- planning | compare | analyze | create | learn
    workflow TEXT NOT NULL,                   -- internal workflow id, e.g. travel
    status TEXT NOT NULL DEFAULT 'asking',    -- asking | running | done | error | abandoned
    request TEXT NOT NULL DEFAULT '',
    slots TEXT NOT NULL DEFAULT '{}',         -- JSON: what has been collected
    state TEXT NOT NULL DEFAULT '{}',         -- JSON: progress (checked sessions, quiz scores ...)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_runs_chat ON skill_runs(chat_id, updated_at);
CREATE TABLE IF NOT EXISTS learn_cards (            -- flashcards (spaced repetition, SM-2) belonging to a /learn course
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL,                  -- skill_runs.id of the course
    module TEXT NOT NULL DEFAULT '',
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'lesson',
    ease REAL NOT NULL DEFAULT 2.5,
    interval INTEGER NOT NULL DEFAULT 0,      -- days
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    due REAL NOT NULL,
    created REAL NOT NULL,
    last_reviewed REAL
);
CREATE INDEX IF NOT EXISTS idx_learn_cards_due ON learn_cards(due);
CREATE INDEX IF NOT EXISTS idx_learn_cards_course ON learn_cards(course_id);
CREATE TABLE IF NOT EXISTS learn_events (           -- what the learner did: quiz | card | lab | math | lesson (counted for streaks and progress)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT,
    kind TEXT NOT NULL,
    module TEXT NOT NULL DEFAULT '',
    right_n INTEGER NOT NULL DEFAULT 0,
    total_n INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '{}',
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learn_events_ts ON learn_events(ts);
CREATE INDEX IF NOT EXISTS idx_learn_events_course ON learn_events(course_id, ts);
CREATE TABLE IF NOT EXISTS plugin_log (             -- what plugins were asked to do (never the content they returned)
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    server TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    chars INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_plugin_log_server ON plugin_log(server, ts);
CREATE TABLE IF NOT EXISTS skill_prefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill TEXT NOT NULL DEFAULT '*',          -- '*' = every skill
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',          -- the feedback text it came from
    created_at REAL NOT NULL,
    UNIQUE(skill, key)
);
"""

# Columns added after Phase 1, applied to existing databases.
MIGRATIONS = [
    ("chats", "leaf_id", "INTEGER"),
    ("chats", "assistant_id", "TEXT"),
    ("messages", "feedback", "TEXT"),
    ("messages", "feedback_note", "TEXT"),
    ("messages", "attachments", "TEXT"),
    ("messages", "meta", "TEXT"),
    ("learn_cards", "kind", "TEXT NOT NULL DEFAULT 'basic'"),    # basic | cloze (a sentence with the key term blanked out)
    ("learn_cards", "page", "INTEGER"),                          # book page the card came from
    ("learn_cards", "hint", "TEXT NOT NULL DEFAULT ''"),
    ("learn_cards", "suspended", "INTEGER NOT NULL DEFAULT 0"),  # 1 = never shown (a leech you gave up on)
    ("learn_cards", "first_seen", "REAL"),                       # when the card was first reviewed (drives the daily limit on new cards)
]


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        # cards reviewed before first_seen existed count as already introduced (so they stay in the review queue, not the "new" limit)
        c.execute("UPDATE learn_cards SET first_seen=last_reviewed WHERE first_seen IS NULL AND last_reviewed IS NOT NULL")


# ---------------------------------------------------------------- settings
def get_settings() -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    with conn() as c:
        for row in c.execute("SELECT key, value FROM settings"):
            if row["key"] in out:
                try:
                    out[row["key"]] = json.loads(row["value"])
                except ValueError:
                    pass
    return out


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        return int(float(value))
    if isinstance(default, float):
        return float(value)
    return "" if value is None else str(value)


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    with conn() as c:
        for key, value in patch.items():
            if key not in DEFAULT_SETTINGS:
                continue
            try:
                value = _coerce(key, value)
            except (TypeError, ValueError):
                continue
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
    return get_settings()


# ------------------------------------------------------------------- chats
def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_chats(q: str = "", archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM chats WHERE archived=?"
    params: list[Any] = [1 if archived else 0]
    if q:
        sql += (
            " AND (title LIKE ? ESCAPE '\\' OR id IN "
            "(SELECT chat_id FROM messages WHERE content LIKE ? ESCAPE '\\'))"
        )
        params += [_like(q), _like(q)]
    sql += " ORDER BY pinned DESC, updated_at DESC"
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params)]


def create_chat(model: Optional[str] = None) -> dict:
    now = time.time()
    cid = uuid.uuid4().hex[:16]
    with conn() as c:
        c.execute(
            "INSERT INTO chats(id, title, model, created_at, updated_at) VALUES(?,?,?,?,?)",
            (cid, "New chat", model, now, now),
        )
    return get_chat(cid)  # type: ignore[return-value]


def get_chat(chat_id: str) -> Optional[dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    return dict(row) if row else None


_CHAT_FIELDS = {"title", "title_state", "model", "pinned", "archived", "project_id", "leaf_id", "assistant_id"}


def update_chat(chat_id: str, **fields: Any) -> Optional[dict]:
    fields = {k: v for k, v in fields.items() if k in _CHAT_FIELDS}
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        with conn() as c:
            c.execute(f"UPDATE chats SET {sets} WHERE id=?", [*fields.values(), chat_id])
    return get_chat(chat_id)


def chat_dir(chat_id: str) -> Path:
    return DATA_DIR / "chats" / chat_id


def work_dir(chat_id: str) -> Path:
    """Per-chat working folder used by the code interpreter."""
    p = chat_dir(chat_id) / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p


def delete_chat(chat_id: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM chats WHERE id=?", (chat_id,))
    shutil.rmtree(chat_dir(chat_id), ignore_errors=True)


def delete_all_chats() -> None:
    with conn() as c:
        c.execute("DELETE FROM messages")
        c.execute("DELETE FROM chats")
    shutil.rmtree(DATA_DIR / "chats", ignore_errors=True)


# ------------------------------------------------------------------- files
def add_file(name: str, mime: str, size: int, kind: str, path: str, file_id: str) -> dict:
    with conn() as c:
        c.execute(
            "INSERT INTO files(id, name, mime, size, kind, path, created_at) VALUES(?,?,?,?,?,?,?)",
            (file_id, name, mime, size, kind, path, time.time()),
        )
    return get_file(file_id)  # type: ignore[return-value]


def get_file(file_id: str) -> Optional[dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- messages
def _decode(row: Any) -> dict:
    m = dict(row)
    for key, default in (("attachments", []), ("meta", {})):
        raw = m.get(key)
        try:
            m[key] = json.loads(raw) if raw else default
        except ValueError:
            m[key] = default
    return m


def get_messages(chat_id: str) -> list[dict]:
    """Every message in the chat (all branches), oldest first."""
    with conn() as c:
        return [
            _decode(r)
            for r in c.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY id", (chat_id,))
        ]


def get_message(message_id: int) -> Optional[dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    return _decode(row) if row else None


def _children(msgs: list[dict]) -> dict[Optional[int], list[int]]:
    kids: dict[Optional[int], list[int]] = defaultdict(list)
    for m in msgs:  # msgs are ordered by id, so each list is oldest -> newest
        kids[m["parent_id"]].append(m["id"])
    return kids


def get_path(chat_id: str) -> list[dict]:
    """Messages of the selected branch (root -> leaf), each annotated with its
    sibling versions: `siblings` (ids) and `sib_index`."""
    chat = get_chat(chat_id)
    if not chat:
        return []
    msgs = get_messages(chat_id)
    if not msgs:
        return []
    by_id = {m["id"]: m for m in msgs}
    kids = _children(msgs)
    leaf = chat.get("leaf_id")
    if leaf not in by_id:
        leaf = msgs[-1]["id"]
    path: list[dict] = []
    cur: Optional[int] = leaf
    while cur is not None and cur in by_id:
        path.append(dict(by_id[cur]))
        cur = by_id[cur]["parent_id"]
    path.reverse()
    for m in path:
        sibs = kids[m["parent_id"]]
        m["siblings"] = sibs
        m["sib_index"] = sibs.index(m["id"])
    return path


def set_leaf(chat_id: str, message_id: int) -> None:
    """Select the branch containing `message_id`: follow the newest child down to a leaf."""
    msgs = get_messages(chat_id)
    kids = _children(msgs)
    cur = message_id
    while kids.get(cur):
        cur = kids[cur][-1]
    update_chat(chat_id, leaf_id=cur)


def add_message(
    chat_id: str,
    role: str,
    content: str,
    *,
    parent_id: Optional[int],
    model: Optional[str] = None,
    status: str = "done",
    think_ms: Optional[int] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    attachments: Optional[list] = None,
    meta: Optional[dict] = None,
) -> int:
    now = time.time()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO messages(chat_id, parent_id, role, content, model, status, think_ms,"
            " tokens_in, tokens_out, attachments, meta, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                chat_id, parent_id, role, content, model, status, think_ms, tokens_in, tokens_out,
                json.dumps(attachments) if attachments else None,
                json.dumps(meta) if meta else None,
                now,
            ),
        )
        new_id = int(cur.lastrowid)
        c.execute("UPDATE chats SET updated_at=?, leaf_id=? WHERE id=?", (now, new_id, chat_id))
        return new_id


_MSG_FIELDS = {"content", "status", "think_ms", "tokens_in", "tokens_out", "feedback", "feedback_note", "meta"}


def update_message(message_id: int, **fields: Any) -> Optional[dict]:
    fields = {k: v for k, v in fields.items() if k in _MSG_FIELDS}
    if "meta" in fields and not isinstance(fields["meta"], str):
        fields["meta"] = json.dumps(fields["meta"]) if fields["meta"] else None
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        with conn() as c:
            c.execute(f"UPDATE messages SET {sets} WHERE id=?", [*fields.values(), message_id])
    return get_message(message_id)


# ------------------------------------------------------------------- stats
def stats() -> dict:
    with conn() as c:
        rows = c.execute(
            "SELECT COALESCE(model,'unknown') AS model, COUNT(*) AS messages,"
            " COALESCE(SUM(tokens_in),0) AS tokens_in, COALESCE(SUM(tokens_out),0) AS tokens_out"
            " FROM messages WHERE role='assistant' GROUP BY model ORDER BY messages DESC"
        ).fetchall()
        total_chats = c.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        user_msgs = c.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0]
        up = c.execute("SELECT COUNT(*) FROM messages WHERE feedback='up'").fetchone()[0]
        down = c.execute("SELECT COUNT(*) FROM messages WHERE feedback='down'").fetchone()[0]
    return {
        "models": [dict(r) for r in rows],
        "chats": total_chats,
        "user_messages": user_msgs,
        "thumbs_up": up,
        "thumbs_down": down,
    }


# ------------------------------------------------------------------ export
def export_chat(chat_id: str, tree: bool = False) -> Optional[dict]:
    """tree=False -> selected branch only (with sibling info); tree=True -> every message."""
    chat = get_chat(chat_id)
    if not chat:
        return None
    return {**chat, "messages": get_messages(chat_id) if tree else get_path(chat_id)}


def export_all() -> list[dict]:
    with conn() as c:
        ids = [r["id"] for r in c.execute("SELECT id FROM chats ORDER BY created_at")]
    return [e for e in (export_chat(i, tree=True) for i in ids) if e]


# ---------------------------------------------------------------- plugin activity log
_PLUGIN_LOG_DDL = ("CREATE TABLE IF NOT EXISTS plugin_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, server TEXT NOT NULL, tool TEXT NOT NULL, "
                   "args TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 1, chars INTEGER NOT NULL DEFAULT 0)")


def log_plugin_call(server: str, tool: str, args: dict, ok: bool, chars: int) -> None:
    """Record that a plugin tool was used. Only the tool and a short summary of the arguments are kept, never what was read."""
    summary = ", ".join(f"{k}={str(v)[:40]}" for k, v in (args or {}).items())[:160]
    with conn() as c:
        c.execute(_PLUGIN_LOG_DDL)
        c.execute("INSERT INTO plugin_log(ts, server, tool, args, ok, chars) VALUES(?,?,?,?,?,?)", (time.time(), server, tool, summary, int(ok), int(chars)))
        c.execute("DELETE FROM plugin_log WHERE id NOT IN (SELECT id FROM plugin_log ORDER BY id DESC LIMIT 500)")


def plugin_activity(server: Optional[str] = None, limit: int = 20) -> list[dict]:
    with conn() as c:
        c.execute(_PLUGIN_LOG_DDL)
        rows = c.execute("SELECT ts, server, tool, args, ok, chars FROM plugin_log" + (" WHERE server=?" if server else "") + " ORDER BY id DESC LIMIT ?",
                         ((server, limit) if server else (limit,))).fetchall()
    return [dict(r) for r in rows]
