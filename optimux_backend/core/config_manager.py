import os
import re
import json
from pydantic import BaseModel
from dotenv import load_dotenv

from optimux_backend.core.security import (
    ValidationError, resolve_project_dir, resolve_scope_root, validate_env_key, validate_env_value,
)

from optimux_backend.core import paths

# secrets.env is the single source of truth for keys managed in the UI, so it
# wins over variables already exported in the shell that starts the server.
# (A repo .env is only read if the move to secrets.env hasn't happened yet.)
SECRETS_FILE = paths.SECRETS_FILE
load_dotenv(SECRETS_FILE if os.path.exists(SECRETS_FILE) else ".env", override=True)

MODELS_FILE = paths.MODELS_FILE
ROUTING_FILE = paths.ROUTING_FILE
SETTINGS_FILE = paths.SETTINGS_FILE

# Seeded into .optimux/models.json on first run only; existing installs keep their list.
LEGACY_SUPPORTED_MODELS = {
    "gemini/gemini-3.6-flash": {
        "name": "Gemini 3.6 Flash",
        "env_key": "GEMINI_API_KEY",
        "instructions": "Get your API key at https://aistudio.google.com/apikey. Paste it below to configure."
    },
    "anthropic/claude-haiku-4-5-20251001": {
        "name": "Claude Haiku 4.5",
        "env_key": "ANTHROPIC_API_KEY",
        "instructions": "Create an API key (sk-ant-...) at https://console.anthropic.com/. Paste it below to configure."
    },
    "anthropic/claude-sonnet-5-5": {
        "name": "Claude Sonnet 5.5",
        "env_key": "ANTHROPIC_API_KEY",
        "instructions": "Create an API key (sk-ant-...) at https://console.anthropic.com/. Paste it below to configure."
    }
}

class ApiKeyPayload(BaseModel):
    env_key: str
    api_key: str

class ModelPayload(BaseModel):
    id: str
    name: str
    env_key: str = ""
    instructions: str = ""
    # Optional prices in USD per million tokens; override LiteLLM's price list
    input_cost_per_mtok: float | None = None
    output_cost_per_mtok: float | None = None
    cached_input_cost_per_mtok: float | None = None
    # None keeps the model's current thinking level
    thinking: str | None = None
    # Optional server to send this model's calls to (a proxy, reseller or your own
    # server); None keeps the current one, "" removes it (v0.4-5o)
    api_base: str | None = None

PRICE_FIELDS = ("input_cost_per_mtok", "output_cost_per_mtok", "cached_input_cost_per_mtok")

# "default" sends nothing, so the provider decides: Claude doesn't think,
# Gemini 3 thinks but keeps its thoughts hidden. The others are passed to
# LiteLLM as reasoning_effort and stream the model's thinking.
THINKING_LEVELS = ("default", "minimal", "low", "medium", "high")


def validate_thinking(level: str) -> str:
    if level not in THINKING_LEVELS:
        raise ValidationError(f"thinking must be one of {', '.join(THINKING_LEVELS)}.")
    return level


def thinking_for(model_id: str) -> str:
    level = get_all_models().get(model_id, {}).get("thinking", "default")
    return level if level in THINKING_LEVELS else "default"


def thinking_kwargs(model_id: str) -> dict:
    """LiteLLM arguments for the model's thinking level ({} for default)."""
    level = thinking_for(model_id)
    return {} if level == "default" else {"reasoning_effort": level}


def supports_thinking(model_id: str) -> bool | None:
    """True/False from LiteLLM's model list; None when LiteLLM doesn't know the model."""
    try:
        import litellm
        info = litellm.get_model_info(model=model_id)
        value = info.get("supports_reasoning")
        return bool(value) if value is not None else None
    except Exception:
        try:
            import litellm
            return True if litellm.supports_reasoning(model=model_id) else None
        except Exception:
            return None


def set_thinking(model_id: str, level: str) -> None:
    validate_thinking(level)
    models = get_all_models()
    if model_id not in models:
        raise ValidationError(f"Unknown model: {model_id}")
    models[model_id]["thinking"] = level
    with open(MODELS_FILE, "w") as f:
        json.dump(models, f, indent=4)

class RoutingPayload(BaseModel):
    # conductor_model may be "off": skip the Conductor and start on simple
    conductor_model: str = ""
    simple_model: str = ""
    complex_model: str = ""

