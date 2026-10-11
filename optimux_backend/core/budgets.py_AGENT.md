# budgets.py Agent Details

## File Overview
`budgets.py` stores monthly cost budgets per project and reports how much of each budget is used this month.

## Core Functionality
- **Storage:** `~/.optimux/config/budgets.json` holds `{project: {"monthly_usd": float, "on_limit": "block" | "warn"}}`. `get_budgets()` reads it. `save_budget(project, monthly_usd, on_limit)` sets a budget (the amount must be > 0) or removes it when `monthly_usd` is `None`. Bad values raise `ValidationError`.
- **`status(project)`:** Returns this calendar month's spending (`usage.month_totals(project)`, one SQL query) against the budget: `monthly_usd`, `on_limit`, `spent`, `ratio`, `calls_without_cost` and `state` (`ok` below 80%, `warning` from 80%, `over` from 100%). Returns `None` when the project has no budget.
- **`all_statuses()`:** Status for every project that has a budget.
- **`blocks(project)`:** The status when the budget is used up and set to `block`, otherwise `None`.
- **Limitation:** calls without a known price can't be counted, so a budget can be exceeded by them; the status reports how many there were.

## Links & Dependencies
- Uses `usage.py` (spending) and `security.ValidationError`.
- Used by `main.py`: `GET`/`POST /api/budgets`, the check before each request, and the check before each model call.
