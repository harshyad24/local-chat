"""Run state, stored in SQLite so a workflow survives a restart and can be continued or revised later."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from .. import db


def _row(r) -> dict:
    d = dict(r)
    d["slots"] = json.loads(d.get("slots") or "{}")
    d["state"] = json.loads(d.get("state") or "{}")
    return d


def create(chat_id: str, skill: str, workflow: str, request: str, slots: Optional[dict] = None) -> dict:
    rid = uuid.uuid4().hex[:12]
    now = time.time()
    with db.conn() as c:
        c.execute("INSERT INTO skill_runs(id, chat_id, skill, workflow, status, request, slots, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (rid, chat_id, skill, workflow, "asking", request, json.dumps(slots or {}, ensure_ascii=False), "{}", now, now))
    return get(rid)  # type: ignore[return-value]


def get(rid: str) -> Optional[dict]:
    with db.conn() as c:
        r = c.execute("SELECT * FROM skill_runs WHERE id=?", (rid,)).fetchone()
    return _row(r) if r else None


def update(rid: str, **fields: Any) -> Optional[dict]:
    sets, vals = [], []
    for k, v in fields.items():
        if k in ("slots", "state"):
            v = json.dumps(v, ensure_ascii=False)
        sets.append(f"{k}=?")
        vals.append(v)
    sets.append("updated_at=?")
    vals += [time.time(), rid]
    with db.conn() as c:
        c.execute(f"UPDATE skill_runs SET {', '.join(sets)} WHERE id=?", vals)
    return get(rid)


def latest(chat_id: str, statuses: tuple[str, ...] = ("asking",)) -> Optional[dict]:
    marks = ",".join("?" * len(statuses))
    with db.conn() as c:
        r = c.execute(f"SELECT * FROM skill_runs WHERE chat_id=? AND status IN ({marks}) ORDER BY updated_at DESC LIMIT 1", (chat_id, *statuses)).fetchone()
    return _row(r) if r else None


def set_state(rid: str, key: str, value: Any) -> Optional[dict]:
    run = get(rid)
    if not run:
        return None
    st = run["state"]
    st[key] = value
    return update(rid, state=st)
