# Optimux client protocol

This page describes how a client talks to the Optimux server. The web page
(`web/app.js`) is one client. The terminal client (`optimux-tui`) is another.

A test (`tests/test_protocol.py`) reads the server code and the web page and
fails when a message type is missing from this page, or when this page lists
a type the server no longer uses. So: **when you add, rename or remove a
message, update this page in the same commit.**

All messages are JSON objects with a `type` field, sent over one WebSocket.

## 1. Signing in

There are two ways. Both end with the server sending `auth_ok`.

### a) Session (recommended for the terminal client)

1. `POST /auth/login` with JSON `{"password": "...", "code": "123456"}`.
   - `code` is the two-step code; send `""` when two-step sign-in is off.
   - `401` with `{"need_code": true}`: two-step sign-in is on, ask for the code.
   - `401` otherwise: wrong password or code. `429`: too many tries, wait
     (`Retry-After` header).
2. The answer sets the cookie `optimux_session`. Keep it private (file mode `0600`).
3. Open `ws(s)://<host>/ws` with the header `Cookie: optimux_session=<value>`.
   The server sends `auth_ok` at once.
4. REST calls send the same cookie. **POST/DELETE calls must also send
   `Origin: <server URL>`** (for example `Origin: http://127.0.0.1:8000`),
   because the server checks the origin of every change made with a cookie.
5. `POST /auth/logout` ends the session.

A session gets everything the web page gets, including the Workspace
messages (`projects_changed`, `project_archived`, `file_saved`, `auth_event`).

### b) Access token (scripts)

1. Open `ws(s)://<host>/ws` with **no** `Origin` header.
2. Within 10 seconds send `{"type": "auth", "token": "<access token>"}`.
3. Good token: `auth_ok`. Wrong token: `error` with `"Unauthorized"`, then close
   code `4401`.

REST calls send `Authorization: Bearer <token>`.
A token connection does **not** get the Workspace messages.

### Limits

- 5 failed sign-ins (password, code or token) in 15 minutes from one address
  block that address for a while (close code `4429`, HTTP `429`).
- In team mode only the admin can sign in this way; members use the team add-on.

### Close codes

| Code | Meaning |
|------|---------|
| `1008` | The `Origin` header is not allowed. |
| `4401` | Not signed in, wrong token, or the session ended. |
| `4403` | A team member who is disabled or on no project. |
| `4429` | Too many failed sign-ins from this address. |
| `4409` | Another client picked up this client's running request (after this one dropped or stayed silent, see 5). Don't reconnect on your own: two clients would take the request from each other. Let the user decide. |

## 2. Client → server

| Type | Fields | What it does |
|------|--------|--------------|
| `auth` | `token` | Token sign-in (first message only, see 1b). |
| `ping` | `t` (any number) | Checks the line. Answered at once with `pong`. Once a client pings, its silence means it is gone. |
| *(no type)* | `prompt`, `model` | **A prompt.** Any message with a `prompt` and a type not listed here is a prompt. `model`: `"auto"` (the Conductor decides) or a model name. The text `/new` starts a new chat. |
| `new_chat` | – | Starts a new chat. Answer: `chat_reset`, `done`. |
| `open_session` | `id` | Opens a chat: an id, `"latest"` (newest chat of the active project) or `"last"` (newest chat of any project; switches to it). Answer: `session_loaded`. If a request is still running for that chat, the client gets it back (see 5). |
| `stop` | – | Stops the running request (also its shell command). Answer: `stopped`/`done`. |
| `escalate` | – | Gives the last request to the Complex model. |
| `approval_response` | `approved` and more, see 4 | The answer to an `approval_request`. |
| `limit_response` | `choice`: `more`, `until_end` or `stop` | The answer to a `command_limit`. |
| `detach` | – | "I'm leaving on purpose": when this line closes, the running request waits `detach_wait` seconds for a window instead of `away` (see 5). Answer: `detached`. |
| `plan_action` | `action`: `run`, `resume`, `skip`, `cancel`; `plan_id` | A button on a plan card. |
| `switch_project` | `project` | Team members only. The solo owner switches with `POST /api/settings` (see 7), then sends `open_session` `"latest"`. |

