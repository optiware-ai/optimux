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
import hashlib
import json
import os
import re
import tempfile
import threading

from optimux_backend.core import changes as changes_lib
from optimux_backend.core import paths
from optimux_backend.core import security
from optimux_backend.core.security import GITHUB_TOKEN_ENV
from optimux_backend.core.workspace import (
    MANUAL_COMMIT_MESSAGE, WorkspaceError, _GITHUB_REMOTE, _clean_remote, _git_remote, _run, copy_target, git_can_commit,
    projects_root, validate_name,
)
from optimux_backend.core.i18n import tr

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
BIG_FILE = 50 * 1024 * 1024          # GitHub refuses files over 100 MB; warn at 50
SCAN_MAX = 5 * 1024 * 1024            # files larger than this aren't scanned for keys
MAX_COMMITS = 30                      # incoming commits listed by Update
STATUS_CHARS = 20_000                 # output of one command shown by Status
STATUS_LOG = 5                        # recent commits shown by Status
DIFF_CHARS = 15_000                   # diff sent to the model that writes the message
TEMPLATE_CHARS = 4_000
SECRET_NAME = changes_lib.SECRET_NAME
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
        raise WorkspaceError(tr("There's no project called {name} in the Workspace folder.", name=name))
    if not os.path.isdir(os.path.join(path, ".git")):
        raise GitSyncError(tr("This project isn't a git repository."))
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


# v0.8: where each project last pushed from Optimux. A command the model runs can
# change origin (git remote set-url); Push then warns before sending the code there.
REMOTES_FILE = os.path.join(paths.CONFIG_DIR, "git_remotes.json")


def _known_remotes() -> dict:
    try:
        with open(REMOTES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_remote(name: str, remote: str) -> None:
    data = _known_remotes()
    if data.get(name) == remote:
        return
    data[name] = remote
    os.makedirs(os.path.dirname(REMOTES_FILE), exist_ok=True)
    tmp = REMOTES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, REMOTES_FILE)


def changed_remote(name: str, remote: str | None) -> str | None:
    """The remote of the last push from Optimux when origin points elsewhere now."""
    before = _known_remotes().get(name)
    return before if before and remote and before != remote else None


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
    """(name, value) of every saved key (security.secret_values). Only their
    names are ever shown."""
    return [(k, v.encode("utf-8")) for k, v in security.secret_values()]


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


def status_report(name: str) -> dict:
    """What Status shows in its terminal window: git fetch (only the remote
    branches move, never the files), git status and the last commits, plus
    how far the branch is from the one it pushes to on GitHub. Nothing in
    the project changes; saved keys in the output are masked."""
    path = _project(name)
    lock = _lock(name)
    if not lock.acquire(blocking=False):
        raise GitSyncError(tr("A push or update of this project is running. Try Status again when it's done."))
    try:
        out = _basics(name, path, fetch=True)
        steps = []
        if out["remote"]:
            steps.append(("git fetch origin", not out["fetch_error"], out["fetch_error"] or ""))
        r = _git(path, "--no-optional-locks", "status", "--branch", timeout=20)
        steps.append(("git status", r["ok"], r["output"]))
        r = _git(path, "log", "--oneline", "--decorate", "-n", str(STATUS_LOG), timeout=10)
        steps.append((f"git log --oneline -n {STATUS_LOG}", r["ok"], r["output"]))
        ahead, behind = _counts(path, out["upstream"]) if out["upstream"] else (None, None)
    finally:
        lock.release()
    shown = _shown(steps)
    return {"project": name, "remote": security.mask_secrets(out["remote"] or "")[0] or None,
            "branch": out["branch"], "target": out["target"], "copy_of": out["copy_of"],
            "upstream": out["upstream"], "ahead": ahead, "behind": behind, "steps": shown}


# ssh remotes: scp-like [user@]host:path, or ssh://[user@]host[:port]/path. The
# user and host can't start with "-" (a remote set by a model-run command must
# never become an ssh option).
_SSH_SCP = re.compile(r"^(?:((?!-)[A-Za-z0-9._-]+)@)?((?!-)[A-Za-z0-9._-]+):(?!//)\S+$")
_SSH_URL = re.compile(r"^ssh://(?:((?!-)[A-Za-z0-9._-]+)@)?((?!-)[A-Za-z0-9._-]+)(?::(\d{1,5}))?/\S*$")
SSH_TIMEOUT = 15


