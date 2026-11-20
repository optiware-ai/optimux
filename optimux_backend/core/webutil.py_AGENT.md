# webutil.py Agent Details

## File Overview
`core/webutil.py` (p105; before that in `main.py`) holds small helpers about the caller of a request, so the core and the add-ons share them without the add-on importing `main`.

## Functions
- `client_ip(conn)`: the caller's address (`unknown` when there is none). Behind a reverse proxy on the same machine uvicorn already puts the forwarded address here.
- `limited(request, password=False)`: a 429 `JSONResponse` with `Retry-After` when `auth` says this address has failed too often (sign-in counter, or the password counter), else None. `main.py` imports it as `_limited`.
- `is_https(conn)`: true for https/wss.
- `signed_in(request, response, user_id=None)`: starts a session (`auth.create_session`) and sets the cookie (HttpOnly, SameSite=Strict, Secure on https or on a server). `main.py` imports it as `_signed_in`; the add-on uses it for members.
