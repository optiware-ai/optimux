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

from optimux_backend.core import store, team
from optimux_backend.core.security import mask_obj, mask_secrets, secret_values, secrets_fingerprint


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
            conn.execute("INSERT INTO sessions (id, project, title, created, updated, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                         (sid, project or "", title, now, now, team.current_user_id()))
    finally:
        conn.close()
    return sid


def answers(sid: str) -> dict[int, str]:
    """What each kept model call wrote, read from the chat's log (v0.8): {sent id: text}. The page streams a
    call's text before the usage event that names the call, so the text after one usage event and before the
    next belongs to the second. Calls that wrote nothing are left out; saved keys are hidden."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT data FROM session_events WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
    finally:
        conn.close()
    out, text = {}, ""
    for r in rows:
        try:
            e = json.loads(r["data"])
        except (ValueError, TypeError):
            continue
        if e.get("type") == "token" and isinstance(e.get("content"), str):
            text += e["content"]
        elif e.get("type") == "usage":
            if e.get("sent") and text.strip():
                out[int(e["sent"])] = mask_secrets(text)[0]
            text = ""
    return out


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
    SELECT s.id, s.project, s.user_id, s.title, s.created, s.updated, s.archived, s.turns, s.tier,
           COALESCE((SELECT SUM(u.cost) FROM usage u WHERE u.session = s.id), 0) AS cost,
           COALESCE((SELECT SUM(CASE WHEN u.cost IS NULL THEN 1 ELSE 0 END) FROM usage u WHERE u.session = s.id), 0)
               AS calls_without_cost
    FROM sessions s
"""


def list_sessions(project: str | None = None, archived: bool = False, query: str = "", limit: int = 200,
                  owner="all", hidden=()) -> list[dict]:
    """Newest first. project=None lists all projects; "" the Workspace's (no project). `owner`: "all",
    "me" (the install's own) or a member's id. A search never looks in the titles of `hidden` members."""
    where, args = ["s.archived = ?"], [int(archived)]
    if project is not None:
        where.append("COALESCE(s.project, '') = ?")
        args.append(project)
    if query.strip():
        where.append("s.title LIKE ?")
        args.append(f"%{query.strip()}%")
        if hidden:
            where.append(f"(s.user_id IS NULL OR s.user_id NOT IN ({','.join('?' * len(hidden))}))")
            args.extend(hidden)
    clause, more = store.owner_clause("s.user_id", owner)
    if clause:
        where.append(clause)
        args.extend(more)
    conn = store.connect()
    try:
        rows = conn.execute(_LIST_SQL + " WHERE " + " AND ".join(where) + " ORDER BY s.updated DESC LIMIT ?",
                            [*args, limit]).fetchall()
    finally:
        conn.close()
    return [dict(r) | {"archived": bool(r["archived"]), "title": mask_secrets(r["title"] or "")[0]} for r in rows]


def owner_of(sid: str) -> int | None:
    """Whose chat it is: a member's id, or None (the install's own, or no such chat)."""
    conn = store.connect()
    try:
        row = conn.execute("SELECT user_id FROM sessions WHERE id = ?", (sid,)).fetchone()
    finally:
        conn.close()
    return None if row is None else row["user_id"]


def project_of(sid: str) -> str | None:
    """The project of a stored chat ("" = the Workspace), or None when there is no such chat.
    A light read, without the event log (v0.8: per-project memory settings)."""
    conn = store.connect()
    try:
        row = conn.execute("SELECT project FROM sessions WHERE id = ?", (sid,)).fetchone()
    finally:
        conn.close()
    return None if row is None else (row["project"] or "")


def get(sid: str) -> dict | None:
    """Session with its model state and event log, or None."""
    conn = store.connect()
    try:
        row = conn.execute(_LIST_SQL + " WHERE s.id = ?", (sid,)).fetchone()
        if row is None:
            return None
        state = conn.execute("SELECT history, tier_pinned, last_turn_ok FROM sessions WHERE id = ?", (sid,)).fetchone()
        stored = conn.execute("SELECT ts, data FROM session_events WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
        events = [json.loads(r["data"]) for r in stored]
        times = [r["ts"] for r in stored]
    finally:
        conn.close()
    # Saved keys are hidden on the way out too (v0.5.1): a chat stored before
    # keys were hidden, or before a key was saved, never shows one again
    return dict(row) | {
        "title": mask_secrets(row["title"] or "")[0],
        "archived": bool(row["archived"]),
        "history": mask_obj(json.loads(state["history"]))[0],
        "tier_pinned": bool(state["tier_pinned"]),
        "last_turn_ok": bool(state["last_turn_ok"]),
        "events": mask_obj(events)[0],
        "times": times,          # when each event was stored, in step with `events` (v0.8: the time under a reply)
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


def latest(project: str, owner="me") -> str | None:
    """The project's most recent chat of one owner ("me": the install's own, which is every chat in solo mode)."""
    clause, more = store.owner_clause("user_id", owner)
    conn = store.connect()
    try:
        row = conn.execute("SELECT id FROM sessions WHERE project = ? AND archived = 0"
                           + (" AND " + clause if clause else "") + " ORDER BY updated DESC LIMIT 1",
                           (project or "", *more)).fetchone()
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


# ------------------------------------------------------------ saved keys in old records (v0.5.1)
# What is stored since v0.5.1 is already hidden. Chats, plans and memory history
# stored before, or before a key was saved, are cleaned once: at startup, after
# the daily backup, and again whenever the saved keys change.
SCRUB_META = "keys_scrubbed"
_SCRUB_JSON = (("session_events", "id", "data"), ("sessions", "id", "history"), ("plans", "id", "plan"))
_SCRUB_TEXT = (("sessions", "id", "title"), ("plans", "id", "goal"), ("memory_versions", "id", "before"),
               ("memory_versions", "id", "after"), ("memory_versions", "id", "summary"))


def scrub_saved_keys(force: bool = False) -> int:
    """Replace saved keys with ••••(NAME) in stored records. Returns how many
    values were changed; does nothing when the keys didn't change since the
    last run (unless force)."""
    if not secret_values():
        return 0
    mark = secrets_fingerprint()
    conn = store.connect()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (SCRUB_META,)).fetchone()
        if row and row["value"] == mark and not force:
            return 0
        changed = 0
        with conn:
            for table, key, col in _SCRUB_JSON + _SCRUB_TEXT:
                is_json = (table, key, col) in _SCRUB_JSON
                for r in conn.execute(f"SELECT {key} AS k, {col} AS v FROM {table}").fetchall():
                    if not r["v"]:
                        continue
                    if is_json:
                        try:
                            value, n = mask_obj(json.loads(r["v"]))
                        except ValueError:
                            value, n = mask_secrets(r["v"])
                        else:
                            value = json.dumps(value) if n else value
                    else:
                        value, n = mask_secrets(r["v"])
                    if n:
                        conn.execute(f"UPDATE {table} SET {col} = ? WHERE {key} = ?", (value, r["k"]))
                        changed += 1
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (SCRUB_META, mark))
        return changed
    finally:
        conn.close()
