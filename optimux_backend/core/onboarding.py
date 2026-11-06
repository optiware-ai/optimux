"""Getting started (v0.6.1): the first-run checklist.

Each step is ticked by looking at the real settings, never by a click, except
"Choose your features", which only the user can decide ("These are fine", or
changing memory, plan mode or skills). What the user did with the card itself
(features confirmed, hidden for good, "You're ready" seen) is kept in the
database's meta table, so it follows them to every browser.

An install that already had requests before this feature existed is
"existing": its list stays on Dashboard -> Getting started and never pops up
in the chat. No step costs anything: no model is called.
"""
import json
import os
import platform
import time

from optimux_backend.core import store
from optimux_backend.core.i18n import tr

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
        raise OnboardingError(tr("Unknown action: {action}", action=action))
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


def _step(id_, title, why, done, target, optional=False, info="", check=""):
    """`check`: one short line saying what was found (v0.8: tutorial checks)."""
    return {"id": id_, "title": title, "why": why, "done": bool(done), "target": target,
            "optional": optional, "info": info, "check": check}


def _count(n: int, one: str, many: str) -> str:
    """'1 model ready.' / '3 models ready.': both forms are whole texts, so each translates."""
    return tr(one) if n == 1 else tr(many, n=n)


def _autonomy_check() -> tuple[bool, str]:
    from optimux_backend.core import autonomy
    o = autonomy.overview()
    own = len(o["projects"])
    level = tr(str(o["default"]).capitalize())     # "Supervised", the name the page shows
    if own:
        line = (tr("Default: {level}, 1 project with its own level.", level=level) if own == 1 else
                tr("Default: {level}, {n} projects with their own level.", level=level, n=own))
    elif o["default_saved"]:
        line = tr("Default: {level}.", level=level)
    else:
        line = tr("Default: {level} (not chosen yet).", level=level)
    return bool(o["default_saved"] or own), line


def _github_check() -> tuple[bool, str]:
    from optimux_backend.core import workspace
    try:
        st = workspace.github_status()          # cached for a short while: gh can be slow
    except Exception:                          # noqa: BLE001 (a check never breaks the checklist)
        return False, tr("Couldn't check.")
    if st.get("ready"):
        return True, tr("Connected as {login}.", login=st["login"]) if st.get("login") else tr("Connected.")
    return False, st.get("reason") or tr("Not connected.")


def _backup_check() -> tuple[bool, str]:
    from optimux_backend.core import snapshots
    try:
        ready = [s for s in snapshots.list_snapshots() if s.get("status") == "ready"]
    except OSError:
        return False, tr("Couldn't check.")
    return bool(ready), _count(len(ready), "1 snapshot.", "{n} snapshots.") if ready else tr("No snapshot yet.")


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
    usable = models["available"]
    made = _requests_made()
    plan = budgets.get_budgets()
    autonomy_ok, autonomy_line = _autonomy_check()
    github_ok, github_line = _github_check()
    backup_ok, backup_line = _backup_check()
    simple, complex_ = routing.get("simple_model") or "", routing.get("complex_model") or ""
    steps = [
        _step("models", tr("Add a model and its key"),
              tr("Optimux can't answer without one. Add a cheap fast model and a strong one; Test checks each "
                 "for a fraction of a cent."), usable, "models",
              check=_count(len(usable), "1 model ready.", "{n} models ready.") if usable else tr("No model with a key yet.")),
        _step("routing", tr("Choose who does what"),
              tr("The Conductor picks Simple or Complex for each message; most work goes to the cheap Simple "
                 "model, so this is where the savings come from."),
              simple and complex_ and conductor, "routing",
              check=f"Simple: {simple or '-'}, Complex: {complex_ or '-'}, Conductor: {conductor or '-'}."),
        _step("workspace", tr("Set your Workspace folder"),
              tr("Every folder inside it is a project; commands work there."), root and os.path.isdir(root), "scope",
              check=(root if os.path.isdir(root) else tr("{root} doesn't exist.", root=root)) if root else tr("Not set.")),
        _step("sandbox", tr("Turn on the sandbox"),
              tr("Commands can then change only the active project, even if you approve a bad one."),
              sandbox_ok, "execution",
              check=tr("On.") if sandbox_ok else (tr("Works, but off.") if sb.get("works") else tr("Doesn't work here yet.")),
              info="" if is_linux else tr("Not available on this system: bubblewrap is Linux-only. You approve "
                                          "every command instead.")),
        _step("features", tr("Choose your features"),
              tr("Memory updates itself after each chat (a small cost per chat); plan mode splits big tasks into "
                 "steps for the cheap model; skills are reusable instructions. The defaults are fine to start."),
              s.get("features_ok"), "memory"),
        _step("project", tr("Pick or create a project"),
              tr("The chat works in the active project: its files, its memory and its budget."),
              settings.get("active_project"), "projects",
              check=tr("Active: {project}.", project=settings["active_project"]) if settings.get("active_project") else tr("None open.")),
        _step("first", tr("Send your first request"),
              tr("Ask anything about the project. Commands and file changes are shown for your approval first."),
              made > 0, "chat", check=_count(made, "1 request so far.", "{n} requests so far.") if made else tr("None yet.")),
        _step("budget", tr("Set a monthly budget"), tr("Optimux warns at 80% and can stop at 100%."),
              plan, "budgets", optional=True,
              check=_count(len(plan), "1 budget set.", "{n} budgets set.") if plan else tr("No budget set.")),
        _step("autonomy", tr("Decide what runs without asking"),
              tr("Supervised asks before every command and file change; a project can have more freedom."),
              autonomy_ok, "execution", optional=True, check=autonomy_line),
        _step("backups", tr("Take a first snapshot"),
              tr("A copy of your settings, chats and projects on this machine; the database is also copied daily."),
              backup_ok, "backups", optional=True, check=backup_line),
        _step("github", tr("Connect GitHub"),
              tr("Lets Optimux create repositories for new projects and push. Without it, projects stay local."),
              github_ok, "github", optional=True, check=github_line),
    ]
    if auth.server_mode():
        steps.append(_step("totp", tr("Turn on two-step codes"),
                           tr("On a server, a code from your phone protects the sign-in even if the password leaks."),
                           auth.totp_enabled(), "system", optional=True,
                           check=tr("On.") if auth.totp_enabled() else tr("Off.")))
    if not is_linux:
        steps[3]["done"] = True             # nothing to do there: shown as information
        steps[3]["check"] = ""
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
