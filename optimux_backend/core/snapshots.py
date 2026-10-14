"""Snapshots: copies of Optimux's data or of the Workspace, kept on this machine.

Three kinds:
- full:      everything below in one archive (the default in the Dashboard, so
             nothing is forgotten; one moment in time for data and projects).
- data:      settings, models and routing, memory, skills, the database (chats,
             usage and costs, memory history, sign-ins), and the keys
             (secrets.env, auth.json).
- workspace: the project folders (all of them, or the ones chosen), each with
             its project memory file.

A snapshot is one .tar.gz in SNAPSHOT_DIR, plus a .json file next to it that
describes it (so listing never opens the archives). Every archive starts with
manifest.json (what is inside, for restoring) and BACKUP-README.txt (the same
for people, with how to restore by hand); it ends with SHA256SUMS, the checksum
of every file (check it with `sha256sum -c SHA256SUMS` after unpacking).

Every file is stored as a full copy (hard links too), symlinks as links (never
followed). A file that changes size while it is copied is stored at the size
it had when the copy began and listed as "changed"; files that can't be read
are listed as "unreadable". Both lists are in the snapshot's description.

Taking a snapshot runs in a background thread: the archive is written to
<id>.tar.gz.partial and renamed when complete, so a crash never leaves a
half-written snapshot that looks finished. Space is checked first against the
free disk and the size limit (settings: snapshot_limit_gb, default 5);
automatic snapshots are removed oldest first to make room, the user's own never.
"""
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import threading
import time

from optimux_backend.core import paths

FORMAT = "optimux-snapshot"
FORMAT_VERSION = 1
KINDS = ("full", "data", "workspace")
KIND_LABELS = {"full": "Everything", "data": "Data", "workspace": "Workspace"}
DEFAULT_LIMIT_GB = 5
FREE_MARGIN = 200 * 1024 * 1024      # never fill the disk to the last byte

# Data snapshot: (path under OPTIMUX_HOME, what it is). The database is copied
# with SQLite's backup API, never as a raw file, so it is consistent while in use.
DATA_ITEMS = (
    ("config", "Settings, models, routing and budgets"),
    ("memory", "Personal, professional and project memory"),
    ("skills", "Global skills"),
    ("data/optimux.db", "Database: chats, usage and costs, memory history, sign-ins"),
    ("secrets.env", "API keys, the access token and the GitHub token"),
    ("auth.json", "Password and two-step codes"),
)
DATA_LEFT_OUT = ("backups/ (daily database copies)", "snapshots/ (the snapshots themselves)",
                 "logs/", "legacy/ (old files moved aside)")

# Folders a project can rebuild (npm install, pip install, ...): skipped by default
REBUILDABLE = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
                         ".mypy_cache", ".ruff_cache", ".tox"})

ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}-(full|data|workspace)(-\d+)?$")

_running: set[str] = set()
_lock = threading.Lock()


class SnapshotError(Exception):
    """A snapshot that can't be taken or found; the message is shown to the user."""


# ------------------------------------------------------------------ settings and space

def limit_bytes() -> int:
    from optimux_backend.core.config_manager import get_settings
    try:
        gb = float(get_settings().get("snapshot_limit_gb", DEFAULT_LIMIT_GB))
    except (TypeError, ValueError):
        gb = DEFAULT_LIMIT_GB
    return int(max(gb, 0.1) * 1024 ** 3)


