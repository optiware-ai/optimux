import asyncio
import os
import json
import time
import traceback
import uuid
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Import from the modular core
# Put files in ~/.optimux's layout before any module reads config or secrets
from optimux_backend.core import paths
for _move in paths.prepare_home():
    print(f"[Optimux] {_move}")

from optimux_backend.core.config_manager import (
    get_models_config, save_api_key_to_env, ApiKeyPayload,
    save_model, delete_model, ModelPayload,
    delete_api_key_from_env, get_routing_config, save_routing_config, RoutingPayload,
    get_settings, save_settings, SettingsPayload, model_id_problem, get_all_models,
    litellm_key_kwargs, max_steps_for, thinking_kwargs, set_thinking, _read_settings, GITHUB_FIELDS,
    set_github_fields, update_settings,
)
from optimux_backend.core.executor import run_shell_command, get_active_cwd
import litellm
from optimux_backend.core.llm_client import route_model_with_usage, TOOLS
from optimux_backend.core import usage as usage_meter
from optimux_backend.core import providers
from optimux_backend.core import budgets
from optimux_backend.core import store
from optimux_backend.core import workspace
from optimux_backend.core import project_records
from optimux_backend.core import skills as skills_lib
from optimux_backend.core import sandbox as sandbox_lib
from optimux_backend.core import curator
from optimux_backend.core import snapshots
from optimux_backend.core import auth
from optimux_backend.core import sessions as chat_store
from optimux_backend.core.providers import ModelUnavailable, ProviderFailure
from optimux_backend.core.memory_manager import init_memory, log_command_to_session, get_memory_context, update_memory
from optimux_backend.core.agents import get_executor_prompt
from optimux_backend.core.security import (
    GITHUB_TOKEN_ENV, ScopeError, ValidationError, bearer_from_header, check_token,
    get_or_create_token, origin_allowed, paths_outside_scope, resolve_project_dir, rotate_token, validate_project_name,
)

# No interactive API docs: they would load scripts from a CDN and list every endpoint
app = FastAPI(title="Optimux Gateway", docs_url=None, redoc_url=None, openapi_url=None)
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(REPO_DIR, "web")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
init_memory()  # Creates the .optimux directory on startup
get_or_create_token()  # Prints a new token on first run, then reads it from .env
try:
    for _name in skills_lib.seed_starters():
        print(f"[Optimux] added starter skill: {_name}")
except Exception as e:
    print(f"[Optimux] starter skills failed: {e}")
try:
    usage_meter.ensure_ready()  # schema + one-time import of the old usage log, before the backup
    # Daily copy of the database (sessions, usage), newest 14 kept
    print(f"[Optimux] database backup: {store.backup()}")
except Exception as e:
    print(f"[Optimux] database backup failed: {e}")

# No CORS middleware: the UI is served from this same origin.


# ---------------------------------------------------------------- access (Feature 23)

# Reachable without signing in: the sign-in page and its API, and static files
PUBLIC_PATHS = {"/login", "/auth/status", "/auth/login", "/auth/setup", "/auth/logout", "/favicon.ico"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(conn) -> str:
    """The caller's address. Behind a reverse proxy on the same machine,
    uvicorn already replaces it with the X-Forwarded-For address."""
    return (conn.client.host if conn.client else "") or "unknown"


def is_https(conn) -> bool:
    return conn.url.scheme in ("https", "wss")


def same_origin(request: Request) -> bool:
    """Cookie-authenticated changes must come from Optimux's own page (CSRF).
    Browsers always send Origin on POST/DELETE; Referer is the fallback."""
    origin = request.headers.get("origin") or ""
    if not origin:
        referer = request.headers.get("referer") or ""
        if referer:
            from urllib.parse import urlsplit
            parts = urlsplit(referer)
            origin = f"{parts.scheme}://{parts.netloc}"
    return bool(origin) and origin != "null" and origin_allowed(origin, request.headers.get("host"))


def security_headers(request, response) -> None:
    host = request.headers.get("host", "")
    ws = f" ws://{host} wss://{host}" if host else ""
    # 'unsafe-eval' is needed by Vue's in-page template compiler; no inline or
    # remote scripts are allowed, so injected HTML can't run code.
    response.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self'; img-src 'self' data:; "
        f"font-src 'self'; connect-src 'self'{ws}; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if is_https(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    path = request.url.path
    if path.startswith("/static/vendor/"):
        # Third-party files carry their version in the name, so a file never changes
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    elif path.startswith("/static/"):
        # Optimux's own files (app.js, app.css, ...): kept, but checked on every load
        # (a 304 when unchanged), so after an update the page never runs an old script
        response.headers.setdefault("Cache-Control", "no-cache")
    else:
        response.headers.setdefault("Cache-Control", "no-store")


def _unauthorized(request: Request):
    if request.method == "GET" and request.url.path == "/":
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Every request except the sign-in page and static files needs a signed-in
    browser (session cookie) or the access token (Bearer header, for scripts)."""
    path = request.url.path
    request.state.login_id = None
    if not (path.startswith("/static/") or path in PUBLIC_PATHS):
        bearer = bearer_from_header(request.headers.get("authorization"))
        ip = client_ip(request)
        if bearer:
            wait = auth.retry_after(ip)
            if wait:
                response = JSONResponse({"detail": "Too many failed attempts."}, status_code=429,
                                        headers={"Retry-After": str(wait)})
                security_headers(request, response)
                return response
            if not check_token(bearer):
                auth.failed(ip)
                response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
                security_headers(request, response)
                return response
        else:
            login_id = auth.check_session(request.cookies.get(auth.COOKIE))
            if not login_id:
                response = _unauthorized(request)
                security_headers(request, response)
                return response
            if request.method not in SAFE_METHODS and not same_origin(request):
                response = JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
                security_headers(request, response)
                return response
            request.state.login_id = login_id
    elif request.method not in SAFE_METHODS and path.startswith("/auth/") and not same_origin(request):
        response = JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
        security_headers(request, response)
        return response
    response = await call_next(request)
    security_headers(request, response)
    return response


@app.exception_handler(ScopeError)
@app.exception_handler(ValidationError)
async def bad_config_handler(request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/config")
async def get_config():
    return JSONResponse(get_models_config())

@app.get("/api/routing")
async def get_routing():
    return get_routing_config()

@app.post("/api/routing")
async def save_routing(payload: RoutingPayload):
    save_routing_config(payload)
    return {"status": "success"}

@app.post("/api/keys")
async def save_api_key(payload: ApiKeyPayload):
    save_api_key_to_env(payload)
    # A new key may fix whatever put these models in cooldown (quota, billing, bad key)
    using_key = [mid for mid, d in get_all_models().items() if d.get("env_key") == payload.env_key]
    cleared = providers.clear_models(using_key)
    return {"status": "success", "cooldowns_cleared": cleared}

@app.delete("/api/keys/{env_key}")
async def delete_api_key(env_key: str):
    delete_api_key_from_env(env_key)
    return {"status": "success"}

@app.post("/api/models")
async def create_model(payload: ModelPayload):
    save_model(payload)
    return {"status": "success"}

from pydantic import BaseModel
import os

class MemoryPayload(BaseModel):
    file_type: str
    content: str

MEMORY_FILES = {"personal", "professional"}

@app.get("/api/memory/{file_type}")
async def get_memory_file(file_type: str):
    from optimux_backend.core.memory_manager import PERSONAL_MD, PROFESSIONAL_MD, _read_md, DEFAULT_PERSONAL, DEFAULT_PROFESSIONAL
    if file_type not in MEMORY_FILES:
        raise HTTPException(400, f"file_type must be one of {sorted(MEMORY_FILES)}")
    if file_type == "personal":
        return {"content": _read_md(PERSONAL_MD, DEFAULT_PERSONAL)}
    return {"content": _read_md(PROFESSIONAL_MD, DEFAULT_PROFESSIONAL)}

@app.post("/api/memory")
async def save_memory_file(payload: MemoryPayload):
    from optimux_backend.core.memory_manager import PERSONAL_MD, PROFESSIONAL_MD
    if payload.file_type not in MEMORY_FILES:
        raise HTTPException(400, f"file_type must be one of {sorted(MEMORY_FILES)}")
    filepath = PERSONAL_MD if payload.file_type == "personal" else PROFESSIONAL_MD
    with open(filepath, "w") as f:
        f.write(payload.content)
    return {"status": "success"}

class ModelTestPayload(BaseModel):
    id: str


@app.post("/api/models/test")
async def test_model(payload: ModelTestPayload):
    """One tiny real call to check a model ID and its key. Costs a few tokens."""
    problem = model_id_problem(payload.id)
    if problem:
        return {"ok": False, "kind": "config", "message": problem}
    started = time.monotonic()
    try:
        response = await litellm.acompletion(
            model=payload.id,
            messages=[{"role": "user", "content": "Reply with: ok"}],
            max_tokens=5,
            timeout=20,
            **litellm_key_kwargs(payload.id),
        )
    except Exception as e:
        info = providers.classify(e)
        return {"ok": False, "kind": info.kind, "message": info.summary}
    duration = int((time.monotonic() - started) * 1000)
    usage_meter.record("settings", "test", payload.id, getattr(response, "usage", None), duration)
    # The model answered, so any cooldown on it is out of date
    cleared = providers.clear_model(payload.id)
    message = f"Works ({duration} ms)." + (" Cooldown cleared." if cleared else "")
    return {"ok": True, "message": message, "duration_ms": duration, "cooldown_cleared": cleared}


class ThinkingPayload(BaseModel):
    id: str
    thinking: str


@app.post("/api/models/thinking")
async def set_model_thinking(payload: ThinkingPayload):
    set_thinking(payload.id, payload.thinking)
    return {"status": "success"}


@app.delete("/api/models/{model_id:path}")
async def remove_model(model_id: str):
    delete_model(model_id)
    return {"status": "success"}

@app.get("/api/usage")
async def get_usage(project: str | None = None):
    """Usage totals; ?project=<name> limits them to one project."""
    return usage_meter.summary(project=project)

class BudgetPayload(BaseModel):
    project: str
    monthly_usd: float | None = None  # None removes the budget
    on_limit: str = "block"


@app.get("/api/budgets")
async def get_budgets():
    """Budget status (this month) for every project that has a budget. An
    archived project's budget is `paused`: kept for a restore, but nothing can
    spend there (fix25e)."""
    out = budgets.all_statuses()
    root = get_settings().get("projects_root", "")
    try:
        archived = {a["name"] for a in workspace.list_archived()} if root else set()
    except workspace.WorkspaceError:
        archived = set()
    for name, s in out.items():
        if s and name in archived and not os.path.lexists(os.path.join(root, name)):
            s["paused"] = True
    return out


@app.post("/api/budgets")
async def set_budget(payload: BudgetPayload):
    budgets.save_budget(payload.project, payload.monthly_usd, payload.on_limit)
    return {"status": "success", "budget": budgets.status(payload.project)}


class SessionRename(BaseModel):
    title: str


class SessionArchive(BaseModel):
    archived: bool = True


class SessionDelete(BaseModel):
    confirm: str  # must equal the session's title (or its id when untitled)


@app.get("/api/sessions")
async def list_sessions(project: str | None = None, all_projects: bool = False,
                        archived: bool = False, q: str = ""):
    """Stored chats, newest first: the active project's by default."""
    if not all_projects and project is None:
        project = active_project()
    return chat_store.list_sessions(None if all_projects else project, archived, q)


@app.post("/api/sessions/{sid}/rename")
async def rename_session(sid: str, payload: SessionRename):
    if not payload.title.strip():
        raise HTTPException(400, "Title can't be empty.")
    if not chat_store.rename(sid, payload.title):
        raise HTTPException(404, "No such session.")
    return {"status": "success"}


@app.post("/api/sessions/{sid}/archive")
async def archive_session(sid: str, payload: SessionArchive):
    if not chat_store.set_archived(sid, payload.archived):
        raise HTTPException(404, "No such session.")
    return {"status": "success"}


@app.post("/api/sessions/{sid}/delete")
async def delete_session(sid: str, payload: SessionDelete):
    """Permanent. Requires the session's title (or id if untitled) as confirmation.
    Usage and cost records of the session are kept."""
    data = chat_store.get(sid)
    if data is None:
        raise HTTPException(404, "No such session.")
    expected = data["title"] or data["id"]
    if payload.confirm.strip() != expected.strip():
        raise HTTPException(400, "Confirmation doesn't match the session title.")
    chat_store.delete(sid)
    return {"status": "success"}


@app.get("/api/usage/daily")
async def get_usage_daily(days: int = 30, project: str | None = None):
    """Cost and tokens per day and model for charts."""
    return usage_meter.daily(max(1, min(days, 366)), project)


class ProjectMemoryPayload(BaseModel):
    content: str


def _project_memory_path(name: str) -> str:
    from optimux_backend.core.memory_manager import PROJECTS_DIR
    validate_project_name(name)  # ScopeError -> 400
    return os.path.join(PROJECTS_DIR, f"{name}.md")


@app.get("/api/memory/project/{name}")
async def get_project_memory(name: str):
    from optimux_backend.core.memory_manager import _read_md, DEFAULT_PROJECT, init_memory
    init_memory()
    path = _project_memory_path(name)
    return {"content": _read_md(path, DEFAULT_PROJECT), "exists": os.path.exists(path)}


@app.post("/api/memory/project/{name}")
async def save_project_memory(name: str, payload: ProjectMemoryPayload):
    _project_memory_path(name)  # validates the name
    version = curator.write_memory(name, payload.content, "user", "Edited in the dashboard")
    return {"status": "success", "version_id": version}


@app.get("/api/memory/project/{name}/versions")
async def project_memory_versions(name: str):
    """Change history of a project's memory, newest first, each with a diff."""
    _project_memory_path(name)
    return curator.list_versions(name)


@app.post("/api/memory/versions/{version_id}/undo")
async def undo_memory_version(version_id: int):
    try:
        return curator.undo(version_id)
    except KeyError:
        raise HTTPException(404, "No such version.")


@app.post("/api/memory/project/{name}/curate")
async def curate_project_now(name: str):
    """Run the curator now on the project's most recent chat, reading the whole chat
    (even parts read before, e.g. after an undo) and even if automatic updates are off."""
    _project_memory_path(name)
    sid = chat_store.latest(name)
    if not sid:
        return {"changed": False, "reason": "There are no chats in this project yet."}
    result = await curate_session(sid, force=True, full=True)
    data = chat_store.get(sid) or {}
    result["chat_title"] = data.get("title") or "Untitled chat"
    return result


@app.get("/api/sandbox")
async def sandbox_status(refresh: bool = False):
    """Whether bubblewrap is installed and works, and whether the sandbox is on or forced on."""
    return {**sandbox_lib.status(refresh=refresh), "required": sandbox_lib.required(),
            "enabled": sandbox_lib.enabled(get_settings()), "server_mode": auth.server_mode()}


@app.get("/api/costs/estimates")
async def cost_estimates():
    """What extra paid actions usually cost, from the last few priced calls (Feature 18):
    shown before Update memory now, Save as skill and Escalate, and next to the memory switch."""
    month = usage_meter.month_start()
    return {
        "memory_update": usage_meter.recent_average("curator"),
        "skill_draft": usage_meter.recent_average("skill_draft"),
        "complex_call": usage_meter.recent_average("complex"),
        "memory_this_month": usage_meter.role_totals("curator", since=month),
    }


@app.get("/api/projects/overview")
async def projects_overview():
    """One entry per project (folders under the projects root, plus any project
    with usage or chats): this month's and all-time cost, budget, chats, last activity."""
    settings = get_settings()
    root = settings.get("projects_root", "")
    names = set()
    if root and os.path.isdir(root):
        names |= {d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")}
    activity = usage_meter.project_activity()
    chats = chat_store.project_counts()
    names |= {p for p in activity if p not in ("(unknown)", usage_meter.NO_PROJECT)}
    names |= {p for p in chats if p}
    deleted = project_records.deleted_projects(root if root and os.path.isdir(root) else None)
    try:
        archived = {a["name"] for a in workspace.list_archived()}
    except workspace.WorkspaceError:
        archived = set()
    out = []
    for name in sorted(names):
        a = activity.get(name, {})
        c = chats.get(name, {})
        month = usage_meter.month_totals(name)
        last = max([t for t in (a.get("last_ts"), c.get("last_updated")) if t] or [0]) or None
        out.append({
            "project": name,
            "exists": bool(root) and os.path.isdir(os.path.join(root, name)),
            "active": name == settings.get("active_project", ""),
            "month_cost": month["cost"],
            "all_time_cost": a.get("cost", 0.0),
            "calls": a.get("calls", 0),
            "chats": c.get("chats", 0),
            "last_activity": last,
            "budget": budgets.status(name),
            "archived": name in archived,
            "deleted": deleted.get(name),
        })
    return out


# ---- fix25a: archive, restore and delete from the Dashboard (buttons only; the
# model can archive through its tool, but never restore or delete)

@app.get("/api/projects/deleted")
async def deleted_projects():
    """{name: deleted_at} for deleted projects whose chats, costs and memory were kept."""
    root = get_settings().get("projects_root", "")
    return project_records.deleted_projects(root if root and os.path.isdir(root) else None)


@app.get("/api/projects/archived")
async def archived_projects():
    try:
        items = workspace.list_archived()
    except workspace.WorkspaceError:
        return []
    out = []
    for a in items:
        remote = await asyncio.to_thread(workspace._git_remote, os.path.join(workspace._archive_dir(), a["folder"]))
        out.append({**a, "records": project_records.record_counts(a["name"]), "phrase": workspace.delete_phrase(a["name"]),
                    "remote": remote, "github_settings": workspace.github_settings_url(remote)})
    return out


@app.post("/api/projects/{name}/archive")
async def archive_project_api(name: str):
    if BUSY_PROJECTS.get(name):
        return JSONResponse({"detail": f"A request is running in '{name}' (maybe in another tab). "
                                       "Wait for it to finish, or stop it, then archive."}, status_code=422)
    try:
        result = await asyncio.to_thread(workspace.archive_project, name)
    except workspace.WorkspaceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    if active_project() == result["project"]:
        save_settings(SettingsPayload(**{**get_settings(), "active_project": ""}))
        result["was_active"] = True
    # Every open page: refresh the project list, and leave the project if it's open there
    await _broadcast({"type": "project_archived", "project": result["project"]})
    return result


@app.post("/api/projects/archived/{folder}/restore")
async def restore_project_api(folder: str):
    try:
        return await asyncio.to_thread(workspace.restore_archived, folder)
    except workspace.WorkspaceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)


@app.get("/api/projects/archived/{folder}/github")
async def archived_github_state(folder: str):
    """Does its GitHub repository still exist? Asked when the delete dialog opens (fix25c)."""
    try:
        path, _ = workspace._archived_path(folder)
    except workspace.WorkspaceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    remote = await asyncio.to_thread(workspace._git_remote, path)
    state = await asyncio.to_thread(workspace.github_repo_state, remote)
    return {**state, "settings": workspace.github_settings_url(remote)}


class DeleteProjectPayload(BaseModel):
    confirm: str
    keep_records: bool = True


@app.post("/api/projects/archived/{folder}/delete")
async def delete_project_api(folder: str, payload: DeleteProjectPayload):
    """Permanently delete an archived project's folder. Its GitHub repository is never touched."""
    try:
        return await asyncio.to_thread(workspace.delete_archived, folder, payload.confirm, payload.keep_records)
    except workspace.WorkspaceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)


