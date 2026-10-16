"""Sandbox for the commands the model runs (bubblewrap, Linux).

With the sandbox on, every execute_bash command runs inside `bwrap`:

- the whole system is visible **read-only** (tools, compilers, libraries);
- only the working folder (the active project, or the Workspace folder when no
  project is active) is **writable**;
- the home folder(s) and Optimux's own folder (keys, token, chats, memory) are
  **hidden** behind empty, **read-only** folders, so a write there fails loudly
  ("Read-only file system") instead of vanishing with the sandbox; the global
  skills folder and any extra read-only folders the user lists are put back;
- HOME points at a scratch folder in the private /tmp (with ~/.gitconfig), so
  tool caches (pip, npm) still work and are thrown away after the command;
- /tmp and /run are private and empty (no Docker socket, no session bus);
- the command gets its own process, IPC and hostname namespaces, dies with
  Optimux, and optionally has **no network**.

The sandbox fails closed: when it is on but bubblewrap is missing or doesn't
work (for example unprivileged user namespaces are disabled), commands are
refused instead of running unprotected. OPTIMUX_REQUIRE_SANDBOX=1 forces it on
(the dashboard can't turn it off), for servers reachable by others.

Workspace actions (creating or archiving projects) are fixed git/gh commands
run by the server itself after approval, not shell commands, and don't go
through the sandbox.
"""
import os
import shutil
import subprocess

from optimux_backend.core import paths

REQUIRE_ENV = "OPTIMUX_REQUIRE_SANDBOX"


# HOME inside the sandbox: a scratch folder in the private /tmp
SANDBOX_HOME = "/tmp/home"


class SandboxError(Exception):
    pass


def bwrap_path() -> str | None:
    return shutil.which("bwrap")


def required() -> bool:
    """Forced on by the server's environment (can't be turned off from the UI):
    OPTIMUX_REQUIRE_SANDBOX=1, or OPTIMUX_SERVER=1 which implies it."""
    from optimux_backend.core.auth import server_mode
    return server_mode() or os.environ.get(REQUIRE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def enabled(settings: dict) -> bool:
    return required() or bool(settings.get("sandbox", False))


def _home_dirs() -> list[str]:
    """Folders to hide: /home (all users), the user's home, and Optimux's folder."""
    out = []
    for p in ("/home", os.path.expanduser("~"), paths.HOME):
        p = os.path.realpath(p)
        if os.path.isdir(p) and p != "/" and p not in out:
            out.append(p)
    return out


def _under(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip(os.sep) + os.sep)


def check_ro_path(path: str) -> str:
    """Validate an extra read-only folder: absolute, existing, and not exposing
    Optimux's own folder (keys and token) or a whole home folder."""
    if not path or not os.path.isabs(path):
        raise SandboxError(f"'{path}' must be an absolute path.")
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise SandboxError(f"'{path}' is not an existing folder.")
    if _under(os.path.realpath(paths.HOME), real):
        raise SandboxError(f"'{path}' would expose Optimux's folder ({paths.HOME}), which holds your keys.")
    if real in ("/", "/home") or real == os.path.realpath(os.path.expanduser("~")):
        raise SandboxError(f"'{path}' is too broad; list the specific folders the commands need.")
    return real


def build_argv(command: str, workdir: str, shell: str, network: bool = True,
               ro_paths: list[str] | tuple = (), bwrap: str = "bwrap") -> list[str]:
    """The bwrap command line for one shell command. Order matters: hidden folders
    become empty tmpfs first, then the allowed folders are mounted back on top."""
    workdir = os.path.realpath(workdir)
    argv = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--tmpfs", "/var/tmp", "--tmpfs", "/run"]
    # DNS on systemd-resolved systems lives under /run; put it back read-only
    for keep in ("/run/systemd/resolve",):
        if os.path.isdir(keep):
            argv += ["--ro-bind", keep, keep]
    hidden = [h for h in _home_dirs() if h != workdir]
    for h in hidden:
        argv += ["--tmpfs", h]
    # The working folder is mounted over any of them that lie inside it, so only
    # the others are made read-only at the end (Optimux's folder is re-hidden below)
    hidden_dirs = [h for h in hidden if not _under(h, workdir)]
    home = os.path.realpath(os.path.expanduser("~"))
    argv += ["--dir", home, "--dir", SANDBOX_HOME, "--setenv", "HOME", SANDBOX_HOME]
    gitconfig = os.path.join(home, ".gitconfig")
    if os.path.isfile(gitconfig):
        argv += ["--ro-bind", gitconfig, os.path.join(SANDBOX_HOME, ".gitconfig")]
    for p in ro_paths:
        try:
            real = check_ro_path(p)
        except SandboxError:
            continue  # validated on save; skip anything that became invalid
        argv += ["--ro-bind", real, real]
    argv += ["--bind", workdir, workdir]
    optimux_home = os.path.realpath(paths.HOME)
    if _under(optimux_home, workdir) and os.path.isdir(optimux_home):
        argv += ["--tmpfs", optimux_home]      # the working folder contains Optimux's folder: hide it again
        hidden_dirs.append(optimux_home)
    if os.path.isdir(paths.SKILLS_DIR):
        skills = os.path.realpath(paths.SKILLS_DIR)
        argv += ["--ro-bind", skills, skills]
    # Last, once everything is mounted into them: the hidden folders become read-only
    # (the working folder and the other mounts inside them stay as they are)
    for hidden in hidden_dirs:
        argv += ["--remount-ro", hidden]
    argv += ["--chdir", workdir,
             "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
             "--die-with-parent", "--new-session"]
    if not network:
        argv.append("--unshare-net")
    return argv + [shell, "-c", command]


_STATUS: dict | None = None
PROC_HINT = ("Something is mounted over parts of /proc where Optimux runs, so the kernel refuses each command its "
             "own /proc. As a systemd service, remove ProtectHostname, ProtectProc, ProcSubset, "
             "ProtectKernelTunables and ProtectKernelLogs (see the Guide, Troubleshooting); in a container, "
             "the host may mount files over /proc.")


def status(refresh: bool = False) -> dict:
    """Whether bubblewrap is installed and actually works here (cached)."""
    global _STATUS
    if _STATUS is not None and not refresh:
        return _STATUS
    path = bwrap_path()
    out = {"installed": bool(path), "path": path, "version": None, "works": False, "error": None,
           "required": required()}
    if not path:
        out["error"] = "bubblewrap is not installed (Debian/Ubuntu: sudo apt install bubblewrap)."
    else:
        try:
            v = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
            out["version"] = (v.stdout or v.stderr).strip()
            t = subprocess.run(build_argv("true", "/tmp", shutil.which("bash") or "/bin/sh", bwrap=path),
                               capture_output=True, text=True, timeout=10)
            out["works"] = t.returncode == 0
            if not out["works"]:
                why = (t.stderr or "").strip()[:300] or f"exit {t.returncode}"
                out["error"] = "bubblewrap is installed but can't create a sandbox here: " + why + ". " + (
                    PROC_HINT if "mount proc" in why else "Unprivileged user namespaces may be disabled.")
        except Exception as e:
            out["error"] = f"bubblewrap check failed: {e}"
    _STATUS = out
    return out
