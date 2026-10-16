# usage.py Agent Details

## File Overview
`usage.py` meters token usage and cost for every LLM call (the Conductor and the executors), so Optimux can measure whether its routing actually saves tokens. Usage is stored in the `usage` table of the SQLite store (`store.py`).

## Core Functionality
- **`extract_usage(usage)`:** Normalizes a LiteLLM usage object or dict to `prompt_tokens`, `completion_tokens` and `cached_tokens`. `prompt_tokens` includes cached tokens. `reasoning_tokens` (the thinking share of the output) comes from `completion_tokens_details.reasoning_tokens`. Cached tokens are read from `prompt_tokens_details.cached_tokens` (OpenAI style), `cache_read_input_tokens` (Anthropic style) or a top-level `cached_tokens`.
- **`cost_for(model, tokens)`:** USD cost of one call. Uses the per-model prices from `models.json` (`input_cost_per_mtok`, `output_cost_per_mtok`, optional `cached_input_cost_per_mtok`) if set, otherwise `litellm.cost_per_token` (which applies cache discounts), otherwise 0 for local models (`ollama/`, `ollama_chat/`), otherwise `None` (unknown, never 0).
- **Storage:** `ensure_ready()` creates the schema and runs the import now (called at startup before the backup). `insert(entry)` writes one row. On first use per database, `import_jsonl()` imports the legacy `~/.optimux/usage.jsonl` (entries without a project become `(unknown)`, malformed lines are skipped) and renames it to `usage.jsonl.migrated`, which is kept and never deleted. `read_entries(project=None, since=0)` reads rows back as dicts.
- **`daily(days=30, project=None)`:** calls, tokens and cost per local day and model, for the dashboard charts (days without usage are omitted). **`project_activity()`:** all-time cost, calls and last use per project.
- **`month_totals(project)`:** This calendar month's calls, cost and calls without a cost for one project, as a single SQL aggregate (budgets check it before every model call). `month_start()` is the local start of the month.
- **`recent_average(role, n=5)`:** average cost of the last `n` priced calls with a role (any project), with the count and the latest model; None without priced calls. **`role_totals(role, since)`:** calls and known cost for a role. Both feed `/api/costs/estimates` (Feature 18).
- **`record(session_id, role, model, usage, duration_ms=None, project="")`:** Stores one row with `ts`, `session`, `project` (`(no project)` when none is active), `role` (`conductor`, `simple`, `complex` or `override`), `model`, the token counts, `cost`, `duration_ms` (wall time of the call, including streaming) and an optional `estimated` flag. Never raises.
- **`estimate(model, messages, text)`:** Fallback token count with LiteLLM's tokenizer when a provider returns no usage for a streamed call. Tool-call arguments are not counted. Marked `estimated: true`.
- **`summary(now=None, project=None)`:** Totals for `today` and `this_month` (local time), `last_7_days` and `all_time`, each with `total`, `by_role`, `by_model` and `by_project` (entries written before projects were recorded count as `(unknown)`). With `project`, only that project's entries are counted. Every bucket includes `reasoning_tokens`, `calls_without_cost`, so unknown prices are visible, plus `duration_ms`, `timed_calls` and `avg_duration_ms` (over calls that recorded a duration). Roles also include `test` (Settings key/model tests).

## Data Relations
- The `usage` table (see `store.py`); the old JSON-lines log is only read once, for the import.

## Links & Dependencies
- Called by `main.py` (`meter()` after each Conductor and executor call, and `GET /api/usage`).
- Reads model prices through `config_manager.get_all_models`.
- Uses `litellm.cost_per_token` and `litellm.token_counter`.

- **Plans (v0.4):** `record(..., plan_id=None, step=None)` ties a call to a plan and its step (`COLUMNS` includes both; empty outside plans).
- **Skills:** `record(..., skill=None)` stores the active skill. `summary()` adds `by_skill` (calls made while using a skill) and `skill_drafts` ("Save as skill" drafting calls, role `skill_draft`), so creating and using a skill are counted separately.
- **Memory updates:** `summary()` also adds `memory_updates`, the curator's calls (role `curator`) per project, so the cost of automatic memory updates is visible on its own; they are still counted under `by_role` too.
