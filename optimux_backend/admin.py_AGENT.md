# admin.py Agent Details

## File Overview
`python -m optimux_backend.admin <command>` (Feature 24): setup from the command line, used by `install.sh` and the `optimux` command. It calls the same functions as the Dashboard (config_manager, auth, sandbox, security), so validation is identical. Uses `OPTIMUX_HOME` like the server.

## Commands
- `init`: `paths.prepare_home()` (folder 700, secrets 600), the access token (not printed), memory files, starter skills, database. Safe to repeat.
- `token`: print the access token.
- `set-password` (stdin; ≥ 12 chars, then every session ends) / `reset-password` (deletes auth.json, ends sessions).
- `set-key ENV_KEY` (stdin) → `save_api_key_to_env`; `add-model ID NAME [ENV_KEY]` → `save_model`; `set-routing CONDUCTOR SIMPLE COMPLEX` → `save_routing_config` (models must exist).
- `set-workspace PATH [--create]`, `sandbox on|off`: change one setting, keeping the others (`_update_settings` re-validates through `save_settings`).
- `set-github` (stdin; Feature 25): `workspace.connect_github` (checked with GitHub first) → JSON `{login, warning, hint, identity_set}`. `github-status`: `workspace.github_status()` as JSON. `remove-github`: forget the token.
- `sandbox-check`: `sandbox.status(refresh=True)` as JSON. `test-model ID`: one tiny LiteLLM call (max 5 tokens). `status`: what's configured, as JSON, never secrets.

## Rules
- Secrets only on stdin (never argv, so not in `ps` or shell history). Exit 1 with the reason on a refusal, 2 on bad usage.
- Tests: `tests/test_admin.py` (also checks that the models install.sh offers are routable).
