"""Team version, step 9: what a member may do without asking, and whether their commands have a network.
Members start strict (everything asks, no network); the admin loosens it per project."""
import asyncio
import contextvars
import json

import pytest

from optimux_backend.core import autonomy, config_manager as cm, executor, sandbox, team
from tests.test_team_chat import crew, ws_root  # noqa: F401
from tests.test_team_users import ORIGIN, app_client, home, invite_and_activate, solo, teamed  # noqa: F401


@pytest.fixture
def levels(tmp_path, monkeypatch):
    monkeypatch.setattr(autonomy, "AUTONOMY_FILE", str(tmp_path / "autonomy.json"))
    monkeypatch.setattr(autonomy, "server_block", lambda: "")


def as_member(uid, fn):
    def run():
        team.bind_user(uid)
        return fn()
    return contextvars.copy_context().run(run)


def test_members_start_strict(levels):
    assert autonomy.member_rules("alpha") == {"level": "supervised", "network": False}


def test_the_admin_loosens_a_project_for_members(levels):
    autonomy.set_member_rules("alpha", "checkpointed", True)
    assert autonomy.member_rules("alpha") == {"level": "checkpointed", "network": True}
    assert autonomy.member_rules("beta") == {"level": "supervised", "network": False}
    autonomy.set_member_rules("alpha", "supervised", False)
    assert autonomy.member_rules("alpha") == {"level": "supervised", "network": False}


@pytest.mark.parametrize("project,level", [("alpha", "reckless"), ("../x", "supervised"), ("", "supervised")])
def test_member_rules_are_checked(levels, project, level):
    with pytest.raises(ValueError):
        autonomy.set_member_rules(project, level, False)


def test_a_members_level_is_theirs_not_the_projects(levels):
    autonomy.set_level("alpha", "autonomous")                         # the admin's own level for the project
    assert autonomy.project_level("alpha") == "autonomous"
    assert as_member(7, lambda: autonomy.project_level("alpha")) == "supervised"
    autonomy.set_member_rules("alpha", "checkpointed", False)
    assert as_member(7, lambda: autonomy.project_level("alpha")) == "checkpointed"
    assert autonomy.project_level("alpha") == "autonomous"            # nothing changed for the admin
    assert as_member(7, lambda: autonomy.project_level("")) == "supervised"


def test_a_member_is_never_offered_to_change_the_projects_level(levels):
    from optimux_backend import main
    assert main.raise_level_for("alpha", "supervised", "Supervised: ask") == "checkpointed"
    assert as_member(7, lambda: main.raise_level_for("alpha", "supervised", "Supervised: ask")) == ""


def run_command(uid, project, monkeypatch, tmp_path, global_network=True):
    """What network flag the sandbox gets for a command run for `uid` in `project`."""
    seen = {}

    def fake_argv(command, workdir, shell, network=True, **kw):
        seen["network"] = network
        return ["/bin/true"]

    monkeypatch.setattr(sandbox, "enabled", lambda s: True)
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": True, "path": "/usr/bin/bwrap", "error": None})
    monkeypatch.setattr(sandbox, "build_argv", fake_argv)
    monkeypatch.setattr(executor, "get_settings", lambda: {"active_project": project, "sandbox_network": global_network})

    def go():
        if uid is not None:
            team.bind_user(uid)
        return asyncio.run(executor.run_shell_command("echo hi", cwd=str(tmp_path)))

    contextvars.copy_context().run(go)
    return seen["network"]


def test_a_members_command_has_no_network_unless_the_admin_allows_it(levels, monkeypatch, tmp_path):
    assert run_command(None, "alpha", monkeypatch, tmp_path) is True                  # the admin's own: unchanged
    assert run_command(7, "alpha", monkeypatch, tmp_path) is False
    autonomy.set_member_rules("alpha", "supervised", True)
    assert run_command(7, "alpha", monkeypatch, tmp_path) is True
    assert run_command(7, "alpha", monkeypatch, tmp_path, global_network=False) is False     # never more than the install allows
    assert run_command(7, "beta", monkeypatch, tmp_path) is False


def test_the_hint_for_a_member_points_to_the_admin():
    out = "curl: (6) Could not resolve host: example.com"
    assert "Dashboard → Execution" in executor.network_hint(out, 6, True, False)
    hint = executor.network_hint(out, 6, True, False, member=True)
    assert "admin" in hint and "Dashboard → Execution" not in hint


def test_the_admin_sets_and_reads_the_rules(crew, levels):
    admin = crew["admin"]
    assert admin.get("/api/team/projects").json()["rules"]["alpha"] == {"level": "supervised", "network": False}
    r = admin.post("/api/team/projects/alpha/rules", json={"level": "checkpointed", "network": True}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["rules"] == {"level": "checkpointed", "network": True}
    assert admin.get("/api/team/projects").json()["rules"]["alpha"]["network"] is True
    assert admin.post("/api/team/projects/alpha/rules", json={"level": "wild", "network": False}, headers=ORIGIN).status_code == 400
    assert admin.post("/api/team/projects/nope/rules", json={"level": "supervised", "network": False}, headers=ORIGIN).status_code == 404


def test_a_member_cannot_loosen_their_own_rules(crew, levels):
    anna = crew["anna"][0]
    r = anna.post("/api/team/projects/alpha/rules", json={"level": "autonomous", "network": True}, headers=ORIGIN)
    assert r.status_code == 403
    assert autonomy.member_rules("alpha") == {"level": "supervised", "network": False}
