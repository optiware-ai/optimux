# scout.py Agent Details

## File Overview
`scout.py` (v0.8) runs the **scout**: before a Complex task, the Simple model reads the project with read-only tools and writes a brief, which `main.scout_first` puts after the request for the Complex model. In an agent loop each call re-reads the whole conversation, so looking around on the Complex model pays its (5-20x) price on every step; the scout does it on the cheap model, within a cap. Its job is factual (files, quotes, what exists, what's missing); judging what's wrong stays with the Complex model.

## Tools (read-only, in Python, never a shell)
- `list_files(root, path)`: `planner.file_list` of a folder, up to `LIST_MAX` (200).
- `read_file(root, path, start_line)`: numbered lines, at most `READ_LINES` (200) and `READ_CHARS` (12,000), then "(continues: read again from line N)". Paths go through `files.resolve_target` (inside the project, links followed and checked, never `.git`, never Optimux's data); binary or over `files.MAX_BYTES` is refused.
- `search_files(root, text, path)`: case-insensitive substring over text files up to `SEARCH_BYTES`, at most `SEARCH_FILES` files and `SEARCH_MAX` matches as `path:line: text`; skips `planner.SKIP_DIRS`, hidden folders and links.
- `run_tool(root, name, args)`: a problem comes back as "Error: …" for the scout to adjust; unknown tool names are refused.

## Functions
- `names_files(request, files)`: project files the request names by path or file name (`./` and a leading `/` ignored).
- `skip_reason(request, routing, workdir, files)`: "" or why not (no Simple model, Simple = Complex, no project, no files, request names files).
- `async run(request, model, memory, workdir, call=None)`: system prompt `agents.get_scout_prompt(MAX_TOOL_CALLS)`, one user message with the task, memory and up to `FILES_SHOWN` files. Loop with `tools=TOOLS`, `drop_params`, `CALL_TIMEOUT`, the model's key, messages masked (`mask_messages`). After `MAX_TOOL_CALLS` (8) tool calls or `MAX_PROMPT_TOKENS` (60k input in all) it asks for the brief with `tool_choice="none"`; tool calls past the budget get "Not run". Returns `{"brief" (≤ BRIEF_CHARS), "calls": [(usage, ms)], "tools", "read", "log", "files", "total"}`. A passing error (`providers.classify` kind `transient`, not a timeout: 503 overloaded, 429, a dropped connection) is tried again after each of `RETRY_DELAYS` (2 s, 5 s; `_with_retries`); other provider errors, and the third passing one, are raised.
- `report(result)` and `handoff(request, result, model)`: see below.

## Links & Dependencies
- `files.py` (path checks), `planner.py` (`file_list`, `SKIP_DIRS`), `agents.py` (`get_scout_prompt`), `config_manager.litellm_key_kwargs`, `security.mask_messages`.
- Used by `main.scout_first`. Tests: `tests/test_scout.py`.
- Ported from OmniLatch (0.1.0-p55 to p58) in v0.8.

## A report the Complex model can trust
In OmniLatch (where the scout was built), a brief said correctly that something didn't exist, and the Complex model searched again anyway (`find -name`, `ls`): 11 calls. So:
- `search_files` also matches file paths ("(the file's name or folder matches)"); nothing found reads "No file name or line contains …".
- `outcome(name, args, result)` writes one log line per lookup from the real result (`search "player" → nothing found`, `read a.py → 40 lines`, `list src → 4 files`); `run` returns `log`, `files` and `total` too.
- `report(result)` is Optimux's own part: the project's files (up to `HANDOFF_FILES`, 80) and the lookups run. `handoff(request, result, model)` sends the request, "Scout report…" (exact and current), the report, "Scout's brief:", and how to use it: don't repeat these lookups or list the project, read again only to change a file, look further only for what isn't covered, and if what the task works on doesn't exist, say so and ask the user how to go on instead of searching.
- A lookup with the same tool and arguments as one already run (`done`) isn't run or counted again; the scout gets "Already run above, with the same result: use that one."
