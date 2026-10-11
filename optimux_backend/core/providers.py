"""Provider error classification and per-model cooldowns.

Distinguishes three kinds of LLM provider failure:
- quota:     the account/plan limit is used up (e.g. Gemini free tier per-day
             limit, OpenAI insufficient_quota). Retrying soon is pointless; the
             model is put in cooldown until the provider's retry delay passes.
- transient: overload, short rate limit, timeout, connection trouble. Worth
             one retry; if that fails too, a short cooldown.
- fatal:     everything else (bad request, auth, unknown model). No retry.

Cooldowns are process-wide and in memory: a restart clears them.
"""
import re
import time
from dataclasses import dataclass

import litellm

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
TRANSIENT_ERRORS = ("ServiceUnavailableError", "RateLimitError", "Timeout", "APIConnectionError",
                    "InternalServerError", "MidStreamFallbackError")
QUOTA_MARKERS = ("insufficient_quota", "exceeded your current quota", "resource_exhausted",
                 "quota exceeded", "billing", "credit balance")
DEFAULT_QUOTA_COOLDOWN = 30 * 60   # when a quota error gives no retry delay
TRANSIENT_COOLDOWN = 60            # after a transient error survives its retry
LONG_RETRY_IS_QUOTA = 120          # a retry delay longer than this is treated as quota


@dataclass
class ProviderError:
    kind: str                 # "quota" | "transient" | "fatal"
    summary: str              # one line for the UI
    retry_after: float | None = None
    status: int | None = None


class ModelUnavailable(Exception):
    """Raised instead of calling a model that is in cooldown."""

    def __init__(self, model: str, info: ProviderError):
        super().__init__(f"{model}: {info.summary}")
        self.model = model
        self.info = info


class ProviderFailure(Exception):
    """A classified provider error, raised after retry/cooldown handling."""

    def __init__(self, model: str, info: ProviderError, original: Exception | None = None):
        super().__init__(f"{model}: {info.summary}")
        self.model = model
        self.info = info
        self.original = original


# ------------------------------------------------------------------ parsing

def parse_retry_after(text: str) -> float | None:
    """Seconds from 'Please retry in 6h7m10.7s' or '"retryDelay": "22030s"'."""
    m = re.search(r'"?retryDelay"?\s*:\s*"(\d+(?:\.\d+)?)s"', text)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in ((?:\d+h)?(?:\d+m)?(?:\d+(?:\.\d+)?s)?)", text, re.IGNORECASE)
    if m and m.group(1):
        parts = re.findall(r"(\d+(?:\.\d+)?)([hms])", m.group(1))
        if parts:
            return sum(float(v) * {"h": 3600, "m": 60, "s": 1}[u] for v, u in parts)
    return None


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        h, m = divmod(seconds // 60, 60)
        return f"~{h}h{m:02d}m" if m else f"~{h}h"
    if seconds >= 60:
        return f"~{seconds // 60}m"
    return f"~{seconds}s"


def _status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _is_transient_type(exc: Exception) -> bool:
    for name in TRANSIENT_ERRORS:
        cls = getattr(litellm, name, None)
        if isinstance(cls, type) and isinstance(exc, cls):
            return True
    return False


def classify(exc: Exception) -> ProviderError:
    text = str(exc)
    lower = text.lower()
    status = _status(exc)
    retry_after = parse_retry_after(text)

    is_quota = any(mk in lower for mk in QUOTA_MARKERS) or (
        retry_after is not None and retry_after > LONG_RETRY_IS_QUOTA)
    if is_quota:
        detail = "quota exhausted"
        limit = re.search(r"limit:\s*(\d+)", text)
        if "free_tier" in lower or "free tier" in lower:
            per_day = "perday" in lower.replace("_", "").replace(" ", "")
            detail += f" (free tier{', ' + limit.group(1) + '/day' if limit and per_day else ''})"
        elif "insufficient_quota" in lower:
            detail = "out of credit (insufficient_quota)"
        elif "credit balance" in lower:
            detail = "out of credit (credit balance too low); add credits in the provider's billing settings"
        if retry_after:
            detail += f", resets in {human_duration(retry_after)}"
        return ProviderError("quota", detail, retry_after, status or 429)

    if _is_transient_type(exc) or status in TRANSIENT_STATUS:
        label = {429: "rate limited", 503: "overloaded", 408: "timed out"}.get(status, "unavailable")
        if "timeout" in type(exc).__name__.lower():
            label = "timed out"
        return ProviderError("transient", label + (f" ({status})" if status else ""), retry_after, status)

    if status == 404 or "not_found_error" in lower or "model not found" in lower or (
            isinstance(getattr(litellm, "NotFoundError", None), type) and isinstance(exc, litellm.NotFoundError)):
        return ProviderError("fatal", "model not found: check the model ID (it may be retired or misspelled)", None, 404)

    first_line = text.strip().splitlines()[0] if text.strip() else type(exc).__name__
    if len(first_line) > 200:
        first_line = first_line[:200] + "..."
    return ProviderError("fatal", first_line, None, status)


# ------------------------------------------------------------------ cooldowns

_cooldowns: dict[str, tuple[float, ProviderError]] = {}


def mark_unavailable(model: str, seconds: float, info: ProviderError) -> None:
    _cooldowns[model] = (time.time() + seconds, info)


def cooldown(model: str) -> ProviderError | None:
    """The reason a model is cooling down, with an updated 'resets in', or None."""
    entry = _cooldowns.get(model)
    if not entry:
        return None
    until, info = entry
    remaining = until - time.time()
    if remaining <= 0:
        _cooldowns.pop(model, None)
        return None
    base = info.summary.split(", resets in")[0]
    return ProviderError(info.kind, f"{base}, resets in {human_duration(remaining)}", remaining, info.status)


def is_available(model: str) -> bool:
    return cooldown(model) is None


def record_failure(model: str, exc: Exception) -> ProviderError:
    """Classify an error and put the model in cooldown if it warrants one."""
    info = classify(exc)
    if info.kind == "quota":
        mark_unavailable(model, info.retry_after or DEFAULT_QUOTA_COOLDOWN, info)
    elif info.kind == "transient":
        mark_unavailable(model, TRANSIENT_COOLDOWN, info)
    return info


def active_cooldowns() -> dict:
    out = {}
    for model in list(_cooldowns):
        info = cooldown(model)
        if info:
            out[model] = {"kind": info.kind, "reason": info.summary, "seconds_left": int(info.retry_after or 0)}
    return out


def clear_model(model: str) -> bool:
    """End a model's cooldown. Returns True if it had one."""
    return _cooldowns.pop(model, None) is not None


def clear_models(models) -> list[str]:
    """End the cooldowns of several models; returns the ones that had one."""
    return [m for m in models if clear_model(m)]


def clear() -> None:
    _cooldowns.clear()
