# sandbox.py Agent Details

## File Overview
`sandbox.py` confines the commands the model runs (`execute_bash`) with **bubblewrap** (`bwrap`, Linux). It builds the `bwrap` command line, checks that bubblewrap works on this machine, and decides whether the sandbox is on.

## What a sandboxed command sees
- `/` read-only (system tools, compilers, libraries); fresh `/dev` and `/proc`.
- Empty private `/tmp`, `/var/tmp` and `/run` (no Docker socket, no session bus); `/run/systemd/resolve` is put back read-only so DNS keeps working.
- `/home`, the user's home and Optimux's folder (`paths.HOME`: keys, token, database, memory) hidden behind empty tmpfs; an empty home directory is recreated.
- Read-only again on top: `~/.gitconfig`, the global skills folder, and the user's extra read-only folders.
- The working folder (active project, or the Workspace folder) mounted **read-write**. If it contains Optimux's folder, that folder is hidden again after the mount (only `skills/` stays visible, read-only).
- Own PID, IPC, UTS (and cgroup when possible) namespaces, `--die-with-parent`, `--new-session` (no terminal injection); `--unshare-net` when the network is off.

## Core Functionality
- **`build_argv(command, workdir, shell, network, ro_paths, bwrap)`:** the full `bwrap ... bash -c <command>` argv. Mount order matters: hide first, then mount the allowed folders on top.
- **`check_ro_path(path)`:** validates an extra read-only folder (absolute, existing, not `/`, `/home` or the home folder, and not containing Optimux's folder). Raises `SandboxError`.
- **`status(refresh=False)`:** cached `{installed, path, version, works, error, required}`; `works` comes from really running `true` in a sandbox, so disabled user namespaces are detected.
- **`enabled(settings)`:** the `sandbox` setting, or forced by `OPTIMUX_REQUIRE_SANDBOX=1` (`required()`), which the dashboard can't override.

## Failure behaviour
Fail closed: `executor.run_shell_command` refuses commands (exit 126) when the sandbox is on and `status()["works"]` is false; `config_manager.save_settings` refuses to turn it on in that case.

## Not covered
Workspace actions (`workspace.py`: create, archive, GitHub) run fixed git/gh commands from the server after approval, outside the sandbox.

## Links & Dependencies
- `paths.py` (Optimux's folder, skills folder).
- Used by `executor.py` (running commands), `config_manager.py` (validation), `main.py` (`/api/sandbox`, the `sandboxed` flag on approvals).

## Feature 23
- `required()` is also true in server mode (`OPTIMUX_SERVER=1`). The network inside the sandbox defaults to off in server mode (`config_manager.get_settings`).

## fix24b: read-only hidden folders, scratch HOME
- The hidden folders (tmpfs over `/home`, the home folder, Optimux's folder) are remounted read-only at the end of the argv (`--remount-ro`, after every mount inside them), except those inside the working folder (the bind mount covers them; Optimux's folder there is re-hidden and remounted read-only too). Writes outside the working folder fail with EROFS instead of landing in a throwaway tmpfs.
- `HOME` is `/tmp/home` (`SANDBOX_HOME`, inside the private `/tmp`), with `~/.gitconfig` bound read-only there, so caches work and vanish after the command.
- `executor.run_shell_command` appends a one-line explanation to stderr when a sandboxed command fails with "Read-only file system".