def _ensure_dir() -> str:
    os.makedirs(paths.SNAPSHOT_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(paths.SNAPSHOT_DIR, stat.S_IRWXU)
    except OSError:
        pass
    return paths.SNAPSHOT_DIR


def free_bytes() -> int:
    folder = paths.SNAPSHOT_DIR if os.path.isdir(paths.SNAPSHOT_DIR) else paths.HOME
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return 0


def used_bytes() -> int:
    return sum(s["size_bytes"] for s in list_snapshots())


# ------------------------------------------------------------------ what goes in

def _walk(top: str, skip_rebuildable: bool, errors: list[str] | None = None) -> tuple[list[str], int, int, int]:
    """Files, symlinks and folders under top (never following links), with the total
    size; and how much the skipped rebuildable folders hold. Folders that can't be
    read are added to errors."""
    entries, size, skipped_dirs, skipped_bytes = [top], 0, 0, 0
    onerror = (lambda e: errors.append(e.filename)) if errors is not None else None
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False, onerror=onerror):
        keep = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if skip_rebuildable and d in REBUILDABLE and not os.path.islink(full):
                skipped_dirs += 1
                skipped_bytes += _tree_size(full)
            else:
                keep.append(d)
        dirnames[:] = sorted(keep)
        for d in dirnames:
            entries.append(os.path.join(dirpath, d))
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                entries.append(full)
                size += st.st_size if stat.S_ISREG(st.st_mode) else 0
    return entries, size, skipped_dirs, skipped_bytes


def _tree_size(top: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(top, followlinks=False):
        for f in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, f))
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
            except OSError:
                pass
    return total


def _project_memory(name: str) -> str:
    return os.path.join(paths.MEMORY_DIR, "projects", f"{name}.md")


def _workspace_root() -> str:
    from optimux_backend.core import workspace
    try:
        return workspace.projects_root()
    except workspace.WorkspaceError as e:
        raise SnapshotError(str(e))


def _linked_projects(root: str) -> list[str]:
    """Project folders that are links to another folder: never followed."""
    from optimux_backend.core import workspace
    return [n for n in workspace.project_names(root) if os.path.islink(os.path.join(root, n))]


def _choose_projects(root: str, projects: list[str] | None) -> list[str]:
    from optimux_backend.core import workspace
    linked = set(_linked_projects(root))
    available = [n for n in workspace.project_names(root) if n not in linked]
    if not projects:
        return available
    links = [p for p in projects if p in linked]
    if links:
        raise SnapshotError(f"These projects are links to another folder, which snapshots don't follow: "
                            f"{', '.join(links)}")
    unknown = [p for p in projects if p not in available]
    if unknown:
        raise SnapshotError(f"Not in the Workspace: {', '.join(unknown)}")
    return [p for p in available if p in projects]


def plan(kind: str, projects: list[str] | None = None, skip_rebuildable: bool = True) -> dict:
    """What a snapshot would hold: the items (archive path, label, real path, files,
    bytes), the projects, the total size and whether it fits."""
    if kind not in KINDS:
        raise SnapshotError(f"Unknown snapshot type '{kind}' (full, data or workspace).")
    items, project_rows, skipped, left_out = [], [], {"dirs": 0, "bytes": 0}, []
    if kind in ("data", "full"):
        for rel, label in DATA_ITEMS:
            full = os.path.join(paths.HOME, rel)
            if not os.path.exists(full):
                continue
            if os.path.isdir(full):
                entries, size, _, _ = _walk(full, False)
                files = sum(1 for e in entries if not os.path.isdir(e) or os.path.islink(e))
            else:
                files, size = 1, os.path.getsize(full)
                if rel == "data/optimux.db" and os.path.exists(full + "-wal"):
                    size += os.path.getsize(full + "-wal")   # the copy includes what the WAL holds
            items.append({"path": rel, "label": label, "source": full, "files": files, "bytes": size,
                          "private": True})
        left_out += DATA_LEFT_OUT
    if kind in ("workspace", "full"):
        if kind == "full":
            root = _safe_workspace()       # no Workspace folder yet: Everything is the data alone
            if projects:
                raise SnapshotError("Everything holds all projects; choose Workspace to pick some.")
        else:
            root = _workspace_root()
        for name in _choose_projects(root, projects) if root else []:
            full = os.path.join(root, name)
            entries, size, sd, sb = _walk(full, skip_rebuildable)
            skipped["dirs"] += sd
            skipped["bytes"] += sb
            files = sum(1 for e in entries if not os.path.isdir(e) or os.path.islink(e))
            mem = _project_memory(name)
            has_mem = os.path.isfile(mem)
            items.append({"path": f"workspace/{name}", "label": f"Project {name}", "source": full,
                          "files": files, "bytes": size, "project": True})
            if has_mem and kind == "workspace":       # in full, memory/ already holds it
                items.append({"path": f"memory/projects/{name}.md", "label": f"Memory of {name}",
                              "source": mem, "files": 1, "bytes": os.path.getsize(mem)})
            project_rows.append({"name": name, "files": files, "bytes": size, "memory": has_mem,
                                 "git": os.path.isdir(os.path.join(full, ".git"))})
        if root:
            left_out += [f"{n}/ (a link to another folder; not followed)" for n in _linked_projects(root)]
    total = sum(i["bytes"] for i in items)
    # The archive can't be bigger than this: compression only shrinks the data, but
    # every file and folder adds a header (512 bytes, often 1 KB with long names)
    estimate = total + 1024 * (sum(i["files"] for i in items) + len(items)) + 64 * 1024
    used, limit, free = used_bytes(), limit_bytes(), free_bytes()
    reclaimable = sum(s["size_bytes"] for s in list_snapshots() if s.get("auto") and s["status"] == "ready")
    reason = ""
    if estimate + FREE_MARGIN > free:
        reason = "Not enough free disk space."
    elif used + estimate > limit and used - reclaimable + estimate > limit:
        reason = ("It would pass the snapshot size limit. Delete some snapshots, or raise the limit.")
    return {
        "kind": kind, "items": items, "projects": project_rows, "total_bytes": total, "estimate_bytes": estimate,
        "skip_rebuildable": skip_rebuildable, "skipped": skipped, "rebuildable": sorted(REBUILDABLE),
        "left_out": left_out,
        "used_bytes": used, "limit_bytes": limit, "free_bytes": free,
        "fits": not reason, "reason": reason,
    }


