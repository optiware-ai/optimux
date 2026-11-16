# Team version: audit of who may do what (step 0)

Read-only. Nothing in the app changed. `tests/team_route_classes.json` holds the table; `tests/test_team_route_classes.py` fails when a new endpoint, websocket message or model tool is not classified, so it cannot go stale.

## Classes

| Class | Meaning | Endpoints |
|---|---|---|
| P | public | 6 |
| T | own token | 1 |
| S | signed in (own data) | 33 |
| R | read for all, admin writes | 10 |
| M | project member | 34 |
| A | admin only | 72 |

Total: 156 (155 REST endpoints and the chat socket).

## The three doors
Everything a person can do goes through one of three doors, and team mode must guard all of them:

1. **REST** (`@app.middleware("http")` `guard`): one choke point today. It checks the sign-in cookie or the access token (Bearer) and sets `request.state.login_id`. It knows nobody's name or role.
2. **The chat socket** (`/ws`): its own origin check and sign-in; then 8 message types plus the plain prompt (listed below).
3. **Model tool calls**: what a model can do on its own in a chat (run a command, write files, switch or create projects). Checking only REST would leave this door open.

## What is global today and must become per user

| Where | What | Team version |
|---|---|---|
| `settings.active_project` (54 uses in main.py) | one active project for the whole server | per user (and per connection) |
| `_broadcast()` | sends to every open page | only to the pages of the people it concerns |
| `RUNNING`, `BUSY_PROJECTS` | runs by session, counts by project | add the owner; one agent run per project at a time |
| `LIVE_SOCKETS` | sockets by sign-in (`login_id`) | already per sign-in; map `login_id` to a user |
| `auth.json` | one password, one 2-step secret | a users table; keep the first password as the first admin |
| Bearer access token | acts as the owner | stays, as an admin credential for scripts |
| `OPTIMUX_SERVER` (`server_mode()`) | "others can reach it" | team mode implies it |
| `/api/language`, `/api/settings` | one language, one set of switches | language per user; switches admin-only |
| memory (personal, professional) | one file each | one per user; project memory shared by the members |
| `sent_calls`, `sessions`, usage, tasks | no owner (`user_id` exists only in `plans`) | add `user_id`; filter every read |
| GitHub token, API keys, snapshots, backups | one set for the install | admin only; a snapshot holds everyone's chats |

## Risks found while reading

1. **Hiding buttons is not security.** 71 endpoints are admin-only and 34 need project membership; each needs a check on the server.
2. **The sandbox may show other projects.** Commands run with the whole disk read-only (`--ro-bind / /`) and only home folders hidden. If the Workspace is not under a home folder (a VPS often uses `/srv/...`), a command on one project could *read* another project it is not on. To verify, then hide the rest of the Workspace explicitly. It can only write to its own project.
3. **Two people on one project** share one folder. First version: one run at a time per project.
4. **`/preview/...` tokens** must be issued only to members.
5. **The admin and chat contents**: a policy decision (see the proposal).
6. **Budgets**: today per project; add per user and charge both.

## Chat socket messages

| Message | Class | Note |
|---|---|---|
| `prompt` | M | a message with no 'type': a request in the chat's project; spends the user's budget |
| `new_chat` | S | team: in a project the user is on |
| `open_session` | S | own chats only; switches the active project (today a global setting) |
| `escalate` | M | asks the Complex model for the last request |
| `plan_action` | M | run, edit or cancel a plan of the user's own chat |
| `approval_response` | M | approves a command or change: who may approve on a project is a policy decision |
| `limit_response` | M | answers 'budget reached' |
| `stop` | S | stops the user's own run |
| `ping` | S |  |

## Model tools

| Tool | Class | Note |
|---|---|---|
| `execute_bash` | M | write access; the sandbox may change only the active project |
| `write_file` | M | write access |
| `edit_file` | M | write access |
| `read_file` | M | read access |
| `list_files` | M | read access |
| `search_files` | M | read access |
| `load_skill` | R |  |
| `update_markdown_memory` | S | personal/professional: the user's; project memory needs write access |
| `finish_step` | S | plan bookkeeping |
| `finish_check` | S | plan bookkeeping |
| `list_projects` | S | team: only the projects the user is on |
| `switch_project` | M | only to a project the user is on; per user, not global |
| `project_status` | M | read access |
| `push_project` | M | write access; uses the admin's GitHub token |
| `update_project` | M | write access |
| `create_project` | A | creates a folder and maybe a GitHub repository |
| `archive_project` | A | moves a project's folder aside |

