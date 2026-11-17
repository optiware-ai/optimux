"""Team version, step 3b: an active project for one connection only.
The saved setting is the install's (the admin's); a bound project is read by everything that
asks get_settings(), but only inside the task that bound it, and is never saved."""
import asyncio
import json

import pytest

from optimux_backend.core import config_manager as cm, project_files, team, workspace
from tests.test_team_users import ORIGIN, home, invite_and_activate, teamed, app_client, solo  # noqa: F401


@pytest.fixture
def saved(home, monkeypatch):
    path = home / "settings.json"
    root = home / "ws"
    (root / "shared").mkdir(parents=True)
    path.write_text(json.dumps({"projects_root": str(root), "active_project": "shared"}))
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(path))
    return path


def test_nothing_changes_until_a_project_is_bound(saved):
    assert cm.get_settings()["active_project"] == "shared"


def test_a_bound_project_replaces_the_saved_one_in_its_task_only(saved):
    async def run():
        cm.bind_project("mine")
        inner = cm.get_settings()["active_project"]
        child = await asyncio.create_task(asyncio.sleep(0, result=cm.get_settings()["active_project"]))
        return inner, child

    inner, child = asyncio.run(run())
    assert (inner, child) == ("mine", "mine"), "tasks started after the bind inherit it"
    assert cm.get_settings()["active_project"] == "shared", "the caller's own context is untouched"


def test_two_connections_do_not_see_each_other(saved):
    async def conn(name):
        cm.bind_project(name)
        await asyncio.sleep(0.01)
        return cm.get_settings()["active_project"]

    async def both():
        return await asyncio.gather(asyncio.create_task(conn("a")), asyncio.create_task(conn("b")), asyncio.create_task(conn(None)))

    assert asyncio.run(both()) == ["a", "b", "shared"]


def test_the_bound_project_is_never_saved(saved):
    async def run():
        cm.bind_project("mine")
        cm.update_settings(sandbox=True)

    asyncio.run(run())
    assert json.loads(saved.read_text())["active_project"] == "shared"


def test_the_other_readers_follow_the_bound_project(saved, monkeypatch):
    from optimux_backend import main
    monkeypatch.setattr(project_files, "project_dir", lambda name: f"/w/{name}")

    async def run():
        cm.bind_project("mine")
        return main.active_project(), project_files.active_project(), cm.effective_settings()["active_project"]

    assert asyncio.run(run()) == ("mine", ("mine", "/w/mine"), "mine")


def test_a_member_starts_on_their_first_project(teamed, home, monkeypatch):
    for n in ("alpha", "beta", "gamma"):
        (home / "w" / n).mkdir(parents=True)
    monkeypatch.setattr(workspace, "projects_root", lambda: str(home / "w"))
    _, a = invite_and_activate(teamed, "anna")
    assert team.default_project(a["id"], ["alpha", "beta"]) == ""
    for p in ("beta", "alpha"):
        teamed.post(f"/api/team/projects/{p}/members/{a['id']}", json={"member": True},
                    headers=ORIGIN)
    assert team.default_project(a["id"], ["alpha", "beta", "gamma"]) == "alpha"
