# project_records.py Agent Details

## File Overview
What Optimux remembers about projects deleted from the Dashboard (fix25a). Called by `workspace.py` (`delete_archived`, `restore_archived`, `create_project`) and `main.py` (overview, archived list, `/api/projects/deleted`).

## Functions
- `mark_deleted(name)` / `unmark_deleted(name)` / `deleted_projects(root=None)`: the `deleted_projects` table (migration 7: name, deleted timestamp). A name is listed only while its records are kept and no folder uses it: with `root`, a name whose folder exists again (cloned or made by hand) is unmarked.
- `record_counts(name)`: chats, model calls and their cost, requests with their 👍 (`likes`) and 👎 (`dislikes`), model `errors` and `escalations` (model_events), `plans`, memory versions, whether a memory file exists. The delete dialog lists every one, so the user sees exactly what goes.
- `forget(name)`: deletes the project's session events and sessions, usage rows, requests (`tasks`, with their ratings), `model_events`, `plans` (v0.8: so no statistic is left half-counted), memory versions, its row in `deleted_projects`, its memory file (`memory_manager.PROJECTS_DIR/<name>.md`, only inside that folder) and its budget. Other projects' rows are untouched.

## Tests
`tests/test_workspace.py` (fix25a section).
- `locations(name, root)` (v0.8, from OmniLatch): every place on disk with something of the project, for Projects → "Where its files are": the folder and the memory file (always), then, if they exist, the database (chats, model calls, requests, memory versions), budgets.json, project_settings.json (when the project has own settings), archived copies in `.archive/` and ready snapshots that include it. `shared` marks files that also hold other projects' data. Labels and descriptions go through `tr`.