def ssh_target(remote: str) -> tuple[str, str | None] | None:
    """(destination, port) for an ssh remote, else None."""
    m = _SSH_URL.match(remote or "") or _SSH_SCP.match(remote or "")
    if not m:
        return None
    user, host = m.group(1), m.group(2)
    port = m.group(3) if m.re is _SSH_URL else None
    return (f"{user}@{host}" if user else host), port


def _authenticated(text: str) -> bool:
    low = text.lower()
    if "denied" in low or "not authenticated" in low or "verification failed" in low:
        return False
    return "successfully authenticated" in low or "welcome to" in low or "authenticated via" in low


def identity_report(name: str) -> dict:
    """Who talks to the remote: the commit identity, then for an ssh remote
    `ssh -T` (as git would connect, never asking anything: an unknown host key
    fails instead of being added), for an HTTPS GitHub remote the account of
    Optimux's saved token (`gh auth status`). Nothing in the project changes;
    URL credentials and saved keys are removed from the output."""
    path = _project(name)
    remote = _clean_remote(_git_remote(path) or "")
    steps, notes = [], []
    r = _git(path, "var", "GIT_AUTHOR_IDENT", timeout=10)
    steps.append(("git var GIT_AUTHOR_IDENT", r["ok"],
                  re.sub(r"\s+\d+ [+-]\d{4}$", "", r["output"]) if r["ok"] else r["output"]))
    r = _git(path, "remote", "get-url", "origin", timeout=5)
    steps.append(("git remote get-url origin", r["ok"], remote if r["ok"] else r["output"]))
    kind = "none"
    if remote and (target := ssh_target(remote)):
        kind = "ssh"
        dest, port = target
        shown = ["ssh", "-T", *(["-p", port] if port else []), dest]
        r = _run(["ssh", "-T", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_TIMEOUT - 5}",
                  *(["-p", port] if port else []), "--", dest], path, timeout=SSH_TIMEOUT, keep=None)
        steps.append((" ".join(shown), r["ok"] or _authenticated(r["output"]), r["output"]))
        own = _ok(_git(path, "config", "--get", "core.sshCommand", timeout=5))
        if own:
            notes.append(f"This repository sets core.sshCommand ({own}); git uses it, this check used plain ssh.")
        if "verification failed" in r["output"].lower():
            notes.append(f"Unknown host key: run ssh -T {dest} once as the user Optimux runs as, and check the key.")
    elif remote.startswith("https://") and _GITHUB_REMOTE.match(remote):
        kind = "https"
        if os.environ.get(GITHUB_TOKEN_ENV):
            r = _run(["gh", "auth", "status", "--hostname", "github.com"], path, timeout=SSH_TIMEOUT, github=True,
                     keep=None)
            steps.append(("gh auth status --hostname github.com", r["ok"], r["output"]))
            notes.append("Push and Update use this account (the GitHub token saved in Optimux).")
        else:
            notes.append("No GitHub token is saved in Optimux (Dashboard → GitHub): git uses this machine's "
                         "own credential helper for HTTPS.")
    elif remote.startswith(("https://", "http://")):
        kind = "https"
        notes.append("An HTTPS remote outside GitHub: the account is whatever this machine's credential helper "
                     "holds; Optimux can't ask it without a password prompt.")
    elif remote:
        kind = "local"
        notes.append("origin is a folder on this machine: no account is involved.")
    else:
        notes.append("No origin: this project isn't connected to a remote repository.")
    return {"project": name, "remote": remote or None, "kind": kind, "steps": _shown(steps), "notes": notes}


