"""Workspace actions: list, create, switch and archive projects.

These replace the old "create-repo" keyword intercept and the rule in
professional memory that told the model to run create-repo-github.sh. The
model calls them as tools (see WORKSPACE_TOOLS); main.py asks the user to
approve anything with side effects. Projects are folders under the projects
root. Nothing here deletes a project: archiving moves its folder to
<root>/.archive/, and its chats, usage and memory are kept.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from optimux_backend.core.config_manager import (
    ApiKeyPayload, check_git_identity, delete_api_key_from_env, get_secret_env_keys, get_settings,
    git_identity_env, save_api_key_to_env, set_github_fields,
)
from optimux_backend.core.security import ValidationError
from optimux_backend.core.security import GITHUB_TOKEN_ENV, ScopeError, resolve_scope_root, scrubbed_env

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
ARCHIVE_DIR = ".archive"
TEMPLATES = ("basic", "python")
VISIBILITIES = ("private", "public")
DESCRIPTION_MAX = 350          # GitHub's limit
GITHUB_API = "https://api.github.com"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{20,255}$")

COMMIT_TEMPLATE = "[Tag] Short summary\n\nDetailed description:\n- \n"
GITIGNORE = {
    "basic": "# OS generated files\n.DS_Store\nThumbs.db\n.env\n",
    "python": ("# OS generated files\n.DS_Store\nThumbs.db\n.env\n\n# Python\n__pycache__/\n*.py[cod]\n"
               ".venv/\nvenv/\n.pytest_cache/\n*.egg-info/\ndist/\nbuild/\n"),
}


class WorkspaceError(Exception):
    """A request that can't be carried out; the message is shown to the model and the user."""


def projects_root() -> str:
    root = get_settings().get("projects_root", "")
    try:
        return str(resolve_scope_root(root))
    except ScopeError as e:
        raise WorkspaceError(f"The Workspace folder isn't set up: {e}")


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name) or name in (".", ".."):
        raise WorkspaceError(f"'{name}' isn't a valid project name: use letters, digits, '.', '_' or '-' "
                             "(up to 100 characters, not starting with a dot or dash).")
    return name


def project_names(root: str | None = None) -> list[str]:
    root = root or projects_root()
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)) and not d.startswith("."))


def _env(github: bool = False) -> dict:
    """Environment for Optimux's own git and gh calls: no LLM keys, the commit
    identity from the settings, nothing prompts, and, only for the calls that
    talk to GitHub, the saved GitHub token as GH_TOKEN. Model-run commands
    never get this environment."""
    env = scrubbed_env(get_secret_env_keys())
    env.update(git_identity_env(get_settings()))
    env.update(GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0", GH_NO_UPDATE_NOTIFIER="1")
    if not github:
        for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
            env.pop(name, None)      # GitHub credentials only where GitHub is called
    token = os.environ.get(GITHUB_TOKEN_ENV, "")
    if github and token:
        env["GH_TOKEN"] = token
    return env


# Optimux's own git calls never run hooks or an fsmonitor from a project's
# config (projects are writable by model-run commands)
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


def _run(cmd: list[str], cwd: str, timeout: int = 60, github: bool = False, keep: int | None = 2000) -> dict:
    argv = ["git", *GIT_SAFE, *cmd[1:]] if cmd[0] == "git" else cmd
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=_env(github))
        out = (p.stdout + p.stderr).strip()
        return {"step": shlex.join(cmd), "ok": p.returncode == 0, "output": out if keep is None else out[-keep:]}
    except FileNotFoundError:
        return {"step": shlex.join(cmd), "ok": False, "output": f"{cmd[0]} is not installed"}
    except subprocess.TimeoutExpired:
        return {"step": shlex.join(cmd), "ok": False, "output": f"timed out after {timeout}s"}


def _git_remote(path: str) -> str | None:
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    r = _run(["git", "remote", "get-url", "origin"], path, timeout=5)
    return r["output"] if r["ok"] else None


