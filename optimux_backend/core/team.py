"""The team version, step 1: members, invitations, consent and the access log.

Optimux starts as a "team of one" (solo). `optimux enable-team` turns the
existing password into the first admin; the admin then invites members. Solo
installs never touch this module's tables.

- Two doors: members sign in with their name and password; the admin signs in
  with the admin password (and two-step codes) through the admin door. The
  member door never opens an admin account.
- Accounts are invite-only. An invitation is a random token shown once; only
  its SHA-256 is stored. It works once, for 7 days, and a new invitation
  replaces the old link and the old password.
- Consent is recorded when the member activates the account: the choice, the
  date and the version of the text they saw. Every change is kept as history.
  By default the admin sees usage and cost only; reading chats needs the
  member's "yes", which they can take back at any time.
- Every time an admin opens a member's data it is logged (what kind and which
  item, never the content). The member can read their own lines; the database
  refuses to change or erase them.

No FastAPI imports, so it can be tested alone.
"""
import contextvars
import hashlib
import re
import secrets
import time

from optimux_backend.core import auth, store
from optimux_backend.core.i18n import tk, tr

INVITE_SECONDS = 7 * 24 * 3600
CONSENT_VERSION = 1
NAME = re.compile(r"[\w][\w .'\-]{1,39}")
LOG_KINDS = ("chat", "sent", "usage")

DISABLED_TEXT = tk("Your account has been disabled by the admin. Please contact your admin.")

CONSENT_TEXT = tk(
    "Your admin always sees your usage of the models: time, model, cost and counts. "
    "If you choose \"Admin may also read my chats\", the admin can open your chats and what was sent to the models; "
    "that includes the text of the memory sent with your requests. "
    "Project memory is shared with everyone on the project, and it is updated from the chats held in it. "
    "Every time the admin opens your data it is logged, and you can read that log. "
    "You can revoke this choice at any time; it stops future access but cannot undo what was already seen.")


