"""Per-project settings (v0.8): a project can override some global settings.

Each override replaces the global value for that project only; a project without
one follows the global setting (Dashboard -> Routing, Memory, Execution), so
changing the global value still changes every project that follows it.

Overrides live in config/project_settings.json: {"projects": {name: {key: value}}}
(snapshots and restores carry it with the other settings). `config_manager.
effective_settings(project)` merges them over get_settings(); the code that reads
these keys asks that function, so commands, plans, the scout and the memory
curator follow the project they work in.
"""
import json
import os
import re

from optimux_backend.core import paths

PROJECT_SETTINGS_FILE = paths.PROJECT_SETTINGS_FILE
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class ProjectSettingsError(ValueError):
    pass


def _bool(v):
    if not isinstance(v, bool):
        raise ProjectSettingsError("must be true or false")
    return v


def _int(lo: int, hi: int):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ProjectSettingsError(f"must be a whole number from {lo} to {hi}")
        return v
    return check


def _plan(v):
    from optimux_backend.core.config_manager import PLAN_MODES
    if v not in PLAN_MODES:
        raise ProjectSettingsError("must be off, ask or auto")
    return v


def _model(field: str):
    """A configured model (v0.8, routing per project); the Conductor may also be "off"."""
    def check(v):
        from optimux_backend.core.config_manager import get_all_models
        if field == "conductor_model" and v == "off":
            return v
        if not isinstance(v, str) or v not in get_all_models():
            raise ProjectSettingsError("must be a model configured in Dashboard → Models")
        return v
    return check


def _checks() -> dict:
    from optimux_backend.core.config_manager import COMMAND_TIMEOUT_RANGE
    return {
        "plan_mode": _plan,
        "scout": _bool,
        "memory_curator": _bool,
        "curator_idle_minutes": _int(1, 240),
        "max_steps_simple": _int(1, 100),
        "max_steps_complex": _int(1, 100),
        "command_timeout": _int(*COMMAND_TIMEOUT_RANGE),
        "sandbox_network": _bool,
        **{k: _model(k) for k in ROUTING_KEYS},
    }


# v0.8: the project's own models; they replace the global Routing for this project
ROUTING_KEYS = ("conductor_model", "simple_model", "complex_model")
KEYS = ("plan_mode", "scout", "memory_curator", "curator_idle_minutes", "max_steps_simple", "max_steps_complex",
        "command_timeout", "sandbox_network") + ROUTING_KEYS


def _read() -> dict:
    try:
        with open(PROJECT_SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    os.makedirs(os.path.dirname(PROJECT_SETTINGS_FILE), exist_ok=True)
    tmp = PROJECT_SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, PROJECT_SETTINGS_FILE)


def overrides(project: str) -> dict:
    """The project's own values (only the valid ones; a bad value in the file is ignored)."""
    if not project:
        return {}
    own = ((_read().get("projects") or {}).get(project) or {})
    checks, out = _checks(), {}
    for k, v in own.items():
        if k in checks:
            try:
                out[k] = checks[k](v)
            except ProjectSettingsError:
                pass
    return out


def routing_overrides(project: str) -> dict:
    """The project's own models (only models that still exist)."""
    return {k: v for k, v in overrides(project).items() if k in ROUTING_KEYS}


def stale(project: str) -> dict:
    """Models the project chose that aren't configured any more: {key: model}. Such a
    choice is ignored (the global model applies) until it is changed or the model is back."""
    if not project:
        return {}
    own = ((_read().get("projects") or {}).get(project) or {})
    valid = overrides(project)
    return {k: v for k, v in own.items() if k in ROUTING_KEYS and k not in valid}


def projects_with_models() -> dict:
    """{project: {key: model}} for every project with models of its own (the Routing page lists them)."""
    out = {}
    for name in sorted((_read().get("projects") or {})):
        own = routing_overrides(name)
        if own:
            out[name] = own
    return out


def rename_model(old: str, new: str) -> list[str]:
    """A model's ID changed (Models → Edit): the projects that chose it follow (v0.8).
    Returns those projects' names."""
    data = _read()
    projects = data.get("projects") or {}
    moved = []
    for name, own in projects.items():
        hit = False
        for k in ROUTING_KEYS:
            if isinstance(own, dict) and own.get(k) == old:
                own[k] = new
                hit = True
        if hit:
            moved.append(name)
    if moved:
        data["projects"] = projects
        _write(data)
    return sorted(moved)


def check_name(project: str) -> str:
    if not isinstance(project, str) or not NAME_RE.match(project) or project in (".", ".."):
        raise ProjectSettingsError("Invalid project name.")
    return project


def update(project: str, changes: dict) -> dict:
    """Set (a value) or remove (None: follow the global setting again) overrides.
    Every change is checked first; nothing is saved when one is wrong."""
    check_name(project)
    checks = _checks()
    clean = {}
    for k, v in (changes or {}).items():
        if k not in checks:
            raise ProjectSettingsError(f"{k} can't be set per project.")
        try:
            clean[k] = None if v is None else checks[k](v)
        except ProjectSettingsError as e:
            raise ProjectSettingsError(f"{k} {e}.")
    data = _read()
    projects = dict(data.get("projects") or {})
    own = dict(projects.get(project) or {})
    for k, v in clean.items():
        if v is None:
            own.pop(k, None)
        else:
            own[k] = v
    if own:
        projects[project] = own
    else:
        projects.pop(project, None)
    data["projects"] = projects
    _write(data)
    return overrides(project)
