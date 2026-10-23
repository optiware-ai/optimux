"""Token usage and cost metering.

Every LLM call (Conductor and executors) is stored as one row in the usage
table of the SQLite store (~/.optimux/optimux.db). The old JSON-lines log is
imported once and renamed to usage.jsonl.migrated. Recording never raises:
metering must not break a chat.
"""
import json
import os
import time
from datetime import datetime, timedelta

from optimux_backend.core import store

USAGE_FILE = store.paths.LEGACY_USAGE_LOG  # legacy log, imported once
COLUMNS = ("ts", "session", "project", "role", "model", "prompt_tokens", "completion_tokens",
           "cached_tokens", "reasoning_tokens", "cost", "duration_ms", "estimated", "skill", "plan_id", "step",
           "ref_model", "ref_cost", "task_id")
_imported_for: str | None = None
NO_PROJECT = "(no project)"
LOCAL_PROVIDERS = ("ollama/", "ollama_chat/")  # run on your machine: no per-token cost


def is_local(model: str) -> bool:
    return model.startswith(LOCAL_PROVIDERS)


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def extract_usage(usage) -> dict:
    """Normalize a LiteLLM usage object (or dict) to plain token counts.

    prompt_tokens includes cached tokens; cached_tokens is the cached share.
    """
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0}
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    details = get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _int(details.get("cached_tokens"))
    else:
        cached = _int(getattr(details, "cached_tokens", None))
    cached = cached or _int(get("cache_read_input_tokens")) or _int(get("cached_tokens"))
    # Thinking is billed as output: completion_tokens includes it, this is its share
    out_details = get("completion_tokens_details")
    if isinstance(out_details, dict):
        reasoning = _int(out_details.get("reasoning_tokens"))
    else:
        reasoning = _int(getattr(out_details, "reasoning_tokens", None))
    reasoning = reasoning or _int(get("reasoning_tokens"))
    return {
        "prompt_tokens": _int(get("prompt_tokens")),
        "completion_tokens": _int(get("completion_tokens")),
        "cached_tokens": cached,
        "reasoning_tokens": reasoning,
    }


def _custom_prices(model: str) -> dict | None:
    """Per-model prices (USD per million tokens) set in Settings > Models."""
    try:
        from optimux_backend.core.config_manager import get_all_models
        info = get_all_models().get(model, {})
    except Exception:
        return None
    if info.get("input_cost_per_mtok") is None or info.get("output_cost_per_mtok") is None:
        return None
    return info


def _via_base_url(model: str) -> bool:
    """True for a model sent to its own server (a proxy or reseller)."""
    try:
        from optimux_backend.core.config_manager import get_all_models
        return bool(get_all_models().get(model, {}).get("api_base"))
    except Exception:
        return False


def cost_for(model: str, tokens: dict) -> float | None:
    """USD cost of one call, or None when no price is known for the model."""
    p, c, cached = tokens["prompt_tokens"], tokens["completion_tokens"], tokens["cached_tokens"]
    prices = _custom_prices(model)
    if prices is None and is_local(model):
        return 0.0
    if prices is None and _via_base_url(model):
        return None     # another server's prices: LiteLLM's list is for the model's maker (v0.4-5o)
    if prices:
        cached_rate = prices.get("cached_input_cost_per_mtok")
        if cached_rate is None:
            cached_rate = prices["input_cost_per_mtok"]
        return (
            (p - cached) * prices["input_cost_per_mtok"]
            + cached * cached_rate
            + c * prices["output_cost_per_mtok"]
        ) / 1_000_000
    try:
        import litellm
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model, prompt_tokens=p, completion_tokens=c, cache_read_input_tokens=cached,
        )
        total = prompt_cost + completion_cost
        # LiteLLM returns 0 for some models it knows but has no price for
        return total if total > 0 or (p + c) == 0 else None
    except Exception:
        return None