def _version(cmd: list[str]) -> str:
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3,
                              cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@app.get("/api/system")
async def system_info():
    """Versions and storage, for the System section."""
    import platform
    from importlib import metadata
    try:
        litellm_version = metadata.version("litellm")
    except Exception:
        litellm_version = "unknown"
    return {
        "optimux": _version(["git", "describe", "--always", "--dirty"]),
        "litellm": litellm_version,
        "python": platform.python_version(),
        "storage": store.info(),
        "paths": paths.layout(),
        "migrations": paths.migration_log(20),
    }


# ---------------------------------------------------------------- snapshots (v0.3)
# Errors are 422 (not 400) so the Backups forms show them inline (boot.js alerts on 400).

class SnapshotPayload(BaseModel):
    kind: str
    note: str = ""
    projects: list[str] = []
    skip_rebuildable: bool = True


class SnapshotLimitPayload(BaseModel):
    gb: float


@app.get("/api/snapshots")
async def list_snapshots_api():
    """Snapshots kept on this machine, newest first, with the space they use."""
    return await asyncio.to_thread(snapshots.summary)


@app.post("/api/snapshots/preview")
async def preview_snapshot_api(payload: SnapshotPayload):
    """What a snapshot would hold and whether it fits; nothing is written."""
    try:
        return await asyncio.to_thread(snapshots.preview, payload.kind, payload.projects or None,
                                       payload.skip_rebuildable)
    except snapshots.SnapshotError as e:
        raise HTTPException(422, str(e))


@app.post("/api/snapshots")
async def take_snapshot_api(payload: SnapshotPayload):
    """Start taking a snapshot in the background; poll GET /api/snapshots for its status."""
    try:
        sid = await asyncio.to_thread(snapshots.start, payload.kind, payload.note,
                                      payload.projects or None, payload.skip_rebuildable)
    except snapshots.SnapshotError as e:
        raise HTTPException(422, str(e))
    return {"id": sid}


@app.post("/api/snapshots/limit")
async def snapshot_limit_api(payload: SnapshotLimitPayload):
    """Change only the snapshot space limit (other unsaved Dashboard edits stay unsaved)."""
    try:
        update_settings(snapshot_limit_gb=payload.gb)
    except (ValidationError, ScopeError) as e:
        raise HTTPException(422, str(e))
    return {"snapshot_limit_gb": payload.gb}


@app.get("/api/snapshots/{sid}/readme")
async def snapshot_readme_api(sid: str):
    """The README inside a snapshot, to read it in the page: {text, format: md|txt}."""
    try:
        return await asyncio.to_thread(snapshots.read_readme, sid)
    except snapshots.SnapshotError as e:
        raise HTTPException(422, str(e))


@app.post("/api/snapshots/{sid}/delete")
async def delete_snapshot_api(sid: str):
    try:
        snapshots.delete(sid)
    except snapshots.SnapshotError as e:
        raise HTTPException(422, str(e))
    return {"status": "deleted"}


class RotatePayload(BaseModel):
    current: str


@app.post("/api/system/rotate-token")
async def rotate_access_token(payload: RotatePayload):
    """Replace the access token. The current token must be typed again; the new
    one is returned once (it is also in .env)."""
    if not check_token(payload.current):
        raise HTTPException(400, "The current token doesn't match.")
    return {"token": rotate_token()}


# ---------------------------------------------------------------- signing in (Feature 23)

class SetupPayload(BaseModel):
    token: str
    password: str


class LoginPayload(BaseModel):
    password: str
    code: str = ""


class PasswordPayload(BaseModel):
    current: str
    new: str


class CodePayload(BaseModel):
    code: str


class PasswordOnlyPayload(BaseModel):
    password: str


# Open chats per sign-in, so signing out closes them too
LIVE_SOCKETS: dict[str, set] = {}


async def _close_sockets(keep: str | None = None) -> None:
    for login_id, socks in list(LIVE_SOCKETS.items()):
        if login_id == keep:
            continue
        for sock in list(socks):
            try:
                await sock.close(code=4401)
            except Exception:
                pass
        LIVE_SOCKETS.pop(login_id, None)


# Requests running per project (started there), so the Dashboard can't archive
# a project under a running chat in another tab
BUSY_PROJECTS: dict[str, int] = {}


async def _broadcast(msg: dict) -> None:
    """Send to every open page, straight to the socket (never part of a chat)."""
    text = json.dumps(msg)
    for socks in list(LIVE_SOCKETS.values()):
        for sock in list(socks):
            try:
                await sock.send_text(text)
            except Exception:
                pass


# ---- Feature 26: notice changes in the Workspace made outside Optimux (a folder
# made by hand, git clone in a terminal) and tell open pages. Runs only while a
# page is open; reading two folder listings every few seconds costs nothing.
WATCH: dict = {"task": None, "snapshot": None}
WATCH_INTERVAL = 3.0


def _pages_open() -> bool:
    return any(LIVE_SOCKETS.values())


async def _watch_workspace() -> None:
    try:
        while _pages_open():
            try:
                snap = await asyncio.to_thread(workspace.workspace_snapshot)
                if WATCH["snapshot"] is not None and snap != WATCH["snapshot"]:
                    await _broadcast({"type": "projects_changed"})
                WATCH["snapshot"] = snap
            except Exception as e:          # never let one bad look stop the updates
                print(f"[Optimux] Workspace watcher: {e}")
            await asyncio.sleep(WATCH_INTERVAL)
    finally:
        WATCH.update(task=None, snapshot=None)


