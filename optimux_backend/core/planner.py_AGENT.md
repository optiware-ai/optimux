# planner.py Agent Details

## File Overview
`planner.py` makes the plan for plan mode (v0.4, the task manager MVP): **one Complex call with no tools**, which returns the plan JSON checked by `plans.validate`. There is **no read-only scout** in the MVP: the planner gets the request, the project memory and the project's file list.

## Functions
- `file_list(root)`: up to `MAX_FILES_LISTED` (300) paths relative to the project, and how many were seen (the walk stops after `MAX_WALK`, 5,000). It walks the folder in Python, never following links, and skips `SKIP_DIRS` (`.git`, `node_modules`, virtualenvs, caches, build output, `.optimux-skills`) and hidden folders except `.github`. **Git is deliberately not run**: a cloned repository's own git config can make `git ls-files` execute a program (`core.fsmonitor`), and planning must never run anything from the project.
- `build_messages(request, memory, files, total)`: the planner's system prompt (`agents.get_planner_prompt`) and one user message: the task, the project memory, and the file list (with "and N more not listed").
- `async make_plan(request, model, memory, workdir, call=None)`: calls the model (`litellm.acompletion` by default; `call` is for tests) with the key and thinking settings of that model and `PLANNER_TIMEOUT` (120 s). An answer that `plans.validate` refuses is sent back once with the reason ("That plan can't be used: … Reply with the corrected plan, JSON only."). Returns `{"plan": checked plan or None, "calls": [(usage, duration_ms), …], "error": reason, "thinking": text}`; `thinking` joins the `reasoning_content` of both tries when the provider shares it (Claude does; Gemini keeps it hidden), so the chat can show what the planner thought. Provider errors are raised, so the caller handles cooldowns like any call.

## Links & Dependencies
- `plans.py` (`validate`), `agents.py` (`get_planner_prompt`), `config_manager.py` (`litellm_key_kwargs`, `thinking_kwargs`).
- Used by `main.start_plan`. Tests: `tests/test_planner.py`.
