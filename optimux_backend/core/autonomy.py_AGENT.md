# autonomy.py Agent Details

## File Overview
Autonomy levels (v0.8, ported from OmniLatch v0.1.0-12): how much models may do without asking, per project. Used by `main.py` before every command (`execute_bash`) and file change (`write_file`/`edit_file`). The server decides; the page no longer answers approvals by itself.

## Levels and limits
- **`LEVELS`, `LEVEL_TEXT`:** `supervised` (everything asks), `checkpointed` (file changes and ordinary commands run; git history and a whole-file rewrite of an existing file ask), `autonomous` (also commits and rewrites).
- **Hard limits (`HARD`)** ask at every level: `publish`, `delete`, `install`, `network`, `system`, `unclear`, `outside`. `REASONS` has the words shown to the user (also `history` and `rewrite`). Budget limits are enforced in budgets.py; creating or archiving projects always asks (workspace tools).

## Storage
- `AUTONOMY_FILE` = `paths.AUTONOMY_FILE` (`config/autonomy.json`, mode 600, so snapshots and restores carry it with the other settings): `{"default": level, "projects": {name: level}}`. Tests point it at a temporary file (conftest).
- `default_level()`: the saved default, else the old `auto_approve` setting (on → checkpointed, off → supervised); `supervised` while `server_block()` says why not.
- `project_level(name)`: the project's own level or the default; always `supervised` with no project (the Workspace) and while `server_block()` is non-empty.
- `set_level(project|None, level|None)`: None project = the default; None level = the project follows the default again. Refuses unknown levels, project names that aren't valid folder names, and anything but `supervised` while `server_block()` is non-empty. Returns `overview()`.
- `overview()`: `default` (the saved or migrated one, even while blocked), `default_saved`, `projects`, `blocked`, `levels`, `hard_limits`.

## Servers
- `server_block()`: "" unless `auth.server_mode()`. On a server the levels apply only while the sandbox is on (`sandbox.enabled`) and works (`sandbox.status()["works"]`, cached); otherwise it returns why, and every project reads as supervised.

## Reading a command (`classify`)
Here-documents are dropped (`security.strip_heredocs`); `$(…)` and backticks are classified on their own (depth ≤ 3); the rest is split into segments at `; && || | & ( )` and newlines (shlex). Per segment (`_segment_kinds`): leading `VAR=x` and wrappers (`env`, `nohup`, `time`, `nice`, `command`, `exec`, `timeout N`, `xargs`, `watch`) are skipped; `sudo`/`doas`/`su`/`pkexec` add `system`; `eval` or a command name from a variable (`$CMD`) is `unclear`; `bash|sh|… -c '…'` is classified inside; then by name: `DELETE`, `find -delete/-exec`, `NETWORK`, `INSTALL`, `INSTALL_SUB` (pip/npm/yarn/pnpm/bun/cargo/go/uv/poetry subcommands), `python -m pip install`, `SYSTEM`, `gh` (publish + network), `git` (`_git_kinds`: push and lfs push publish; rm, clean, reset --hard, checkout/restore of files, stash drop/clear delete; commit/merge/rebase/cherry-pick/revert/tag/am/pull and branch -d are history; clone/fetch/pull/ls-remote/remote/submodule update are network).

## `decide(level, kind, command="", outside=None)`
`(ask, why)` for `kind` `"command"`, `"file"` or `"rewrite"` (write_file over an existing file). Any hard kind (or `outside` paths) → ask, "Always asked: …". Supervised → ask, "Supervised: …". Checkpointed and `history` → ask; Checkpointed and `rewrite` → ask. Otherwise run, `(False, "")`. File changes have no hard kinds (they are inside the project by construction).

## Limits of the check
It reads the command's words before it runs; scripts and programs the model starts can do whatever their code does. The bubblewrap sandbox is what limits that. The Dashboard and the Guide say so.

## Links
- `main.py`: `execute_bash`, `run_file_tool`, `raise_level_for`/`raise_project_level`, `approval_check`, `/api/autonomy`.
- Tests: `tests/test_autonomy.py`.
