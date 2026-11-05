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

## File operations (v0.8)
- `expand_all(root)`: every folder's `list_dir`, level by level, for "Expand all"; folders that are ignored by git, links or hidden (`.github`, `.venv`) aren't opened; at most `EXPAND_MAX` (400). `{folders: {path: listing}, expanded, truncated}`.
- `_target(root, rel)`: an existing entry to rename or delete: its folder must resolve inside the project (`resolve_dir`), the entry itself isn't followed (a link is a "link"); never the project itself, `.git`, or anything holding or inside Optimux's data folder (`paths.HOME`).
- `_new_target(root, rel)`: a free place inside the project (parent resolved and checked with `_forbidden`; nothing into `.git`); 409 when taken or the parent is a file.
- `make_folder`, `rename_path` (moves too; missing folders made; a folder never into itself; files that mention the old path aren't changed).
- `delete_info` (files, folders, size, `truncated` past `COUNT_LIMIT`, how many git tracks, `trash`: home|workspace|null) and `delete_path`: `trash.where` then `trash.move_to_trash`; with no trash on the same filesystem it's a 409 and nothing changes (never a permanent delete).

## Open in file (v0.8)
- `locate(root, page, el)`: `el` = `{tag, id, classes, attrs, text, index}` from the preview's picker. In the page's own HTML (`page` ending in .htm(l), read through `read_file`/`files.resolve_target`), every start tag of that name is scored (`_in_page`): id +100 (another id -30), +10 per shared class and -4 per class only the source has, +8 per other equal attribute (not id, class, style), +25 when the element's normalized text is inside it, up to +12 for the same position among same tags; the best with 15+ wins, and `_element_end` finds its end tag (depth counted; void and self-closing tags end at the tag). Otherwise the text (first 60 chars, also across line breaks), the id, then classes of 4+ letters are searched in `_project_paths` files with `LOCATE_EXTS` (not hidden, under `LOCATE_BYTES`, the page and HTML first, at most `LOCATE_FILES`). Returns `{found, path, from: {line, col}, to: {line, col}, how: page|text|name}` (lines from 1, columns from 0) or `{found: false}`; an invalid tag name is a 400.

## Open the CSS, Open the script (v0.8)
- `_css_rules(text, i, end, media)`: a small CSS reader (comments and strings skipped, braces counted): every style rule's `(selectors, start, end, media)`, inside `_NESTING` at-rules (`@media`, `@supports`, `@layer`, `@container`, `@document`, `@scope`; `media` is their preludes joined); other at-rules (`@font-face`, `@keyframes`, `@import`) are skipped; nested CSS inside a style rule isn't split.
- `_page_parts(root, page)`: the page's text and its `<link>`, `<style>` and `<script>` tags with their content range (HTML comments skipped). `_local_ref(page, ref)`: a link or src as a project path (relative to the page, `/x` from the project's top), `None` for other sites, `data:`, or anything that leaves the project. `_read_text`: through `files.resolve_target`, up to `LOCATE_BYTES`.
- `style_rules(root, page)`: `{rules: [{i, path, selectors, media, from, to}], sheets, truncated}` for local `rel=stylesheet` files and `<style>` blocks in the page's order (at most `STYLE_RULES`); a page that isn't .htm(l) is a 400. Which rules apply is decided in the preview (`element.matches`).
- `script_refs(root, page, el)`: lines naming the element's id (weight 3), classes of 3+ letters (2) or `data-` attributes (2), as whole names (`(?<![\w-])name(?![\w-])`), +1 when the line has `#id`/`.class` or `getElementById`; first the page's `<script>` blocks (classic or module) and local `src` files (`where: page`), else the project's `SCRIPT_EXTS` files (`where: project`). Sorted by weight, the page first; at most `SCRIPT_MATCHES`. `{matches: [{path, from, to, text, why}], where}`.

## Search (v0.8)
- `search(root, query)` (OmniLatch p81): files and folders whose lowercase path contains every word (at most 10); `_project_paths` lists `git ls-files --cached --others --exclude-standard` (or, without git, `os.walk` skipping dot folders), up to `SEARCH_MAX_FILES`; parts starting with a dot are skipped. Ranked: the name is the query, the name starts with the first word, the name has every word, the path does; folders first; shorter paths first. At most `SEARCH_RESULTS` (200) with `total` and `truncated`.

## Links
- `files.py` (`resolve_target`, `prepare_user_save`, `apply`, `_digest`, `MAX_BYTES`), `security.py` (`mask_secrets`, `resolve_project_dir`), `changes.py` (`SECRET_NAME`), `config_manager.get_settings`.
- `trash.py` (Delete).
- Tests: `tests/test_studio.py`, `tests/test_studio_files.py`.