def ensure_workspace_watcher() -> None:
    if WATCH["task"] is None:
        WATCH["task"] = asyncio.get_running_loop().create_task(_watch_workspace())


async def _announce(event: str, request: Request | None = None) -> None:
    """Tell every signed-in browser with Optimux open that someone signed in
    (shown as a notice) or out (lists refresh). Sent straight to the socket,
    so it never becomes part of a chat."""
    msg = {"type": "auth_event", "event": event, "time": time.time()}
    if request is not None:
        msg.update(ip=client_ip(request), agent=request.headers.get("user-agent", "")[:200])
    text = json.dumps(msg)
    for socks in list(LIVE_SOCKETS.values()):
        for sock in list(socks):
            try:
                await sock.send_text(text)
            except Exception:
                pass


def _signed_in(request: Request, response: JSONResponse) -> JSONResponse:
    token = auth.create_session(client_ip(request), request.headers.get("user-agent", ""))
    response.set_cookie(auth.COOKIE, token, max_age=auth.MAX_AGE_SECONDS, path="/", httponly=True,
                        samesite="strict", secure=is_https(request) or auth.server_mode())
    return response


def _limited(request: Request, password: bool = False) -> JSONResponse | None:
    ip = client_ip(request)
    wait = auth.password_retry_after(ip) if password else auth.retry_after(ip)
    if wait:
        mins = max(1, round(wait / 60))
        return JSONResponse({"detail": f"Too many failed attempts. Try again in about {mins} minute{'s' if mins != 1 else ''}."},
                            status_code=429, headers={"Retry-After": str(wait)})
    return None


@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(WEB_DIR, "login.html"), media_type="text/html")


@app.get("/favicon.ico")
async def favicon():
    """The logo, for browsers and tools that ask for /favicon.ico (pages link the SVG)."""
    return FileResponse(os.path.join(WEB_DIR, "logo.svg"), media_type="image/svg+xml")


@app.get("/auth/status")
async def auth_status(request: Request):
    return {"setup_needed": not auth.password_set(), "totp": auth.totp_enabled(),
            "signed_in": bool(auth.check_session(request.cookies.get(auth.COOKIE))),
            "server_mode": auth.server_mode(), "https": is_https(request)}


@app.post("/auth/setup")
async def auth_setup(payload: SetupPayload, request: Request):
    """First run: whoever has the access token (from the server's files) sets the password."""
    if auth.password_set():
        raise HTTPException(409, "A password is already set. Sign in instead.")
    limited = _limited(request)
    if limited:
        return limited
    if not check_token(payload.token.strip()):
        auth.failed(client_ip(request))
        raise HTTPException(401, "That access token doesn't match.")
    try:
        auth.set_password(payload.password)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    auth.end_all_sessions()
    await _close_sockets()          # a reset (deleted auth.json) also closes chats still open
    auth.succeeded(client_ip(request))
    return _signed_in(request, JSONResponse({"ok": True}))


@app.post("/auth/login")
async def auth_login(payload: LoginPayload, request: Request):
    limited = _limited(request, password=True)
    if limited:
        return limited
    ip = client_ip(request)
    if not auth.password_set():
        raise HTTPException(409, "No password yet: set one with the access token.")
    if not await asyncio.to_thread(auth.check_password, payload.password):
        auth.password_failed(ip)
        raise HTTPException(401, "Wrong password.")
    if auth.totp_enabled():
        if not payload.code.strip():
            return JSONResponse({"need_code": True}, status_code=401)
        wait = auth.code_limiter.retry_after(auth.GLOBAL)
        if wait:
            return JSONResponse({"detail": "Too many wrong codes. Two-step sign-in is paused for a while; try again later."},
                                status_code=429, headers={"Retry-After": str(wait)})
        if not auth.check_totp(payload.code):
            auth.failed(ip)
            auth.code_limiter.fail(auth.GLOBAL)
            raise HTTPException(401, "Wrong or already used code.")
    auth.succeeded(ip)
    await _announce("signin", request)      # the other browsers see who just signed in
    return _signed_in(request, JSONResponse({"ok": True}))