def list_projects() -> dict:
    root = projects_root()
    active = get_settings().get("active_project", "")
    projects = []
    for name in project_names(root):
        path = os.path.join(root, name)
        projects.append({
            "name": name,
            "active": name == active,
            "git": os.path.isdir(os.path.join(path, ".git")),
            "remote": _git_remote(path),
            "modified": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path))),
        })
    archived = sorted(os.listdir(os.path.join(root, ARCHIVE_DIR))) if os.path.isdir(os.path.join(root, ARCHIVE_DIR)) else []
    return {"root": root, "active": active or None, "projects": projects, "archived": archived}


def form_values(form) -> dict:
    """The create form (from the model's tool call, the chat form or the New
    project button) as plain values, without judging them (so a form shown
    again keeps what the user typed)."""
    form = form if isinstance(form, dict) else {}
    out = {"name": str(form.get("name") or "").strip()[:200], "template": str(form.get("template") or "basic"),
           "git_init": form.get("git_init", True) is not False, "github": str(form.get("github") or "") or None,
           "description": " ".join(str(form.get("description") or "").split())[:2000],   # one line, for GitHub
           "switch": form.get("switch", True) is not False}
    if out["github"]:
        out["git_init"] = True        # a GitHub repository always starts from a local one
    return out


def normalize_create(form) -> dict:
    """form_values, and a WorkspaceError for what check_create doesn't cover."""
    out = form_values(form)
    if len(out["description"]) > DESCRIPTION_MAX:
        raise WorkspaceError(f"The description is too long ({len(out['description'])} characters; GitHub allows {DESCRIPTION_MAX}).")
    return out


def describe_create(name: str, template: str = "basic", git_init: bool = True,
                    github: str | None = None, description: str = "") -> str:
    """One line for the approval prompt."""
    parts = [f"Create project '{name}' ({template} template)"]
    if git_init:
        parts.append("with a git repository")
    if github:
        parts.append(f"and a {github} GitHub repository" + (f" ({description!r})" if description else ""))
    return " ".join(parts)


def check_create(name: str, template: str = "basic", git_init: bool = True,
                 github: str | None = None) -> tuple[str, str]:
    """Validate a create request before asking for approval. Returns (root, name)."""
    root = projects_root()
    name = validate_name(name)
    if template not in TEMPLATES:
        raise WorkspaceError(f"Unknown template '{template}'; use one of: {', '.join(TEMPLATES)}.")
    if github and github not in VISIBILITIES:
        raise WorkspaceError("GitHub visibility must be 'private' or 'public'.")
    if github and not git_init:
        raise WorkspaceError("A GitHub repository needs git_init=true.")
    if os.path.exists(os.path.join(root, name)):
        raise WorkspaceError(f"A project named '{name}' already exists.")
    return root, name


def create_project(name: str, template: str = "basic", git_init: bool = True,
                   github: str | None = None, description: str = "") -> dict:
    """Create the folder and starter files, then optionally git and GitHub.
    The folder is kept even if a later step fails; each step is reported."""
    root, name = check_create(name, template, git_init, github)
    path = os.path.join(root, name)
    try:
        os.makedirs(path)
    except OSError as e:          # created meanwhile, or the folder isn't writable
        raise WorkspaceError(f"Couldn't create {path}: {e.strerror or e}.")
    from optimux_backend.core import project_records
    project_records.unmark_deleted(name)      # a deleted project's name in use again
    steps = [{"step": f"create {path}", "ok": True, "output": ""}]
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write(f"# {name}\n" + (f"\n{description}\n" if description else ""))
    with open(os.path.join(path, ".gitignore"), "w") as f:
        f.write(GITIGNORE[template])
    if template == "python":
        os.makedirs(os.path.join(path, "src"))
        os.makedirs(os.path.join(path, "tests"))
        open(os.path.join(path, "tests", "__init__.py"), "w").close()
    steps.append({"step": "write starter files", "ok": True, "output": ", ".join(sorted(os.listdir(path)))})

    github_url = None
    if git_init:
        with open(os.path.join(path, "commit-template.txt"), "w") as f:
            f.write(COMMIT_TEMPLATE)
        for cmd in (["git", "init"], ["git", "config", "commit.template", "commit-template.txt"],
                    ["git", "add", "."], ["git", "commit", "-m", "Initial commit"], ["git", "branch", "-M", "main"]):
            r = _run(cmd, path)
            if not r["ok"] and "who you are" in r["output"]:
                r["output"] = ("git doesn't know who commits. Set the commit name and email in "
                               "Dashboard → GitHub, then commit by hand.\n" + r["output"])
            steps.append(r)
            if not r["ok"]:
                break
        if github and all(s["ok"] for s in steps):
            gh = github_status()
            if not gh["ready"]:
                steps.append({"step": "gh repo create", "ok": False,
                              "output": gh["reason"] + " The project was created locally."})
            else:
                r = _run(["gh", "repo", "create", name, f"--{github}", "--description", description or "",
                          "--source=.", "--remote=origin"], path, timeout=120, github=True)
                steps.append(r)
                if r["ok"]:
                    github_url = _git_remote(path)
                    # Pushed over HTTPS (even if gh set an SSH remote) with gh as the
                    # credential helper, so the token never lands in a git config
                    # file or on a command line
                    steps.append(_run(["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                                       "-c", "url.https://github.com/.pushInsteadOf=git@github.com:",
                                       "-c", "url.https://github.com/.pushInsteadOf=ssh://git@github.com/",
                                       "push", "-u", "origin", "main"], path, timeout=120, github=True))
    return {"ok": all(s["ok"] for s in steps), "project": name, "path": path,
            "github_url": github_url, "steps": steps}


