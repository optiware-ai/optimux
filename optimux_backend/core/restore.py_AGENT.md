# restore.py Agent Details

## File Overview
`restore.py` restores snapshots (v0.3, **Dashboard → Backups → Restore**). A restore always starts from a snapshot in the list (`snapshots.py`); a downloaded file is first **uploaded and imported** into the list. The rules, all enforced here:
- The archive is checked while it is read (`scan`); every file must match `SHA256SUMS`, otherwise nothing is restored.
- The user chooses the parts and types `CONFIRM_PHRASE` ("yes I am sure I want to restore the backup").
- When data parts are restored, a **Data snapshot of the current state** is taken (`snapshots.create("data", note="Before restoring <id>")`, not automatic, so never removed to make room) after the archive has been checked and before anything changes. If it fails, nothing is restored. The snapshot being restored is **held** (`snapshots.hold`/`release`) for the whole restore, so making room for the safety snapshot never removes it, and it can't be deleted meanwhile.
- **Usage and cost history and the current sign-ins are never rolled back** (`_merge_database`).
- **Nothing is deleted**: projects move to the Workspace archive (`workspace.archive_project`, shown under Archived projects), data files to `~/.optimux/legacy/before-restore-<time>/`.
- Refused while a reply runs (`replies_running`, from `main.BUSY_PROJECTS`), while a snapshot is being taken, or while another restore waits for the restart.

## Reading an archive: `scan(archive, extract_to=None, select=None, free=None)`
Streams the `.tar.gz` once (`r|gz`) and returns `{manifest, files, bytes, extracted}`; raises `RestoreError`.
- `manifest.json` must be the **first** member (≤ 1 MB) with `format` = `snapshots.FORMAT`, `format_version` ≤ `snapshots.FORMAT_VERSION` ("made by a newer Optimux" otherwise), a known `kind` and `items` whose paths pass `_part_ok`: a `DATA_PARTS` entry, `workspace/<name>` or `memory/projects/<name>.md` (names as in `workspace.NAME_RE`).
- Every other member: a safe path (`_clean_name`: no absolute path, no `..`, `.` or empty component, no NUL), under one of the manifest's parts (or the README / `SHA256SUMS`), never twice, and only a folder, a regular file or a symbolic link (hard links, devices, FIFOs and sparse files are refused). `SHA256SUMS` must come last; members after it are refused, and it is read line by line (at most `MAX_SUMS`, 64 MB).
- **Symbolic links are kept only inside projects.** In a data part (`_is_data`: the `DATA_PARTS` and project memory files) a link could point the keys or the database outside the data folder, so it is never created: it is listed in `skipped_links` (returned by `restore`, first 20).
- `tarfile` reads long-name and pax headers whole into memory, so `_SafeInfo` (the `tarinfo` class) refuses any above `MAX_EXTENDED_HEADER` (1 MB): a tiny archive can't claim gigabytes.
- Every regular file is hashed (also those not extracted); at the end the hashes must equal `SHA256SUMS` exactly (no file missing, none extra).
- Extraction (members `select(name)` accepts) goes into a **new, empty folder**: parents are created by `_ensure_parents`, which refuses any component that is not a real folder (so nothing is ever written through a link, even a link the archive itself created); files are created with `O_EXCL | O_NOFOLLOW`, mode `(mode & 0o777) | 0o600` (no setuid bits), mtime kept; folders get their modes at the end; links are created as links. `free` stops extraction when the disk would fill.
- `check_database(path)`: SQLite `quick_check`, and the schema version is not newer than `store.MIGRATIONS`.

## What can be restored: `inspect(sid)`
From the snapshot's `manifest.json` (read with `snapshots.read_head_member`): the parts (project memory files come with their project, not separately), each with `kind` (`data` / `project`), `exists`, `default`, `effect`, `note` and `blocked` (a current project that is a symlink; no Workspace folder). Defaults: everything on except `secrets.env` and `auth.json` (`SENSITIVE`: an older copy would bring back changed keys or password). Also `confirm_phrase`, `pending`, `workspace`.

