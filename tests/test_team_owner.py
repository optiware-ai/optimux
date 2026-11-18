"""Team version, step 4a: every chat, usage row, request and sent prompt records whose it is.
NULL means the install's own (solo, the admin, or from before team mode)."""
import asyncio

import pytest

from optimux_backend.core import sent, sessions, store, tasks, team, usage


def one(table, col="user_id"):
    conn = store.connect()
    try:
        return [r[col] for r in conn.execute(f"SELECT {col} FROM {table} ORDER BY rowid")]
    finally:
        conn.close()


def test_the_columns_exist():
    conn = store.connect()
    try:
        for table in ("sessions", "usage", "tasks", "sent_calls"):
            assert "user_id" in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}, table
    finally:
        conn.close()


def make_all():
    sessions.create("alpha", "hello")
    usage.insert({"project": "alpha", "model": "m"})
    t = tasks.new("prompt", "alpha")
    t["calls"] = 1
    tasks.finish(t, "chat-1")
    sent.record("chat-1", "simple", "m", [{"role": "user", "content": "hi"}], None, project="alpha")


def test_nothing_bound_means_the_installs_own():
    make_all()
    for table in ("sessions", "usage", "tasks", "sent_calls"):
        assert one(table) == [None], table


def test_a_bound_user_owns_what_is_recorded():
    async def run():
        team.bind_user(7)
        make_all()
        assert team.current_user_id() == 7

    asyncio.run(run())
    for table in ("sessions", "usage", "tasks", "sent_calls"):
        assert one(table) == [7], table
    assert team.current_user_id() is None, "the caller's context is untouched"


def test_two_connections_record_their_own():
    async def conn(uid):
        team.bind_user(uid)
        await asyncio.sleep(0.01)
        usage.insert({"project": "alpha", "model": "m", "session": f"s{uid}"})

    async def both():
        await asyncio.gather(asyncio.create_task(conn(1)), asyncio.create_task(conn(2)), asyncio.create_task(conn(None)))

    asyncio.run(both())
    conn = store.connect()
    try:
        got = {r["session"]: r["user_id"] for r in conn.execute("SELECT session, user_id FROM usage")}
    finally:
        conn.close()
    assert got == {"s1": 1, "s2": 2, "sNone": None}


def test_an_entry_cannot_name_another_owner():
    async def run():
        team.bind_user(3)
        usage.insert({"project": "alpha", "model": "m", "user_id": 99})

    asyncio.run(run())
    assert one("usage") == [3]
