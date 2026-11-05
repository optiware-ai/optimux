"""The Studio (v0.8): list, read and save the active project's files, and serve
them for the preview. Ported from OmniLatch's Files panel (p55-p81).

Everything is limited to one project folder inside the Workspace. Paths are
relative to the project, symlinks are followed and must stay inside it, and
`.git` and Optimux's own data folder are never listed, read, written or served
(the same boundary as the models' file tools, files.resolve_target). Saving goes
through files.prepare_user_save and files.apply: atomic, and refused when the
file changed since it was opened.

Saved keys never reach the page (Nothing leaks): a file is shown with each saved
key as ••••(NAME), a save writes the mark back as the key, and the preview never
serves files that hold secrets (.env, *.pem, *.key, ...) and hides saved keys in
the text it serves.

Git markers come from `git status` (the whole project) and `git check-ignore`
(per listed folder). A project that isn't its own git repository has none.
"""
import hashlib
import hmac
import mimetypes
import os
import secrets
import subprocess

from optimux_backend.core import config_manager, files as filetools, paths, trash
from optimux_backend.core.changes import SECRET_NAME
from optimux_backend.core.security import ScopeError, mask_secrets, resolve_project_dir

MAX_ENTRIES = 2000        # entries listed per folder; the rest are counted
MAX_STATUS = 5000         # changed files reported by git_status
GIT_TIMEOUT = 10          # seconds for each git call; a slower project gets no markers
HIDDEN = {".git"}         # never listed
PREVIEW_MASK_BYTES = 5_000_000   # text served to the preview up to this size has saved keys hidden


class FilesError(Exception):
    """A request that can't be done; `status` is the HTTP status for the page."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def project_dir(name: str) -> str:
    """The real folder of a project in the Workspace."""
    settings = config_manager.get_settings()
    if not name:
        raise FilesError("No project is open. Pick one in the top bar.", 409)
    try:
        return str(resolve_project_dir(settings.get("projects_root", ""), name))
    except ScopeError as e:
        raise FilesError(str(e), 404)


def active_project() -> tuple[str, str]:
    """(name, real folder) of the active project."""
    name = config_manager.get_settings().get("active_project") or ""
    return name, project_dir(name)


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def _clean_rel(rel: str) -> str:
    if not isinstance(rel, str) or "\0" in rel:
        raise FilesError("Invalid path.")
    rel = rel.strip()
    if os.path.isabs(rel) or rel.startswith("\\"):
        raise FilesError("Use a path relative to the project, not an absolute one.")
    return rel.rstrip("/")


def _forbidden(real: str, root: str) -> str | None:
    if not _inside(real, root):
        return "is outside the project folder"
    if ".git" in os.path.relpath(real, root).split(os.sep):
        return "is inside .git"
    if _inside(real, os.path.realpath(paths.HOME)):
        return "is in Optimux's own data folder"
    return None


def resolve_dir(root: str, rel: str) -> tuple[str, str]:
    """(real path, clean relative path) of a folder in the project; "" is the project itself."""
    rel = _clean_rel(rel)
    real = os.path.realpath(os.path.join(root, rel)) if rel else root
    why = _forbidden(real, root)
    if why:
        raise FilesError(f"{rel} {why}.", 403)
    if not os.path.isdir(real):
        raise FilesError(f"{rel or 'The project'} isn't a folder (or no longer exists).", 404)
    return real, "" if real == root else os.path.relpath(real, root)


# ---------------------------------------------------------------- git

def _git(args: list[str], cwd: str, data: bytes | None = None) -> subprocess.CompletedProcess | None:
    """A read-only git call. No GIT_* variables from outside (nothing redirects it
    to another repository), no optional locks (it never blocks a model's git)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", *args], cwd=cwd, env=env, input=data, capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None


def is_repo(root: str) -> bool:
    """True when the project folder is the top of its own git repository."""
    r = _git(["rev-parse", "--show-toplevel"], root)
    if not r or r.returncode != 0:
        return False
    return os.path.realpath(r.stdout.decode(errors="replace").strip()) == root


def _code(xy: str) -> str:
    """One letter for the tree from git's two-letter status."""
    if xy == "??":
        return "U"                     # untracked: new, not added yet
    if "U" in xy or xy in ("AA", "DD"):
        return "C"                     # merge conflict
    for letter in ("D", "R", "A", "M"):
        if letter in xy:
            return letter
    return "M"


def git_status(root: str) -> dict:
    """{git, branch, files: {path: code}, truncated} for the whole project."""
    if not is_repo(root):
        return {"git": False, "branch": "", "files": {}, "truncated": False}
    r = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"], root)
    if not r or r.returncode != 0:
        return {"git": True, "branch": "", "files": {}, "truncated": False,
                "error": "git status didn't answer in time." if r is None else r.stderr.decode(errors="replace").strip()}
    out: dict[str, str] = {}
    truncated = False
    for item in r.stdout.decode("utf-8", errors="replace").split("\0"):
        if len(item) < 4:
            continue
        if len(out) >= MAX_STATUS:
            truncated = True
            break
        out[item[3:].rstrip("/")] = _code(item[:2])
    b = _git(["branch", "--show-current"], root)
    branch = b.stdout.decode(errors="replace").strip() if b and b.returncode == 0 else ""
    return {"git": True, "branch": branch, "files": out, "truncated": truncated}


def _ignored(root: str, rels: list[str]) -> set[str]:
    if not rels:
        return set()
    r = _git(["check-ignore", "-z", "--stdin"], root, "\0".join(rels).encode() + b"\0")
    if not r or r.returncode not in (0, 1):
        return set()
    return {p for p in r.stdout.decode("utf-8", errors="replace").split("\0") if p}


# ---------------------------------------------------------------- listing, reading, saving

def list_dir(root: str, rel: str = "") -> dict:
    """One folder of the project: folders first, then files, by name.

    Entry: {name, path, type: dir|file|outside, size (files), link (a symlink),
    ignored}. A link that leads out of the project is listed as "outside" and
    can't be opened."""
    real, rel = resolve_dir(root, rel)
    try:
        names = os.listdir(real)
    except OSError as e:
        raise FilesError(f"Can't read {rel or 'the project folder'}: {e.strerror}.", 403)
    entries = []
    for name in names:
        if name in HIDDEN:
            continue
        path = os.path.join(rel, name) if rel else name
        full = os.path.join(real, name)
        link = os.path.islink(full)
        target = os.path.realpath(full)
        if _forbidden(target, root):
            kind, size = "outside", None
        elif os.path.isdir(target):
            kind, size = "dir", None
        elif os.path.isfile(target):
            kind = "file"
            try:
                size = os.path.getsize(target)
            except OSError:
                size = None
        else:
            continue                   # sockets, broken links and the like
        entries.append({"name": name, "path": path, "type": kind, "size": size, "link": link})
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower(), e["name"]))
    total = len(entries)
    entries = entries[:MAX_ENTRIES]
    ignored = _ignored(root, [e["path"] for e in entries]) if entries and is_repo(root) else set()
    for e in entries:
        e["ignored"] = e["path"] in ignored
    return {"path": rel, "entries": entries, "total": total, "truncated": total > MAX_ENTRIES}


