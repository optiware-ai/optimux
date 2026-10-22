"""SQLite storage for data that must survive restarts: usage and chat sessions.

The database lives outside the repo at ~/.optimux/data/optimux.db, in WAL mode so
reads don't block writes. Schema changes are numbered migrations recorded in
the meta table. backup() copies the database with SQLite's online backup API
(safe while in use) to ~/.optimux/backups/, keeping the newest KEEP_BACKUPS.
Uses only the standard library.
"""
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


def backup(now: float | None = None) -> str:
    """Copy the database to BACKUP_DIR/optimux-YYYY-MM-DD.db (one per day,
    refreshed on each call) and delete all but the newest KEEP_BACKUPS."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = time.strftime("optimux-%Y-%m-%d.db", time.localtime(now or time.time()))
    target = os.path.join(BACKUP_DIR, name)
    src = connect()
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    backups = paths.backup_files(BACKUP_DIR)       # oldest first
    for old in backups[:-KEEP_BACKUPS]:
        os.remove(os.path.join(BACKUP_DIR, old))
    return target


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
    }