def preview(kind: str, projects: list[str] | None = None, skip_rebuildable: bool = True) -> dict:
    """plan() without the local paths of the sources."""
    p = plan(kind, projects, skip_rebuildable)
    p["items"] = [{k: v for k, v in i.items() if k not in ("source", "private")} for i in p["items"]]
    return p


# ------------------------------------------------------------------ the archive

def optimux_version() -> str:
    try:
        out = subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True,
                             timeout=3, cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def readme(manifest: dict) -> str:
    """BACKUP-README.txt: what is inside and how to restore it by hand."""
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(manifest["created"]))
    lines = [
        "OPTIMUX SNAPSHOT",
        "================",
        "",
        f"Type:     {KIND_LABELS.get(manifest['kind'], manifest['kind'])}",
        f"Taken:    {when}",
        f"Optimux:  {manifest['optimux_version']}",
    ]
    if manifest.get("note"):
        lines.append(f"Note:     {manifest['note']}")
    lines += ["", "WHAT IS INSIDE", "--------------"]
    for i in manifest["items"]:
        lines.append(f"  {i['path']:<40} {i['label']} ({i['files']} file{'s' if i['files'] != 1 else ''}, {_fmt(i['bytes'])})")
    lines.append("  SHA256SUMS                               Checksums: after unpacking, run  sha256sum -c SHA256SUMS")
    q = shlex.quote
    kind = manifest["kind"]
    home = q(manifest["home"])
    root = q(manifest["workspace"]) if manifest.get("workspace") else "<your Workspace folder>"
    has_data, has_projects = kind in ("data", "full"), kind in ("workspace", "full")
    lines.append("")
    if has_projects:
        names = ", ".join(p["name"] for p in manifest.get("projects", [])) or "(none)"
        lines.append(f"Projects: {names}")
        if manifest.get("skip_rebuildable"):
            lines.append("Skipped (rebuild them with npm install, pip install, ...): "
                         + ", ".join(manifest.get("rebuildable", [])))
    left = list(manifest.get("left_out") or [])
    if kind == "workspace":
        left = ["chats, usage and memory history (they are in a Data or Everything snapshot)"] + left
    if has_projects:
        left.append("archived projects")
    lines.append("Not included: " + "; ".join(left) + ".")
    if has_projects:
        lines.append("Databases inside projects are copied as files: a database in use may need a check after restore.")
    if manifest.get("includes_secrets"):
        lines += ["", "!! This file contains API keys and sign-in data. Keep it private."]
    lines += ["", "RESTORE", "-------"]
    if kind == "full":
        lines += ["If your Optimux has Dashboard -> Backups -> Restore, use it: you choose the parts,",
                  "it keeps your usage and cost history and takes a snapshot of the current state first."]
    elif kind == "data":
        lines += ["If your Optimux has Dashboard -> Backups -> Restore, use it: it keeps your",
                  "usage and cost history and takes a snapshot of the current state first."]
    else:
        lines.append("If your Optimux has Dashboard -> Backups -> Restore, use it.")
    lines += ["", "By hand" + (" (this also replaces the usage and cost history with the one in this file):"
                               if has_data else ":")]
    steps = []
    if has_data:
        steps.append("Stop Optimux:   optimux stop    (on a server: sudo optimux stop)")
    steps.append("Unpack into a new private folder:  mkdir -m 700 restore && tar -xzf <this file> -C restore")
    if has_data:
        tops = []
        for i in manifest["items"]:
            top = i["path"].split("/")[0]
            if top != "workspace" and top not in tops:
                tops.append(top)
        steps += [
            f"Move the current data folder aside (nothing is deleted):\n       mv {home} {home}.before-restore",
            f"Put the snapshot's data in its place:\n       mkdir -m 700 {home} && cp -a "
            + " ".join(f"restore/{t}" for t in tops) + f" {home}/",
            f"Bring back what a snapshot leaves out, if you want it:\n       cp -a {home}.before-restore/backups "
            f"{home}.before-restore/logs {home}.before-restore/legacy {home}/",
        ]
    if has_projects:
        mem = (f"\n       cp -a restore/memory/projects/<project>.md {home}/memory/projects/"
               if kind == "workspace" else "")
        steps += [
            # Into .archive with Optimux's own name format, so it shows under Archived projects
            f"For each project, move the current folder to the archive (nothing is deleted):\n"
            f"       mkdir -p {root}/.archive && mv {root}/<project> {root}/.archive/<project>-$(date +%Y%m%d-%H%M%S)",
            f"Put the snapshot's copy in its place:\n       cp -a restore/workspace/<project> {root}/" + mem,
        ]
    owner = [home] if has_data else []
    if has_projects:
        owner.append(f"{root}/<project>")
        if kind == "workspace":
            owner.append(f"{home}/memory")
    steps.append("On a server, give the files back to Optimux's user:  sudo chown -R optimux:optimux " + " ".join(owner))
    if has_data:
        steps.append("Start Optimux:  optimux start   (on a server: sudo optimux start)")
    lines += [f"  {n}. {t}" for n, t in enumerate(steps, 1)]
    return "\n".join(lines) + "\n"


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