class TeamError(ValueError):
    """Carries an HTTP status for the endpoints."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _query(sql: str, args: tuple = ()):
    conn = store.connect()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _write(sql: str, args: tuple = ()) -> int:
    conn = store.connect()
    try:
        with conn:
            return conn.execute(sql, args).lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------- mode

def mode() -> str:
    return "team" if store.get_meta("mode") == "team" else "solo"


def enabled() -> bool:
    return mode() == "team"


def clean_name(name: str) -> str:
    name = (name or "").strip() if isinstance(name, str) else ""
    if not NAME.fullmatch(name):
        raise TeamError(tr("Use 2 to 40 letters, digits, spaces, dots, dashes or apostrophes for the name."))
    return name


def enable(admin_name: str = "admin") -> dict:
    """Team mode on; the existing sign-in becomes the first admin's. Safe to repeat."""
    if not auth.password_set():
        raise TeamError(tr("Set the sign-in password first: team mode turns it into the admin's."))
    row = _query("SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    if row:
        admin = _public(row[0])
    else:
        name = clean_name(admin_name or "admin")
        uid = _write("INSERT INTO users (name, role, status, created) VALUES (?, 'admin', 'active', ?)", (name, time.time()))
        admin = _public(_query("SELECT * FROM users WHERE id = ?", (uid,))[0])
    store.set_meta("mode", "team")
    return admin


def disable() -> int:
    """Back to solo: members are signed out and can't sign in; their data stays."""
    store.set_meta("mode", "solo")
    conn = store.connect()
    try:
        with conn:
            cur = conn.execute("DELETE FROM logins WHERE user_id IN (SELECT id FROM users WHERE role = 'user')")
            return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------- users

def _consent(row) -> dict | None:
    return {"version": row["consent_version"], "at": row["consent_at"]} if row["consent_at"] else None


def _public(row, with_sessions: bool = False) -> dict:
    out = {"id": row["id"], "name": row["name"], "role": row["role"], "status": row["status"],
           "created": row["created"], "last_login": row["last_login"],
           "chat_access": bool(row["chat_access"]), "consent": _consent(row)}
    if with_sessions:
        out["signed_in"], out["last_seen"] = _presence(row)
    return out


def _live(created: float, last_seen: float, now: float) -> bool:
    return now - created <= auth.MAX_AGE_SECONDS and now - last_seen <= auth.IDLE_SECONDS


def _presence(row) -> tuple[bool, float | None]:
    now = time.time()
    if row["status"] == "disabled":
        return False, None
    if row["role"] == "admin":      # browsers signed in before team mode belong to the admin too
        sessions = _query("SELECT created, last_seen FROM logins WHERE user_id IS NULL OR user_id = ?", (row["id"],))
    else:
        sessions = _query("SELECT created, last_seen FROM logins WHERE user_id = ?", (row["id"],))
    seen = [s["last_seen"] for s in sessions]
    return any(_live(s["created"], s["last_seen"], now) for s in sessions), (max(seen) if seen else None)


def get(user_id: int) -> dict | None:
    rows = _query("SELECT * FROM users WHERE id = ?", (user_id,))
    return _public(rows[0]) if rows else None


def get_by_name(name: str) -> dict | None:
    rows = _query("SELECT * FROM users WHERE name = ?", (name,))
    return _public(rows[0]) if rows else None


def first_admin_id() -> int | None:
    rows = _query("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    return rows[0]["id"] if rows else None


def list_users() -> list[dict]:
    return [_public(r, with_sessions=True) for r in _query("SELECT * FROM users ORDER BY role = 'admin' DESC, name COLLATE NOCASE")]


def session_user(login_id: str | None) -> dict | None:
    """The member or admin behind a browser session; None for the sessions
    that belong to the install itself (solo mode, or signed in before team mode)."""
    if not login_id:
        return None
    rows = _query("SELECT u.id, u.name, u.role, u.status FROM logins l JOIN users u ON u.id = l.user_id WHERE l.id = ?", (login_id,))
    return dict(rows[0]) if rows else None


def me(login_id: str | None) -> dict:
    """What the page needs to know about whoever is signed in."""
    if not enabled():
        return {"mode": "solo", "admin": True, "user": None}
    who = session_user(login_id)
    if who and who["role"] == "user":
        return {"mode": "team", "admin": False, "user": get(who["id"]), "consent_text": tr(CONSENT_TEXT)}
    uid = who["id"] if who else first_admin_id()
    return {"mode": "team", "admin": True, "user": get(uid) if uid else None}


# ---------------------------------------------------------------- invitations

def invite(name: str) -> dict:
    name = clean_name(name)
    if get_by_name(name):
        raise TeamError(tr("That name is already taken."), 409)
    uid = _write("INSERT INTO users (name, role, status, created) VALUES (?, 'user', 'invited', ?)", (name, time.time()))
    return reinvite(uid)


def reinvite(user_id: int) -> dict:
    """A new link; the old one and the old password stop working and the member is signed out."""
    user = get(user_id)
    if not user or user["role"] != "user":
        raise TeamError(tr("No such member."), 404)
    token = secrets.token_urlsafe(24)
    expires = time.time() + INVITE_SECONDS
    _write("UPDATE users SET status = 'invited', password = NULL, invite_hash = ?, invite_expires = ? WHERE id = ?",
           (_hash(token), expires, user_id))
    auth.end_user_sessions(user_id)
    return {"id": user_id, "name": user["name"], "token": token, "expires": expires}


def _by_token(token: str):
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        return None
    rows = _query("SELECT * FROM users WHERE role = 'user' AND status = 'invited' AND invite_hash = ? AND invite_expires > ?",
                  (_hash(token), time.time()))
    return rows[0] if rows else None


def invite_info(token: str) -> dict | None:
    row = _by_token(token)
    if not row:
        return None
    return {"name": row["name"], "consent": {"version": CONSENT_VERSION, "text": tr(CONSENT_TEXT)}}


def activate(token: str, password: str, chat_access: bool) -> dict | None:
    """Set the member's password and record their consent choice; None for a bad token."""
    row = _by_token(token)
    if not row:
        return None
    auth.check_new_password(password)
    now = time.time()
    conn = store.connect()
    try:
        with conn:
            cur = conn.execute(
                "UPDATE users SET password = ?, status = 'active', invite_hash = NULL, invite_expires = NULL, last_login = ?, "
                "chat_access = ?, consent_version = ?, consent_at = ? WHERE id = ? AND status = 'invited'",
                (auth.hash_password(password), now, int(bool(chat_access)), CONSENT_VERSION, now, row["id"]))
            if not cur.rowcount:
                return None
            conn.execute("INSERT INTO consent_history (user_id, ts, chat_access, version) VALUES (?, ?, ?, ?)",
                         (row["id"], now, int(bool(chat_access)), CONSENT_VERSION))
    finally:
        conn.close()
    return get(row["id"])


def check_login(name: str, password: str) -> dict | None:
    """A member's name and password. Admin accounts never match here. A disabled member with the
    right password is returned too (status 'disabled') so they can be told why; the caller must not sign them in."""
    rows = _query("SELECT * FROM users WHERE name = ? AND role = 'user' AND status IN ('active', 'disabled') AND password IS NOT NULL",
                  (name.strip() if isinstance(name, str) else "",))
    if not rows or not isinstance(password, str) or len(password) > auth.MAX_PASSWORD:
        auth.hash_password("timing-equalizer")      # same cost whether or not the name exists
        return None
    return _public(rows[0]) if auth.verify_password(password, rows[0]["password"]) else None


def touch_login(user_id: int) -> None:
    _write("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user_id))


def set_disabled(user_id: int, disabled: bool) -> dict:
    user = get(user_id)
    if not user or user["role"] != "user":
        raise TeamError(tr("No such member."), 404)
    if disabled:
        # The sessions stay for now: the member's open page asks, is told why, and signs itself out.
        _write("UPDATE users SET status = 'disabled' WHERE id = ?", (user_id,))
    elif user["status"] == "disabled":
        has_password = bool(_query("SELECT 1 FROM users WHERE id = ? AND password IS NOT NULL", (user_id,)))
        _write("UPDATE users SET status = ? WHERE id = ?", ("active" if has_password else "invited", user_id))
        auth.end_user_sessions(user_id)         # a fresh sign-in after being enabled again
    return get(user_id)


# ---------------------------------------------------------------- projects

def set_member(project: str, user_id: int, member: bool) -> None:
    """Put a member on a project or take them off (safe to repeat). Admins are on every project."""
    user = get(user_id)
    if not user:
        raise TeamError(tr("No such member."), 404)
    if user["role"] != "user":
        raise TeamError(tr("The admin is on every project already."))
    if member:
        _write("INSERT OR IGNORE INTO project_members (project, user_id, added) VALUES (?, ?, ?)", (project, user_id, time.time()))
    else:
        _write("DELETE FROM project_members WHERE project = ? AND user_id = ?", (project, user_id))


def project_members() -> dict[str, list[int]]:
    """Every project that has members: name -> member ids."""
    out: dict[str, list[int]] = {}
    for r in _query("SELECT project, user_id FROM project_members ORDER BY project, user_id"):
        out.setdefault(r["project"], []).append(r["user_id"])
    return out


def projects_of(user_id: int, existing: list[str]) -> list[str]:
    """The member's projects that are in the Workspace now (an archived one comes back with its folder)."""
    mine = {r["project"] for r in _query("SELECT project FROM project_members WHERE user_id = ?", (user_id,))}
    return [name for name in existing if name in mine]


# Whose work is being recorded. Bound in the task that serves a member's connection
# (tasks started later inherit it), read where chats, calls and requests are stored.
_BOUND_USER: contextvars.ContextVar = contextvars.ContextVar("optimux_bound_user", default=None)


def bind_user(user_id: int | None) -> None:
    """Record what this task and the ones it starts do as this member's; None is the install's own."""
    _BOUND_USER.set(user_id)


def current_user_id() -> int | None:
    return _BOUND_USER.get()


def default_project(user_id: int, existing: list[str]) -> str:
    """The project a member starts on: their first one in alphabetical order, or "" if they have none."""
    mine = projects_of(user_id, existing)
    return mine[0] if mine else ""


def can_use_project(user_id: int, project: str) -> bool:
    """The admin: every project. A member: only the ones they were put on."""
    user = get(user_id)
    if not user:
        return False
    if user["role"] == "admin":
        return True
    return bool(_query("SELECT 1 FROM project_members WHERE project = ? AND user_id = ?", (project, user_id)))


# ---------------------------------------------------------------- consent

def set_chat_access(user_id: int, allow: bool) -> dict:
    now = time.time()
    conn = store.connect()
    try:
        with conn:
            conn.execute("UPDATE users SET chat_access = ?, consent_version = ?, consent_at = ? WHERE id = ?",
                         (int(bool(allow)), CONSENT_VERSION, now, user_id))
            conn.execute("INSERT INTO consent_history (user_id, ts, chat_access, version) VALUES (?, ?, ?, ?)",
                         (user_id, now, int(bool(allow)), CONSENT_VERSION))
    finally:
        conn.close()
    return get(user_id)


def admin_may_read_chats(user_id: int) -> bool:
    rows = _query("SELECT chat_access FROM users WHERE id = ? AND status = 'active'", (user_id,))
    return bool(rows and rows[0]["chat_access"])


def consent_history(user_id: int) -> list[dict]:
    return [{"at": r["ts"], "chat_access": bool(r["chat_access"]), "version": r["version"]}
            for r in _query("SELECT * FROM consent_history WHERE user_id = ? ORDER BY id", (user_id,))]


# ---------------------------------------------------------------- the access log

def log_admin_access(admin_id: int, user_id: int, kind: str, ref: str = "") -> None:
    if kind not in LOG_KINDS:
        raise TeamError(tr("Unknown kind of access."))
    _write("INSERT INTO admin_access (ts, admin_id, user_id, kind, ref) VALUES (?, ?, ?, ?, ?)",
           (time.time(), admin_id, user_id, kind, (ref or "")[:200]))


def _lines(where: str, args: tuple) -> list[dict]:
    rows = _query("SELECT a.ts, a.kind, a.ref, adm.name AS admin, usr.name AS user FROM admin_access a "
                  "LEFT JOIN users adm ON adm.id = a.admin_id LEFT JOIN users usr ON usr.id = a.user_id "
                  f"{where} ORDER BY a.id DESC LIMIT 500", args)
    return [{"at": r["ts"], "kind": r["kind"], "ref": r["ref"], "admin": r["admin"], "user": r["user"]} for r in rows]


def audit_for_user(user_id: int) -> list[dict]:
    return _lines("WHERE a.user_id = ?", (user_id,))


def audit_all() -> list[dict]:
    return _lines("", ())
