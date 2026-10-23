"""Chat sessions, stored so they survive restarts and can be reopened.

A session keeps two things:
- the model-facing state (history, tier, pin), saved after every turn, so a
  reopened session continues with full context;
- an event log of everything the UI was sent for it, plus the user's prompts,
  in order. Reopening replays these events through the same UI code as a
  live chat, so a restored session looks exactly as it did.
Sessions are only removed by delete(), which also removes their events.
Usage rows (cost data) are never deleted.
"""
import json
import time
import uuid

from optimux_backend.core import store
from optimux_backend.core.security import mask_secrets


def _stored(obj) -> str:
    """JSON for the database, with saved keys hidden: the last safety net (IM-02b)
    behind the hiding in command output, prompts and model replies."""
    return mask_secrets(json.dumps(obj))[0]

TITLE_LENGTH = 80


def create(project: str, first_prompt: str = "") -> str:
    sid = uuid.uuid4().hex
    title = " ".join(first_prompt.split())[:TITLE_LENGTH]
    now = time.time()
    conn = store.connect()
    try:
        with conn:
            conn.execute("INSERT INTO sessions (id, project, title, created, updated) VALUES (?, ?, ?, ?, ?)",
                         (sid, project or "", title, now, now))
    finally:
        conn.close()
    return sid


def exists(sid: str) -> bool:
    conn = store.connect()
    try:
        return conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone() is not None
    finally:
        conn.close()


def append_events(sid: str, events: list[dict]) -> None:
    if not events:
        return
    now = time.time()
    conn = store.connect()
    try:
        with conn:
            conn.executemany("INSERT INTO session_events (session_id, ts, data) VALUES (?, ?, ?)",
                             [(sid, now, _stored(e)) for e in events])
            conn.execute("UPDATE sessions SET updated = ? WHERE id = ?", (now, sid))
    finally:
        conn.close()


def save_state(sid: str, history: list, tier: str | None, tier_pinned: bool,
               last_turn_ok: bool, turns_added: int = 0) -> None:
    conn = store.connect()
    try:
        with conn:
            conn.execute(
                "UPDATE sessions SET history = ?, tier = ?, tier_pinned = ?, last_turn_ok = ?, "
                "turns = turns + ?, updated = ? WHERE id = ?",
                (_stored(history), tier, int(tier_pinned), int(last_turn_ok), turns_added, time.time(), sid))
    finally:
        conn.close()


_LIST_SQL = """
    SELECT s.id, s.project, s.title, s.created, s.updated, s.archived, s.turns, s.tier,
           COALESCE((SELECT SUM(u.cost) FROM usage u WHERE u.session = s.id), 0) AS cost,
           COALESCE((SELECT SUM(CASE WHEN u.cost IS NULL THEN 1 ELSE 0 END) FROM usage u WHERE u.session = s.id), 0)
               AS calls_without_cost
    FROM sessions s
"""


def list_sessions(project: str | None = None, archived: bool = False, query: str = "", limit: int = 200) -> list[dict]:
    """Newest first. project=None lists all projects."""
    where, args = ["s.archived = ?"], [int(archived)]
    if project is not None:
        where.append("s.project = ?")
        args.append(project)
    if query.strip():
        where.append("s.title LIKE ?")
        args.append(f"%{query.strip()}%")
    conn = store.connect()
    try:
        rows = conn.execute(_LIST_SQL + " WHERE " + " AND ".join(where) + " ORDER BY s.updated DESC LIMIT ?",
                            [*args, limit]).fetchall()
    finally:
        conn.close()
    return [dict(r) | {"archived": bool(r["archived"])} for r in rows]


def get(sid: str) -> dict | None:
    """Session with its model state and event log, or None."""
    conn = store.connect()
    try:
        row = conn.execute(_LIST_SQL + " WHERE s.id = ?", (sid,)).fetchone()
        if row is None:
            return None
        state = conn.execute("SELECT history, tier_pinned, last_turn_ok FROM sessions WHERE id = ?", (sid,)).fetchone()
        events = [json.loads(r["data"]) for r in
                  conn.execute("SELECT data FROM session_events WHERE session_id = ? ORDER BY id", (sid,))]
    finally:
        conn.close()
    return dict(row) | {
        "archived": bool(row["archived"]),
        "history": json.loads(state["history"]),
        "tier_pinned": bool(state["tier_pinned"]),
        "last_turn_ok": bool(state["last_turn_ok"]),
        "events": events,
    }


def project_counts() -> dict:
    """Per project: number of (non-archived) chats and the last update."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT project, SUM(CASE WHEN archived = 0 THEN 1 ELSE 0 END) AS chats, "
                            "MAX(updated) AS last_updated FROM sessions GROUP BY project").fetchall()
    finally:
        conn.close()
    return {r["project"]: {"chats": r["chats"], "last_updated": r["last_updated"]} for r in rows}


def latest(project: str) -> str | None:
    conn = store.connect()
    try:
        row = conn.execute("SELECT id FROM sessions WHERE project = ? AND archived = 0 ORDER BY updated DESC LIMIT 1",
                           (project or "",)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def rename(sid: str, title: str) -> bool:
    return _update(sid, "title = ?", (" ".join(title.split())[:200],))


def set_archived(sid: str, archived: bool) -> bool:
    return _update(sid, "archived = ?", (int(archived),))


def _update(sid: str, assignment: str, args: tuple) -> bool:
    conn = store.connect()
    try:
        with conn:
            cur = conn.execute(f"UPDATE sessions SET {assignment} WHERE id = ?", (*args, sid))
        return cur.rowcount > 0
    finally:
        conn.close()


def delete(sid: str) -> bool:
    """Permanently remove a session and its events. Its usage rows are kept."""
    conn = store.connect()
    try:
        with conn:
            conn.execute("DELETE FROM session_events WHERE session_id = ?", (sid,))
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        return cur.rowcount > 0
    finally:
        conn.close()


class Recorder:
    """Collects what the UI is sent for one request and stores it with the
    session. Consecutive token / thinking chunks are merged into one event.
    Events sent before the request has a session are kept until it does."""

    MERGE = ("token", "thinking")
    SKIP = ("done", "auth_ok", "approval_request", "chat_reset", "session_loaded", "session_started", "stopped",
            "project_created",   # a toast: only for the page that's watching
            "prompt_masked",     # the stored prompt is already the hidden version
            "command_limit", "command_limit_done")   # the keep-waiting question, live only

    def __init__(self):
        self.pending: list[dict] = []

    def add(self, payload: dict) -> None:
        kind = payload.get("type")
        if kind in self.SKIP:
            return
        last = self.pending[-1] if self.pending else None
        if kind in self.MERGE and last is not None and last.get("type") == kind:
            last["content"] = last.get("content", "") + (payload.get("content") or "")
            return
        self.pending.append(dict(payload))

    def flush(self, sid: str | None) -> None:
        if sid and self.pending:
            append_events(sid, self.pending)
        self.pending = []
