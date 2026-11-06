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
import posixpath
import re
import secrets
import subprocess

from optimux_backend.core import config_manager, files as filetools, paths, trash
from optimux_backend.core.changes import SECRET_NAME
from optimux_backend.core.security import ScopeError, mask_secrets, resolve_project_dir
from optimux_backend.core.i18n import tr

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
        raise FilesError(tr("No project is open. Pick one in the top bar."), 409)
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
        raise FilesError(tr("Invalid path."))
    rel = rel.strip()
    if os.path.isabs(rel) or rel.startswith("\\"):
        raise FilesError(tr("Use a path relative to the project, not an absolute one."))
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
        raise FilesError(tr("{v} isn't a folder (or no longer exists).", v=rel or 'The project'), 404)
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
        raise FilesError(tr("Can't read {v}: {strerror}.", v=rel or 'the project folder', strerror=e.strerror), 403)
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
        raise FilesError(tr("{rel} doesn't exist (any more).", rel=rel), 404)
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
        raise FilesError(tr("The file name is empty."))
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
            raise FilesError(tr("{v} changed on disk while saving; nothing was written.", v=change['path']), 409)
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
        raise FilesError(tr("The project folder itself can't be changed here."), 400)
    parent, name = os.path.split(rel)
    if name in ("", ".", ".."):
        raise FilesError(tr("Invalid path."), 400)
    parent_real, _ = resolve_dir(root, parent)
    full = os.path.join(parent_real, name)
    if not os.path.lexists(full):
        raise FilesError(tr("{rel} doesn't exist (any more).", rel=rel), 404)
    rel = os.path.relpath(full, root)
    if name in HIDDEN or ".git" in rel.split(os.sep):
        raise FilesError(tr("{rel} is git's own data; it can't be changed here.", rel=rel), 403)
    if os.path.islink(full):
        return full, rel, "link"
    home = os.path.realpath(paths.HOME)
    if _inside(full, home) or _inside(home, full):
        raise FilesError(tr("{rel} holds Optimux's own data folder; it can't be changed here.", rel=rel), 403)
    return full, rel, "folder" if os.path.isdir(full) else "file"


def _new_target(root: str, rel: str) -> tuple[str, str]:
    """(path, relative path) of a free place for a new entry, inside the project."""
    rel = _clean_rel(rel)
    parent, name = os.path.split(rel)
    if not rel or name in ("", ".", ".."):
        raise FilesError(tr("Give the new name."), 400)
    if name in HIDDEN or ".git" in rel.split("/"):
        raise FilesError(tr("Nothing goes into .git from here."), 403)
    parent_real = os.path.realpath(os.path.join(root, parent)) if parent else root
    why = _forbidden(parent_real, root)
    if why:
        raise FilesError(f"{parent or 'That folder'} {why}.", 403)
    if os.path.exists(parent_real) and not os.path.isdir(parent_real):
        raise FilesError(tr("{parent} is a file, not a folder.", parent=parent), 409)
    full = os.path.join(parent_real, name)
    if os.path.lexists(full):
        raise FilesError(tr("{v} already exists.", v=os.path.relpath(full, root)), 409)
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
        raise FilesError(tr("A folder can't go inside itself."), 400)
    os.makedirs(os.path.dirname(new_full), exist_ok=True)
    if os.path.realpath(os.path.dirname(new_full)) != os.path.dirname(new_full):
        raise FilesError(tr("{v} changed while renaming.", v=os.path.dirname(new_rel)), 409)
    os.rename(full, new_full)
    return {"path": rel, "to": new_rel, "kind": kind}


# ---------------------------------------------------------------- search by name (v0.8, OmniLatch p81)

SEARCH_MAX_FILES = 50_000      # files looked at per search
SEARCH_RESULTS = 200           # results returned


