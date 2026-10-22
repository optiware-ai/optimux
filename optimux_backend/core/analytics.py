"""Cost and performance per model, for Dashboard → Performance (v0.5).

Everything comes from what Optimux already records: one usage row per model
call (tokens, cost, duration) and, from v0.5 on, model_events: failed calls
(kind 'error') and escalations away from a model that got stuck (kind
'escalation'). Nothing here calls a model or the network.

Definitions (shown in the Guide):
- latency: the call's wall time, from the request to the last token
  (thinking and tool-call output included); p10 / p50 / p90 over the calls.
- speed: output tokens per second of that wall time, the median over calls
  with at least MIN_SPEED_TOKENS output tokens.
- cost per call, and cost per 1,000 output tokens (what a model charges for
  the work it writes, input included).
- cache share: cached input tokens / all input tokens.
- reliability: calls / (calls + failed calls), counted since events were first
  recorded (older calls have no failure records).
- escalation rate: escalations away from the model / its calls.
"""
import contextlib
import io
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

from optimux_backend.core import store, usage

MIN_SPEED_TOKENS = 20        # shorter outputs say more about latency than about speed
MIN_SPEED_MS = 200
MAX_DAILY_BUCKETS = 120      # longer ranges are grouped by week
TOP_MODELS = 8               # more models are folded into "Other" in the charts (not the table)


def record_event(kind: str, model: str, role: str = "", detail: str = "", project: str | None = None) -> None:
    """Store a failed call ('error') or an escalation ('escalation'). Never raises:
    a statistics write must not break a chat."""
    if not model:
        return
    try:
        if project is None:
            from optimux_backend.core.config_manager import get_settings
            project = get_settings().get("active_project", "")
        conn = store.connect()
        try:
            with conn:
                conn.execute("INSERT INTO model_events (ts, project, role, model, kind, detail) VALUES (?, ?, ?, ?, ?, ?)",
                             (time.time(), project or usage.NO_PROJECT, role or "", model, kind, (detail or "")[:200]))
        finally:
            conn.close()
    except Exception as e:
        print(f"[Optimux] model event not recorded: {e}")


def _events(project: str | None, since: float) -> tuple[list[dict], float | None]:
    conn = store.connect()
    try:
        sql, args = "SELECT * FROM model_events WHERE ts >= ?", [since]
        if project is not None:
            sql += " AND COALESCE(project, '(unknown)') = ?"
            args.append(project)
        rows = conn.execute(sql, args).fetchall()
        first = conn.execute("SELECT MIN(ts) AS t FROM model_events").fetchone()
    finally:
        conn.close()
    return [dict(r) for r in rows], (first["t"] if first and first["t"] else None)


def color_order() -> list[str]:
    """Every model ever used, by all-time cost (then calls). The page gives the
    first TOP_MODELS of them a fixed colour, so a filter never repaints a model."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT model, SUM(COALESCE(cost, 0)) AS c, COUNT(*) AS n FROM usage "
                            "GROUP BY model ORDER BY c DESC, n DESC, model").fetchall()
    finally:
        conn.close()
    return [r["model"] for r in rows if r["model"]]


def provider_of(model: str, cards: dict | None = None) -> dict:
    """Who serves a model: the provider (from the LiteLLM route, e.g.
    'anthropic' for 'claude-sonnet-5') and, for a model with its own Base URL,
    that server's host. Local only: nothing is called."""
    card = (cards or {}).get(model) or {}
    endpoint = urlparse(card.get("api_base") or "").hostname or None
    provider = model.split("/", 1)[0] if "/" in model else None
    if not provider:
        try:
            import litellm
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                provider = litellm.get_llm_provider(model)[1]
        except Exception:
            provider = None
    return {"provider": provider, "endpoint": endpoint}


def _cards() -> dict:
    try:
        from optimux_backend.core.config_manager import get_all_models
        return get_all_models()
    except Exception:
        return {}