class _Copy:
    """Reads exactly `size` bytes from a file, hashing them. A file that shrank or
    can't be read further is padded with zeros, so the archive stays valid."""

    def __init__(self, f, size: int):
        self.f, self.left, self.short = f, size, False
        self.sha = hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        n = self.left if n is None or n < 0 else min(n, self.left)
        data = b""
        if not self.short:
            try:
                data = self.f.read(n)
            except OSError:
                data = b""
            if len(data) < n:
                self.short = True
        data += b"\0" * (n - len(data))
        self.left -= n
        self.sha.update(data)
        return data


class _Writer:
    """Adds files to the archive: full copies (also of hard links), symlinks as
    links, owner names removed; records checksums and problems."""

    def __init__(self, tar: tarfile.TarFile):
        self.tar = tar
        self.sums: list[tuple[str, str]] = []
        self.changed: list[str] = []
        self.unreadable: list[str] = []

    def add(self, path: str, arcname: str, private: bool = False) -> None:
        self.tar.inodes.clear()            # never store a hard link: each file is a full copy
        try:
            info = self.tar.gettarinfo(path, arcname)
        except OSError:
            self.unreadable.append(arcname)
            return
        if not (info.isdir() or info.isreg() or info.issym()):
            return                         # sockets, pipes, devices: never
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        if private:                        # the data folder: readable only by its owner when unpacked
            info.mode = 0o700 if info.isdir() else 0o600
        if not info.isreg():
            self.tar.addfile(info)
            return
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            self.unreadable.append(arcname)
            return
        with os.fdopen(fd, "rb") as f:
            copy = _Copy(f, info.size)
            self.tar.addfile(info, copy)
            try:
                grew = os.fstat(fd).st_size != info.size
            except OSError:
                grew = False
        if copy.short or grew:
            self.changed.append(arcname)
        self.sums.append((copy.sha.hexdigest(), arcname))

    def checksums(self) -> bytes:
        return "".join(f"{h}  {n}\n" for h, n in self.sums).encode()


