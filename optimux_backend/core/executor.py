import asyncio
import os
import shutil
import signal

from optimux_backend.core import sandbox
import resource

from optimux_backend.core.config_manager import get_settings, command_env
from optimux_backend.core.security import ScopeError, resolve_project_dir, resolve_scope_root


def get_active_cwd() -> str:
    """The active project folder, or the projects root if no project is selected.

    Raises ScopeError instead of silently falling back to the server's cwd.
    """
    settings = get_settings()
    root = settings.get("projects_root", "")
    active = settings.get("active_project", "")
    if active:
        return str(resolve_project_dir(root, active))
    return str(resolve_scope_root(root))


def _kill_group(process) -> None:
    """SIGTERM the command's process group now and SIGKILL it a second later
    (non-blocking), so commands that ignore SIGTERM still die."""
    if process is None or process.returncode is not None:
        return
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return

    def kill(sig):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    kill(signal.SIGTERM)
    try:
        asyncio.get_running_loop().call_later(1.0, kill, signal.SIGKILL)
    except RuntimeError:
        kill(signal.SIGKILL)


def limits_for(settings: dict) -> list[tuple[int, int]]:
    """(resource, limit) pairs for a command, from the settings (0 = no limit).
    Open files are always capped; the rest are opt-in because some tools
    (Node, Go, Java) reserve far more memory than they use."""
    MB = 1024 * 1024
    out = [(resource.RLIMIT_NOFILE, 4096), (resource.RLIMIT_CORE, 0)]
    def num(key, default):
        try:
            return max(0, int(settings.get(key, default) or 0))
        except (TypeError, ValueError):
            return default
    if num("limit_memory_mb", 0):
        out.append((resource.RLIMIT_AS, num("limit_memory_mb", 0) * MB))
    if num("limit_file_mb", 1024):
        out.append((resource.RLIMIT_FSIZE, num("limit_file_mb", 1024) * MB))
    if num("limit_processes", 0):
        out.append((resource.RLIMIT_NPROC, num("limit_processes", 0)))
    return out


def _child_setup(limits: list[tuple[int, int]]):
    """Runs in the child before the command: own process group, then limits
    (only ever lowered, never above what the server itself may use)."""
    def setup():
        os.setsid()
        for res, value in limits:
            try:
                soft, hard = resource.getrlimit(res)
                cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(res, (cap, cap))
            except (ValueError, OSError):
                pass
    return setup


# The tool is called execute_bash and models write bash (process substitution,
# [[ ]], arrays), so don't let /bin/sh (dash on Debian/Ubuntu) run it.
SHELL = shutil.which("bash") or "/bin/sh"


async def run_shell_command(command: str, timeout: int = 30, cwd: str | None = None) -> dict:
    """Execute a bash command asynchronously inside the configured scope.

    With the sandbox on (sandbox.py), the command runs in bubblewrap and can
    only change the working folder; if bubblewrap doesn't work the command is
    refused (fail closed). With it off, cwd is only a starting directory and the
    command can reach anything the server user can.
    """
    try:
        workdir = cwd or get_active_cwd()
    except ScopeError as e:
        return {"stdout": "", "stderr": f"Scope error: {e}", "exit_code": 126}

    settings = get_settings()
    argv = None
    if sandbox.enabled(settings):
        st = sandbox.status()
        if not st["works"]:
            return {"stdout": "", "stderr": "Refused: the sandbox is on but unavailable. " + (st["error"] or ""),
                    "exit_code": 126}
        argv = sandbox.build_argv(command, workdir, SHELL, network=bool(settings.get("sandbox_network", True)),
                                  ro_paths=settings.get("sandbox_ro_paths") or [], bwrap=st["path"])

    process = None
    try:
        # Only allow-listed variables (no keys or tokens) and the configured limits
        common = dict(stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=workdir,
                      env=command_env(settings), preexec_fn=_child_setup(limits_for(settings)))
        if argv:
            spawn = asyncio.ensure_future(asyncio.create_subprocess_exec(*argv, **common))
        else:
            spawn = asyncio.ensure_future(asyncio.create_subprocess_shell(command, executable=SHELL, **common))
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # Stop pressed while the command was starting: it may already run, so let
            # the start finish and stop it (the except below re-raises the cancel)
            try:
                process = await spawn
            except Exception:
                process = None
            raise
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        err = stderr.decode("utf-8", errors="replace")
        if argv and process.returncode and "Read-only file system" in err:
            # Tell the model why, so it doesn't try other paths outside the project
            err += (f"\n[Optimux sandbox: only the working folder ({workdir}) can be changed; the home folder, "
                    "other projects and the rest of the system are read-only. To create a project, use the "
                    "create_project tool; anything else outside this folder is for the user to do.]")
        return {
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": err,
            "exit_code": process.returncode,
        }
    except asyncio.TimeoutError:
        _kill_group(process)
        return {"stdout": "", "stderr": f"Command timed out after {timeout}s.", "exit_code": 124}
    except asyncio.CancelledError:
        # The user pressed Stop: don't leave the command running
        _kill_group(process)
        raise
    except Exception as e:
        return {"stdout": "", "stderr": f"Execution error: {e}", "exit_code": 1}
