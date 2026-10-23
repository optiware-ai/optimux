"""A broken sandbox is told before a command needs it; small executor rules."""
import json

import optimux_backend.main as main
from optimux_backend.core import agents
from tests.ws_helpers import make_client

BROKEN = {"installed": True, "works": False, "error": "bwrap: Can't mount proc", "path": "/usr/bin/bwrap",
          "version": "0.9", "required": True}


def connect(client):
    cm = client.websocket_connect("/ws")
    ws = cm.__enter__()
    ws.send_text(json.dumps({"type": "auth", "token": "test-token"}))
    first = json.loads(ws.receive_text())
    return cm, ws, first


def test_a_broken_sandbox_is_told_right_after_sign_in(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    mocker.patch.object(main.sandbox_lib, "status", return_value=BROKEN)
    mocker.patch.object(main.sandbox_lib, "enabled", return_value=True)
    cm, ws, first = connect(client)
    second = json.loads(ws.receive_text())
    cm.__exit__(None, None, None)
    assert first["type"] == "auth_ok"
    assert second == {"type": "sandbox_problem", "content": "bwrap: Can't mount proc"}


def test_nothing_is_told_when_it_works_or_is_off(mocker, tmp_path):
    mocker.patch.object(main.sandbox_lib, "status", return_value={**BROKEN, "works": True})
    mocker.patch.object(main.sandbox_lib, "enabled", return_value=True)
    assert main.sandbox_problem() == ""
    mocker.patch.object(main.sandbox_lib, "status", return_value=BROKEN)
    mocker.patch.object(main.sandbox_lib, "enabled", return_value=False)
    assert main.sandbox_problem() == ""


def test_switching_to_the_active_project_does_nothing(mocker):
    mocker.patch.object(main, "active_project", return_value="makerhub")
    mocker.patch.object(main.workspace, "check_switch", return_value="makerhub")
    switch = mocker.patch.object(main, "set_active_project")
    sent = mocker.patch.object(main, "send")
    import asyncio
    result = asyncio.run(main.run_workspace_tool(None, {}, "switch_project", '{"name": "makerhub"}', "t1"))
    assert result == {"ok": True, "active_project": "makerhub", "note": "Already active; nothing changed."}
    switch.assert_not_called() and sent.assert_not_called()


def test_executor_rules_against_checking_and_wandering():
    prompt = agents.get_executor_prompt("", workdir="/w/alpha")
    assert "report everything they did; don't check their work with shell commands" in prompt
    assert "If a command is refused or can't run, tell the user in one or two sentences and stop." in prompt
    assert "offer it in one sentence at the end" in prompt


# ---- IM-07 (v0.5.2): a sandboxed command without network fails with a clear hint

import pytest  # noqa: E402

from optimux_backend.core import executor  # noqa: E402


@pytest.mark.parametrize("output", [
    "curl: (6) Could not resolve host: example.com",
    "fatal: unable to access 'https://github.com/x/y.git/': Could not resolve host: github.com",
    "WARNING: Retrying ... Failed to establish a new connection: [Errno -3] Temporary failure in name resolution",
    "npm ERR! request to https://registry.npmjs.org/x failed, reason: getaddrinfo EAI_AGAIN registry.npmjs.org",
    "wget: unable to resolve host address 'example.com'",
    "socket.gaierror: [Errno -2] Name or service not known",
    "connect: Network is unreachable",
])
def test_network_failures_get_the_hint(output):
    assert executor.network_hint(output, 6, sandboxed=True, network=False) == executor.NETWORK_HINT


@pytest.mark.parametrize("output,code,sandboxed,network", [
    ("curl: (6) Could not resolve host: example.com", 6, True, True),     # the network is on: a real DNS problem
    ("curl: (6) Could not resolve host: example.com", 6, False, False),   # not in the sandbox
    ("Could not resolve host", 0, True, False),                           # it worked
    ("SyntaxError: invalid syntax", 1, True, False),                      # another failure
])
def test_no_hint_otherwise(output, code, sandboxed, network):
    assert executor.network_hint(output, code, sandboxed, network) == ""


@pytest.mark.asyncio
async def test_the_hint_reaches_the_result(mocker, tmp_path):
    """As if sandboxed without network: the command's own error, then the hint."""
    settings = {"sandbox": True, "sandbox_network": False}
    mocker.patch.object(executor, "get_settings", return_value=settings)
    mocker.patch.object(executor.sandbox, "enabled", return_value=True)
    mocker.patch.object(executor.sandbox, "status", return_value={"works": True, "path": "/usr/bin/bwrap", "error": ""})
    mocker.patch.object(executor.sandbox, "build_argv", side_effect=lambda cmd, *a, **k: ["bash", "-c", cmd])
    r = await executor.run_shell_command("echo 'curl: (6) Could not resolve host: x.org' >&2; exit 6", cwd=str(tmp_path))
    assert r["exit_code"] == 6 and r["stderr"].startswith("curl: (6) Could not resolve host")
    assert r["stderr"].endswith(executor.NETWORK_HINT) and r["hint"] == executor.NETWORK_HINT
