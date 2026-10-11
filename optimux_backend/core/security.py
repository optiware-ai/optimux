"""Security helpers for Optimux: token auth, origin checks, scope validation,
env-key validation, and secret scrubbing for spawned shells.

Kept free of FastAPI imports so it can be unit-tested in isolation.
"""
import hmac
import os
import re
import secrets
import shlex
from pathlib import Path
from urllib.parse import urlsplit

TOKEN_ENV = "OPTIMUX_TOKEN"
# Feature 25: the GitHub token Optimux uses to create repositories (secrets.env).
# Only workspace.py's own gh calls get it; model-run commands never see it.
GITHUB_TOKEN_ENV = "OPTIMUX_GITHUB_TOKEN"
ALLOWED_ORIGINS_ENV = "OPTIMUX_ALLOWED_ORIGINS"

# Names the UI may write to .env. Restricting the suffix keeps the API from
# setting PATH, LD_PRELOAD, BASH_ENV, PYTHONPATH, etc. in the server process.
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,62}$")
ENV_KEY_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_API_BASE")


class ScopeError(ValueError):
    """Raised when a path or name falls outside the configured scope."""


class ValidationError(ValueError):
    """Raised when user-supplied config fails validation."""


# ---------------------------------------------------------------- auth

def _secrets_file() -> str:
    from optimux_backend.core import paths
    return paths.SECRETS_FILE


def _saved_token(env_file: str) -> str:
    """The token saved in secrets.env (the last one, as dotenv reads it), or ''.
    Also accepts its name from before the rename (CODEMUX_TOKEN)."""
    found = {}
    try:
        with open(env_file) as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key in (TOKEN_ENV, "CODEMUX_TOKEN") and value.strip():
                    found[key] = value.strip().strip("'\"")
    except FileNotFoundError:
        return ""
    return found.get(TOKEN_ENV) or found.get("CODEMUX_TOKEN", "")


def get_or_create_token(env_file: str | None = None) -> str:
    """Return the access token, generating and persisting one on first run."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    env_file = env_file or _secrets_file()
    # A process that hasn't loaded secrets.env (the admin command) must find the saved
    # token there, not add a second one the running server doesn't know
    token = _saved_token(env_file)
    if token:
        os.environ[TOKEN_ENV] = token
        return token
    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(os.path.abspath(env_file)), exist_ok=True)
    prefix = ""
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            content = f.read()
        if content and not content.endswith("\n"):
            prefix = "\n"
    from optimux_backend.core import paths
    paths.write_private(env_file, f"{prefix}{TOKEN_ENV}={token}\n", append=True)
    os.environ[TOKEN_ENV] = token
    print(f"[Optimux] Generated access token (saved to {env_file} as {TOKEN_ENV}):\n    {token}")
    return token


def rotate_token(env_file: str | None = None) -> str:
    """Replace the access token in .env and in the running process; returns the new one.
    Already-authenticated WebSocket connections stay open; new requests need the new token."""
    token = secrets.token_urlsafe(32)
    env_file = env_file or _secrets_file()
    lines = []
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            lines = [l for l in f.readlines() if not l.startswith(f"{TOKEN_ENV}=")]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{TOKEN_ENV}={token}\n")
    from optimux_backend.core import paths
    paths.write_private(env_file, "".join(lines))
    os.environ[TOKEN_ENV] = token
    return token


def check_token(provided: str | None, expected: str | None = None) -> bool:
    expected = expected if expected is not None else os.environ.get(TOKEN_ENV, "")
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def bearer_from_header(header_value: str | None) -> str:
    if not header_value:
        return ""
    scheme, _, value = header_value.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


# ---------------------------------------------------------------- origin

def origin_allowed(origin: str | None, host: str | None, extra: str | None = None) -> bool:
    """Allow same-host origins, plus any listed in OPTIMUX_ALLOWED_ORIGINS.

    Non-browser clients send no Origin header; they are allowed here and still
    have to pass token auth.
    """
    if not origin:
        return True
    extra = extra if extra is not None else os.environ.get(ALLOWED_ORIGINS_ENV, "")
    allowed = {o.strip().rstrip("/") for o in extra.split(",") if o.strip()}
    if origin.rstrip("/") in allowed:
        return True
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


# ---------------------------------------------------------------- scope

def validate_project_name(name: str) -> str:
    """A project is a single, non-hidden folder name directly under the scope root."""
    if not isinstance(name, str) or not name.strip():
        raise ScopeError("Project name is empty.")
    if any(c in name for c in ("/", "\\", "\x00")) or name in (".", "..") or name.startswith("."):
        raise ScopeError(f"Invalid project name: {name!r}")
    return name


def resolve_scope_root(root: str) -> Path:
    if not root:
        raise ScopeError("No Workspace folder configured. Set it in Dashboard > Scope.")
    p = Path(root)
    if not p.is_absolute():
        raise ScopeError(f"The Workspace folder must be an absolute path: {root!r}")
    p = p.resolve()
    if not p.is_dir():
        raise ScopeError(f"The Workspace folder does not exist or is not a directory: {root!r}")
    return p


def resolve_project_dir(root: str, name: str) -> Path:
    """Resolve root/name, following symlinks, and require it to stay inside root."""
    base = resolve_scope_root(root)
    target = (base / validate_project_name(name)).resolve()
    if target.parent != base:
        raise ScopeError(f"Project {name!r} resolves outside the Workspace folder.")
    if not target.is_dir():
        raise ScopeError(f"Project folder not found: {name!r}")
    return target


# ---------------------------------------------------------------- env keys

def validate_env_key(key: str) -> str:
    if not isinstance(key, str) or not ENV_KEY_RE.match(key) or not key.endswith(ENV_KEY_SUFFIXES):
        raise ValidationError(
            f"Invalid env key {key!r}: use UPPER_SNAKE_CASE ending in one of {', '.join(ENV_KEY_SUFFIXES)}."
        )
    if key == TOKEN_ENV:
        raise ValidationError(f"{TOKEN_ENV} cannot be changed from the UI.")
    return key


def validate_env_value(value: str) -> str:
    if any(c in value for c in ("\n", "\r", "\x00")):
        raise ValidationError("Value must not contain newlines or NUL bytes.")
    return value


# ---------------------------------------------------------------- subprocess env

def scrubbed_env(secret_names: set[str] | None = None, base: dict | None = None) -> dict:
    """Copy of the environment without LLM provider keys or the Optimux token,
    so a command like `env` or a prompt-injected `curl` can't leak them."""
    base = dict(os.environ if base is None else base)
    drop = set(secret_names or ()) | {TOKEN_ENV, GITHUB_TOKEN_ENV}
    return {k: v for k, v in base.items()
            if k not in drop and not k.endswith("_API_KEY") and not k.startswith("CODEMUX_")}  # old names (before Optimux)