# The savings view (v0.6, IM-08). Each call also gets what the same tokens would
# have cost on the reference model: the Complex model at the time of the call.
# The Conductor's reference is 0: with the strong model for everything there is
# no routing call, so it is pure overhead. Model tests are not work: no reference.
REF_ZERO_ROLES = ("conductor",)
REF_SKIP_ROLES = ("test",)


def reference_model() -> str:
    """The model calls are compared with: the Complex model of the routing."""
    try:
        from optimux_backend.core.config_manager import get_routing_config
        return (get_routing_config() or {}).get("complex_model", "") or ""
    except Exception:
        return ""


def reference_cost(role: str, tokens: dict, ref_model: str) -> float | None:
    """What these tokens would have cost on ref_model for this role; None when
    it can't be known (no reference model, or no price for it)."""
    if role in REF_SKIP_ROLES or not ref_model:
        return None
    if role in REF_ZERO_ROLES:
        return 0.0
    return cost_for(ref_model, tokens)


def record(session_id: str, role: str, model: str, usage, duration_ms: int | None = None,
           project: str = "", skill: str | None = None, plan_id: str | None = None,
           step: int | None = None, task_id: str | None = None) -> dict:
    """Append one call to the usage log and return the entry. plan_id and step tie a
    call to a plan (v0.4 plan mode); both stay empty outside plans. task_id ties it
    to the request it served (v0.6, IM-09)."""
    tokens = extract_usage(usage)
    entry = {
        "ts": time.time(),
        "session": session_id,
        "project": project or NO_PROJECT,
        "skill": skill or None,
        "role": role,
        "model": model,
        **tokens,
        "cost": cost_for(model, tokens),
        "duration_ms": duration_ms,
    }
    ref = reference_model()
    if role not in REF_SKIP_ROLES and ref:
        entry["ref_model"] = ref
        entry["ref_cost"] = reference_cost(role, tokens, ref)
    if plan_id:
        entry["plan_id"] = plan_id
        entry["step"] = step
    if task_id:
        entry["task_id"] = task_id
    if isinstance(usage, dict) and usage.get("estimated"):
        entry["estimated"] = True
    try:
        insert(entry)
    except Exception as e:
        print(f"[Optimux] usage write failed: {e}")
    return entry


def _empty() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0,
            "cost": 0.0, "calls_without_cost": 0, "duration_ms": 0, "timed_calls": 0, "avg_duration_ms": None}


def _add(bucket: dict, e: dict):
    bucket["calls"] += 1
    for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens"):
        bucket[k] += _int(e.get(k))
    if isinstance(e.get("cost"), (int, float)):
        bucket["cost"] += e["cost"]
    else:
        bucket["calls_without_cost"] += 1
    if _int(e.get("duration_ms")) > 0:
        bucket["duration_ms"] += e["duration_ms"]
        bucket["timed_calls"] += 1
        bucket["avg_duration_ms"] = round(bucket["duration_ms"] / bucket["timed_calls"])


def _connect():
    """Store connection, importing the legacy JSON-lines log on first use."""
    global _imported_for
    conn = store.connect()
    if _imported_for != store.DB_PATH:
        _imported_for = store.DB_PATH
        import_jsonl(conn)
    return conn


def ensure_ready() -> None:
    """Create the schema and import the legacy log now (called at startup, before the backup)."""
    _connect().close()