def _copy_database(target: str) -> None:
    from optimux_backend.core import store
    os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))   # private from the start
    src = store.connect()
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def _meta_path(sid: str) -> str:
    return os.path.join(paths.SNAPSHOT_DIR, f"{sid}.json")


def archive_path(sid: str) -> str:
    if not ID_RE.fullmatch(sid or ""):
        raise SnapshotError("No such snapshot.")
    return os.path.join(paths.SNAPSHOT_DIR, f"{sid}.tar.gz")


def _db_copy_path(sid: str) -> str:
    return os.path.join(paths.SNAPSHOT_DIR, f".{sid}.db")


_LEFTOVER = re.compile(r"^(?:\.(?P<db>.+)\.db|(?P<part>.+)\.tar\.gz\.partial)$")


def _sweep() -> None:
    """Remove what a snapshot interrupted by a stop left behind: the database copy
    (it holds chats and sign-ins) and the half-written archive."""
    try:
        names = os.listdir(paths.SNAPSHOT_DIR)
    except FileNotFoundError:
        return
    with _lock:
        busy = set(_running)
    for name in names:
        m = _LEFTOVER.match(name)
        if not m:
            continue
        sid = m.group("db") or m.group("part")
        if ID_RE.fullmatch(sid) and sid not in busy:
            try:
                os.unlink(os.path.join(paths.SNAPSHOT_DIR, name))
            except OSError:
                pass


def _write_meta(sid: str, meta: dict) -> None:
    paths.write_private(_meta_path(sid), json.dumps(meta, indent=2))


def _new_id(kind: str) -> str:
    base = time.strftime("%Y-%m-%d_%H%M%S", time.localtime()) + f"-{kind}"
    sid, n = base, 1
    while os.path.exists(_meta_path(sid)) or sid in _running:
        n += 1
        sid = f"{base}-{n}"
    return sid


def _make_room(needed: int) -> None:
    """Remove automatic snapshots, oldest first, until needed bytes fit the limit."""
    over = used_bytes() + needed - limit_bytes()
    for s in sorted((x for x in list_snapshots() if x.get("auto") and x["status"] == "ready"),
                    key=lambda x: x["created"]):
        if over <= 0:
            break
        delete(s["id"])
        over -= s["size_bytes"]


def create(kind: str, note: str = "", projects: list[str] | None = None, skip_rebuildable: bool = True,
           auto: bool = False, sid: str | None = None) -> dict:
    """Take a snapshot now (blocking). Returns its description."""
    p = plan(kind, projects, skip_rebuildable)
    if not p["fits"]:
        raise SnapshotError(p["reason"])
    if kind == "workspace" and not p["projects"]:
        raise SnapshotError("There are no projects in the Workspace.")
    _ensure_dir()
    with _lock:
        sid = sid or _new_id(kind)
        _running.add(sid)                  # so _sweep() never takes its files for leftovers
    try:
        return _create(kind, note, p, skip_rebuildable, auto, sid)
    finally:
        with _lock:
            _running.discard(sid)