# ---------------------------------------------------------------- GitHub

_STATUS_CACHE: dict = {"at": 0.0, "value": None}
STATUS_TTL = 30        # seconds; gh's own sign-in check can be slow (and may touch a keyring)


def github_status(fresh: bool = False) -> dict:
    """Can Optimux create GitHub repositories? gh installed, and a token saved
    in Optimux or gh's own sign-in. Cached briefly (cleared on any change)."""
    now = time.monotonic()
    if not fresh and _STATUS_CACHE["value"] is not None and now - _STATUS_CACHE["at"] < STATUS_TTL:
        return dict(_STATUS_CACHE["value"])
    value = _github_status()
    _STATUS_CACHE.update(at=now, value=value)
    return dict(value)


def _github_status() -> dict:
    gh = shutil.which("gh")
    saved = bool(os.environ.get(GITHUB_TOKEN_ENV))
    out = {"gh": bool(gh), "gh_version": "", "token_saved": saved, "source": None,
           "login": get_settings().get("github_login", "") or "", "ready": False, "reason": ""}
    if not gh:
        out["reason"] = "The GitHub CLI (gh) isn't installed on the Optimux machine."
        return out
    v = _run(["gh", "--version"], os.getcwd(), timeout=10)
    out["gh_version"] = v["output"].splitlines()[0] if v["ok"] and v["output"] else ""
    if saved:
        out["source"] = "optimux"
    elif _run(["gh", "auth", "status", "--hostname", "github.com"], os.getcwd(), timeout=15, github=True)["ok"]:
        out["source"] = "gh"          # someone ran `gh auth login` as the Optimux user (works with old gh too)
    if out["source"]:
        out["ready"] = True
    else:
        out["reason"] = "GitHub isn't set up: add a token in Dashboard → GitHub."
    return out


class GitHubError(Exception):
    """The token was refused or GitHub couldn't be reached; the message says which."""