def _shown(steps: list[tuple[str, bool, str]]) -> list[dict]:
    """Steps for a terminal window: cut, without URL credentials or saved keys."""
    out = []
    for command, ok, text in steps:
        if len(text) > STATUS_CHARS:
            text = text[:STATUS_CHARS] + "\n… (cut)"
        text = re.sub(r"(https?://)[^@/\s]+@", r"\1", text)     # no user or token from a URL
        out.append({"command": command, "ok": ok, "output": security.mask_secrets(text, edges=True)[0]})
    return out


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
    if out["files"] and not git_can_commit(path):
        out["identity"] = False
        out["problems"].append("Git has no name and email to commit with. Set them in Dashboard → GitHub "
                               "(Who commits).")
    problems, warnings = _scan(path, out["files"])
    out["problems"] += problems
    out["warnings"] += warnings
    before = changed_remote(name, out["remote"])
    if before:
        out["warnings"].insert(0, tr("origin changed since the last push from Optimux: it was {before}, now it is "
                                     "{now}. Check that this is where this code should go.",
                                     before=security.mask_secrets(before)[0], now=security.mask_secrets(out["remote"])[0]))
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
        raise GitSyncError(tr("A push or update of this project is already running."))
    try:
        prep = push_prepare(name)
        path = _project(name)
        if prep["tree"] != tree:
            raise GitSyncError(tr("The files changed since you opened this. Open Push again to see the new list."))
        if prep["problems"]:
            raise GitSyncError(prep["problems"][0])
        if prep["warnings"] and not confirmed:
            raise GitSyncError(tr("Confirm the warnings first."))
        commit = None
        if prep["files"]:
            message = (message or "").strip()
            if not message:
                raise GitSyncError(tr("Write a commit message first."))
            add = _git(path, "add", "-A", timeout=60)
            if not add["ok"]:
                raise GitSyncError(tr("git add failed: {v}", v=add["output"][-300:]))
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write(message + "\n")
                msg_file = f.name
            try:
                c = _git(path, "commit", "--no-verify", "--cleanup=whitespace", "-F", msg_file, timeout=60)
            finally:
                os.unlink(msg_file)
            if not c["ok"]:
                raise GitSyncError(tr("git commit failed: {v}", v=c["output"][-300:]))
        commit = _ok(_git(path, "rev-parse", "--short", "HEAD", timeout=5))
        full = _ok(_git(path, "rev-parse", "HEAD", timeout=5))
        refspec = f"HEAD:refs/heads/{prep['target']}"
        p = _git(path, "push", "-u", "origin", refspec, timeout=180, github=True, auth=_auth(prep["remote"]))
        if not p["ok"]:
            note = " The commit was made here; Push again sends it." if prep["files"] else ""
            raise GitSyncError(tr("GitHub refused the push: {v}{note}", v=p["output"][-500:], note=note))
        remember_remote(name, prep["remote"])
        return {"ok": True, "project": name, "commit": commit, "branch": prep["target"],
                "files": len(prep["files"]) + prep["more"], "commits": prep["ahead"] + (1 if prep["files"] else 0),
                "url": f"{prep['github']}/commit/{full}" if prep["github"] and full else None}
    finally:
        lock.release()


def _added(top: str) -> tuple[set[str], set[str], str | None]:
    """(paths added to git's index, those changed again since, the index as a tree).
    Read-only: write-tree stores tree objects, the index and the files stay."""
    staged = _git(top, "diff", "--cached", "--name-only", "--no-renames", "-z", timeout=10)
    paths = {p for p in staged["output"].split("\0") if p} if staged["ok"] else set()
    if not paths:
        return set(), set(), None
    later = _git(top, "diff", "--name-only", "--no-renames", "-z", timeout=10)
    again = {p for p in later["output"].split("\0") if p} & paths if later["ok"] else set()
    return paths, again, _ok(_git(top, "write-tree", timeout=10)) or None


