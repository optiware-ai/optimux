# store.py Agent Details

## File Overview
`store.py` is Optimux's SQLite storage for data that must survive restarts: usage and chat sessions. It uses only the standard library (`sqlite3`).

## Core Functionality
- **Deleted database:** `connect()` re-applies the migrations when the database file is missing (for example `~/.optimux` was deleted while the server runs), so the app keeps working with an empty database instead of failing with "no such table".
- **Location:** `DB_PATH = ~/.optimux/data/optimux.db` (from `paths.py`), outside the repo. WAL mode, so reads don't block writes.
- **`connect()`:** Returns a new connection (row factory `sqlite3.Row`); callers close it. On first use per database path it applies pending migrations.
- **Migrations:** `MIGRATIONS` is a list of SQL scripts; the applied count is stored as `schema_version` in the `meta` table. Migration 1 creates `usage` (one row per LLM call: ts, session, project, role, model, prompt/completion/cached/reasoning tokens, cost, duration_ms, estimated) with indexes on `ts` and `(project, ts)`. Migration 2 creates `sessions` (id, project, title, created, updated, tier, tier_pinned, last_turn_ok, archived, turns, history JSON) and `session_events` (session_id, ts, data JSON), plus an index on `usage.session`. Migration 3 adds `usage.skill` (the skill active for a call). Migration 4 adds `memory_versions` (project memory history, see `curator.py`) and `sessions.curated_turns`. Migration 5 adds `memory_versions.cost`. Migration 6 adds `logins` (Feature 23). Migration 7 adds `deleted_projects` (name, deleted; fix25a, see project_records.py). Migration 8 (v0.4 plan mode) adds `plans` (id, session, project, goal, plan JSON, status, created, updated, user_id, team_id; index on `(session, created)`, see plans.py) and `usage.plan_id` / `usage.step`. Migration 9 (v0.5) adds `model_events` (ts, project, role, model, kind `error`/`escalation`, detail; index on `ts`; see analytics.py). New schema changes are appended, never edited.
- **`get_meta` / `set_meta`:** Key/value settings in `meta`.
- **`backup(now=None)`:** Copies the database with SQLite's online backup API (safe while in use) to `~/.optimux/backups/optimux-YYYY-MM-DD.db` (one per day, refreshed), keeping the newest `KEEP_BACKUPS` (14). `main.py` calls it on every server start.
- **`info()`:** Path, size (including the WAL file), usage row count, backup folder, number of backups, and the latest backup with its time (`GET /api/storage`).

## Links & Dependencies
- Used by `usage.py` (usage table), `sessions.py` (sessions and events) and `main.py` (startup backup, `/api/storage`).
- Tests use `tests/conftest.py`, which points `DB_PATH`, `BACKUP_DIR` and the legacy usage path at a temporary folder for every test.

## Feature 23
- Migration 6: table `logins` (browser sign-ins; `id` is the SHA-256 of the cookie). See `auth.py_AGENT.md`.
