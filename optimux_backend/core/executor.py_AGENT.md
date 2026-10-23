# executor.py Agent Details

## File Overview
`executor.py` runs shell commands issued by the LLM through the `execute_bash` tool.

## Core Functionality
- **Working directory (`get_active_cwd`):** Uses the active project folder, or the Workspace folder if no project is selected, both validated by `security.py`. There is no fallback to the server's cwd: on a `ScopeError`, `run_shell_command` returns exit code 126 with the reason in stderr.
- **Async execution (`run_shell_command`):** Wraps `asyncio.create_subprocess_shell` in its own process group, run by `bash` (`SHELL`, falling back to `/bin/sh` only if bash is missing), because the tool is `execute_bash` and models write bash syntax such as process substitution. Accepts an optional `cwd` override (used by tests).
- **Environment:** Commands receive `scrubbed_env(get_secret_env_keys())`, with LLM provider keys and `OPTIMUX_TOKEN` removed. Other variables, such as `GH_TOKEN`, are kept.
- **Timeouts and Stop:** Default timeout 30s; on timeout it kills the process group and returns exit code 124. If the awaiting task is cancelled (the user pressed Stop), it kills the process group and re-raises; the start itself is shielded, so a Stop that arrives while the command is still starting waits for the start to finish and then kills it (otherwise the command kept running). `_kill_group` sends SIGTERM to the group, then SIGKILL one second later without blocking the event loop.
- **Sandbox:** when `sandbox.enabled(settings)` (the `sandbox` setting, or `OPTIMUX_REQUIRE_SANDBOX`), the command runs through `create_subprocess_exec` with `sandbox.build_argv(...)` (bubblewrap; network and extra read-only folders from the settings). If `sandbox.status()` says bubblewrap doesn't work, the command is refused with exit 126 ("Refused: the sandbox is on but unavailable"), never run unprotected.
- **Without the sandbox:** cwd is a starting point, not isolation; commands can reach any path the server user can.

## Data Relations
- Input is a raw command string. Output is `{stdout, stderr, exit_code}`.

## Links & Dependencies
- `config_manager.get_settings`, `config_manager.get_secret_env_keys`, `security.*`, `sandbox.py`.
- Called by `main.py` after the user approves the command.

## Feature 23: environment and limits
- Commands get `config_manager.command_env(settings)`: an allow-list (`BASE_COMMAND_ENV`: PATH, HOME, USER, LOGNAME, SHELL, LANG, LANGUAGE, TERM, TZ, COLORTERM, NO_COLOR, plus `LC_*`) and the names in the `command_env` setting. Keys from `secrets.env`, `*_API_KEY`, `OPTIMUX_*` and loader variables (`LD_PRELOAD`…) are refused there. This replaced `security.scrubbed_env` (a deny-list), which `workspace.py` still uses for its server-side git/gh commands.
- `limits_for(settings)` → rlimits applied in the child (`_child_setup`, after `setsid`): open files 4096 and no core dumps always; `limit_memory_mb` (RLIMIT_AS), `limit_file_mb` (RLIMIT_FSIZE, default 1024) and `limit_processes` (RLIMIT_NPROC, per user!) when non-zero. Limits are only lowered, never above the server's own hard limit.
- **Output cap (v0.5.1):** stdout and stderr are read in 64 KB chunks into `_Capped` buffers that keep only the first and last bytes (`OUTPUT_CAPS`: stdout 8,000 + 16,000, stderr 2,000 + 4,000) and count the total, so memory stays bounded whatever a command prints. When something was dropped, `text()` puts a note with the counts and the advice to use grep/head/tail/sed in the middle, and the result gets `cut` (bytes dropped, stdout + stderr). main.py sends `cut` with `command_output` (the chat shows an "output cut" label) and the whole result goes to the model as the tool message. On a timeout the result keeps what was printed until then, with the timeout message at the end of stderr. When something is dropped, `_parts()` moves the cut to line ends (the start ends after its last whole line, the end begins with its first whole line) unless that would drop more than half of a part (one very long line); `cut` counts exactly what is missing. Bytes are cut, so a split UTF-8 character (inside a very long line) becomes U+FFFD.
- **Saved keys (v0.5.1, IM-02):** `_Capped.text()` passes the kept text through `security.mask_secrets` (each side of a cut with `edges=True`) and counts in `hidden`; the result gets `hidden` when > 0. main.py sends it with `command_output` and the chat shows "🔒 key hidden".