def check_github_token(token: str) -> dict:
    """Ask GitHub who this token belongs to. Returns login, name, id, the
    classic token scopes (None for fine-grained tokens), a warning (something
    is wrong) and a hint (a reminder that can't be checked from here)."""
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise GitHubError("That doesn't look like a GitHub token (they start with github_pat_ or ghp_).")
    req = urllib.request.Request(GITHUB_API + "/user", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Optimux"})
    try:
        with _github_get(req, timeout=15) as r:
            data = json.load(r)
            scopes_header = r.headers.get("X-OAuth-Scopes")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise GitHubError("GitHub refused the token: it's wrong, expired or revoked.")
        raise GitHubError(f"GitHub answered {e.code} ({e.reason}).")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GitHubError(f"Couldn't reach GitHub: {getattr(e, 'reason', e)}. "
                          "If this machine needs a proxy, set HTTPS_PROXY for Optimux.")
    scopes = None if scopes_header is None else [x.strip() for x in scopes_header.split(",") if x.strip()]
    warning = hint = ""
    if scopes is not None and "repo" not in scopes:
        warning = ("This token can create public repositories only (it has public_repo, not repo)."
                   if "public_repo" in scopes else
                   "This token is missing the 'repo' scope, so creating repositories will fail.")
    elif scopes is None:
        hint = ("Fine-grained token: make sure it has access to All repositories, with Administration and "
                "Contents set to Read and write (GitHub doesn't report that until a repository is created).")
    return {"login": data.get("login", ""), "name": data.get("name") or "", "id": data.get("id"),
            "scopes": scopes, "warning": warning, "hint": hint}


def noreply_email(login: str, uid) -> str:
    """GitHub's private commit address for an account."""
    return f"{uid}+{login}@users.noreply.github.com" if uid else f"{login}@users.noreply.github.com"


def git_has_identity() -> bool:
    """Does git already know who commits (git config user.email), for the Optimux user?"""
    try:
        p = subprocess.run(["git", "config", "--get", "user.email"], capture_output=True, text=True, timeout=5,
                           env=scrubbed_env(get_secret_env_keys()))
        return p.returncode == 0 and bool(p.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def connect_github(token: str) -> dict:
    """Check the token with GitHub, then save it (secrets.env) and the account
    name. When git doesn't know who commits yet, commits are made as the
    GitHub account with its private noreply address."""
    token = (token or "").strip()
    info = check_github_token(token)
    settings = get_settings()
    changes = {"github_login": info["login"]}
    if not (settings.get("git_name") and settings.get("git_email")) and not git_has_identity():
        name = info["name"] or info["login"]
        try:
            check_git_identity(name, "x@example.com")
        except ValidationError:
            name = info["login"]      # a display name git can't use (e.g. with < >)
        changes.update(git_name=name, git_email=noreply_email(info["login"], info["id"]))
    save_api_key_to_env(ApiKeyPayload(env_key=GITHUB_TOKEN_ENV, api_key=token))
    set_github_fields(**changes)
    _STATUS_CACHE["value"] = None
    return {**info, "identity_set": "git_email" in changes}


def disconnect_github() -> None:
    """Forget the saved token (gh's own sign-in, if any, is left alone)."""
    delete_api_key_from_env(GITHUB_TOKEN_ENV)
    set_github_fields(github_login="")
    _STATUS_CACHE["value"] = None


def check_switch(name: str) -> str:
    """'' switches to the projects root (workspace mode)."""
    if not name:
        return ""
    name = validate_name(name)
    if name not in project_names():
        raise WorkspaceError(f"There is no project named '{name}'. Use list_projects to see them.")
    return name


def check_archive(name: str) -> str:
    name = validate_name(name)
    if name not in project_names():
        raise WorkspaceError(f"There is no project named '{name}'.")
    return name


def archive_project(name: str) -> dict:
    """Move the project folder to <root>/.archive/<name>-<timestamp>. Chats, usage and memory are kept."""
    name = check_archive(name)
    root = projects_root()
    dest_dir = _archive_dir(create=True)
    t = time.time()
    while True:                   # never move into an existing folder (same second)
        dest = os.path.join(dest_dir, f"{name}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}")
        if not os.path.lexists(dest):
            break
        t += 1
    try:
        shutil.move(os.path.join(root, name), dest)
    except OSError as e:
        raise WorkspaceError(f"Couldn't archive '{name}': {e.strerror or e}.")
    return {"ok": True, "project": name, "archived_to": dest}


# ---------------------------------------------------------------- archived projects (fix25a)
# Archive, restore and delete are Dashboard buttons. The model can archive
# (archive_project) but never restore or delete: there is no tool for those.

ARCHIVED_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,99})-(?P<stamp>\d{8}-\d{6})$")
DELETE_PHRASE = "yes I want to delete {name}"


def _archive_dir(create: bool = False) -> str:
    """<root>/.archive, refused if it is a symlink or resolves elsewhere (a
    model-run command could plant one to point archive/restore/delete outside)."""
    root = projects_root()
    d = os.path.join(root, ARCHIVE_DIR)
    if create and not os.path.lexists(d):
        os.makedirs(d, exist_ok=True)
    if os.path.lexists(d) and (os.path.islink(d) or os.path.realpath(d) != os.path.join(os.path.realpath(root), ARCHIVE_DIR)):
        raise WorkspaceError(f"{d} isn't a plain folder inside the Workspace (a link?); fix or remove it by hand.")
    return d