def insert(entry: dict, conn=None) -> None:
    """Store one usage entry (missing fields default to 0/None)."""
    own = conn is None
    conn = conn or _connect()
    try:
        row = {k: entry.get(k) for k in COLUMNS}
        row["ts"] = row["ts"] if row["ts"] is not None else time.time()
        for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens"):
            row[k] = _int(row[k])
        row["estimated"] = 1 if entry.get("estimated") else 0
        with conn:
            conn.execute(f"INSERT INTO usage ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                         [row[k] for k in COLUMNS])
    finally:
        if own:
            conn.close()


def import_jsonl(conn) -> int:
    """Import the legacy usage.jsonl once, then rename it to .migrated (kept, never deleted)."""
    if not os.path.exists(USAGE_FILE):
        return 0
    count = 0
    with open(USAGE_FILE, "r") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(e, dict):
                continue
            if "project" not in e:
                e["project"] = "(unknown)"  # written before projects were recorded
            insert(e, conn)
            count += 1
    os.replace(USAGE_FILE, USAGE_FILE + ".migrated")
    print(f"[Optimux] imported {count} usage entries from {USAGE_FILE}")
    return count


def read_entries(project: str | None = None, since: float = 0) -> list[dict]:
    conn = _connect()
    try:
        sql, args = "SELECT * FROM usage WHERE ts >= ?", [since]
        if project is not None:
            sql += " AND COALESCE(project, '(unknown)') = ?"
            args.append(project)
        rows = conn.execute(sql + " ORDER BY ts", args).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        e = dict(r)
        e["project"] = e.get("project") or "(unknown)"
        e["estimated"] = bool(e.get("estimated"))
        out.append(e)
    return out


def daily(days: int = 30, project: str | None = None, now: float | None = None) -> list[dict]:
    """Per local day and model for the last `days` days (today included):
    calls, tokens and cost. Days without usage are omitted."""
    now = now or time.time()
    start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    sql = ("SELECT date(ts, 'unixepoch', 'localtime') AS day, model, COUNT(*) AS calls, "
           "SUM(prompt_tokens) AS prompt_tokens, SUM(completion_tokens) AS completion_tokens, "
           "SUM(cached_tokens) AS cached_tokens, COALESCE(SUM(cost), 0) AS cost, "
           "SUM(CASE WHEN cost IS NULL THEN 1 ELSE 0 END) AS calls_without_cost "
           "FROM usage WHERE ts >= ?")
    args = [start.timestamp()]
    if project is not None:
        sql += " AND COALESCE(project, '(unknown)') = ?"
        args.append(project)
    conn = _connect()
    try:
        rows = conn.execute(sql + " GROUP BY day, model ORDER BY day, model", args).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def project_activity() -> dict:
    """Per project: all-time cost and calls, and the last time it was used."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT COALESCE(project, '(unknown)') AS project, COUNT(*) AS calls, COALESCE(SUM(cost), 0) AS cost, "
            "MAX(ts) AS last_ts FROM usage GROUP BY COALESCE(project, '(unknown)')").fetchall()
    finally:
        conn.close()
    return {r["project"]: dict(r) for r in rows}


def month_start(now: float | None = None) -> float:
    return datetime.fromtimestamp(now or time.time()).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def month_totals(project: str, now: float | None = None) -> dict:
    """This calendar month's cost for one project, computed in SQL (used on every model call)."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(cost), 0) AS cost, "
            "COALESCE(SUM(CASE WHEN cost IS NULL THEN 1 ELSE 0 END), 0) AS without_cost "
            "FROM usage WHERE ts >= ? AND COALESCE(project, '(unknown)') = ?",
            (month_start(now), project)).fetchone()
    finally:
        conn.close()
    return {"calls": row["calls"], "cost": float(row["cost"]), "calls_without_cost": row["without_cost"]}


