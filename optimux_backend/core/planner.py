"""The planner (v0.4 plan mode): one Complex call, no tools, that turns a big task
into a plan (see plans.py for the format).

There is no read-only scout in the MVP: the planner gets the request, project
memory and the project's file list, made here by walking the folder in Python.
Git is deliberately not run: a cloned repository's own git config could make
"git ls-files" run a program (core.fsmonitor), and planning must never execute
anything from the project.

On an answer that is not a usable plan, the planner is told why once and asked
again; a second failure means the task runs the old way.
"""
import os
import time

from optimux_backend.core import plans

MAX_FILES_LISTED = 300
MAX_WALK = 5000                     # entries looked at, so a huge folder can't stall planning
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
                       ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode", "dist", "build", ".next",
                       ".cache", ".optimux-skills"})
PLANNER_TIMEOUT = 120


def file_list(root: str) -> tuple[list[str], int]:
    """Up to MAX_FILES_LISTED project paths (relative, sorted, folders first by
    walk order), and how many were seen in all. Links are never followed."""
    if not root or not os.path.isdir(root):
        return [], 0
    found: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (d.startswith(".") and d != ".github"))
        for f in sorted(filenames):
            seen += 1
            if len(found) < MAX_FILES_LISTED:
                found.append(os.path.relpath(os.path.join(dirpath, f), root))
        if seen >= MAX_WALK:
            break
    return found, seen


def build_messages(request: str, memory: str, files: list[str], total: int) -> list[dict]:
    from optimux_backend.core.agents import get_planner_prompt
    listing = "\n".join(files) if files else "(no files, or no project is active)"
    more = f"\n(and {total - len(files)} more not listed)" if total > len(files) else ""
    user = (f"Task:\n{request}\n\n"
            f"Project memory:\n{memory.strip() or '(empty)'}\n\n"
            f"Project files ({len(files)} shown):\n{listing}{more}")
    return [{"role": "system", "content": get_planner_prompt()}, {"role": "user", "content": user}]


async def make_plan(request: str, model: str, memory: str, workdir: str, call=None) -> dict:
    """Ask the planner for a plan. Returns {"plan": checked plan or None, "calls":
    [(response usage, duration_ms)], "error": reason or ""}. Provider errors are
    raised (the caller handles cooldowns and fallback like any call)."""
    import litellm
    from optimux_backend.core.config_manager import litellm_key_kwargs, thinking_kwargs
    call = call or litellm.acompletion
    files, total = file_list(workdir)
    messages = build_messages(request, memory, files, total)
    calls, error = [], ""
    for attempt in range(2):
        started = time.monotonic()
        kwargs = {"model": model, "messages": messages, "timeout": PLANNER_TIMEOUT,
                  **litellm_key_kwargs(model), **thinking_kwargs(model)}
        response = await call(**kwargs)
        calls.append((getattr(response, "usage", None), int((time.monotonic() - started) * 1000)))
        text = response.choices[0].message.content or ""
        try:
            return {"plan": plans.validate(text), "calls": calls, "error": ""}
        except plans.PlanError as e:
            error = str(e)
            if attempt == 0:
                messages = messages + [{"role": "assistant", "content": text},
                                       {"role": "user", "content": f"That plan can't be used: {error} "
                                                                   "Reply with the corrected plan, JSON only."}]
    return {"plan": None, "calls": calls, "error": error}
