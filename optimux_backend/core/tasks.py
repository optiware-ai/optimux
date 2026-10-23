"""Task records (v0.6, IM-09): one row per request, kept for routing that
learns from outcomes later.

A request is one job of the chat: a message, the Escalate button, or a plan
action (proposing a plan and running it are two rows, linked by plan_id). The
row holds what was decided and what happened, never the text: the chat keeps
that, and the two join on `session`. Nothing here is sent anywhere.

The running request lives in session["task"] (a plain dict built by `new`);
main.py fills it as the request goes and calls `finish` at the end. Writes
never raise: a statistics row must not break a chat.
"""
import csv
import io
import time
import uuid

from optimux_backend.core import store

COLUMNS = ("id", "ts", "session", "project", "kind", "tier", "model", "conductor_model", "conductor_tier",
           "conductor_plan", "conductor_error", "escalated", "escalation_reason", "outcome", "steps", "calls",
           "cost", "calls_without_cost", "duration_ms", "files_changed", "lines_added", "lines_removed",
           "approved", "rejected", "skill", "plan_id", "rating", "rated_ts")
KINDS = ("prompt", "escalate", "plan_action")
RATINGS = (-1, 0, 1)               # 👎, cleared, 👍
REASON_CHARS = 200


class TaskError(Exception):
    pass


def new(kind: str, project: str = "") -> dict:
    """A request starts: an empty record held in memory until `finish`."""
    return {"id": uuid.uuid4().hex[:16], "ts": time.time(), "_t0": time.monotonic(), "kind": kind,
            "project": project or None, "escalated": 0, "steps": 0, "calls": 0, "cost": 0.0,
            "calls_without_cost": 0, "approved": 0, "rejected": 0}


def add_call(task: dict | None, entry: dict) -> None:
    """One model call of this request (the usage entry from usage.record)."""
    if task is None:
        return
    task["calls"] += 1
    if entry.get("cost") is None:
        task["calls_without_cost"] += 1
    else:
        task["cost"] += entry["cost"]


def escalate(task: dict | None, reason: str) -> None:
    if task is None:
        return
    task["escalated"] = 1
    task["escalation_reason"] = (reason or "")[:REASON_CHARS] or None


def set_changes(task: dict | None, result: dict) -> None:
    """The changed-files summary (changes.compare) of this request."""
    if task is None or not result.get("git") or result.get("error"):
        return
    files = result.get("files") or []
    task["files_changed"] = len(files) + int(result.get("more") or 0)
    task["lines_added"] = sum(f.get("added") or 0 for f in files)
    task["lines_removed"] = sum(f.get("removed") or 0 for f in files)


def worth_keeping(task: dict) -> bool:
    """A request that called no model and has no outcome (a /simple command
    alone, a refused budget, "Nothing to escalate") is not a task."""
    return bool(task.get("calls") or task.get("outcome"))


def finish(task: dict | None, session_id: str | None) -> bool:
    """Write the record. Returns True when a row was written."""
    if task is None or not worth_keeping(task):
        return False
    row = {k: task.get(k) for k in COLUMNS}
    row["session"] = session_id or None
    row["duration_ms"] = int((time.monotonic() - task["_t0"]) * 1000)
    if row["calls"] and row["calls_without_cost"] == row["calls"]:
        row["cost"] = None                     # no call had a known price: unknown, not free
    if row["conductor_plan"] is not None:
        row["conductor_plan"] = 1 if row["conductor_plan"] else 0
    try:
        conn = store.connect()
        try:
            with conn:
                conn.execute(f"INSERT OR REPLACE INTO tasks ({', '.join(COLUMNS)}) "
                             f"VALUES ({', '.join('?' * len(COLUMNS))})", [row[k] for k in COLUMNS])
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"[Optimux] task record write failed: {e}")
        return False


def mark_escalated(task_id: str | None, reason: str) -> None:
    """An earlier request turned out too hard (the user pressed Escalate)."""
    if not task_id:
        return
    try:
        conn = store.connect()
        try:
            with conn:
                conn.execute("UPDATE tasks SET escalated = 1, escalation_reason = ? WHERE id = ?",
                             ((reason or "")[:REASON_CHARS], task_id))
        finally:
            conn.close()
    except Exception as e:
        print(f"[Optimux] task record update failed: {e}")


def rate(task_id: str, rating: int) -> dict:
    """👍 (1), 👎 (-1) or cleared (0) for one request."""
    if rating not in RATINGS:
        raise TaskError("The rating must be 1, -1 or 0.")
    conn = store.connect()
    try:
        with conn:
            cur = conn.execute("UPDATE tasks SET rating = ?, rated_ts = ? WHERE id = ?",
                               (rating or None, time.time() if rating else None, task_id))
        if not cur.rowcount:
            raise TaskError("No such request.")
    finally:
        conn.close()
    return {"id": task_id, "rating": rating or None}


def ratings(session_id: str) -> dict:
    """{task id: rating or None} for one chat, so a replayed chat shows its 👍 / 👎."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT id, rating FROM tasks WHERE session = ?", (session_id,)).fetchall()
    finally:
        conn.close()
    return {r["id"]: r["rating"] for r in rows}


def first_ts() -> float | None:
    """When task records started (escalations before it are not linked to calls)."""
    conn = store.connect()
    try:
        row = conn.execute("SELECT MIN(ts) AS t FROM tasks").fetchone()
    finally:
        conn.close()
    return row["t"] if row and row["t"] is not None else None


def stats() -> dict:
    conn = store.connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n, SUM(rating = 1) AS up, SUM(rating = -1) AS down, "
                           "MIN(ts) AS first FROM tasks").fetchone()
    finally:
        conn.close()
    return {"count": row["n"] or 0, "thumbs_up": row["up"] or 0, "thumbs_down": row["down"] or 0,
            "first": row["first"]}


def export_csv() -> str:
    """Every record, oldest first, as CSV (times in UTC ISO 8601 next to the raw ts)."""
    conn = store.connect()
    try:
        rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM tasks ORDER BY ts").fetchall()
    finally:
        conn.close()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time_utc", *COLUMNS])
    for r in rows:
        w.writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["ts"])), *[_cell(r[k]) for k in COLUMNS]])
    return out.getvalue()


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):     # never a formula in a spreadsheet
        return "'" + v
    return v


def delete_all() -> int:
    """Remove every task record (the calls keep their usage rows)."""
    conn = store.connect()
    try:
        with conn:
            n = conn.execute("DELETE FROM tasks").rowcount
    finally:
        conn.close()
    return n
