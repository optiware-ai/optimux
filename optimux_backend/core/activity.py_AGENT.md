# activity.py Agent Details

## File Overview
`optimux_backend/core/activity.py` (v0.8) estimates, for one model call, how much of it is reading the project's files and how much is writing them. Pure functions; `main.call_model` calls `shares` and passes the result to `meter` → `usage.record(parts=)`, stored in the usage columns `read_tokens` and `write_tokens` (store migration 13; NULL for calls from before).

## Core Functionality
- `is_read_command(command)`: every part (split on `&&`, `||`, `;`, `|`, newlines) is in `READ_COMMANDS` (cat, head, tail, grep, rg, ls, find, wc…), `sed -n` without `-i`, or a `git` read (`GIT_READS`: diff, status, log, show…); no redirect to a file; `cd` alone doesn't count.
- `chars(message)`: text plus tool-call arguments.
- `shares(messages, reply_text, reply_calls, tokens)` → `{read_tokens, write_tokens}`: `prompt_tokens` × (chars of the tool results of read commands ÷ chars of all messages); (`completion_tokens` − `reasoning_tokens`) × (chars of `write_file` / `edit_file` arguments ÷ chars of the reply and its tool calls). Zeros when nothing was read or written.

## Notes
- An estimate by characters: a file read once counts again in every later call that still carries it (that is its real cost).
- `analytics.savings_of` prices them (`by_activity`): reads at their share of the call's input cost (cached tokens included), writes as output tokens, each also on the reference model. A call whose reference was zeroed has reference 0 here too.
- Only executor calls (`call_model`) are measured; Conductor, Scout, planner and jobs have NULL.
- Tests: `tests/test_activity.py`.