def _project_paths(root: str) -> tuple[list[str], bool]:
    """Every file of the project that git doesn't ignore (tracked and new), or for a
    project without git every file outside hidden folders. (paths, truncated)."""
    files: list[str] = []
    if is_repo(root):
        r = _git(["-c", "core.quotePath=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], root)
        if r and r.returncode == 0:
            seen = set()
            for p in r.stdout.decode("utf-8", errors="replace").split("\0"):
                if p and p not in seen and os.path.lexists(os.path.join(root, p)):
                    seen.add(p)
                    files.append(p)
                    if len(files) >= SEARCH_MAX_FILES:
                        return files, True
            return files, False
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in HIDDEN and not d.startswith("."))
        rel = os.path.relpath(here, root)
        for n in sorted(names):
            files.append(n if rel == "." else os.path.join(rel, n).replace(os.sep, "/"))
            if len(files) >= SEARCH_MAX_FILES:
                return files, True
    return files, False


def search(root: str, query: str) -> dict:
    """Files and folders whose path contains every word of `query` (any case). Not
    searched: what git ignores (node_modules, builds), .git, and hidden names (a part
    starting with a dot). Best first: the name is the query, the name starts with its
    first word, the name has every word, then the path does; folders before files;
    shorter paths first. {query, results: [{path, name, type}], total, truncated}."""
    terms = [t for t in str(query or "").lower().split() if t][:10]
    if not terms:
        return {"query": query, "results": [], "total": 0, "truncated": False}
    files, truncated = _project_paths(root)
    folders: set[str] = set()
    for f in files:
        parts = f.split("/")
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))
    whole = " ".join(terms)
    found = []
    for path, kind in [(d, "dir") for d in folders] + [(f, "file") for f in files]:
        if any(part.startswith(".") for part in path.split("/")):
            continue
        low = path.lower()
        if not all(t in low for t in terms):
            continue
        name = low.rsplit("/", 1)[-1]
        rank = 0 if name == whole else 1 if name.startswith(terms[0]) else 2 if all(t in name for t in terms) else 3
        found.append((rank, kind != "dir", len(path), low, path, kind))
    found.sort()
    results = [{"path": p, "name": p.rsplit("/", 1)[-1], "type": k} for *_, p, k in found[:SEARCH_RESULTS]]
    return {"query": query, "results": results, "total": len(found), "truncated": truncated}


# ---------------------------------------------------------------- "Open in file" (v0.8)
# The preview's right-click "Open in file": find the source of an element the user
# pointed at. First the page's own HTML (its start tags, scored by id, classes, other
# attributes, text and position), then, for content a script made, the project's text
# files (the element's text, its id or a class).

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
LOCATE_EXTS = (".html", ".htm", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".php", ".erb",
               ".hbs", ".njk", ".liquid", ".jinja", ".j2", ".twig", ".md", ".py", ".rb")
LOCATE_FILES = 3000            # files looked at for text
LOCATE_BYTES = 1_000_000       # larger files are skipped
_ATTR = re.compile(r"""([^\s=/>"']+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]+))?""")


def _attrs(tag_text: str) -> dict:
    inner = re.sub(r"^<\s*[^\s/>]+", "", tag_text).rstrip(">").rstrip("/")
    out = {}
    for m in _ATTR.finditer(inner):
        v = m.group(2) or ""
        if v[:1] in "\"'":
            v = v[1:-1]
        out[m.group(1).lower()] = v
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text or "")).strip().lower()


