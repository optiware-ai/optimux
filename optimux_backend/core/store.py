"""SQLite storage for data that must survive restarts: usage and chat sessions.

The database lives outside the repo at ~/.optimux/data/optimux.db, in WAL mode so
reads don't block writes. Schema changes are numbered migrations recorded in
the meta table. backup() copies the database with SQLite's online backup API
(safe while in use) to ~/.optimux/backups/, keeping the newest KEEP_BACKUPS.
Uses only the standard library.
"""
import json
import os
import sqlite3
import time

from optimux_backend.core import paths

DB_PATH = paths.DB_PATH
BACKUP_DIR = paths.BACKUP_DIR
KEEP_BACKUPS = 14

MIGRATIONS = [
    # 1: usage, one row per LLM call
    """
    CREATE TABLE usage (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        session TEXT,
        project TEXT,
        role TEXT,
        model TEXT,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost REAL,
        duration_ms INTEGER,
        estimated INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX usage_ts ON usage (ts);
    CREATE INDEX usage_project_ts ON usage (project, ts);
    """,
    # 2: chat sessions and their event log (what the UI was sent, in order)
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        project TEXT,
        title TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        updated REAL NOT NULL,
        tier TEXT,
        tier_pinned INTEGER NOT NULL DEFAULT 0,
        last_turn_ok INTEGER NOT NULL DEFAULT 1,
        archived INTEGER NOT NULL DEFAULT 0,
        turns INTEGER NOT NULL DEFAULT 0,
        history TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX sessions_project_updated ON sessions (project, updated);
    CREATE TABLE session_events (
        id INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions (id),
        ts REAL NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX session_events_session ON session_events (session_id, id);
    CREATE INDEX usage_session ON usage (session);
    """,
    # 3: which skill (if any) was active for a call
    """
    ALTER TABLE usage ADD COLUMN skill TEXT;
    """,
    # 4: project memory history (curator, manual edits, model writes, undo)
    # and how much of each chat the curator has already read
    """
    CREATE TABLE memory_versions (
        id INTEGER PRIMARY KEY,
        project TEXT NOT NULL,
        ts REAL NOT NULL,
        source TEXT NOT NULL,
        session_id TEXT,
        summary TEXT NOT NULL DEFAULT '',
        before TEXT NOT NULL,
        after TEXT NOT NULL
    );
    CREATE INDEX memory_versions_project ON memory_versions (project, id);
    ALTER TABLE sessions ADD COLUMN curated_turns INTEGER NOT NULL DEFAULT 0;
    """,
    # 5: what each automatic memory update cost (NULL when unknown or not a model call)
    """
    ALTER TABLE memory_versions ADD COLUMN cost REAL;
    """,
    # 6: browser sign-ins (Feature 23). `id` is the SHA-256 of the cookie, never the cookie itself.
    """
    CREATE TABLE logins (
        id TEXT PRIMARY KEY,
        created REAL NOT NULL,
        last_seen REAL NOT NULL,
        ip TEXT,
        agent TEXT
    );
    """,
    # 7: projects deleted from the Dashboard whose chats, costs and memory were kept (fix25a)
    """
    CREATE TABLE deleted_projects (
        name TEXT PRIMARY KEY,
        deleted REAL NOT NULL
    );
    """,
    # 8: plan mode (v0.4): plans and their steps, and which plan and step each call belongs to.
    # user_id / team_id stay empty until the team version.
    """
    CREATE TABLE plans (
        id TEXT PRIMARY KEY,
        session TEXT,
        project TEXT,
        goal TEXT NOT NULL DEFAULT '',
        plan TEXT NOT NULL,
        status TEXT NOT NULL,
        created REAL NOT NULL,
        updated REAL NOT NULL,
        user_id TEXT,
        team_id TEXT
    );
    CREATE INDEX plans_session ON plans (session, created);
    ALTER TABLE usage ADD COLUMN plan_id TEXT;
    ALTER TABLE usage ADD COLUMN step INTEGER;
    """,
    # 9: what happened to model calls besides their usage (v0.5 performance charts):
    # failed calls (kind 'error', detail = the error class) and escalations away from a
    # model that got stuck (kind 'escalation', detail = why)
    """
    CREATE TABLE model_events (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        project TEXT,
        role TEXT,
        model TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail TEXT
    );
    CREATE INDEX model_events_ts ON model_events (ts);
    """,
    # 10: the savings view (v0.6, IM-08): what the same tokens would have cost on the
    # reference model (the Complex model at the time of the call). NULL = not measured.
    """
    ALTER TABLE usage ADD COLUMN ref_model TEXT;
    ALTER TABLE usage ADD COLUMN ref_cost REAL;
    """,
]

_ready_for: str | None = None


def connect() -> sqlite3.Connection:
    """A new connection with the schema up to date. Callers close it."""
    global _ready_for
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # A missing file (e.g. ~/.optimux deleted while running) gets its tables again
    fresh = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    if _ready_for != DB_PATH or fresh:
        _migrate(conn)
        _ready_for = DB_PATH
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    version = int(row["value"]) if row else 0
    for number, sql in enumerate(MIGRATIONS, start=1):
        if number > version:
            with conn:
                conn.executescript(sql)
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(number),))
    conn.commit()


def get_meta(key: str) -> str | None:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_meta(key: str, value: str) -> None:
    conn = connect()
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    finally:
        conn.close()


class BackupCheckFailed(RuntimeError):
    """The new backup didn't pass PRAGMA quick_check; it was kept aside."""


STATUS_FILE = "last-check.json"     # in BACKUP_DIR: the result of the last backup's check


def _quick_check(path: str) -> str:
    """'ok', or what SQLite's quick_check reports (or why it couldn't run)."""
    try:
        # immutable: a backup never changes, so no lock and no -wal/-shm files beside it
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            rows = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"couldn't be read: {e}"
    return "ok" if rows == ["ok"] else "; ".join(str(r) for r in rows[:5])


def _write_status(status: dict) -> None:
    """Save the last check, and keep each backup's own result in "files"."""
    old = backup_status() or {}
    files = {k: v for k, v in (old.get("files") or {}).items()
             if isinstance(v, dict) and os.path.exists(os.path.join(BACKUP_DIR, k))}
    name = os.path.basename(status.get("file") or "")
    if paths._BACKUP_NAME.fullmatch(name):
        files[name] = {"ok": status["ok"], "time": status["time"], "error": status.get("error", "")}
    status = {**status, "files": files}
    try:
        with open(os.path.join(BACKUP_DIR, STATUS_FILE), "w") as f:
            json.dump(status, f)
    except OSError as e:
        print(f"[Optimux] backup check status not saved: {e}")


def backup_status() -> dict | None:
    """The last backup's check: {"time", "ok", "file", "error"}, or None."""
    try:
        with open(os.path.join(BACKUP_DIR, STATUS_FILE)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def backup(now: float | None = None, keep: int | None = None) -> str:
    """Copy the database to BACKUP_DIR/optimux-YYYY-MM-DD.db (one per day,
    refreshed on each call) and delete all but the newest KEEP_BACKUPS.

    The copy is checked (PRAGMA quick_check) before it replaces anything
    (v0.5.2, IM-05). A copy that fails is moved to BACKUP_DIR/failed/ (kept,
    never deleted), today's earlier backup and all older ones stay, nothing is
    pruned, and BackupCheckFailed is raised; Dashboard -> System shows it."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = now or time.time()
    name = time.strftime("optimux-%Y-%m-%d.db", time.localtime(stamp))
    target = os.path.join(BACKUP_DIR, name)
    fresh = target + ".new"
    for leftover in (fresh, fresh + "-wal", fresh + "-shm"):
        if os.path.exists(leftover):
            os.remove(leftover)             # left by an interrupted backup
    src = connect()
    dst = sqlite3.connect(fresh)
    try:
        with dst:
            src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")     # one self-contained file, no -wal/-shm beside it
    finally:
        dst.close()
        src.close()
    result = _quick_check(fresh)
    if result != "ok":
        failed_dir = os.path.join(BACKUP_DIR, "failed")
        os.makedirs(failed_dir, exist_ok=True)
        kept = os.path.join(failed_dir, time.strftime("optimux-%Y-%m-%d-%H%M%S.db", time.localtime(stamp)))
        os.replace(fresh, kept)
        _write_status({"time": stamp, "ok": False, "file": kept, "error": result})
        raise BackupCheckFailed(f"the new backup failed its check ({result}); it was kept as {kept}, and the "
                                "older backups were kept. The database itself may be damaged: see Dashboard → "
                                "System.")
    os.replace(fresh, target)
    _write_status({"time": stamp, "ok": True, "file": target, "error": ""})
    backups = paths.backup_files(BACKUP_DIR)       # oldest first
    for old in backups[:-(keep or KEEP_BACKUPS)]:
        os.remove(os.path.join(BACKUP_DIR, old))
    return target


# ------------------------------------------------------------ daily backups in Dashboard -> Backups (v0.5.2)

class BackupError(ValueError):
    """A daily backup that doesn't exist or can't be used; the message is shown."""


def backup_path(name: str) -> str:
    """The path of a daily backup by its file name (optimux-YYYY-MM-DD.db), checked."""
    if not paths._BACKUP_NAME.fullmatch(name or "") or name not in paths.backup_files(BACKUP_DIR):
        raise BackupError("There is no daily backup with that name.")
    return os.path.join(BACKUP_DIR, name)


def list_backups() -> list[dict]:
    """The daily backups, newest first: name, date, size, time and whether the
    last check of that file passed (only the latest is checked each day; older
    ones passed their check when they were made, since v0.5.2)."""
    status = backup_status() or {}
    out = []
    for name in reversed(paths.backup_files(BACKUP_DIR)):
        full = os.path.join(BACKUP_DIR, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        check = (status.get("files") or {}).get(name) or {}
        out.append({"name": name, "date": name[8:18], "size_bytes": st.st_size, "mtime": st.st_mtime,
                    "checked": check.get("ok"), "checked_at": check.get("time"), "error": check.get("error", "")})
    return out


def check_backup(name: str) -> dict:
    """Run the check on one daily backup now (also older ones, made before
    v0.5.2) and remember the result for the list. The last-backup status of
    Dashboard -> System is left as it is."""
    full = backup_path(name)
    result = _quick_check(full)
    now = time.time()
    old = backup_status() or {}
    files = dict(old.get("files") or {})
    files[name] = {"ok": result == "ok", "time": now, "error": "" if result == "ok" else result}
    try:
        with open(os.path.join(BACKUP_DIR, STATUS_FILE), "w") as f:
            json.dump({**old, "files": files}, f)
    except OSError as e:
        print(f"[Optimux] backup check status not saved: {e}")
    return files[name] | {"name": name}


def delete_backup(name: str) -> None:
    os.remove(backup_path(name))


def info() -> dict:
    """Database location, size (including the WAL file) and latest backup."""
    conn = connect()
    try:
        usage_rows = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    finally:
        conn.close()
    size = sum(os.path.getsize(p) for p in (DB_PATH, DB_PATH + "-wal") if os.path.exists(p))
    backups = paths.backup_files(BACKUP_DIR)
    latest = os.path.join(BACKUP_DIR, backups[-1]) if backups else None
    return {
        "path": DB_PATH,
        "size_bytes": size,
        "usage_rows": usage_rows,
        "backup_dir": BACKUP_DIR,
        "backups": len(backups),
        "latest_backup": latest,
        "latest_backup_time": os.path.getmtime(latest) if latest else None,
        "backup_check": backup_status(),
    }
