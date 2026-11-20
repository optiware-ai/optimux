# identity.py Agent Details

## File Overview
`identity.py` (v0.8) holds the two facts the core keeps about the team version, so the core never imports the team code. No FastAPI imports.

## Functions
- `mode()` returns `"team"` when the stored `mode` setting is `team`, else `"solo"`; `enabled()` is `mode() == "team"`.
- `bind_user(user_id)` records, in a context variable, whose work the current task and the ones it starts record (a member's id; `None` is the install's own: the solo user or the admin). `current_user_id()` reads it. The team code binds the person where it knows who is connected (a member's `/ws` handler); sessions, sent calls, usage, tasks, budgets, autonomy, memory, the executor and the sandbox only read it.

## Rules
- Core modules (`budgets`, `autonomy`, `executor`, `memory_manager`, `sent`, `sandbox`, `sessions`, `usage`, `tasks`) import `identity`, never `team`. `tests/test_identity.py` fails if one of them imports or calls the team code again.
- `optimux_team/team.py` (moved there in p110) hands out the same four functions (`team.bind_user is identity.bind_user`), so the team code and its tests are unchanged.
- This is the identity socket of the add-on plan (`plugins.py_AGENT.md`): the add-on binds the person here instead of the core asking the add-on.

- `disable_team()` (p110): mode back to solo and every member's sign-in ended; members' data stays. It lives here so `optimux disable-team` works with no add-on (it touches the `users` and `logins` tables, which are still created by `core/store.py`).
