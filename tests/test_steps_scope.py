"""Step limits, the bash shell, the working-directory prompt and outside-path flags."""
import json

import pytest

import optimux_backend.main as main
import optimux_backend.core.config_manager as cm
from optimux_backend.core.agents import get_executor_prompt
from optimux_backend.core.config_manager import SettingsPayload, save_settings, max_steps_for
from optimux_backend.core.executor import run_shell_command
from optimux_backend.core.security import ValidationError, paths_outside_scope
from tests.ws_helpers import Conversation, chunk, fake_stream, make_client, collect
from tests.test_routing_session import Recorder, notes, reply

DONE = {"prompt_tokens": 5, "completion_tokens": 1}
WD = "/home/u/git/projects/demo"


def tool(name, args):
    return fake_stream([chunk(tool_call=(name, args)), chunk(empty=True, usage_obj=DONE)])


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


# ---------- outside-path heuristic

@pytest.mark.parametrize("cmd,flagged", [
    ("cd /home/u/git/Optimux && pwd", ["/home/u/git/Optimux"]),
    ('diff <(cat a.py) "$OLDPWD/executor.py"', ["$OLDPWD/executor.py"]),
    ("cat ~/.ssh/id_rsa", ["~/.ssh/id_rsa"]),
    ("ls ../", ["../"]),
    ("grep -rn foo /etc/passwd", ["/etc/passwd"]),
    ("python3 x.py --out=/var/log/x", ["/var/log/x"]),
    ("echo $HOME", ["$HOME"]),
    ('find . -name executor.py -not -path "*/node_modules/*"', []),
    ("cat -n executor.py 2>/dev/null; ls -la", []),
    ("/usr/bin/env python3 x.py > /dev/null", []),
    (f"cat {WD}/a.py", []),
    ("ls sub/../other", []),
    ("awk '/pattern/ {print}' f.txt", []),
    ("curl https://example.com/x", []),
])
def test_paths_outside_scope(cmd, flagged, monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert paths_outside_scope(cmd, WD) == flagged


def test_approval_request_carries_outside_paths(mocker, client):
    mocker.patch.object(main, "current_workdir", return_value=WD)
    mocker.patch.object(main, "run_shell_command")
    responses = iter([tool("execute_bash", {"command": "cd /home/u/git/Optimux && ls"}), reply("ok")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "look around", "model": "auto"}))
    while True:
        m = json.loads(chat.ws.receive_text())
        if m["type"] == "approval_request":
            break
    assert m["outside_paths"] == ["/home/u/git/Optimux"] and m["workdir"] == WD
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": False}))
    collect(chat.ws)
    chat.close()


# ---------- working directory in the prompt

def test_executor_prompt_names_the_project():
    p = get_executor_prompt("MEM", WD)
    assert f"active project: {WD}" in p and "Work only inside it" in p
    assert p.index("Work only inside it") < p.index("MEM")  # static text stays before memory
    assert "Work only inside it" not in get_executor_prompt("MEM", "")


def test_system_prompt_gets_workdir(mocker, client):
    mocker.patch.object(main, "current_workdir", return_value=WD)
    rec = Recorder(reply("hi"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    Conversation(client).prompt("hello")
    assert WD in rec.calls[0]["messages"][0]["content"]


# ---------- step limits

def test_max_steps_defaults_and_bounds(mocker):
    mocker.patch.object(cm, "get_settings", return_value={})
    assert (max_steps_for("simple"), max_steps_for("complex"), max_steps_for("override")) == (8, 25, 25)
    mocker.patch.object(cm, "get_settings", return_value={"max_steps_simple": 0, "max_steps_complex": "500"})
    assert (max_steps_for("simple"), max_steps_for("complex")) == (1, 100)


def test_save_settings_validates_step_limits():
    with pytest.raises(ValidationError):
        save_settings(SettingsPayload(max_steps_complex=0))
    with pytest.raises(ValidationError):
        save_settings(SettingsPayload(max_steps_simple=101))


def _looping_tool_calls(n):
    # distinct tool calls so the anti-loop check doesn't fire
    return [tool("update_markdown_memory", {"file_type": "personal", "content": f"note {i}"}) for i in range(n)]


def test_complex_step_limit_pauses_and_offers_continue(mocker, tmp_path):
    client = make_client(mocker, tmp_path, tier="complex")
    mocker.patch.object(main, "max_steps_for", return_value=3)
    mocker.patch.object(main, "update_memory", return_value="saved")
    rec = Recorder(*_looping_tool_calls(3), reply("finished"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    msgs = chat.prompt("big refactor")
    limit = [m for m in msgs if m["type"] == "step_limit"]
    assert len(limit) == 1 and limit[0]["steps"] == 3 and "Continue" in limit[0]["content"]
    assert len(rec.calls) == 3
    # Continue is a normal follow-up; history ends with tool results, which is valid
    chat.prompt("Continue from where you stopped.")
    roles = [m["role"] for m in rec.calls[3]["messages"]]
    assert roles[-2:] == ["tool", "user"]
    chat.close()


def test_simple_step_limit_escalates_instead(mocker, client):
    mocker.patch.object(main, "max_steps_for", side_effect=lambda role: 2 if role == "simple" else 25)
    mocker.patch.object(main, "update_memory", return_value="saved")
    rec = Recorder(*_looping_tool_calls(2), reply("done on complex"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("task")
    assert any(n.startswith("Escalated to complex-model: step limit (2) reached") for n in notes(msgs))
    assert not any(m["type"] == "step_limit" for m in msgs)


# ---------- bash

@pytest.mark.asyncio
async def test_commands_run_in_bash(tmp_path):
    result = await run_shell_command('diff <(echo a) <(echo a) && echo "$BASH_VERSION" | head -c 1', cwd=str(tmp_path))
    assert result["exit_code"] == 0 and result["stdout"].strip().isdigit()
