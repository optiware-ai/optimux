"""Feature 23: what model-run commands can see and use, and server mode."""
import resource

import pytest

from optimux_backend.core import config_manager as cm
from optimux_backend.core import executor
from optimux_backend.core.security import ValidationError


def test_command_env_is_an_allow_list():
    base = {"PATH": "/usr/bin", "HOME": "/h", "LC_ALL": "C", "GEMINI_API_KEY": "k", "GH_TOKEN": "g",
            "AWS_SECRET_ACCESS_KEY": "s", "OPTIMUX_TOKEN": "t", "VIRTUAL_ENV": "/v"}
    env = cm.command_env({}, base)
    assert env == {"PATH": "/usr/bin", "HOME": "/h", "LC_ALL": "C"}
    env = cm.command_env({"command_env": ["VIRTUAL_ENV"]}, base)
    assert env["VIRTUAL_ENV"] == "/v" and "GH_TOKEN" not in env


@pytest.mark.parametrize("name", ["OPTIMUX_TOKEN", "OPENAI_API_KEY", "LD_PRELOAD", "BASH_ENV", "bad name", "OPTIMUX_HOME"])
def test_secret_or_dangerous_names_refused(name):
    with pytest.raises(ValidationError):
        cm.check_command_env([name])


def test_names_from_secrets_file_refused(mocker):
    mocker.patch.object(cm, "get_secret_env_keys", return_value={"GH_TOKEN"})
    with pytest.raises(ValidationError):
        cm.check_command_env(["GH_TOKEN"])
    assert cm.check_command_env(["JAVA_HOME", " ", "JAVA_HOME"]) == ["JAVA_HOME"]


def test_limits_from_settings():
    lim = dict(executor.limits_for({}))
    assert lim[resource.RLIMIT_NOFILE] == 4096 and lim[resource.RLIMIT_FSIZE] == 1024 * 1024 * 1024
    assert resource.RLIMIT_AS not in lim and resource.RLIMIT_NPROC not in lim
    lim = dict(executor.limits_for({"limit_memory_mb": 512, "limit_file_mb": 0, "limit_processes": 200}))
    assert lim[resource.RLIMIT_AS] == 512 * 1024 * 1024 and lim[resource.RLIMIT_NPROC] == 200
    assert resource.RLIMIT_FSIZE not in lim


@pytest.mark.asyncio
async def test_command_sees_no_keys_and_gets_limits(tmp_path, mocker, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value")
    monkeypatch.setenv("SOME_TOKEN", "secret-token")
    mocker.patch.object(executor, "get_settings", return_value={"limit_file_mb": 1})
    mocker.patch.object(cm, "get_settings", return_value={"limit_file_mb": 1})
    r = await executor.run_shell_command("env; echo nofile=$(ulimit -n) fsize=$(ulimit -f)", cwd=str(tmp_path))
    assert r["exit_code"] == 0
    assert "secret-value" not in r["stdout"] and "secret-token" not in r["stdout"]
    assert "nofile=4096" in r["stdout"] and "fsize=1024" in r["stdout"]     # ulimit -f is in 1 KiB blocks
    r = await executor.run_shell_command("head -c 2000000 /dev/zero > big", cwd=str(tmp_path))
    assert r["exit_code"] != 0 and (tmp_path / "big").stat().st_size <= 1024 * 1024


def test_server_mode_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text('{"auto_approve": true}')
    assert cm.get_settings()["auto_approve"] is True
    monkeypatch.setenv("OPTIMUX_SERVER", "1")
    s = cm.get_settings()
    assert s["auto_approve"] is False and s["sandbox_network"] is False
    from optimux_backend.core import sandbox
    assert sandbox.required()
    with pytest.raises(ValidationError):
        cm.save_settings(cm.SettingsPayload(auto_approve=True))
    cm.save_settings(cm.SettingsPayload())          # saving other settings keeps it automatic
    assert cm.get_settings()["sandbox_network"] is False
    monkeypatch.delenv("OPTIMUX_SERVER")
    assert cm.get_settings()["sandbox_network"] is True


@pytest.mark.parametrize("field,value", [("limit_memory_mb", 100), ("limit_processes", 5), ("limit_file_mb", -1)])
def test_bad_limits_refused(field, value, monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    with pytest.raises(ValidationError):
        cm.save_settings(cm.SettingsPayload(**{field: value}))


# ---- IM-03 (v0.5.1): the command timeout is a setting

@pytest.mark.parametrize("value", [9, 1801, -1])
def test_bad_timeouts_refused(value, monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    with pytest.raises(ValidationError, match="command timeout must be between 10 and 1800"):
        cm.save_settings(cm.SettingsPayload(command_timeout=value))


def test_timeout_setting_saved_and_read(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert cm.command_timeout_for() == 120                       # the default, with nothing saved
    cm.save_settings(cm.SettingsPayload(command_timeout=600))
    assert cm.get_settings()["command_timeout"] == 600 and cm.command_timeout_for() == 600
    assert cm.command_timeout_for({"command_timeout": "x"}) == 120
    assert cm.command_timeout_for({"command_timeout": 99999}) == 1800     # a hand-edited file stays in range


@pytest.mark.asyncio
async def test_commands_use_the_setting(tmp_path, mocker):
    mocker.patch.object(executor, "get_settings", return_value={"command_timeout": 10})
    real = executor._watch
    seen = {}

    async def spy(task, limit, on_limit):
        seen["limit"] = limit
        return await real(task, limit, on_limit)
    mocker.patch.object(executor, "_watch", side_effect=spy)
    r = await executor.run_shell_command("echo hi", cwd=str(tmp_path))
    assert r["stdout"] == "hi\n" and seen["limit"] == 10


@pytest.mark.asyncio
async def test_the_timeout_message_names_the_setting(tmp_path, mocker):
    mocker.patch.object(executor, "command_timeout_for", return_value=1)
    r = await executor.run_shell_command("sleep 5", cwd=str(tmp_path))
    assert r["exit_code"] == 124
    assert "timed out after 1s (the limit set in Dashboard → Execution → Command timeout)" in r["stderr"]
    assert "don't retry the same command" in r["stderr"]
