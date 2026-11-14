"""Import chats from this app's own export, ChatGPT's conversations.json and Claude's conversations.json.

Everything becomes ordinary chats with a message tree, so branches from ChatGPT's "edit" and "regenerate" survive.
Imported content is plain text; nothing from the file is executed or trusted as HTML.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import db, library

MAX_CHATS = 2000
MAX_TEXT = 200_000


class ImportError_(ValueError):
    pass


def _text(value: Any) -> str:
    return value[:MAX_TEXT] if isinstance(value, str) else ""


def _new_chat(title: str, model: Optional[str]) -> str:
    chat = db.create_chat(model)
    db.update_chat(chat["id"], title=(_text(title).strip() or "Imported chat")[:120], title_state=2)
    return chat["id"]


# -------------------------------------------------------------- this app
def _import_own(chat: dict) -> Optional[str]:
    msgs = [m for m in chat.get("messages") or [] if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not msgs:
        return None
    cid = _new_chat(chat.get("title"), chat.get("model"))
    idmap: dict[Any, int] = {}
    for m in msgs:  # exported oldest-first, so parents come before children
        parent = idmap.get(m.get("parent_id"))
        idmap[m.get("id")] = db.add_message(
            cid, m["role"], _text(m.get("content")), parent_id=parent, model=m.get("model"),
            status=m.get("status") if m.get("status") in ("done", "stopped", "length", "error") else "done",
            think_ms=m.get("think_ms"), tokens_in=m.get("tokens_in"), tokens_out=m.get("tokens_out"),
            meta=m.get("meta") if isinstance(m.get("meta"), dict) and m["meta"] else None,
        )
    leaf = idmap.get(chat.get("leaf_id")) or list(idmap.values())[-1]
    db.update_chat(cid, leaf_id=leaf)
    return cid


# --------------------------------------------------------------- ChatGPT
def _chatgpt_text(msg: dict) -> str:
    content = msg.get("content") or {}
    parts = content.get("parts") if isinstance(content, dict) else None
    if isinstance(parts, list):
        return "\n".join(p for p in parts if isinstance(p, str)).strip()
    return _text(content.get("text")) if isinstance(content, dict) else ""


def _import_chatgpt(conv: dict) -> Optional[str]:
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        return None
    roots = [k for k, v in mapping.items() if isinstance(v, dict) and not v.get("parent")]
    if not roots:
        return None
    cid: Optional[str] = None
    new_ids: dict[str, int] = {}
    last_kept: dict[str, Optional[int]] = {}  # node -> nearest kept ancestor (new id)

    stack = [(r, None) for r in reversed(roots)]
    while stack:
        key, kept_parent = stack.pop()
        node = mapping.get(key)
        if not isinstance(node, dict):
            continue
        msg = node.get("message") if isinstance(node.get("message"), dict) else None
        role = ((msg or {}).get("author") or {}).get("role")
        text = _chatgpt_text(msg) if msg else ""
        parent_for_children = kept_parent
        if msg and role in ("user", "assistant") and text and not (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            if cid is None:
                cid = _new_chat(conv.get("title"), None)
            model = (msg.get("metadata") or {}).get("model_slug") if role == "assistant" else None
            new_ids[key] = db.add_message(cid, role, _text(text), parent_id=kept_parent, model=model)
            parent_for_children = new_ids[key]
        last_kept[key] = parent_for_children
        for child in reversed(node.get("children") or []):
            stack.append((child, parent_for_children))
    if cid is None:
        return None
    leaf = last_kept.get(conv.get("current_node")) or (list(new_ids.values())[-1] if new_ids else None)
    if leaf:
        db.update_chat(cid, leaf_id=leaf)
    return cid


# ---------------------------------------------------------------- Claude
def _import_claude(conv: dict) -> Optional[str]:
    items = conv.get("chat_messages")
    if not isinstance(items, list):
        return None
    cid: Optional[str] = None
    parent: Optional[int] = None
    for m in items:
        if not isinstance(m, dict):
            continue
        role = {"human": "user", "assistant": "assistant"}.get(m.get("sender"))
        text = _text(m.get("text"))
        if not text and isinstance(m.get("content"), list):
            text = "\n".join(_text(c.get("text")) for c in m["content"] if isinstance(c, dict))
        if not role or not text.strip():
            continue
        if cid is None:
            cid = _new_chat(conv.get("name"), None)
        parent = db.add_message(cid, role, text, parent_id=parent)
    return cid


# --------------------------------------------------------------- the door
def import_data(data: Any) -> dict:
    """-> {"chats": n, "memories": n, "assistants": n, "projects": n}"""
    out = {"chats": 0, "memories": 0, "assistants": 0, "projects": 0}
    if isinstance(data, dict) and data.get("local_chat_library"):
        lib = data["local_chat_library"]
        for m in lib.get("memories") or []:
            if isinstance(m, dict) and library.add_memory(m.get("text", ""), "import")[1] == "saved":
                out["memories"] += 1
        for a in lib.get("assistants") or []:
            if isinstance(a, dict) and library.save_assistant({**a, "id": None}):
                out["assistants"] += 1
        for p in lib.get("projects") or []:
            if isinstance(p, dict) and library.save_project(p):
                out["projects"] += 1
        return out
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else None
    if not items:
        raise ImportError_("That file isn't a list of chats.")
    for conv in items[:MAX_CHATS]:
        if not isinstance(conv, dict):
            continue
        if "mapping" in conv:
            cid = _import_chatgpt(conv)
        elif "chat_messages" in conv:
            cid = _import_claude(conv)
        elif isinstance(conv.get("messages"), list):
            cid = _import_own(conv)
        else:
            cid = None
        out["chats"] += 1 if cid else 0
    if not out["chats"]:
        raise ImportError_(
            "No chats found. Supported files: this app's JSON export, ChatGPT's conversations.json, Claude's conversations.json."
        )
    return out


def parse(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ImportError_("That file isn't valid JSON.") from None


def export_library() -> dict:
    return {
        "local_chat_library": {
            "memories": [{"text": m["text"]} for m in library.list_memories()],
            "assistants": [
                {k: a[k] for k in ("name", "description", "prompt", "model")} for a in library.list_assistants()
            ],
            "projects": [{"name": p["name"], "instructions": p["instructions"]} for p in library.list_projects()],
        }
    }
