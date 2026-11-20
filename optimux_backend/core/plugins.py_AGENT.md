# plugins.py Agent Details

## File Overview
`plugins.py` (v0.8) lets a separate package plug into the core. The core asks questions and sends events; an installed add-on answers. With none installed every question gets its default and Optimux is the solo product. No FastAPI imports (`mount(app)` only calls `app.include_router`). The plan is to move the team version into the add-on `optimux_team`, package by package (p100 is the loader; p101 added `identity.py`, the identity socket: the core reads whose work and which mode, and the add-on binds it).

## Rules
- **Loading:** `load()` imports each installed package in `PLUGIN_MODULES` (`optimux_team`) and calls its `register(api)` once. A package that isn't installed (`find_spec` is None) is skipped; one that is installed and won't import, has no `register`, or whose `register` raises stops the start with `PluginError`. A broken add-on is never skipped: an add-on can be what keeps people out of places.
- **The `api` an add-on gets:** `add_router(router)`, `answer(question, fn)`, `listen(event, fn)`. Nothing else of the core is reachable. Later packages add storage migrations, translations and a command-line hook.
- **Questions:** `ask(question, *args, default=None, **kw)` returns the answer of the one add-on that answers it, else `default`. A question has one answerer (a second raises `PluginError` and leaves no trace). An answer that raises is NOT caught: the request fails instead of falling back to the default.
- **Events:** `emit(event, **kw)` tells every listener. A failing listener is logged and the others still run: events tell, they never decide.
- **Mode check:** `check_mode(mode)` raises when the database says `team` and `optimux_team` isn't loaded. It is not wired into the start yet: it goes in when the team code has moved into the add-on (until then the team code is still in the core and the check would refuse a working install). `optimux disable-team` must keep working without the add-on, so the owner can always get out.
- **Wiring:** `main.py` calls `plugins.load()` and `plugins.mount(app)` as its last lines, after every core route. Routes of the core win over an add-on's on the same path.
- **Tests:** `tests/test_plugins.py` writes a tiny fake package to a temporary folder; `reset()` forgets every add-on between tests.
- **Questions answered so far (p102):** `member_rules(project)` -> `{level, network}` (autonomy level and network for a member's connection; asked by `autonomy.project_level` and `executor.run_shell_command`). The core's own default when nothing answers is `autonomy.STRICT_MEMBER`.
  `member_budget_block(user_id)` -> status dict or None (p103; asked by `budgets.blocking`; default None).
  `member_note_file(user_id, kind)` -> path of a member's note (p104; asked by `memory_manager`; default None: the core then uses the default text and never the admin's file).
- **Tests:** `tests/conftest.py` calls `plugins.load()` so the real add-on answers in every test (as `main.py` does at start); `tests/test_plugins.py` restores it after each test. `tests/test_core_independence.py` lists the core files that still import the team code; the list only shrinks and must end empty.
  `guard(request)` -> None or `{status, body}` (p106; asked by the `guard` middleware in `main.py` after a valid browser session, never for the Bearer token or public paths; default None).
  `member_login(payload, request, ip)` -> an awaitable returning the sign-in response (p107; asked by `/auth/login` for the members' door; default None: 503). `is_member(login_id)` -> bool (p107; asked by `/auth/status`; default False).
  `member_of(login_id)` -> None or `{id, status, projects}` (p108; asked by `/ws` on connect and before every message; default None, which is why team mode must refuse to start without the add-on).
  p109: `owner_filter(user)` -> "all" / "me" / member id (422 for an unknown member); `name_rows(rows)` -> rows with `user` and `private`; `hidden_users()` -> ids; `read_block(owner_id, kind, ref)` -> "" or why not (logs the opening); `log_usage_look(owner_id)`; `admin_signed_in()` -> the admin's user id (and notes the sign-in); `member_budget_status(user_id)` -> status or None. The first five are asked only when `identity.enabled()`.
  `member_page(login_id, which)` -> a `FileResponse` or None (p112; `which` is `chat` or `account`; asked by `GET /` and `GET /account`; default None).

- `Api.public_path(*paths)`, `is_public(path)`, `public_paths()` (p114): paths an add-on declares reachable without signing in; the core's guard lets them through next to its own `PUBLIC_PATHS`.
