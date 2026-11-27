# tui/ — optimux-tui (Rust)

**Purpose:** a terminal client of the Optimux server. It never runs models or
commands itself: everything goes through the server's `/ws` socket and HTTP API,
as described in `docs/PROTOCOL.md` (kept true by `tests/test_protocol.py`).

**State (T4 + Dashboard steps 1–8):** sign-in (`login`, `logout`, `status`), the chat
window (no command, or `chat`), the chat list with several chats at once, and the
Dashboard (Ctrl+G) with every section of the web page but Team (not planned), and
the Studio without its preview; each also has its commands.

## Files
- `build.rs`: puts `../VERSION` into `OPTIMUX_VERSION` (the version users see).
- `src/main.rs`: the command line (clap): `chat` (the default), `login`, `logout`,
  `status`; `sign_in()` asks for the password and code and keeps the session, used by
  `login` and by `chat` when there is no live session (only in a terminal; a script
  gets exit 3). Version warning (client and server of another major.minor).
- `src/server.rs`: parses and normalises a server address (the key of a saved
  sign-in); refuses plain http to another computer unless `--allow-http`.
- `src/api.rs`: HTTP — `GET /auth/status`, `POST /auth/login` (sends `Origin`;
  reads the `optimux_session` cookie), `GET /api/auth` (is the session alive),
  `POST /auth/logout`, `GET /api/sessions`, `GET /api/projects/overview`, and the
  project switch (`GET` then `POST /api/settings` with every setting). A new
  connection per call; no proxy for this computer. `Api::call(method, path, body)`
  for the Dashboard: JSON in and out, the server's `detail` in words on failure
  (`detail_text`). Every /api path the client uses is checked against the server's
  routes by `tests/test_tui_api.py` (the calls with their method, and every "/api/..."
  text written anywhere in the client).
- `src/socket.rs`: opens `/ws` with the cookie (or sends the token), waits for
  `auth_ok`; close codes in words; `Refused(code)` error for "not signed in".
- `src/store.rs`: `sessions.json` in `~/.config/optimux-tui/` (0600, folder 0700,
  atomic write); never in `~/.optimux`.
- `src/chat/mod.rs`: the window's loop (terminal, keys, line, HTTP results) and
  giving the terminal back on exit or panic.
- `src/chat/state.rs`: the chat as data. `State::apply` plays one server message
  (live, or from `session_loaded.events`); `kind_of` and `answer` for approvals.
  Pure: unit-tested with protocol messages.
- `src/chat/app.rs`: keys, cards (reason, pickers, quit), the 0.7 s answer guard,
  history/project pickers (HTTP in the background), the project switch. The chat list
  (`chats`, refreshed every 3 s from `GET /api/sessions` with `state`/`away`; `marks()`
  gives the ✓ and the bell). `leave()`: while a request runs, opening another chat sends
  `detach`, then `REOPEN` to the line (close it, open a fresh one), and opens the other
  chat there (`after`); the request stays on the server for `detach_wait`. Ctrl+D:
  `detach`, then quit on `detached` (or after 3 s).
- `src/chat/view.rs`: drawing (ratatui). While drawing it records the clickable
  parts in `App::hits` (`Click`): hint lines through `link_labels` / `key_of`
  ("Ctrl+O chats" -> Ctrl+O), card buttons, list rows. `App::on_mouse` turns a click
  into that key, so clicks follow the same rules as keys (the answer guard too).
- `src/chat/text.rs`: Markdown to lines, word wrap aware of wide characters.
- `src/chat/editor.rs`: the message box.
- `src/cli/`: commands for scripts (`models`, `keys`, `routing`, `budgets`, `usage`,
  `performance`, `projects`, `project`, `scope`, `execution`, `memory`, `skills`,
  `history`, `overview`, `system`; later the other sections). main.rs sets SIGPIPE to
  its default (a closed pipe ends the program, no panic):
  `mod.rs` `Client` (token or saved session, no password asked; `NotSignedIn` →
  exit 3, `Failed` → exit 1), `table`, `confirm` (`--yes` without a terminal),
  `read_key` (hidden or `--stdin`, never an argument). `models.rs`, `keys.rs`: the
  same calls as `dash/models.rs`. The model is saved before its key.