class SettingsPayload(BaseModel):
    projects_root: str = ""
    active_project: str = ""
    auto_approve: bool = False
    # Model calls allowed per turn before Optimux stops and asks to continue
    max_steps_simple: int = 8
    max_steps_complex: int = 25
    # Feature 17: update project memory automatically when a chat ends
    memory_curator: bool = True
    curator_idle_minutes: int = 10
    # Feature 18: ask (with a cost estimate) before extra paid actions
    confirm_update_memory: bool = True
    confirm_escalate: bool = True
    # Feature 19: off | manual (only /name) | auto (auto skills are listed in prompts)
    skills_mode: str = "auto"
    # Sandbox (bubblewrap): commands can only change the working folder
    sandbox: bool = False
    # None = automatic: on for a local install, off on a server (OPTIMUX_SERVER=1)
    sandbox_network: bool | None = None
    sandbox_ro_paths: list[str] = []
    # Feature 23: extra environment variables commands may see (all others are dropped)
    command_env: list[str] = []
    # Feature 23: per-command limits (0 = no limit)
    limit_memory_mb: int = 0
    limit_file_mb: int = 1024
    limit_processes: int = 0
    # v0.5.1: seconds a command may run before it is stopped (installs, builds, tests take a while)
    command_timeout: int = 120
    # Feature 25: who commits (initial project commits and model-run git); empty = git's own config
    git_name: str = ""
    git_email: str = ""
    # The GitHub account last checked (shown in the Dashboard; the token is in secrets.env)
    github_login: str = ""
    # v0.3: how much disk the snapshots (Dashboard -> Backups) may use, in GB
    snapshot_limit_gb: float = 5
    # v0.5.2: how many daily database backups to keep (Dashboard -> Backups)
    backups_keep: int = 14
    snapshot_recipient: str = ""      # a public key remembered for encrypted downloads
    online_catalog: bool = False      # may the Models screen ask providers and GitHub (on Refresh only)
    # v0.4 plan mode: off | ask (show the plan, wait for Run) | auto (show it and start).
    # Off by default since v0.4-5h: measured on six tasks, plans didn't pay off.
    plan_mode: str = "off"


DEFAULT_MAX_STEPS = {"simple": 8, "complex": 25}
DEFAULT_COMMAND_TIMEOUT = 120
BACKUPS_KEEP_RANGE = (3, 60)


def backups_keep_for(settings: dict | None = None) -> int:
    """How many daily backups to keep (Dashboard -> Backups), within the range."""
    settings = get_settings() if settings is None else settings
    try:
        value = int(settings.get("backups_keep", 14))
    except (TypeError, ValueError):
        value = 14
    return max(BACKUPS_KEEP_RANGE[0], min(value, BACKUPS_KEEP_RANGE[1]))
COMMAND_TIMEOUT_RANGE = (10, 1800)


def command_timeout_for(settings: dict | None = None) -> int:
    """Seconds a command may run (Dashboard -> Execution), within the allowed range."""
    settings = get_settings() if settings is None else settings
    try:
        value = int(settings.get("command_timeout", DEFAULT_COMMAND_TIMEOUT))
    except (TypeError, ValueError):
        value = DEFAULT_COMMAND_TIMEOUT
    return max(COMMAND_TIMEOUT_RANGE[0], min(value, COMMAND_TIMEOUT_RANGE[1]))
PLAN_MODES = ("off", "ask", "auto")


def max_steps_for(tier: str) -> int:
    """Step limit for a tier ('override' uses the complex limit)."""
    key = "simple" if tier == "simple" else "complex"
    try:
        value = int(get_settings().get(f"max_steps_{key}", DEFAULT_MAX_STEPS[key]))
    except (TypeError, ValueError):
        value = DEFAULT_MAX_STEPS[key]
    return max(1, min(value, 100))

def _read_settings() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        return {"projects_root": "", "active_project": ""}
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"projects_root": "", "active_project": ""}
    except Exception:
        return {"projects_root": "", "active_project": ""}


def get_settings() -> dict:
    data = _read_settings()
    from optimux_backend.core.auth import server_mode
    server = server_mode()
    # Commands have no network on a server unless it was turned on deliberately
    if data.get("sandbox_network") is None:
        data["sandbox_network"] = not server
    if server:
        data["auto_approve"] = False   # on a server every command needs approval
    return data

