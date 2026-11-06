"""Autonomy levels (v0.8): how much models may do without asking, per project.

Three levels:
- supervised: every command and every file change waits for the user.
- checkpointed: file changes and ordinary commands inside the project run
  without asking; models stop for git history (commit, merge, rebase...), for a
  whole-file rewrite of a file that already exists, and for the hard limits.
- autonomous: also commits and rewrites files; models stop only for the hard limits.

Hard limits, at every level: publishing (git push, gh), deleting files,
installing software, network use, system changes (sudo, services, killing
processes), paths outside the project, and commands Optimux can't read (eval).
Budget limits are enforced separately (budgets.py), and creating or archiving
projects always asks (workspace tools). With no project active (the Workspace)
everything asks.

The check reads the command's words before it runs; it is a guard against a
model doing these things by accident, not a sandbox. Scripts the model runs can
still do anything their own code does; the bubblewrap sandbox (project-only
writes, no network unless turned on) is what limits them. On a server
(OPTIMUX_SERVER=1) the levels apply only while the sandbox works; otherwise every
project is supervised.

Levels live in config/autonomy.json: {"default": level, "projects": {name: level}}.
Without a saved default, the old auto_approve setting decides it (on:
checkpointed, off: supervised), so existing installs keep their behaviour.
Ported from OmniLatch (v0.1.0-12).
"""
import json
import os
import re
import shlex

from optimux_backend.core import paths
from optimux_backend.core.security import strip_heredocs
from optimux_backend.core.i18n import tk, tr

AUTONOMY_FILE = paths.AUTONOMY_FILE

LEVELS = ("supervised", "checkpointed", "autonomous")
LEVEL_TEXT = {
    "supervised": tk("Every command and file change waits for you."),
    "checkpointed": tk("File changes and ordinary commands in the project run without asking; models stop before "
                    "commits, before rewriting a whole file, and at the hard limits."),
    "autonomous": tk("Models also commit and rewrite files on their own; they stop only at the hard limits."),
}
HARD = ("publish", "delete", "install", "network", "system", "unclear", "outside")
REASONS = {
    "publish": tk("it publishes (git push, GitHub)"),
    "delete": tk("it deletes or discards files"),
    "install": tk("it installs or removes software"),
    "network": tk("it uses the network"),
    "system": tk("it changes the system (sudo, services, processes)"),
    "unclear": tk("it builds a command that can't be checked before it runs (eval)"),
    "outside": tk("it touches paths outside the project"),
    "history": tk("it changes git history (commit, merge, rebase…)"),
    "rewrite": tk("it rewrites a whole file that already exists"),
}

SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n", "|&", ";;"}
WRAPPERS = {"env", "nohup", "time", "nice", "ionice", "command", "exec", "builtin", "stdbuf", "chronic"}
DELETE = {"rm", "rmdir", "unlink", "shred", "srm", "trash", "trash-put", "gio"}
NETWORK = {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet", "ftp", "lftp", "http",
           "https", "aria2c", "npx", "bunx", "pipx", "docker", "podman", "socat"}
INSTALL = {"apt", "apt-get", "aptitude", "dnf", "yum", "pacman", "zypper", "brew", "snap", "flatpak", "port", "emerge",
           "apk", "nix-env", "conda", "mamba", "micromamba", "gem", "dpkg", "rpm"}
INSTALL_SUB = {"pip": {"install", "uninstall", "download"}, "pip3": {"install", "uninstall", "download"},
               "npm": {"install", "i", "add", "ci", "uninstall", "remove", "rm", "update", "up", "exec"},
               "yarn": {"add", "install", "remove", "dlx", "up", "upgrade"},
               "pnpm": {"add", "install", "i", "remove", "rm", "update", "up", "dlx"},
               "bun": {"add", "install", "i", "remove", "x"}, "cargo": {"install", "uninstall"},
               "go": {"install", "get"}, "uv": {"pip", "add", "remove", "tool"}, "poetry": {"add", "remove", "install"}}
SYSTEM = {"sudo", "su", "doas", "pkexec", "systemctl", "service", "kill", "killall", "pkill", "reboot", "shutdown",
          "poweroff", "halt", "crontab", "mount", "umount", "chroot", "useradd", "usermod", "passwd", "ufw", "iptables",
          "loginctl", "xdg-open"}