def read_file(root: str, rel: str) -> dict:
    """A text file for the editor: {path, size, text, digest, hidden}. Saved keys
    are shown as ••••(NAME) (`hidden` counts them); `digest` is of the file on disk.
    A binary or too-large file gives {path, size, binary|too_large: true} and no text."""
    rel = _clean_rel(rel)
    try:
        real, rel = filetools.resolve_target(root, rel)
    except filetools.FileToolError as e:
        raise FilesError(str(e).replace(" can be written", " can be opened"), 403)
    if not os.path.isfile(real):
        raise FilesError(f"{rel} doesn't exist (any more).", 404)
    size = os.path.getsize(real)
    if size > filetools.MAX_BYTES:
        return {"path": rel, "size": size, "too_large": True}
    with open(real, "rb") as f:
        data = f.read()
    if b"\0" in data:
        return {"path": rel, "size": size, "binary": True}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "size": size, "binary": True}
    shown, hidden = mask_secrets(text)
    return {"path": rel, "size": size, "text": shown, "digest": filetools._digest(text), "hidden": hidden}


def save_file(root: str, rel: str, text: str, digest: str | None) -> dict:
    """Save the editor's text. digest None creates a new file (parent folders too).
    Conflicts (changed, created or deleted meanwhile) are 409s."""
    rel = _clean_rel(rel)
    if not rel:
        raise FilesError("The file name is empty.")
    change = None
    try:
        change = filetools.prepare_user_save(root, rel, text, digest)
        if change is None:
            real, rel = filetools.resolve_target(root, rel)
            return {"path": rel, "digest": digest, "changed": False}
        filetools.apply(change)
    except filetools.SaveConflict as e:
        raise FilesError(str(e), 409)
    except filetools.FileToolError as e:
        msg = str(e)
        if change is not None and "changed while waiting" in msg:
            raise FilesError(f"{change['path']} changed on disk while saving; nothing was written.", 409)
        raise FilesError(msg.replace(" can be written", " can be saved"), 400)
    return {"path": change["path"], "digest": filetools._digest(change["new_text"]), "changed": True,
            "op": change["op"], "added": change["added"], "removed": change["removed"]}