@app.post("/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(auth.COOKIE)
    if token:
        login_id = auth.session_hash(token)
        auth.end_session(login_id, is_id=True)
        for sock in list(LIVE_SOCKETS.pop(login_id, set())):
            try:
                await sock.close(code=4401)
            except Exception:
                pass
    await _announce("signout")
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/api/auth")
async def auth_overview(request: Request):
    return {"totp": auth.totp_enabled(), "server_mode": auth.server_mode(),
            "sessions": auth.list_sessions(request.state.login_id),
            "via_token": request.state.login_id is None}


@app.post("/api/auth/signout-others")
async def auth_signout_others(request: Request):
    """Sign out every other browser (this one stays signed in)."""
    ended = auth.end_all_sessions(keep_id=request.state.login_id)
    await _close_sockets(keep=request.state.login_id)
    await _announce("signout")
    return {"ended": ended}


@app.post("/api/auth/password")
async def auth_change_password(payload: PasswordPayload, request: Request):
    limited = _limited(request)
    if limited:
        return limited
    if not await asyncio.to_thread(auth.check_password, payload.current):
        auth.failed(client_ip(request))
        raise HTTPException(400, "The current password doesn't match.")
    try:
        auth.set_password(payload.new)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    ended = auth.end_all_sessions(keep_id=request.state.login_id)   # other browsers must sign in again
    await _close_sockets(keep=request.state.login_id)
    return {"ok": True, "ended": ended}


@app.post("/api/auth/totp/start")
async def auth_totp_start(payload: PasswordOnlyPayload, request: Request):
    limited = _limited(request)
    if limited:
        return limited
    if not await asyncio.to_thread(auth.check_password, payload.password):
        auth.failed(client_ip(request))
        raise HTTPException(400, "The password doesn't match.")
    try:
        return auth.start_totp()
    except auth.AuthError as e:
        raise HTTPException(409, str(e))


@app.post("/api/auth/totp/confirm")
async def auth_totp_confirm(payload: CodePayload):
    if not auth.confirm_totp(payload.code):
        raise HTTPException(400, "That code doesn't match. Check the phone's clock and try the next code.")
    return {"ok": True}


@app.post("/api/auth/totp/disable")
async def auth_totp_disable(payload: LoginPayload, request: Request):
    limited = _limited(request)
    if limited:
        return limited
    if auth.code_limiter.retry_after(auth.GLOBAL):
        return JSONResponse({"detail": "Too many wrong codes; try again later."}, status_code=429)
    ok = await asyncio.to_thread(auth.check_password, payload.password) and auth.check_totp(payload.code)
    if not ok:
        auth.failed(client_ip(request))
        auth.code_limiter.fail(auth.GLOBAL)
        raise HTTPException(400, "Password or code doesn't match.")
    auth.disable_totp()
    return {"ok": True}


def _loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


@app.get("/api/security")
async def security_checklist(request: Request):
    """What protects this install right now (Dashboard > System)."""
    settings = get_settings()
    sb = sandbox_lib.status()
    server = auth.server_mode()
    local = _loopback(client_ip(request))
    sandbox_on = sandbox_lib.enabled(settings) and sb["works"]
    network = bool(settings.get("sandbox_network", True))
    others = max(0, len(auth.list_sessions()) - 1)
    checks = [
        {"id": "https", "label": "HTTPS", "ok": is_https(request), "important": not local,
         "detail": "This page is encrypted." if is_https(request) else
                   ("Plain HTTP is fine on this computer only." if local else
                    "Plain HTTP over the network: the password and your chats can be read in transit. Put Optimux behind a reverse proxy with HTTPS.")},
        {"id": "totp", "label": "Two-step codes", "ok": auth.totp_enabled(), "important": server or not local,
         "detail": "A code from your phone is needed to sign in." if auth.totp_enabled() else
                   "Off: the password alone signs in. Turn it on below."},
        {"id": "sandbox", "label": "Sandbox", "ok": sandbox_on, "important": True,
         "detail": ("Commands can only change the working folder." if sandbox_on else
                    ("On, but bubblewrap doesn't work here, so commands are refused." if sandbox_lib.enabled(settings) else
                     "Off: an approved command can change anything the server's user can. Turn it on in Execution."))},
        {"id": "network", "label": "Network for commands", "ok": not (sandbox_on and network), "important": server,
         "detail": ("Commands have no network." if sandbox_on and not network else
                    ("Commands can reach the internet (needed for pip, npm, git over HTTPS)." if sandbox_on else
                     "Without the sandbox, commands always have the network."))},
        {"id": "approve", "label": "Command approval", "ok": not settings.get("auto_approve"), "important": True,
         "detail": "Every command waits for your approval." if not settings.get("auto_approve") else
                   "Auto-approve is on: commands run without asking."},
        {"id": "server", "label": "Server mode", "ok": server, "important": not local,
         "detail": "OPTIMUX_SERVER=1: sandbox required, auto-approve off, secure cookies." if server else
                   "Off (fine on your own computer). On a VPS, start with OPTIMUX_SERVER=1."},
        {"id": "sessions", "label": "Other signed-in browsers", "ok": True, "important": False,
         "detail": f"{others} other browser{'s' if others != 1 else ''} signed in." if others else "Only this browser."},
    ]
    return {"checks": checks, "server_mode": server}


class SkillPayload(BaseModel):
    content: str


class SkillDraftPayload(BaseModel):
    session_id: str
    name: str = ""


@app.get("/api/skills")
async def list_skills():
    """Global skills and the active project's, with this month's and all-time usage."""
    found = current_skills()
    summary = usage_meter.summary()
    all_time = summary["all_time"]["by_skill"]
    month = summary["this_month"]["by_skill"]
    drafts = summary["all_time"]["skill_drafts"]
    # list both scopes even when a project skill overrides a global one
    entries = []
    for scope, root in skills_lib.roots(skill_project_dir()):
        if not os.path.isdir(root):
            continue
        for folder in sorted(os.listdir(root)):
            path = os.path.join(root, folder, "SKILL.md")
            if not os.path.isfile(path):
                continue
            with open(path) as f:
                meta, _ = skills_lib.parse(f.read())
            info = skills_lib._clean_meta(meta, folder)
            entries.append({**info, "name": folder, "scope": scope, "folder": os.path.join(root, folder),
                            "active": folder in found and found[folder]["scope"] == scope,
                            "usable": bool(info["description"]) and bool(skills_lib.NAME_RE.match(folder)),
                            "month": month.get(folder, {}), "all_time": all_time.get(folder, {}),
                            "drafting": drafts.get(folder, {})})
    listed = listed_skills(found)
    return {"skills": entries, "global_dir": paths.SKILLS_DIR, "skills_mode": skills_mode(),
            "listed": sorted(listed), "catalog_tokens": skills_lib.catalog_tokens(listed),
            "project_dir_name": (os.path.basename(skills_lib.project_skills_dir(skill_project_dir()))
                                 if skill_project_dir() else skills_lib.PROJECT_SKILLS_DIR),
            "project_dir": skills_lib.project_skills_dir(skill_project_dir()) if skill_project_dir() else None}


@app.get("/api/skills/template")
async def skill_template(name: str = "my-skill"):
    return {"content": skills_lib.template(name)}


@app.get("/api/skills/{scope}/{name}")
async def read_skill(scope: str, name: str):
    try:
        return {"content": skills_lib.read_raw(scope, name, skill_project_dir())}
    except skills_lib.SkillError as e:
        raise HTTPException(404, str(e))


@app.post("/api/skills/{scope}/{name}")
async def save_skill(scope: str, name: str, payload: SkillPayload):
    try:
        return {"status": "success", "skill": skills_lib.save_raw(scope, name, payload.content, skill_project_dir())}
    except skills_lib.SkillError as e:
        raise HTTPException(400, str(e))


class SkillModePayload(BaseModel):
    mode: str


@app.post("/api/skills/{scope}/{name}/mode")
async def set_skill_mode(scope: str, name: str, payload: SkillModePayload):
    """manual (runs only on /name) or auto (listed in every prompt)."""
    try:
        skills_lib.set_mode(scope, name, payload.mode, skill_project_dir())
    except skills_lib.SkillError as e:
        raise HTTPException(400, str(e))
    return {"status": "success"}


@app.post("/api/skills/{scope}/{name}/remove")
async def remove_skill(scope: str, name: str):
    """Moves the skill folder to ~/.optimux/legacy/skills/ (not deleted)."""
    try:
        return {"status": "success", "moved_to": skills_lib.remove(scope, name, skill_project_dir())}
    except skills_lib.SkillError as e:
        raise HTTPException(404, str(e))


@app.post("/api/skills/draft")
async def draft_skill(payload: SkillDraftPayload):
    """Draft a SKILL.md from a stored chat with the Simple model. Nothing is saved;
    the dashboard shows the draft for editing."""
    data = chat_store.get(payload.session_id)
    if data is None:
        raise HTTPException(404, "No such session.")
    model = get_routing_config().get("simple_model", "")
    if not model:
        raise HTTPException(400, "No Simple model configured.")
    transcript = []
    for e in data["events"]:
        if e.get("type") == "prompt":
            transcript.append(f"USER: {e.get('content', '')}")
        elif e.get("type") == "command_start":
            transcript.append(f"COMMAND: {e.get('command', '')}")
        elif e.get("type") == "command_output":
            transcript.append(f"OUTPUT (exit {e.get('exit_code')}): {(e.get('output') or '')[:400]}")
        elif e.get("type") == "token":
            transcript.append(f"ASSISTANT: {(e.get('content') or '')[:1500]}")
    text = "\n".join(transcript)[-12000:]
    name = payload.name or "new-skill"
    prompt = ("Turn this chat into a reusable skill for a coding assistant. Write only a SKILL.md file: front matter "
              f"with name: {name}, a one-line description that says what the skill does and when to use it, and "
              "mode: manual, optionally tier (simple or complex) and max_steps; then concise steps and rules in markdown. Generalize "
              "away from this chat's specifics (file names, values) unless they are the point of the skill.\n\n" + text)
    started = time.monotonic()
    try:
        # The model's own thinking level (Gemini 3's default thinks a lot, which made drafts costly)
        response = await litellm.acompletion(model=model, messages=[{"role": "user", "content": prompt}],
                                             timeout=60, **litellm_key_kwargs(model), **thinking_kwargs(model))
    except Exception as e:
        raise HTTPException(502, providers.classify(e).summary)
    usage_meter.record(payload.session_id, "skill_draft", model, getattr(response, "usage", None),
                       int((time.monotonic() - started) * 1000), project=data.get("project", ""), skill=name)
    content = (response.choices[0].message.content or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return {"content": content + "\n", "name": name}


@app.get("/api/storage")
async def get_storage():
    """Where usage and sessions are stored, and the latest backup."""
    return store.info()


@app.get("/api/cooldowns")
async def get_cooldowns():
    """Models currently skipped because of quota or repeated provider errors."""
    return providers.active_cooldowns()


class CooldownClearPayload(BaseModel):
    model: str | None = None  # omitted: clear all


@app.post("/api/cooldowns/clear")
async def clear_cooldowns(payload: CooldownClearPayload):
    if payload.model:
        return {"cleared": [payload.model] if providers.clear_model(payload.model) else []}
    cleared = list(providers.active_cooldowns())
    providers.clear()
    return {"cleared": cleared}

@app.get("/api/settings")
async def get_app_settings():
    return get_settings()

@app.post("/api/settings")
async def save_app_settings(payload: SettingsPayload):
    # The GitHub fields have their own endpoint; a page with stale values can't undo them
    stored = _read_settings()
    for field in GITHUB_FIELDS:
        setattr(payload, field, stored.get(field, "") or "")
    save_settings(payload)
    return {"status": "success"}

@app.get("/api/projects")
async def get_projects():
    import os
    settings = get_settings()
    root = settings.get("projects_root", "")
    projects = []
    if root and os.path.isdir(root):
        try:
            for item in os.listdir(root):
                if os.path.isdir(os.path.join(root, item)) and not item.startswith('.'):
                    projects.append(item)
        except Exception:
            pass
    return sorted(projects)


class CreateProjectPayload(BaseModel):
    name: str
    template: str = "basic"
    git_init: bool = True
    github: str = ""
    description: str = ""


# Errors here are 422, not 400: the forms show them inline (boot.js alerts on 400)
@app.post("/api/projects")
async def create_project_api(payload: CreateProjectPayload):
    """Feature 25: the New project form (no model involved). The click on Create is the approval."""
    try:
        form = workspace.normalize_create(payload.model_dump())
    except workspace.WorkspaceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    problem = await asyncio.to_thread(create_problem, form)
    if problem:
        return JSONResponse({"detail": problem}, status_code=422)
    try:
        result = await asyncio.to_thread(workspace.create_project, form["name"], template=form["template"],
                                         git_init=form["git_init"], github=form["github"], description=form["description"])
    except workspace.WorkspaceError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return {**result, "report": create_report(result)}


class GitHubTokenPayload(BaseModel):
    token: str


@app.get("/api/github")
async def github_info(fresh: bool = False):
    settings = get_settings()
    status = await asyncio.to_thread(workspace.github_status, fresh)
    return {**status, "git_name": settings.get("git_name", ""), "git_email": settings.get("git_email", ""),
            "sandbox": sandbox_lib.enabled(settings)}


class GitIdentityPayload(BaseModel):
    git_name: str = ""
    git_email: str = ""


@app.post("/api/github/identity")
async def github_identity(payload: GitIdentityPayload):
    """Who commits (Dashboard → GitHub); saved on its own, never with the other settings."""
    try:
        set_github_fields(git_name=payload.git_name, git_email=payload.git_email)
    except ValidationError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return {"status": "success"}


@app.post("/api/github")
async def github_connect(payload: GitHubTokenPayload):
    """Check the token with GitHub, then save it. Never echoed back."""
    try:
        info = await asyncio.to_thread(workspace.connect_github, payload.token)
    except (workspace.GitHubError, ValidationError) as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return {"login": info["login"], "name": info["name"], "warning": info["warning"], "hint": info["hint"],
            "identity_set": info["identity_set"]}


@app.post("/api/github/check")
async def github_check():
    """Ask GitHub whether the saved token still works."""
    token = os.environ.get(GITHUB_TOKEN_ENV, "")
    if not token:
        return JSONResponse({"detail": "No token is saved in Optimux."}, status_code=422)
    try:
        info = await asyncio.to_thread(workspace.check_github_token, token)
    except workspace.GitHubError as e:
        return JSONResponse({"detail": str(e)}, status_code=422)
    return {"login": info["login"], "name": info["name"], "warning": info["warning"], "hint": info["hint"]}


@app.delete("/api/github")
async def github_disconnect():
    await asyncio.to_thread(workspace.disconnect_github)
    return {"status": "success"}


# ---- Feature 26: import a repository from GitHub (no model involved)

@app.get("/api/github/repos")
async def github_repos():
    try:
        return await asyncio.to_thread(workspace.list_github_repos)
    except (workspace.GitHubError, workspace.WorkspaceError) as e:
        return JSONResponse({"detail": str(e)}, status_code=422)


class ClonePayload(BaseModel):
    full_name: str
    name: str


CLONE_JOBS: dict[str, dict] = {}
CLONE_TIMEOUT = 1800          # seconds


def _clone_view(job: dict) -> dict:
    return {k: job[k] for k in ("id", "full_name", "name", "state", "progress", "error", "started")}


def _kill_group(proc) -> None:
    """Kill git and its helpers, unless the process has already ended (its pid may be reused)."""
    import signal
    if proc is not None and proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _kill_all_clones() -> None:
    """At exit: no git keeps running on its own after Optimux stops (staging
    folders it leaves are removed by the next clone)."""
    for job in list(CLONE_JOBS.values()):
        _kill_group(job.get("proc"))


import atexit  # noqa: E402
atexit.register(_kill_all_clones)


async def _run_clone(job: dict) -> None:
    """git clone into the job's staging folder in the background, progress from
    its stderr; on success the folder is moved into place, otherwise removed.
    Nothing outside the staging folder is ever deleted."""
    tail: list[str] = []
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(*job["argv"], cwd=os.path.dirname(job["staging"]), env=workspace.clone_env(),
                                                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.PIPE, start_new_session=True)
        job["proc"] = proc
        if job["state"] == "cancelled":         # cancelled while starting
            _kill_group(proc)

        async def read_progress():
            buf = b""
            while True:
                chunk = await proc.stderr.read(1024)
                if not chunk:
                    break
                buf += chunk
                parts = buf.replace(b"\r", b"\n").split(b"\n")
                buf = parts.pop()
                for line in parts:
                    text = line.decode(errors="replace").strip()
                    if text:
                        job["progress"] = text[:200]
                        tail.append(text[:300])
                        del tail[:-15]
        try:
            await asyncio.wait_for(asyncio.gather(read_progress(), proc.wait()), CLONE_TIMEOUT)
        except asyncio.TimeoutError:
            job["error"] = f"Stopped after {CLONE_TIMEOUT // 60} minutes."
            _kill_group(proc)
            await proc.wait()
        if job["state"] == "cancelled":
            pass
        elif proc.returncode == 0 and not job["error"]:
            try:
                await asyncio.to_thread(workspace.finish_clone, job["staging"], job["path"])
                job["state"] = "done"
                project_records.unmark_deleted(job["name"])
            except workspace.WorkspaceError as e:
                job["state"], job["error"] = "failed", str(e)
        else:
            job["state"] = "failed"
            job["error"] = job["error"] or "\n".join(tail[-6:]) or f"git clone exited with {proc.returncode}."
    except asyncio.CancelledError:          # the server is stopping
        job["state"], job["error"] = "failed", "Optimux stopped during the clone."
        _kill_group(proc)
        raise
    except Exception as e:                  # e.g. git missing
        job["state"], job["error"] = "failed", str(e)
    finally:
        job["proc"] = None
        if job["state"] != "done":
            try:
                workspace.remove_staging(job["staging"])     # only ever the staging folder
            except Exception as e:
                job["error"] = (job["error"] or "") + f" (its temporary folder couldn't be removed: {e})"
        try:
            await _broadcast({"type": "projects_changed"})
        except BaseException:
            pass


@app.post("/api/github/clone")
async def github_clone(payload: ClonePayload):
    """Start cloning github.com/<full_name> into the Workspace as <name>; poll its state."""
    name = payload.name.strip()
    if any(j["state"] == "running" and j["name"] == name for j in CLONE_JOBS.values()):
        return JSONResponse({"detail": f"'{name}' is already being cloned."}, status_code=422)
    # Registered before anything is awaited, so a double click can't start two clones
    job = {"id": uuid.uuid4().hex, "full_name": payload.full_name.strip(), "name": name, "path": "", "staging": "",
           "argv": [], "state": "running", "progress": "Starting…", "error": "", "started": time.time(), "proc": None}
    CLONE_JOBS[job["id"]] = job
    try:
        running = {k for k, j in CLONE_JOBS.items() if j["state"] == "running"}
        await asyncio.to_thread(workspace.remove_stale_staging, running)
        job["argv"], job["staging"], job["path"] = await asyncio.to_thread(
            workspace.clone_command, job["full_name"], name, job["id"])
    except (workspace.WorkspaceError, workspace.GitHubError) as e:
        CLONE_JOBS.pop(job["id"], None)
        return JSONResponse({"detail": str(e)}, status_code=422)
    for old in [k for k, j in CLONE_JOBS.items() if j["state"] != "running"][:-10]:
        CLONE_JOBS.pop(old, None)           # keep the last few finished jobs
    job["task"] = asyncio.get_running_loop().create_task(_run_clone(job))
    return _clone_view(job)


@app.get("/api/github/clone/{job_id}")
async def github_clone_state(job_id: str):
    job = CLONE_JOBS.get(job_id)
    if not job:
        return JSONResponse({"detail": "No such clone."}, status_code=404)
    return _clone_view(job)


@app.delete("/api/github/clone/{job_id}")
async def github_clone_cancel(job_id: str):
    job = CLONE_JOBS.get(job_id)
    if not job:
        return JSONResponse({"detail": "No such clone."}, status_code=404)
    if job["state"] == "running":
        job["state"] = "cancelled"
        _kill_group(job.get("proc"))
        if job.get("task") is not None:
            await asyncio.wait({job["task"]}, timeout=30)
    return _clone_view(job)




# ------------------------------------------------------------------ session

HISTORY_CAP = 40                 # messages kept between turns
TOOL_OUTPUT_HISTORY_CHARS = 4000  # tool output kept in history (full output is used in-turn)
TIER_COMMANDS = {"/simple": "simple", "/complex": "complex"}
NEW_CHAT_COMMAND = "/new"


def new_session() -> dict:
    """Per-connection state: chat history, sticky tier and usage totals."""
    return {
        "id": uuid.uuid4().hex, "tokens": 0, "cost": 0.0, "cost_complete": True,
        "history": [], "tier": None,
        "tier_pinned": False,    # set by /simple or /complex: the Conductor no longer changes the tier
        "last_turn_ok": True,    # the previous turn finished normally (not stopped, failed or paused)
        "budget_notes": set(),   # (project, level, month) budget notes already shown
        "approval": None,        # Future resolved by the reader loop with the user's approval_response
        "current_stream": None,  # StreamResult of the call in flight (for partial text on stop)
        "chat_id": None,         # stored session (created on the first prompt of a chat)
        "recorder": chat_store.Recorder(),  # what the UI is sent, saved with the session
    }


def reset_chat(session: dict) -> None:
    session["chat_id"] = None    # the next prompt starts a new stored session
    session["history"] = []
    session["tier"] = None
    session["tier_pinned"] = False
    session["last_turn_ok"] = True


def parse_tier_command(text: str) -> tuple[str | None, str]:
    """'/complex refactor x' -> ('complex', 'refactor x')."""
    stripped = text.strip()
    head, _, rest = stripped.partition(" ")
    if head.lower() in TIER_COMMANDS:
        return TIER_COMMANDS[head.lower()], rest.strip()
    return None, text


def compact_history(messages: list) -> list:
    """Drop mid-conversation system nudges and truncate long tool output."""
    out = []
    for m in messages:
        if m.get("role") == "system":
            continue
        if "thinking_blocks" in m:
            # only needed within the tool loop that produced them
            m = {k: v for k, v in m.items() if k != "thinking_blocks"}
        if m.get("role") == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > TOOL_OUTPUT_HISTORY_CHARS:
            m = {**m, "content": m["content"][:TOOL_OUTPUT_HISTORY_CHARS] + "\n...[output truncated in history]"}
        out.append(m)
    return out


def trim_history(messages: list, cap: int = HISTORY_CAP) -> list:
    """Keep the last `cap` messages, starting at a user message so no tool
    result is left without the assistant tool call it answers."""
    trimmed = messages[-cap:] if len(messages) > cap else list(messages)
    while trimmed and trimmed[0].get("role") != "user":
        trimmed.pop(0)
    return trimmed


def is_anthropic(model: str) -> bool:
    m = model.lower()
    return m.startswith("anthropic/") or m.startswith("claude")


def current_workdir() -> str:
    """The active project folder ('' if the scope isn't configured)."""
    try:
        return get_active_cwd()
    except ScopeError:
        return ""


def build_messages(context: str, history: list, user_message: str | None, model: str, workdir: str = "",
                   skills: dict | None = None, active_skill: dict | None = None) -> list:
    """[system, *history, user]; with user_message=None the history already ends
    where the model should continue (e.g. with tool results). The system prompt
    lists the available skills and, when one is active, its instructions."""
    system_text = get_executor_prompt(context, workdir, workspace=not active_project(),
                                      sandboxed=sandbox_lib.enabled(get_settings()),
                                      skills_catalog=skills_lib.catalog_text(skills) if skills else "",
                                      active_skill=active_skill)
    if is_anthropic(model):
        # Anthropic only caches prompt prefixes that are explicitly marked
        system = {"role": "system", "content": [
            {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]}
    else:
        system = {"role": "system", "content": system_text}
    tail = [] if user_message is None else [{"role": "user", "content": user_message}]
    return [system, *history, *tail]


# ------------------------------------------------------------------ metering

async def meter(websocket: WebSocket, session: dict, role: str, model: str, usage, duration_ms: int | None = None) -> None:
    """Record one LLM call and push it, with session totals, to the UI."""
    entry = usage_meter.record(session.get("chat_id") or session["id"], role, model, usage, duration_ms,
                               project=get_settings().get("active_project", ""),
                               skill=(session.get("skill") or {}).get("name"))
    session["tokens"] += entry["prompt_tokens"] + entry["completion_tokens"]
    if entry["cost"] is None:
        session["cost_complete"] = False
    else:
        session["cost"] += entry["cost"]
    await send(websocket, "usage", **entry,
               session_tokens=session["tokens"],
               session_cost=session["cost"],
               session_cost_complete=session["cost_complete"])


async def send(websocket: WebSocket, kind: str, content=None, **extra) -> None:
    payload = {"type": kind, **extra}
    if content is not None:
        payload["content"] = content
    recorder = getattr(websocket, "optimux_recorder", None)
    if recorder is not None:
        recorder.add(payload)
    await websocket.send_text(json.dumps(payload))


# ------------------------------------------------------------------ provider errors

RETRY_DELAY = 2  # seconds before the single retry of a transient error


class StreamResult:
    def __init__(self):
        self.thinking = ""          # streamed reasoning, shown collapsed in the UI
        self.thinking_blocks = []   # Anthropic's signed thinking blocks, re-sent within a tool loop
        self.text = ""
        self.tool_calls = {}
        self.is_tool_call = False
        self.pending_text = False
        self.usage = None
        self.streamed = False


def prepare_messages(messages: list, model: str) -> list:
    """Provider-specific view of the messages: Anthropic gets cache
    breakpoints and keeps thinking blocks; others never see thinking blocks."""
    if is_anthropic(model):
        return with_cache_breakpoint(messages, model)
    if any("thinking_blocks" in m for m in messages):
        return [{k: v for k, v in m.items() if k != "thinking_blocks"} for m in messages]
    return messages


def with_cache_breakpoint(messages: list, model: str) -> list:
    """For Anthropic, also mark the newest user/tool message as a cache
    breakpoint (the system prompt is marked in build_messages). In an agent
    loop each call resends everything before it, so the next call can read
    almost the whole prompt from cache. Other providers cache automatically.
    Returns a copy; stored history is not changed."""
    if not is_anthropic(model):
        return messages
    for i in range(len(messages) - 1, 0, -1):
        if messages[i].get("role") in ("user", "tool") and messages[i].get("content"):
            marked = list(messages)
            marked[i] = {**messages[i], "cache_control": {"type": "ephemeral"}}
            return marked
    return messages


def skill_project_dir() -> str | None:
    """The active project's folder (for project skills), or None at the root."""
    return current_workdir() if active_project() else None


def current_skills() -> dict:
    try:
        return skills_lib.discover(skill_project_dir())
    except Exception as e:
        print(f"[Optimux] skills unavailable: {e}")
        return {}


def skills_mode() -> str:
    mode = get_settings().get("skills_mode", "auto")
    return mode if mode in skills_lib.GLOBAL_MODES else "auto"


def listed_skills(found: dict | None = None) -> dict:
    """Skills that go into prompts (auto skills, when skills_mode is auto)."""
    return skills_lib.listed(current_skills() if found is None else found, skills_mode())


def offered_tools(skills: dict | None = None) -> list:
    skills = listed_skills() if skills is None else skills
    return TOOLS + workspace.tools_for(active_project()) + ([skills_lib.LOAD_SKILL_TOOL] if skills else [])


async def stream_once(websocket: WebSocket, model: str, messages: list, r: StreamResult,
                      thinking: str | None = None) -> StreamResult:
    """One streamed completion into `r`. Text is forwarded to the UI as it
    arrives, except output that looks like a JSON tool call, which is held
    back. `r` is passed in so the caller can see what was streamed on error."""
    stream = await litellm.acompletion(
        model=model,
        messages=prepare_messages(messages, model),
        tools=offered_tools(),
        stream=True,
        stream_options={"include_usage": True},
        timeout=120,
        **litellm_key_kwargs(model),
        **({"reasoning_effort": thinking} if thinking else thinking_kwargs(model)),
    )
    async for chunk in stream:
        if getattr(chunk, "usage", None):
            r.usage = chunk.usage
        # The usage-only final chunk has no choices
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            r.thinking += reasoning
            await send(websocket, "thinking", reasoning)
        for block in getattr(delta, "thinking_blocks", None) or []:
            block = dict(block)
            # Partial blocks stream without a signature; the complete one carries it
            if block.get("type") == "redacted_thinking" or block.get("signature"):
                r.thinking_blocks.append(block)

        if getattr(delta, "tool_calls", None):
            r.is_tool_call = True
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in r.tool_calls:
                    r.tool_calls[idx] = {
                        "id": getattr(tc, "id", f"call_{idx}") or f"call_{idx}",
                        "type": "function",
                        "function": {"name": getattr(tc.function, "name", "") or "", "arguments": ""},
                    }
                if getattr(tc.function, "arguments", None):
                    r.tool_calls[idx]["function"]["arguments"] += tc.function.arguments

        if getattr(delta, "content", None):
            content = delta.content
            r.text += content
            if not r.text.lstrip().startswith("{"):
                if r.pending_text:
                    content = r.text
                    r.pending_text = False
                await send(websocket, "token", content)
                r.streamed = True
            else:
                r.pending_text = True
    return r


class BudgetExceeded(Exception):
    """The active project's monthly budget is used up and set to block."""

    def __init__(self, status: dict):
        super().__init__("budget reached")
        self.status = status


def active_project() -> str:
    return get_settings().get("active_project", "")


def budget_text(s: dict) -> str:
    unknown = f" ({s['calls_without_cost']} calls without a known price aren't counted)" if s["calls_without_cost"] else ""
    return (f"Budget for {s['project']}: ${s['spent']:.2f} of ${s['monthly_usd']:.2f} this month "
            f"({s['ratio'] * 100:.0f}%){unknown}.")


# Budget notes already shown, per (project, level, month): once per server run,
# not per connection, so reloading the page doesn't repeat them.
BUDGET_NOTES_SHOWN: set = set()


async def check_budget_before_request(websocket: WebSocket, session: dict) -> bool:
    """Refuse a new request when the project's budget blocks; otherwise send a
    one-time note per level (80%, 100% in warn mode). Returns True to proceed."""
    s = budgets.status(active_project())
    if not s:
        return True
    if s["state"] == "over" and s["on_limit"] == "block":
        await send(websocket, "budget", budget_text(s) + " New requests are blocked until you raise it.",
                   state="blocked", budget=s)
        return False
    if s["state"] in ("warning", "over"):
        key = (s["project"], s["state"], time.strftime("%Y-%m"))
        if key not in BUDGET_NOTES_SHOWN:
            BUDGET_NOTES_SHOWN.add(key)
            await send(websocket, "budget", budget_text(s), state=s["state"], budget=s)
    return True


async def call_model(websocket: WebSocket, session: dict, role: str, model: str, messages: list) -> StreamResult:
    """Stream one completion and meter it.

    A model in cooldown is not called (ModelUnavailable). A transient error is
    retried once, only if nothing was streamed yet (no duplicate text). Quota
    errors are not retried. Failures put the model in cooldown and raise
    ProviderFailure; other errors propagate unchanged.
    """
    blocked = budgets.blocks(active_project())
    if blocked:
        raise BudgetExceeded(blocked)  # the turn pauses before spending more
    reason = providers.cooldown(model)
    if reason:
        raise ModelUnavailable(model, reason)
    for attempt in (1, 2):
        r = StreamResult()
        session["current_stream"] = r
        started = time.monotonic()
        try:
            await stream_once(websocket, model, messages, r, (session.get("skill") or {}).get("thinking"))
        except asyncio.CancelledError:
            # Stopped by the user: tokens were still spent, so record an estimate
            duration = int((time.monotonic() - started) * 1000)
            await meter(websocket, session, role, model, usage_meter.estimate(model, messages, r.text), duration)
            raise
        except Exception as e:
            info = providers.classify(e)
            print(f"[Optimux] {model} call failed ({info.kind}): {e}")
            if info.kind == "transient" and attempt == 1 and not r.streamed:
                await send(websocket, "system", f"{model} {info.summary}, retrying...")
                await asyncio.sleep(RETRY_DELAY)
                continue
            if info.kind == "fatal":
                raise
            raise ProviderFailure(model, providers.record_failure(model, e), e) from e
        session["current_stream"] = None
        duration = int((time.monotonic() - started) * 1000)
        usage = r.usage if r.usage is not None else usage_meter.estimate(model, messages, r.text)
        await meter(websocket, session, role, model, usage, duration)
        return r


# ------------------------------------------------------------------ one turn

# ------------------------------------------------------------------ memory curator

CURATING: set = set()        # session ids being curated right now


def curator_enabled() -> bool:
    return bool(get_settings().get("memory_curator", True))


def curator_idle_seconds() -> int:
    try:
        return max(1, min(240, int(get_settings().get("curator_idle_minutes", 10)))) * 60
    except (TypeError, ValueError):
        return 600


async def curate_session(sid: str | None, websocket: WebSocket | None = None, force: bool = False,
                         full: bool = False) -> dict:
    """Update the chat's project memory from what happened since it was last curated.
    force runs even when automatic updates are off; full re-reads the whole chat (both are
    used by Update now in the dashboard, e.g. after an undo). Never raises: a failed curation only means memory isn't
    updated this time. Reasons are shown to the user as they are."""
    if not sid or (not force and not curator_enabled()):
        return {"changed": False, "reason": "Automatic updates are off."}
    if sid in CURATING:
        return {"changed": False, "reason": "An update from this chat is already running."}
    CURATING.add(sid)
    notified = False      # "memory_updating" was sent, so the page expects an outcome
    try:
        data = chat_store.get(sid)
        if data is None or not data["project"]:
            return {"changed": False, "reason": "Workspace chats have no project memory."}
        project = data["project"]
        done = 0 if full else curator.curated_turns(sid)
        if data["turns"] <= done:
            return {"changed": False, "reason": "Nothing new in the chat since the last update."}
        chat = curator.transcript(data["events"], skip_turns=done)
        if len(chat) < 200:                                         # e.g. just "Hello"
            curator.mark_curated(sid, data["turns"])
            return {"changed": False, "reason": "The chat is too short to learn from."}
        if budgets.blocks(project):
            return {"changed": False, "reason": "The project's budget is reached."}
        model = get_routing_config().get("simple_model", "")
        if not model or providers.cooldown(model):
            return {"changed": False, "reason": "The Simple model isn't available right now."}
        current = curator.read_memory(project)
        await notify(websocket, "memory_updating", project=project)
        notified = True
        started = time.monotonic()
        response = await litellm.acompletion(
            model=model, messages=[{"role": "user", "content": curator.build_prompt(project, current, chat)}],
            timeout=90, **litellm_key_kwargs(model), **thinking_kwargs(model))
        entry = usage_meter.record(sid, "curator", model, getattr(response, "usage", None),
                                   int((time.monotonic() - started) * 1000), project=project)
        cost = entry.get("cost")
        new, summary = curator.parse_reply(response.choices[0].message.content)
        curator.mark_curated(sid, data["turns"])
        if new is None:
            result = {"changed": False, "reason": "The chat had nothing durable to add.", "cost": cost}
        elif (problem := curator.acceptable(new)):
            print(f"[Optimux] memory curator result rejected for {project}: {problem}")
            result = {"changed": False, "reason": f"The result was rejected: {problem}.", "cost": cost}
        else:
            version = curator.write_memory(project, new, "curator", summary, sid, cost=cost)
            if version is None:
                result = {"changed": False, "reason": "The memory already says this.", "cost": cost}
            else:
                result = {"changed": True, "project": project, "summary": summary, "version_id": version,
                          "cost": cost}
        if result["changed"]:
            await notify(websocket, "memory_updated", project=project, summary=summary,
                         version_id=result["version_id"], cost=cost)
        else:
            await notify(websocket, "memory_unchanged", project=project, reason=result["reason"], cost=cost)
        return result
    except Exception as e:
        print(f"[Optimux] memory curator failed for session {sid}: {e}")
        if notified:
            await notify(websocket, "memory_unchanged", project=data["project"], reason="The update failed.",
                         cost=None)
        return {"changed": False, "reason": f"error: {e}"}
    finally:
        CURATING.discard(sid)


async def notify(websocket: WebSocket | None, kind: str, **extra) -> None:
    """Background status for the open page (memory updates). Not recorded in the chat's
    event log, and a closed page is ignored."""
    if websocket is None:
        return
    try:
        await websocket.send_text(json.dumps({"type": kind, **extra}))
    except Exception:
        pass


def schedule_curation(websocket: WebSocket | None, sid: str | None, delay: float = 0) -> asyncio.Task | None:
    """Curate in the background (optionally after a delay); never blocks the chat."""
    if not sid or not curator_enabled():
        return None

    async def later():
        if delay:
            await asyncio.sleep(delay)
        await curate_session(sid, websocket)

    task = asyncio.create_task(later())
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


BACKGROUND_TASKS: set = set()   # keeps background curations alive until they finish


def in_skill_folder(path: str) -> bool:
    """Scripts in skill folders may be run from any project without the outside-project warning."""
    real = os.path.realpath(os.path.expanduser(path))
    for _, root in skills_lib.roots(skill_project_dir()):
        root = os.path.realpath(root)
        if real == root or real.startswith(root + os.sep):
            return True
    return False


async def wait_for_answer(websocket: WebSocket, session: dict, tc_id: str, command: str, **extra) -> dict:
    """Show the approval box (or form) and wait for the user's answer (the reader
    loop resolves it). Returns the answer; approved only if answer["approved"] is True."""
    approval = asyncio.get_running_loop().create_future()
    session["approval"] = approval
    await send(websocket, "approval_request", command=command, tool_call_id=tc_id,
               sandboxed=sandbox_lib.enabled(get_settings()), **extra)
    try:
        answer = await approval
    finally:
        session["approval"] = None
    if answer.get("type") != "approval_response" or answer.get("approved") is not True:
        return {"approved": False}
    return answer


async def ask_approval(websocket: WebSocket, session: dict, tc_id: str, command: str, **extra) -> bool:
    return (await wait_for_answer(websocket, session, tc_id, command, **extra))["approved"] is True


def create_problem(form: dict) -> str:
    """Why this create form can't be carried out, or ''."""
    try:
        workspace.check_create(form["name"], form["template"], form["git_init"], form["github"])
    except workspace.WorkspaceError as e:
        return str(e)
    if form["github"]:
        gh = workspace.github_status()
        if not gh["ready"]:
            return gh["reason"]
    return ""


async def ask_create_form(websocket: WebSocket, session: dict, tc_id: str, args: dict) -> dict | None:
    """Feature 25: the approval for create_project is a form filled in with the
    model's values; the user changes anything and confirms. The final values are
    checked again here (the form is only a convenience). None = cancelled."""
    form = workspace.form_values(args)
    error = ""
    try:
        workspace.normalize_create(form)
        if form["name"]:
            error = await asyncio.to_thread(create_problem, form)
    except workspace.WorkspaceError as e:
        error = str(e)
    while True:
        github = await asyncio.to_thread(workspace.github_status)
        answer = await wait_for_answer(
            websocket, session, tc_id, workspace.describe_create(form["name"] or "(no name yet)", form["template"],
                                                                 form["git_init"], form["github"], form["description"]),
            action="create_project", always_ask=True, form=form, error=error, github=github,
            active_project=active_project())
        if not answer["approved"]:
            return None
        if isinstance(answer.get("form"), dict):
            form = workspace.form_values(answer["form"])      # shown again as typed if refused
        try:
            form = workspace.normalize_create(form)
        except workspace.WorkspaceError as e:
            error = str(e)
            continue
        error = await asyncio.to_thread(create_problem, form)
        if not error:
            return form


def create_report(result: dict) -> str:
    report = "\n".join(f"{'✓' if s['ok'] else '✗'} {s['step']}" + (f"\n  {s['output']}" if s["output"] and not s["ok"] else "")
                       for s in result["steps"])
    if result["github_url"]:
        report += f"\nGitHub: {result['github_url']}"
    return report


def workspace_tool_names() -> set:
    return {t["function"]["name"] for t in workspace.WORKSPACE_TOOLS}


async def set_active_project(websocket: WebSocket, name: str) -> None:
    """Change the active project and tell the UI (header, costs, memory follow)."""
    settings = get_settings()
    save_settings(SettingsPayload(**{**settings, "active_project": name}))
    await send(websocket, "project_switched", project=name)


async def run_workspace_tool(websocket: WebSocket, session: dict, name: str, args_str: str, tc_id: str) -> dict:
    """list_projects / create_project / switch_project / archive_project.
    Anything with side effects outside settings needs approval, even with
    auto-approve on (always_ask), because it creates or moves folders and may
    create a GitHub repository."""
    try:
        args = json.loads(args_str or "{}") or {}
    except Exception:
        args = {}
    if not isinstance(args, dict):
        args = {}
    try:
        if name == "list_projects":
            return workspace.list_projects()

        if name == "switch_project":
            target = workspace.check_switch(str(args.get("name", "")))
            await set_active_project(websocket, target)
            where = f"project '{target}'" if target else "the Workspace (no project)"
            await send(websocket, "system", f"Switched to {where}.")
            return {"ok": True, "active_project": target or None,
                    "note": "Commands now run there and its project memory applies from the next request."}

        if name == "create_project":
            form = await ask_create_form(websocket, session, tc_id, args)
            if form is None:
                await send(websocket, "system", "Project creation cancelled.")
                return {"ok": False, "declined": True,
                        "note": "The user cancelled the form. Don't retry; ask what they'd like to change."}
            pname = form["name"]
            label = workspace.describe_create(pname, form["template"], form["git_init"], form["github"], form["description"])
            await send(websocket, "command_start", command=label)
            try:
                result = await asyncio.to_thread(workspace.create_project, pname, template=form["template"],
                                                 git_init=form["git_init"], github=form["github"],
                                                 description=form["description"])
            except workspace.WorkspaceError as e:    # e.g. created meanwhile from another tab
                await send(websocket, "command_output", command=label, output=f"✗ {e}", exit_code=1)
                return {"ok": False, "error": str(e)}
            await send(websocket, "command_output", command=label, output=create_report(result),
                       exit_code=0 if result["ok"] else 1)
            await send(websocket, "projects_changed")
            await send(websocket, "project_created", project=pname, ok=result["ok"], github_url=result["github_url"])
            result["form"] = {k: form[k] for k in ("name", "template", "git_init", "github", "description")}
            previous = active_project()
            if form["switch"] and previous != pname and os.path.isdir(result["path"]):
                await set_active_project(websocket, pname)
                await send(websocket, "system", f"Switched to project '{pname}'.")
                result["note"] = (f"Created with the values the user confirmed (see form) and switched to '{pname}'. "
                                  "The chat moves to the new project when this reply ends: finish with one short sentence.")
            elif previous != pname:
                result["note"] = (f"Created with the values the user confirmed (see form). The user chose to stay in "
                                  f"'{previous or 'the Workspace'}'; don't offer to switch.")
            return result

        if name == "archive_project":
            pname = workspace.check_archive(str(args.get("name", "")))
            label = f"Archive project '{pname}' (move its folder to .archive/; chats and costs are kept)"
            if not await ask_approval(websocket, session, tc_id, label, action="archive_project", always_ask=True):
                await send(websocket, "system", "Archiving declined.")
                return {"ok": False, "declined": True, "note": "The user declined. Don't retry."}
            await send(websocket, "command_start", command=label)
            result = await asyncio.to_thread(workspace.archive_project, pname)
            await send(websocket, "command_output", command=label, output=f"Moved to {result['archived_to']}", exit_code=0)
            if active_project() == pname:
                await set_active_project(websocket, "")
            await send(websocket, "projects_changed")
            return result
    except workspace.WorkspaceError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"Unknown workspace tool {name}"}


def with_thinking(message: dict, r: StreamResult) -> dict:
    """Keep Anthropic's signed thinking blocks on the assistant message: with
    extended thinking, Claude requires them back on the next call of a tool loop."""
    if r.thinking_blocks:
        message["thinking_blocks"] = r.thinking_blocks
    return message


async def run_turn(websocket: WebSocket, session: dict, model: str, role: str, messages: list,
                   max_loops: int | None = None) -> str:
    """Agent loop for one user request. Mutates `messages`.

    Returns 'ok', 'aborted' (model repeated the same tool call) or
    'loop_limit'. Provider errors propagate to the caller.
    """
    last_tool_calls_str = ""
    max_loops = max_loops or (session.get("skill") or {}).get("max_steps") or max_steps_for(role)
    for loop_count in range(1, max_loops + 1):
        r = await call_model(websocket, session, role, model, messages)
        full_text_response = r.text
        tool_calls_accumulator = r.tool_calls
        is_tool_call = r.is_tool_call

        # Some models print tool calls as JSON text instead of real tool calls
        if not is_tool_call and r.pending_text:
            text_call = parse_text_tool_call(full_text_response)
            if text_call and text_call["function"]["name"] in KNOWN_TOOLS:
                is_tool_call = True
                tool_calls_accumulator = {0: text_call}
                full_text_response = ""
            elif text_call:
                bad_name = text_call["function"]["name"]
                messages.append({"role": "assistant", "content": full_text_response})
                messages.append({"role": "user", "content": (
                    f"There is no tool named '{bad_name}'. Do not output JSON. "
                    "Answer my previous message directly in plain language, using the CURRENT MEMORY & CONTEXT in your instructions."
                )})
                continue
            else:
                if "stdout" in full_text_response and "exit_code" in full_text_response:
                    try:
                        parsed = json.loads(full_text_response)
                        if "stdout" in parsed:
                            full_text_response = parsed["stdout"]
                    except Exception:
                        pass
                await send(websocket, "token", full_text_response)

        if not is_tool_call:
            if not full_text_response.strip() and loop_count == 1:
                messages.append({"role": "system", "content": "Please provide a direct conversational response."})
                continue
            messages.append(with_thinking({"role": "assistant", "content": full_text_response}, r))
            return "ok"

        tool_calls_list = [tool_calls_accumulator[i] for i in sorted(tool_calls_accumulator.keys())]

        # Anti-loop check
        current_tool_calls_str = json.dumps([{k: v for k, v in t.items() if k != "id"} for t in tool_calls_list])
        if current_tool_calls_str == last_tool_calls_str:
            await send(websocket, "system", "Execution finished (LLM loop gracefully aborted).")
            return "aborted"
        last_tool_calls_str = current_tool_calls_str

        messages.append(with_thinking(
            {"role": "assistant", "content": full_text_response or None, "tool_calls": tool_calls_list}, r))

        for tool_call_dict in tool_calls_list:
            tc_id = tool_call_dict["id"]
            tc_name = tool_call_dict["function"]["name"]
            tc_args_str = tool_call_dict["function"]["arguments"]

            if tc_name == "load_skill":
                try:
                    sname = json.loads(tc_args_str or "{}").get("name", "")
                    if session.get("skill") and session["skill"]["name"] == sname:
                        # Already in the system prompt: don't pay for its instructions twice
                        raise skills_lib.SkillError(f"'{sname}' is already loaded (see ACTIVE SKILL in your instructions); "
                                                    "follow it without calling load_skill.")
                    loaded = skills_lib.load(sname, skill_project_dir())
                    session["skill"] = loaded
                    await send(websocket, "system", f"Skill loaded: {loaded['name']}", skill=loaded["name"])
                    result = {"ok": True, **{k: loaded[k] for k in ("name", "instructions", "files", "folder")}}
                except skills_lib.SkillError as e:
                    result = {"ok": False, "error": str(e)}
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": tc_name, "content": json.dumps(result)})
            elif tc_name in workspace_tool_names():
                result = await run_workspace_tool(websocket, session, tc_name, tc_args_str, tc_id)
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": tc_name, "content": json.dumps(result)})
            elif tc_name == "execute_bash":
                try:
                    cmd = json.loads(tc_args_str).get("command", "")
                except Exception:
                    cmd = ""

                workdir = current_workdir()
                outside = [p for p in (paths_outside_scope(cmd, workdir) if workdir else [])
                           if not in_skill_folder(p)]
                if await ask_approval(websocket, session, tc_id, cmd, outside_paths=outside, workdir=workdir):
                    await send(websocket, "command_start", command=cmd)
                    exec_result = await run_shell_command(cmd)
                    out = exec_result["stdout"] or exec_result["stderr"] or "(no output)"
                    log_command_to_session(cmd, exec_result["exit_code"])
                    await send(websocket, "command_output", command=cmd, output=out, exit_code=exec_result["exit_code"])
                    messages.append({"role": "tool", "tool_call_id": tc_id, "name": tc_name, "content": json.dumps(exec_result)})
                else:
                    await send(websocket, "system", f"Command '{cmd}' rejected.")
                    messages.append({"role": "tool", "tool_call_id": tc_id, "name": tc_name, "content": "The user declined to run this command. Do not retry it. Briefly tell the user what you intended and ask how they'd like to proceed."})

            elif tc_name == "update_markdown_memory":
                try:
                    args = json.loads(tc_args_str)
                    file_type = args.get("file_type", "")
                    content = args.get("content", "")
                except Exception:
                    file_type, content = "error", "error parsing json"
                result = update_memory(file_type, content)
                await send(websocket, "system", f"Memory Saved: {file_type}.md")
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": tc_name, "content": result})

            else:
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": tc_name, "content": (
                    f"Error: no tool named '{tc_name}' exists. Answer directly using the CURRENT PROJECT CONTEXT."
                )})
    return "loop_limit"