def local_state(name: str) -> dict:
    """The Studio's Git pane (v0.8): uncommitted files, branch and ahead/behind
    against the last known GitHub branch. Never fetches: it is refreshed after
    every reply, so it must be fast and work offline. Nothing changes.
    Each file says whether it was added (`staged`: in git's index) and whether it
    changed again since (`again`). With files added, Commit takes only those:
    `staged` counts them, `staged_tree` pins them, and the problems, warnings and
    can_commit are about them alone."""
    path = _project(name)
    out = _basics(name, path, fetch=False)
    out.update(files=[], more=0, ahead=0, behind=0, tree=None, problems=[], warnings=[],
               identity=True, can_commit=False, staged=0, staged_tree=None)
    top = _ok(_git(path, "rev-parse", "--show-toplevel", timeout=5)) or path
    tree = changes_lib._tree(top)
    head = _ok(_git(path, "rev-parse", "--verify", "--quiet", "HEAD^{tree}", timeout=5)) or EMPTY_TREE
    out["tree"] = tree
    if tree and tree != head:
        diff = changes_lib.compare({"git": True, "top": top, "tree": head})
        out["files"], out["more"] = diff.get("files", []), diff.get("more", 0)
    staged, again, staged_tree = _added(top)
    if staged and staged_tree and staged_tree != head:
        shown = {f["path"] for f in out["files"]}
        if staged - shown and not out["more"]:
            # added, then put back as it was: git would still commit the added version
            extra = changes_lib.compare({"git": True, "top": top, "tree": head}, staged_tree).get("files", [])
            out["files"] += [f for f in extra if f["path"] not in shown]
            again |= staged - shown
        out["staged"], out["staged_tree"] = len(staged), staged_tree
    else:
        staged, again = set(), set()
    for f in out["files"]:
        f["staged"], f["again"] = f["path"] in staged, f["path"] in again
    taken = [f for f in out["files"] if f["staged"]] if out["staged"] else out["files"]
    out["ahead"], out["behind"] = _counts(path, out["upstream"])
    if not out["upstream"]:
        out["ahead"] = 0                # no GitHub branch to compare with: nothing is "ahead"
    if out["files"] and not git_can_commit(path):
        out["identity"] = False
        out["problems"].append(tr("Git has no name and email to commit with. Set them in Dashboard → GitHub "
                                  "(Who commits)."))
    if not out["branch"]:
        out["problems"].append(tr("HEAD isn't on a branch (detached). Check out a branch first."))
    problems, warnings = _scan(path, taken)
    out["problems"] += problems
    out["warnings"] += warnings
    out["can_commit"] = bool(taken) and not out["problems"]
    return out


def stage_run(name: str, paths: list[str], staged: bool = True) -> dict:
    """The + in front of a file in the Git pane: add it to what the next commit
    takes (git add), or take it back (the file itself is never touched). An empty
    list means every file. Only paths the pane shows are accepted, and they are
    passed to git literally, never as patterns or options. Returns local_state."""
    lock = _lock(name)
    if not lock.acquire(blocking=False):
        raise GitSyncError(tr("A push or update of this project is already running."))
    try:
        path = _project(name)
        top = _ok(_git(path, "rev-parse", "--show-toplevel", timeout=5)) or path
        state = local_state(name)
        known = {f["path"] for f in state["files"]}
        for p in paths:
            if p not in known:
                raise GitSyncError(tr("{path} has no change to add.", path=str(p)[:200]))
        if staged:
            r = _git(top, "--literal-pathspecs", "add", "-A", "--", *(paths or ["."]), timeout=60)
        elif _ok(_git(top, "rev-parse", "--verify", "--quiet", "HEAD", timeout=5)):
            r = _git(top, "--literal-pathspecs", "reset", "-q", "HEAD", "--", *(paths or ["."]), timeout=60)
        else:               # no commit yet: nothing to reset to
            r = _git(top, "--literal-pathspecs", "rm", "-r", "-q", "--cached", "--", *(paths or ["."]), timeout=60)
        if not r["ok"]:
            raise GitSyncError(tr("git add failed: {v}", v=r["output"][-300:]))
        return local_state(name)
    finally:
        lock.release()


MARK_FILES = 3000      # files whose size and time go into local_mark; more only count by name