GIT_HISTORY = {"commit", "merge", "rebase", "cherry-pick", "revert", "tag", "am", "pull"}
GIT_NETWORK = {"clone", "fetch", "pull", "ls-remote", "remote"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SUBSHELL = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


def _read() -> dict:
    try:
        with open(AUTONOMY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    os.makedirs(os.path.dirname(AUTONOMY_FILE), exist_ok=True)
    paths.write_private(AUTONOMY_FILE, json.dumps(data, indent=2) + "\n")


def server_block() -> str:
    """Why the levels can't apply here, or "": on a server (OPTIMUX_SERVER=1) they
    need a working sandbox, so an approved-by-level command can only change the project."""
    from optimux_backend.core.auth import server_mode
    if not server_mode():
        return ""
    from optimux_backend.core import sandbox
    from optimux_backend.core.config_manager import get_settings
    if not sandbox.enabled(get_settings()):
        return tr("On a server, levels other than Supervised need the sandbox, and it is off.")
    st = sandbox.status()
    if not st.get("works"):
        return tr("On a server, levels other than Supervised need the sandbox, and it doesn't work here: {error}",
                  error=str(st.get("error") or tr("unknown error")))
    return ""


def _saved_default() -> str:
    level = _read().get("default")
    if level in LEVELS:
        return level
    from optimux_backend.core.config_manager import get_settings
    return "checkpointed" if get_settings().get("auto_approve") else "supervised"


def default_level() -> str:
    return "supervised" if server_block() else _saved_default()


def project_level(project: str) -> str:
    """The project's own level, or the default. Supervised with no project (the
    Workspace) and on a server without a working sandbox."""
    if not project or server_block():
        return "supervised"
    level = (_read().get("projects") or {}).get(project)
    return level if level in LEVELS else _saved_default()


def overview() -> dict:
    data = _read()
    own = {k: v for k, v in (data.get("projects") or {}).items() if v in LEVELS}
    block = server_block()
    return {"default": _saved_default(), "default_saved": data.get("default") in LEVELS, "projects": own,
            "blocked": block,                # non-empty: every project is supervised for now, and why
            "levels": [{"id": lv, "text": tr(LEVEL_TEXT[lv])} for lv in LEVELS],
            "hard_limits": [tr(REASONS[h]) for h in HARD]}


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def set_level(project: str | None, level: str | None) -> dict:
    """project None: the default. level None: the project follows the default again."""
    if level is not None and level not in LEVELS:
        raise ValueError(tr("Unknown level {level}; use one of {v}.", level=f'{level!r}', v=', '.join(LEVELS)))
    if project is not None and (not NAME_RE.match(project) or project in (".", "..")):
        raise ValueError(tr("Invalid project name."))
    block = server_block()
    if level not in (None, "supervised") and block:
        raise ValueError(block)
    data = _read()
    if project is None:
        if level is None:
            raise ValueError(tr("The default needs a level."))
        data["default"] = level
    else:
        projects = dict(data.get("projects") or {})
        if level is None:
            projects.pop(project, None)
        else:
            projects[project] = level
        data["projects"] = projects
    _write(data)
    return overview()


# ---------------------------------------------------------------- reading a command

def _segments(command: str) -> list[list[str]]:
    words, out = [], []
    for line in strip_heredocs(command).split("\n"):
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        lex.commenters = "#"
        try:
            tokens = list(lex)
        except ValueError:                      # unbalanced quotes
            tokens = line.split()
        for t in tokens + ["\n"]:
            if t in SEPARATORS or set(t) <= set(";&|()"):
                if words:
                    out.append(words)
                words = []
            else:
                words.append(t)
    return out


def _segment_kinds(words: list[str], depth: int) -> set[str]:
    kinds: set[str] = set()
    i = 0
    while i < len(words) and ASSIGNMENT.match(words[i]):
        i += 1
    while i < len(words):
        name = os.path.basename(words[i])
        if name in ("sudo", "doas", "su", "pkexec"):
            kinds.add("system")
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
            continue
        if name in WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or ASSIGNMENT.match(words[i])):
                i += 1
            continue
        if name in ("timeout", "xargs", "watch"):
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", words[i])):
                i += 1
            continue
        break
    if i >= len(words):
        return kinds
    name, args = os.path.basename(words[i]), words[i + 1:]
    if name in ("eval",) or words[i].startswith("$"):
        kinds.add("unclear")
    elif name in ("bash", "sh", "zsh", "dash", "fish") and "-c" in args:
        inner = args[args.index("-c") + 1] if args.index("-c") + 1 < len(args) else ""
        kinds |= classify(inner, depth + 1) if depth < 3 else {"unclear"}
    elif name in DELETE:
        kinds.add("delete")
    elif name == "find" and ("-delete" in args or any(a in ("-exec", "-execdir", "-ok") for a in args)):
        if "-delete" in args:
            kinds.add("delete")
        for flag in ("-exec", "-execdir", "-ok"):
            if flag in args:
                kinds |= _segment_kinds(args[args.index(flag) + 1:], depth + 1)
    elif name in NETWORK:
        kinds.add("network")
    elif name in INSTALL:
        kinds.add("install")
    elif name in INSTALL_SUB and args and args[0] in INSTALL_SUB[name]:
        kinds.add("install")
        kinds.add("network")
    elif name.startswith("python") and len(args) >= 3 and args[0] == "-m" and args[1] in ("pip", "ensurepip") \
            and args[2] in ("install", "uninstall", "download"):
        kinds |= {"install", "network"}
    elif name in SYSTEM:
        kinds.add("system")
    elif name == "gh":
        kinds |= {"publish", "network"}
    elif name == "git":
        kinds |= _git_kinds(args)
    return kinds


def _git_kinds(args: list[str]) -> set[str]:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i >= len(args):
        return set()
    sub, rest = args[i], args[i + 1:]
    if sub == "push":
        return {"publish", "network"}
    if sub == "lfs":
        verb = rest[0] if rest else ""
        return {"publish", "network"} if verb == "push" else {"network"} if verb in ("pull", "fetch") else set()
    if sub in ("rm", "clean"):
        return {"delete"}
    if sub == "reset" and "--hard" in rest:
        return {"delete", "history"}
    if sub in ("checkout", "restore") and ("--" in rest or "." in rest or "-f" in rest or "--force" in rest):
        return {"delete"}
    if sub == "stash" and rest and rest[0] in ("drop", "clear"):
        return {"delete"}
    if sub == "branch" and any(a in ("-d", "-D", "--delete") for a in rest):
        return {"history"}
    kinds = set()
    if sub in GIT_NETWORK:
        kinds.add("network")
    if sub in GIT_HISTORY:
        kinds.add("history")
    if sub == "submodule" and rest and rest[0] in ("update", "add", "sync"):
        kinds.add("network")
    return kinds


def classify(command: str, depth: int = 0) -> set[str]:
    """The kinds of things a shell command does that matter for approvals."""
    kinds: set[str] = set()
    for m in SUBSHELL.finditer(command):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        kinds |= classify(inner, depth + 1) if depth < 3 else {"unclear"}
    for words in _segments(SUBSHELL.sub(" ", command)):
        kinds |= _segment_kinds(words, depth)
    return kinds


def decide(level: str, kind: str, command: str = "", outside: list | None = None) -> tuple[bool, str]:
    """(ask?, why) for a command ("command"), a file change ("file") or a whole-file
    rewrite of an existing file ("rewrite") at a level."""
    if level not in LEVELS:
        level = "supervised"
    found = classify(command) if kind == "command" else set()
    if outside:
        found.add("outside")
    hard = [tr(REASONS[h]) for h in HARD if h in found]
    if hard:
        return True, tr("Always asked: {reasons}.", reasons="; ".join(hard))
    if level == "supervised":
        return True, tr("Supervised: every command and file change waits for you.")
    if level == "checkpointed" and "history" in found:
        return True, tr("Checkpointed: {reason}.", reason=tr(REASONS["history"]))
    if level == "checkpointed" and kind == "rewrite":
        return True, tr("Checkpointed: {reason}.", reason=tr(REASONS["rewrite"]))
    return False, ""
