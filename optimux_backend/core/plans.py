"""Plans (v0.4 plan mode): a big task split into steps, each run on the cheapest tier.

A plan is written by the Complex model as JSON (see the design doc):

    {"goal": "...", "steps": [{"id": 1, "title": "...", "tier": "simple"|"complex",
     "do": "...", "done_when": "...", "files": ["..."]}, ...], "check": "..."}

This module only parses, checks and stores plans; nothing here calls a model or
runs a step. A stored plan keeps, per step, its run state: status, result note,
model, cost and times. It is saved after every change, so a stop, an error or a
restart leaves it at a known step (see mark_interrupted).
"""
import json
import re
import secrets
import time

from optimux_backend.core import store

TIERS = ("simple", "complex")
MIN_STEPS, MAX_STEPS = 2, 8
MAX_GOAL = 300
MAX_TITLE = 120
MAX_DO = 600
MAX_DONE_WHEN = 300
MAX_CHECK = 600
MAX_FILES = 20
MAX_FILE = 300
MAX_RESULT = 1500            # a step's result note, as kept (the prompt asks for 150 words)

# Plan statuses: waiting (shown, not started) · running · paused (stopped, stuck, interrupted,
# budget) · done · cancelled. Step statuses: pending · running · done · blocked · skipped · failed.
PLAN_STATUSES = ("waiting", "running", "paused", "done", "cancelled")
STEP_STATUSES = ("pending", "running", "done", "blocked", "skipped", "failed")
OPEN = ("waiting", "running", "paused")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{16}$")


class PlanError(Exception):
    """A plan that can't be used or found; the message says why."""


# ------------------------------------------------------------------ reading the planner's answer

def parse(text: str) -> dict:
    """The JSON object in a model's answer: plain, or inside a ``` block, or the
    outermost {...} in surrounding text."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            data = json.loads(c)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise PlanError("The plan is not valid JSON.")


def _text(value, limit: int, what: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise PlanError(f"{what} is missing.")
    value = " ".join(value.split())
    if len(value) > limit:
        raise PlanError(f"{what} is longer than {limit} characters.")
    return value


def validate(raw) -> dict:
    """A clean plan from the planner's JSON (dict or text). Raises PlanError with a
    reason the planner can be told on its one retry."""
    data = parse(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise PlanError("The plan must be a JSON object.")
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise PlanError("The plan has no steps list.")
    if len(steps) < MIN_STEPS:
        raise PlanError(f"A plan needs at least {MIN_STEPS} steps.")
    if len(steps) > MAX_STEPS:
        raise PlanError(f"A plan has at most {MAX_STEPS} steps; merge some.")
    clean = []
    for n, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            raise PlanError(f"Step {n} is not an object.")
        tier = s.get("tier")
        if tier not in TIERS:
            raise PlanError(f"Step {n}: tier must be \"simple\" or \"complex\".")
        files = s.get("files") or []
        if not isinstance(files, list) or len(files) > MAX_FILES or not all(
                isinstance(f, str) and f.strip() and len(f) <= MAX_FILE for f in files):
            raise PlanError(f"Step {n}: files must be a list of at most {MAX_FILES} paths.")
        clean.append({
            "id": n,                                   # renumbered: the planner's ids are not trusted
            "title": _text(s.get("title"), MAX_TITLE, f"Step {n}'s title"),
            "tier": tier,
            "do": _text(s.get("do"), MAX_DO, f"Step {n}'s instructions (do)"),
            "done_when": _text(s.get("done_when"), MAX_DONE_WHEN, f"Step {n}'s done_when"),
            "files": [f.strip() for f in files],
        })
    if all(s["tier"] == "complex" for s in clean):
        raise PlanError("Every step is complex: there is nothing to save with a plan.")
    return {"goal": _text(data.get("goal"), MAX_GOAL, "The goal"),
            "steps": clean,
            "check": _text(data.get("check"), MAX_CHECK, "The check", required=False)}


# ------------------------------------------------------------------ storing

def _new_step_state(step: dict) -> dict:
    return {**step, "status": "pending", "result": "", "model": None, "cost": 0.0, "calls": 0,
            "started": None, "finished": None, "escalated": False}


def _row(r) -> dict:
    plan = json.loads(r["plan"])
    return {"id": r["id"], "session": r["session"], "project": r["project"], "goal": r["goal"],
            "status": r["status"], "created": r["created"], "updated": r["updated"], **plan}


def create(session: str, project: str, plan: dict, request: str = "") -> dict:
    """Store a checked plan (status waiting). Returns it with its id and step states."""
    plan = validate(plan)
    now = time.time()
    body = {"steps": [_new_step_state(s) for s in plan["steps"]], "check": plan["check"],
            "request": (request or "")[:2000], "note": "", "interrupted": False}
    pid = secrets.token_urlsafe(12)
    conn = store.connect()
    try:
        with conn:
            conn.execute("INSERT INTO plans (id, session, project, goal, plan, status, created, updated) "
                         "VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?)",
                         (pid, session, project or "", plan["goal"], json.dumps(body), now, now))
    finally:
        conn.close()
    return get(pid)


def get(pid: str) -> dict:
    if not ID_RE.fullmatch(pid or ""):
        raise PlanError("No such plan.")
    conn = store.connect()
    try:
        r = conn.execute("SELECT * FROM plans WHERE id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise PlanError("No such plan.")
    return _row(r)


def for_session(session: str) -> list[dict]:
    """The plans of a chat, oldest first."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT * FROM plans WHERE session = ? ORDER BY created", (session,)).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def open_plan(session: str) -> dict | None:
    """The chat's plan that is not finished (waiting, running or paused), if any."""
    for p in reversed(for_session(session)):
        if p["status"] in OPEN:
            return p
    return None