def list_archived() -> list[dict]:
    """Archived project folders, newest first: folder, project name, when."""
    d = _archive_dir()
    out = []
    try:
        entries = os.listdir(d) if os.path.isdir(d) else []
    except OSError as e:
        raise WorkspaceError(f"Can't read {d}: {e.strerror or e}.")
    for folder in entries:
        m = ARCHIVED_RE.match(folder)
        path = os.path.join(d, folder)
        if not m or os.path.islink(path) or not os.path.isdir(path):
            continue
        try:
            when = time.mktime(time.strptime(m["stamp"], "%Y%m%d-%H%M%S"))
        except (ValueError, OverflowError):
            continue              # not one of ours (e.g. foo-99999999-999999)
        out.append({"folder": folder, "name": m["name"], "archived": when})
    return sorted(out, key=lambda x: -x["archived"])


def _archived_path(folder: str) -> tuple[str, str]:
    """(path, project name) of an archived folder; refuses anything else
    (other names, symlinks, paths that resolve outside .archive/)."""
    m = ARCHIVED_RE.match(folder or "")
    if not m:
        raise WorkspaceError(f"'{folder}' isn't an archived project.")
    d = _archive_dir()
    path = os.path.join(d, folder)
    if os.path.islink(path) or not os.path.isdir(path):
        raise WorkspaceError(f"There is no archived project '{folder}'.")
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(d):
        raise WorkspaceError("That folder isn't inside the Workspace's .archive folder.")
    return path, m["name"]


def restore_archived(folder: str) -> dict:
    """Move an archived folder back into the Workspace under its project name."""
    path, name = _archived_path(folder)
    dest = os.path.join(projects_root(), name)
    if os.path.lexists(dest):
        raise WorkspaceError(f"A project named '{name}' exists already; rename or archive it first.")
    try:
        os.rename(path, dest)     # same file system: one atomic step
    except OSError as e:
        raise WorkspaceError(f"Couldn't restore '{name}': {e.strerror or e}.")
    from optimux_backend.core import project_records
    project_records.unmark_deleted(name)
    return {"ok": True, "project": name, "path": dest}


GITHUB_REMOTE_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                              r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def github_settings_url(remote: str | None) -> str | None:
    """The repository's settings page (where GitHub offers to delete it), for a GitHub remote."""
    m = GITHUB_REMOTE_RE.match(remote or "")
    return f"https://github.com/{m['repo']}/settings" if m else None


def _rmtree_at(parent_fd: int, name: str) -> None:
    """Delete parent/name and everything in it, working through directory file
    descriptors: every folder is opened with O_NOFOLLOW, symlinks are removed,
    never followed, so nothing outside can be reached even if the tree changes
    meanwhile. (shutil.rmtree(dir_fd=...) does the same, but only from Python 3.11.)"""
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        with os.scandir(fd) as entries:
            items = [(e.name, e.is_dir(follow_symlinks=False)) for e in entries]
        for child, is_dir in items:
            if is_dir:
                _rmtree_at(fd, child)
            else:
                os.unlink(child, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent_fd)