def fallback_for(role: str, model: str, routing: dict) -> tuple[str, str] | None:
    """Simple escalates to complex; complex falls back to simple (only for
    provider errors). Explicit overrides never switch. Models in cooldown
    are skipped."""
    other = {"simple": "complex", "complex": "simple"}.get(role)
    if not other:
        return None
    other_model = routing.get(f"{other}_model", "")
    if not other_model or other_model == model or not providers.is_available(other_model):
        return None
    return other, other_model


def unavailable_note(model: str) -> str:
    reason = providers.cooldown(model)
    return f"{model} {reason.summary}" if reason else f"{model} unavailable"


async def run_with_fallback(websocket: WebSocket, session: dict, routing: dict,
                            model: str, role: str, user_message: str, prior: list | None = None) -> None:
    """Run a turn; on failure escalate (simple -> complex) or fall back
    (complex -> simple on provider errors). Then commit the turn to history.

    Only difficulty signals (step limit, repeated tool call) make the chat
    stay on complex; a provider outage says nothing about the task.
    """
    if prior is None:
        messages = build_messages(get_memory_context(), session["history"], user_message, model, current_workdir(),
                                  session.get("skills"), session.get("skill"))
    else:
        # Continue an existing conversation that already ends with the request (and its tool results)
        messages = build_messages(get_memory_context(), prior, None, model, current_workdir(),
                                  session.get("skills"), session.get("skill"))
    try:
        await _run_with_fallback(websocket, session, routing, model, role, messages)
    except asyncio.CancelledError:
        session["last_turn_ok"] = False
        # Stopped by the user: keep what happened so the next turn has context
        stream = session.get("current_stream")
        close_stopped_turn(messages, stream.text if stream else "")
        session["current_stream"] = None
        session["history"] = trim_history(compact_history(messages[1:]))
        raise