# ---------------------------------------------------------------- command paths

# System locations commands legitimately use; everything else outside the
# project is flagged.
SYSTEM_PREFIXES = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/tmp/", "/proc/self/")
SYSTEM_PATHS = {"/usr", "/bin", "/sbin", "/tmp", "/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"}
PATH_VARIABLES = ("$HOME", "${HOME}", "$OLDPWD", "${OLDPWD}")


HEREDOC_RE = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n"}


def strip_heredocs(command: str) -> str:
    """Drop here-document bodies: they are file contents, not paths."""
    out, pending = [], []
    for line in command.split("\n"):
        if pending:
            strip_tabs, delim = pending[0]
            if (line.lstrip("\t") if strip_tabs else line).rstrip() == delim:
                pending.pop(0)
            continue
        out.append(line)
        pending += [(m.group(1) == "-", m.group(3)) for m in HEREDOC_RE.finditer(line)]
    return "\n".join(out)


def _tokens(command: str) -> list[str]:
    """Shell-like tokens with separators (; && || | & newlines) kept as tokens."""
    tokens = []
    for line in command.split("\n"):
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        try:
            tokens += list(lex)
        except ValueError:  # unbalanced quotes: fall back to plain words
            tokens += line.split()
        tokens.append("\n")
    return tokens


def paths_outside_scope(command: str, workdir: str) -> list[str]:
    """Heuristic: path-like arguments in a shell command that point outside
    `workdir` (the active project).

    Flags absolute paths, ~ paths, `..` paths and $HOME/$OLDPWD that leave the
    project, ignoring common system locations. Here-document bodies are
    skipped (they are file contents), and `cd` is followed within the command,
    so `cd sub && ... && cd ..` stays inside. Not a security boundary:
    variables, command substitution or encoded paths can evade it. The
    sandbox phase replaces it with real isolation.
    """
    root = os.path.realpath(workdir)
    cur = root            # where the command is, following its cd's
    flagged: list[str] = []

    def outside(path: str) -> bool:
        norm = os.path.normpath(path)
        if norm in SYSTEM_PATHS or norm.startswith(SYSTEM_PREFIXES):
            return False
        return not (norm == root or norm.startswith(root + os.sep))

    def flag(tok: str) -> None:
        if tok not in flagged:
            flagged.append(tok)

    def resolve(tok: str) -> str | None:
        if tok.startswith("~"):
            return os.path.expanduser(tok)
        if tok.startswith("/"):
            return tok
        if tok == ".." or tok.startswith("../") or "/../" in tok or tok.endswith("/.."):
            return os.path.join(cur, tok)
        return None

    tokens = _tokens(strip_heredocs(command))
    seg_start = True
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in SEPARATORS:
            seg_start = True
            i += 1
            continue
        if seg_start and tok == "cd":
            seg_start = False
            arg = tokens[i + 1] if i + 1 < len(tokens) and tokens[i + 1] not in SEPARATORS else ""
            if arg in ("", "~"):
                flag(arg or "cd")                       # cd alone goes to $HOME
            elif arg != "-" and not any(arg.startswith(v) for v in PATH_VARIABLES):
                target = resolve(arg) or os.path.join(cur, arg)
                if outside(target):
                    flag(arg)
                else:
                    cur = os.path.normpath(target)      # later relative paths start here
            elif any(arg.startswith(v) for v in PATH_VARIABLES):
                flag(arg)
            i += 2 if arg else 1
            continue
        seg_start = False
        tok = tok.split("=", 1)[1] if tok.startswith("-") and "=" in tok else tok
        if tok and not any(c.isspace() for c in tok) and not ("{" in tok and "}" in tok and not tok.startswith("${")):
            if any(tok.startswith(v) for v in PATH_VARIABLES):
                flag(tok)
            else:
                path = resolve(tok)
                if path is not None and outside(path):
                    flag(tok)
        i += 1
    return flagged
