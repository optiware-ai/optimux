import asyncio
import os
import shutil
import signal
import time

from optimux_backend.core import sandbox
import resource

from optimux_backend.core.config_manager import command_env, command_timeout_for, get_settings
from optimux_backend.core.security import ScopeError, mask_secrets, resolve_project_dir, resolve_scope_root


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

# Output kept from a command (v0.5.1). The tool result goes back to the model at
# every following step of the turn, so one huge output (a log, a minified file)
# could cost more than the rest of the task and overflow the context. The start
# and the end are kept (errors and summaries are usually at the end); the middle
# is replaced by a note. Bytes beyond the cap are read and dropped, never held.
OUTPUT_CAPS = {"stdout": (8_000, 16_000), "stderr": (2_000, 4_000)}   # (head, tail) in bytes


class _Capped:
    """Collects one output stream: the first `head` bytes, the last `tail`
    bytes and the total count, in bounded memory."""

    def __init__(self, head: int, tail: int):
        self.head_n, self.tail_n = head, tail
        self.head, self.tail, self.total = bytearray(), bytearray(), 0
        self.hidden = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self.head_n - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail += chunk
            if len(self.tail) > self.tail_n:
                del self.tail[:len(self.tail) - self.tail_n]

    def _parts(self) -> tuple[bytes, bytes]:
        """The kept start and end. When something was dropped, the cut falls on
        line ends: the start ends after its last whole line and the end begins
        with its first whole line, unless that would drop more than half of the
        part (one very long line, e.g. a minified file)."""
        head, tail = bytes(self.head), bytes(self.tail)
        if self.total <= len(head) + len(tail):
            return head, tail
        i = head.rfind(b"\n")
        if i >= len(head) // 2:
            head = head[:i + 1]
        j = tail.find(b"\n")
        if 0 <= j < len(tail) // 2:
            tail = tail[j + 1:]
        return head, tail

    @property
    def cut(self) -> int:
        head, tail = self._parts()
        return self.total - len(head) - len(tail)

    def text(self) -> str:
        """The kept output with saved keys hidden (security.mask_secrets); how
        many were hidden is in self.hidden. Each side of a cut is checked for a
        piece of a key too, since the cut can fall inside one."""
        head, tail = self._parts()
        cut = self.total - len(head) - len(tail)
        if not cut:
            out, self.hidden = mask_secrets((head + tail).decode("utf-8", errors="replace"))
            return out
        start, n1 = mask_secrets(head.decode("utf-8", errors="replace").rstrip("\n"), edges=True)
        end, n2 = mask_secrets(tail.decode("utf-8", errors="replace"), edges=True)
        self.hidden = n1 + n2
        return (start + f"\n\n[... {cut:,} bytes of output cut here by Optimux ({self.total:,} in all). "
                        "To see a part, run the command again with grep, head, tail or sed -n. ...]\n\n" + end)


def _stopped(why: str, ran: float, limit: int, named: bool, out_buf: _Capped, err_buf: _Capped) -> dict:
    """The result of a command stopped at its time limit, with what it printed
    until then (it helps to see where it hung) and why, for the model."""
    if why == "user":
        note = (f"Stopped by the user after {_minutes(ran)}: the command reached its time limit ({limit}s) and the "
                "user chose to stop it. Don't run it again unless the user asks you to.")
    elif why == "no_answer":
        note = (f"Stopped after {_minutes(ran)}: the command reached its time limit ({limit}s) and nobody answered "
                "whether to keep waiting. If it needs longer, ask the user to raise the limit (Dashboard → "
                "Execution → Timeout, or the time limit in the approval box); don't retry the same command.")
    else:
        note = f"Command timed out after {limit}s" + (
            " (the limit set in Dashboard → Execution → Command timeout). If it needs longer, ask the user to "
            "raise that limit; don't retry the same command." if named else ".")
    result = {"stdout": out_buf.text(), "stderr": (err_buf.text() + "\n" if err_buf.total else "") + note,
              "exit_code": 124, "stopped": why}
    return _extras(result, out_buf, err_buf)