STOPPED_NOTE = "[Stopped by the user.]"


async def notify_budget_pause(websocket: WebSocket, s: dict) -> None:
    await send(websocket, "budget", budget_text(s) + " Paused before the next model call; raise the budget to continue.",
               state="paused", budget=s)


async def notify_step_limit(websocket: WebSocket, role: str) -> None:
    """The turn used all its steps and there is nowhere to escalate: say so,
    and let the UI offer Continue (a normal follow-up prompt)."""
    steps = max_steps_for(role)
    await send(websocket, "step_limit",
               f"Paused after {steps} steps, the limit for this tier. Press Continue to keep going, "
               "or change the limit in Settings > Scope.", steps=steps)


def close_stopped_turn(messages: list, partial_text: str) -> None:
    """Make an interrupted turn valid history: answer any tool calls left
    without a result, then end with an assistant message noting the stop."""
    last_call = max((i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")),
                    default=None)
    if last_call is not None:
        answered = {m.get("tool_call_id") for m in messages[last_call + 1:] if m.get("role") == "tool"}
        for tc in messages[last_call]["tool_calls"]:
            if tc["id"] not in answered:
                messages.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                                 "content": "Cancelled: the user stopped the request before this ran."})
    text = partial_text.strip()
    messages.append({"role": "assistant", "content": f"{text}\n\n{STOPPED_NOTE}" if text else STOPPED_NOTE})