def local_mark(name: str) -> str:
    """A fingerprint of what the Git pane shows, polled every few seconds while the
    Studio is open: HEAD, the remote branches (ahead/behind), git status with every
    untracked file, and the size and time of each changed file (an edit to a file
    already shown as changed must count too). Read-only and cheap: no fetch, no
    temporary index. '' for a folder without git."""
    path = _project(name)
    status = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all", timeout=10)
    if not status["ok"]:
        return ""
    head = _ok(_git(path, "rev-parse", "--verify", "--quiet", "HEAD", timeout=5))
    refs = _ok(_git(path, "for-each-ref", "--format=%(objectname) %(refname)", "refs/remotes", timeout=5))
    top = _ok(_git(path, "rev-parse", "--show-toplevel", timeout=5)) or path
    h = hashlib.sha1(f"{head}\n{refs}\n{status['output']}".encode("utf-8", "surrogateescape"))
    entries = [e for e in status["output"].split("\0") if e]
    for entry in entries[:MARK_FILES]:
        rel = entry[3:] if len(entry) > 3 and entry[2] == " " else entry      # renames add a bare old name
        try:
            st = os.stat(os.path.join(top, rel))
            h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\0".encode("utf-8", "surrogateescape"))
        except OSError:
            h.update(f"{rel}:-\0".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def commit_run(name: str, message: str, tree: str | None, confirmed: bool,
               staged_tree: str | None = None) -> dict:
    """Commit here only (no push): every change shown by local_state, or, with
    `staged_tree` (files were added in the pane), exactly those added files."""
    lock = _lock(name)
    if not lock.acquire(blocking=False):
        raise GitSyncError(tr("A push or update of this project is already running."))
    try:
        state = local_state(name)
        path = _project(name)
        only_added = bool(staged_tree or state["staged"])
        # Added files: what is committed is the index, so that is what must not have moved
        # (the other files may change freely). Else everything shown, as before.
        if (state["staged_tree"] != staged_tree) if only_added else (state["tree"] != tree):
            raise GitSyncError(tr("The files changed since this list was shown. Check the new list, then commit."))
        taken = [f for f in state["files"] if f["staged"]] if only_added else state["files"]
        if not taken:
            raise GitSyncError(tr("Nothing to commit."))
        if state["problems"]:
            raise GitSyncError(state["problems"][0])
        if state["warnings"] and not confirmed:
            raise GitSyncError(tr("Confirm the warnings first."))
        message = (message or "").strip()
        if not message:
            raise GitSyncError(tr("Write a commit message first."))
        if not only_added:
            add = _git(path, "add", "-A", timeout=60)
            if not add["ok"]:
                raise GitSyncError(tr("git add failed: {v}", v=add["output"][-300:]))
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(message + "\n")
            msg_file = f.name
        try:
            c = _git(path, "commit", "--no-verify", "--cleanup=whitespace", "-F", msg_file, timeout=60)
        finally:
            os.unlink(msg_file)
        if not c["ok"]:
            raise GitSyncError(tr("git commit failed: {v}", v=c["output"][-300:]))
        return {"ok": True, "project": name, "commit": _ok(_git(path, "rev-parse", "--short", "HEAD", timeout=5)),
                "branch": state["branch"],
                "files": state["staged"] if only_added else len(state["files"]) + state["more"]}
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
        raise GitSyncError(tr("A push or update of this project is already running."))
    try:
        prep = update_prepare(name)
        path = _project(name)
        if not sha or prep["sha"] != sha:
            raise GitSyncError(tr("GitHub changed since you opened this. Open Update again."))
        if prep["problems"]:
            raise GitSyncError(prep["problems"][0])
        r = _git(path, "merge", "--ff-only", "--no-edit", sha, timeout=120)
        if not r["ok"]:
            raise GitSyncError(tr("git refused the update: {v}", v=r["output"][-500:]))
        return {"ok": True, "project": name, "commits": prep["behind"], "files": len(prep["files"]),
                "head": _ok(_git(path, "rev-parse", "--short", "HEAD", timeout=5))}
    finally:
        lock.release()


def message_material(name: str, staged: bool = False) -> dict:
    """What the model gets to write a commit message: the project's template,
    recent commit subjects (for style) and the diff of what Push would commit
    (with `staged`: of the files added in the Git pane only).
    Refused while a file holds a saved key, so a key never reaches a model."""
    path = _project(name)
    top = _ok(_git(path, "rev-parse", "--show-toplevel", timeout=5)) or path
    tree = (_added(top)[2] if staged else None) or changes_lib._tree(top)
    head = _ok(_git(path, "rev-parse", "--verify", "--quiet", "HEAD^{tree}", timeout=5)) or EMPTY_TREE
    if tree == head:
        raise GitSyncError(tr("There's nothing to commit, so there's no message to write."))
    diff = changes_lib.compare({"git": True, "top": top, "tree": head}, tree)
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