def _line_col(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    return line, offset - (text.rfind("\n", 0, offset) + 1)


def _element_end(text: str, tag: str, start: int, open_end: int) -> int:
    """Offset after the element's closing tag (or after its start tag for a void or
    self-closing one, or when no closing tag is found)."""
    if tag in VOID_TAGS or text[open_end - 2:open_end] == "/>":
        return open_end
    pat = re.compile(r"<(/?)" + re.escape(tag) + r"(?=[\s/>])[^>]*>", re.I)
    depth = 1
    for m in pat.finditer(text, open_end):
        if m.group(1):
            depth -= 1
            if depth == 0:
                return m.end()
        elif not m.group(0).endswith("/>"):
            depth += 1
    return open_end


def _span(text: str, start: int, end: int, path: str, how: str) -> dict:
    fl, fc = _line_col(text, start)
    tl, tc = _line_col(text, end)
    return {"found": True, "path": path, "from": {"line": fl, "col": fc}, "to": {"line": tl, "col": tc}, "how": how}


def _in_page(text: str, el: dict) -> tuple[int, int, int] | None:
    """(score, start, end) of the best start tag in an HTML text."""
    tag = el["tag"]
    want_classes = set(el.get("classes") or [])
    want_text = _norm(el.get("text") or "")[:80]
    best = None
    for i, m in enumerate(re.finditer(r"<" + re.escape(tag) + r"(?=[\s/>])[^>]*>", text, re.I)):
        a = _attrs(m.group(0))
        score = 0
        if el.get("id") and a.get("id") == el["id"]:
            score += 100
        elif a.get("id") and el.get("id") != a.get("id"):
            score -= 30
        have = set((a.get("class") or "").split())
        score += 10 * len(want_classes & have) - 4 * len(have - want_classes)
        for k, v in (el.get("attrs") or {}).items():
            if k in ("id", "class", "style"):
                continue
            if a.get(k) == v:
                score += 8
        end = _element_end(text, tag, m.start(), m.end())
        if want_text and want_text in _norm(text[m.end():end if end > m.end() else m.end() + 4000]):
            score += 25
        if el.get("index") is not None:
            score += max(0, 12 - 4 * abs(int(el["index"]) - i))
        if best is None or score > best[0]:
            best = (score, m.start(), end)
    return best


def locate(root: str, page: str, el: dict) -> dict:
    """Where an element of the previewed `page` comes from: {found, path, from: {line, col},
    to: {line, col}, how: page|text|name} (lines from 1, columns from 0), or {found: false}."""
    tag = str(el.get("tag") or "").lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}", tag):
        raise FilesError(tr("Invalid element."))
    el = {**el, "tag": tag}
    page = _clean_rel(page or "")
    text = None
    if page and re.search(r"\.html?$", page, re.I):
        try:
            got = read_file(root, page)
            real, _ = filetools.resolve_target(root, page)
            with open(real, encoding="utf-8") as f:
                text = f.read() if got.get("text") is not None else None
        except (FilesError, filetools.FileToolError, OSError, UnicodeDecodeError):
            text = None
    if text is not None:
        best = _in_page(text, el)
        if best and best[0] >= 15:
            return _span(text, best[1], best[2], page, "page")
    # Content made by a script, or a page we couldn't match: the text, the id or a class in the project's files
    needles = []
    words = re.sub(r"\s+", " ", str(el.get("text") or "")).strip()
    if 4 <= len(words):
        needles.append(("text", words[:60]))
    if el.get("id"):
        needles.append(("name", str(el["id"])))
    needles += [("name", c) for c in (el.get("classes") or [])[:3] if len(c) >= 4]
    if not needles:
        return {"found": False}
    files, _ = _project_paths(root)
    files = [f for f in files if f.lower().endswith(LOCATE_EXTS) and not any(p.startswith(".") for p in f.split("/"))]
    files.sort(key=lambda f: (f != page, not f.lower().endswith((".html", ".htm")), len(f)))
    for how, needle in needles:
        for f in files[:LOCATE_FILES]:
            try:
                real, rel = filetools.resolve_target(root, f)
                if os.path.getsize(real) > LOCATE_BYTES:
                    continue
                with open(real, encoding="utf-8") as fh:
                    body = fh.read()
            except (filetools.FileToolError, OSError, UnicodeDecodeError):
                continue
            pos = body.find(needle)
            if pos < 0 and how == "text":
                # the same words across line breaks and indentation
                m = re.search(r"\s+".join(re.escape(w) for w in needle.split(" ")), body)
                pos = m.start() if m else -1
            if pos >= 0:
                return _span(body, pos, pos + len(needle), rel, how)
    return {"found": False}


# ---------------------------------------------------------------- "Open the CSS", "Open the script" (v0.8)
# The page's styles: the rules of its local stylesheets and <style> blocks, with where each
# one is. Which rules apply to an element is decided in the preview (element.matches), so
# only selectors go there. The page's scripts: lines that mention the element (its id, a
# class, a data- attribute). Code that only finds elements some other way isn't found.

STYLE_RULES = 5000             # rules returned per page
SCRIPT_MATCHES = 40            # lines returned
_NESTING = ("@media", "@supports", "@layer", "@container", "@document", "@scope")


def _skip(text: str, i: int) -> int:
    """After a comment or a string starting at i (or i itself)."""
    if text.startswith("/*", i):
        j = text.find("*/", i + 2)
        return len(text) if j < 0 else j + 2
    if text[i] in "\"'":
        q, j = text[i], i + 1
        while j < len(text) and text[j] != q:
            j += 2 if text[j] == "\\" else 1
        return j + 1
    return i