async def _run_with_fallback(websocket: WebSocket, session: dict, routing: dict,
                             model: str, role: str, messages: list) -> None:
    reason, provider_problem = None, False
    session["last_turn_ok"] = False  # set True only when the turn (or its fallback) finishes normally
    try:
        outcome = await run_turn(websocket, session, model, role, messages)
        session["last_turn_ok"] = outcome == "ok"
        if outcome == "loop_limit":
            reason = f"step limit ({max_steps_for(role)}) reached"
        elif outcome == "aborted":
            reason = "model repeated the same tool call"
    except (ProviderFailure, ModelUnavailable) as e:
        provider_problem = True
        reason = f"{model} {e.info.summary}"
    except BudgetExceeded as e:
        await notify_budget_pause(websocket, e.status)
        session["history"] = trim_history(compact_history(messages[1:]))
        return
    except Exception as e:
        traceback.print_exc()
        await send(websocket, "error", f"{model}: {providers.classify(e).summary}")
        return  # nothing usable happened; keep history as it was

    fallback = None
    if reason and (role == "simple" or provider_problem):
        fallback = fallback_for(role, model, routing)

    if fallback:
        fb_role, fb_model = fallback
        verb = "Escalated" if fb_role == "complex" else "Fell back"
        await send(websocket, "system", f"{verb} to {fb_model}: {reason}")
        if fb_role == "complex" and not provider_problem:
            session["tier"] = "complex"  # the task proved hard; stay on complex for this chat
        try:
            # Continue from the same messages so work already done (and approved) is kept
            fb_outcome = await run_turn(websocket, session, fb_model, fb_role, messages)
            session["last_turn_ok"] = fb_outcome == "ok"
            if fb_outcome == "loop_limit":
                await notify_step_limit(websocket, fb_role)
        except (ProviderFailure, ModelUnavailable) as e:
            await send(websocket, "error", f"{reason}. Fallback {fb_model} also failed: {e.info.summary}")
        except BudgetExceeded as e:
            await notify_budget_pause(websocket, e.status)
        except Exception as e:
            traceback.print_exc()
            await send(websocket, "error", f"{fb_model}: {providers.classify(e).summary}")
    elif reason and reason.startswith("step limit"):
        await notify_step_limit(websocket, role)
    elif provider_problem:
        other = {"simple": "complex", "complex": "simple"}.get(role)
        other_model = routing.get(f"{other}_model", "") if other else ""
        if role == "override":
            hint = "Pick another model or switch back to Auto-Router."
        elif other_model and other_model != model:
            hint = f"Fallback {unavailable_note(other_model)}."
        else:
            hint = "No fallback model is configured."
        await send(websocket, "error", f"{reason}. {hint}")

    session["history"] = trim_history(compact_history(messages[1:]))


# ------------------------------------------------------------------ request handling

async def resolve_tier(websocket: WebSocket, session: dict, user_message: str) -> str:
    """Routing between tasks, never within one.

    The Conductor checks every new message (unless the tier is pinned with
    /simple or /complex, or the Conductor is Off). It may move the chat up to
    complex at any time. It moves it back down to simple only when the
    previous turn finished normally, so an unfinished task (stopped, paused
    at the step limit, failed) is never handed to a weaker model halfway.
    """
    if session["tier_pinned"]:
        return session["tier"]
    result = await route_model_with_usage(user_message, session.get("skills") or None)
    tier, conductor_model, conductor_usage, duration = result[:4]
    session["picked_skill"] = result[4] if len(result) > 4 else None
    if conductor_usage is not None:
        await meter(websocket, session, "conductor", conductor_model, conductor_usage, duration)
    if conductor_model == "off" and session["tier"] is not None:
        return session["tier"]  # Conductor Off: only escalation changes the tier
    if tier == "complex":
        if session["tier"] == "simple":
            await send(websocket, "system", "Conductor: this request looks complex; moving the chat to the complex tier.")
        session["tier"] = "complex"
    elif session["tier"] == "complex":
        if session["last_turn_ok"]:
            await send(websocket, "system", "Conductor: this looks like a new, simpler request; moving the chat back to the simple tier.")
            session["tier"] = "simple"
    else:
        session["tier"] = "simple"
    return session["tier"]


