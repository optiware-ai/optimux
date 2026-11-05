# project_files.py Agent Details

## File Overview
The Studio (v0.8, ported from OmniLatch's Files panel): list, read and save the active project's files, and serve them for the preview. Everything stays inside one project folder of the Workspace: paths are relative, symlinks are followed and must stay inside, and `.git` and Optimux's data folder (`paths.HOME`) are never listed, read, written or served (the same boundary as `files.resolve_target`). Errors are `FilesError(message, status)`; main.py turns them into HTTP errors with that status.

## Functions
- `project_dir(name)` / `active_project()`: the real folder (409 with no project, 404 for a missing one).
- `resolve_dir(root, rel)`, `_forbidden`, `_clean_rel`: the path checks.
- `git_status(root)`: `{git, branch, files: {path: M|U|A|D|R|C}, truncated}` from `git status --porcelain -z` (read-only git: no outside GIT_* variables, no optional locks, `GIT_TIMEOUT`).
- `list_dir(root, rel)`: one folder, folders first; entries `{name, path, type: dir|file|outside, size, link, ignored}` (`.git` hidden; a link out of the project is `outside`; `MAX_ENTRIES` per folder).
- `read_file(root, rel)`: `{path, size, text, digest, hidden}`; saved keys shown as ••••(NAME) (`mask_secrets`, `hidden` counts them) while `digest` is of the file on disk; binary or over `files.MAX_BYTES` gives `binary` / `too_large` and no text.
- `save_file(root, rel, text, digest)`: through `files.prepare_user_save` (a mark where the file has the key is written back as the key; a new mark is refused) and `files.apply` (atomic). `digest` None creates a file; a stale digest, a file that appeared or vanished is 409.

## The preview
- Served by main.py at `/preview/<project>/<token>/<path>`; `preview_token(project)` is an HMAC with `_PREVIEW_SECRET` (new at every start), checked with `preview_ok`; `preview_base(project)` is the address the page gets from `/api/files/preview`.
- `preview_target(root, rel)`: (real file, content type); a folder serves its `index.html`; never `.git`, Optimux's data, links out, or files matching `changes.SECRET_NAME` (.env, *.pem, *.key, SSH keys): 403.
- `preview_text(real, ctype)`: text types up to `PREVIEW_MASK_BYTES` are served with saved keys hidden; anything else is streamed as it is.
- `find_index(root)`: the first page: `index.html`, else in dist/, build/, public/, out/, site/, docs/, www/.
- The sandbox itself (CSP `sandbox` without `allow-same-origin`, `frame-ancestors 'self'`, CORP cross-origin) is set by main.py (`PREVIEW_CSP`).

## Links
- `files.py` (`resolve_target`, `prepare_user_save`, `apply`, `_digest`, `MAX_BYTES`), `security.py` (`mask_secrets`, `resolve_project_dir`), `changes.py` (`SECRET_NAME`), `config_manager.get_settings`.
- Tests: `tests/test_studio.py`.
