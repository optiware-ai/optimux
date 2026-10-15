"""The model catalog: providers, their models, prices and context sizes.

Offline by default. The list comes from the price list that LiteLLM ships with
(about 3,800 models: provider, prices per token, context size, tool support), so
the Models screen works with no network, as fresh as the installed LiteLLM.

Online, only when the user turns on the setting `online_catalog` AND clicks
Refresh (nothing runs in the background):
- refresh_models(provider): asks the provider which models the user's own key can
  use (its /models endpoint, with the key saved in secrets.env). Same party the
  requests already go to. Ollama is asked on this machine (OLLAMA_API_BASE).
- refresh_prices(): downloads LiteLLM's public price list from GitHub
  (litellm.model_cost_map_url). Providers don't publish prices through their APIs;
  this list is the machine-readable source LiteLLM itself uses. The list is saved
  in ~/.optimux/catalog/prices.json and loaded into LiteLLM, so cost metering uses
  the fresher prices too (prices set by hand on a model still win).
HTTP goes through httpx with the usual proxy variables (HTTPS_PROXY ...).
"""
import json
import os
import time

from optimux_backend.core import paths

CATALOG_DIR_NAME = "catalog"
PRICES_FILE = "prices.json"
MAX_PRICES_BYTES = 40 * 1024 * 1024
HTTP_TIMEOUT = 20

# Providers shown in the Models screen. prefix: how LiteLLM model IDs start
# ("" = bare IDs such as gpt-5). env: the key variable (None = no key, local).
# models_url: the provider's list of models (OpenAI-style unless `kind` says).
PROVIDERS = [
    {"id": "openai", "label": "OpenAI", "prefix": "", "env": "OPENAI_API_KEY",
     "models_url": "https://api.openai.com/v1/models", "keys_url": "https://platform.openai.com/api-keys"},
    {"id": "anthropic", "label": "Anthropic (Claude)", "prefix": "", "env": "ANTHROPIC_API_KEY",
     "models_url": "https://api.anthropic.com/v1/models?limit=1000", "kind": "anthropic",
     "keys_url": "https://console.anthropic.com/settings/keys"},
    {"id": "gemini", "label": "Google (Gemini)", "prefix": "gemini/", "env": "GEMINI_API_KEY",
     "models_url": "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", "kind": "gemini",
     "keys_url": "https://aistudio.google.com/apikey"},
    {"id": "xai", "label": "xAI (Grok)", "prefix": "xai/", "env": "XAI_API_KEY",
     "models_url": "https://api.x.ai/v1/models", "keys_url": "https://console.x.ai"},
    {"id": "mistral", "label": "Mistral", "prefix": "mistral/", "env": "MISTRAL_API_KEY",
     "models_url": "https://api.mistral.ai/v1/models", "keys_url": "https://console.mistral.ai/api-keys"},
    {"id": "deepseek", "label": "DeepSeek", "prefix": "deepseek/", "env": "DEEPSEEK_API_KEY",
     "models_url": "https://api.deepseek.com/models", "keys_url": "https://platform.deepseek.com/api_keys"},
    {"id": "groq", "label": "Groq", "prefix": "groq/", "env": "GROQ_API_KEY",
     "models_url": "https://api.groq.com/openai/v1/models", "keys_url": "https://console.groq.com/keys"},
    {"id": "openrouter", "label": "OpenRouter (many providers)", "prefix": "openrouter/", "env": "OPENROUTER_API_KEY",
     "models_url": "https://openrouter.ai/api/v1/models", "kind": "openrouter",
     "keys_url": "https://openrouter.ai/settings/keys"},
    {"id": "ollama", "label": "Ollama (local, free)", "prefix": "ollama/", "env": None, "kind": "ollama",
     "models_url": "/api/tags", "keys_url": "https://ollama.com/library"},
]
PROVIDER_IDS = tuple(p["id"] for p in PROVIDERS)


class CatalogError(Exception):
    """A catalog action that can't be done; the message is shown to the user."""


def provider(pid: str) -> dict:
    for p in PROVIDERS:
        if p["id"] == pid:
            return p
    raise CatalogError(f"Unknown provider '{pid}'.")


def catalog_dir() -> str:
    folder = os.path.join(paths.HOME, CATALOG_DIR_NAME)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    return folder