async def handle_prompt(websocket: WebSocket, session: dict, user_message: str, model_override: str) -> None:
    if user_message.strip().lower() == NEW_CHAT_COMMAND:
        reset_chat(session)
        await send(websocket, "chat_reset")
        await send(websocket, "system", "New chat started.")
        return

    if not await check_budget_before_request(websocket, session):
        return
    if ensure_chat(session, user_message):
        await send(websocket, "session_started",
                   session={"id": session["chat_id"], "title": " ".join(user_message.split())[:chat_store.TITLE_LENGTH],
                            "project": active_project()})

    found = current_skills()
    session["skills"] = listed_skills(found)
    session["skill"] = None          # the skill active for this request, if any
    session["picked_skill"] = None

    tier_cmd, user_message = parse_tier_command(user_message)
    if tier_cmd:
        session["tier"] = tier_cmd
        session["tier_pinned"] = True
        if not user_message:
            await send(websocket, "system", f"Using the {tier_cmd} tier for this chat.")
            return

    # /<skill> or /skill <name>: run a skill on request (any mode; this is how manual skills run)
    asked_skill, rest = skills_lib.parse_command(user_message, found)
    if asked_skill:
        if skills_mode() == "off":
            await send(websocket, "error", f"Skills are turned off, so /{asked_skill} can't run. "
                                           "Turn them on in Dashboard > Skills.")
            return
        user_message = rest or f"Run the {asked_skill} skill."

    routing = get_routing_config()
    if model_override and model_override != "auto":
        model, role = model_override, "override"
    else:
        role = await resolve_tier(websocket, session, user_message)
        model = routing.get(f"{role}_model", "")
        if not model:
            await send(websocket, "error", f"Routing role '{role}_model' is not configured. Please assign it in Settings > Routing.")
            return
        if not providers.is_available(model):
            # Don't spend a request on a model we know is down; this switch is not sticky
            alt = fallback_for(role, model, routing)
            if alt:
                await send(websocket, "system", f"Skipping {unavailable_note(model)}; using {alt[1]}.")
                role, model = alt
    picked = asked_skill or session.get("picked_skill")
    if picked and (role != "override" or asked_skill):
        try:
            session["skill"] = skills_lib.load(picked, skill_project_dir())
            skill_tier = session["skill"].get("tier")
            note = f"Skill: {picked}" + (" (you asked for it)" if asked_skill else "")
            if skill_tier and skill_tier != role and not session["tier_pinned"] and routing.get(f"{skill_tier}_model"):
                role, model = skill_tier, routing[f"{skill_tier}_model"]
                note += f" (uses the {skill_tier} tier)"
            await send(websocket, "system", note, skill=picked)
        except skills_lib.SkillError:
            session["skill"] = None
    await send(websocket, "system", f"Routed to: {model} ({role})", tier=role, model=model)
    await run_with_fallback(websocket, session, routing, model, role, user_message)


def ensure_chat(session: dict, first_prompt: str) -> bool:
    """Create the stored session for this chat if it doesn't exist yet.
    Returns True when a new one was created."""
    if session["chat_id"] and not chat_store.exists(session["chat_id"]):
        session["chat_id"] = None  # deleted from History while open
    if not session["chat_id"]:
        session["chat_id"] = chat_store.create(active_project(), first_prompt)
        return True
    return False


def save_chat(session: dict, turns_added: int) -> None:
    """Store this request's events and the model-facing state with the session."""
    sid = session["chat_id"]
    if sid and not chat_store.exists(sid):
        sid = session["chat_id"] = None
    session["recorder"].flush(sid)
    if sid:
        chat_store.save_state(sid, session["history"], session["tier"], session["tier_pinned"],
                              session["last_turn_ok"], turns_added)


def missing_project_reason(name: str) -> str | None:
    """None when the project's folder is in the Workspace; otherwise why it isn't
    ("archived" or "missing": deleted, renamed or moved outside Optimux)."""
    if not name:
        return None
    try:
        resolve_project_dir(get_settings().get("projects_root", ""), name)
        return None
    except (ScopeError, ValidationError):
        pass
    try:
        if any(a.get("name") == name for a in workspace.list_archived()):
            return "archived"
    except Exception:
        pass
    return "missing"


def last_open_chat() -> str | None:
    """The most recent chat, in any project, whose project folder is still in the
    Workspace (chats of archived or deleted projects are skipped)."""
    for s in chat_store.list_sessions(None, False, "", limit=100):
        if missing_project_reason(s["project"]) is None:
            return s["id"]
    return None


def switch_active_project(name: str) -> bool:
    """Make name ("" = the Workspace) the active project. True when it changed."""
    if active_project() == name:
        return False
    save_settings(SettingsPayload(**{**get_settings(), "active_project": name}))
    return True


async def handle_open_session(websocket: WebSocket, session: dict, sid: str) -> None:
    """Load a stored session into this connection and send its events for replay.
    sid "latest" opens the active project's most recent session, if any; "last" opens
    the most recent chat in any project (the logo), or the Workspace when there is none."""
    switched = False
    if sid == "latest":
        sid = chat_store.latest(active_project())
    elif sid == "last":
        sid = last_open_chat()
        target = chat_store.get(sid)["project"] if sid else ""
        try:
            switched = switch_active_project(target)
        except Exception as e:
            await send(websocket, "error", f"Can't switch to {target or 'the Workspace'}: {e}")
    data = chat_store.get(sid) if sid else None
    if data is None:
        reset_chat(session)  # nothing to open: start clean, as the UI does
        await send(websocket, "session_loaded", session=None, events=[], active_project_changed=switched)
        return
    summary = {k: data[k] for k in ("id", "project", "title", "created", "updated", "turns", "tier", "cost",
                                    "calls_without_cost", "archived")}
    reason = missing_project_reason(data["project"]) if data["project"] != active_project() else None
    if reason:
        # Its project folder is gone (archived or deleted): show the chat to read, but
        # don't switch to that project or continue the chat, since commands couldn't run there.
        reset_chat(session)
        await send(websocket, "session_loaded", session=summary, events=data["events"],
                   read_only=True, reason=reason)
        return
    if data["project"] and data["project"] != active_project():
        # Commands must run in the session's own project
        try:
            settings = get_settings()
            save_settings(SettingsPayload(**{**settings, "active_project": data["project"]}))
        except Exception as e:
            await send(websocket, "error", f"Can't switch to project {data['project']}: {e}")
    reset_chat(session)
    session.update({
        "chat_id": data["id"], "history": data["history"], "tier": data["tier"],
        "tier_pinned": data["tier_pinned"], "last_turn_ok": data["last_turn_ok"],
    })
    session["budget_notes"] = set()
    await send(websocket, "session_loaded", session=summary, events=data["events"], active_project_changed=switched)


async def handle_escalate(websocket: WebSocket, session: dict) -> None:
    """Re-run the last user request on the complex tier."""
    if not await check_budget_before_request(websocket, session):
        return
    session["skills"] = listed_skills()  # keep session["skill"]: escalating continues the same task
    history = session["history"]
    last_user = max((i for i, m in enumerate(history) if m.get("role") == "user"), default=None)
    routing = get_routing_config()
    model = routing.get("complex_model", "")
    if last_user is None or not model:
        await send(websocket, "system", "Nothing to escalate." if last_user is None else "No Complex Executor is configured.")
        return
    user_message = history[last_user]["content"]
    # Keep the commands already run and their output, so the stronger model
    # doesn't repeat them; drop only the weaker model's written answer.
    work = [m for m in history[last_user + 1:] if m.get("tool_calls") or m.get("role") == "tool"]
    prior = history[:last_user + 1] + work
    session["history"] = history[:last_user]
    session["tier"] = "complex"
    reused = sum(1 for m in work if m.get("role") == "tool")
    note = f"Escalated to: {model} (complex)" + (f", reusing {reused} command result{'s' if reused != 1 else ''}" if reused else "")
    await send(websocket, "system", note, tier="complex", model=model)
    await run_with_fallback(websocket, session, routing, model, "complex", user_message, prior=prior)


KNOWN_TOOLS = {t["function"]["name"] for t in TOOLS + workspace.WORKSPACE_TOOLS + [skills_lib.LOAD_SKILL_TOOL]}


def parse_text_tool_call(text: str):
    """Return a tool-call dict if the model printed a call like {"name": ..., "arguments": {...}}."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "name" not in data:
        return None
    args = data.get("arguments", data.get("parameters", {}))
    return {
        "id": "call_text_0",
        "type": "function",
        "function": {"name": str(data["name"]), "arguments": args if isinstance(args, str) else json.dumps(args)},
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Origin check before accept() -> rejected browsers get HTTP 403
    if not origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        await websocket.close(code=1008)
        return
    # Browsers are signed in with the session cookie; scripts send the access
    # token as the first message (kept out of the URL and the access log).
    login_id = auth.check_session(websocket.cookies.get(auth.COOKIE))
    await websocket.accept()
    if not login_id:
        ip = client_ip(websocket)
        if auth.retry_after(ip):
            await websocket.close(code=4429)
            return
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
        except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
            await websocket.close(code=4401)
            return
        if not isinstance(first, dict) or first.get("type") != "auth" or not check_token(first.get("token")):
            auth.failed(ip)
            await websocket.send_text(json.dumps({"type": "error", "content": "Unauthorized"}))
            await websocket.close(code=4401)
            return
    else:
        LIVE_SOCKETS.setdefault(login_id, set()).add(websocket)
        ensure_workspace_watcher()
    await websocket.send_text(json.dumps({"type": "auth_ok"}))

    session = new_session()
    websocket.optimux_recorder = session["recorder"]  # send() records what this chat shows
    current: asyncio.Task | None = None

    async def run_job(payload: dict) -> None:
        project = active_project()
        BUSY_PROJECTS[project] = BUSY_PROJECTS.get(project, 0) + 1
        try:
            await _run_job(payload)
        finally:
            BUSY_PROJECTS[project] -= 1
            if not BUSY_PROJECTS[project]:
                BUSY_PROJECTS.pop(project, None)

    async def _run_job(payload: dict) -> None:
        """One request, run as a task so the reader loop stays free for stop
        and approval messages. Always ends with `done`."""
        kind = payload.get("type")
        recorder = session["recorder"]
        recorder.pending = []
        turns_added = 0
        try:
            if kind in ("new_chat", "open_session") and session["chat_id"]:
                schedule_curation(websocket, session["chat_id"])   # the chat being left is finished
            if kind == "new_chat":
                reset_chat(session)
                await send(websocket, "chat_reset")
            elif kind == "open_session":
                await handle_open_session(websocket, session, str(payload.get("id", "latest")))
            elif kind == "escalate":
                recorder.add({"type": "escalate"})
                turns_added = 1
                await handle_escalate(websocket, session)
            else:
                prompt = payload.get("prompt", "")
                if prompt.strip().lower() != NEW_CHAT_COMMAND:
                    recorder.add({"type": "prompt", "content": prompt})
                    turns_added = 1
                model_override = payload.get("model", payload.get("model_override", "auto"))
                await handle_prompt(websocket, session, prompt, model_override)
        except asyncio.CancelledError:
            await send(websocket, "stopped")
            await send(websocket, "system", "Stopped.")
        except WebSocketDisconnect:
            return
        except Exception as e:
            traceback.print_exc()
            await send(websocket, "error", providers.classify(e).summary)
        if kind not in ("new_chat", "open_session"):
            try:
                save_chat(session, turns_added)
            except Exception as e:
                print(f"[Optimux] saving the session failed: {e}")
            # Idle timer: curate if nothing else happens in this chat for a while
            if session.get("idle_task") is not None:
                session["idle_task"].cancel()
            session["idle_task"] = schedule_curation(websocket, session["chat_id"], curator_idle_seconds())
        try:
            await send(websocket, "done")
        except Exception:
            pass  # socket already closed

    try:
        while True:
            payload = json.loads(await websocket.receive_text())
            # The sign-in may have expired or been ended while the chat was open
            if login_id and not auth.session_alive(login_id):
                await websocket.close(code=4401)
                break
            kind = payload.get("type")
            busy = current is not None and not current.done()

            if kind == "stop":
                if busy:
                    current.cancel()
                else:
                    await send(websocket, "done")  # nothing running; just release the UI
                continue
            if kind == "approval_response":
                approval = session.get("approval")
                if approval is not None and not approval.done():
                    approval.set_result(payload)
                continue  # otherwise a stray click after the turn ended
            if kind not in ("new_chat", "escalate", "open_session") and not payload.get("prompt", ""):
                continue
            if busy:
                await send(websocket, "system", "Still working on the previous request. Stop it first.")
                continue
            current = asyncio.create_task(run_job(payload))
    except WebSocketDisconnect:
        print("Optimux client disconnected.")
    finally:
        if current is not None and not current.done():
            current.cancel()  # also kills a running shell command
        if session.get("idle_task") is not None:
            session["idle_task"].cancel()
        schedule_curation(None, session.get("chat_id"))  # page closed: the chat is finished
        if login_id:
            LIVE_SOCKETS.get(login_id, set()).discard(websocket)

GUIDE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "GUIDE.md")


@app.get("/api/guide")
async def get_guide():
    """The user guide (docs/GUIDE.md), shown in Dashboard > Guide (Feature 20)."""
    try:
        with open(GUIDE_PATH, encoding="utf-8") as f:
            return {"content": f.read()}
    except FileNotFoundError:
        raise HTTPException(404, "docs/GUIDE.md is missing.")


@app.get("/")
async def get():
    with open(os.path.join(REPO_DIR, "index.html"), "r") as f:
        return HTMLResponse(f.read())
