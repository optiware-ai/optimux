# plans.py Agent Details

## File Overview
`plans.py` stores the plans of **plan mode** (v0.4, the task manager MVP; design: the "Optimux Task Manager: design" doc). A big task is split into 2–8 steps by the Complex model; each step runs on the tier it names. This module only **parses, checks and stores** plans and their steps' run state. It calls no model and runs nothing (the planner and the step runner come in later commits).

## The plan JSON (from the planner)
`{"goal", "steps": [{"id", "title", "tier": "simple"|"complex", "do", "done_when", "files"?}], "check"?}`.
- `parse(text)`: the JSON object in a model's answer: plain, inside a ``` block, or the outermost `{...}`; `PlanError("The plan is not valid JSON.")` otherwise.
- `validate(raw)`: a clean plan or a `PlanError` whose message can be shown to the planner on its one retry. Rules: 2–8 steps (`MIN_STEPS`, `MAX_STEPS`; more → "merge some"); `tier` simple or complex; `title` ≤ 120, `do` ≤ 600, `done_when` ≤ 300, `goal` ≤ 300, `check` ≤ 600 characters (whitespace collapsed); `files` a list of at most 20 paths; **a plan whose steps are all complex is refused** (nothing to save). Step ids are **renumbered 1..n**: the planner's ids are not trusted.

## Storage
- Table `plans` (store migration 8): `id` (16 URL-safe characters, `ID_RE`), `session`, `project`, `goal`, `plan` (JSON: `steps` with their run state, `check`, `request` (≤ 2000 chars), `note`, `interrupted`), `status`, `created`, `updated`, and empty `user_id` / `team_id` for the team version.
- Plan statuses (`PLAN_STATUSES`): `waiting` (shown, not started) · `running` · `paused` (stopped, stuck, interrupted, budget) · `done` · `cancelled`; `OPEN` = the first three. Step statuses (`STEP_STATUSES`): `pending` · `running` · `done` · `blocked` · `skipped` · `failed`.
- A step's run state (`_new_step_state`): `status`, `result` (the note for later steps, ≤ `MAX_RESULT`), `model`, `cost`, `calls`, `started`, `finished`, `escalated`.
- Every change is saved at once (`_save`), so a stop or a crash leaves the plan at a known step.

## Functions
- `create(session, project, plan, request="")` (validates first; status `waiting`), `get(pid)` (validates the id), `for_session(session)` (oldest first), `open_plan(session)` (the newest waiting / running / paused plan).
- `set_status(pid, status, note=None)` (running clears `interrupted`), `update_step(pid, step_id, **fields)` (only `STEP_FIELDS`; checks statuses and tiers; trims `result`), `add_cost(pid, step_id, cost, model=None)` (one metered call: `calls` + 1, cost added when known).
- `edit_waiting(pid, tiers={id: tier}, remove=[ids])`: the MVP's Edit before Run. Only a `waiting` plan; at least 2 steps and one Simple step must remain; ids renumbered.
- `next_step(plan)`: the first step pending, running (cut) or blocked. `total_cost(plan)`.
- `mark_interrupted()`: called at start by `main.py`; a plan left `running` becomes `paused` with `interrupted: true` and its running step back to `pending`, so Resume continues there.
- **Running (commit 3):** `STEP_FIELDS` include `details` (at most `MAX_DETAILS` = 6000 chars: a design or list of cases later steps must follow). `earlier_work(plan, step_id)` returns the result lines of finished and skipped steps before it and their details, newest first within `MAX_DETAILS_IN_CONTEXT` (12000; the oldest one is cut). `last_words(text, 150)` is the result of a step that ended with plain text. `CHECK_STEP` (0) is the final check's step number: `add_cost(pid, CHECK_STEP, …)` counts into `check_cost` / `check_calls`, which `total_cost` includes. `mark_interrupted` also sets the note "Interrupted by a restart at step N…".
- `summary_for_history(plan)`: the compact record for chat history (goal, each step's tier, model, status and result note, then `Check: …`; never a step's transcript).

## Links & Dependencies
- `store.py` (migration 8: `plans`, and `usage.plan_id` / `usage.step`). `usage.record(..., plan_id=None, step=None)` ties a call to a plan and step.
- Used by `main.py` at start (`mark_interrupted`). Tests: `tests/test_plans.py`.