- `src/ui/external.rs`: $VISUAL / $EDITOR (else nano, vim, vi) on a 0600 file in a
  0700 temporary folder, removed afterwards.
- `src/ui/`: building blocks of the Dashboard screens, pure and unit-tested:
  `form.rs` (fields Text, Secret (shown as dots only), Number, Choice, Toggle;
  `check()` before sending), `table.rs` (rows with ids, `/` filter, the chosen row kept
  across refreshes), `confirm.rs` (y/n, or a sentence to type).
- `src/dash/mod.rs`: the Dashboard (Ctrl+G, `App::dash`): `SECTIONS` (the web page's
  order; `step` = the plan step that brings it, 0 = here), the menu, key routing.
  `Ctx::call` / `Ctx::calls` (the main call first, then a key; the main call's
  answer) run `Api::call` in the background; answers come back as
  `Bg::Dash(Answer{tag, result})` and go to the section by `tag`.
- `src/dash/mod.rs` `Section`: the trait of every section (load, key, on_answer,
  keys, draw, has_window, click_row); `make()` builds one by name. `Ctx::save_settings`
  reads every setting and sends them back with the changes (POST /api/settings saves
  all of them). `fmt.rs`: the page's number formats.
- `src/dash/routing.rs`, `budgets.rs`, `usage.rs`, `performance.rs`: those sections
  (Usage and Performance: the Details tables; charts stay in the web page).
