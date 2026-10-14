"""Where Optimux keeps its files, and the one-time move into that layout.

    $OPTIMUX_HOME (default ~/.optimux)
    ├── config/       settings.json models.json routing.json budgets.json
    ├── secrets.env   API keys and the access token (mode 0600)
    ├── data/         optimux.db
    ├── backups/      daily database copies
    ├── snapshots/    Data and Workspace snapshots (taken in the Dashboard)
    ├── memory/       personal.md professional.md projects/<name>.md
    ├── skills/
    ├── logs/         migrations.log
    └── legacy/       old files, moved here once, never deleted

prepare_home() creates the layout and moves files from older locations (the
repo's .env and the home root). It only moves, never deletes, and logs every
move. main.py calls it before any other module reads a file.
Set OPTIMUX_NO_MIGRATE=1 to skip the move (tests do).
"""
import os
import re
import shutil
import stat
import time

DEFAULT_HOME = os.path.expanduser("~/.optimux")
HOME = os.path.abspath(os.path.expanduser(os.environ.get("OPTIMUX_HOME", DEFAULT_HOME)))
CONFIG_DIR = os.path.join(HOME, "config")
SECRETS_FILE = os.path.join(HOME, "secrets.env")
AUTH_FILE = os.path.join(HOME, "auth.json")  # password hash and two-step secret (Feature 23)
DATA_DIR = os.path.join(HOME, "data")
DB_PATH = os.path.join(DATA_DIR, "optimux.db")
BACKUP_DIR = os.path.join(HOME, "backups")
SNAPSHOT_DIR = os.path.join(HOME, "snapshots")
MEMORY_DIR = os.path.join(HOME, "memory")
SKILLS_DIR = os.path.join(HOME, "skills")
LOGS_DIR = os.path.join(HOME, "logs")
LEGACY_DIR = os.path.join(HOME, "legacy")
MIGRATION_LOG = os.path.join(LOGS_DIR, "migrations.log")

SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
MODELS_FILE = os.path.join(CONFIG_DIR, "models.json")
ROUTING_FILE = os.path.join(CONFIG_DIR, "routing.json")
BUDGETS_FILE = os.path.join(CONFIG_DIR, "budgets.json")
LEGACY_USAGE_LOG = os.path.join(HOME, "usage.jsonl")  # imported once by usage.py

def layout() -> dict:
    return {"home": HOME, "config": CONFIG_DIR, "secrets": SECRETS_FILE, "auth": AUTH_FILE, "data": DATA_DIR,
            "backups": BACKUP_DIR, "snapshots": SNAPSHOT_DIR, "memory": MEMORY_DIR, "skills": SKILLS_DIR,
            "logs": LOGS_DIR, "legacy": LEGACY_DIR}


def _log(moves: list[str], text: str) -> None:
    moves.append(text)
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(MIGRATION_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


def _legacy_target(name: str) -> str:
    """A free path under legacy/ (never overwrites an earlier legacy file)."""
    target = os.path.join(LEGACY_DIR, name)
    n = 1
    while os.path.exists(target):
        target = os.path.join(LEGACY_DIR, f"{name}.{n}")
        n += 1
    return target


def _move(src: str, dst: str, moves: list[str]) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    _log(moves, f"moved {src} -> {dst}")


def _private(path: str) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def write_private(path: str, text: str, append: bool = False) -> None:
    """Write a file that only the owner can read (keys, token). The file is
    created with mode 600 from the start, so it is never briefly readable,
    and an existing file with looser permissions is tightened."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    if append:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, text.encode())
        finally:
            os.close(fd)
        return
    # Replace atomically: readers see the old or the new file, never a half-written one
    tmp = os.path.join(folder, f".{os.path.basename(path)}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, text.encode())
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp, path)
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)


def _merge_env(src: str, dst: str, moves: list[str]) -> None:
    """Add keys from src that dst doesn't have yet (dst wins on conflicts)."""
    def keys(path):
        with open(path) as f:
            return {l.split("=", 1)[0] for l in f if "=" in l and not l.lstrip().startswith("#")}
    have = keys(dst)
    with open(src) as f:
        extra = [l if l.endswith("\n") else l + "\n" for l in f
                 if "=" in l and not l.lstrip().startswith("#") and l.split("=", 1)[0] not in have]
    if extra:
        write_private(dst, "".join(extra), append=True)
        _log(moves, f"added {len(extra)} key(s) from {src} to {dst}")


_BACKUP_NAME = re.compile(r"^optimux-(\d{4}-\d{2}-\d{2})\.db$")


def backup_files(folder: str | None = None) -> list[str]:
    """Daily database backups, oldest first."""
    try:
        names = os.listdir(folder or BACKUP_DIR)
    except FileNotFoundError:
        return []
    found = [(m.group(1), n) for n in names if (m := _BACKUP_NAME.match(n))]
    return [n for _, n in sorted(found)]


def prepare_home(repo_dir: str | None = None) -> list[str]:
    """Create the layout and move old files into it. Returns the moves made."""
    moves: list[str] = []
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    for d in (CONFIG_DIR, DATA_DIR, BACKUP_DIR, MEMORY_DIR, SKILLS_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)
    # Only the owner may enter Optimux's folder or read its keys (also fixes older installs)
    try:
        os.chmod(HOME, stat.S_IRWXU)
    except OSError:
        pass
    for secret in (SECRETS_FILE, AUTH_FILE):
        if os.path.exists(secret):
            _private(secret)
    if os.environ.get("OPTIMUX_NO_MIGRATE") == "1":
        return moves
    repo_dir = os.path.abspath(repo_dir or os.getcwd())

    # 1. Secrets: the repo's .env moves out of the repo
    repo_env = os.path.join(repo_dir, ".env")
    if os.path.isfile(repo_env):
        if not os.path.exists(SECRETS_FILE):
            _move(repo_env, SECRETS_FILE, moves)
        else:
            _merge_env(repo_env, SECRETS_FILE, moves)
            dst = _legacy_target("repo.env")
            _move(repo_env, dst, moves)
            _private(dst)
    if os.path.exists(SECRETS_FILE):
        _private(SECRETS_FILE)

    # 2. Leftovers from older versions
    for name in ("usage.jsonl.migrated", "projects"):
        src = os.path.join(HOME, name)
        if os.path.exists(src):
            _move(src, _legacy_target(name), moves)
    return moves


def migration_log(lines: int = 50) -> list[str]:
    try:
        with open(MIGRATION_LOG) as f:
            return [l.rstrip("\n") for l in f.readlines()[-lines:]]
    except FileNotFoundError:
        return []
