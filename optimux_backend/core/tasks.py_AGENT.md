# tasks.py Agent Details

## File Overview
`tasks.py` keeps the task records (v0.6, IM-09): one row per request in the `tasks` table (store migration 11), for routing that learns from outcomes later. Nothing is sent anywhere.

## Core Functionality
- **A request** is one chat job: a message (`prompt`), the Escalate button (`escalate`) or a plan action (`plan_action`). Proposing a plan and running it are two rows with the same `plan_id`.
- **No text:** a row never holds the message or the reply. The chat keeps them; `session` links the two.
- **In memory while it runs:** `new(kind, project)` builds the dict kept in `session["task"]`; main.py fills it (`add_call` from `meter`, the Conductor's decision in `resolve_tier`, tier/model/skill in `task_route`, `escalate`, outcome in `task_outcome`, steps in `run_turn`, approved/rejected in `wait_for_answer`, `set_changes` from `report_changes`). `finish(task, session_id)` writes it at the end of `_run_job` (main.`finish_task`); a request with no calls and no outcome (a `/simple` command alone, a refused budget) is not written (`worth_keeping`).
- **Columns:** `COLUMNS`. Outcomes: `ok`, `step_limit`, `stuck`, `stopped`, `error`, `provider_error`, `budget_paused`, or `plan_<status>` for a plan request that ended without one of those.
- **Escalate button:** `mark_escalated(previous_task_id, reason)` marks the request being redone.
- **Ratings:** `rate(id, 1 | -1 | 0)` (0 clears; `TaskError` for a bad value or an unknown id); `ratings(session_id)` for a replayed chat.
- **Export:** `export_csv()` (every row, oldest first, `time_utc` added; text starting with `= + - @` is prefixed with `'` so a spreadsheet never runs it). `stats()`, `first_ts()`, `delete_all()`.
- Writes from the chat never raise (a statistics row must not break a chat).

## Links & Dependencies
- Uses `store.py`. Used by `main.py` (hooks above; `GET /api/tasks`, `GET /api/tasks.csv`, `DELETE /api/tasks`, `POST /api/tasks/{id}/rating`; `session_loaded` carries `ratings`; the `task_done` event gives the page the id for 👍 / 👎) and by `analytics.unlinked_escalations`.
- Escalations also zero the stuck model's reference cost: `usage.zero_reference(task_id, role, step)`.
- Tests: `tests/test_tasks.py`.
