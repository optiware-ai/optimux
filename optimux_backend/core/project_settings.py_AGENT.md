# project_settings.py Agent Details

## File Overview
Per-project settings (v0.8): a project's own values for some global settings, set on Dashboard → Project. A project without its own value follows the global setting, so changing the global value still changes it.

## Keys and checks (`KEYS`, `_checks()`)
- `plan_mode` (`config_manager.PLAN_MODES`), `scout` (bool), `memory_curator` (bool), `curator_idle_minutes` (1-240), `max_steps_simple` / `max_steps_complex` (1-100), `command_timeout` (`config_manager.COMMAND_TIMEOUT_RANGE`), `sandbox_network` (bool). Booleans must be real booleans and numbers whole numbers (True is not 1).

## Storage
- `PROJECT_SETTINGS_FILE` = `paths.PROJECT_SETTINGS_FILE` (`config/project_settings.json`, written through a temporary file; snapshots and restores carry it): `{"projects": {name: {key: value}}}`. Tests point it at a temporary file (conftest).
- `overrides(project)`: the project's valid values ({} for the Workspace `""`; a bad value in the file is ignored).
- `update(project, changes)`: `{key: value}` sets, `{key: None}` removes (follow global again). Every change is checked first, so nothing is half-saved; unknown keys and bad project names raise `ProjectSettingsError` (a `ValueError`). A project left with no values is removed from the file. Returns the new `overrides`.
- `check_name(project)`: a valid folder name.

## How they apply
`config_manager.effective_settings(project=None)` (None = the active project) and `with_project_overrides(data, project=None)` merge them over `get_settings()`. Readers: `config_manager.max_steps_for` and `command_timeout_for`, `executor.run_shell_command` (timeout and sandbox network), `main.plan_mode`, `main.scout_first`, `main.curator_enabled(project)` / `curator_idle_seconds(project)` (the chat's project, `main.chat_project` → `sessions.project_of`). Never save a merged dict.

## Links
- `main.py`: `GET` / `POST /api/projects/{name}/settings` (`project_settings_view`: `overrides`, `global`, `effective`, `keys`; errors are 422).
- Tests: `tests/test_project_settings.py`.

## Models per project (v0.8)
- `ROUTING_KEYS` (`conductor_model`, `simple_model`, `complex_model`) are in `KEYS`; `_model(field)` accepts a model in `config_manager.get_all_models()` (the Conductor also `off`). A chosen model removed from Models later fails the check on reading, so it's ignored (the global one applies): `stale(project)` reports such choices.
- `routing_overrides(project)`: only the routing keys; `config_manager.with_project_overrides` leaves them out of the settings, `config_manager.effective_routing(project)` and `main.routing_for` put them over the global Routing. `projects_with_models()`: `{project: {key: model}}` for the Routing page.
- `rename_model(old, new)`: a model's ID changed (Models → Edit): the projects that chose it follow; returns their names.
