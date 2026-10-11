"""Signing in to Optimux (Feature 23).

- One password (scrypt hash) and optional two-step codes (TOTP, RFC 6238),
  kept in ~/.optimux/auth.json, readable only by the owner.
- Browsers get a random session id in an HttpOnly, SameSite=Strict cookie.
  Only its SHA-256 is stored (table `logins`), so the database and its
  backups can't be used to sign in. Sessions end after 14 idle days or 30
  days in all, on sign-out, on "sign out everywhere" and on a password change.
- Failed attempts are limited per client address (5 in 15 minutes, then a
  15-minute wait).
- The first password is set with the access token from secrets.env, so only
  someone who can read the server's files can claim a new install.

Scripts can still use the access token as a Bearer header.
Kept free of FastAPI imports so it can be unit-tested in isolation.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import struct
import threading
import time
from collections import deque
from urllib.parse import quote

from optimux_backend.core import paths, store

COOKIE = "optimux_session"
IDLE_SECONDS = 14 * 24 * 3600
MAX_AGE_SECONDS = 30 * 24 * 3600
MIN_PASSWORD = 12
MAX_PASSWORD = 1024
SERVER_ENV = "OPTIMUX_SERVER"

# scrypt cost: ~50 ms and 16 MiB per check; slow enough to make guessing expensive
_N, _R, _P = 2 ** 14, 8, 1


class AuthError(ValueError):
    pass


def server_mode() -> bool:
    """OPTIMUX_SERVER=1: running where others can reach it (see docs/GUIDE.md)."""
    return os.environ.get(SERVER_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def auth_file() -> str:
    return os.path.join(paths.HOME, "auth.json")


# ---------------------------------------------------------------- stored state

_lock = threading.Lock()


def _load() -> dict:
    """The stored state. A file that exists but can't be read fails closed:
    it counts as "a password is set, two-step codes are on, nothing matches",
    never as a fresh install."""
    try:
        with open(auth_file()) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    print(f"[Optimux] {auth_file()} is unreadable: sign-in is locked until it is fixed or deleted.")
    return {"corrupt": True}


def _save(data: dict) -> None:
    paths.write_private(auth_file(), json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------- passwords

def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def check_new_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters (a few random words work well).")
    if len(password) > MAX_PASSWORD:
        raise AuthError("That password is too long.")
    return password


def password_set() -> bool:
    data = _load()
    return bool(data.get("password") or data.get("corrupt"))


def set_password(new: str) -> None:
    check_new_password(new)
    with _lock:
        data = _load()
        if data.get("corrupt"):
            raise AuthError(f"{auth_file()} is unreadable; fix or delete it on the server.")
        data["password"] = hash_password(new)
        data["password_changed"] = int(time.time())
        _save(data)


def check_password(password: str) -> bool:
    stored = _load().get("password")
    if not stored or not isinstance(password, str) or len(password) > MAX_PASSWORD:
        hash_password("timing-equalizer")  # same cost whether or not a password exists
        return False
    return verify_password(password, stored)


# ---------------------------------------------------------------- two-step codes (TOTP)

STEP = 30
DIGITS = 6


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def match_totp(secret: str, code: str, now: float | None = None, after_step: int = -1) -> int | None:
    """The time step the code belongs to (current one ±1, for clock drift), or None.
    Steps at or before `after_step` are refused, so a code works only once."""
    code = (code or "").replace(" ", "")
    if not (code.isascii() and code.isdigit() and len(code) == DIGITS):
        return None
    now_step = int((time.time() if now is None else now) // STEP)
    for step in (now_step - 1, now_step, now_step + 1):
        if step > after_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def totp_enabled() -> bool:
    data = _load()
    t = data.get("totp") or {}
    return bool(data.get("corrupt") or (t.get("enabled") and t.get("secret")))


def otpauth_uri(secret: str, label: str = "Optimux") -> str:
    return (f"otpauth://totp/{quote(label)}?secret={secret}&issuer=Optimux"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def start_totp() -> dict:
    """A new secret waiting to be confirmed with a code (nothing changes until then).
    Refused while codes are on: switching phones means turning codes off first,
    which needs a current code, so a stolen password can't move them."""
    secret = new_totp_secret()
    with _lock:
        data = _load()
        if data.get("corrupt") or (data.get("totp") or {}).get("enabled"):
            raise AuthError("Two-step codes are already on. Turn them off first (that needs a current code).")
        data["totp_pending"] = secret
        _save(data)
    return {"secret": secret, "uri": otpauth_uri(secret)}


def confirm_totp(code: str) -> bool:
    with _lock:
        data = _load()
        secret = data.get("totp_pending")
        if (data.get("totp") or {}).get("enabled"):
            return False
        step = match_totp(secret, code) if secret else None
        if step is None:
            return False
        data["totp"] = {"secret": secret, "enabled": True, "last_step": step}
        data.pop("totp_pending", None)
        _save(data)
    return True


def check_totp(code: str) -> bool:
    """Check a sign-in code against the enabled secret, once per code."""
    with _lock:
        data = _load()
        if data.get("corrupt"):
            return False
        t = data.get("totp") or {}
        if not (t.get("enabled") and t.get("secret")):
            return True
        step = match_totp(t["secret"], code, after_step=int(t.get("last_step", -1)))
        if step is None:
            return False
        t["last_step"] = step
        data["totp"] = t
        _save(data)
    return True


