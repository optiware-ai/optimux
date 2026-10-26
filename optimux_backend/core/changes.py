"""What a reply changed in the project's files, from git, without a model call (v0.4-5m).

Before the first command or file change of a request, `snapshot()` records
the project's files as a git tree; when the request ends, `compare()` takes a
second tree and lists what differs, file by file. Both trees are built in a
temporary index file, so the user's own index (what they staged) is never
touched; only objects are added to .git/objects, as `git stash` would. Files
that .gitignore excludes are not seen. A project without git has no summary.
"""
import os
import re
import shutil
import subprocess
import tempfile

TIMEOUT = 20            # seconds for each git call; a slower project gets no summary
MAX_FILES = 30          # files listed; the rest are counted
SHRINK_MIN_LINES = 20   # a file this long that loses half its lines or more is flagged
MAX_COUNTED = 200       # files whose lines are counted (the shrink check); more is a mass change
IGNORE_LINES_SHOWN = 10  # lines removed from a .gitignore that the summary lists
# A file of secrets by its name (also used by Push, gitsync.py)
SECRET_NAME = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets\.env|[^/]*\.pem|[^/]*\.key|id_rsa|id_ed25519|id_ecdsa)$",
                         re.IGNORECASE)


def _git(args: list[str], cwd: str, env: dict | None = None, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, input=data, capture_output=True, timeout=TIMEOUT)


def _clean_env() -> dict:
    """The environment for these git calls: no GIT_* variables from outside, so
    nothing can redirect them to another repository or index."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _tree(top: str) -> str:
    """The project's files as a git tree, built in a temporary index (a copy of
    the real one, so unchanged files aren't hashed again)."""
    env = _clean_env()
    git_dir = _git(["rev-parse", "--absolute-git-dir"], top, env).stdout.decode().strip()
    with tempfile.TemporaryDirectory(prefix="optimux-changes-") as tmp:
        index = os.path.join(tmp, "index")
        real = os.path.join(git_dir, "index")
        if os.path.isfile(real):
            shutil.copy2(real, index)       # keep its mtime: git's check for racily clean entries needs it
        env["GIT_INDEX_FILE"] = index
        add = _git(["add", "-A", "--", "."], top, env)
        if add.returncode != 0:
            raise RuntimeError(add.stderr.decode(errors="replace").strip() or "git add failed")
        tree = _git(["write-tree"], top, env)
        if tree.returncode != 0:
            raise RuntimeError(tree.stderr.decode(errors="replace").strip() or "git write-tree failed")
        return tree.stdout.decode().strip()


def snapshot(workdir: str) -> dict:
    """The state to compare against later: {"git": False} for a project
    without git, {"git": True, "top", "tree"}, or {"git": True, "error"}."""
    try:
        top = _git(["rev-parse", "--show-toplevel"], workdir, _clean_env())
    except (OSError, subprocess.SubprocessError):
        return {"git": False}
    if top.returncode != 0:
        return {"git": False}
    top_dir = top.stdout.decode().strip()
    try:
        return {"git": True, "top": top_dir, "tree": _tree(top_dir)}
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        return {"git": True, "error": str(e)[:200]}


def _lines(top: str, tree: str, path: str) -> int:
    blob = _git(["cat-file", "blob", f"{tree}:{path}"], top, _clean_env()).stdout
    return blob.count(b"\n") + (1 if blob and not blob.endswith(b"\n") else 0)


def _removed_lines(top: str, old: str, new: str, path: str) -> list[str]:
    """The rules a .gitignore lost (not comments or blank lines): what git may see now."""
    def rules(tree):
        if tree is None:
            return []
        blob = _git(["cat-file", "blob", f"{tree}:{path}"], top, _clean_env()).stdout.decode(errors="replace")
        return [line.strip() for line in blob.splitlines() if line.strip() and not line.strip().startswith("#")]
    after = set(rules(new))
    return [r for r in dict.fromkeys(rules(old)) if r not in after]


def compare(before: dict) -> dict:
    """What changed since `snapshot()`: {"git": False}, {"error"}, or
    {"files": [...], "more": n}. Each file: path, status (new | deleted |
    changed), added, removed (None for binary), lines_before, lines_after,
    warn (deleted, emptied or shrunk by half or more, or a file of secrets git
    now sees: `secret`), and for a .gitignore that lost lines, `unignored`
    (those lines: what git may now see)."""
    if not before.get("git"):
        return {"git": False}
    if before.get("error"):
        return {"git": True, "error": before["error"]}
    top, old = before["top"], before["tree"]
    try:
        new = _tree(top)
        if new == old:
            return {"git": True, "files": [], "more": 0}
        env = _clean_env()
        status = _git(["diff-tree", "-r", "--no-renames", "--name-status", "-z", old, new], top, env).stdout.decode(errors="replace")
        numstat = _git(["diff-tree", "-r", "--no-renames", "--numstat", "-z", old, new], top, env).stdout.decode(errors="replace")
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        return {"git": True, "error": str(e)[:200]}
    parts = status.split("\0")
    kinds = {parts[i + 1]: parts[i] for i in range(0, len(parts) - 1, 2) if parts[i]}
    counts = {}
    for entry in numstat.split("\0"):
        bits = entry.split("\t")
        if len(bits) == 3:
            counts[bits[2]] = (None if bits[0] == "-" else int(bits[0]), None if bits[1] == "-" else int(bits[1]))
    files = []
    for i, path in enumerate(sorted(kinds)):
        kind = {"A": "new", "D": "deleted"}.get(kinds[path], "changed")
        added, removed = counts.get(path, (None, None))
        binary = added is None
        count = not binary and i < MAX_COUNTED       # one git call per file: not for huge changes
        before_n = _lines(top, old, path) if count and kind != "new" else 0
        after_n = _lines(top, new, path) if count and kind != "deleted" else 0
        warn = kind == "deleted" or (kind == "changed" and count and before_n >= SHRINK_MIN_LINES
                                     and after_n * 2 <= before_n)
        entry = {"path": path, "status": kind, "added": added, "removed": removed,
                 "lines_before": before_n, "lines_after": after_n, "warn": warn}
        # A file of secrets that git sees now (new, or no longer ignored): a Push would include it
        if kind == "new" and SECRET_NAME.search(path):
            entry.update(warn=True, secret=True)
        if os.path.basename(path) == ".gitignore" and kind != "new" and removed:
            gone = _removed_lines(top, old, new, path)
            if gone:
                entry.update(warn=True, unignored=gone[:IGNORE_LINES_SHOWN], unignored_more=max(0, len(gone) - IGNORE_LINES_SHOWN))
        files.append(entry)
    # Warnings first, so a damaged file is never hidden behind "and N more"
    files.sort(key=lambda f: (not f["warn"], f["path"]))
    return {"git": True, "files": files[:MAX_FILES], "more": max(0, len(files) - MAX_FILES)}
