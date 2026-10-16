"""Restoring snapshots (Dashboard → Backups → Restore).

A restore always starts from a snapshot in the list; a downloaded file is first
uploaded and imported into the list (decrypted with its key when it is an .age
file, then checked like any other snapshot).

The rules:
- The archive is checked while it is read: every member must belong to a part
  the manifest lists, no absolute paths, no "..", only folders, files and
  symbolic links, and every file must match SHA256SUMS. Nothing is ever
  written through a symbolic link.
- The user chooses the parts and types CONFIRM_PHRASE.
- Before anything changes, a Data snapshot of the current state is taken (when
  data parts are restored); projects are moved to the Workspace archive (they
  show under Archived projects). Nothing is deleted.
- Usage and cost history, and the current sign-ins, are never rolled back: the
  restored database gets the current ones.
- Projects are restored at once. Data (settings, memory, skills, database,
  keys) is staged in ~/.optimux/restore-pending and put in place when Optimux
  next starts (apply_pending, called before any module reads config or the
  database); until then no new chat turn starts. The current files are moved
  to ~/.optimux/legacy/before-restore-<time>.
- A restore is refused while a reply is running, while a snapshot is being
  taken, or while another restore is waiting for the restart.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tarfile
import threading
import time

from optimux_backend.core import paths

CONFIRM_PHRASE = "yes I am sure I want to restore the backup"
PENDING_NAME = "restore-pending"
STAGING_NAME = "restore-staging"
LAST_NAME = "restore-last.json"
PLAN_NAME = "plan.json"
PROJECT_MEMORY_NAME = "project-memory"   # in restore-pending: project memory files put back after the database
UPLOAD_TTL = 3600
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
HEAD_FILES = ("manifest.json", "BACKUP-README.md", "BACKUP-README.txt")
SUMS = "SHA256SUMS"
MAX_HEAD = 1024 * 1024
MAX_SUMS = 64 * 1024 * 1024
MAX_MEMBERS = 2_000_000
MAX_EXTENDED_HEADER = 1024 * 1024       # long names and pax headers are read whole by tarfile
FREE_MARGIN = 200 * 1024 * 1024

# Parts of the data folder, in the order they are put back (paths under OPTIMUX_HOME)
DATA_PARTS = ("config", "memory", "skills", "data/optimux.db", "secrets.env", "auth.json")
# Off by default: an older copy would bring back keys or a password that were changed
SENSITIVE = {"secrets.env": "API keys, the access token and the GitHub token",
             "auth.json": "the sign-in password and two-step codes"}

_lock = threading.Lock()
_busy = False


class RestoreError(Exception):
    """A restore that can't be done; the message is shown to the user."""


# ------------------------------------------------------------------ places

def _home(*parts: str) -> str:
    return os.path.join(paths.HOME, *parts)


def pending_dir() -> str:
    return _home(PENDING_NAME)


def uploads_dir() -> str:
    folder = os.path.join(paths.SNAPSHOT_DIR, "uploads")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, stat.S_IRWXU)
    return folder


def busy() -> bool:
    """A restore is running right now (chat turns wait)."""
    return _busy


def pending() -> dict | None:
    """The data restore waiting for the restart, or None."""
    try:
        with open(os.path.join(pending_dir(), PLAN_NAME)) as f:
            plan = json.load(f)
        return plan if isinstance(plan, dict) else None
    except (OSError, ValueError):
        return None


def blocked_reason() -> str:
    """Why a chat turn can't start now ("" when it can)."""
    if _busy:
        return "A backup is being restored. Wait for it to finish."
    if pending():
        return "A restore is waiting for Optimux to restart (Dashboard → Backups). Restart it, or cancel the restore."
    return ""


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def _aside_dir(prefix: str) -> str:
    base = _home("legacy", f"{prefix}-{_stamp()}")
    target, n = base, 1
    while os.path.lexists(target):
        n += 1
        target = f"{base}-{n}"
    os.makedirs(target, mode=0o700)
    os.chmod(_home("legacy"), stat.S_IRWXU)
    return target