def disable_totp() -> None:
    with _lock:
        data = _load()
        data.pop("totp", None)
        data.pop("totp_pending", None)
        _save(data)


# ---------------------------------------------------------------- sessions

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_hash(token: str) -> str:
    return _hash(token)


def create_session(ip: str = "", agent: str = "", now: float | None = None) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time() if now is None else now
    conn = store.connect()
    try:
        with conn:
            conn.execute("INSERT INTO logins (id, created, last_seen, ip, agent) VALUES (?, ?, ?, ?, ?)",
                         (_hash(token), now, now, ip[:64], agent[:200]))
    finally:
        conn.close()
    return token


def check_session(token: str | None, now: float | None = None) -> str | None:
    """The session's id (hash) if the cookie is valid; refreshes its idle timer."""
    if not token or len(token) > 200:
        return None
    now = time.time() if now is None else now
    sid = _hash(token)
    conn = store.connect()
    try:
        row = conn.execute("SELECT created, last_seen FROM logins WHERE id = ?", (sid,)).fetchone()
        if not row:
            return None
        if now - row["created"] > MAX_AGE_SECONDS or now - row["last_seen"] > IDLE_SECONDS:
            with conn:
                conn.execute("DELETE FROM logins WHERE id = ?", (sid,))
            return None
        if now - row["last_seen"] > 60:
            with conn:
                conn.execute("UPDATE logins SET last_seen = ? WHERE id = ?", (now, sid))
        return sid
    finally:
        conn.close()


def session_alive(session_id: str, now: float | None = None) -> bool:
    """Whether a session (by id) still exists and hasn't expired; for open chats.
    Using a chat counts as activity, like any request."""
    now = time.time() if now is None else now
    conn = store.connect()
    try:
        row = conn.execute("SELECT created, last_seen FROM logins WHERE id = ?", (session_id,)).fetchone()
        if not row or now - row["created"] > MAX_AGE_SECONDS or now - row["last_seen"] > IDLE_SECONDS:
            return False
        if now - row["last_seen"] > 60:
            with conn:
                conn.execute("UPDATE logins SET last_seen = ? WHERE id = ?", (now, session_id))
        return True
    finally:
        conn.close()


def end_session(token_or_id: str, is_id: bool = False) -> None:
    sid = token_or_id if is_id else _hash(token_or_id)
    conn = store.connect()
    try:
        with conn:
            conn.execute("DELETE FROM logins WHERE id = ?", (sid,))
    finally:
        conn.close()


def end_all_sessions(keep_id: str | None = None) -> int:
    conn = store.connect()
    try:
        with conn:
            cur = conn.execute("DELETE FROM logins WHERE id != ?", (keep_id or "",))
            return cur.rowcount
    finally:
        conn.close()


def list_sessions(current_id: str | None = None) -> list[dict]:
    conn = store.connect()
    try:
        rows = conn.execute("SELECT id, created, last_seen, ip, agent FROM logins ORDER BY last_seen DESC").fetchall()
    finally:
        conn.close()
    return [{"created": r["created"], "last_seen": r["last_seen"], "ip": r["ip"], "agent": r["agent"],
             "current": r["id"] == current_id} for r in rows]


# ---------------------------------------------------------------- failed attempts

def client_key(ip: str) -> str:
    """The rate-limit key for an address: IPv6 addresses count per /64, since
    one machine usually controls a whole /64."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "unknown"
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


class RateLimiter:
    """Failure counter: `limit` failures within `window` seconds lock that key
    until the oldest of them is `window` seconds old."""

    def __init__(self, limit: int = 5, window: float = 15 * 60):
        self.limit, self.window = limit, window
        self._fails: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque:
        q = self._fails.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def retry_after(self, key: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            q = self._trim(key, now)
            if not q:
                self._fails.pop(key, None)
            if len(q) < self.limit:
                return 0
            return max(1, int(q[0] + self.window - now + 0.999))

    def fail(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._trim(key, now).append(now)
            if len(self._fails) > 10000:             # drop expired keys so it can't grow without bound
                for k in list(self._fails):
                    if not self._trim(k, now):
                        del self._fails[k]

    def clear(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


# Per address: 5 failures in 15 minutes (passwords, codes, tokens).
limiter = RateLimiter()
# Wrong passwords for the whole install, so changing address (or faking it from
# the same machine) doesn't allow unlimited guessing. Only passwords count:
# junk tokens can't be used to lock everyone out, and the random token
# doesn't need it. Signed-in browsers and the token keep working when it trips.
global_limiter = RateLimiter(limit=30, window=15 * 60)
# Two-step codes: 10 wrong codes an hour for the account, whatever the address
code_limiter = RateLimiter(limit=10, window=60 * 60)
GLOBAL = "*"


def retry_after(ip: str) -> int:
    return limiter.retry_after(client_key(ip))


def failed(ip: str) -> None:
    limiter.fail(client_key(ip))


def password_retry_after(ip: str) -> int:
    return max(retry_after(ip), global_limiter.retry_after(GLOBAL))


def password_failed(ip: str) -> None:
    failed(ip)
    global_limiter.fail(GLOBAL)


def succeeded(ip: str) -> None:
    limiter.clear(client_key(ip))