def save_settings(payload: SettingsPayload):
    for field in ("max_steps_simple", "max_steps_complex"):
        if not 1 <= getattr(payload, field) <= 100:
            raise ValidationError(f"{field} must be between 1 and 100.")
    from optimux_backend.core import sandbox
    try:
        payload.sandbox_ro_paths = [sandbox.check_ro_path(p.strip()) for p in payload.sandbox_ro_paths if p.strip()]
    except sandbox.SandboxError as e:
        raise ValidationError(str(e))
    if payload.sandbox and not get_settings().get("sandbox") and not sandbox.status(refresh=True)["works"]:
        raise ValidationError("Can't turn the sandbox on: " + (sandbox.status()["error"] or "bubblewrap doesn't work here."))
    from optimux_backend.core.auth import server_mode
    if payload.auto_approve and server_mode():
        raise ValidationError("Auto-approve is off on servers (OPTIMUX_SERVER=1): every command needs your approval.")
    payload.command_env = check_command_env(payload.command_env)
    for field, top in (("limit_memory_mb", 1024 * 1024), ("limit_file_mb", 1024 * 1024), ("limit_processes", 100000)):
        if not 0 <= getattr(payload, field) <= top:
            raise ValidationError(f"{field} must be between 0 (no limit) and {top}.")
    if not COMMAND_TIMEOUT_RANGE[0] <= payload.command_timeout <= COMMAND_TIMEOUT_RANGE[1]:
        raise ValidationError(f"The command timeout must be between {COMMAND_TIMEOUT_RANGE[0]} and "
                              f"{COMMAND_TIMEOUT_RANGE[1]} seconds.")
    if not BACKUPS_KEEP_RANGE[0] <= payload.backups_keep <= BACKUPS_KEEP_RANGE[1]:
        raise ValidationError(f"Keep between {BACKUPS_KEEP_RANGE[0]} and {BACKUPS_KEEP_RANGE[1]} daily backups.")
    if not 0.1 <= payload.snapshot_limit_gb <= 10000:
        raise ValidationError("snapshot_limit_gb must be between 0.1 and 10000.")
    # Checked only when it changes: a saved key never blocks unrelated saves
    # (switching project, archiving), even if a later library refuses it
    if payload.snapshot_recipient.strip() and \
            payload.snapshot_recipient != _read_settings().get("snapshot_recipient", ""):
        from optimux_backend.core.exports import ExportError, parse_recipient
        try:
            parse_recipient(payload.snapshot_recipient)
        except ExportError as e:
            raise ValidationError(str(e))
    if 0 < payload.limit_memory_mb < 256:
        raise ValidationError("A memory limit under 256 MB stops most tools from starting; use 0 for no limit.")
    if 0 < payload.limit_processes < 32:
        raise ValidationError("A process limit under 32 breaks ordinary commands; use 0 for no limit.")
    if payload.skills_mode not in ("off", "manual", "auto"):
        raise ValidationError("skills_mode must be off, manual or auto.")
    if payload.plan_mode not in PLAN_MODES:
        raise ValidationError("plan_mode must be off, ask or auto.")
    if not 1 <= payload.curator_idle_minutes <= 240:
        raise ValidationError("curator_idle_minutes must be between 1 and 240.")
    payload.git_name, payload.git_email = check_git_identity(payload.git_name, payload.git_email)
    if not re.match(r"^[A-Za-z0-9-]{0,39}$", payload.github_login or ""):
        raise ValidationError("github_login isn't a GitHub user name.")
    # Reject bad paths at save time (raises ScopeError -> HTTP 400 in main.py)
    if payload.projects_root:
        root = resolve_scope_root(payload.projects_root)
        if payload.projects_root != _read_settings().get("projects_root", ""):
            from optimux_backend.core.snapshots import is_private_home_path
            if is_private_home_path(str(root)):
                raise ValidationError("The Workspace folder can't be Optimux's own data folder (or a part of it that "
                                      "holds keys and chats). Pick a folder for your projects, for example ~/projects.")
            if not os.access(root, os.W_OK | os.X_OK):
                raise ValidationError(workspace_not_writable(root))
            if payload.active_project and not (root / payload.active_project).is_dir():
                payload.active_project = ""     # not in the new folder: back to the Workspace itself
    if payload.active_project:
        resolve_project_dir(payload.projects_root, payload.active_project)
    settings_file = SETTINGS_FILE
    os.makedirs(os.path.dirname(settings_file), exist_ok=True)
    with open(settings_file, "w") as f:
        json.dump(payload.model_dump(), f, indent=4)