def _rmtree(path: str) -> None:
    """Remove a folder Optimux made for itself (staging, a failed import), never a user folder."""
    if os.path.islink(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.lexists(path):
        os.unlink(path)


# ------------------------------------------------------------------ reading an archive

def _part_ok(path: str) -> bool:
    """A part path a manifest may list."""
    if path in DATA_PARTS:
        return True
    m = re.fullmatch(r"workspace/([^/]+)", path) or re.fullmatch(r"memory/projects/([^/]+)\.md", path)
    return bool(m and NAME_RE.fullmatch(m.group(1)))


def _clean_name(name: str) -> str:
    """The member's path, or an error for anything that could escape the folder."""
    if not name or "\x00" in name or name.startswith("/") or len(name) > 4096:
        raise RestoreError(f"The archive holds an unsafe path: {name[:200]!r}.")
    name = name.rstrip("/")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise RestoreError(f"The archive holds an unsafe path: {name[:200]!r}.")
    return name


def _check_manifest(manifest) -> dict:
    from optimux_backend.core import snapshots
    if not isinstance(manifest, dict) or manifest.get("format") != snapshots.FORMAT:
        raise RestoreError("This is not an Optimux snapshot (manifest.json is missing or of another kind).")
    if not isinstance(manifest.get("format_version"), int) or manifest["format_version"] > snapshots.FORMAT_VERSION:
        raise RestoreError("This snapshot was made by a newer Optimux. Update Optimux, then restore it.")
    if manifest.get("kind") not in snapshots.KINDS or not isinstance(manifest.get("items"), list):
        raise RestoreError("The snapshot's manifest is damaged.")
    for item in manifest["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not _part_ok(item["path"]):
            raise RestoreError("The snapshot's manifest lists an unknown part.")
    return manifest


def _ensure_parents(root: str, rel: str) -> None:
    """Create the folders above rel inside root. A folder in the way that is a
    symbolic link (or not a folder) stops the restore: nothing is ever written
    through a link."""
    full = root
    for part in rel.split("/")[:-1]:
        full = os.path.join(full, part)
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            os.mkdir(full, 0o700)
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise RestoreError(f"The archive writes through a link or file: {rel[:200]!r}.")


class _SafeInfo(tarfile.TarInfo):
    """tarfile reads a long-name or pax header whole into memory: a tiny archive
    could claim gigabytes. Such headers are refused above MAX_EXTENDED_HEADER."""

    def _proc_gnulong(self, tarfile_):
        if self.size > MAX_EXTENDED_HEADER:
            raise tarfile.HeaderError("an oversized long-name header")
        return super()._proc_gnulong(tarfile_)

    def _proc_pax(self, tarfile_):
        if self.size > MAX_EXTENDED_HEADER:
            raise tarfile.HeaderError("an oversized pax header")
        return super()._proc_pax(tarfile_)


def _is_data(name: str) -> bool:
    """A member of a data part (the data folder: never a symbolic link there)."""
    return name.startswith("memory/projects/") or any(name == p or name.startswith(p + "/") for p in DATA_PARTS)


def scan(archive: str, extract_to: str | None = None, select=None, free: int | None = None) -> dict:
    """Read the whole archive once: check every member and every checksum, and
    extract the members select(name) accepts into extract_to (a new, empty
    folder). Returns {manifest, files, bytes, extracted}. Raises RestoreError.
    Symbolic links are kept only inside projects (as links, never followed);
    in the data folder a link could point the keys or the database elsewhere,
    so links there are left out (listed in skipped_links)."""
    manifest = None
    item_paths: list[str] = []
    seen: set[str] = set()
    sums: dict[str, str] = {}
    listed: dict[str, str] | None = None
    dirs: list[tuple[str, int]] = []
    skipped_links: list[str] = []
    files = total = extracted = 0
    try:
        with tarfile.open(archive, mode="r|gz", tarinfo=_SafeInfo) as tar:
            for n, member in enumerate(tar):
                if n >= MAX_MEMBERS:
                    raise RestoreError("The archive holds too many files.")
                if listed is not None:
                    raise RestoreError("The archive has data after its checksums (SHA256SUMS must come last).")
                name = _clean_name(member.name)
                if name in seen:
                    raise RestoreError(f"The archive holds {name[:200]!r} twice.")
                seen.add(name)
                if n == 0:
                    if name != "manifest.json" or not member.isreg() or member.size > MAX_HEAD:
                        raise RestoreError("This is not an Optimux snapshot (manifest.json must come first).")
                    try:
                        manifest = _check_manifest(json.loads(tar.extractfile(member).read()))
                    except ValueError:
                        raise RestoreError("The snapshot's manifest is damaged.")
                    item_paths = [i["path"] for i in manifest["items"]]
                    continue
                if name in HEAD_FILES:
                    if not member.isreg() or member.size > MAX_HEAD:
                        raise RestoreError("The archive's README is damaged.")
                    continue
                if name == SUMS:
                    if not member.isreg() or member.size > MAX_SUMS:
                        raise RestoreError("The archive's checksums are damaged.")
                    listed = {}
                    for raw in tar.extractfile(member):       # line by line: never the whole file at once
                        line = raw.decode("utf-8", "replace").rstrip("\n")
                        h, sep, fname = line.partition("  ")
                        if not sep or not re.fullmatch(r"[0-9a-f]{64}", h):
                            raise RestoreError("The archive's checksums are damaged.")
                        listed[fname] = h
                    continue
                if not any(name == p or name.startswith(p + "/") for p in item_paths):
                    raise RestoreError(f"The archive holds a file outside the parts it lists: {name[:200]!r}.")
                if not (member.isreg() or member.isdir() or member.issym()) or member.issparse():
                    raise RestoreError(f"The archive holds a special file (only folders, files and links are "
                                       f"allowed): {name[:200]!r}.")
                if member.issym() and _is_data(name):
                    skipped_links.append(name)       # never created: see the docstring
                    continue
                take = extract_to is not None and select is not None and select(name)
                dest = os.path.join(extract_to, name) if take else None
                if take:
                    _ensure_parents(extract_to, name)
                    if os.path.lexists(dest):
                        if not (member.isdir() and os.path.isdir(dest) and not os.path.islink(dest)):
                            raise RestoreError(f"The archive writes {name[:200]!r} twice.")
                if member.isdir():
                    if take and not os.path.lexists(dest):
                        os.mkdir(dest, 0o700)
                    if take:
                        dirs.append((dest, member.mode))
                    continue
                if member.issym():
                    if take:
                        os.symlink(member.linkname, dest)
                        extracted += 1
                    continue
                files += 1
                total += member.size
                if take and free is not None and total > free:
                    raise RestoreError("Not enough free disk space to restore these parts.")
                src = tar.extractfile(member)
                sha = hashlib.sha256()
                out = None
                if take:
                    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                    out = os.fdopen(fd, "wb")
                try:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        sha.update(chunk)
                        if out:
                            out.write(chunk)
                finally:
                    if out:
                        out.close()
                if take:
                    os.chmod(dest, (member.mode & 0o777) | 0o600)
                    try:
                        os.utime(dest, (member.mtime, member.mtime))
                    except OSError:
                        pass
                    extracted += 1
                sums[name] = sha.hexdigest()
    except RestoreError:
        raise
    except (OSError, tarfile.TarError, EOFError, UnicodeError) as e:
        raise RestoreError(f"The snapshot file is damaged or incomplete: {e}")
    if manifest is None:
        raise RestoreError("This is not an Optimux snapshot (manifest.json is missing).")
    if listed is None:
        raise RestoreError("The snapshot is incomplete: its checksums (SHA256SUMS) are missing.")
    bad = [n for n, h in sums.items() if listed.get(n) != h]
    missing = [n for n in listed if n not in sums]
    if bad or missing:
        example = (bad or missing)[0]
        raise RestoreError(f"The snapshot doesn't match its checksums ({len(bad) + len(missing)} files, "
                           f"for example {example[:200]!r}). It may be damaged: nothing was restored.")
    for path, mode in reversed(dirs):       # folders last, so their files could be written
        try:
            os.chmod(path, (mode & 0o777) | 0o700)
        except OSError:
            pass
    return {"manifest": manifest, "files": files, "bytes": total, "extracted": extracted,
            "skipped_links": skipped_links}


def check_database(path: str) -> None:
    """The restored database opens, passes SQLite's check, and is not from a newer Optimux."""
    from optimux_backend.core import store
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RestoreError("The database in the snapshot is damaged.")
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise RestoreError(f"The database in the snapshot can't be read: {e}")
    if row and int(row[0]) > len(store.MIGRATIONS):
        raise RestoreError("The database in the snapshot is from a newer Optimux. Update Optimux, then restore it.")


# ------------------------------------------------------------------ what can be restored

def inspect(sid: str) -> dict:
    """The parts of a snapshot, what restoring each one would do now, and the defaults."""
    from optimux_backend.core import snapshots, workspace
    meta = snapshots.get(sid)
    if meta.get("status") != "ready":
        raise RestoreError("This snapshot is not ready.")
    try:
        manifest = _check_manifest(json.loads(snapshots.read_head_member(sid, "manifest.json")))
    except (snapshots.SnapshotError, ValueError) as e:
        raise RestoreError(f"This snapshot can't be read: {e}")
    try:
        root = workspace.projects_root()
    except workspace.WorkspaceError:
        root = ""
    current_projects = set(workspace.project_names(root)) if root else set()
    parts = []
    for item in manifest["items"]:
        path = item["path"]
        if path.startswith("memory/projects/"):
            continue                        # comes with its project
        row = {"path": path, "label": item.get("label", path), "files": item.get("files"),
               "bytes": item.get("bytes")}
        if path.startswith("workspace/"):
            name = path.split("/", 1)[1]
            row.update(kind="project", name=name, exists=name in current_projects, default=True)
            if name in current_projects and os.path.islink(os.path.join(root, name)):
                row.update(blocked="The current project is a link to another folder; Optimux doesn't replace it.",
                           default=False)
            elif not root:
                row.update(blocked="Set the Workspace folder first (Dashboard → Scope).", default=False)
            row["effect"] = ("The current folder moves to Archived projects first." if row["exists"]
                             else "A new project folder.")
        else:
            row.update(kind="data", exists=os.path.lexists(_home(path)), default=path not in SENSITIVE)
            if path in SENSITIVE:
                row["note"] = (f"Off by default: this brings back {SENSITIVE[path]} as they were in the snapshot "
                               "(turn it on for a new server, or if you lost them).")
            if path == "data/optimux.db":
                row["note"] = ("Chats, memory history and the list of deleted projects come back. Your usage and "
                               "cost history and your current sign-ins are kept as they are now.")
            row["effect"] = "The current copy moves to the legacy folder when Optimux restarts."
        parts.append(row)
    return {"id": sid, "kind": manifest["kind"], "created": manifest.get("created"),
            "optimux_version": manifest.get("optimux_version"), "note": manifest.get("note", ""),
            "parts": parts, "confirm_phrase": CONFIRM_PHRASE, "pending": bool(pending()),
            "workspace": root}


# ------------------------------------------------------------------ restore

def _workspace_free(root: str) -> int:
    try:
        return shutil.disk_usage(root).free
    except OSError:
        return 0


def _put_project(staging: str, root: str, name: str, archived: list[str]) -> None:
    """Put one staged project in the Workspace. It is first copied next to its place
    (the Workspace may be on another disk), and only then is the current folder
    archived and the copy renamed into place: a failed copy changes nothing."""
    from optimux_backend.core import workspace
    src = os.path.join(staging, "workspace", name)
    if not os.path.isdir(src) or os.path.islink(src):
        raise RestoreError(f"The snapshot's copy of {name} is not a folder.")
    target = os.path.join(root, name)
    if os.path.islink(target):
        raise RestoreError(f"The current project {name} is a link to another folder; Optimux doesn't replace it.")
    tmp = os.path.join(root, f".restore-{name}-{secrets.token_hex(4)}")
    try:
        shutil.move(src, tmp)
    except OSError as e:
        _rmtree(tmp)                        # a partial copy of the snapshot, never the user's folder
        raise RestoreError(f"Couldn't copy {name} into the Workspace: {e.strerror or e}.")
    try:
        if os.path.lexists(target):
            archived.append(workspace.archive_project(name)["archived_to"])
        os.rename(tmp, target)
    except (OSError, workspace.WorkspaceError):
        _rmtree(tmp)
        raise


def restore(sid: str, parts: list[str], confirm: str, replies_running=False) -> dict:
    """Restore the chosen parts of a snapshot (see the module's rules).
    replies_running: a bool, or a function checked once chat turns are blocked."""
    global _busy
    from optimux_backend.core import snapshots, workspace
    if (confirm or "").strip() != CONFIRM_PHRASE:
        raise RestoreError(f'Type "{CONFIRM_PHRASE}" to confirm.')
    if pending():
        raise RestoreError("Another restore is waiting for the restart. Restart Optimux, or cancel it first.")
    if snapshots.summary()["running"]:
        raise RestoreError("A snapshot is being taken. Wait for it to finish.")
    info = inspect(sid)
    available = {p["path"]: p for p in info["parts"]}
    chosen = [p for p in dict.fromkeys(parts or []) if p in available]
    unknown = [p for p in parts or [] if p not in available]
    if unknown:
        raise RestoreError(f"Not in this snapshot: {', '.join(unknown)}")
    if not chosen:
        raise RestoreError("Choose at least one part to restore.")
    blocked = [available[p] for p in chosen if available[p].get("blocked")]
    if blocked:
        raise RestoreError(blocked[0]["blocked"])
    data = [p for p in DATA_PARTS if p in chosen]
    projects = [available[p]["name"] for p in chosen if available[p]["kind"] == "project"]
    need = sum(available[p].get("bytes") or 0 for p in chosen)
    if snapshots.free_bytes() < need + FREE_MARGIN:
        raise RestoreError("Not enough free disk space to restore these parts.")
    root = workspace.projects_root() if projects else ""
    project_bytes = sum(available[f"workspace/{n}"].get("bytes") or 0 for n in projects)
    if projects and _workspace_free(root) < project_bytes + FREE_MARGIN:
        raise RestoreError("Not enough free disk space in the Workspace to restore these projects.")

    with _lock:
        if _busy:
            raise RestoreError("A restore is already running.")
        _busy = True                        # from here on no chat turn starts
    snapshots.hold(sid)                     # never removed to make room, nor deleted, meanwhile
    staging = _home(STAGING_NAME)
    try:
        if replies_running() if callable(replies_running) else replies_running:
            raise RestoreError("A reply is running. Wait for it to finish (or stop it), then restore.")
        # 1. Check the whole archive and extract the chosen parts (nothing changes yet)
        _rmtree(staging)
        os.makedirs(staging, mode=0o700)
        memory_of = {f"memory/projects/{n}.md": n for n in projects}
        prefixes = data + [f"workspace/{n}" for n in projects]

        def select(name: str) -> bool:
            return name in memory_of or any(name == p or name.startswith(p + "/") for p in prefixes)

        found = scan(snapshots.archive_path(sid), staging, select, free=snapshots.free_bytes() - FREE_MARGIN)
        if "data/optimux.db" in data:
            check_database(os.path.join(staging, "data", "optimux.db"))

        # 2. The current state, before anything changes (the user's own snapshot: never removed automatically)
        before = None
        if data:
            try:
                before = snapshots.create("data", note=f"Before restoring {sid}")["id"]
            except snapshots.SnapshotError as e:
                raise RestoreError(f"The snapshot of the current state, taken before every restore, failed: {e} "
                                   "Nothing was restored.")

        # 3. Projects: now. The current folder goes to the Workspace archive (Archived projects)
        done, archived, memory_later = [], [], []
        try:
            for name in projects:
                _put_project(staging, root, name, archived)
                done.append(name)
                mem = os.path.join(staging, "memory", "projects", f"{name}.md")
                if not os.path.isfile(mem) or os.path.islink(mem) or "memory" in data:
                    continue                # no memory file, or the whole memory part comes back
                if "data/optimux.db" in data:
                    memory_later.append(name)   # its history entry belongs in the restored database
                    continue
                from optimux_backend.core import curator
                with open(mem, encoding="utf-8", errors="replace") as f:
                    curator.write_memory(name, f.read(), "restore", summary=f"Restored from snapshot {sid}")
        except (RestoreError, workspace.WorkspaceError, OSError) as e:
            what = f" Already restored: {', '.join(done)}." if done else ""
            kept = " The earlier copies are under Archived projects." if archived else ""
            raise RestoreError(f"{e}{what}{kept} The data parts were not restored.")

        # 4. Data: staged, put in place at the next start
        if data:
            pend = pending_dir()
            _rmtree(pend)
            os.makedirs(pend, mode=0o700)
            for part in data:
                src = os.path.join(staging, part)
                if not os.path.lexists(src):
                    continue
                dst = os.path.join(pend, part)
                os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
                os.replace(src, dst)
            for name in memory_later:
                dst = os.path.join(pend, PROJECT_MEMORY_NAME, f"{name}.md")
                os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
                os.replace(os.path.join(staging, "memory", "projects", f"{name}.md"), dst)
            plan = {"source": sid, "parts": data, "before_snapshot": before, "created": time.time(),
                    "projects": done, "project_memory": memory_later}
            paths.write_private(os.path.join(pend, PLAN_NAME), json.dumps(plan, indent=2))
        return {"source": sid, "projects": done, "archived": archived, "before_snapshot": before,
                "data": data, "restart_needed": bool(data), "skipped_links": found["skipped_links"][:20]}
    finally:
        _rmtree(staging)
        snapshots.release(sid)
        with _lock:
            _busy = False


def cancel_pending() -> str:
    """Drop a staged data restore: its files move to the legacy folder (nothing is deleted)."""
    if not pending():
        raise RestoreError("No restore is waiting.")
    target = _aside_dir("cancelled-restore")
    os.replace(pending_dir(), os.path.join(target, PENDING_NAME))
    return target


# ------------------------------------------------------------------ at start

def _merge_database(new: str, current: str) -> None:
    """Bring the restored database up to date and give it the current usage and
    cost history and the current sign-ins (never rolled back)."""
    from optimux_backend.core import store
    conn = sqlite3.connect(new)
    conn.row_factory = sqlite3.Row
    try:
        store._migrate(conn)
        if os.path.exists(current):
            conn.execute("ATTACH DATABASE ? AS cur", (current,))
            for table in ("usage", "logins"):
                cols = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
                have = {r[1] for r in conn.execute(f"PRAGMA cur.table_info({table})")}
                common = ", ".join(c for c in cols if c in have)
                with conn:
                    conn.execute(f"DELETE FROM main.{table}")
                    if common:
                        conn.execute(f"INSERT INTO main.{table} ({common}) SELECT {common} FROM cur.{table}")
            conn.execute("DETACH DATABASE cur")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def apply_pending() -> list[str]:
    """Put a staged data restore in place. Called at start, before anything reads
    config, keys or the database. Returns lines for the log; never raises (a
    failure here must not stop Optimux from starting)."""
    try:
        return _apply_pending()
    except Exception as e:                         # e.g. the disk is full: report, and start anyway
        return [f"restore: could not be applied ({e}); the staged files stay in {pending_dir()}"]


def _apply_pending() -> list[str]:
    plan = pending()
    if plan is None:
        if os.path.isdir(pending_dir()):           # damaged: kept aside, never applied half-way
            target = _aside_dir("damaged-restore")
            os.replace(pending_dir(), os.path.join(target, PENDING_NAME))
            return [f"restore: the staged restore was damaged and was not applied (moved to {target})"]
        return []
    from optimux_backend.core import store
    pend = pending_dir()
    aside = _aside_dir("before-restore")
    moved: list[tuple[str, str]] = []              # (current place, where it went), to undo on failure
    placed: list[tuple[str, str]] = []             # (staged, current place)

    def move_aside(path: str, rel: str) -> None:
        if os.path.lexists(path):
            dst = os.path.join(aside, rel)
            os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
            os.replace(path, dst)
            moved.append((path, dst))

    def put(src: str, cur: str) -> None:
        os.makedirs(os.path.dirname(cur), mode=0o700, exist_ok=True)
        os.replace(src, cur)
        placed.append((src, cur))

    try:
        db_new = os.path.join(pend, "data", "optimux.db")
        if os.path.isfile(db_new):
            _merge_database(db_new, store.DB_PATH)
        for part in plan.get("parts", []):
            if part not in DATA_PARTS:
                continue
            is_db = part == "data/optimux.db"
            src, cur = os.path.join(pend, part), store.DB_PATH if is_db else _home(part)
            if not os.path.lexists(src):
                continue
            for suffix in ("", "-wal", "-shm") if is_db else ("",):
                move_aside(cur + suffix, part + suffix)
            put(src, cur)
        # Project memory restored with its project, recorded in the (restored) memory history
        for name in plan.get("project_memory", []):
            staged = os.path.join(pend, PROJECT_MEMORY_NAME, f"{name}.md")
            if not NAME_RE.fullmatch(name) or not os.path.isfile(staged) or os.path.islink(staged):
                continue
            cur = os.path.join(paths.MEMORY_DIR, "projects", f"{name}.md")
            before = ""
            if os.path.isfile(cur):
                with open(cur, encoding="utf-8", errors="replace") as f:
                    before = f.read()
            with open(staged, encoding="utf-8", errors="replace") as f:
                after = f.read()
            move_aside(cur, f"memory/projects/{name}.md")
            put(staged, cur)
            if before != after:
                conn = store.connect()
                try:
                    with conn:
                        conn.execute("INSERT INTO memory_versions (project, ts, source, summary, before, after) "
                                     "VALUES (?, ?, 'restore', ?, ?, ?)",
                                     (name, time.time(), f"Restored from snapshot {plan.get('source')}", before, after))
                finally:
                    conn.close()
        for secret in (paths.SECRETS_FILE, paths.AUTH_FILE, _home("secrets.env"), _home("auth.json")):
            if os.path.isfile(secret) and not os.path.islink(secret):
                os.chmod(secret, 0o600)
        os.replace(os.path.join(pend, PLAN_NAME), os.path.join(aside, PLAN_NAME))
        _rmtree(pend)                              # empty by now: everything was moved out
        result = {"ok": True, "ts": time.time(), "source": plan.get("source"), "parts": plan.get("parts", []),
                  "projects": plan.get("projects", []), "before_snapshot": plan.get("before_snapshot"),
                  "aside": aside}
        paths.write_private(_home(LAST_NAME), json.dumps(result, indent=2))
        return [f"restore: restored {', '.join(result['parts'])} from snapshot {result['source']}; "
                f"the previous files are in {aside}"]
    except Exception as e:
        undone = True
        for staged, cur in reversed(placed):       # undo: the restored parts back to staging,
            try:
                os.replace(cur, staged)
            except OSError:
                undone = False
        for cur, dst in reversed(moved):           # and the current ones back in place
            try:
                os.replace(dst, cur)
            except OSError:
                undone = False
        target = pend
        try:
            target = _aside_dir("failed-restore")
            os.replace(pend, os.path.join(target, PENDING_NAME))
        except OSError:
            pass
        state = ("nothing was changed" if undone else
                 f"it was only partly undone: check {aside} for files that were not put back")
        try:
            paths.write_private(_home(LAST_NAME), json.dumps(
                {"ok": False, "ts": time.time(), "source": plan.get("source"), "error": f"{str(e)[:300]}; {state}",
                 "aside": target}))
        except OSError:
            pass
        return [f"restore: FAILED ({e}); {state}; the staged files are in {target}"]


def last_result() -> dict | None:
    try:
        with open(_home(LAST_NAME)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def dismiss_last() -> None:
    try:
        os.unlink(_home(LAST_NAME))           # a notice only: the restored files stay
    except FileNotFoundError:
        pass


def supervised() -> bool:
    """Started by a service manager that starts Optimux again when it exits: set
    in the systemd units the installer writes (deploy/optimux.service, the user unit)."""
    return os.environ.get("OPTIMUX_SUPERVISED") == "1"


def status() -> dict:
    return {"pending": pending(), "last": last_result(), "busy": _busy, "confirm_phrase": CONFIRM_PHRASE,
            "can_restart": supervised()}


# ------------------------------------------------------------------ uploads

def new_upload() -> tuple[str, str]:
    """A token and the path an uploaded file is written to."""
    sweep_uploads()
    token = secrets.token_urlsafe(24)
    return token, os.path.join(uploads_dir(), f"{token}.upload")


def upload_path(token: str) -> str:
    if not TOKEN_RE.fullmatch(token or ""):
        raise RestoreError("This upload is not valid. Upload the file again.")
    path = os.path.join(uploads_dir(), f"{token}.upload")
    if not os.path.isfile(path):
        raise RestoreError("This upload has expired or doesn't exist. Upload the file again.")
    return path


def sweep_uploads(now: float | None = None) -> None:
    now = now or time.time()
    folder = os.path.join(paths.SNAPSHOT_DIR, "uploads")
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return
    for name in names:
        full = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(full) > UPLOAD_TTL:
                os.unlink(full)
        except OSError:
            pass


AGE_MAGIC = b"age-encryption.org/v1\n"


def is_encrypted(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(len(AGE_MAGIC)) == AGE_MAGIC


def parse_identity(text: str):
    """The age secret key from what the user pasted (or the key file Optimux gave)."""
    import pyrage
    keys = [w for w in re.split(r"\s+", text or "") if w.upper().startswith("AGE-SECRET-KEY-1")]
    if not keys:
        if "PRIVATE KEY" in (text or "").upper():
            raise RestoreError("SSH keys are not accepted here: never paste a private SSH key into a web page. "
                               "Decrypt the file on your computer (age -d -i ~/.ssh/id_ed25519 -o snapshot.tar.gz "
                               "<file>.age) and upload the .tar.gz.")
        raise RestoreError("Paste the key Optimux gave you (it starts with AGE-SECRET-KEY-1), or choose its key file.")
    try:
        return pyrage.x25519.Identity.from_str(keys[0].upper())
    except Exception:
        raise RestoreError("This key is not valid. Check that it was copied whole.")


def import_upload(token: str, key: str = "", filename: str = "") -> dict:
    """Turn an uploaded file into a snapshot in the list: decrypt it (with the
    key) when it is an .age file, check it completely, and describe it."""
    import pyrage
    from optimux_backend.core import snapshots
    path = upload_path(token)
    work = path + ".tar.gz"
    keep_upload = False                     # a wrong key can be corrected without uploading again
    try:
        if is_encrypted(path):
            keep_upload = True
            identity = parse_identity(key)
            if snapshots.free_bytes() < os.path.getsize(path) + FREE_MARGIN:
                raise RestoreError("Not enough free disk space to decrypt the file.")
            try:
                fd = os.open(work, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
                    pyrage.decrypt_io(src, dst, [identity])
            except pyrage.DecryptError:
                raise RestoreError("This key doesn't open the file. Use the key file that was made with this download "
                                   "(optimux-key-<snapshot>.txt).")
            keep_upload = False
            os.unlink(path)
        else:
            os.replace(path, work)
        info = scan(work)                   # the whole file: structure and every checksum
        manifest = info["manifest"]
        kind = manifest["kind"]
        snapshots._ensure_dir()
        size = os.path.getsize(work)
        if snapshots.used_bytes() + size > snapshots.limit_bytes():
            raise RestoreError("The file would pass the snapshot size limit. Delete some snapshots, or raise the limit.")
        with snapshots._lock:
            sid = snapshots._new_id(kind)
            os.replace(work, snapshots.archive_path(sid))
        taken = time.strftime("%Y-%m-%d %H:%M", time.localtime(manifest.get("created") or 0))
        label = (filename or "a file").strip()[:120]
        meta = {"id": sid, "kind": kind, "created": time.time(), "auto": False,
                "note": f"Uploaded: {label} (taken {taken})"[:200],
                "includes_secrets": bool(manifest.get("includes_secrets")),
                "optimux_version": manifest.get("optimux_version", "unknown"),
                "total_bytes": manifest.get("total_bytes", info["bytes"]),
                "items": manifest["items"], "left_out": manifest.get("left_out", []),
                "skip_rebuildable": manifest.get("skip_rebuildable", False),
                "home_excluded": bool(manifest.get("home_excluded")),
                "projects": [p.get("name") for p in manifest.get("projects", []) if isinstance(p, dict)],
                "status": "ready", "finished": time.time(), "uploaded": True, "original_id": manifest.get("id"),
                "original_created": manifest.get("created")}
        snapshots._write_meta(sid, meta)
        return snapshots.get(sid)
    finally:
        for f in ([work] if keep_upload else [path, work]):
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass
