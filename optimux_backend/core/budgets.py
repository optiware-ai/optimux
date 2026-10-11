"""Monthly cost budgets per project.

Budgets live in .optimux/budgets.json as {project: {"monthly_usd": float,
"on_limit": "block" | "warn"}}. Spending comes from usage.py (this calendar
month, local time). Calls without a known price can't be counted, so a
budget may be exceeded by them; the status reports how many there were.
"""
import json
import os

from optimux_backend.core import usage
from optimux_backend.core.security import ValidationError

from optimux_backend.core import paths

BUDGETS_FILE = paths.BUDGETS_FILE
ON_LIMIT = ("block", "warn")
WARN_RATIO = 0.8


def get_budgets() -> dict:
    try:
        with open(BUDGETS_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_budget(project: str, monthly_usd: float | None, on_limit: str = "block") -> None:
    """Set a project's monthly budget, or remove it with monthly_usd=None."""
    if not project:
        raise ValidationError("Project name is required.")
    budgets = get_budgets()
    if monthly_usd is None:
        budgets.pop(project, None)
    else:
        if monthly_usd <= 0:
            raise ValidationError("Budget must be greater than 0 (leave it empty for no limit).")
        if on_limit not in ON_LIMIT:
            raise ValidationError(f"on_limit must be one of {', '.join(ON_LIMIT)}.")
        budgets[project] = {"monthly_usd": float(monthly_usd), "on_limit": on_limit}
    os.makedirs(os.path.dirname(BUDGETS_FILE), exist_ok=True)
    with open(BUDGETS_FILE, "w") as f:
        json.dump(budgets, f, indent=4)


def status(project: str) -> dict | None:
    """This month's spending against the project's budget, or None if it has none.

    state: "ok" (< 80%), "warning" (>= 80%), "over" (>= 100%).
    """
    budget = get_budgets().get(project or usage.NO_PROJECT)
    if not budget:
        return None
    month = usage.month_totals(project or usage.NO_PROJECT)
    limit = budget["monthly_usd"]
    spent = month["cost"]
    ratio = spent / limit if limit else 0.0
    return {
        "project": project or usage.NO_PROJECT,
        "monthly_usd": limit,
        "on_limit": budget.get("on_limit", "block"),
        "spent": spent,
        "ratio": ratio,
        "calls_without_cost": month["calls_without_cost"],
        "state": "over" if ratio >= 1 else ("warning" if ratio >= WARN_RATIO else "ok"),
    }


def all_statuses() -> dict:
    return {p: status(p) for p in get_budgets()}


def blocks(project: str) -> dict | None:
    """The status if this project's budget is used up and set to block, else None."""
    s = status(project)
    return s if s and s["state"] == "over" and s["on_limit"] == "block" else None
