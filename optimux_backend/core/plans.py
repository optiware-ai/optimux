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
import os
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
MAX_DETAILS = 6000           # a step's longer notes for later steps (a design, a list of cases)
MAX_DETAILS_IN_CONTEXT = 12000   # all earlier details given to one step, at most

# Plan statuses: waiting (shown, not started) · running · paused (stopped, stuck, interrupted,
# budget) · done · cancelled. Step statuses: pending · running · done · blocked · skipped · failed.
PLAN_STATUSES = ("waiting", "running", "paused", "done", "cancelled")
STEP_STATUSES = ("pending", "running", "done", "blocked", "skipped", "failed")
OPEN = ("waiting", "running", "paused")
CHECK_VERDICTS = ("passed", "failed", "not_verified")   # the final check's own verdict (finish_check)
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
    return {**step, "status": "pending", "result": "", "details": "", "model": None, "cost": 0.0, "calls": 0,
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
            "request": (request or "")[:2000], "note": "", "interrupted": False, "check_cost": 0.0, "check_calls": 0}
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
    body["check_cost"] = plan.get("check_cost", 0.0)
    body["check_calls"] = plan.get("check_calls", 0)
    if plan.get("check_verdict"):
        body["check_verdict"], body["check_note"] = plan["check_verdict"], plan.get("check_note", "")
    plan["updated"] = time.time()
    conn = store.connect()
    try:
        with conn:
            conn.execute("UPDATE plans SET plan = ?, status = ?, updated = ? WHERE id = ?",
                         (json.dumps(body), plan["status"], plan["updated"], plan["id"]))
    finally:
        conn.close()
    return plan


def set_check(pid: str, verdict: str, note: str = "") -> dict:
    """The final check's verdict: passed, failed, or not_verified (it couldn't run)."""
    plan = get(pid)
    plan["check_verdict"] = verdict if verdict in CHECK_VERDICTS else "not_verified"
    plan["check_note"] = (note or "")[:300]
    return _save(plan)


SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".pytest_cache", "dist", "build"}
MAX_WALK = 20000


def missing_files(step: dict, workdir: str) -> list[str]:
    """The step's files that exist nowhere in the project: not at the given path,
    and no file of that name in any folder (the planner may not know the final
    folder). An empty list when the step lists none."""
    want = [f for f in (step.get("files") or []) if f.strip()]
    if not want or not workdir or not os.path.isdir(workdir):
        return []
    root = os.path.realpath(workdir)
    names, seen = set(), 0
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        names.update(files)
        seen += len(files)
        if seen > MAX_WALK:
            break
    missing = []
    for f in want:
        path = os.path.realpath(os.path.join(root, f.lstrip("/")))
        if (path.startswith(root + os.sep) and os.path.exists(path)) or os.path.basename(f.rstrip("/")) in names:
            continue
        missing.append(f)
    return missing


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


STEP_FIELDS = ("status", "result", "details", "model", "cost", "calls", "started", "finished", "escalated", "tier")


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
    if "details" in fields:
        fields["details"] = (fields["details"] or "")[:MAX_DETAILS]
    plan = get(pid)
    step = next((s for s in plan["steps"] if s["id"] == step_id), None)
    if step is None:
        raise PlanError(f"The plan has no step {step_id}.")
    step.update(fields)
    return _save(plan)


CHECK_STEP = 0               # the step number of the final check, in usage rows and add_cost


def add_cost(pid: str, step_id: int, cost: float | None, model: str | None = None) -> dict:
    """Count one metered call in a step (cost may be unknown); CHECK_STEP is the final check."""
    plan = get(pid)
    if step_id == CHECK_STEP:
        plan["check_calls"] = plan.get("check_calls", 0) + 1
        if isinstance(cost, (int, float)):
            plan["check_cost"] = round(plan.get("check_cost", 0.0) + cost, 8)
        return _save(plan)
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
    """Steps and final check (the planner's own call is in Usage with the plan's id)."""
    return round(sum(s.get("cost") or 0.0 for s in plan["steps"]) + (plan.get("check_cost") or 0.0), 8)


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
        cut = next((s for s in plan["steps"] if s["status"] == "running"), None)
        for s in plan["steps"]:
            if s["status"] == "running":
                s["status"] = "pending"
        plan["status"] = "paused"
        plan["interrupted"] = True
        plan["note"] = (f"Interrupted by a restart at step {cut['id']}. Resume to do it again."
                        if cut else "Interrupted by a restart. Resume to go on.")
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
        verdict = {"passed": " (passed)", "failed": " (failed)", "not_verified": " (not verified)"}.get(
            plan.get("check_verdict") or "", "")
        lines.append(f"Check: {plan['check']}{verdict}")
        if plan.get("check_note"):
            lines.append(f"   {plan['check_note']}")
    return "\n".join(lines)



def earlier_work(plan: dict, step_id: int) -> tuple[list[str], list[str]]:
    """What the steps before `step_id` hand over: one result line per finished
    or skipped step, and their longer details (newest kept first when they
    don't all fit in MAX_DETAILS_IN_CONTEXT)."""
    results, details = [], []
    for s in plan["steps"]:
        if s["id"] >= step_id:
            break
        if s["status"] == "done":
            results.append(f"- Step {s['id']} ({s['title']}): {s.get('result') or 'done, no note.'}")
            if s.get("details"):
                details.append(f"Details from step {s['id']} ({s['title']}):\n{s['details']}")
        elif s["status"] == "skipped":
            results.append(f"- Step {s['id']} ({s['title']}): skipped by the user.")
    kept, room = [], MAX_DETAILS_IN_CONTEXT
    for d in reversed(details):
        if len(d) > room:
            if room > 200:
                kept.append(d[:room] + "\n[cut: too long to pass on in full]")
            break
        kept.append(d)
        room -= len(d)
    return results, list(reversed(kept))


def last_words(text: str, n: int = 150) -> str:
    """A step that ends with plain text: its last n words become its result note."""
    words = (text or "").split()
    return ("... " if len(words) > n else "") + " ".join(words[-n:])


# Typical model calls per step, low and high: a step reads, acts and reports (finish_step).
# Steps start from a small context, so a call usually costs less than a chat call of the
# same tier; the estimate is from recent calls of that tier, so it leans high.
STEP_CALLS = {"simple": (2, 4), "complex": (1, 3)}
CHECK_CALLS = (1, 3)


def estimate(plan: dict, averages: dict) -> dict:
    """A cost range for the steps still to run (and the check), from the average
    cost of a recent call per tier: {"low", "high", "missing": [tiers without an
    average]}. low/high are None when a needed tier has no priced call yet."""
    need = {"simple": [0, 0], "complex": [0, 0]}
    for s in plan["steps"]:
        if s["status"] in ("pending", "running", "blocked"):
            lo, hi = STEP_CALLS[s["tier"]]
            need[s["tier"]][0] += lo
            need[s["tier"]][1] += hi
    if plan.get("check"):
        need["simple"][0] += CHECK_CALLS[0]
        need["simple"][1] += CHECK_CALLS[1]
    missing = [t for t, (lo, hi) in need.items() if hi and not averages.get(t)]
    if missing:
        return {"low": None, "high": None, "missing": missing}
    low = sum(lo * averages[t] for t, (lo, hi) in need.items() if hi)
    high = sum(hi * averages[t] for t, (lo, hi) in need.items() if hi)
    return {"low": round(low, 6), "high": round(high, 6), "missing": []}
