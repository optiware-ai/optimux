"""write_file and edit_file: the models' tools for changing files (v0.4-5i).

Writing through shell here-docs emptied files, ate quotes and looped on
quoting during the plan-mode measurement. These tools write in Python
instead: only inside the active project, never into .git, text only, and
atomically (a temporary file renamed over the target), so a crash can't
leave a half-written or empty file.

They run in Optimux itself, not in the bubblewrap sandbox, so the path
check here is the safety boundary: the target is resolved with symlinks
and must stay inside the project folder.

Flow: prepare_write / prepare_edit check everything and compute the change
(nothing is written); the user approves it; apply writes it, after checking
that the file didn't change in the meantime.
"""
import difflib
import hashlib
import os
import re
import tempfile

MAX_BYTES = 1_000_000          # a file larger than this isn't written or edited
DIFF_LINES = 400               # diff lines shown in the approval and the chat
SHRINK_MIN_LINES = 20          # write_file may not cut a file this long to half its lines or less

# Control characters a text file normally has: tab, newline, carriage return, form feed.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
# A JSON escape for half of an emoji (\ud83d) written into the file as text.
_SURROGATE_ESCAPE = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}")

# The process umask, read once at import (single-threaded then), so a new file
# gets the same permissions a shell redirect would give it.
_UMASK = os.umask(0)
os.umask(_UMASK)


class FileToolError(ValueError):
    """Why a write or edit can't be done; the text goes back to the model."""


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def resolve_target(project: str, path: str) -> tuple[str, str]:
    """(absolute real path, path relative to the project) for `path`, which may be
    relative to the project or absolute inside it. Symlinks are followed, so a
    link that points out of the project is refused like a path that does."""
    if not isinstance(path, str) or not path.strip():
        raise FileToolError("path is empty.")
    if "\0" in path:
        raise FileToolError("path contains a NUL character.")
    root = os.path.realpath(project)
    raw = path.strip()
    joined = raw if os.path.isabs(raw) else os.path.join(root, raw)
    real = os.path.realpath(joined)
    if not _inside(real, root) or real == root:
        raise FileToolError(f"{raw} is outside the project folder; only files inside {root} can be written.")
    rel = os.path.relpath(real, root)
    if ".git" in rel.split(os.sep):
        raise FileToolError(f"{rel} is inside .git; git's own files are never written by these tools.")
    from optimux_backend.core import paths
    home = os.path.realpath(paths.HOME)
    if _inside(real, home):
        raise FileToolError(f"{rel} is in Optimux's own data folder.")
    if os.path.isdir(real):
        raise FileToolError(f"{rel} is a folder, not a file.")
    return real, rel


def _read_text(real: str, rel: str) -> str:
    size = os.path.getsize(real)
    if size > MAX_BYTES:
        raise FileToolError(f"{rel} is {size} bytes; files over {MAX_BYTES} bytes aren't edited by these tools.")
    with open(real, "rb") as f:
        data = f.read()
    if b"\0" in data:
        raise FileToolError(f"{rel} looks like a binary file; only text files can be changed.")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileToolError(f"{rel} isn't UTF-8 text; only UTF-8 text files can be changed.")


def _lines(text: str) -> int:
    return len(text.splitlines())


