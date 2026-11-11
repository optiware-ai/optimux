"""What Optimux remembers about projects whose folder is gone (fix25a).

When a project is deleted from the Dashboard, its folder is removed and either
- its records are kept (chats, costs, memory): the name is listed here, so the
  Dashboard can show it as *deleted* next to the others; or
- its records are forgotten too: chats and their events, usage rows (model
  calls), requests with their ratings, model errors and escalations, plans,
  memory versions, the project memory file and its budget.
"""
import os
import re
import time

from optimux_backend.core import budgets, paths, store
from optimux_backend.core.i18n import tr
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


def _item(key: str, label: str, path: str, what: str, shared: bool = False) -> dict:
    return {"key": key, "label": label, "path": path, "what": what, "shared": shared,
            "exists": os.path.exists(path), "folder": os.path.isdir(path)}


def locations(name: str, root: str | None) -> list[dict]:
    """Every place on disk that holds something of this project, the project folder
    first (v0.8, from OmniLatch): Projects → a card's "Where its files are".
    `shared` marks a file that also holds other projects' data (the database,
    budgets.json, the project settings, a snapshot), so deleting it isn't the way to
    remove one project. Only places that exist are listed, except the project folder
    and its memory file, which every project has (or will have)."""
    out = []
    if root:
        out.append(_item("folder", tr("Project folder"), os.path.join(root, name),
                         tr("Its files, git history and its own skills (.optimux-skills)")))
    out.append(_item("memory", tr("Project memory"), _memory_file(name),
                     tr("What the models remember about this project (Memory page)")))
    counts = record_counts(name)
    if counts["chats"] or counts["calls"] or counts["memory_versions"] or counts["requests"]:
        out.append(_item("database", tr("Chats, costs and history"), paths.DB_PATH,
                         tr("{chats} chats, {calls} model calls, {requests} requests, {versions} memory versions",
                            chats=counts["chats"], calls=counts["calls"], requests=counts["requests"],
                            versions=counts["memory_versions"]), shared=True))
    if budgets.status(name):
        out.append(_item("budget", tr("Budget"), budgets.BUDGETS_FILE, tr("This project's monthly limit"), shared=True))
    try:
        from optimux_backend.core import project_settings
        own = name in (project_settings._read().get("projects") or {})
    except Exception:
        own = False
    if own:
        out.append(_item("settings", tr("Project settings"), paths.PROJECT_SETTINGS_FILE,
                         tr("Its own models, steps, memory and planning settings"), shared=True))
    if root:
        archive = os.path.join(root, ".archive")
        if os.path.isdir(archive) and not os.path.islink(archive):
            pat = re.compile(re.escape(name) + r"-\d{8}-\d{6}$")
            for f in sorted(os.listdir(archive)):
                if pat.fullmatch(f):
                    out.append(_item("archived", tr("Archived copy"), os.path.join(archive, f),
                                     tr("Restore it from Projects → Archived projects")))
    try:
        from optimux_backend.core import snapshots
        snaps = snapshots.list_snapshots()
    except Exception:          # a damaged snapshot folder mustn't hide the rest
        snaps = []
    for m in snaps:
        names = [p.get("name") if isinstance(p, dict) else p for p in (m.get("projects") or [])]
        if m.get("status") == "ready" and name in names:
            out.append(_item("snapshot", tr("Snapshot"), snapshots.archive_path(m["id"]),
                             tr("A snapshot that includes it ({kind})", kind=m.get("kind", "")), shared=True))
    return [o for o in out if o["exists"] or o["key"] in ("folder", "memory")]
