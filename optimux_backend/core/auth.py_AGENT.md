# auth.py Agent Details

## File Overview
`auth.py` (Feature 23) signs browsers in to Optimux: one password, optional two-step codes (TOTP), server-side sessions and a per-address limit on failed attempts. No FastAPI imports; `main.py` wires it into HTTP and the WebSocket.

## Stored state
- `~/.optimux/auth.json` (mode 600, via `paths.write_private`): `password` (`scrypt$N$r$p$salt$hash`, N=2^14, r=8, p=1), `password_changed`, `totp` (`secret`, `enabled`, `last_step`), `totp_pending` while setting up. Deleting the file resets the password (the access token then sets a new one) and turns two-step codes off.
- Table `logins` (store migration 6): `id` = SHA-256 of the cookie value (never the cookie), `created`, `last_seen`, `ip`, `agent`.

## Core Functionality
- **Passwords:** `set_password` (≥ `MIN_PASSWORD` = 12), `check_password` (constant cost when no password exists), `password_set`.
- **Sessions:** `create_session(ip, agent)` returns the cookie value; `check_session(token)` returns the session id or None, ending sessions idle for 14 days (`IDLE_SECONDS`) or older than 30 (`MAX_AGE_SECONDS`), and refreshes `last_seen` at most once a minute; `end_session`, `end_all_sessions(keep_id)`, `list_sessions(current_id)`.
- **TOTP (RFC 6238, SHA-1, 6 digits, 30 s):** `start_totp` (pending secret + `otpauth://` URI), `confirm_totp(code)`, `check_totp(code)` (accepts the current step ±1, each step only once via `last_step`), `disable_totp`.
- **`RateLimiter` / `limiter`:** 5 failures per address in 15 minutes lock that address until the oldest failure is 15 minutes old. In memory (a restart clears it).
- **`server_mode()`:** `OPTIMUX_SERVER=1`. Used by `sandbox.required()`, `config_manager.get_settings/save_settings` (auto-approve off, network off by default) and `main.py` (Secure cookies, checklist).

## How main.py uses it
- `guard` middleware: everything except `/login`, `/auth/*` and `/static/*` needs the cookie (`optimux_session`, HttpOnly, SameSite=Strict, Secure on HTTPS or in server mode) or the access token as a Bearer header. Cookie-authenticated POST/DELETE must come from the same origin (Origin, else Referer). Bearer failures count toward the limit.
- `/auth/setup` (access token + first password), `/auth/login` (password, then `need_code` if TOTP is on), `/auth/logout`; `/api/auth` (overview), `/api/auth/signout-others`, `/api/auth/password` (ends other sessions), `/api/auth/totp/start|confirm|disable`.
- WebSocket `/ws`: cookie at the handshake, else the token as the first message (scripts). Open sockets are tracked per session (`LIVE_SOCKETS`) and closed with 4401 when that session ends.

## Links & Dependencies
- `paths.py` (folder, `write_private`), `store.py` (`logins`), `security.check_token` (setup, Bearer).
- Tests: `tests/test_auth.py`.
