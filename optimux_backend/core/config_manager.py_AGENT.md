# config_manager.py Agent Details

## File Overview
`config_manager.py` stores Optimux configuration: models, routing roles, app settings, and API keys in `.env`.

## Core Functionality
- **Keys file:** `save_api_key_to_env` and `delete_api_key_from_env` write `secrets.env` through `paths.write_private` (mode 600).
- **Environment:** `load_dotenv(paths.SECRETS_FILE, override=True)`, so `~/.optimux/secrets.env` (which the UI manages) wins over variables already exported in the shell. A repo `.env` is read only if the move to `secrets.env` hasn't happened yet.
- **Files:** `MODELS_FILE`, `ROUTING_FILE` and `SETTINGS_FILE` live in `~/.optimux/config/` (`paths.py`).
- **Models:** `LEGACY_SUPPORTED_MODELS` seeds `.optimux/models.json` on first run only (Gemini 3.6 Flash, Claude Haiku 4.5, Claude Sonnet 5.5). `get_all_models`, `save_model` and `delete_model` manage it. Models can carry optional prices in USD per million tokens (`input_cost_per_mtok`, `output_cost_per_mtok`, `cached_input_cost_per_mtok`). `usage.py` uses these in place of LiteLLM's price list, and negative prices are rejected. `get_models_config` splits models into `available` (key present in `os.environ`, or none needed) and `requires_setup`. Each entry carries `problem` (from `model_id_problem`), `price_source`, the price fields and `limits` (the key's rate limits learned from earlier calls: `ratelimits.get`).
- **Thinking:** each model has `thinking`: `default` (send nothing, so the provider decides: Claude doesn't think, Gemini 3 thinks but hides it), `minimal`, `low`, `medium` or `high` (passed to LiteLLM as `reasoning_effort`). Related helpers:
  - `thinking_for` and `thinking_kwargs` read the level.
  - `set_thinking` changes it (validated).
  - `save_model` keeps the current level unless one is given.
  - `supports_thinking` returns True, False or None (unknown to LiteLLM) and is reported in `get_models_config`, together with `thinking`.
- **Price status (`price_source`):** `custom` (prices set in the UI), `litellm` (LiteLLM's price list has the model), `local` (Ollama: no per-token cost), `zero` (LiteLLM lists it at $0 in and out: a free model, or one with no price known) or `unknown`.
- **Validation:** `model_id_problem(id)` uses `litellm.get_llm_provider` to reject IDs LiteLLM can't route (for example a missing provider prefix); `save_model` raises `ValidationError` on it. `save_routing_config` rejects models that aren't configured (`conductor_model` may be `"off"`).
- **Secret names (`get_secret_env_keys`):** Returns every provider env-key name from the legacy and stored models. `executor.py` removes these from the shell environment.
- **Routing:** `get_routing_config` / `save_routing_config` persist `conductor_model` (a model ID, or `"off"` to skip the Conductor), `simple_model` and `complex_model` in `.optimux/routing.json`. The retired `tool_model` key is ignored if present.
- **Settings:** `get_settings` / `save_settings` persist `projects_root`, `active_project`, `auto_approve`, `max_steps_simple` (default 8) and `max_steps_complex` (default 25) in `.optimux/settings.json`. Step limits must be between 1 and 100; `max_steps_for(tier)` reads them, clamped, with `override` using the complex limit. `save_settings` validates the root and project through `security.py` and raises `ScopeError` on bad values.
- **API keys:** `save_api_key_to_env` and `delete_api_key_from_env` update `secrets.env` (kept at mode 0600) and `os.environ`. Values are stripped of whitespace and must not be empty. Key formats are not checked, because providers change them; the Models tab's Test is the real check.
- **Per-model keys:** `api_key_for(model_id)` returns the value of the model's own `env_key` variable, so any variable name (for example `CLAUDE_API_KEY`) works. `litellm_key_kwargs(model_id)` turns that into `{"api_key": ...}` for LiteLLM calls, or `{}` when the model has no env key or it is unset; LiteLLM then uses its default variable for the provider (such as `ANTHROPIC_API_KEY`). Key names and values are validated by `security.validate_env_key` / `validate_env_value` (`ValidationError` on failure). `save_model` validates its `env_key` the same way.

## Data Relations
- Files: `.env` and `.optimux/{models,routing,settings}.json`. These paths are currently repo-relative; moving them to `~/.optimux` is a planned change.
- Pydantic payloads: `ApiKeyPayload`, `ModelPayload`, `RoutingPayload`, `SettingsPayload`.

## Links & Dependencies
- Uses `security.py` for validation.
- Called by `main.py` (REST endpoints), `executor.py` (settings, secret names), `memory_manager.py` (settings) and `llm_client.py` (routing).

- **Memory curator settings:** `memory_curator` (bool, default true) and `curator_idle_minutes` (1 to 240, default 10) in `SettingsPayload`; out-of-range values raise `ValidationError`.
- **Cost confirmations (Feature 18):** `confirm_update_memory` and `confirm_escalate` (bool, default true): ask, with an estimate, before Update memory now and Escalate.
- **Skills mode (Feature 19):** `skills_mode` (off, manual or auto, default auto; anything else raises `ValidationError`). Auto lists only skills whose own mode is auto.
- **Sandbox:** `sandbox` (default false), `sandbox_network` (default true), `sandbox_ro_paths` (list; each normalized by `sandbox.check_ro_path`, which refuses relative, missing, too broad or Optimux-exposing paths). Turning `sandbox` on is refused with `ValidationError` when bubblewrap doesn't work (`sandbox.status(refresh=True)`).
- **Saving JSON:** uses Pydantic v2's `model_dump()`.

## Feature 23
- New settings: `command_env` (extra variable names for commands, validated by `check_command_env`, which refuses `OPTIMUX_*` and `CODEMUX_*` (Optimux's old name) variables, so they never reach a command), `limit_memory_mb` (0, or ≥ 256), `limit_file_mb` (1024), `limit_processes` (0, or ≥ 32).
- `sandbox_network` is `None` by default = automatic: `get_settings()` resolves it to on locally and off in server mode (`OPTIMUX_SERVER=1`). In server mode `get_settings()` also forces `auto_approve` off and `save_settings` refuses to turn it on.
- `command_env(settings, base)` builds the environment for model-run commands (see executor.py_AGENT.md), plus `git_identity_env(settings)`.
- Feature 25: `git_name`/`git_email` (validated by `check_git_identity`: both or neither, no control characters or `<>`, a plausible email) and `github_login` (display only) in `SettingsPayload`; `git_identity_env` turns them into GIT_AUTHOR_*/GIT_COMMITTER_* (empty = git's own config). `update_settings(**changes)` changes some settings and keeps the rest (used by admin.py). `GITHUB_FIELDS` are written only by `set_github_fields(**fields)` (validates just those; an unrelated invalid setting doesn't block them); `POST /api/settings` keeps their stored values, so a page with stale values can't undo them. The email and name refuse control characters (a NUL would break every subprocess).
- v0.3: `snapshot_limit_gb` (default 5, 0.1 to 10000) in `SettingsPayload`: the most space snapshots may use (`snapshots.limit_bytes`). Out-of-range values raise `ValidationError`.
- v0.3 C: `snapshot_recipient` (default empty): a public key remembered for encrypted snapshot downloads, validated with `exports.parse_recipient` (a secret key is refused).
- v0.3 C: a new `projects_root` (only when it changes) is refused when it is the data folder or inside its private parts (`snapshots.is_private_home_path`): those hold keys and chats and must never look like projects.
- v0.3: `online_catalog` (default false): whether the Models screen's Refresh may contact providers (with the user's key) and GitHub (LiteLLM's price list). Even when on, nothing happens without a click.
