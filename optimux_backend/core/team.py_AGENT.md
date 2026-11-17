# team.py Agent Details

## File Overview
`team.py` (team version, step 1) holds the members of a team, their invitations, their consent and the log of admin access. No FastAPI imports; `main.py` wires it into HTTP. Solo installs never use its tables. The plan is in `docs/TEAM_AUDIT.md`.

## Rules
- **Mode:** `meta.mode` is `team` or absent (solo). `enable(name)` needs a password, creates the first admin (a row with no password: the admin signs in with `auth.json`) and is safe to repeat. `disable()` goes back to solo and deletes the members' sessions.
- **Two doors:** members use `check_login(name, password)` (role `user`, status `active`; the same cost whether or not the name exists); the admin uses the admin door of `/auth/login` (`admin: true`), which is the old password-plus-codes check. A member door never matches an admin row.
- **Invitations:** `invite(name)` / `reinvite(id)` return a random token once; `users.invite_hash` stores its SHA-256, `invite_expires` is 7 days. `activate(token, password, chat_access)` works once, sets the password (≥ 12 characters via `auth.check_new_password`), records the consent (`chat_access`, `consent_version`, `consent_at`, plus a `consent_history` line). A new invitation clears the password and signs the member out.
- **Disabling:** `set_disabled(id, True)` keeps the member's sessions on purpose: the guard answers every request but `/` with 403 `{detail, disabled: true}`, the open account page shows the notice and signs itself out after 6 seconds (`/login?disabled=1` repeats the message). Signing in with the **right** password to a disabled account says why (403); a wrong password or unknown name gets the generic 401 so strangers learn nothing. Enabling again ends the old sessions (a fresh sign-in).
- **Consent:** default is usage and cost only. `CONSENT_TEXT` (translated with `tr`) is what the member sees; change its meaning, then bump `CONSENT_VERSION`. `set_chat_access` records every change. `admin_may_read_chats(id)` is what later steps check before showing a chat or a prompt.
- **Access log:** `log_admin_access(admin_id, user_id, kind, ref)` with kind `chat`, `sent` or `usage`; the reference is an id, never content. `audit_for_user`, `audit_all`. Triggers in the database refuse UPDATE and DELETE on `admin_access`.
- **Sessions:** `logins.user_id` is NULL for the install's own browsers (solo, or signed in before team mode: they are the admin's). `session_user(login_id)` returns the member or admin row; `me(login_id)` is what `/api/me` sends (members also get `consent_text`).

- Projects (step 3a): table `project_members(project, user_id, added)` by folder name. `set_member`, `project_members`, `projects_of(user, existing)`, `can_use_project(user, project)` (admin: all; a member: only assigned ones; used by later steps to guard M routes), `default_project(user, existing)` (first assigned project, or "").

## Links & Dependencies
- `auth.py` (passwords, sessions), `store.py` (tables, `meta`), `i18n.py`.
- `main.py`: the guard lets a member reach only `/` and `/api/me*` (403 elsewhere) and `/ws` closes with 4403 until step 2.
- Tests: `tests/test_team_users.py`.
