# memory_manager.py Agent Details

## File Overview
`memory_manager.py` manages Optimux's markdown memory, which is injected into every system prompt.

## Core Functionality
- **Layout** (under `paths.MEMORY_DIR`): `~/.optimux/memory/personal.md`, `~/.optimux/memory/professional.md`, and `~/.optimux/memory/projects/<active_project>.md`.
- **`init_memory`:** Creates the directories and seeds the personal and professional files with defaults.
- **`get_memory_context`:** Returns personal + professional memory, plus the active project's memory when a project is selected. The project name is validated with `security.validate_project_name`. If it is invalid, project memory is skipped instead of reading outside the projects folder.
- **`update_memory(file_type, content)`:** Appends to `personal`, `professional` or `project` memory (the target of the LLM's `update_markdown_memory` tool). Project names are validated the same way. Returns a result string for the model.
- **`log_command_to_session`:** Legacy no-op kept for compatibility.

## Data Relations
- Plain markdown files, appended to by the LLM and fully rewritten by the Settings > Memory UI (through `main.py`).

## Links & Dependencies
- Uses `config_manager.get_settings` and `security.py`.
- Called by `main.py` (`init_memory`, `get_memory_context`, `update_memory`, memory endpoints).

- **History:** when `update_memory` appends to project memory, the change is recorded with `curator.record_version(..., source="model")`, so it appears in the dashboard's History and can be undone.
- **Size limit and atomic writes (v0.5.2, IM-04):** `MEMORY_MAX_CHARS` = 20,000 per file; `check_size(content, what)` raises `MemoryTooLarge` with a message about the per-request cost. `write_atomic(path, content)` writes a temp file in the same folder (fsync, mode of the old file) and `os.replace`s it; on any error the temp file is removed and the old file stays. Used by `init_memory`, `update_memory` (the model tool: over the limit returns an error string, nothing saved), `/api/memory` (400) and `curator.write_memory` (400 from `/api/memory/project/{name}`; `check=False` for undo and snapshot restore).

- **Team members (step 6, moved to the add-on in p104):** each member has `~/.optimux/memory/members/<id>/personal.md` and `professional.md`, managed by `optimux_team/notes.py` (`MEMBERS_DIR`, `note_file`, `member_notes`, `save_member_notes`). The core asks `plugins.ask("member_note_file", user_id, kind, default=None)` for the path. `get_memory_context()` returns the member's own two files plus the project's notes (never the admin's or another member's); with no add-on a member gets the default text. `update_memory("personal"|"professional")` appends to the member's own file, or returns an Error and writes nothing when there is no add-on (never the admin's file). Only `GET /api/me/memory/{kind}` and `POST /api/me/memory` read or write them; there is no admin route. Backups already include the folder.