def known_projects() -> list[str]:
    """Every project with recorded calls, archived and deleted ones too (for the filter)."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT DISTINCT COALESCE(project, '(unknown)') AS p FROM usage ORDER BY p").fetchall()
    finally:
        conn.close()
    return [r["p"] for r in rows]


def percentile(values: list[float], q: float) -> float | None:
    """Linear interpolation between closest ranks (like numpy's default)."""
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _start(days: int, now: float) -> float:
    if days <= 0:
        return 0.0
    day0 = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return day0.timestamp()


def _bucket_keys(start: float, now: float, weekly: bool) -> list[str]:
    d = datetime.fromtimestamp(start).date()
    end = datetime.fromtimestamp(now).date()
    if weekly:
        d -= timedelta(days=d.weekday())
    keys = []
    while d <= end:
        keys.append(d.isoformat())
        d += timedelta(days=7 if weekly else 1)
    return keys


def _bucket(ts: float, weekly: bool) -> str:
    d = datetime.fromtimestamp(ts).date()
    if weekly:
        d -= timedelta(days=d.weekday())
    return d.isoformat()


def _empty_model() -> dict:
    return {"calls": 0, "cost": 0.0, "calls_without_cost": 0, "estimated": 0, "prompt_tokens": 0,
            "cached_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "roles": {}, "projects": {},
            "_durations": [], "_speeds": [], "errors": 0, "error_kinds": {}, "escalations": 0}


def analytics(days: int = 30, project: str | None = None, now: float | None = None) -> dict:
    """Per-model cost and performance for the last `days` days (0 = all time),
    optionally for one project."""
    now = now or time.time()
    since = _start(days, now)
    entries = usage.read_entries(project=project, since=since)
    events, events_since = _events(project, since)

    models: dict[str, dict] = {}
    for e in entries:
        m = models.setdefault(e.get("model") or "?", _empty_model())
        m["calls"] += 1
        if e.get("cost") is None:
            m["calls_without_cost"] += 1
        else:
            m["cost"] += e["cost"]
        if e.get("estimated"):
            m["estimated"] += 1
        for k in ("prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens"):
            m[k] += int(e.get(k) or 0)
        role = e.get("role") or "?"
        m["roles"][role] = m["roles"].get(role, 0) + 1
        proj = e.get("project") or "(unknown)"
        m["projects"][proj] = m["projects"].get(proj, 0) + 1
        ms = int(e.get("duration_ms") or 0)
        if ms > 0:
            m["_durations"].append(ms)
            out = int(e.get("completion_tokens") or 0)
            if out >= MIN_SPEED_TOKENS and ms >= MIN_SPEED_MS:
                m["_speeds"].append(out / (ms / 1000))
    for ev in events:
        m = models.get(ev["model"])
        if m is None:
            m = models.setdefault(ev["model"], _empty_model())
        if ev["kind"] == "error":
            m["errors"] += 1
            k = ev.get("detail") or "error"
            m["error_kinds"][k] = m["error_kinds"].get(k, 0) + 1
        elif ev["kind"] == "escalation":
            m["escalations"] += 1

    total_cost = sum(m["cost"] for m in models.values())
    rows = []
    for model_id, m in models.items():
        d, sp = m.pop("_durations"), m.pop("_speeds")
        calls, out = m["calls"], m["completion_tokens"]
        priced = calls - m["calls_without_cost"]
        rows.append({
            "model": model_id, **m,
            "share": (m["cost"] / total_cost) if total_cost > 0 else 0.0,
            "cost_per_call": (m["cost"] / priced) if priced else None,
            "cost_per_1k_out": (m["cost"] / out * 1000) if out and priced == calls else None,
            "p10_ms": percentile(d, 0.10), "p50_ms": percentile(d, 0.50), "p90_ms": percentile(d, 0.90),
            "timed_calls": len(d),
            "speed_tps": percentile(sp, 0.50), "speed_calls": len(sp),
            "cache_share": (m["cached_tokens"] / m["prompt_tokens"]) if m["prompt_tokens"] else None,
            "reliability": (calls / (calls + m["errors"])) if (calls + m["errors"]) else None,
            "escalation_rate": (m["escalations"] / calls) if calls else None,
        })
    rows.sort(key=lambda r: (-r["cost"], -r["calls"], r["model"]))
    cards = _cards()
    for r in rows:
        r.update(provider_of(r["model"], cards))

    # Routing at work: calls and spending per role (Conductor, Simple, Complex...)
    roles: dict[str, dict] = {}
    for e in entries:
        b = roles.setdefault(e.get("role") or "?", {"calls": 0, "cost": 0.0, "calls_without_cost": 0, "models": {}})
        b["calls"] += 1
        if e.get("cost") is None:
            b["calls_without_cost"] += 1
        else:
            b["cost"] += e["cost"]
        mid = e.get("model") or "?"
        b["models"][mid] = b["models"].get(mid, 0) + 1
    role_rows = sorted(({"role": k, **v} for k, v in roles.items()), key=lambda r: (-r["calls"], r["role"]))

    # Over time: per day (or week, for long ranges), cost and calls per model
    first_ts = entries[0]["ts"] if entries else now
    start = since or datetime.fromtimestamp(first_ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    weekly = (now - start) / 86400 > MAX_DAILY_BUCKETS
    keys = _bucket_keys(start, now, weekly)
    series = {k: {} for k in keys}
    for e in entries:
        b = series.setdefault(_bucket(e["ts"], weekly), {})
        cell = b.setdefault(e.get("model") or "?", {"cost": 0.0, "calls": 0})
        cell["cost"] += e.get("cost") or 0.0
        cell["calls"] += 1
    timeline = [{"bucket": k, "models": series[k]} for k in sorted(series)]

    # Where the money goes: project x model (only across projects)
    matrix = None
    if project is None:
        cells: dict[tuple, dict] = {}
        for e in entries:
            key = (e.get("project") or "(unknown)", e.get("model") or "?")
            c = cells.setdefault(key, {"cost": 0.0, "calls": 0})
            c["cost"] += e.get("cost") or 0.0
            c["calls"] += 1
        projects = sorted({p for p, _ in cells}, key=lambda p: -sum(c["cost"] for (pp, _), c in cells.items() if pp == p))
        matrix = {"projects": projects,
                  "cells": [{"project": p, "model": mm, **c} for (p, mm), c in cells.items()]}

    all_d = [int(e.get("duration_ms") or 0) for e in entries if int(e.get("duration_ms") or 0) > 0]
    totals = {
        "calls": len(entries), "cost": total_cost,
        "calls_without_cost": sum(r["calls_without_cost"] for r in rows),
        "p50_ms": percentile(all_d, 0.5),
        "errors": sum(r["errors"] for r in rows), "escalations": sum(r["escalations"] for r in rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "cached_tokens": sum(r["cached_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
    }
    totals["reliability"] = (totals["calls"] / (totals["calls"] + totals["errors"])) \
        if (totals["calls"] + totals["errors"]) else None
    return {"days": days, "project": project, "since": since or start, "now": now, "weekly": weekly,
            "events_since": events_since, "totals": totals, "models": rows, "timeline": timeline,
            "matrix": matrix, "roles": role_rows, "top": TOP_MODELS, "color_order": color_order(),
            "projects": known_projects()}