While a request runs, only `stop`, `approval_response`, `limit_response`,
`detach` and `ping` are taken. Anything else gets the `system` message *"Still working on
the previous request. Stop it first."*

## 3. Server → client

Every request ends with `done`. Until then the client is busy.

| Type | Fields | What it means |
|------|--------|---------------|
| `auth_ok` | `build`, `away`, `detach_wait`, `version` | Signed in. `away`: seconds a request waits for a client that dropped; `detach_wait`: for one that left on purpose (see 5). |
| `detached` | `running`, `wait` | The answer to `detach`. `running: false`: nothing was running, nothing is kept. |
| `pong` | `t` | The answer to `ping` (same `t`). |
| `error` | `content` | Something failed. |
| `sandbox_problem` | `content` | The sandbox is on but does not work: commands will be refused. |
| `no_projects` | `content` | Team member on no project; the server closes the line. |
| `member_state` | `projects`, `project` | Team member: their projects and the open one. |
| `session_started` | `session` (`id`, `title`, `project`) | The first prompt made a new chat. |
| `session_loaded` | `session`, `events`, `plans`, `ratings`, `running`, `read_only`, `reason`, `times`, `active_project_changed` | A chat was opened. `events` is the whole chat (see 6). `running: true`: the request is still going and its messages follow. |
| `chat_reset` | – | The chat is empty now (new chat, or another project). |
| `status` | `stage`, `model`, `net`, `host`, `ago` | What the server is doing: `stage` is `conductor`, `model` or `files`. `ago`: seconds since it was sent (only when a client comes back). |
| `token` | `content` | A piece of the answer text (Markdown). Add pieces together. |
| `thinking` | `content` | A piece of the model's thinking. |
| `system` | `content`, `tier`, `model`, `skill`, `brief`, `plan_step`, `step_title` | A note from Optimux (routing, refusals, skill started...). |
| `usage` | `ts`, `session`, `project`, `role`, `model`, `prompt_tokens`, `completion_tokens`, `cost`, `duration_ms`, ..., `session_tokens`, `session_cost`, `session_cost_complete`, `sent` | One model call was measured. `cost` can be `null` (unknown price). |
| `budget` | `state`, `budget`, `content` | The project's budget is near or over its limit. |
| `approval_request` | see 4 | Optimux waits for the user's yes or no. |
| `command_start` | `command`, `file`, `time_limit` | A shell command started. |
| `command_output` | `command`, `output`, `exit_code`, `cut`, `hidden`, `file` | A shell command ended. `cut`: the output was shortened. |
| `command_limit` | `command`, `elapsed`, `limit` | A command runs longer than its limit. Answer with `limit_response`. |
| `command_limit_done` | `command` | The limit question is closed. |
| `step_limit` | `content`, `steps` | The model used all its steps. |
| `changes` | `git`, `files`, `more`, `error`, `top`, `tree` | Files changed by this request. Each file: `path`, `status` (`new`/`changed`/`deleted`), `added`, `removed`, `lines_before`, `lines_after`, `warn`, `secret`. |
| `plan` | `plan` (`id`, `goal`, `status`, `steps`, `total_cost`, `estimate`, ...) | A plan card, new or updated. Plan `status`: `waiting`, `running`, `paused`, `done`, `cancelled`. |
| `prompt_masked` | `content` | The prompt had a saved key in it; this is the prompt with the key hidden. |
| `project_offer` | `project` | The model offers to open another project. |
| `project_created` | `project`, `ok`, `github_url` | A project was made. |
| `project_switched` | `project` | The active project changed. |
| `memory_updating` | `project` | The project memory is being updated after a chat. |
| `memory_updated` | `project`, `summary`, `cost`, `version_id` | It was updated. |
| `memory_unchanged` | `project`, `reason`, `cost` | Nothing was changed (or the update failed). |
| `task_done` | `task_id` | The request's task record is complete (for the rating buttons). |
| `stopped` | – | The request was stopped. |
| `done` | – | The request is over. The client can send a new prompt. |

Sent to every signed-in session (not to token connections, never part of a chat):

