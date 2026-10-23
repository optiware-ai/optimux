# sessions.py Agent Details

## File Overview
`sessions.py` stores chat sessions in the SQLite store, so they survive restarts and can be reopened from History exactly as they were and continued with full context.

## Core Functionality
- **Model state:** `history` (JSON), `tier`, `tier_pinned`, `last_turn_ok` and a `turns` count, saved after every request by `save_state()`.
- **Event log (`session_events`):** everything the UI was sent for the session, plus `prompt` and `escalate` markers, in order. Reopening replays these events through the same UI code as a live chat.
- **`Recorder`:** collects a request's events (attached to the WebSocket, so every `send()` is seen). It merges consecutive `token` and `thinking` chunks and skips transient messages (`done`, `approval_request`, `chat_reset`, `session_loaded`, `session_started`, `stopped`). `flush(sid)` stores the events; with no session they are dropped.
- **Functions:**
  - `create(project, first_prompt)`: the title is the first prompt, up to 80 characters.
  - `exists`, `append_events`, `save_state`.
  - `list_sessions(project=None, archived=False, query="")`: newest first, with `cost` and `calls_without_cost` summed from the usage rows whose `session` is the session id.
  - `get(sid)`: summary plus `history`, state and `events`.
  - `latest(project)`: the newest non-archived session.
  - `project_counts()`: non-archived chats and last update per project (Projects section).
  - `rename`, `set_archived`.
  - `delete(sid)`: permanent; removes the session and its events. Usage rows are kept, so cost data is never lost.

## Links & Dependencies
- Uses `store.py` (migration 2: `sessions` and `session_events`, plus an index on `usage.session`).
- Used by `main.py`: recording in `send()` and `run_job`, `ensure_chat`, `save_chat`, `handle_open_session`, and the `/api/sessions` endpoints.
- Feature 25: `project_created` is in `Recorder.SKIP` (it's a toast for the page that's watching, not part of the chat).
- **Saved keys (v0.5.1):** events and history are stored through `_stored()` (masked JSON); `get()` and `list_sessions()` mask titles, history and events on the way out (`security.mask_obj`). `scrub_saved_keys(force)` rewrites stored records once (session events, history and titles, plan JSON and goals, memory history before/after/summary), only values that hold a key; it records `security.secrets_fingerprint()` in `meta` (`keys_scrubbed`) and runs again only when the saved keys change. main.py calls it at startup after the daily backup.