def _save(plan: dict) -> dict:
    body = {k: plan[k] for k in ("steps", "check", "request", "note", "interrupted")}
    plan["updated"] = time.time()
    conn = store.connect()
    try:
        with conn:
            conn.execute("UPDATE plans SET plan = ?, status = ?, updated = ? WHERE id = ?",
                         (json.dumps(body), plan["status"], plan["updated"], plan["id"]))
    finally:
        conn.close()
    return plan


def set_status(pid: str, status: str, note: str | None = None) -> dict:
    if status not in PLAN_STATUSES:
        raise PlanError(f"Unknown plan status '{status}'.")
    plan = get(pid)
    plan["status"] = status
    if note is not None:
        plan["note"] = note[:300]
    if status == "running":
        plan["interrupted"] = False
    return _save(plan)


STEP_FIELDS = ("status", "result", "model", "cost", "calls", "started", "finished", "escalated", "tier")


def update_step(pid: str, step_id: int, **fields) -> dict:
    """Change one step's run state (only STEP_FIELDS). Returns the plan."""
    unknown = set(fields) - set(STEP_FIELDS)
    if unknown:
        raise PlanError(f"Unknown step fields: {', '.join(sorted(unknown))}")
    if "status" in fields and fields["status"] not in STEP_STATUSES:
        raise PlanError(f"Unknown step status '{fields['status']}'.")
    if "tier" in fields and fields["tier"] not in TIERS:
        raise PlanError("A step's tier is simple or complex.")
    if "result" in fields:
        fields["result"] = (fields["result"] or "")[:MAX_RESULT]
    plan = get(pid)
    step = next((s for s in plan["steps"] if s["id"] == step_id), None)
    if step is None:
        raise PlanError(f"The plan has no step {step_id}.")
    step.update(fields)
    return _save(plan)


def add_cost(pid: str, step_id: int, cost: float | None, model: str | None = None) -> dict:
    """Count one metered call in a step (cost may be unknown)."""
    plan = get(pid)
    step = next((s for s in plan["steps"] if s["id"] == step_id), None)
    if step is None:
        raise PlanError(f"The plan has no step {step_id}.")
    step["calls"] = step.get("calls", 0) + 1
    if isinstance(cost, (int, float)):
        step["cost"] = round(step.get("cost", 0.0) + cost, 8)
    if model:
        step["model"] = model
    return _save(plan)


def edit_waiting(pid: str, tiers: dict | None = None, remove: list[int] | None = None) -> dict:
    """Before Run: change steps' tiers and remove steps (the MVP's Edit). The plan
    must still have MIN_STEPS steps and at least one Simple step."""
    plan = get(pid)
    if plan["status"] != "waiting":
        raise PlanError("Only a plan that hasn't started can be edited.")
    remove = set(remove or [])
    steps = [s for s in plan["steps"] if s["id"] not in remove]
    for s in steps:
        t = (tiers or {}).get(str(s["id"]), (tiers or {}).get(s["id"]))
        if t is not None:
            if t not in TIERS:
                raise PlanError("A step's tier is simple or complex.")
            s["tier"] = t
    if len(steps) < MIN_STEPS:
        raise PlanError(f"A plan needs at least {MIN_STEPS} steps; cancel it instead.")
    if all(s["tier"] == "complex" for s in steps):
        raise PlanError("Every step would be complex: there is nothing to save with a plan. Cancel it instead.")
    for n, s in enumerate(steps, 1):              # keep ids 1..n, in order
        s["id"] = n
    plan["steps"] = steps
    return _save(plan)


def next_step(plan: dict) -> dict | None:
    """The first step still to do (pending, or cut while running/blocked)."""
    for s in plan["steps"]:
        if s["status"] in ("pending", "running", "blocked"):
            return s
    return None


def total_cost(plan: dict) -> float:
    return round(sum(s.get("cost") or 0.0 for s in plan["steps"]), 8)


def mark_interrupted() -> int:
    """At start: a plan left running by a stopped server is paused, marked
    interrupted, with its running step back to pending. Returns how many."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT * FROM plans WHERE status = 'running'").fetchall()
    finally:
        conn.close()
    for r in rows:
        plan = _row(r)
        for s in plan["steps"]:
            if s["status"] == "running":
                s["status"] = "pending"
        plan["status"] = "paused"
        plan["interrupted"] = True
        _save(plan)
    return len(rows)


def summary_for_history(plan: dict) -> str:
    """The compact record kept in chat history: goal, each step's tier, model and
    result note (never a step's transcript), and the final check."""
    lines = [f"[Plan] {plan['goal']} ({plan['status']})"]
    for s in plan["steps"]:
        model = f", {s['model']}" if s.get("model") else ""
        lines.append(f"{s['id']}. {s['title']} [{s['tier']}{model}, {s['status']}]")
        if s.get("result"):
            lines.append(f"   {s['result']}")
    if plan.get("check"):
        lines.append(f"Check: {plan['check']}")
    return "\n".join(lines)