# ---------------------------------------------------------------- expand all, new folder, rename, delete (v0.8)
# Ported from OmniLatch (p74, p81). The user's own clicks: no approval, but the same
# boundary as everything else here (inside the project, never .git or Optimux's data).

EXPAND_MAX = 400          # folders "Expand all" opens at most


def expand_all(root: str) -> dict:
    """Every folder's listing for "Expand all", level by level. Not opened: folders
    git ignores (node_modules, builds), links, and hidden folders (.github, .venv).
    {folders: {path: listing}, expanded: [paths], truncated}."""
    listings: dict[str, dict] = {}
    expanded: list[str] = []
    level, truncated = [""], False
    while level:
        nxt = []
        for rel in level:
            try:
                listing = list_dir(root, rel)
            except FilesError:
                continue
            listings[listing["path"]] = listing
            for e in listing["entries"]:
                if e["type"] != "dir" or e["link"] or e["ignored"] or e["name"].startswith("."):
                    continue
                if len(expanded) >= EXPAND_MAX:
                    truncated = True
                    continue
                expanded.append(e["path"])
                nxt.append(e["path"])
        level = nxt
    return {"folders": listings, "expanded": expanded, "truncated": truncated}


def _target(root: str, rel: str) -> tuple[str, str, str]:
    """(path, relative path, kind: file|folder|link) of an existing entry to rename or
    delete. Its folder must be inside the project; the entry itself isn't followed."""
    rel = _clean_rel(rel)
    if not rel:
        raise FilesError("The project folder itself can't be changed here.", 400)
    parent, name = os.path.split(rel)
    if name in ("", ".", ".."):
        raise FilesError("Invalid path.", 400)
    parent_real, _ = resolve_dir(root, parent)
    full = os.path.join(parent_real, name)
    if not os.path.lexists(full):
        raise FilesError(f"{rel} doesn't exist (any more).", 404)
    rel = os.path.relpath(full, root)
    if name in HIDDEN or ".git" in rel.split(os.sep):
        raise FilesError(f"{rel} is git's own data; it can't be changed here.", 403)
    if os.path.islink(full):
        return full, rel, "link"
    home = os.path.realpath(paths.HOME)
    if _inside(full, home) or _inside(home, full):
        raise FilesError(f"{rel} holds Optimux's own data folder; it can't be changed here.", 403)
    return full, rel, "folder" if os.path.isdir(full) else "file"


def _new_target(root: str, rel: str) -> tuple[str, str]:
    """(path, relative path) of a free place for a new entry, inside the project."""
    rel = _clean_rel(rel)
    parent, name = os.path.split(rel)
    if not rel or name in ("", ".", ".."):
        raise FilesError("Give the new name.", 400)
    if name in HIDDEN or ".git" in rel.split("/"):
        raise FilesError("Nothing goes into .git from here.", 403)
    parent_real = os.path.realpath(os.path.join(root, parent)) if parent else root
    why = _forbidden(parent_real, root)
    if why:
        raise FilesError(f"{parent or 'That folder'} {why}.", 403)
    if os.path.exists(parent_real) and not os.path.isdir(parent_real):
        raise FilesError(f"{parent} is a file, not a folder.", 409)
    full = os.path.join(parent_real, name)
    if os.path.lexists(full):
        raise FilesError(f"{os.path.relpath(full, root)} already exists.", 409)
    return full, os.path.relpath(full, root)


def make_folder(root: str, rel: str) -> dict:
    full, rel = _new_target(root, rel)
    os.makedirs(full)
    return {"path": rel}


def rename_path(root: str, rel: str, to: str) -> dict:
    """Rename or move a file, folder or link inside the project (missing folders on
    the way are made). Files that mention the old path aren't changed."""
    full, rel, kind = _target(root, rel)
    new_full, new_rel = _new_target(root, to)
    if kind == "folder" and _inside(new_full, full):
        raise FilesError("A folder can't go inside itself.", 400)
    os.makedirs(os.path.dirname(new_full), exist_ok=True)
    if os.path.realpath(os.path.dirname(new_full)) != os.path.dirname(new_full):
        raise FilesError(f"{os.path.dirname(new_rel)} changed while renaming.", 409)
    os.rename(full, new_full)
    return {"path": rel, "to": new_rel, "kind": kind}


COUNT_LIMIT = 20_000      # entries counted for the delete question


def _workspace() -> str:
    return os.path.realpath(config_manager.get_settings().get("projects_root") or "") or ""