def workspace_not_writable(root) -> str:
    """Why a Workspace folder is refused when Optimux can't write in it."""
    import pwd
    try:
        user = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        user = str(os.geteuid())
    text = f"Optimux can't write in {root} (it runs as the user {user})."
    from optimux_backend.core.auth import server_mode
    if server_mode():
        text += (" On a server, the service may only write inside its own folder: use a folder there owned by "
                 f"{user}, for example /var/lib/{user}/projects (sudo install -d -m 750 -o {user} -g {user} "
                 f"/var/lib/{user}/projects).")
    else:
        text += f" Give {user} write access to it, or pick another folder."
    return text


# Set from Dashboard → GitHub (or the installer) only; a generic settings save keeps them
GITHUB_FIELDS = ("git_name", "git_email", "github_login")


def set_github_fields(**fields) -> None:
    """Change only the GitHub-related settings, validating just those (an
    unrelated setting that became invalid, like a deleted Workspace folder,
    doesn't block them)."""
    unknown = set(fields) - set(GITHUB_FIELDS)
    if unknown:
        raise ValidationError(f"Not a GitHub setting: {', '.join(sorted(unknown))}")
    data = _read_settings()
    merged = {f: data.get(f, "") or "" for f in GITHUB_FIELDS}
    merged.update({k: (v or "") for k, v in fields.items()})
    merged["git_name"], merged["git_email"] = check_git_identity(merged["git_name"], merged["git_email"])
    if not re.match(r"^[A-Za-z0-9-]{0,39}$", merged["github_login"]):
        raise ValidationError("github_login isn't a GitHub user name.")
    data.update(merged)
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=4)


def update_settings(**changes) -> None:
    """Change some settings and keep the rest (validated like a Dashboard save)."""
    current = {k: v for k, v in _read_settings().items() if k in SettingsPayload.model_fields}
    current.update(changes)
    save_settings(SettingsPayload(**current))


# Variables every command gets (when the server has them); the rest of the
# server's environment, including every key and token, is dropped.
BASE_COMMAND_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "TZ",
                    "COLORTERM", "NO_COLOR")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def check_command_env(names: list[str]) -> list[str]:
    """Validate the extra variable names commands may see. Keys and tokens kept
    by Optimux (secrets.env) can never be passed on."""
    out = []
    secret = get_secret_env_keys() | {"OPTIMUX_TOKEN", "CODEMUX_TOKEN"}
    for raw in names or []:
        name = (raw or "").strip()
        if not name:
            continue
        if not _ENV_NAME.match(name):
            raise ValidationError(f"'{name}' is not a valid variable name.")
        if name in secret or name.endswith("_API_KEY") or name.startswith(("OPTIMUX_", "CODEMUX_")):
            raise ValidationError(f"{name} holds an Optimux key or setting and can't be given to commands.")
        if name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV", "PROMPT_COMMAND"):
            raise ValidationError(f"{name} changes how every program starts; it can't be passed to commands.")
        if name not in out:
            out.append(name)
    return out


_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+$")


def check_git_identity(name: str, email: str) -> tuple[str, str]:
    """Commit name and email: both or neither, no control characters."""
    name, email = (name or "").strip(), (email or "").strip()
    if any(ord(c) < 32 or ord(c) == 127 or c in "<>" for c in name) or len(name) > 100:
        raise ValidationError("The commit name can't contain < > or control characters (100 characters at most).")
    if email and (not _EMAIL.match(email) or len(email) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in email)):
        raise ValidationError(f"'{email}' isn't an email address.")
    if bool(name) != bool(email):
        raise ValidationError("Set both the commit name and email, or neither.")
    return name, email


