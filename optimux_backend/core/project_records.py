"""What Optimux remembers about projects whose folder is gone (fix25a).

When a project is deleted from the Dashboard, its folder is removed and either
- its records are kept (chats, costs, memory): the name is listed here, so the
  Dashboard can show it as *deleted* next to the others; or
- its records are forgotten too: chats and their events, usage rows, memory
  versions, the project memory file and its budget.
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
    """What would be forgotten: chats, model calls and their cost, memory versions."""
    conn = store.connect()
    try:
        chats = conn.execute("SELECT COUNT(*) FROM sessions WHERE project = ?", (name,)).fetchone()[0]
        calls, cost = conn.execute("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM usage WHERE project = ?", (name,)).fetchone()
        versions = conn.execute("SELECT COUNT(*) FROM memory_versions WHERE project = ?", (name,)).fetchone()[0]
    finally:
        conn.close()
    return {"chats": chats, "calls": calls, "cost": cost, "memory_versions": versions,
            "memory_file": os.path.isfile(_memory_file(name))}


def _memory_file(name: str) -> str:
    return os.path.join(PROJECTS_DIR, f"{name}.md")


def forget(name: str) -> dict:
    """Remove every record of a project: chats and their events, usage rows,
    memory versions, its memory file and its budget. Returns what was removed."""
    counts = record_counts(name)
    conn = store.connect()
    try:
        with conn:
            conn.execute("DELETE FROM session_events WHERE session_id IN (SELECT id FROM sessions WHERE project = ?)", (name,))
            conn.execute("DELETE FROM sessions WHERE project = ?", (name,))
            conn.execute("DELETE FROM usage WHERE project = ?", (name,))
            conn.execute("DELETE FROM memory_versions WHERE project = ?", (name,))
            conn.execute("DELETE FROM deleted_projects WHERE name = ?", (name,))
    finally:
        conn.close()
    path = _memory_file(name)
    if os.path.realpath(os.path.dirname(path)) == os.path.realpath(PROJECTS_DIR) and os.path.isfile(path):
        os.remove(path)
    budgets.save_budget(name, None)
    return counts
