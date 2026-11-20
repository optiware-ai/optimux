"""Identity (v0.8): the two facts the core keeps about the team version.

- Which mode this install is in: "solo" (the default) or "team".
- Whose work a task records: the member bound to the connection that started
  it, or None for the install's own (the solo user, or the admin).

The core reads both here and never imports the team code. The team add-on
binds the person (`bind_user`) where it knows who is connected; everything
that stores a chat, a call or a request reads `current_user_id()`.

No FastAPI imports, so it can be tested alone.
"""
import contextvars
import sqlite3

from optimux_backend.core import store

# Whose work is being recorded. Bound in the task that serves a member's connection
# (tasks started later inherit it), read where chats, calls and requests are stored.
_BOUND_USER: contextvars.ContextVar = contextvars.ContextVar("optimux_bound_user", default=None)


def mode() -> str:
    return "team" if store.get_meta("mode") == "team" else "solo"


def enabled() -> bool:
    return mode() == "team"


def bind_user(user_id: int | None) -> None:
    """Record what this task and the ones it starts do as this member's; None is the install's own."""
    _BOUND_USER.set(user_id)


def current_user_id() -> int | None:
    return _BOUND_USER.get()


def disable_team() -> int:
    """Back to solo, needing no add-on (`optimux disable-team`): members are signed out and can't sign in;
    their data stays. Returns how many member sign-ins ended."""
    store.set_meta("mode", "solo")
    conn = store.connect()
    try:
        with conn:
            try:
                cur = conn.execute("DELETE FROM logins WHERE user_id IN (SELECT id FROM users WHERE role = 'user')")
            except sqlite3.OperationalError:     # no members' table (the add-on never ran here): no members to sign out
                return 0
            return cur.rowcount
    finally:
        conn.close()