def _read_json(name: str):
    try:
        with open(os.path.join(paths.HOME, CATALOG_DIR_NAME, name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(name: str, data) -> None:
    paths.write_private(os.path.join(catalog_dir(), name), json.dumps(data))


def online_allowed() -> bool:
    from optimux_backend.core.config_manager import get_settings
    return bool(get_settings().get("online_catalog"))


def key_saved(p: dict) -> bool:
    return p["env"] is None or bool(os.environ.get(p["env"]))


# ------------------------------------------------------------------ offline list

def _per_mtok(value) -> float | None:
    try:
        return round(float(value) * 1_000_000, 6) if value is not None else None
    except (TypeError, ValueError):
        return None


def model_id(p: dict, key: str) -> str:
    """The LiteLLM model ID for a price-list key (some keys lack the prefix)."""
    if p["prefix"] and not key.startswith(p["prefix"]):
        return p["prefix"] + key
    return key


def _row(p: dict, key: str, info: dict) -> dict:
    return {
        "id": model_id(p, key),
        "input": _per_mtok(info.get("input_cost_per_token")),
        "output": _per_mtok(info.get("output_cost_per_token")),
        "cached": _per_mtok(info.get("cache_read_input_token_cost")),
        "context": info.get("max_input_tokens") or info.get("max_tokens"),
        "max_output": info.get("max_output_tokens"),
        "tools": bool(info.get("supports_function_calling")),
        "reasoning": bool(info.get("supports_reasoning")),
        "deprecated": info.get("deprecation_date") or None,
        "retired": _retired(info.get("deprecation_date")),
    }


def _retired(date: str | None) -> bool:
    """True when the provider's retirement date (YYYY-MM-DD) has passed (on the
    day itself the model may still answer)."""
    return bool(date) and isinstance(date, str) and date[:10] < time.strftime("%Y-%m-%d")


def offline_models(pid: str) -> list[dict]:
    """Chat models of one provider from LiteLLM's price list, cheapest first."""
    import litellm
    p = provider(pid)
    rows: dict[str, dict] = {}
    for key, info in litellm.model_cost.items():
        if not isinstance(info, dict) or info.get("mode") != "chat" or info.get("litellm_provider") != pid:
            continue
        if key.startswith("ft:") or "fine-tuned" in key or "/ft:" in key:
            continue                        # templates for fine-tuned models, not models
        row = _row(p, key, info)
        rows.setdefault(row["id"], row)     # bare and prefixed keys can name the same model
    return sorted(rows.values(), key=lambda r: (r["input"] is None, r["input"] or 0, r["id"]))


def models(pid: str) -> dict:
    """The models of one provider: the offline list, merged with the last list the
    provider gave for the user's key (marking which models the key can use, and
    adding new models the price list doesn't know yet)."""
    p = provider(pid)
    rows = {r["id"]: r for r in offline_models(pid)}
    live = _read_json(f"models-{pid}.json") or {}
    live_ids = live.get("models") or []
    if live_ids:
        seen = set(live_ids)
        for r in rows.values():
            r["available"] = r["id"] in seen
        for mid in live_ids:
            if mid not in rows:
                extra = (live.get("prices") or {}).get(mid) or {}
                rows[mid] = {"id": mid, "input": extra.get("input"), "output": extra.get("output"),
                             "cached": None, "context": extra.get("context"), "max_output": None,
                             "tools": None, "reasoning": None, "deprecated": None, "retired": False,
                             "available": True,
                             "new": True}
    out = sorted(rows.values(), key=lambda r: (r["input"] is None, r["input"] or 0, r["id"]))
    return {"provider": {k: p[k] for k in ("id", "label", "prefix", "env", "keys_url")},
            "key_saved": key_saved(p), "models": out,
            "models_refreshed": live.get("fetched"), "online": online_allowed()}


def summary() -> dict:
    prices = _read_json(PRICES_FILE) or {}
    return {
        "online": online_allowed(),
        "prices_refreshed": prices.get("fetched"),
        "prices_count": len(prices.get("models") or {}),
        "providers": [{"id": p["id"], "label": p["label"], "prefix": p["prefix"], "env": p["env"],
                       "key_saved": key_saved(p), "keys_url": p["keys_url"],
                       "models_refreshed": (_read_json(f"models-{p['id']}.json") or {}).get("fetched")}
                      for p in PROVIDERS],
    }


# ------------------------------------------------------------------ online refresh

def _http_get(url: str, headers: dict | None = None):
    import httpx
    try:
        # A request that carries a key never follows a redirect: httpx drops only the
        # Authorization header on a move to another site, not x-api-key or x-goog-api-key
        r = httpx.get(url, headers=headers or {}, timeout=HTTP_TIMEOUT, follow_redirects=not headers)
    except httpx.HTTPError as e:
        raise CatalogError(f"Could not reach {url.split('/')[2]}: {e.__class__.__name__}. "
                           "Check the network or the proxy (HTTPS_PROXY).")
    if r.status_code in (401, 403):
        raise CatalogError("The provider refused the API key (HTTP %d). Check the key in Models." % r.status_code)
    if r.status_code >= 400:
        raise CatalogError(f"The provider answered HTTP {r.status_code}.")
    return r


def _require_online() -> None:
    if not online_allowed():
        raise CatalogError("The online catalog is off. Turn it on in Models first; until then Optimux "
                           "connects to nothing for the catalog.")


def refresh_models(pid: str) -> dict:
    """Ask the provider which models the user's key can use, and keep that list."""
    p = provider(pid)
    kind = p.get("kind", "openai")
    if kind == "ollama":
        base = (os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434").rstrip("/")
        try:
            data = _http_get(base + p["models_url"]).json()
        except CatalogError as e:
            raise CatalogError(f"Ollama doesn't answer at {base}. Is it running? ({e})")
        ids = [p["prefix"] + m["name"] for m in data.get("models", []) if m.get("name")]
        result = {"fetched": time.time(), "models": sorted(ids), "prices": {}}
        _write_json(f"models-{pid}.json", result)
        return {"count": len(ids)}
    _require_online()
    key = os.environ.get(p["env"] or "")
    if not key and kind != "openrouter":
        raise CatalogError(f"Save your {p['label']} API key first ({p['env']}): the provider only lists "
                           "models for a key.")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if kind == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif kind == "gemini":
        headers = {"x-goog-api-key": key}
    r = _http_get(p["models_url"], headers)
    try:
        data = r.json()
    except ValueError:
        raise CatalogError("The provider's answer was not valid JSON.")
    ids, prices = [], {}
    if kind == "gemini":
        for m in data.get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []) and m.get("name", "").startswith("models/"):
                ids.append(p["prefix"] + m["name"][len("models/"):])
    else:
        for m in data.get("data", []):
            mid = m.get("id")
            if not isinstance(mid, str) or not mid:
                continue
            full = model_id(p, mid)
            ids.append(full)
            if kind == "openrouter" and isinstance(m.get("pricing"), dict):
                prices[full] = {"input": _per_mtok(m["pricing"].get("prompt")),
                                "output": _per_mtok(m["pricing"].get("completion")),
                                "context": m.get("context_length")}
    result = {"fetched": time.time(), "models": sorted(set(ids)), "prices": prices}
    _write_json(f"models-{pid}.json", result)
    return {"count": len(result["models"])}


# Fields kept from the downloaded price list (everything else is dropped)
PRICE_KEYS = ("litellm_provider", "mode", "input_cost_per_token", "output_cost_per_token",
              "cache_read_input_token_cost", "cache_creation_input_token_cost", "max_input_tokens",
              "max_output_tokens", "max_tokens", "supports_function_calling", "supports_reasoning",
              "supports_prompt_caching", "supports_vision", "deprecation_date", "output_cost_per_reasoning_token")


def _clean_prices(raw) -> dict:
    """Only well-formed entries with known fields of the right types."""
    if not isinstance(raw, dict):
        raise CatalogError("The downloaded price list has an unexpected format.")
    out = {}
    for key, info in raw.items():
        if key == "sample_spec" or not isinstance(key, str) or len(key) > 300 or not isinstance(info, dict):
            continue
        entry = {}
        for f in PRICE_KEYS:
            v = info.get(f)
            if v is None:
                continue
            if f in ("litellm_provider", "mode", "deprecation_date"):
                if isinstance(v, str) and len(v) <= 100:
                    entry[f] = v
            elif f.startswith("supports_"):
                if isinstance(v, bool):
                    entry[f] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e9:
                entry[f] = v
        if entry.get("litellm_provider"):
            out[key] = entry
    if len(out) < 100:
        raise CatalogError("The downloaded price list looks incomplete; the current prices are kept.")
    return out


def refresh_prices() -> dict:
    """Download LiteLLM's public price list (GitHub), keep it, and use it."""
    import litellm
    _require_online()
    r = _http_get(litellm.model_cost_map_url)
    if len(r.content) > MAX_PRICES_BYTES:
        raise CatalogError("The downloaded price list is too large; the current prices are kept.")
    try:
        cleaned = _clean_prices(r.json())
    except ValueError:
        raise CatalogError("The downloaded price list was not valid JSON; the current prices are kept.")
    _write_json(PRICES_FILE, {"fetched": time.time(), "source": litellm.model_cost_map_url, "models": cleaned})
    _apply(cleaned)
    return {"count": len(cleaned)}


def _apply(entries: dict) -> None:
    """Merge into LiteLLM's price table (what cost metering reads). Done directly:
    litellm.register_model logs a line per unknown model and takes seconds."""
    import litellm
    for key, entry in entries.items():
        litellm.model_cost.setdefault(key, {}).update(entry)
        if entry.get("mode") == "chat":
            # So a new bare model ID (e.g. a new gpt-... or claude-...) routes to its provider
            group = {"openai": "open_ai_chat_completion_models", "anthropic": "anthropic_models"}.get(
                entry.get("litellm_provider"))
            target = getattr(litellm, group, None) if group else None
            if isinstance(target, (set, list)) and key not in target and "/" not in key:
                target.add(key) if isinstance(target, set) else target.append(key)
    invalidate = getattr(getattr(litellm, "utils", None), "_invalidate_model_cost_lowercase_map", None)
    if callable(invalidate):
        invalidate()


def load_saved_prices() -> int:
    """At start: use the price list downloaded earlier, if any (no network)."""
    saved = _read_json(PRICES_FILE)
    if not saved or not isinstance(saved.get("models"), dict):
        return 0
    try:
        _apply(_clean_prices(saved["models"]))
    except CatalogError:
        return 0
    return len(saved["models"])
