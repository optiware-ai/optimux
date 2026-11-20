"""Identity (v0.8): the two facts the core keeps about the team version.

Which mode this install is in, and whose work a task records. They live in
core/identity.py, so the core does not import the team code. The team code
binds the person; the core only reads it.
"""
import asyncio
import os
import re

import pytest

from optimux_backend.core import identity, store

CORE_DIR = os.path.join(os.path.dirname(__file__), "..", "optimux_backend", "core")
# Core modules that record whose work something is, or that depend on the mode.
# They must read it from identity and never import the team code.
CORE_MODULES = ("budgets", "autonomy", "executor", "memory_manager", "sent", "sandbox",
                "sessions", "usage", "tasks")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("OPTIMUX_HOME", str(tmp_path))
    identity.bind_user(None)
    yield
    identity.bind_user(None)


def test_solo_has_no_owner_and_is_not_team():
    assert identity.current_user_id() is None
    assert identity.mode() == "solo"
    assert identity.enabled() is False


def test_mode_follows_the_stored_setting():
    store.set_meta("mode", "team")
    assert identity.mode() == "team" and identity.enabled() is True
    store.set_meta("mode", "solo")
    assert identity.mode() == "solo" and identity.enabled() is False


def test_bind_and_read_and_clear():
    identity.bind_user(5)
    assert identity.current_user_id() == 5
    identity.bind_user(None)
    assert identity.current_user_id() is None


def test_a_started_task_inherits_the_owner_and_a_sibling_does_not():
    async def main():
        identity.bind_user(3)                       # the connection's own task

        async def child():
            return identity.current_user_id()       # started later: inherits

        async def other():
            identity.bind_user(9)                   # another connection's task
            await asyncio.sleep(0)
            return identity.current_user_id()

        got = await asyncio.gather(asyncio.create_task(child()), asyncio.create_task(other()))
        return got, identity.current_user_id()

    (child_id, other_id), mine = asyncio.run(main())
    assert child_id == 3 and other_id == 9 and mine == 3


@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_modules_do_not_import_the_team_code(module):
    source = open(os.path.join(CORE_DIR, module + ".py"), encoding="utf-8").read()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"import\s+[^\n]*\bteam\b", code), f"{module}.py imports the team code"
    assert not re.search(r"\bteam\.", code), f"{module}.py calls the team code"