def delete_info(root: str, rel: str) -> dict:
    """What Delete would move aside, for the question: {path, kind, files, folders, size,
    truncated, tracked, trash: home|workspace|null}. Changes nothing."""
    full, rel, kind = _target(root, rel)
    files = folders = size = 0
    truncated = False
    if kind == "folder":
        for d, ds, fs in os.walk(full):          # links are not followed
            folders += len(ds)
            for f in fs:
                files += 1
                try:
                    size += os.lstat(os.path.join(d, f)).st_size
                except OSError:
                    pass
            if files + folders > COUNT_LIMIT:
                truncated = True
                break
    else:
        files, size = 1, os.lstat(full).st_size
    tracked = 0
    if is_repo(root):
        r = _git(["ls-files", "-z", "--", rel], root)
        tracked = len([p for p in r.stdout.split(b"\0") if p]) if r and r.returncode == 0 else 0
    place = trash.where(full, _workspace())
    return {"path": rel, "kind": kind, "files": files, "folders": folders, "size": size, "truncated": truncated,
            "tracked": tracked, "trash": None if place is None else ("home" if place == trash.home_trash() else "workspace")}


def delete_path(root: str, rel: str) -> dict:
    """Move a file, folder or link aside: to the desktop Trash, else the Workspace's
    .trash/. Never deleted for good: when neither is possible, nothing changes."""
    full, rel, kind = _target(root, rel)
    place = trash.where(full, _workspace())
    if place is None:
        raise FilesError(f"{rel} is on another disk than the Trash and the Workspace's .trash folder, so it can't be "
                         "moved aside. Nothing was deleted; delete it yourself if you're sure.", 409)
    try:
        name = trash.move_to_trash(full, place)
    except OSError as e:
        raise FilesError(f"Couldn't move {rel} aside: {e.strerror or e}. Nothing was deleted.", 500)
    return {"path": rel, "kind": kind, "trash": "home" if place == trash.home_trash() else "workspace",
            "trash_path": os.path.join(place, "files", name)}


# ---------------------------------------------------------------- the preview
# Served at /preview/<project>/<token>/<path> with a CSP `sandbox` header, so the page
# runs with an opaque origin: its scripts can't read Optimux's pages, cookies or storage,
# and its requests to Optimux carry no sign-in. The token (an HMAC of the project name
# with a secret made at each start) is only given to a signed-in page.

_PREVIEW_SECRET = secrets.token_bytes(32)


def preview_token(project: str) -> str:
    return hmac.new(_PREVIEW_SECRET, project.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def preview_ok(project: str, token: str) -> bool:
    return bool(project) and isinstance(token, str) and hmac.compare_digest(preview_token(project), token)


def preview_base(project: str) -> str:
    from urllib.parse import quote
    return f"/preview/{quote(project, safe='')}/{preview_token(project)}/"


TEXT_TYPES = ("text/", "application/javascript", "application/json", "application/xml", "image/svg+xml",
              "application/manifest+json")


def preview_target(root: str, rel: str) -> tuple[str, str]:
    """(real file, content type) to serve for a preview path. A folder serves its
    index.html. Never .git, Optimux's data, links out of the project, or files that
    hold secrets."""
    rel = _clean_rel(rel)
    real = os.path.realpath(os.path.join(root, rel)) if rel else root
    why = _forbidden(real, root)
    if why:
        raise FilesError(f"{rel} {why}.", 403)
    if os.path.isdir(real):
        real = os.path.join(real, "index.html")
        if not os.path.isfile(real):
            raise FilesError(f"{rel or 'The project'} has no index.html.", 404)
    if not os.path.isfile(real):
        raise FilesError(f"{rel} doesn't exist.", 404)
    shown = os.path.relpath(real, root).replace(os.sep, "/")
    if SECRET_NAME.search(shown) or SECRET_NAME.search(os.path.basename(shown)):
        raise FilesError(f"{shown} may hold secrets; the preview never serves it.", 403)
    ctype = mimetypes.guess_type(real)[0] or "application/octet-stream"
    return real, ctype


def preview_text(real: str, ctype: str) -> str | None:
    """A text file's content with saved keys hidden, or None to stream the file as it is
    (not text, or too large to check)."""
    if not ctype.startswith(TEXT_TYPES) or os.path.getsize(real) > PREVIEW_MASK_BYTES:
        return None
    with open(real, "rb") as f:
        data = f.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return mask_secrets(text)[0]


def find_index(root: str) -> str | None:
    """The page the preview opens first: index.html at the top, or in a built output
    folder (dist, build, public, out, site, docs), or None."""
    for rel in ("index.html", "dist/index.html", "build/index.html", "public/index.html", "out/index.html",
                "site/index.html", "docs/index.html", "www/index.html"):
        if os.path.isfile(os.path.join(root, rel)):
            return rel
    return None