def _digest(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_text(rel: str, before: str | None, after: str) -> None:
    """Refuse new text that was mangled on the way: control characters or
    emoji escape codes that the file didn't have before. Small models
    sometimes escape special characters (—, ©, emoji) wrongly in the tool
    call, and the file would silently get garbage (t9: "©" became a control
    character, emoji became "\\ud83d\\udce1")."""
    try:
        after.encode("utf-8")
    except UnicodeEncodeError:
        raise FileToolError("the new text has a broken character (half of an emoji). Write special "
                            "characters (—, ©, emoji) as themselves, not as escape codes.")
    old = before or ""
    bad = sorted({c for c in _CONTROL.findall(after)} - set(_CONTROL.findall(old)))
    if bad:
        line = next(i for i, l in enumerate(after.split("\n"), 1) if any(c in l for c in bad))
        codes = ", ".join(f"U+{ord(c):04X}" for c in bad[:3])
        raise FileToolError(f"the new text for {rel} has control characters ({codes}, first on line {line}) "
                            "that the file didn't have. Special characters were probably escaped wrongly: "
                            "write them as themselves (—, ©, emoji), not as escape codes.")
    if len(_SURROGATE_ESCAPE.findall(after)) > len(_SURROGATE_ESCAPE.findall(old)):
        raise FileToolError(f"the new text for {rel} has emoji escape codes such as \\ud83d written as text. "
                            "Write emoji and other special characters as themselves.")


def _change(real: str, rel: str, op: str, before: str | None, after: str) -> dict:
    _check_text(rel, before, after)
    if len(after.encode("utf-8")) > MAX_BYTES:
        raise FileToolError(f"the new content is over {MAX_BYTES} bytes.")
    diff = list(difflib.unified_diff((before or "").splitlines(), after.splitlines(),
                                     fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=2))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    shown = diff[2:] if len(diff) >= 2 else diff         # the ---/+++ header repeats the path
    if len(shown) > DIFF_LINES:
        shown = shown[:DIFF_LINES] + [f"… {len(shown) - DIFF_LINES} more diff lines"]
    return {"abs": real, "path": rel, "op": op, "new_text": after, "before_digest": _digest(before),
            "lines_before": 0 if before is None else _lines(before), "lines_after": _lines(after),
            "added": added, "removed": removed, "diff": "\n".join(shown)}


def prepare_write(project: str, path: str, content) -> dict:
    """The change for write_file (create, or replace the whole file). Writes nothing."""
    if not isinstance(content, str):
        raise FileToolError("content must be a string.")
    real, rel = resolve_target(project, path)
    before = _read_text(real, rel) if os.path.exists(real) else None
    if before is not None and before == content:
        raise FileToolError(f"{rel} already has exactly this content; nothing to change.")
    if before is not None:
        n_before, n_after = _lines(before), _lines(content)
        if n_before >= SHRINK_MIN_LINES and n_after * 2 <= n_before:
            # t9: a small model rewrote a 783-line stylesheet from memory and kept 296 lines
            raise FileToolError(
                f"{rel} has {n_before} lines and this would leave {n_after}. write_file replaces the whole "
                "file, so the rest would be lost. Use edit_file to change part of the file, or to remove a "
                "part on purpose.")
    return _change(real, rel, "create" if before is None else "replace", before, content)


def prepare_edit(project: str, path: str, old_text, new_text) -> dict:
    """The change for edit_file: old_text must appear exactly once. Writes nothing."""
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        raise FileToolError("old_text and new_text must be strings.")
    if not old_text:
        raise FileToolError("old_text is empty; use write_file to create or replace a whole file.")
    if old_text == new_text:
        raise FileToolError("old_text and new_text are the same; nothing to change.")
    real, rel = resolve_target(project, path)
    if not os.path.exists(real):
        raise FileToolError(f"{rel} doesn't exist; use write_file to create it.")
    before = _read_text(real, rel)
    count = before.count(old_text)
    if count != 1:
        hint = ("Read the file again and copy the exact text, including spaces, quotes and special characters."
                if count == 0 else "Include more surrounding lines so the text is unique.")
        raise FileToolError(f"old_text was found {count} times in {rel}; it must appear exactly once. {hint}")
    return _change(real, rel, "edit", before, before.replace(old_text, new_text, 1))


def apply(change: dict) -> dict:
    """Write an approved change atomically. Refuses if the file changed (or
    appeared) since prepare, so an approval never overwrites newer work."""
    real, rel = change["abs"], change["path"]
    # A folder on the way swapped for a link after the approval would lead elsewhere
    folder = os.path.dirname(real)
    existing = folder
    while not os.path.exists(existing):
        existing = os.path.dirname(existing)
    if os.path.realpath(existing) != existing or (os.path.islink(real)):
        raise FileToolError(f"{rel}: a folder or the file was replaced by a link after the approval; nothing was written.")
    exists = os.path.exists(real)
    current = _read_text(real, rel) if exists else None
    if _digest(current) != change["before_digest"]:
        raise FileToolError(f"{rel} changed while waiting for approval; nothing was written. Read it again.")
    os.makedirs(folder, exist_ok=True)
    mode = (os.stat(real).st_mode & 0o7777) if exists else (0o666 & ~_UMASK)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".optimux-write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(change["new_text"])
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return {"ok": True, "path": rel, "op": change["op"], "lines_before": change["lines_before"],
            "lines_after": change["lines_after"], "added": change["added"], "removed": change["removed"]}


def describe(change: dict) -> str:
    """The one-line label shown in the approval box and the chat."""
    if change["op"] == "create":
        return f"✎ create {change['path']} ({change['lines_after']} lines)"
    return f"✎ {change['op']} {change['path']} (+{change['added']} −{change['removed']})"


def public(change: dict) -> dict:
    """What the page gets about a change (never the whole new text)."""
    return {k: change[k] for k in ("path", "op", "lines_before", "lines_after", "added", "removed")}


WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Create a file, or replace a whole file, in the active project. Use it for new files and for "
            "small files you rewrite completely. To change part of an existing file, use edit_file instead: "
            "write_file can't cut a file to half its lines or less. Write special characters (—, ©, emoji) "
            "as themselves. The user approves each change. Never write files with shell here-docs or echo."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the project folder."},
                "content": {"type": "string", "description": "The complete new content of the file."},
            },
            "required": ["path", "content"],
        },
    },
}

EDIT_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "Change part of an existing file in the active project: replace old_text with new_text. old_text "
            "must appear exactly once in the file, copied exactly (spaces, quotes, special characters); "
            "include a few surrounding lines to make it unique. To make several changes, call it once per "
            "change. Write special characters (—, ©, emoji) as themselves. The user approves each change."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to the project folder."},
                "old_text": {"type": "string", "description": "The exact text to replace (must appear once)."},
                "new_text": {"type": "string", "description": "The text to put in its place."},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
}

FILE_TOOLS = [WRITE_FILE_TOOL, EDIT_FILE_TOOL]
FILE_TOOL_NAMES = ("write_file", "edit_file")