## `restore(sid, parts, confirm, replies_running=False)`
Checks (phrase, pending, snapshot running, parts known and not blocked, free space on the data disk and, for projects, on the Workspace disk), sets `_busy` (chat turns and changes wait: `blocked_reason`), holds the snapshot, then checks `replies_running` (a bool, or a function called once `_busy` is set, so no turn can slip in between). Then, in this order:
1. Extracts the chosen parts (plus `memory/projects/<name>.md` of chosen projects) into `~/.optimux/restore-staging` with `scan` (the whole archive is checked) and checks the database. A damaged archive stops here, before any snapshot is taken.
2. Takes the before-restore snapshot when data parts are chosen.
3. **Projects at once** (`_put_project`): the staged folder is first moved next to its place as `<root>/.restore-<name>-<random>` (a copy when the Workspace is on another disk), and only then is the current project archived (`workspace.archive_project`) and the copy renamed into place. A failed copy removes only the partial copy and changes nothing. Its memory file is written with `curator.write_memory(..., "restore")` (in the memory history, so Undo works), unless the whole `memory` part is restored too, or the database is restored (then it is staged and written at start, so its history entry lands in the restored database). A failure part-way says which projects were already restored.
4. **Data staged** in `~/.optimux/restore-pending/` (mode 700) with `plan.json` (`source`, `parts`, `before_snapshot`, `created`, `projects`, `project_memory`; those memory files in `project-memory/`).
The staging folder is always removed (it holds only copies). Returns `{source, projects, archived, before_snapshot, data, restart_needed, skipped_links}`.

## At start: `apply_pending()`
Called by `main.py` right after `paths.prepare_home()`, **before any module reads config, keys or the database**. It **never raises** (an error, e.g. a full disk, is reported and the staged files stay for the next start), so a restore can't stop Optimux from starting. For a damaged plan, the folder moves to `legacy/damaged-restore-<time>` and nothing is applied. Otherwise:
1. `_merge_database(new, store.DB_PATH)`: migrates the restored database (`store._migrate`), then replaces its `usage` and `logins` rows with the current ones (`ATTACH`, common columns), and checkpoints the WAL.
2. Each part in `DATA_PARTS` order: the current copy (for the database also `-wal` / `-shm`) moves to `legacy/before-restore-<time>/<part>`, the staged one into place (`os.replace`; the database goes to `store.DB_PATH`). `secrets.env` and `auth.json` are set to 600.
3. Each `project_memory` file replaces the project's memory file (the current one moves aside) and a `restore` row is added to `memory_versions` in the restored database.
4. `plan.json` moves to the aside folder; `restore-last.json` records the result (`ok`, `ts`, `source`, `parts`, `projects`, `before_snapshot`, `aside`).
On any error every move is undone in reverse, the staged files go to `legacy/failed-restore-<time>`, and `restore-last.json` records `ok: false` with the error, saying whether the undo was complete. Returns lines for the log.

## Other functions
- `pending()`, `blocked_reason()` (used by the chat WebSocket, the memory curator and `main.py`'s middleware), `busy()`, `supervised()` (`OPTIMUX_SUPERVISED=1`, set only in the systemd units the installer writes, which start Optimux again when it exits with 75), `status()` (`pending`, `last`, `busy`, `confirm_phrase`, `can_restart` = `supervised()`).
- `cancel_pending()`: the staged files move to `legacy/cancelled-restore-<time>`. `last_result()`, `dismiss_last()` (removes the notice file only).
- **Uploads**: `new_upload()` (token + path in `SNAPSHOT_DIR/uploads/`, mode 700; swept after `UPLOAD_TTL` = 1 hour), `upload_path(token)` (`TOKEN_RE`), `is_encrypted` (age header), `parse_identity` (the `AGE-SECRET-KEY-1…` line from a pasted key or the key file Optimux gave; a pasted SSH private key is refused with how to decrypt on the computer instead), `import_upload(token, key, filename)`: decrypts with `pyrage.decrypt_io` when encrypted, runs `scan` on the whole file, checks the size limit, moves it into the list as a new snapshot id (`snapshots._new_id` under `snapshots._lock`) and writes its description (`uploaded: true`, `original_id`, `original_created`, note "Uploaded: <file> (taken <date>)"). A wrong key keeps the upload, so it can be retried without uploading again; any other failure removes it.

## Links & Dependencies
- `paths.py`, `snapshots.py` (archive paths, descriptions, `create`, `read_head_member`, `free_bytes`, limits), `workspace.py` (`projects_root`, `project_names`, `archive_project`), `curator.py` (`write_memory`), `store.py` (`DB_PATH`, `MIGRATIONS`, `_migrate`), `pyrage`.
- Used by `main.py`: startup (`apply_pending`), the chat WebSocket (`blocked_reason`), and `GET /api/restore/status`, `GET|POST /api/snapshots/{id}/restore`, `POST /api/restore/cancel`, `/dismiss`, `/restart`, `/upload`, `/import`. A `RestoreError` becomes a 422 with its message.
- Tests: `tests/test_restore.py`.
