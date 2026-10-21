"""Push and Update from GitHub, run by Optimux when the user clicks (v0.4-5t).

Models never get these: only the user's buttons call them, and each one is
shown first (prepare) and runs only after the user confirms (run).

Push: commit everything (git add -A) and push the current branch; a copy
pushes to its own branch (see workspace.copy_target). Never --force, never
deletes a branch. Refused when GitHub has commits we don't have ("Update
first"), when a file holds one of the saved keys, or without a commit
identity. Update: fast-forward only (git merge --ff-only): refused when the
histories diverged or an incoming file would overwrite a local change.

Every git call goes through workspace._run: hooks and fsmonitor off, no
prompts, the GitHub token only in the environment of calls that talk to
GitHub, with gh as the credential helper (never in a URL or a git config).
run_* re-checks what prepare_* showed (the files' tree, the upstream commit),
so a confirmation never applies to something else.
"""
import os
import re
import tempfile
import threading

from optimux_backend.core import changes as changes_lib
from optimux_backend.core import paths
from optimux_backend.core.security import GITHUB_TOKEN_ENV
from optimux_backend.core.workspace import (
    MANUAL_COMMIT_MESSAGE, WorkspaceError, _GITHUB_REMOTE, _clean_remote, _git_remote, _run, copy_target,
    projects_root, validate_name,
)
from optimux_backend.core.config_manager import get_settings, git_identity_env

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
BIG_FILE = 50 * 1024 * 1024          # GitHub refuses files over 100 MB; warn at 50
SCAN_MAX = 5 * 1024 * 1024            # files larger than this aren't scanned for keys
MIN_SECRET = 8                        # shorter values would match by chance
MAX_COMMITS = 30                      # incoming commits listed by Update
DIFF_CHARS = 15_000                   # diff sent to the model that writes the message
TEMPLATE_CHARS = 4_000
SECRET_NAME = re.compile(r"(^|/)(\.env(\.[^/]*)?|secrets\.env|[^/]*\.pem|[^/]*\.key|id_rsa|id_ed25519|id_ecdsa)$",
                         re.IGNORECASE)
TEMPLATE_FILES = ("commit-template.txt", ".gitmessage", ".github/commit-template.txt")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class GitSyncError(ValueError):
    """Why a push or update can't run; shown to the user as it is."""