| Type | Fields | What it means |
|------|--------|---------------|
| `projects_changed` | – | The Workspace changed (also by hand, outside Optimux). Reload the project list. |
| `active_project_changed` | `project` | The active project changed, in any window (`POST /api/settings`, or the model's switch). If it isn't yours already and nothing runs, show that project's latest chat (`open_session` `"latest"`). |
| `project_archived` | `project` | A project was archived. |
| `file_saved` | `project`, `path` | A file was saved in the Studio. |
| `auth_event` | `event`, `time` | Someone signed in or out. |

## 4. Approvals

`approval_request` always has:

- `command`: the text to show (a command, or a description of the action).
- `tool_call_id` (can be `null`), `sandboxed`, `sandbox_problem`.

It can also have, depending on what is asked:

| Field | Kind of question | Extra answer fields |
|-------|------------------|---------------------|
| `workdir`, `outside_paths`, `time_limit`, `ask_reason`, `level`, `raise_to` | A shell command | `raise_level: true` ("stop asking in this project", only when `raise_to` is set) |
| `file_change` (with `diff`), `still_asking` | A file change | `all_edits: true` (approve all edits in this reply) |
| `action: "create_project"`, `form`, `error`, `github`, `active_project` | Make a project | `form` (the edited form) |
| `action: "archive_project"`, `always_ask` | Archive a project | – |
| `action` (push/sync), `sync` | GitHub sync | `result` |
| `action` (fallback), `fallback` (`model`, `alt`, `kind`, `summary`) | Use a costlier model | – |
| `action: "step_limit"`, `steps_box` | The model is out of steps | `choice`, `steps` |

The answer is `{"type": "approval_response", "approved": true}` plus the extra
fields, or `{"type": "approval_response", "approved": false, "reason": "..."}`.
The `reason` (optional, at most 500 characters) is passed to the model.

**Rule for every client:** never approve by default. A yes needs a clear,
separate action from the user (a button, or a key that is not Enter).

## 5. A client that drops and comes back

- When the line drops during a request, the request goes on for `away`
  seconds (`auth_ok.away`, now 120). If no client comes back in time, it stops.
- A client that opens that chat gets it back: `open_session` with its id, or with
  `"latest"` / `"last"` only when that is the chat they open (the active project's
  newest; the newest anywhere, whose project then becomes active). Never another chat's
  request. It gets first `session_loaded` with `running: true`, then the last
  `status`, then the open question (`approval_request` or `command_limit`) again,
  then the live messages, in order.
- A client that pings and then goes silent counts as gone, so another client
  can pick up its request.
- **Leaving on purpose:** a client sends `detach`, then closes its line. The request
  then waits `detach_wait` seconds (`auth_ok.detach_wait`; Dashboard → System,
  30 minutes by default) instead of `away`. The budget still applies, and a question
  (a command to approve) waits for a window: nothing runs without an answer.
  `optimux-tui` uses it to leave a request running and to open another chat while one
  runs. When a window picks the request up again, the next drop is an ordinary one.

## 6. Chat events

`session_loaded.events` is the chat as a list of messages, oldest first.
They are the server → client messages above, plus these, which are only
recorded (never sent live):

| Type | Fields | What it is |
|------|--------|------------|
| `prompt` | `content` | What the user wrote. |
| `escalate` | – | The user gave the request to the Complex model. |
| `plan_action` | `action` | The user pressed a plan button. |

A client shows an opened chat by playing these events in order, the same way
it shows live messages.

## 7. REST calls a client needs

| Call | What for |
|------|----------|
| `GET /auth/status` | Is a password set, is two-step sign-in on. |
| `GET /api/settings`, `POST /api/settings` | The active project; switch it. **`POST` saves every setting**: a field left out goes back to its default. Send the whole object from `GET` with only `active_project` changed, as the web page does. |
| `GET /api/sessions` | The list of chats. A chat with a running request also has `state` (`working`, or `waiting`: a question is open) and `away` (no window is on it). |
| `POST /api/sessions/{id}/rename`, `/archive` | Manage a chat. |
| `GET /api/projects/overview` | The projects. |
| `GET /api/usage` | Usage and cost totals. |
