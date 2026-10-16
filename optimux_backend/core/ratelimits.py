"""The rate limits of the user's own keys, as the providers report them.

No public list has them: they depend on the account and its tier. So Optimux
learns them from calls it makes anyway (Test, chat, routing, memory, skills):
- most providers send the limits back with every reply, in headers such as
  x-ratelimit-limit-requests (OpenAI, Groq, ...) or
  anthropic-ratelimit-input-tokens-limit (Anthropic);
- Google sends none; when a limit is hit, its error names it (quotaId and
  quotaValue), and that limit is kept.
Nothing extra is sent to anyone. Kept in ~/.optimux/catalog/ratelimits.json
(mode 600) per model ID: the limits only, never a key.
"""
import json
import os
import re
import threading
import time

from optimux_backend.core import paths

FILE = "ratelimits.json"
WRITE_EVERY = 60          # seconds: the "remaining" counts change on every call
_lock = threading.Lock()
_cache: dict | None = None
_last_write = 0.0

ANTHROPIC = re.compile(r"^anthropic-ratelimit-(requests|tokens|input-tokens|output-tokens)-(limit|remaining)$")
GENERIC = re.compile(r"^x-ratelimit-(limit|remaining)-([a-z0-9-]+)$")
PERIODS = {"minute": "minute", "min": "minute", "hour": "hour", "day": "day", "month": "month",
           "second": "second", "sec": "second"}


def model_key(model: str) -> str:
    """One key per model, as the catalog writes IDs (bare OpenAI/Anthropic IDs)."""
    for prefix in ("anthropic/", "openai/"):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _path() -> str:
    return os.path.join(paths.HOME, "catalog", FILE)


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_path()) as f:
                data = json.load(f)
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save(force: bool = False) -> None:
    global _last_write
    if not force and time.time() - _last_write < WRITE_EVERY:
        return
    folder = os.path.join(paths.HOME, "catalog")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    paths.write_private(_path(), json.dumps(_load()))
    _last_write = time.time()


def reset_cache() -> None:
    """For tests (and a data restore): read the file again."""
    global _cache, _last_write
    _cache, _last_write = None, 0.0


def _int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def parse_headers(model: str, headers: dict) -> list[dict]:
    """[{what, per, limit, remaining}] from a reply's headers (LiteLLM prefixes
    the provider's own ones with "llm_provider-")."""
    items: dict[tuple, dict] = {}
    for raw, value in (headers or {}).items():
        name = str(raw).lower()
        if name.startswith("llm_provider-"):
            name = name[len("llm_provider-"):]
        m = ANTHROPIC.match(name)
        if m:
            what, field, per = m.group(1).replace("-", " "), m.group(2), "minute"
        else:
            m = GENERIC.match(name)
            if not m:
                continue
            field, rest = m.group(1), m.group(2)
            parts = rest.split("-")
            per = PERIODS.get(parts[-1]) if len(parts) > 1 else None
            what = " ".join(parts[:-1] if per else parts)
            what = {"req": "requests", "request": "requests", "token": "tokens"}.get(what, what)
            if not per:
                # OpenAI and most others: per minute; Groq's request limit is per day
                per = "day" if what == "requests" and model.startswith("groq/") else "minute"
        number = _int(value)
        if number is None:
            continue
        item = items.setdefault((what, per), {"what": what, "per": per, "limit": None, "remaining": None})
        item[field] = number
    return [i for i in items.values() if i["limit"] is not None]


QUOTA_RE = re.compile(r'"?quotaId"?\s*:\s*"([^"]+)".*?"?quotaValue"?\s*:\s*"?(\d+)', re.DOTALL)


def parse_error(text: str) -> list[dict]:
    """The limits named in a "quota exceeded" error (Google: quotaId + quotaValue)."""
    out = {}
    for quota_id, value in QUOTA_RE.findall(text or ""):
        q = quota_id.lower()
        what = "input tokens" if "token" in q else "requests" if "request" in q else quota_id
        per = "day" if "perday" in q else "minute" if "perminute" in q else "hour" if "perhour" in q else None
        if per is None:
            continue
        out[(what, per)] = {"what": what, "per": per, "limit": int(value), "remaining": 0,
                            "hit": True, "free_tier": "freetier" in q}
    return list(out.values())


def headers_of(response) -> dict:
    hidden = getattr(response, "_hidden_params", None) or {}
    headers = hidden.get("additional_headers") if isinstance(hidden, dict) else None
    return headers if isinstance(headers, dict) else {}


def record_response(model: str, response) -> None:
    """After a successful call (a response or a finished stream)."""
    try:
        items = parse_headers(model, headers_of(response))
        if not items:
            return
        with _lock:
            data = _load()
            old = data.get(model_key(model)) or {}
            changed = [(i["what"], i["per"], i["limit"]) for i in items] != \
                      [(i["what"], i["per"], i["limit"]) for i in old.get("items", []) if not i.get("hit")]
            data[model_key(model)] = {"items": items, "seen": time.time(), "source": "headers"}
            _save(force=changed)
    except Exception as e:                       # never let this break a reply
        print(f"[Optimux] rate limits not recorded: {e}")


def record_error(model: str, exc: Exception) -> None:
    """After a failed call: keep a limit the error names (merged with known ones)."""
    try:
        items = parse_error(str(exc))
        if not items:
            return
        with _lock:
            data = _load()
            entry = data.get(model_key(model)) or {"items": [], "source": "error"}
            keep = [i for i in entry.get("items", []) if (i["what"], i["per"]) not in
                    {(n["what"], n["per"]) for n in items}]
            entry.update(items=keep + items, seen=time.time(), hit_at=time.time())
            data[model_key(model)] = entry
            _save(force=True)
    except Exception as e:
        print(f"[Optimux] rate limits not recorded: {e}")


def get(model: str) -> dict | None:
    with _lock:
        entry = _load().get(model_key(model))
    return dict(entry) if entry else None


def flush() -> None:
    with _lock:
        if _cache is not None:
            _save(force=True)