def _lock(name: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(name, threading.Lock())


def _project(name: str) -> str:
    name = validate_name(name)
    path = os.path.join(projects_root(), name)
    if not os.path.isdir(path):
        raise WorkspaceError(f"There's no project called {name} in the Workspace folder.")
    if not os.path.isdir(os.path.join(path, ".git")):
        raise GitSyncError("This project isn't a git repository.")
    return path


def _auth(remote: str) -> list[str]:
    """git -c options for calls that talk to GitHub over HTTPS with the saved token."""
    if remote.startswith("https://") and _GITHUB_REMOTE.match(remote) and os.environ.get(GITHUB_TOKEN_ENV):
        return ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    return []


def _git(path: str, *args: str, timeout: int = 30, github: bool = False, auth: list[str] | None = None) -> dict:
    # core.quotePath=false: file names with accents come back as they are, not as "\303\251"
    return _run(["git", "-c", "core.quotePath=false", *(auth or []), *args], path, timeout=timeout,
                github=github, keep=None)


def _ok(r: dict) -> str:
    return r["output"].strip() if r["ok"] else ""


def _github_url(remote: str) -> str | None:
    m = _GITHUB_REMOTE.match(remote or "")
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None


def _basics(name: str, path: str, fetch: bool) -> dict:
    """Remote, branch, the branch on GitHub to compare with, and a fetch."""
    remote = _clean_remote(_git_remote(path) or "")
    branch = _ok(_git(path, "symbolic-ref", "--quiet", "--short", "HEAD", timeout=5))
    out = {"project": name, "remote": remote or None, "branch": branch or None, "copy_of": None,
           "target": None, "github": _github_url(remote), "fetch_error": None, "upstream": None}
    if not remote or not branch:
        return out
    _, out["copy_of"], push_branch = copy_target(name, path, remote)
    out["target"] = push_branch or branch
    if fetch:
        r = _git(path, "fetch", "--quiet", "--no-tags", "origin", timeout=90, github=True, auth=_auth(remote))
        if not r["ok"]:
            out["fetch_error"] = r["output"][-500:] or "git fetch failed"
    ref = f"refs/remotes/origin/{out['target']}"
    if _git(path, "rev-parse", "--verify", "--quiet", ref, timeout=5)["ok"]:
        out["upstream"] = ref
    return out


def _counts(path: str, upstream: str | None) -> tuple[int, int]:
    """(ahead, behind) of HEAD against upstream; (commits, 0) without one."""
    if not upstream:
        n = _ok(_git(path, "rev-list", "--count", "HEAD", timeout=10))
        return (int(n) if n.isdigit() else 0), 0
    r = _ok(_git(path, "rev-list", "--left-right", "--count", f"HEAD...{upstream}", timeout=10)).split()
    return (int(r[0]), int(r[1])) if len(r) == 2 else (0, 0)


def _secret_values() -> list[tuple[str, bytes]]:
    """(name, value) of every key saved in secrets.env: API keys, the GitHub
    token, the access token. Never returned to the page, only their names."""
    out = []
    try:
        with open(paths.SECRETS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                if len(value) >= MIN_SECRET:
                    out.append((key.strip(), value.encode("utf-8")))
    except OSError:
        pass
    return out


def _scan(path: str, files: list[dict]) -> tuple[list[str], list[str]]:
    """(problems, warnings) about the files that would be committed."""
    problems, warnings = [], []
    secrets = _secret_values()
    for f in files:
        if f["status"] == "deleted":
            continue
        full = os.path.join(path, f["path"])
        if os.path.islink(full):
            continue                    # git stores the link itself, not what it points to
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if SECRET_NAME.search(f["path"]):
            warnings.append(f"{f['path']} looks like a file of secrets. Check it shouldn't stay off GitHub "
                            "(add it to .gitignore).")
        if size > BIG_FILE:
            warnings.append(f"{f['path']} is {size // (1024 * 1024)} MB. GitHub refuses files over 100 MB.")
        if secrets and size <= SCAN_MAX:
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            found = sorted({k for k, v in secrets if v in data})
            if found:
                problems.append(f"{f['path']} contains your saved key {', '.join(found)}. Remove it from the "
                                "file (keys stay in ~/.optimux/secrets.env), then push.")
    return problems, warnings


def _template(path: str) -> str:
    """The project's commit template, if it has one (cut to TEMPLATE_CHARS)."""
    configured = _ok(_git(path, "config", "--get", "commit.template", timeout=5))
    names = list(TEMPLATE_FILES)
    if configured:
        names.insert(0, configured)
    root = os.path.realpath(path)
    for n in names:
        full = os.path.realpath(os.path.join(path, os.path.expanduser(n)))
        if not (full == root or full.startswith(root + os.sep)) or not os.path.isfile(full):
            continue                                   # only the project's own files
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read(TEMPLATE_CHARS)
        except OSError:
            continue
    return ""


def push_prepare(name: str) -> dict:
    """What Push would do: nothing changes."""
    path = _project(name)
    out = _basics(name, path, fetch=True)
    out.update(files=[], more=0, ahead=0, behind=0, tree=None, problems=[], warnings=[],
               default_message=MANUAL_COMMIT_MESSAGE, template=False, identity=True, nothing=False)
    if not out["remote"]:
        out["problems"].append("It has no origin. Create the repository on GitHub, then run "
                               "git remote add origin https://github.com/<owner>/<name>.git in it.")
        return out
    if not out["branch"]:
        out["problems"].append("HEAD isn't on a branch (detached). Check out a branch first.")
        return out
    top = _ok(_git(path, "rev-parse", "--show-toplevel", timeout=5)) or path
    tree = changes_lib._tree(top)
    head = _ok(_git(path, "rev-parse", "--verify", "--quiet", "HEAD^{tree}", timeout=5)) or EMPTY_TREE
    out["tree"] = tree
    if tree != head:
        diff = changes_lib.compare({"git": True, "top": top, "tree": head})
        out["files"], out["more"] = diff.get("files", []), diff.get("more", 0)
    out["ahead"], out["behind"] = _counts(path, out["upstream"])
    out["template"] = bool(_template(path))
    if out["files"] and not git_identity_env(get_settings()) \
            and not _ok(_git(path, "config", "--get", "user.email", timeout=5)):
        out["identity"] = False
        out["problems"].append("Git has no name and email to commit with. Set them in Dashboard → GitHub "
                               "(Who commits).")
    problems, warnings = _scan(path, out["files"])
    out["problems"] += problems
    out["warnings"] += warnings
    if out["fetch_error"]:
        out["warnings"].append("Couldn't check GitHub for new commits: " + out["fetch_error"].splitlines()[-1])
    if out["behind"]:
        out["problems"].append(f"GitHub has {out['behind']} commit{'s' if out['behind'] != 1 else ''} on "
                               f"{out['target']} that this project doesn't have. Update first, then push.")
    if not out["files"] and not out["ahead"] and out["upstream"]:
        out["nothing"] = True
        out["problems"].append("Nothing to push: no changes, and GitHub already has every commit.")
    return out


def push_run(name: str, message: str, tree: str | None, confirmed: bool) -> dict:
    """Commit (if there are changes) and push, as shown by push_prepare."""
    lock = _lock(name)
    if not lock.acquire(blocking=False):
        raise GitSyncError("A push or update of this project is already running.")
    try:
        prep = push_prepare(name)
        path = _project(name)
        if prep["tree"] != tree:
            raise GitSyncError("The files changed since you opened this. Open Push again to see the new list.")
        if prep["problems"]:
            raise GitSyncError(prep["problems"][0])
        if prep["warnings"] and not confirmed:
            raise GitSyncError("Confirm the warnings first.")
        commit = None
        if prep["files"]:
            message = (message or "").strip()
            if not message:
                raise GitSyncError("Write a commit message first.")
            add = _git(path, "add", "-A", timeout=60)
            if not add["ok"]:
                raise GitSyncError("git add failed: " + add["output"][-300:])
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write(message + "\n")
                msg_file = f.name
            try:
                c = _git(path, "commit", "--no-verify", "--cleanup=whitespace", "-F", msg_file, timeout=60)
            finally:
                os.unlink(msg_file)
            if not c["ok"]:
                raise GitSyncError("git commit failed: " + c["output"][-300:])
        commit = _ok(_git(path, "rev-parse", "--short", "HEAD", timeout=5))
        full = _ok(_git(path, "rev-parse", "HEAD", timeout=5))
        refspec = f"HEAD:refs/heads/{prep['target']}"
        p = _git(path, "push", "-u", "origin", refspec, timeout=180, github=True, auth=_auth(prep["remote"]))
        if not p["ok"]:
            note = " The commit was made here; Push again sends it." if prep["files"] else ""
            raise GitSyncError("GitHub refused the push: " + p["output"][-500:] + note)
        return {"ok": True, "project": name, "commit": commit, "branch": prep["target"],
                "files": len(prep["files"]) + prep["more"], "commits": prep["ahead"] + (1 if prep["files"] else 0),
                "url": f"{prep['github']}/commit/{full}" if prep["github"] and full else None}
    finally:
        lock.release()


def update_prepare(name: str) -> dict:
    """What Update would bring in: nothing changes."""
    path = _project(name)
    out = _basics(name, path, fetch=True)
    out.update(commits=[], more_commits=0, files=[], ahead=0, behind=0, sha=None, problems=[], local=[],
               up_to_date=False)
    if not out["remote"]:
        out["problems"].append("It has no origin, so there's nothing to update from.")
        return out
    if not out["branch"]:
        out["problems"].append("HEAD isn't on a branch (detached). Check out a branch first.")
        return out
    if out["fetch_error"]:
        out["problems"].append("Couldn't reach GitHub: " + out["fetch_error"].splitlines()[-1])
        return out
    if not out["upstream"]:
        out["problems"].append(f"GitHub has no branch {out['target']} yet"
                               + (": this copy was never pushed, so there's nothing to update from yet."
                                  if out["copy_of"] else "."))
        return out
    out["sha"] = _ok(_git(path, "rev-parse", out["upstream"], timeout=5))
    out["ahead"], out["behind"] = _counts(path, out["upstream"])
    if not out["behind"]:
        out["up_to_date"] = True
        out["problems"].append("Already up to date: GitHub has nothing new on " + out["target"] + ".")
        return out
    log = _ok(_git(path, "log", f"--max-count={MAX_COMMITS}", "--format=%h%x00%s%x00%an%x00%ct",
                   f"HEAD..{out['upstream']}", timeout=10))
    for line in log.splitlines():
        parts = line.split("\0")
        if len(parts) == 4:
            out["commits"].append({"sha": parts[0], "subject": parts[1], "author": parts[2],
                                   "time": int(parts[3]) if parts[3].isdigit() else None})
    out["more_commits"] = max(0, out["behind"] - len(out["commits"]))
    if out["ahead"]:
        out["problems"].append(
            f"This project has {out['ahead']} commit{'s' if out['ahead'] != 1 else ''} GitHub doesn't have, and "
            f"GitHub has {out['behind']} this project doesn't: the histories went apart. Optimux only updates "
            "when nothing has to be merged. Combine them in a terminal (git pull --rebase), then come back.")
    incoming = _ok(_git(path, "diff", "--name-status", "--no-renames", "HEAD", out["upstream"], timeout=20))
    for line in incoming.splitlines():
        bits = line.split("\t", 1)
        if len(bits) == 2:
            out["files"].append({"status": {"A": "new", "D": "deleted"}.get(bits[0][:1], "changed"),
                                 "path": bits[1]})
    # Local changes: tracked files that differ from HEAD (staged or not), and new files
    changed = _ok(_git(path, "diff", "--name-only", "--no-renames", "HEAD", timeout=20)).splitlines()
    untracked = _ok(_git(path, "ls-files", "--others", "--exclude-standard", timeout=20)).splitlines()
    out["local"] = sorted({p for p in changed + untracked if p})
    clash = sorted(set(out["local"]) & {f["path"] for f in out["files"]})
    if clash:
        out["problems"].append("You changed files this update also changes: " + ", ".join(clash[:10])
                               + (" …" if len(clash) > 10 else "") + ". Push your changes first (or undo "
                               "them), then update.")
    return out


def update_run(name: str, sha: str | None) -> dict:
    """Fast-forward to the commit update_prepare showed."""
    lock = _lock(name)
    if not lock.acquire(blocking=False):
        raise GitSyncError("A push or update of this project is already running.")
    try:
        prep = update_prepare(name)
        path = _project(name)
        if not sha or prep["sha"] != sha:
            raise GitSyncError("GitHub changed since you opened this. Open Update again.")
        if prep["problems"]:
            raise GitSyncError(prep["problems"][0])
        r = _git(path, "merge", "--ff-only", "--no-edit", sha, timeout=120)
        if not r["ok"]:
            raise GitSyncError("git refused the update: " + r["output"][-500:])
        return {"ok": True, "project": name, "commits": prep["behind"], "files": len(prep["files"]),
                "head": _ok(_git(path, "rev-parse", "--short", "HEAD", timeout=5))}
    finally:
        lock.release()


def message_material(name: str) -> dict:
    """What the model gets to write a commit message: the project's template,
    recent commit subjects (for style) and the diff of what Push would commit.
    Refused while a file holds a saved key, so a key never reaches a model."""
    path = _project(name)
    top = _ok(_git(path, "rev-parse", "--show-toplevel", timeout=5)) or path
    tree = changes_lib._tree(top)
    head = _ok(_git(path, "rev-parse", "--verify", "--quiet", "HEAD^{tree}", timeout=5)) or EMPTY_TREE
    if tree == head:
        raise GitSyncError("There's nothing to commit, so there's no message to write.")
    diff = changes_lib.compare({"git": True, "top": top, "tree": head})
    problems, _ = _scan(path, diff.get("files", []))
    if problems:
        raise GitSyncError(problems[0])
    stat = _ok(_git(path, "diff", "--stat=100", head, tree, timeout=20))
    patch = _ok(_git(path, "diff", "--no-color", "--no-ext-diff", head, tree, timeout=30))
    if len(patch) > DIFF_CHARS:
        patch = patch[:DIFF_CHARS] + f"\n… (the diff is cut here: {len(patch) - DIFF_CHARS} more characters)"
    recent = _ok(_git(path, "log", "--max-count=5", "--format=%s", timeout=5))
    return {"template": _template(path), "recent": recent, "stat": stat, "diff": patch,
            "files": [f["path"] for f in diff.get("files", [])], "tree": tree}


def message_prompt(m: dict) -> str:
    if m["template"]:
        how = ("Follow this commit template exactly: its first line format, headings, sections and rules. "
               "Fill every part it asks for; replace its placeholders, keep its fixed text.")
    else:
        how = ("Write a short subject line (at most 70 characters), a blank line, then a few short lines on "
               "what changed and why.")
    parts = [
        "Write the git commit message for the changes below. " + how,
        "Keep every line at most 70 characters unless the template says otherwise. Describe only what the "
        "diff shows; don't invent reasons. Reply with the commit message only: no code fences, no comments.",
    ]
    if m["template"]:
        parts.append("COMMIT TEMPLATE:\n" + m["template"])
    if m["recent"]:
        parts.append("RECENT COMMIT SUBJECTS (for style only):\n" + m["recent"])
    parts.append("FILES CHANGED:\n" + m["stat"])
    parts.append("DIFF:\n" + m["diff"])
    return "\n\n".join(parts)


def clean_message(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
