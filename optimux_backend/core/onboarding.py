"""Getting started (v0.6.1): the first-run checklist.

Each step is ticked by looking at the real settings, never by a click, except
"Choose your features", which only the user can decide ("These are fine", or
changing memory, plan mode or skills). What the user did with the card itself
(features confirmed, hidden for good, "You're ready" seen) is kept in the
database's meta table, so it follows them to every browser.

An install that already had requests before this feature existed is
"existing": its card stays on Dashboard -> Overview, folded, and never pops up
in the chat. No step costs anything: no model is called.
"""
import json
import os
import platform
import time

from optimux_backend.core import store

META_KEY = "onboarding"
FEATURE_FIELDS = ("memory_curator", "plan_mode", "skills_mode")
ACTIONS = ("features_ok", "dismiss", "reopen", "ready_seen")


class OnboardingError(ValueError):
    pass


def _state() -> dict:
    try:
        raw = store.get_meta(META_KEY)
        return json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}


def _save(state: dict) -> None:
    store.set_meta(META_KEY, json.dumps(state, sort_keys=True))


def _requests_made() -> int:
    """Chat requests ever made (the executors' calls; the Conductor alone isn't a request)."""
    conn = store.connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM usage WHERE role IN ('simple', 'complex', 'override', "
                           "'planner')").fetchone()
        return row["n"] or 0
    finally:
        conn.close()


def state() -> dict:
    """The card's own state; created on first use. An install with earlier
    requests is marked existing then, so updating never pops up a tutorial."""
    s = _state()
    if "started" not in s:
        s = {"started": time.time(), "existing": _requests_made() > 0, "dismissed": False,
             "features_ok": False, "ready_seen": False}
        _save(s)
    return s


def act(action: str) -> dict:
    if action not in ACTIONS:
        raise OnboardingError(f"Unknown action: {action}")
    s = state()
    if action == "features_ok":
        s["features_ok"] = True
    elif action == "dismiss":
        s["dismissed"] = True
    elif action == "reopen":
        s["dismissed"] = False
        s["existing"] = False          # opened on purpose: it may show in the chat too
    elif action == "ready_seen":
        s["ready_seen"] = True
    _save(s)
    return status()


def note_settings_change(before: dict, after: dict) -> None:
    """A change to memory, plan mode or skills counts as choosing the features."""
    if any(before.get(k) != after.get(k) for k in FEATURE_FIELDS):
        s = state()
        if not s.get("features_ok"):
            s["features_ok"] = True
            _save(s)


def _step(id_, title, why, done, target, optional=False, info=""):
    return {"id": id_, "title": title, "why": why, "done": bool(done), "target": target,
            "optional": optional, "info": info}


def status() -> dict:
    """Every step with its state, the next one to do, and whether to show the card."""
    from optimux_backend.core import auth, budgets, sandbox
    from optimux_backend.core.config_manager import get_models_config, get_routing_config, get_settings

    s = state()
    settings = get_settings()
    routing = get_routing_config()
    models = get_models_config()
    root = settings.get("projects_root") or ""
    is_linux = platform.system() == "Linux"
    sb = sandbox.status() if is_linux else {"works": False}
    sandbox_ok = bool(sb.get("works")) and sandbox.enabled(settings)
    conductor = routing.get("conductor_model") or ""
    steps = [
        _step("models", "Add a model and its key",
              "Optimux can't answer without one. Add a cheap fast model and a strong one; Test checks each "
              "for a fraction of a cent.", models["available"], "models"),
        _step("routing", "Choose who does what",
              "The Conductor picks Simple or Complex for each message; most work goes to the cheap Simple "
              "model, so this is where the savings come from.",
              routing.get("simple_model") and routing.get("complex_model") and conductor, "routing"),
        _step("workspace", "Set your Workspace folder",
              "Every folder inside it is a project; commands work there.", root and os.path.isdir(root), "scope"),
        _step("sandbox", "Turn on the sandbox",
              "Commands can then change only the active project, even if you approve a bad one.",
              sandbox_ok, "execution",
              info="" if is_linux else "Not available on this system: bubblewrap is Linux-only. You approve "
                                       "every command instead."),
        _step("features", "Choose your features",
              "Memory updates itself after each chat (a small cost per chat); plan mode splits big tasks into "
              "steps for the cheap model; skills are reusable instructions. The defaults are fine to start.",
              s.get("features_ok"), "memory"),
        _step("project", "Pick or create a project",
              "The chat works in the active project: its files, its memory and its budget.",
              settings.get("active_project"), "projects"),
        _step("first", "Send your first request",
              "Ask anything about the project. Commands and file changes are shown for your approval first.",
              _requests_made() > 0, "chat"),
        _step("budget", "Set a monthly budget", "Optimux warns at 80% and can stop at 100%.",
              budgets.get_budgets(), "budgets", optional=True),
    ]
    if auth.server_mode():
        steps.append(_step("totp", "Turn on two-step codes",
                           "On a server, a code from your phone protects the sign-in even if the password leaks.",
                           auth.totp_enabled(), "system", optional=True))
    if not is_linux:
        steps[3]["done"] = True             # nothing to do there: shown as information
    required = [x for x in steps if not x["optional"]]
    done = sum(1 for x in required if x["done"])
    nxt = next((x["id"] for x in steps if not x["done"] and not x["optional"]), None) or \
        next((x["id"] for x in steps if not x["done"]), None)
    ready = done == len(required)
    return {"steps": steps, "done": done, "total": len(required), "next": nxt, "ready": ready,
            "dismissed": bool(s.get("dismissed")), "existing": bool(s.get("existing")),
            "ready_seen": bool(s.get("ready_seen")),
            # in the chat: a new install, not hidden, and not finished (or finished but "You're ready" not seen)
            "show_in_chat": not s.get("dismissed") and not s.get("existing") and (not ready or not s.get("ready_seen"))}