def recent_average(role: str, n: int = 5) -> dict | None:
    """Average cost of the last `n` priced calls with this role (any project), used to warn
    before an action that costs money. None when there is no priced call yet."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT cost, model FROM usage WHERE role = ? AND cost IS NOT NULL "
                            "ORDER BY ts DESC LIMIT ?", (role, n)).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    return {"avg": sum(r["cost"] for r in rows) / len(rows), "n": len(rows), "model": rows[0]["model"]}


def plan_spent(plan_id: str, role: str | None = None) -> dict:
    """Calls and known cost recorded for one plan (optionally one role, e.g. planner)."""
    sql = ("SELECT COUNT(*) AS calls, COALESCE(SUM(cost), 0) AS cost, "
           "SUM(CASE WHEN cost IS NULL THEN 1 ELSE 0 END) AS unpriced FROM usage WHERE plan_id = ?")
    args = [plan_id]
    if role:
        sql += " AND role = ?"
        args.append(role)
    conn = _connect()
    try:
        row = conn.execute(sql, args).fetchone()
    finally:
        conn.close()
    return {"calls": row["calls"], "cost": float(row["cost"]), "unpriced": row["unpriced"] or 0}


def role_totals(role: str, since: float = 0) -> dict:
    """Calls and known cost for one role since a time (all projects)."""
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS calls, COALESCE(SUM(cost), 0) AS cost FROM usage "
                           "WHERE role = ? AND ts >= ?", (role, since)).fetchone()
    finally:
        conn.close()
    return {"calls": row["calls"], "cost": float(row["cost"])}


def summary(now: float | None = None, project: str | None = None) -> dict:
    """Totals for today and this calendar month (local time), the last 7 days and all time,
    each broken down by role and by model."""
    now = now if now is not None else time.time()
    start_today = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    start_week = now - timedelta(days=7).total_seconds()
    start_month = month_start(now)
    periods = {"today": start_today, "last_7_days": start_week, "this_month": start_month, "all_time": 0}
    out = {name: {"total": _empty(), "by_role": {}, "by_model": {}, "by_project": {}, "by_skill": {},
                  "skill_drafts": {}, "memory_updates": {}} for name in periods}
    for e in read_entries(project=project):
        ts = e.get("ts", 0)
        for name, start in periods.items():
            if ts >= start:
                p = out[name]
                _add(p["total"], e)
                _add(p["by_role"].setdefault(e.get("role", "?"), _empty()), e)
                _add(p["by_model"].setdefault(e.get("model", "?"), _empty()), e)
                # entries written before projects were recorded have no "project"
                _add(p["by_project"].setdefault(e.get("project", "(unknown)"), _empty()), e)
                if e.get("role") == "curator":
                    # Automatic project-memory updates, per project (also under by_role "curator")
                    _add(p["memory_updates"].setdefault(e.get("project", "(unknown)"), _empty()), e)
                if e.get("skill"):
                    # Using a skill and drafting it ("Save as skill") are counted separately
                    bucket = "skill_drafts" if e.get("role") == "skill_draft" else "by_skill"
                    _add(p[bucket].setdefault(e["skill"], _empty()), e)
    return out


def estimate(model: str, messages: list, completion_text: str) -> dict:
    """Fallback when a provider returns no usage for a streamed call.
    Counts with LiteLLM's tokenizer; tool-call arguments are not counted."""
    try:
        import litellm
        prompt = litellm.token_counter(model=model, messages=messages)
        completion = litellm.token_counter(model=model, text=completion_text or "")
    except Exception:
        prompt, completion = 0, 0
    return {"prompt_tokens": prompt, "completion_tokens": completion, "cached_tokens": 0, "estimated": True}


def zero_reference(task_id: str, role: str, step: int | None = None) -> int:
    """A model that got stuck and handed over (an escalation): its calls in this
    request (or plan step) would not have happened with everything on the
    reference model, so their reference cost becomes 0: pure overhead in the
    savings view (v0.6, IM-09). Returns the rows changed; never raises."""
    if not task_id:
        return 0
    sql, args = "UPDATE usage SET ref_cost = 0 WHERE task_id = ? AND role = ? AND ref_model IS NOT NULL", [task_id, role]
    if step is not None:
        sql += " AND step = ?"
        args.append(step)
    try:
        conn = _connect()
        try:
            with conn:
                return conn.execute(sql, args).rowcount
        finally:
            conn.close()
    except Exception as e:
        print(f"[Optimux] usage update failed: {e}")
        return 0