def github_repo_state(remote: str | None) -> dict:
    """Does the GitHub repository behind a remote still exist? One request, no
    model involved. With a sign-in (the saved token, or gh's own) the answer is
    exact; anonymously GitHub answers 404 for deleted *and* private repositories,
    so a 404 is only "deleted or private".
    Returns {"state": exists | missing | deleted_or_private | unknown, "repo", "reason"}."""
    m = GITHUB_REMOTE_RE.match(remote or "")
    if not m:
        return {"state": "unknown", "repo": None, "reason": "Not a GitHub remote."}
    repo = m["repo"]
    token = os.environ.get(GITHUB_TOKEN_ENV, "")
    if not token and github_status()["source"] == "gh":
        # gh's own sign-in: let gh ask (exit 0 = found; "Not Found" = gone)
        r = _run(["gh", "api", f"repos/{repo}", "--silent"], os.getcwd(), timeout=15, github=True)
        if r["ok"]:
            return {"state": "exists", "repo": repo, "reason": ""}
        if "Not Found" in r["output"] or "404" in r["output"]:
            return {"state": "missing", "repo": repo, "reason": ""}
        return {"state": "unknown", "repo": repo, "reason": r["output"][-200:]}
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Optimux"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with _github_get(urllib.request.Request(f"{GITHUB_API}/repos/{repo}", headers=headers), timeout=10):
            return {"state": "exists", "repo": repo, "reason": ""}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"state": "missing" if token else "deleted_or_private", "repo": repo, "reason": ""}
        limited = e.code == 429 or (e.code == 403 and (e.headers or {}).get("X-RateLimit-Remaining") == "0")
        reason = ("GitHub's hourly limit for requests without a sign-in was reached; try again later."
                  if limited and not token else f"GitHub answered {e.code}.")
        return {"state": "unknown", "repo": repo, "reason": reason}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"state": "unknown", "repo": repo, "reason": f"Couldn't reach GitHub: {getattr(e, 'reason', e)}."}


# ---------------------------------------------------------------- import from GitHub (Feature 26)

# GitHub's rules: owner = letters, digits, single dashes (not first), up to 39;
# repository = letters, digits, . _ - (never "." or "..")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.\.?$)[A-Za-z0-9_.-]{1,100}$")
REPO_FIELDS = ("full_name", "name", "private", "description", "updated_at", "html_url", "fork", "archived", "size")
MAX_REPOS = 1000


def _repo_key(remote: str | None) -> str | None:
    m = GITHUB_REMOTE_RE.match(remote or "")
    return m["repo"].lower() if m else None


def local_github_repos() -> dict:
    """{"owner/repo" (lower case): project name} for projects whose origin is on GitHub."""
    root = projects_root()
    out = {}
    for name in project_names(root):
        key = _repo_key(_git_remote(os.path.join(root, name)))
        if key:
            out[key] = name
    return out


class _GitHubOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within api.github.com (urllib would otherwise send
    the Authorization header along to any host)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(GITHUB_API + "/"):
            raise urllib.error.HTTPError(newurl, code, "redirect outside api.github.com refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_github_opener = urllib.request.build_opener(_GitHubOnlyRedirects)


def _github_get(req, timeout):
    """Every GitHub API request goes through here."""
    return _github_opener.open(req, timeout=timeout)


def list_github_repos() -> dict:
    """The signed-in account's repositories (its own, collaborations and its
    organizations', as far as the sign-in can see), newest first, each with
    `local`: the project that already has it as origin."""
    status = github_status()
    if not status["ready"]:
        raise GitHubError(status["reason"])
    token = os.environ.get(GITHUB_TOKEN_ENV, "")
    repos, more = [], False
    if token:
        url = f"{GITHUB_API}/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member"
        while url and len(repos) < MAX_REPOS:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                       "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Optimux"})
            try:
                with _github_get(req, timeout=20) as r:
                    repos += json.load(r)
                    links = r.headers.get("Link") or ""
            except urllib.error.HTTPError as e:
                raise GitHubError("GitHub refused the token." if e.code == 401 else f"GitHub answered {e.code}.")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise GitHubError(f"Couldn't reach GitHub: {getattr(e, 'reason', e)}.")
            m = re.search(r'<([^>]+)>;\s*rel="next"', links)
            url = m.group(1) if m and m.group(1).startswith(GITHUB_API + "/") else None
            more = url is not None
    else:   # gh's own sign-in
        r = _run(["gh", "api", "--paginate", "user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member",
                  "--jq", ".[] | {" + ", ".join(REPO_FIELDS) + "}"], os.getcwd(), timeout=120, github=True, keep=None)
        if not r["ok"]:
            raise GitHubError("gh couldn't list the repositories: " + r["output"][-300:])
        lines = [line for line in r["output"].splitlines() if line.strip().startswith("{")]
        more = len(lines) > MAX_REPOS
        repos = [json.loads(line) for line in lines[:MAX_REPOS]]
    try:
        local = local_github_repos()
    except WorkspaceError:
        local = {}
    out = []
    for repo in repos[:MAX_REPOS]:
        item = {k: repo.get(k) for k in REPO_FIELDS}
        if not REPO_RE.match(item["full_name"] or ""):
            continue
        item["local"] = local.get(item["full_name"].lower())
        out.append(item)
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"repos": out, "login": status.get("login") or "", "truncated": more,
            "fine_grained": token.startswith("github_pat_")}