def _create(kind: str, note: str, p: dict, skip_rebuildable: bool, auto: bool, sid: str) -> dict:
    _make_room(p["estimate_bytes"])
    manifest = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "id": sid, "kind": kind,
        "created": time.time(), "optimux_version": optimux_version(), "note": (note or "").strip()[:200],
        "auto": bool(auto), "home": paths.HOME,
        "workspace": _workspace_root() if kind == "workspace" else _safe_workspace(),
        "includes_secrets": kind in ("data", "full") and any(i["path"] in ("secrets.env", "auth.json") for i in p["items"]),
        "skip_rebuildable": skip_rebuildable if kind != "data" else False,
        "rebuildable": sorted(REBUILDABLE), "left_out": p["left_out"],
        "items": [{k: v for k, v in i.items() if k not in ("source", "private")} for i in p["items"]],
        "projects": p["projects"], "total_bytes": p["total_bytes"],
    }
    # The list shows what is inside from this file, without opening the archive
    meta = {k: manifest[k] for k in ("id", "kind", "created", "note", "auto", "includes_secrets",
                                      "optimux_version", "total_bytes", "items", "left_out", "skip_rebuildable")}
    meta["projects"] = [x["name"] for x in p["projects"]]
    meta["status"] = "running"
    _write_meta(sid, meta)
    final = archive_path(sid)
    partial = final + ".partial"
    db_copy = _db_copy_path(sid)
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz", compresslevel=6) as tar:
            _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode())
            _add_bytes(tar, "BACKUP-README.txt", readme(manifest).encode())
            w = _Writer(tar)
            for item in p["items"]:
                src, arc = item["source"], item["path"]
                if arc == "data/optimux.db":
                    _copy_database(db_copy)
                    w.add(db_copy, arc, private=True)
                elif os.path.isdir(src) and not os.path.islink(src):
                    errors: list[str] = []
                    entries, _, _, _ = _walk(src, skip_rebuildable and item.get("project", False), errors)
                    w.unreadable += [f"{arc}/{os.path.relpath(e, src)}" for e in errors if e]
                    for e in entries:
                        rel = os.path.relpath(e, src)
                        w.add(e, arc if rel == "." else f"{arc}/{rel}", item.get("private", False))
                else:
                    w.add(src, arc, item.get("private", False))
            _add_bytes(tar, "SHA256SUMS", w.checksums())
        os.replace(partial, final)
        meta.update(status="ready", size_bytes=os.path.getsize(final), finished=time.time(),
                    changed=w.changed[:50], changed_count=len(w.changed),
                    unreadable=w.unreadable[:50], unreadable_count=len(w.unreadable))
        _write_meta(sid, meta)
        return meta
    except Exception as e:
        for f in (partial, final):
            try:
                os.unlink(f)
            except FileNotFoundError:
                pass
        meta.update(status="failed", error=str(e)[:300], finished=time.time())
        _write_meta(sid, meta)
        if isinstance(e, SnapshotError):
            raise
        raise SnapshotError(f"The snapshot failed: {e}")
    finally:
        try:
            os.unlink(db_copy)
        except FileNotFoundError:
            pass


def _safe_workspace() -> str:
    try:
        return _workspace_root()
    except SnapshotError:
        return ""


def start(kind: str, note: str = "", projects: list[str] | None = None, skip_rebuildable: bool = True,
          auto: bool = False) -> str:
    """Check, then take the snapshot in a background thread. Returns its id."""
    p = plan(kind, projects, skip_rebuildable)
    if not p["fits"]:
        raise SnapshotError(p["reason"])
    if kind == "workspace" and not p["projects"]:
        raise SnapshotError("There are no projects in the Workspace.")
    with _lock:
        if _running:
            raise SnapshotError("A snapshot is already being taken. Wait for it to finish.")
        _ensure_dir()
        sid = _new_id(kind)
        _running.add(sid)
        # Listed (as running) from the start, before the thread has done anything
        _write_meta(sid, {"id": sid, "kind": kind, "created": time.time(), "note": (note or "").strip()[:200],
                          "auto": bool(auto), "status": "running",
                          "projects": p["projects"] and [x["name"] for x in p["projects"]] or []})

    def run():
        try:
            create(kind, note, projects, skip_rebuildable, auto, sid=sid)
        except Exception as e:
            # Failures while writing are already recorded; this records the ones before
            # (the plan changed since the check: a project removed, the disk filled, ...)
            try:
                with open(_meta_path(sid)) as f:
                    meta = json.load(f)
                if meta.get("status") == "running":
                    meta.update(status="failed", error=str(e)[:300], finished=time.time())
                    _write_meta(sid, meta)
            except (OSError, ValueError):
                pass
        finally:
            with _lock:
                _running.discard(sid)

    threading.Thread(target=run, name=f"snapshot-{sid}", daemon=True).start()
    return sid