def _extras(result: dict, out_buf: _Capped, err_buf: _Capped) -> dict:
    """Add "cut" (bytes dropped) and "hidden" (saved keys hidden) when there are any."""
    if out_buf.cut or err_buf.cut:
        result["cut"] = out_buf.cut + err_buf.cut
    if out_buf.hidden or err_buf.hidden:
        result["hidden"] = out_buf.hidden + err_buf.hidden
    return result


async def _drain(stream, sink: _Capped) -> None:
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        sink.add(chunk)


LIMIT_CHOICES = ("more", "until_end", "stop")


async def _watch(task: asyncio.Future, limit: int, on_limit) -> tuple[str | None, float]:
    """Wait for the command (task). Returns (why it must be stopped, seconds run):
    None when it ended by itself; "limit" at the time limit without on_limit;
    with on_limit (IM-03b), the command keeps running while on_limit(seconds)
    asks the user: "more" waits one more limit, "until_end" waits without
    limit, "stop" -> "user"; no answer within one more limit -> "no_answer"."""
    started = time.monotonic()
    if not limit:
        await task
        return None, time.monotonic() - started
    done, _ = await asyncio.wait({task}, timeout=limit)
    while not done:
        if on_limit is None:
            return "limit", time.monotonic() - started
        answer = asyncio.ensure_future(on_limit(time.monotonic() - started))
        try:
            done, _ = await asyncio.wait({task, answer}, timeout=limit, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not answer.done():
                answer.cancel()             # it ended, or nobody answered: the question goes away
                await asyncio.gather(answer, return_exceptions=True)   # ...before anything else is shown
        if task in done:
            break
        if answer not in done:
            return "no_answer", time.monotonic() - started
        choice = answer.result()
        if choice == "stop":
            return "user", time.monotonic() - started
        if choice == "until_end":
            await task
            break
        done, _ = await asyncio.wait({task}, timeout=limit)
    return None, time.monotonic() - started


def _minutes(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds}s" if seconds < 120 else f"{seconds // 60} min {seconds % 60:02d}s"


async def run_shell_command(command: str, timeout: int | None = None, cwd: str | None = None,
                            on_limit=None) -> dict:
    """Execute a bash command asynchronously inside the configured scope.

    timeout: seconds, 0 = no limit, None = the setting (Dashboard -> Execution).
    on_limit(seconds) -> "more" | "until_end" | "stop": asked at the limit while
    the command keeps running (IM-03b); without it, the command is stopped there.

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
    # The setting (Dashboard -> Execution) unless a caller sets its own
    named = timeout is None
    timeout = command_timeout_for(settings) if named else timeout
    argv = None
    if sandbox.enabled(settings):
        st = sandbox.status()
        if not st["works"]:
            return {"stdout": "", "stderr": "Refused: the sandbox is on but unavailable. " + (st["error"] or ""),
                    "exit_code": 126}
        argv = sandbox.build_argv(command, workdir, SHELL, network=bool(settings.get("sandbox_network", True)),
                                  ro_paths=settings.get("sandbox_ro_paths") or [], bwrap=st["path"])

    process = None
    out_buf, err_buf = _Capped(*OUTPUT_CAPS["stdout"]), _Capped(*OUTPUT_CAPS["stderr"])
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
        async def collect():
            await asyncio.gather(_drain(process.stdout, out_buf), _drain(process.stderr, err_buf))
            await process.wait()

        collector = asyncio.ensure_future(collect())
        try:
            why, ran = await _watch(collector, timeout, on_limit)
        except BaseException:
            collector.cancel()
            raise
        if why:
            collector.cancel()
            _kill_group(process)
            return _stopped(why, ran, timeout, named, out_buf, err_buf)
        err = err_buf.text()
        if argv and process.returncode and "Read-only file system" in err:
            # Tell the model why, so it doesn't try other paths outside the project
            err += (f"\n[Optimux sandbox: only the working folder ({workdir}) can be changed; the home folder, "
                    "other projects and the rest of the system are read-only. To create a project, use the "
                    "create_project tool; anything else outside this folder is for the user to do.]")
        result = {"stdout": out_buf.text(), "stderr": err, "exit_code": process.returncode}
        return _extras(result, out_buf, err_buf)
    except asyncio.CancelledError:
        # The user pressed Stop: don't leave the command running
        _kill_group(process)
        raise
    except Exception as e:
        return {"stdout": "", "stderr": f"Execution error: {e}", "exit_code": 1}