def _css_rules(text: str, i: int = 0, end: int | None = None, media: str = "") -> list[tuple[str, int, int, str]]:
    """(selectors, start, end, media) of every style rule, inside @media and the like too."""
    end = len(text) if end is None else end
    rules: list[tuple[str, int, int, str]] = []
    while i < end:
        while i < end and (text[i].isspace() or text.startswith("/*", i)):
            i = _skip(text, i) if text.startswith("/*", i) else i + 1
        start = j = i
        while j < end and text[j] not in "{;}":
            k = _skip(text, j)
            j = k if k != j else j + 1
        if j >= end:
            break
        if text[j] != "{":                          # @import ...; or a stray }
            i = j + 1
            continue
        prelude = re.sub(r"/\*.*?\*/", "", text[start:j], flags=re.S).strip()
        depth, k = 1, j + 1                         # the block's end
        while k < end and depth:
            n = _skip(text, k)
            if n != k:
                k = n
                continue
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            k += 1
        if prelude.lower().startswith(_NESTING):
            rules += _css_rules(text, j + 1, k - 1, (media + " " if media else "") + prelude)
        elif prelude and not prelude.startswith("@"):
            rules.append((prelude, start, k, media))
        i = k
    return rules


def _local_ref(page: str, ref: str) -> str | None:
    """A link or script src of `page` as a path in the project, or None (another site, data:...)."""
    ref = (ref or "").split("#")[0].split("?")[0].strip()
    if not ref or re.match(r"^[a-z][a-z0-9+.-]*:|^//", ref, re.I):
        return None
    path = ref.lstrip("/") if ref.startswith("/") else posixpath.join(posixpath.dirname(page), ref)
    path = posixpath.normpath(path)
    return None if path.startswith("..") or path == "." else path


def _read_text(root: str, rel: str) -> str | None:
    try:
        real, _ = filetools.resolve_target(root, rel)
        if not os.path.isfile(real) or os.path.getsize(real) > LOCATE_BYTES:
            return None
        with open(real, encoding="utf-8") as f:
            return f.read()
    except (filetools.FileToolError, OSError, UnicodeDecodeError, ValueError):
        return None


def _page_parts(root: str, page: str) -> tuple[str | None, list[tuple[str, dict, int, int]]]:
    """The page's text and its <link>, <style> and <script> tags: (tag, attrs, content start, content end)."""
    page = _clean_rel(page or "")
    text = _read_text(root, page) if re.search(r"\.html?$", page, re.I) else None
    if text is None:
        return None, []
    parts = []
    for m in re.finditer(r"<!--.*?-->|<(link|style|script)(?=[\s/>])[^>]*>", text, re.I | re.S):
        if not m.group(1):
            continue
        tag = m.group(1).lower()
        if tag == "link":
            parts.append((tag, _attrs(m.group(0)), m.end(), m.end()))
            continue
        close = re.compile(r"</" + tag + r"\s*>", re.I).search(text, m.end())
        parts.append((tag, _attrs(m.group(0)), m.end(), close.start() if close else len(text)))
    return text, parts


def style_rules(root: str, page: str) -> dict:
    """{rules: [{i, path, selectors, media, from, to}], sheets: [paths], truncated} for the
    page's local stylesheets and <style> blocks, in the page's order."""
    page = _clean_rel(page or "")
    text, parts = _page_parts(root, page)
    if text is None:
        raise FilesError(tr("Open the CSS works on HTML pages of the project."), 400)
    rules, sheets = [], []
    for tag, a, start, end in parts:
        if tag == "link" and "stylesheet" in (a.get("rel") or "").lower().split():
            path = _local_ref(page, a.get("href", ""))
            css = _read_text(root, path) if path else None
            if css is None:
                continue
            sheets.append(path)
            found = [(path, css, r) for r in _css_rules(css)]
        elif tag == "style":
            found = [(page, text, r) for r in _css_rules(text, start, end)]
        else:
            continue
        for path, body, (sel, a_, b_, media) in found:
            fl, fc = _line_col(body, a_)
            tl, tc = _line_col(body, b_)
            rules.append({"i": len(rules), "path": path, "selectors": re.sub(r"\s+", " ", sel)[:500], "media": media[:200],
                          "from": {"line": fl, "col": fc}, "to": {"line": tl, "col": tc}})
            if len(rules) >= STYLE_RULES:
                return {"rules": rules, "sheets": sheets, "truncated": True}
    return {"rules": rules, "sheets": sheets, "truncated": False}


SCRIPT_EXTS = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte")