## Endpoints by area

### Backups and restore

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/backups` | A |  |
| `GET /api/restore/status` | A |  |
| `GET /api/snapshots` | A |  |
| `GET /api/snapshots/exports/{token}` | A |  |
| `GET /api/snapshots/{sid}/download` | A |  |
| `GET /api/snapshots/{sid}/readme` | A |  |
| `GET /api/snapshots/{sid}/restore` | A |  |
| `POST /api/backups` | A |  |
| `POST /api/backups/keep` | A |  |
| `POST /api/backups/{name}/check` | A |  |
| `POST /api/backups/{name}/delete` | A |  |
| `POST /api/backups/{name}/export` | A |  |
| `POST /api/backups/{name}/restore` | A |  |
| `POST /api/restore/cancel` | A |  |
| `POST /api/restore/dismiss` | A |  |
| `POST /api/restore/import` | A |  |
| `POST /api/restore/restart` | A |  |
| `POST /api/restore/upload` | A |  |
| `POST /api/snapshots` | A |  |
| `POST /api/snapshots/limit` | A |  |
| `POST /api/snapshots/preview` | A |  |
| `POST /api/snapshots/{sid}/delete` | A |  |
| `POST /api/snapshots/{sid}/export` | A |  |
| `POST /api/snapshots/{sid}/restore` | A |  |

### Chat socket

| Endpoint | Class | Note |
|---|---|---|
| `WEBSOCKET /ws` | S | signed in; every message type below is checked on its own |

### Chats

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/sessions` | S | own chats (does the admin read others' chats? a policy decision) |
| `POST /api/plans/{pid}/edit` | S | own plan |
| `POST /api/sessions/{sid}/archive` | S | own chats (does the admin read others' chats? a policy decision) |
| `POST /api/sessions/{sid}/delete` | S | own chats (does the admin read others' chats? a policy decision) |
| `POST /api/sessions/{sid}/rename` | S | own chats (does the admin read others' chats? a policy decision) |

### GitHub

| Endpoint | Class | Note |
|---|---|---|
| `DELETE /api/github` | A | one shared token held by the admin; commit identity per user comes later |
| `DELETE /api/github/clone/{job_id}` | A | one shared token held by the admin; commit identity per user comes later |
| `GET /api/github` | A | one shared token held by the admin; commit identity per user comes later |
| `GET /api/github/clone/{job_id}` | A | one shared token held by the admin; commit identity per user comes later |
| `GET /api/github/repos` | A | one shared token held by the admin; commit identity per user comes later |
| `POST /api/github` | A | one shared token held by the admin; commit identity per user comes later |
| `POST /api/github/check` | A | one shared token held by the admin; commit identity per user comes later |
| `POST /api/github/clone` | A | one shared token held by the admin; commit identity per user comes later |
| `POST /api/github/identity` | A | one shared token held by the admin; commit identity per user comes later |

### Memory

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/memory/project/{name}` | M | write calls need write access; /curate spends a model call |
| `GET /api/memory/project/{name}/versions` | M | write calls need write access; /curate spends a model call |
| `GET /api/memory/{file_type}` | S | personal and professional memory: one per user |
| `POST /api/memory` | S | personal and professional memory: one per user |
| `POST /api/memory/project/{name}` | M | write calls need write access; /curate spends a model call |
| `POST /api/memory/project/{name}/curate` | M | write calls need write access; /curate spends a model call |
| `POST /api/memory/versions/{version_id}/undo` | M | the version belongs to a project: look it up, then check membership |

### Models and keys

| Endpoint | Class | Note |
|---|---|---|
| `DELETE /api/keys/{env_key}` | A | API keys: never shown to users |
| `DELETE /api/models/{model_id:path}` | A | /api/models/test spends real money |
| `GET /api/catalog` | A |  |
| `GET /api/catalog/{pid}` | A |  |
| `GET /api/config` | R | the models' names and roles, never their keys |
| `GET /api/cooldowns` | A |  |
| `GET /api/models/key-suggestion` | A |  |
| `GET /api/routing` | R | which models the router picks; read only |
| `GET /api/routing/projects` | R | team: only projects the user is on |
| `POST /api/catalog/online` | A |  |
| `POST /api/catalog/refresh` | A |  |
| `POST /api/cooldowns/clear` | A |  |
| `POST /api/keys` | A | API keys: never shown to users |
| `POST /api/models` | A | /api/models/test spends real money |
| `POST /api/models/test` | A | /api/models/test spends real money |
| `POST /api/models/thinking` | A | /api/models/test spends real money |
| `POST /api/models/update` | A | /api/models/test spends real money |
| `POST /api/routing` | A |  |

### Own account

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/auth` | S | own account: password, 2-step code, other sign-ins; team: admin can also end any user's sign-ins |
| `POST /api/auth/password` | S | own account: password, 2-step code, other sign-ins; team: admin can also end any user's sign-ins |
| `POST /api/auth/signout-others` | S | own account: password, 2-step code, other sign-ins; team: admin can also end any user's sign-ins |
| `POST /api/auth/totp/confirm` | S | own account: password, 2-step code, other sign-ins; team: admin can also end any user's sign-ins |
| `POST /api/auth/totp/disable` | S | own account: password, 2-step code, other sign-ins; team: admin can also end any user's sign-ins |
| `POST /api/auth/totp/start` | S | own account: password, 2-step code, other sign-ins; team: admin can also end any user's sign-ins |

### Pages

| Endpoint | Class | Note |
|---|---|---|
| `GET /` | S | the page itself; the guard sends strangers to /login |
| `GET /api/guide` | S |  |
| `GET /favicon.ico` | P |  |
| `GET /login` | P | sign-in page |

### Projects and Git

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/projects` | S | team: only the projects the user is on |
| `GET /api/projects/archived` | A |  |
| `GET /api/projects/archived/{folder}/github` | A |  |
| `GET /api/projects/deleted` | A |  |
| `GET /api/projects/overview` | S | team: only the projects the user is on |
| `GET /api/projects/{name}/diff` | M | git panel; push, update, commit-message spend a model call and need write access |
| `GET /api/projects/{name}/git-commands` | M | git panel; push, update, commit-message spend a model call and need write access |
| `GET /api/projects/{name}/git-mark` | M | git panel; push, update, commit-message spend a model call and need write access |
| `GET /api/projects/{name}/git-state` | M | git panel; push, update, commit-message spend a model call and need write access |
| `GET /api/projects/{name}/identity` | M | git panel; push, update, commit-message spend a model call and need write access |
| `GET /api/projects/{name}/locations` | A | disk paths |
| `GET /api/projects/{name}/push` | M | git panel; push, update, commit-message spend a model call and need write access |
| `GET /api/projects/{name}/settings` | M |  |
| `GET /api/projects/{name}/status` | M | git panel; push, update, commit-message spend a model call and need write access |
| `GET /api/projects/{name}/update` | M | git panel; push, update, commit-message spend a model call and need write access |
| `POST /api/projects` | A | creating a project; users cannot |
| `POST /api/projects/archived/{folder}/delete` | A |  |
| `POST /api/projects/archived/{folder}/restore` | A |  |
| `POST /api/projects/{name}/archive` | A |  |
| `POST /api/projects/{name}/commit` | M | git panel; push, update, commit-message spend a model call and need write access |
| `POST /api/projects/{name}/commit-message` | M | git panel; push, update, commit-message spend a model call and need write access |
| `POST /api/projects/{name}/push` | M | git panel; push, update, commit-message spend a model call and need write access |
| `POST /api/projects/{name}/settings` | A | a project's models, limits, autonomy |
| `POST /api/projects/{name}/stage` | M | git panel; push, update, commit-message spend a model call and need write access |
| `POST /api/projects/{name}/update` | M | git panel; push, update, commit-message spend a model call and need write access |

### Sign-in

| Endpoint | Class | Note |
|---|---|---|
| `GET /auth/status` | P | says whether a password is set |
| `POST /auth/login` | P | team: login by user name; /auth/setup stays for the first admin |
| `POST /auth/logout` | P | already public (it only ends the caller's own sign-in) |
| `POST /auth/setup` | P | team: login by user name; /auth/setup stays for the first admin |

### Skills

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/skills` | R | global skills plus those of the user's projects |
| `GET /api/skills/template` | S | draft spends a model call on the user's own chat |
| `GET /api/skills/{scope}/{name}` | R | global: read only; project: needs membership |
| `POST /api/skills/draft` | S | draft spends a model call on the user's own chat |
| `POST /api/skills/{scope}/{name}` | A | scope=global is admin; scope=project needs write access to that project (decide per scope) |
| `POST /api/skills/{scope}/{name}/mode` | A | scope=global is admin; scope=project needs write access to that project (decide per scope) |
| `POST /api/skills/{scope}/{name}/remove` | A | scope=global is admin; scope=project needs write access to that project (decide per scope) |

### Studio files

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/files/delete` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /api/files/expand` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /api/files/preview` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /api/files/read` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /api/files/search` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /api/files/status` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /api/files/styles` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /api/files/tree` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `GET /preview/{project}/{token}/{path:path}` | T | checks its own token; the token must only be issued to a member of the project |
| `POST /api/files/definition` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `POST /api/files/delete` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `POST /api/files/folder` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `POST /api/files/locate` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `POST /api/files/rename` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `POST /api/files/save` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |
| `POST /api/files/scripts` | M | uses the ACTIVE project: must become the user's own active project; write calls need write access |

### System

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/autonomy` | R | the level of each project, so a user sees what runs without asking |
| `GET /api/autonomy/check` | R | what a command would do at a level; no command runs |
| `GET /api/onboarding` | A | first-run checklist is the admin's |
| `GET /api/sandbox` | R | whether commands are sandboxed here |
| `GET /api/security` | A |  |
| `GET /api/settings` | R | team: without the admin-only fields (retention, snapshots, keys) |
| `GET /api/storage` | A |  |
| `GET /api/system` | A |  |
| `POST /api/autonomy` | A |  |
| `POST /api/language` | S | today a global setting; team: one per user |
| `POST /api/onboarding` | A | first-run checklist is the admin's |
| `POST /api/settings` | A | budget-wide switches, sandbox, retention, autonomy defaults |
| `POST /api/system/rotate-token` | A |  |

### Usage and budgets

| Endpoint | Class | Note |
|---|---|---|
| `DELETE /api/tasks` | A | deletes cost records |
| `GET /api/analytics` | S | filtered by user; the admin sees everyone (?user=) |
| `GET /api/budgets` | S | the user's own cap and the budgets of their projects |
| `GET /api/costs/estimates` | R | typical prices of paid one-off actions |
| `GET /api/savings` | S | filtered by user; the admin sees everyone (?user=) |
| `GET /api/tasks` | S | filtered by user; the admin sees everyone (?user=) |
| `GET /api/tasks.csv` | S | filtered by user; the admin sees everyone (?user=) |
| `GET /api/usage` | S | filtered by user; the admin sees everyone (?user=) |
| `GET /api/usage/daily` | S | filtered by user; the admin sees everyone (?user=) |
| `POST /api/budgets` | A |  |
| `POST /api/tasks/{task_id}/rating` | S | own replies |

### What was sent

| Endpoint | Class | Note |
|---|---|---|
| `GET /api/sent` | S | own kept prompts; admin sees them only if the visibility policy says so |
| `GET /api/sent/prune-preview` | A | retention is global |
| `GET /api/sent/request` | S | own kept prompts; admin sees them only if the visibility policy says so |
| `GET /api/sent/requests` | S | own kept prompts; admin sees them only if the visibility policy says so |
| `GET /api/sent/{sid}` | S | own kept prompts; admin sees them only if the visibility policy says so |
| `POST /api/sent/prune` | A |  |

