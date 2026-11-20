"""Small helpers about the caller of a request, shared by the core and the add-ons."""
from fastapi import Request
from fastapi.responses import JSONResponse

from optimux_backend.core import auth
from optimux_backend.core.i18n import tr


def client_ip(conn) -> str:
    """The caller's address. Behind a reverse proxy on the same machine,
    uvicorn already replaces it with the X-Forwarded-For address."""
    return (conn.client.host if conn.client else "") or "unknown"


def limited(request: Request, password: bool = False) -> JSONResponse | None:
    ip = client_ip(request)
    wait = auth.password_retry_after(ip) if password else auth.retry_after(ip)
    if wait:
        mins = max(1, round(wait / 60))
        return JSONResponse({"detail": tr("Too many failed attempts. Try again in about {mins} minute{v}.", mins=mins, v='s' if mins != 1 else '')},
                            status_code=429, headers={"Retry-After": str(wait)})
    return None


def is_https(conn) -> bool:
    return conn.url.scheme in ("https", "wss")


def signed_in(request: Request, response: JSONResponse, user_id: int | None = None) -> JSONResponse:
    token = auth.create_session(client_ip(request), request.headers.get("user-agent", ""), user_id=user_id)
    response.set_cookie(auth.COOKIE, token, max_age=auth.MAX_AGE_SECONDS, path="/", httponly=True,
                        samesite="strict", secure=is_https(request) or auth.server_mode())
    return response
