# project_records.py Agent Details

## File Overview
What Optimux remembers about projects deleted from the Dashboard (fix25a). Called by `workspace.py` (`delete_archived`, `restore_archived`, `create_project`) and `main.py` (overview, archived list, `/api/projects/deleted`).

## Functions
- `mark_deleted(name)` / `unmark_deleted(name)` / `deleted_projects(root=None)`: the `deleted_projects` table (migration 7: name, deleted timestamp). A name is listed only while its records are kept and no folder uses it: with `root`, a name whose folder exists again (cloned or made by hand) is unmarked.
- `record_counts(name)`: chats, model calls and their cost, memory versions, whether a memory file exists (shown in the delete dialog).
- `forget(name)`: deletes the project's session events and sessions, usage rows, memory versions, its row in `deleted_projects`, its memory file (`memory_manager.PROJECTS_DIR/<name>.md`, only inside that folder) and its budget. Other projects' rows are untouched.

## Tests
`tests/test_workspace.py` (fix25a section).