def git_identity_env(settings: dict | None = None) -> dict:
    """GIT_AUTHOR_*/GIT_COMMITTER_* from the settings, or {} to use git's own config."""
    settings = get_settings() if settings is None else settings
    name, email = settings.get("git_name") or "", settings.get("git_email") or ""
    if not (name and email):
        return {}
    return {"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email}


def command_env(settings: dict | None = None, base: dict | None = None) -> dict:
    """The environment a model-run command gets: an allow-list, not a copy."""
    settings = get_settings() if settings is None else settings
    base = os.environ if base is None else base
    allowed = set(BASE_COMMAND_ENV)
    try:
        allowed |= set(check_command_env(settings.get("command_env") or []))
    except ValidationError:
        pass  # validated on save; ignore anything that became invalid
    env = {k: v for k, v in base.items() if k in allowed or k.startswith("LC_")}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update(git_identity_env(settings))
    return env


def _init_models():
    if not os.path.exists(MODELS_FILE):
        os.makedirs(os.path.dirname(MODELS_FILE), exist_ok=True)
        models = {}
        for mid, details in LEGACY_SUPPORTED_MODELS.items():
            models[mid] = details
        with open(MODELS_FILE, "w") as f:
            json.dump(models, f, indent=4)

def get_all_models() -> dict:
    _init_models()
    try:
        with open(MODELS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def get_secret_env_keys() -> set[str]:
    """Env var names that hold LLM provider credentials (stripped from shell env)."""
    keys = {d.get("env_key", "") for d in LEGACY_SUPPORTED_MODELS.values()}
    keys |= {d.get("env_key", "") for d in get_all_models().values()}
    return {k for k in keys if k}

def model_id_problem(model_id: str) -> str | None:
    """Why LiteLLM can't route this model ID, or None if it can."""
    if not model_id or not model_id.strip():
        return "Model ID is empty."
    try:
        import litellm
        litellm.get_llm_provider(model=model_id)
        return None
    except Exception:
        return (f"LiteLLM can't tell which provider '{model_id}' belongs to. "
                "Add a provider prefix, e.g. anthropic/..., gemini/..., openai/... or ollama/....")


# Providers that run on your own machine or server: no key by default
_LOCAL_PROVIDERS = {"ollama", "ollama_chat", "lm_studio", "hosted_vllm", "llamafile", "vllm"}
# LiteLLM's own names where they don't follow PROVIDER_API_KEY
_KEY_NAMES = {"together_ai": "TOGETHERAI_API_KEY", "gemini": "GEMINI_API_KEY", "azure": "AZURE_API_KEY",
              "fireworks_ai": "FIREWORKS_AI_API_KEY", "nvidia_nim": "NVIDIA_NIM_API_KEY"}


def validate_api_base(url: str) -> str:
    """A server address for a model: http(s), a host, no user:password, no
    query. Returns it without a trailing slash; "" stays "" (no server)."""
    from urllib.parse import urlsplit
    url = (url or "").strip()
    if not url:
        return ""
    if len(url) > 300 or any(c.isspace() for c in url):
        raise ValidationError("The base URL is too long or has spaces.")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError("The base URL must start with https:// (or http:// for a server on your network) "
                              "and name a host, e.g. https://api.example.com/v1.")
    if parts.username or parts.password:
        raise ValidationError("Don't put a user name or password in the base URL; the key goes in the API key field.")
    if parts.query or parts.fragment:
        raise ValidationError("The base URL can't have ?query or #fragment parts.")
    return url.rstrip("/")


def api_base_for(model_id: str) -> str:
    return get_all_models().get(model_id, {}).get("api_base", "")


def api_host(api_base: str) -> str:
    from urllib.parse import urlsplit
    try:
        return urlsplit(api_base).hostname or ""
    except ValueError:
        return ""


def suggested_env_key(model_id: str, api_base: str = "") -> str:
    """The key variable to propose for a new model: for a model behind a base URL,
    a name from the host (api.apmix.ai -> APMIX_API_KEY), so it never shares the
    provider's own key; otherwise LiteLLM's usual name for the provider
    (groq/... -> GROQ_API_KEY). "" for local servers and models without a prefix."""
    if api_base:
        host = api_host(api_base).lower()
        if host in ("localhost", "::1") or host.startswith("127."):
            return ""
        labels = [l for l in host.split(".") if l]
        if len(labels) > 1 and labels[0] in ("api", "www", "llm", "gateway", "openai", "inference"):
            labels = labels[1:]
        name = re.sub(r"[^A-Z0-9]+", "_", (labels[0] if labels else "").upper()).strip("_")
        if not name:
            return ""
        return (name if name[0].isalpha() else "API_" + name)[:50] + "_API_KEY"
    try:
        import litellm
        provider = litellm.get_llm_provider(model_id)[1]
    except Exception:
        return ""
    if provider in _LOCAL_PROVIDERS:
        return ""
    return _KEY_NAMES.get(provider) or re.sub(r"[^A-Z0-9_]", "_", provider.upper()) + "_API_KEY"


def _key_conflict(model_id: str, env_key: str, api_base: str) -> str | None:
    """Why this key variable can't be used here, or None. A key is only shared by
    models that go to the same server: otherwise one provider's key would be
    sent to another server (e.g. the Anthropic key to a reseller)."""
    if not env_key:
        return None
    for other_id, d in get_all_models().items():
        if other_id == model_id or d.get("env_key") != env_key or d.get("api_base", "") == api_base:
            continue
        mine = api_host(api_base) or "its provider directly"
        theirs = api_host(d.get("api_base", "")) or "its provider directly"
        proposal = suggested_env_key(model_id, api_base) if api_base else ""
        hint = f" Use a separate name, e.g. {proposal}." if proposal and proposal != env_key else " Use a separate name."
        return (f"{env_key} is already the key of {d.get('name', other_id)} ({other_id}), which goes to {theirs}. "
                f"This model goes to {mine}, so sharing the name would send that key there.{hint}")
    return None


def api_key_for(model_id: str) -> str | None:
    """The API key from the model's own env key variable, so any variable name
    (e.g. CLAUDE_API_KEY) works. None when the model has no env key or it is
    unset; LiteLLM then falls back to its default variable for the provider."""
    env_key = get_all_models().get(model_id, {}).get("env_key", "")
    return os.environ.get(env_key) or None if env_key else None


def litellm_key_kwargs(model_id: str) -> dict:
    """The key, and the server when the model has its own base URL. A model behind
    a base URL never falls back to LiteLLM's default variable for the provider:
    that would send, say, the Anthropic key to someone else's server."""
    key = api_key_for(model_id)
    base = api_base_for(model_id)
    if base:
        return {"api_base": base, "api_key": key or "none"}
    return {"api_key": key} if key else {}


def price_source(model_id: str, details: dict) -> str:
    """Where cost figures for this model come from: custom | litellm | local | zero | unknown."""
    if details.get("input_cost_per_mtok") is not None and details.get("output_cost_per_mtok") is not None:
        return "custom"
    if model_id.startswith(("ollama/", "ollama_chat/")):
        return "local"
    if details.get("api_base"):
        return "proxy"      # another server's prices: LiteLLM's list is for the model's maker
    try:
        import litellm
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model_id, prompt_tokens=1_000_000, completion_tokens=1_000_000)
        if (prompt_cost + completion_cost) > 0:
            return "litellm"
        info = litellm.get_model_info(model_id)
        if info.get("input_cost_per_token") == 0 and info.get("output_cost_per_token") == 0:
            return "zero"       # listed at $0: a free model, or one with no price known
        return "unknown"
    except Exception:
        return "unknown"


def list_prices(model_id: str) -> dict | None:
    """LiteLLM's prices for a model, in USD per million tokens (input, output,
    cached input), so the card can show the numbers, not just "known"."""
    try:
        import litellm
        info = litellm.get_model_info(model_id)
    except Exception:
        return None
    def per_m(v):
        return None if v is None else round(v * 1_000_000, 6)
    out = {"input": per_m(info.get("input_cost_per_token")), "output": per_m(info.get("output_cost_per_token")),
           "cached": per_m(info.get("cache_read_input_token_cost"))}
    return out if out["input"] is not None and out["output"] is not None else None


def _limits(model_id: str) -> dict | None:
    from optimux_backend.core import ratelimits
    return ratelimits.get(model_id)


def get_models_config() -> dict:
    models = get_all_models()
    available = []
    requires_setup = []
    
    for model_id, details in models.items():
        env_key = details.get("env_key", "")
        # If model has no env_key required, or if the required env_key exists in os.environ
        entry = {
            "id": model_id,
            "name": details.get("name", model_id),
            "env_key": env_key,
            "instructions": details.get("instructions", ""),
            "problem": model_id_problem(model_id),
            "price_source": price_source(model_id, details),
            "thinking": details.get("thinking", "default"),
            "supports_thinking": supports_thinking(model_id),
            "limits": _limits(model_id),
            "api_base": details.get("api_base", ""),
            "list_prices": None,
            "api_host": api_host(details.get("api_base", "")),
        }
        for field in PRICE_FIELDS:
            entry[field] = details.get(field)
        if entry["price_source"] == "litellm":
            entry["list_prices"] = list_prices(model_id)
        if not env_key or os.environ.get(env_key):
            available.append(entry)
        else:
            requires_setup.append(entry)
            
    return {"available": available, "requires_setup": requires_setup}

def save_model(payload: ModelPayload):
    problem = model_id_problem(payload.id)
    if problem:
        raise ValidationError(problem)
    if payload.env_key:
        validate_env_key(payload.env_key)
    for field in PRICE_FIELDS:
        value = getattr(payload, field)
        if value is not None and value < 0:
            raise ValidationError(f"{field} must not be negative.")
    if payload.thinking is not None:
        validate_thinking(payload.thinking)
    models = get_all_models()
    previous = models.get(payload.id, {})
    api_base = previous.get("api_base", "") if payload.api_base is None else validate_api_base(payload.api_base)
    conflict = _key_conflict(payload.id, payload.env_key, api_base)
    if conflict:
        raise ValidationError(conflict)
    previous_thinking = previous.get("thinking", "default")
    models[payload.id] = {
        "name": payload.name,
        "env_key": payload.env_key,
        "instructions": payload.instructions,
        "thinking": payload.thinking if payload.thinking is not None else previous_thinking,
    }
    if api_base:
        models[payload.id]["api_base"] = api_base
    for field in PRICE_FIELDS:
        if getattr(payload, field) is not None:
            models[payload.id][field] = getattr(payload, field)
    with open(MODELS_FILE, "w") as f:
        json.dump(models, f, indent=4)

def delete_model(model_id: str):
    models = get_all_models()
    if model_id in models:
        del models[model_id]
        with open(MODELS_FILE, "w") as f:
            json.dump(models, f, indent=4)

def get_routing_config() -> dict:
    if not os.path.exists(ROUTING_FILE):
        return {"conductor_model": "", "simple_model": "", "complex_model": ""}
    try:
        with open(ROUTING_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"conductor_model": "", "simple_model": "", "complex_model": ""}

def save_routing_config(payload: RoutingPayload):
    known = get_all_models()
    for field in ("conductor_model", "simple_model", "complex_model"):
        value = getattr(payload, field)
        if not value or (field == "conductor_model" and value == "off"):
            continue
        if value not in known:
            raise ValidationError(f"{field}: '{value}' is not a configured model.")
    os.makedirs(os.path.dirname(ROUTING_FILE), exist_ok=True)
    with open(ROUTING_FILE, "w") as f:
        json.dump(payload.model_dump(), f, indent=4)

def save_api_key_to_env(payload: ApiKeyPayload) -> None:
    """Save the key to .env and the running process. Key formats are not
    checked (providers change them); the Models tab's Test is the real check."""
    payload.api_key = payload.api_key.strip()
    validate_env_key(payload.env_key)
    validate_env_value(payload.api_key)
    if not payload.api_key:
        raise ValidationError("API key is empty.")
    env_file = SECRETS_FILE
    lines = []
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            lines = f.readlines()
            
    key_found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{payload.env_key}="):
            lines[i] = f"{payload.env_key}={payload.api_key}\n"
            key_found = True
            break
            
    if not key_found:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(f"{payload.env_key}={payload.api_key}\n")
        
    paths.write_private(env_file, "".join(lines))

    os.environ[payload.env_key] = payload.api_key

def delete_api_key_from_env(env_key: str):
    validate_env_key(env_key)
    env_file = SECRETS_FILE
    if not os.path.exists(env_file):
        return
        
    with open(env_file, "r") as f:
        lines = f.readlines()
        
    new_lines = [line for line in lines if not line.startswith(f"{env_key}=")]
    paths.write_private(env_file, "".join(new_lines))
        
    if env_key in os.environ:
        del os.environ[env_key]
