# curator.py Agent Details

## File Overview
`curator.py` keeps each project's memory (`~/.optimux/memory/projects/<name>.md`) up to date from its chats, and records every change to project memory so it can be inspected and undone. Personal and professional memory are never touched.

## Core Functionality
- **History (store migration 4, `memory_versions`):** each change stores project, time, source (`curator`, `user` for dashboard edits, `model` for the `update_markdown_memory` tool, or `undo`), session, a one-line summary, and the text before and after.
  - `write_memory(project, content, source, summary, session_id, cost=None)` replaces the file and records the change (with the model call's cost for curator updates).
  - `record_version(...)` records a change made elsewhere; unchanged text records nothing.
  - `list_versions(project)` returns versions newest first, each with a unified `diff` and `cost` (null for edits, model tool writes and undos).
  - `undo(version_id)` restores the "before" text and records an `undo` version; raises `KeyError` for unknown ids.
- **What the curator reads:** `transcript(events, skip_turns)` builds a compact view of a stored chat: requests, commands with up to 300 characters of output, and answers, capped at `TRANSCRIPT_CHARS`. It skips the turns already curated (`curated_turns` / `mark_curated`, a column added to `sessions` in migration 4), so a chat is never paid for twice.
- **Prompt (`build_prompt`):** asks for the complete updated file. It keeps only durable facts (how to build, run and test; stack; conventions; decisions; known issues), uses fixed `SECTIONS`, stays under `MAX_TOKENS` (1,500, about `MAX_CHARS`), and never stores secrets. If nothing durable was learned, the reply is `NO_CHANGES`; otherwise the file ends with a `SUMMARY:` line.
- **Checks:** `parse_reply(text)` strips code fences and splits off the summary. `acceptable(new)` rejects output that isn't a markdown file or is far over the size cap, so a bad reply never overwrites memory.

## Links & Dependencies
- `main.py`'s `curate_session` runs the model call, metering, budget and cooldown checks, triggers and endpoints.
- `memory_manager.update_memory` records the model's project-memory additions here.
- Uses `store.py` and `memory_manager.PROJECTS_DIR`, with project names validated by `security.validate_project_name`.
