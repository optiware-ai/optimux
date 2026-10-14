# snapshots.py Agent Details

## File Overview
`snapshots.py` takes, lists and deletes snapshots: copies of Optimux's data or of the Workspace, kept on this machine in `~/.optimux/snapshots/` (`paths.SNAPSHOT_DIR`, mode 700). Added in v0.3 (Dashboard → Backups).

## The two kinds
- **data:** `config/`, `memory/`, `skills/`, `data/optimux.db` (copied with the SQLite backup API, so the copy is consistent while the server runs), `secrets.env` and `auth.json` (`DATA_ITEMS`). Left out (`DATA_LEFT_OUT`): `backups/`, `snapshots/`, `logs/`, `legacy/`.
- **workspace:** the project folders (all, or the ones chosen; a project folder that is a link to another folder is never followed: it is listed in `left_out`, and choosing it is refused), stored as `workspace/<project>/...`, each with its memory file as `memory/projects/<name>.md`. `.archive/` is never included. Rebuildable folders (`REBUILDABLE`: `node_modules`, `.venv`, `venv`, `__pycache__`, tool caches) are skipped unless `skip_rebuildable=False`.

## Files
- `<id>.tar.gz` (mode 600) and `<id>.json` (the description used for listing, so archives are never opened to list them).
- Ids: `YYYY-MM-DD_HHMMSS-<kind>` with `-N` added when the second is taken (`ID_RE`). Every function that takes an id validates it first (`archive_path`), so a path can never be passed in.
- Ids are checked with `ID_RE.fullmatch` (a trailing newline is refused).
- The archive is written to `<id>.tar.gz.partial` and renamed when complete. On failure the partial file is removed and the `.json` says `failed` with the error.
- Every archive starts with `manifest.json` (`FORMAT`, `FORMAT_VERSION`, kind, Optimux version, note, home and Workspace paths, items, projects, whether keys are included) and `BACKUP-README.txt` (`readme(manifest)`: the contents in plain words and how to restore by hand, with the real paths; a warning when keys are inside).
- Every archive ends with `SHA256SUMS` (one line per file, `sha256sum -c` format), computed while copying.
- Members are written by `_Writer`: only folders, files and symlinks (no sockets, pipes or devices); owner set to 0 with no names; symlinks stored as links, never followed; files opened with `O_NOFOLLOW`. Hard links are stored as **full copies** (`tar.inodes` is cleared before each file), so a member never depends on another one. In a data snapshot every member is 600 (folders 700), so a by-hand unpack is private.
- `_Copy` reads exactly the size recorded in the header: a file that shrinks while it is copied is padded with zeros (the archive stays valid) and listed in `changed`; a file or folder that can't be read is listed in `unreadable`. Both (first 50, plus `changed_count` / `unreadable_count`) are in the `.json` description and shown as warnings in the list.
- The database copy `.<id>.db` is created with mode 600. `_sweep()` (called by `summary()`) removes the `.<id>.db` and `<id>.tar.gz.partial` files a stopped server left behind, except for snapshots being taken (`_running`; `create` adds its id there too, also when called directly).

## Space
- `plan()` counts files and bytes (before compression; the database includes its `-wal` file) and estimates the archive (`estimate_bytes`, with header overhead). It refuses (`fits=False`, `reason`) when the free disk minus `FREE_MARGIN` (200 MB) is too small, or when the limit would be passed even after removing every automatic snapshot.
- The limit is the setting `snapshot_limit_gb` (default 5, `DEFAULT_LIMIT_GB`), read by `limit_bytes()`.
- `_make_room(needed)` removes **automatic** snapshots (`auto=True`), oldest first. The user's own snapshots are never removed automatically.

## Core Functionality
- **`preview(kind, projects, skip_rebuildable)`:** `plan()` without local source paths, for the Dashboard.
- **`create(kind, note, projects, skip_rebuildable, auto, sid)`:** takes the snapshot now (blocking) and returns its description. Raises `SnapshotError`.
- **`start(...)`:** checks, writes the `running` description at once, and runs `create` in a daemon thread. Only one snapshot at a time (`_running`, `_lock`). A failure before writing begins (the plan changed since the check) is recorded with its reason, not shown as interrupted.
- **`list_snapshots()`:** newest first, with `size_bytes`. A `running` snapshot this process isn't taking (the server stopped) and a `ready` one whose archive is gone are shown as `failed`.
- **`get(sid)`**, **`delete(sid)`** (refuses while it is being taken; also removes a left-over `.partial`), **`summary()`** (list, used, limit, free disk, folder, running).
- **`optimux_version()`:** `git describe` of the install, or `unknown`.

## Links & Dependencies
- `paths.py` (folders and the database path), `workspace.py` (`projects_root`, `project_names`), `config_manager.py` (`snapshot_limit_gb`).
- Used by `main.py`: `GET /api/snapshots`, `POST /api/snapshots/preview`, `POST /api/snapshots`, `POST /api/snapshots/limit`, `POST /api/snapshots/{id}/delete`. A `SnapshotError` becomes a 422 with its message.
- Tests: `tests/test_snapshots.py`.
