"""What Optimux remembers about projects whose folder is gone (fix25a).

When a project is deleted from the Dashboard, its folder is removed and either
- its records are kept (chats, costs, memory): the name is listed here, so the
  Dashboard can show it as *deleted* next to the others; or
- its records are forgotten too: chats and their events, usage rows (model
  calls), requests with their ratings, model errors and escalations, plans,
  memory versions, the project memory file and its budget.
"""
import os
import time

from optimux_backend.core import budgets, store
from optimux_backend.core.memory_manager import PROJECTS_DIR


def mark_deleted(name: str) -> None:
    conn = store.connect()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO deleted_projects (name, deleted) VALUES (?, ?)", (name, time.time()))
    finally:
        conn.close()


def unmark_deleted(name: str) -> None:
    """A new project with the same name: its records belong to a live project again."""
    conn = store.connect()
    try:
        with conn:
            conn.execute("DELETE FROM deleted_projects WHERE name = ?", (name,))
    finally:
        conn.close()


def deleted_projects(root: str | None = None) -> dict:
    """{name: deleted_at} for deleted projects whose records were kept. With
    the Workspace folder: a name whose folder exists again (made by hand, cloned)
    is a live project, so its mark is dropped."""
    conn = store.connect()
    try:
        out = {r["name"]: r["deleted"] for r in conn.execute("SELECT name, deleted FROM deleted_projects")}
    finally:
        conn.close()
    if root:
        for name in [n for n in out if os.path.lexists(os.path.join(root, n))]:
            unmark_deleted(name)
            out.pop(name)
    return out


def record_counts(name: str) -> dict:
    """Everything that would be forgotten, for the confirmation: chats, model calls and
    their cost, requests and their 👍/👎, model errors and escalations, plans, memory."""
    conn = store.connect()
    try:
        one = lambda sql: conn.execute(sql, (name,)).fetchone()
        chats = one("SELECT COUNT(*) FROM sessions WHERE project = ?")[0]
        calls, cost = one("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM usage WHERE project = ?")
        requests, likes, dislikes = one("SELECT COUNT(*), COALESCE(SUM(rating = 1), 0), COALESCE(SUM(rating = -1), 0) "
                                        "FROM tasks WHERE project = ?")
        errors, escalations = one("SELECT COALESCE(SUM(kind = 'error'), 0), COALESCE(SUM(kind = 'escalation'), 0) "
                                  "FROM model_events WHERE project = ?")
        plans = one("SELECT COUNT(*) FROM plans WHERE project = ?")[0]
        versions = one("SELECT COUNT(*) FROM memory_versions WHERE project = ?")[0]
    finally:
        conn.close()
    return {"chats": chats, "calls": calls, "cost": cost, "requests": requests, "likes": likes,
            "dislikes": dislikes, "errors": errors, "escalations": escalations, "plans": plans,
            "memory_versions": versions, "memory_file": os.path.isfile(_memory_file(name))}


def _memory_file(name: str) -> str:
    return os.path.join(PROJECTS_DIR, f"{name}.md")


def forget(name: str) -> dict:
    """Remove every record of a project: chats and their events, usage rows, requests
    with their ratings, model errors and escalations, plans, memory versions, its
    memory file and its budget. Returns what was removed."""
    counts = record_counts(name)
    conn = store.connect()
    try:
        with conn:
            conn.execute("DELETE FROM session_events WHERE session_id IN (SELECT id FROM sessions WHERE project = ?)", (name,))
            conn.execute("DELETE FROM sessions WHERE project = ?", (name,))
            conn.execute("DELETE FROM usage WHERE project = ?", (name,))
            # v0.8: the rest of its statistics too, so nothing is left half-counted
            conn.execute("DELETE FROM tasks WHERE project = ?", (name,))
            conn.execute("DELETE FROM model_events WHERE project = ?", (name,))
            conn.execute("DELETE FROM plans WHERE project = ?", (name,))
            conn.execute("DELETE FROM memory_versions WHERE project = ?", (name,))
            conn.execute("DELETE FROM deleted_projects WHERE name = ?", (name,))
    finally:
        conn.close()
    path = _memory_file(name)
    if os.path.realpath(os.path.dirname(path)) == os.path.realpath(PROJECTS_DIR) and os.path.isfile(path):
        os.remove(path)
    budgets.save_budget(name, None)
    return counts
