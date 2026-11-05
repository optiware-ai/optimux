"""Moving files aside instead of deleting them (v0.8, the Studio's Delete).

Ported from OmniLatch's core/trash.py, with Optimux's rule that files are moved
aside, never deleted: there is no permanent delete.

First choice, the desktop Trash (freedesktop.org Trash specification, home
trash): $XDG_DATA_HOME/Trash (default ~/.local/share/Trash). The item goes to
Trash/files/<name>, and Trash/info/<name>.trashinfo records where it came from
and when, so the desktop's file manager (Nautilus, Dolphin, Thunar...) can
restore it. On a server the home is the service user's (/var/lib/optimux).

The Trash only works on the same filesystem (a rename, never a copy). When the
item is on another one, it goes to the Workspace's own `.trash/` folder, with the
same layout (files/ and info/), which Optimux never lists as a project. When that
isn't on the same filesystem either, nothing is moved and the caller says so.
"""
import datetime
import os
from urllib.parse import quote

WORKSPACE_TRASH = ".trash"


def home_trash() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "Trash")


def _nearest_existing(path: str) -> str:
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _same_device(path: str, trash: str) -> bool:
    try:
        return os.lstat(path).st_dev == os.stat(_nearest_existing(trash)).st_dev
    except OSError:
        return False


def where(path: str, workspace: str | None) -> str | None:
    """The trash folder `path` (not followed if it's a link) can be moved to:
    the home trash, else the Workspace's .trash/, else None."""
    if _same_device(path, home_trash()):
        return home_trash()
    if workspace:
        ws_trash = os.path.join(workspace, WORKSPACE_TRASH)
        if not os.path.islink(ws_trash) and _same_device(path, ws_trash):
            return ws_trash
    return None


def move_to_trash(path: str, trash: str) -> str:
    """Move `path` (a file, folder or link; links aren't followed) into `trash`.
    Returns the name it got in trash/files. Raises OSError if it can't."""
    files, info = os.path.join(trash, "files"), os.path.join(trash, "info")
    for d in (trash, files, info):
        os.makedirs(d, mode=0o700, exist_ok=True)
    base = os.path.basename(path.rstrip(os.sep)) or "item"
    stem, ext = os.path.splitext(base)
    date = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    record = f"[Trash Info]\nPath={quote(os.path.abspath(path), safe='/')}\nDeletionDate={date}\n"
    for n in range(1, 10_000):
        name = base if n == 1 else f"{stem}.{n}{ext}"
        info_path = os.path.join(info, name + ".trashinfo")
        # The .trashinfo is created first and exclusively: it reserves the name
        try:
            fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(record)
        target = os.path.join(files, name)
        if os.path.lexists(target):
            os.unlink(info_path)
            continue
        try:
            os.rename(path, target)
        except OSError:
            os.unlink(info_path)
            raise
        return name
    raise OSError("the Trash has too many items with this name")
