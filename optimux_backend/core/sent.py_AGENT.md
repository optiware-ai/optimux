# optimux_backend/core/sent.py

What was sent to the models (v0.8): every model call's prompt, kept as it left the machine, so the user can read it under a reply ("What was sent"). Tests: `tests/test_sent.py`.

- **Storage (store.py migration 14):** `sent_parts(hash, text, chars)` holds each piece of text once (key: the first 32 hex of its sha256); `sent_calls(id, ts, session, task_id, project, role, model, parts, tools, chars)` holds one row per call, `parts` being a JSON list of `[kind, hash]` in the order sent and `tools` the offered tools' names. A conversation is sent again in full with every call, so storing by part keeps the size close to that of the chat itself.
- **`split(messages, tools, memory)`:** `(kind, text)` per part: `instructions` (the system text), `memory` (the user's memory, cut out of the system text when `memory` is found in it; a memory holding a hidden key is not found and stays inside `instructions`), `user`, `assistant`, `call` (a tool call: its name, then its arguments as indented JSON), `result` (a tool's answer), `tools` (the tool definitions as JSON, last). Content that is a list of blocks is joined; empty parts are left out.
- **`record(session_id, role, model, messages, tools, project, memory, task_id, keep_days)`:** stores a call and returns its id, or `None` on any failure: it must never be in the way of a model call. The caller passes what actually goes out (`prepare_messages`, so saved keys are already `••••(NAME)`). Every `PRUNE_EVERY` (6 h) it also runs `prune(keep_days)`.
- **`get(id)`:** the call with its parts' text, or `None`.
- **`prune(days)`:** deletes the calls older than `days` and the parts no call refers to any more.
- `KEEP_DAYS` (14) is the default of the `keep_sent_days` setting (`config_manager`, 0 to 365; 0 keeps nothing).
