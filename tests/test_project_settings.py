"""v0.8: a project's own values for some global settings (core/project_settings.py),
how they apply (config_manager.effective_settings) and the Project page's API."""
import asyncio

import pytest

import optimux_backend.main as main
from optimux_backend.core import config_manager, executor, project_settings as ps, sandbox, sessions
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def settings(monkeypatch):
    s = {"projects_root": "/ws", "active_project": "demo", "plan_mode": "off", "scout": True, "memory_curator": True,
         "curator_idle_minutes": 10, "max_steps_simple": 8, "max_steps_complex": 25, "command_timeout": 120,
         "sandbox_network": True}
    monkeypatch.setattr(config_manager, "get_settings", lambda: dict(s))
    return s


def test_overrides_are_checked_set_and_removed():
    assert ps.update("demo", {"plan_mode": "ask", "scout": False, "max_steps_simple": 4}) == \
        {"plan_mode": "ask", "scout": False, "max_steps_simple": 4}
    assert ps.update("demo", {"scout": None}) == {"plan_mode": "ask", "max_steps_simple": 4}     # follows global again
    for bad in ({"plan_mode": "yolo"}, {"scout": "no"}, {"max_steps_simple": 0}, {"max_steps_complex": 101},
                {"command_timeout": 5}, {"curator_idle_minutes": 999}, {"sandbox_network": 1}, {"projects_root": "/"},
                {"max_steps_simple": True}):
        with pytest.raises(ps.ProjectSettingsError):
            ps.update("demo", bad)
    assert ps.overrides("demo") == {"plan_mode": "ask", "max_steps_simple": 4}            # nothing half-saved
    for name in ("../x", ".hidden", "", "a/b"):
        with pytest.raises(ps.ProjectSettingsError):
            ps.update(name, {"scout": True})
    ps.update("demo", {"plan_mode": None, "max_steps_simple": None})
    assert ps._read()["projects"] == {}


def test_effective_settings_follow_the_active_project(settings):
    ps.update("demo", {"plan_mode": "auto", "command_timeout": 600})
    ps.update("other", {"plan_mode": "ask"})
    assert config_manager.effective_settings()["plan_mode"] == "auto"
    assert config_manager.effective_settings("other")["plan_mode"] == "ask"
    assert config_manager.effective_settings("")["plan_mode"] == "off"                     # the Workspace
    assert config_manager.command_timeout_for() == 600
    settings["active_project"] = "third"
    assert config_manager.effective_settings()["plan_mode"] == "off" and config_manager.command_timeout_for() == 120


def test_step_limits_plan_mode_and_scout(settings, monkeypatch):
    ps.update("demo", {"max_steps_simple": 3, "max_steps_complex": 40, "plan_mode": "ask", "scout": False})
    assert config_manager.max_steps_for("simple") == 3 and config_manager.max_steps_for("override") == 40
    assert config_manager.max_steps_for("simple", "other") == 8
    assert main.plan_mode() == "ask"
    assert main.effective_settings().get("scout") is False


def test_memory_updates_follow_the_chat_project(settings):
    ps.update("demo", {"memory_curator": False, "curator_idle_minutes": 2})
    sid = sessions.create("other", "a chat")
    ws = sessions.create("", "a Workspace chat")
    assert main.curator_enabled("demo") is False and main.curator_idle_seconds("demo") == 120
    assert main.curator_enabled("other") is True and main.curator_idle_seconds("other") == 600
    assert main.chat_project(None) is None and main.chat_project("no-such-chat") is None
    assert main.chat_project(sid) == "other" and main.chat_project(ws) == ""
    assert main.curator_enabled(main.chat_project(sid)) is True


def test_commands_use_the_project_network(monkeypatch):
    seen = {}
    monkeypatch.setattr(executor, "get_settings", lambda: {"active_project": "demo", "sandbox": True, "sandbox_network": True})
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": True, "path": "/usr/bin/bwrap"})

    def argv(command, workdir, shell, network, ro_paths, bwrap):
        seen["network"] = network
        return ["true"]
    monkeypatch.setattr(sandbox, "build_argv", argv)
    asyncio.run(executor.run_shell_command("true", cwd="/tmp"))
    assert seen["network"] is True
    ps.update("demo", {"sandbox_network": False})
    asyncio.run(executor.run_shell_command("true", cwd="/tmp"))
    assert seen["network"] is False


def test_api(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    assert client.get("/api/projects/demo/settings").status_code == 401
    r = client.get("/api/projects/demo/settings", headers=H).json()
    assert r["overrides"] == {} and r["global"]["max_steps_simple"] == 8 and r["effective"] == r["global"]
    assert set(r["keys"]) == set(ps.KEYS)
    r = client.post("/api/projects/demo/settings", headers=H, json={"plan_mode": "auto", "scout": False}).json()
    assert r["overrides"] == {"plan_mode": "auto", "scout": False} and r["effective"]["plan_mode"] == "auto"
    assert r["global"]["plan_mode"] == "off"
    bad = client.post("/api/projects/demo/settings", headers=H, json={"plan_mode": "x"})
    assert bad.status_code == 422 and "plan_mode" in bad.json()["detail"]
    assert client.post("/api/projects/demo/settings", headers=H, json={"auto_approve": True}).status_code == 422
    assert client.get("/api/projects/.hidden/settings", headers=H).status_code == 422
    r = client.post("/api/projects/demo/settings", headers=H, json={"plan_mode": None}).json()
    assert r["overrides"] == {"scout": False}