# ------------------------------------------------------------------ listing and deleting

def list_snapshots() -> list[dict]:
    """Newest first. A snapshot left 'running' by a stopped server is 'failed'."""
    out = []
    try:
        names = os.listdir(paths.SNAPSHOT_DIR)
    except FileNotFoundError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        sid = name[:-5]
        if not ID_RE.fullmatch(sid):
            continue
        try:
            with open(_meta_path(sid)) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        meta["id"] = sid
        if meta.get("status") == "running" and sid not in _running:
            meta["status"] = "failed"
            meta.setdefault("error", "Interrupted (Optimux stopped while it was being taken).")
        arc = archive_path(sid)
        meta["size_bytes"] = os.path.getsize(arc) if meta.get("status") == "ready" and os.path.exists(arc) else 0
        if meta.get("status") == "ready" and not os.path.exists(arc):
            meta["status"] = "failed"
            meta["error"] = "The archive file is missing."
        if meta.get("status") == "ready" and "items" not in meta:
            _backfill(sid, meta)
        out.append(meta)
    return sorted(out, key=lambda m: m.get("created", 0), reverse=True)


# The first members of every archive: read without unpacking the rest
_HEAD_MEMBERS = ("manifest.json", "BACKUP-README.txt")
_HEAD_MAX = 1024 * 1024


def read_head_member(sid: str, name: str) -> str:
    """manifest.json or BACKUP-README.txt from a snapshot, as text. They are the
    first two members, so only the start of the archive is read."""
    if name not in _HEAD_MEMBERS:
        raise SnapshotError("Only manifest.json and BACKUP-README.txt can be read.")
    path = archive_path(sid)
    if sid in _running or not os.path.exists(path):
        raise SnapshotError("This snapshot is not ready.")
    try:
        with tarfile.open(path, mode="r|gz") as tar:      # stream: stops reading where it stops
            for n, member in enumerate(tar):
                if n >= len(_HEAD_MEMBERS):
                    break
                if member.name == name and member.isreg() and member.size <= _HEAD_MAX:
                    return tar.extractfile(member).read().decode("utf-8", "replace")
    except (OSError, tarfile.TarError, EOFError) as e:
        raise SnapshotError(f"The snapshot file can't be read: {e}")
    raise SnapshotError(f"{name} is not in this snapshot.")


def _backfill(sid: str, meta: dict) -> None:
    """Snapshots taken before the description kept their parts: copy them once
    from the archive's manifest, so the list can show them."""
    try:
        manifest = json.loads(read_head_member(sid, "manifest.json"))
    except (SnapshotError, ValueError):
        return
    for key in ("items", "left_out", "skip_rebuildable"):
        if key in manifest:
            meta[key] = manifest[key]
    meta.setdefault("projects", [p.get("name") for p in manifest.get("projects", []) if isinstance(p, dict)])
    stored = {k: v for k, v in meta.items() if k not in ("size_bytes",)}
    try:
        _write_meta(sid, stored)
    except OSError:
        pass


def get(sid: str) -> dict:
    for s in list_snapshots():
        if s["id"] == sid:
            return s
    raise SnapshotError("No such snapshot.")


def delete(sid: str) -> None:
    archive = archive_path(sid)            # validates the id
    if sid in _running:
        raise SnapshotError("This snapshot is still being taken.")
    found = False
    for f in (archive, archive + ".partial", _meta_path(sid), _db_copy_path(sid)):
        try:
            os.unlink(f)
            found = found or not f.endswith(".db")
        except FileNotFoundError:
            pass
    if not found:
        raise SnapshotError("No such snapshot.")


def summary() -> dict:
    _sweep()
    snaps = list_snapshots()
    return {"snapshots": snaps, "used_bytes": sum(s["size_bytes"] for s in snaps),
            "limit_bytes": limit_bytes(), "free_bytes": free_bytes(), "dir": paths.SNAPSHOT_DIR,
            "running": bool(_running)}