def script_refs(root: str, page: str, el: dict) -> dict:
    """Lines of the page's scripts (its <script> blocks and local script files; when none
    mention the element, the project's other script files) that name the element's id, a
    class or a data- attribute. {matches: [{path, from, to, text, why}], where: page|project}."""
    page = _clean_rel(page or "")
    names = []
    if el.get("id"):
        names.append((str(el["id"])[:100], 3, "#" + str(el["id"])[:100]))
    for c in (el.get("classes") or [])[:8]:
        if len(str(c)) >= 3:
            names.append((str(c)[:100], 2, "." + str(c)[:100]))
    for k in (el.get("attrs") or {}):
        if str(k).startswith("data-") and len(k) > 5:
            names.append((str(k)[:100], 2, "[" + str(k)[:100] + "]"))
    if not names:
        return {"matches": [], "where": "page"}
    pats = [(re.compile(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])"), w, why) for n, w, why in names]

    def scan(path: str, body: str, a: int = 0, b: int | None = None) -> list:
        out = []
        b = len(body) if b is None else b
        line_start = body.rfind("\n", 0, a) + 1
        for line in body[line_start:b].split("\n"):
            hits = [(w + (1 if ("#" + n if w == 3 else "." + n) in line or "getElementById" in line else 0), why)
                    for (p_, w, why), (n, _, _) in zip(pats, names) if p_.search(line)]
            if hits:
                weight = max(h[0] for h in hits)
                fl, fc = _line_col(body, line_start)
                out.append((-weight, path, fl, {"path": path, "from": {"line": fl, "col": 0}, "to": {"line": fl, "col": len(line)},
                                               "text": line.strip()[:160], "why": ", ".join(sorted({h[1] for h in hits}))}))
            line_start += len(line) + 1
        return out

    found = []
    text, parts = _page_parts(root, page)
    local = []
    for tag, a, start, end in parts if text is not None else []:
        if tag != "script":
            continue
        if a.get("src"):
            path = _local_ref(page, a["src"])
            body = _read_text(root, path) if path else None
            if body is not None:
                local.append(path)
                found += scan(path, body)
        elif (a.get("type") or "text/javascript").lower() in ("text/javascript", "module", "application/javascript", ""):
            found += scan(page, text, start, end)
    where = "page"
    if not found:
        where = "project"
        files, _ = _project_paths(root)
        for f in [f for f in files if f.lower().endswith(SCRIPT_EXTS) and f not in local
                  and not any(x.startswith(".") for x in f.split("/"))][:LOCATE_FILES]:
            body = _read_text(root, f)
            if body is not None:
                found += scan(f, body)
            if len(found) > SCRIPT_MATCHES * 4:
                break
    found.sort(key=lambda x: (x[0], x[1] != page, x[1], x[2]))
    return {"matches": [x[3] for x in found[:SCRIPT_MATCHES]], "where": where}


# ---------------------------------------------------------------- Ctrl+click in the editor (v0.8)
# Where a name used in a file is defined: a class or an id in CSS rules, a local file in an
# href or src, a function or variable in scripts. The file's own stylesheets and scripts
# first (for an HTML page: its <link>, <style> and <script>), then the project's.

STYLE_EXTS = (".css", ".scss", ".sass", ".less")
DEFINITIONS = 30


def _definitions_js(name: str) -> re.Pattern:
    n = re.escape(name)
    return re.compile(
        r"(?:\bfunction\s*\*?\s*(" + n + r")\s*\("                                # function name(
        r"|\b(?:const|let|var)\s+(" + n + r")\b\s*="                             # const name =
        r"|\bclass\s+(" + n + r")\b"                                             # class Name
        r"|^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(" + n + r")\s*\([^)\n]*\)\s*\{"   # name(...) {  (a method)
        r"|(?<![\w$.])(" + n + r")\s*:\s*(?:async\s+)?(?:function\b|\([^)\n]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"  # name: function / () =>
        r"|\bdef\s+(" + n + r")\s*\()",                                           # Python def
        re.M)


def _span_of(body: str, path: str, a: int, b: int) -> dict:
    fl, fc = _line_col(body, a)
    tl, tc = _line_col(body, b)
    line_start = body.rfind("\n", 0, a) + 1
    line_end = body.find("\n", a)
    return {"path": path, "from": {"line": fl, "col": fc}, "to": {"line": tl, "col": tc},
            "text": body[line_start:line_end if line_end >= 0 else len(body)].strip()[:160]}


def definition(root: str, path: str, kind: str, name: str) -> dict:
    """{results: [{path, from, to, text}], where: file|project} for a name in `path`."""
    path = _clean_rel(path or "")
    name = str(name or "").strip()
    if kind not in ("class", "id", "file", "symbol") or not name or len(name) > 300:
        raise FilesError(tr("Nothing to follow here."), 400)
    if kind == "file":
        target = _local_ref(path, name)
        if target is None:
            return {"results": [], "where": "file"}
        try:
            real, rel = filetools.resolve_target(root, target)
        except filetools.FileToolError:
            return {"results": [], "where": "file"}
        if os.path.isdir(real):
            index = os.path.join(rel, "index.html")
            if os.path.isfile(os.path.join(real, "index.html")):
                rel = index.replace(os.sep, "/")
            else:
                return {"results": [{"path": rel, "dir": True, "from": {"line": 1, "col": 0}, "to": {"line": 1, "col": 0}, "text": ""}], "where": "file"}
        elif not os.path.isfile(real):
            return {"results": [], "where": "file"}
        return {"results": [{"path": rel, "from": {"line": 1, "col": 0}, "to": {"line": 1, "col": 0}, "text": ""}], "where": "file"}

    html = bool(re.search(r"\.html?$", path, re.I))
    own: list[tuple[str, str, int, int]] = []           # (path, text, start, end) of the file's own code
    own_files: set[str] = set()
    if html:
        text, parts = _page_parts(root, path)
        for tag, a, start, end in parts if text is not None else []:
            if kind in ("class", "id") and tag == "link" and "stylesheet" in (a.get("rel") or "").lower().split():
                ref = _local_ref(path, a.get("href", ""))
                body = _read_text(root, ref) if ref else None
                if body is not None:
                    own.append((ref, body, 0, len(body))); own_files.add(ref)
            elif kind in ("class", "id") and tag == "style":
                own.append((path, text, start, end))
            elif kind == "symbol" and tag == "script":
                if a.get("src"):
                    ref = _local_ref(path, a["src"])
                    body = _read_text(root, ref) if ref else None
                    if body is not None:
                        own.append((ref, body, 0, len(body))); own_files.add(ref)
                else:
                    own.append((path, text, start, end))
    else:
        body = _read_text(root, path)
        if body is not None:
            own.append((path, body, 0, len(body))); own_files.add(path)

    def find(sources) -> list[dict]:
        out = []
        if kind in ("class", "id"):
            token = re.compile(("\\." if kind == "class" else "#") + re.escape(name) + r"(?![\w-])")
            for p_, body, a, b in sources:
                for sel, s_, e_, media in _css_rules(body, a, b):
                    if token.search(sel):
                        out.append(_span_of(body, p_, s_, e_) | {"text": re.sub(r"\s+", " ", sel)[:160] + (" · " + media if media else "")})
        else:
            pat = _definitions_js(name)
            for p_, body, a, b in sources:
                for m in pat.finditer(body, a, b):
                    g = next(i for i in range(1, 7) if m.group(i))
                    out.append(_span_of(body, p_, m.start(g), m.end(g)))
        return out[:DEFINITIONS]

    results = find(own)
    if results:
        return {"results": results, "where": "file"}
    exts = STYLE_EXTS if kind in ("class", "id") else SCRIPT_EXTS + (".py",)
    files, _ = _project_paths(root)
    sources = []
    for f in files:
        if f.lower().endswith(exts) and f not in own_files and not any(x.startswith(".") for x in f.split("/")):
            body = _read_text(root, f)
            if body is not None:
                sources.append((f, body, 0, len(body)))
        if len(sources) >= LOCATE_FILES:
            break
    return {"results": find(sources), "where": "project"}


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
        raise FilesError(tr("{rel} is on another disk than the Trash and the Workspace's .trash folder, so it can't be moved aside. Nothing was deleted; delete it yourself if you're sure.", rel=rel), 409)
    try:
        name = trash.move_to_trash(full, place)
    except OSError as e:
        raise FilesError(tr("Couldn't move {rel} aside: {v}. Nothing was deleted.", rel=rel, v=e.strerror or e), 500)
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
            raise FilesError(tr("{v} has no index.html.", v=rel or 'The project'), 404)
    if not os.path.isfile(real):
        raise FilesError(tr("{rel} doesn't exist.", rel=rel), 404)
    shown = os.path.relpath(real, root).replace(os.sep, "/")
    if SECRET_NAME.search(shown) or SECRET_NAME.search(os.path.basename(shown)):
        raise FilesError(tr("{shown} may hold secrets; the preview never serves it.", shown=shown), 403)
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
