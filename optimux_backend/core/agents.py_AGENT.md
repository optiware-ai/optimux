# agents.py Agent Details

## File Overview
`agents.py` holds the system prompts for Optimux's two agent roles.

## Core Functionality
- **`get_router_prompt()`:** Instructions for the Conductor. It classifies a request as `simple` or `complex` by difficulty, not by whether tools are needed (both tiers have tools). It replies with JSON only.
- **`get_executor_prompt(context_str, workdir='')`:** The single system prompt shared by the Simple and Complex Executors. It describes the tools (`execute_bash`, `update_markdown_memory`) and basic conduct (answer small talk without tools, ask clarifying questions in plain text, treat earlier messages as context, answer questions about the conversation from the messages themselves (or say it's a new chat) instead of using tools to reconstruct it, and don't repeat file contents or command output that the user already sees, to save output tokens, and write only the reply without narrating internal reasoning). When `workdir` is given, it names the active project and tells the model to work only inside it. Static text comes first and memory last, so the prompt prefix stays stable across turns for provider prompt caching.

## Links & Dependencies
- `get_router_prompt` is used by `llm_client.route_model_with_usage`.
- **Plan mode (v0.4):** `get_router_prompt(catalog, plan=True)` adds `PLAN_RULES` (plan only a request to do work now, in separate phases, with routine parts; never a simple task) and asks for `"plan": true or false` in the JSON (with or without skills); with `plan=False` the prompt is byte-for-byte the old one. `get_planner_prompt()` is the planner's system prompt (Complex, no tools): 2–8 standalone steps, `tier` simple for routine work and complex only for hard thinking (at least one simple), `do` ≤ 600 characters, `done_when`, `files`, a final `check`; reply with JSON only.
- `get_executor_prompt` is used by `main.build_messages` with the output of `memory_manager.get_memory_context()`.

- **Workspace mode:** with `workspace=True` (no active project), the prompt says the model is at the projects root and must manage projects with `list_projects`, `create_project`, `switch_project` and `archive_project`, never with shell commands, and that deleting is only possible from the dashboard. Inside a project, it mentions `switch_project` for working on another project or creating one.

- **Skills:** `get_router_prompt(skills_catalog)` adds the catalog and asks for `{"complexity": ..., "skill": name|null}`; without skills it returns the original two-class prompt. `get_executor_prompt(..., skills_catalog, active_skill)` lists the skills (with how to use `load_skill`) before memory, and appends the active skill's folder, files and instructions after memory (telling the model not to call `load_skill` for it, and to follow it only when the user asked for the task to be done), so the static prefix stays cacheable.
- **Questions vs. tasks:** the router picks a skill only when the user asks to have its task done now; a question about it ("how do I run the tests?") gets `null`. The executor prompt says to answer questions and do work only when asked: read-only commands are fine to answer accurately, but no creating, editing or deleting files, installing packages, or running builds or tests unless requested; it offers to do the work at the end.

## fix24b
- In a project, the prompt names `create_project` for new projects ("never mkdir, git init, gh repo create or scripts").
- `sandboxed=True` adds: only the working folder can be changed, other paths are hidden or read-only (a missing file outside isn't proof it doesn't exist), don't work around it, tell the user what they'd need to do.
