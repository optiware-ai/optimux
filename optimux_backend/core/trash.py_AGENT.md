# trash.py Agent Details

## File Overview
`trash.py` (v0.8) moves a file, folder or link aside for the Studio's Delete. Ported from OmniLatch's `core/trash.py`, with Optimux's rule that files are moved aside, never deleted: there is no permanent delete.

## Functions
- `home_trash()`: `$XDG_DATA_HOME/Trash`, default `~/.local/share/Trash` (freedesktop.org Trash specification, home trash). On a server it's the service user's home (`/var/lib/optimux`).
- `where(path, workspace)`: the trash `path` can be moved to with a rename (same filesystem; `path` isn't followed): the home trash, else `<workspace>/.trash` (`WORKSPACE_TRASH`; not a link), else `None`.
- `move_to_trash(path, trash)`: creates `files/` and `info/` (0700), reserves a name by creating `info/<name>.trashinfo` exclusively (`[Trash Info]`, `Path=` URL-quoted absolute path, `DeletionDate=` local time), then renames `path` to `files/<name>`. Taken names get `stem.2.ext`, `stem.3.ext`… A failed rename removes the record and raises `OSError`.

## Notes
- The Workspace's `.trash/` has the same layout, so its items can be restored by hand; `workspace.project_names` skips dot folders, so it's never listed as a project.
- Callers: `project_files.delete_info` / `delete_path`.
- Tests: `tests/test_studio_files.py`.
