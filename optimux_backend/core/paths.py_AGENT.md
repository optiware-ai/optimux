# paths.py Agent Details

## File Overview
`paths.py` defines where Optimux keeps every file and moves files from older locations into that layout once. It is the single place to look up a path; other modules import their paths from it.

## Layout
`$OPTIMUX_HOME` (default `~/.optimux`):
- `config/`: `settings.json`, `models.json`, `routing.json`, `budgets.json` (human-readable JSON)
- `secrets.env`: API keys and `OPTIMUX_TOKEN`, mode 0600, outside the repo
- `data/optimux.db`: usage and sessions (SQLite)
- `backups/`
- `memory/`: `personal.md`, `professional.md`, `projects/<name>.md`
- `skills/`: reserved for the skill system
- `logs/migrations.log`
- `legacy/`: old files, moved here once and never deleted

## Core Functionality
- **Privacy:** `prepare_home` creates `OPTIMUX_HOME` with mode 700 (and resets it to 700 on every start) and tightens an existing `secrets.env` to 600. `write_private(path, text, append=False)` writes secret files created with mode 600 from the start (no window where they are readable) and tightens existing ones; used for the token, API keys and merged `.env` keys.
- **Constants:** `HOME`, `CONFIG_DIR`, `SECRETS_FILE`, `DATA_DIR`, `DB_PATH`, `BACKUP_DIR`, `MEMORY_DIR`, `SKILLS_DIR`, `LOGS_DIR`, `LEGACY_DIR`, the four config file paths, and `LEGACY_USAGE_LOG`. `layout()` returns them for `/api/system`.
- **`prepare_home(repo_dir=cwd)`:** creates the folders and moves old files. It only moves (never deletes) and logs each move to `logs/migrations.log`:
  - The repo's `.env` becomes `secrets.env`. If `secrets.env` exists, keys it lacks are added (home wins), and the old file goes to `legacy/repo.env` (0600).
  - `usage.jsonl.migrated` and the old `projects/` folder go to `legacy/`.
  - v0.2 removed the upgrade from CodeMux (Optimux's old name): `~/.codemux`, a repo's `.codemux/`, `codemux.db` and `CODEMUX_*` keys are no longer moved or read.
  - Running it again does nothing. `OPTIMUX_NO_MIGRATE=1` skips the moves (tests set it).
- **`migration_log(lines)`:** the latest log lines, for the System section.
- **`backup_files(folder=BACKUP_DIR)`:** the daily backups (`optimux-YYYY-MM-DD.db`), oldest first; `store.py` prunes and reports with it.

## Links & Dependencies
- Called first thing in `main.py`, before `config_manager` loads `secrets.env`.
- Used by `config_manager.py`, `budgets.py`, `store.py`, `usage.py`, `memory_manager.py` and `security.py` (token file).
- `tests/conftest.py` points `OPTIMUX_HOME` at a throwaway folder before importing anything, so tests never touch the real home or the repo.

## Feature 23
- `AUTH_FILE` (`auth.json`: password hash, two-step secret) is listed in `layout()` and tightened to mode 600 by `prepare_home`, like `secrets.env`.