CLONE_PREFIX = ".clone-"
CLONE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def clone_command(full_name: str, name: str, job_id: str) -> tuple[list[str], str, str]:
    """(argv, staging folder, final folder) to clone github.com/<full_name>.

    git clones into a hidden staging folder, <root>/.clone-<job_id>, created
    here (so it's ours alone); `finish_clone` renames it to <root>/<name> only
    when git succeeds. A clone in progress is invisible to the project lists,
    and cleaning up after a failure can only ever touch the staging folder.
    Over HTTPS with gh as the credential helper (the token never reaches
    .git/config or argv); no submodules; Optimux's GIT_SAFE options apply."""
    if not REPO_RE.match(full_name or ""):
        raise WorkspaceError(f"'{full_name}' isn't a GitHub repository name (owner/repo).")
    if not CLONE_ID_RE.match(job_id or ""):
        raise WorkspaceError("Bad clone id.")
    root, name = check_create(name, "basic", True, None)
    if not github_status()["ready"]:
        raise WorkspaceError(github_status()["reason"])
    staging = os.path.join(root, CLONE_PREFIX + job_id)
    try:
        os.mkdir(staging, 0o700)
    except OSError as e:
        raise WorkspaceError(f"Couldn't prepare the clone in {root}: {e.strerror or e}.")
    argv = ["git", *GIT_SAFE, "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
            "clone", "--progress", "--no-recurse-submodules", "--", f"https://github.com/{full_name}.git", staging]
    return argv, staging, os.path.join(root, name)


def finish_clone(staging: str, final: str) -> None:
    """Move a finished clone into place. Never replaces a project: a folder that
    appeared meanwhile under that name makes this fail (the clone is removed)."""
    if os.path.lexists(final):
        raise WorkspaceError(f"A project named '{os.path.basename(final)}' appeared meanwhile; the clone was discarded.")
    try:
        os.rename(staging, final)     # same folder: atomic; refuses a non-empty target
    except OSError as e:
        raise WorkspaceError(f"Couldn't move the clone into place: {e.strerror or e}.")


def clone_env() -> dict:
    """_env(github=True), and Git LFS doesn't download files on its own."""
    env = _env(github=True)
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    return env


def remove_staging(staging: str) -> None:
    """Remove a clone's staging folder (<root>/.clone-<id> only), through its
    parent's file descriptor, never following links."""
    parent, folder = os.path.split(staging)
    if not (folder.startswith(CLONE_PREFIX) and CLONE_ID_RE.match(folder[len(CLONE_PREFIX):])):
        raise WorkspaceError("Not a clone staging folder.")
    if not os.path.lexists(staging) or os.path.islink(staging):
        return
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _rmtree_at(fd, folder)
    finally:
        os.close(fd)


def remove_stale_staging(keep: set[str]) -> None:
    """Remove staging folders no running clone owns (left by a server stopped mid-clone)."""
    try:
        root = projects_root()
        entries = os.listdir(root)
    except (WorkspaceError, OSError):
        return
    for folder in entries:
        if folder.startswith(CLONE_PREFIX) and CLONE_ID_RE.match(folder[len(CLONE_PREFIX):]) \
                and folder[len(CLONE_PREFIX):] not in keep:
            try:
                remove_staging(os.path.join(root, folder))
            except (OSError, WorkspaceError):
                pass


def workspace_snapshot() -> tuple:
    """The folder names in the Workspace and in .archive, to notice changes made
    outside Optimux (a folder made by hand, git clone in a terminal...)."""
    try:
        root = projects_root()
    except WorkspaceError:
        return ()
    try:
        names = tuple(sorted(project_names(root)))
    except OSError:
        names = ()
    arch = os.path.join(root, ARCHIVE_DIR)
    try:
        archived = tuple(sorted(os.listdir(arch))) if os.path.isdir(arch) and not os.path.islink(arch) else ()
    except OSError:
        archived = ()
    return (root, names, archived)