- `src/dash/projects.rs` (Projects: list, new, clone with polling via
  `Ctx::call_later`, archive, archived/deleted views, restore, typed delete; `KINDS`
  is checked against the server's templates by tests/test_templates.py),
  `project.rs` (one project's settings; `parsed`/`shown` convert values), `scope.rs`.
  A section can open another (`take_goto` / `open_with`: Projects → its Project page).
- `src/dash/execution.rs`, `memory.rs`, `skills.rs`. Long texts go to the user's
  editor: a section returns an `Edit` from `take_edit()`; the chat loop
  (`chat/mod.rs`) drops the key reader, leaves the full screen, runs
  `ui/external.rs::edit` in `spawn_blocking` (the line keeps pinging), then gives the
  result to `edited()`. A refused save keeps the typed values (no reload).
- `src/dash/overview.rs`, `history.rs`, `system.rs`. A section opens a chat in the
  chat window with `take_goto` → ("Chat", id): `Dash::open_chat`, then
  `App::after_dash` closes the Dashboard and calls `go_to` (a running request is
  left running, as from the chat list).
- `src/dash/git.rs`: the git window of Projects (`P U S I M`): GET
  `/api/projects/{p}/{push|update|status|identity|git-commands}` (each route written
  whole in `Kind::url`, so tests/test_tui_api.py sees it), POST push/update with what
  the GET returned (tree, head, sha); `problems()` drops the file problems for a
  commits-only push, as the page's syncProblems.
- `src/dash/github.rs` (token, check, remove, who commits), `backups.rs` (snapshots,
  daily backups, restore from a file, the restart banner). Files move with
  `Ctx::download` / `Ctx::upload` → `Api::download_to` (a new 0600 file, removed on
  failure) / `Api::upload_file` (whole file, up to `UPLOAD_MAX` = 2 GB: reqwest's
  stream feature needs tokio-util, not in the lock). A one-time key file is written
  with `write_private_new` (0600, never replacing a file).
- `src/cli/github.rs`, `backups.rs`; git commands in `cli/projects.rs`. Typed
  sentences come from `--confirm` or `system::ask_line`. `main.rs` test
  `every_command_is_well_formed` runs clap's `debug_assert` on every command.
- `src/dash/guide.rs`: GET /api/guide, cut at ## and ### outside code blocks as the
  page's parseGuide; `find` (every word, titles first, 25 at most) and `pick` (the
  section a reader means) are shared with `cli/help.rs`. The text is drawn per section
  at the width, so each heading's line is known (n/p, Find → read there).
- `src/dash/start.rs`: GET/POST /api/onboarding; `TARGETS` maps a step's target to a
  section (and a command); ("Chat", "") from `take_goto` closes the Dashboard without
  opening another chat (`App::after_dash`).
- `src/chat/text.rs` `draw_table`: Markdown tables in columns, or a block per row when
  too wide.
- `src/dash/studio.rs`: the Studio on /api/files/* (the active project: tree,
  status, search, read, save with the digest it opened, folder, rename, delete info and
  delete) and /api/projects/{p}/git-state, git-mark, stage, diff, commit,
  commit-message; Push and Update reuse `git::GitWindow`. It polls git-mark every 4 s
  while drawn in the last 6 s (the first reading at once). A refused save keeps the
  edited text (`OpenFile::unsaved`). ("ChatText", text) from `take_goto` puts text in
  the chat's prompt box (`App::after_dash`). `Edit::suffix` is the file's own ending.
- `src/cli/studio.rs`: the same as commands; `studio edit` keeps the text in a 0600
  temporary file when the save is refused.
- Clicks in the Dashboard: `Section::click_row` returns true when it did the row's
  action (Studio, Guide); otherwise `Dash::click_row` sends Enter on a second click on
  the same row (`last_click`, cleared by any key). `Section::tabs` lists each row of
  tabs as drawn; `find_tabs` looks for them on the screen after drawing (all of a row,
  in order, on one line) and `click_tab` gets the click. The cyan key line is cut only
  between keys (`key_lines`) and made clickable with `link_labels` (`Dash::spots`).
- `src/panes/`: panes and tabs above the chat window (`chat::run` hands over to
  `panes::run`). `Workspace` holds tabs (`Node`: a split tree, `Leaf(id)`), and panes:
  `Chat(App)` (each with its own `link::start` line; its events reach the loop as
  `Msg::Link/Bg(id, ...)`), `Shell` (portable-pty + vt100: a reader thread feeds the
  parser and wakes the loop), `Choose` (a new pane's question), `Starting`. Ctrl+b
  prefix as Herdr. `view::draw_in` draws a chat in its pane; hits stay absolute.
  `Node::borders` gives each split's grab zone (the two columns or lines where its
  panes meet) and path; a press there starts a `Drag`, which sets that split's ratio
  from the pointer until the button goes up. `panes/help.rs`: the keys window
  (Ctrl+b ? or H): `ENTRIES` (group, keys, words, `Run::Prefix/Pane/Nothing`),
  `matching` (every word), drawn at the bottom right of the panes; it takes keys
  and clicks first while open. `Workspace::behind` holds what a pane keeps behind
  it (Ctrl+b o, `flip`): messages, ticks, quitting and the layout (`"behind"`) cover
  it; `chat_at` makes a chat for a given pane id so its line's messages find it;
  `remove` brings what is behind forward.
  `layout.json` (0600) is saved every 5 s when it changed and before Ctrl+b q; restore
  rebuilds tabs and opens each chat with `App::begin(After::Open)`. The shell's
  environment gets TERM and loses OPTIMUX_TOKEN; nothing else is added.
- `src/dash/models.rs`: Dashboard → Models, the same calls and rules as `web/app.js`
  (catalog add sends the key first; an "Other" model on a non-local host needs the
  ack, `is_local_host` copies the page's rule). Views: list, form, delete, providers,
  catalog.
- `src/chat/link.rs`: the `/ws` task: pings every 5 s, dead after 20 s of silence,
  reconnects with backoff; no automatic reconnect after 4409 (`RECONNECT` marker);
  `REOPEN` closes the line and opens a fresh one at once.

## Relations
- Server side: `optimux_backend/main.py` (`/auth/*`, `/api/auth`, `/ws`),
  `core/webutil.py` (`signed_in` sets the cookie), `core/security.py`
  (`origin_allowed`: no Origin is allowed on `/ws`).
- `scripts/optimux`: `optimux tui` runs this program with the local address.
- Team mode: signs in as the admin (`admin: true`); members are not supported.

## Libraries
ratatui 0.29 + crossterm 0.28 (the newest that build with Rust 1.85; ratatui pins
unicode-width =0.2.0). idna_adapter =1.1.0 for the same reason.

## Rules
- A new message type: add it to `docs/PROTOCOL.md` first.
- Never approve anything by default (PROTOCOL.md, part 4).
- Secrets: the session only in `sessions.json`; never print it, never pass a
  password or token as a command-line argument.