def delete_phrase(name: str) -> str:
    return DELETE_PHRASE.format(name=name)


def phrase_matches(typed: str, name: str) -> bool:
    """The typed confirmation: "yes I want to delete <name>" (spaces and the
    case of the words don't matter; the name must match exactly)."""
    words = (typed or "").split()
    want = delete_phrase(name).split()
    return len(words) == len(want) and [w.lower() for w in words[:-1]] == [w.lower() for w in want[:-1]] \
        and words[-1] == want[-1]


def delete_archived(folder: str, confirm: str, keep_records: bool = True) -> dict:
    """Permanently delete an archived project's folder (never its GitHub repository).
    keep_records: keep its chats, costs and memory, and list it as deleted;
    otherwise forget them too (refused while another folder has the same name)."""
    path, name = _archived_path(folder)
    if not phrase_matches(confirm, name):
        raise WorkspaceError(f'Type "{delete_phrase(name)}" to confirm.')
    others = [a for a in list_archived() if a["name"] == name and a["folder"] != folder]
    alive = os.path.lexists(os.path.join(projects_root(), name))
    if not keep_records and (others or alive):
        raise WorkspaceError(f"Another '{name}' ({'in the Workspace' if alive else 'archived'}) shares these "
                             "chats and costs, so they can't be deleted with this one. Keep them, or delete that one first.")
    remote = _git_remote(path)
    # Deleted relative to the .archive folder opened without following links, so
    # swapping .archive for a link after the checks can't redirect it; rmtree
    # never follows symlinks inside either
    fd = None
    try:
        fd = os.open(_archive_dir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _rmtree_at(fd, folder)
    except OSError as e:
        raise WorkspaceError(f"Couldn't delete all of {path}: {e.strerror or e}. What's left is still there; "
                             "nothing else was changed.")
    finally:
        if fd is not None:
            os.close(fd)
    from optimux_backend.core import project_records
    if keep_records:
        if not (others or alive):
            project_records.mark_deleted(name)
            from optimux_backend.core import budgets
            budgets.save_budget(name, None)       # nothing can spend in it any more
        removed = None
    else:
        removed = project_records.forget(name)
    return {"ok": True, "project": name, "folder": folder, "records_kept": keep_records,
            "removed": removed, "github_remote": remote}


# Tool definitions (OpenAI format). At the projects root all four are offered;
# inside a project only list_projects and switch_project, to keep every call small.
WORKSPACE_TOOLS = [
    {"type": "function", "function": {
        "name": "list_projects",
        "description": "List the projects under the projects root (name, git, GitHub remote, last change) and archived ones.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "create_project",
        "description": ("Create a new project folder with starter files, optionally a git repository and a GitHub "
                        "repository. Call it as soon as the user asks, with whatever they said: they get a form "
                        "filled in with your values where they choose the rest (name, GitHub, visibility, "
                        "description, switching to it) and confirm. Don't ask them these questions first."),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Folder and repository name: letters, digits, . _ - (empty if the user gave none)"},
            "template": {"type": "string", "enum": list(TEMPLATES), "description": "basic (README, .gitignore) or python (adds src/ and tests/)"},
            "git_init": {"type": "boolean", "description": "Create a git repository with an initial commit (default true)"},
            "github": {"type": "string", "enum": list(VISIBILITIES), "description": "Also create and push a GitHub repository with this visibility; omit for none"},
            "description": {"type": "string", "description": "Short description for the README and GitHub"},
        }}}},
    {"type": "function", "function": {
        "name": "switch_project",
        "description": "Make another project active (commands then run in it and its memory applies). An empty name returns to the projects root.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "archive_project",
        "description": "Move a project's folder to the projects root's .archive/ folder. Its chats and costs are kept. The user approves before it runs. Deleting projects is not possible from chat.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
]
# Inside a project the model can still list, create (always with approval) and switch
# projects; archiving stays a Workspace-level action.
IN_PROJECT_TOOLS = ("list_projects", "create_project", "switch_project")


def tools_for(active_project: str) -> list[dict]:
    if active_project:
        return [t for t in WORKSPACE_TOOLS if t["function"]["name"] in IN_PROJECT_TOOLS]
    return WORKSPACE_TOOLS
