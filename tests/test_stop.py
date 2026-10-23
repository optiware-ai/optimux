"""Stop button: cancelling a running request from the UI."""
import asyncio
import json
import os
import time

import pytest

import optimux_backend.main as main
from optimux_backend.core.executor import run_shell_command
from tests.ws_helpers import Conversation, chunk, collect, make_client

DONE = {"prompt_tokens": 10, "completion_tokens": 2}


async def hanging_stream(first_text=None, tool_call=None):
    """Yields one chunk, then hangs like a slow model until cancelled."""
    yield chunk(first_text, tool_call=tool_call)
    await asyncio.sleep(60)
    yield chunk(empty=True, usage_obj=DONE)


async def quick(text):
    yield chunk(text)
    yield chunk(empty=True, usage_obj=DONE)


def read_until(ws, kind):
    msgs = []
    while True:
        m = json.loads(ws.receive_text())
        msgs.append(m)
        if m["type"] == kind:
            return msgs


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_stop_during_streaming(mocker, client):
    calls = []

    def fake(**kw):
        calls.append([dict(m) for m in kw["messages"]])
        return hanging_stream("Partial ans") if len(calls) == 1 else quick("fresh")

    mocker.patch("litellm.acompletion", side_effect=fake)
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "explain everything", "model": "auto"}))
    read_until(chat.ws, "token")

    started = time.monotonic()
    chat.ws.send_text(json.dumps({"type": "stop"}))
    msgs = collect(chat.ws)
    assert time.monotonic() - started < 2
    kinds = [m["type"] for m in msgs]
    assert "stopped" in kinds and kinds[-1] == "done"
    # the cancelled call is still metered, as an estimate
    assert any(m["type"] == "usage" and m.get("estimated") for m in msgs)

    # the chat continues, and the model sees what happened
    chat.prompt("go on")
    history = calls[1]
    assert history[-1] == {"role": "user", "content": "go on"}
    assert history[-2]["role"] == "assistant"
    assert history[-2]["content"] == "Partial ans\n\n" + main.STOPPED_NOTE
    chat.close()


def test_stop_during_approval_never_runs_the_command(mocker, client):
    run = mocker.patch.object(main, "run_shell_command")
    calls = []

    def fake(**kw):
        calls.append([dict(m) for m in kw["messages"]])
        if len(calls) == 1:
            return quick_tool()
        return quick("ok")

    async def quick_tool():
        yield chunk(tool_call=("execute_bash", {"command": "rm -rf build"}))
        yield chunk(empty=True, usage_obj=DONE)

    mocker.patch("litellm.acompletion", side_effect=fake)
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "clean the build", "model": "auto"}))
    read_until(chat.ws, "approval_request")
    chat.ws.send_text(json.dumps({"type": "stop"}))
    msgs = collect(chat.ws)
    assert "stopped" in [m["type"] for m in msgs]
    run.assert_not_called()

    # a late approval click is ignored
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    chat.prompt("never mind")
    run.assert_not_called()
    roles = [m["role"] for m in calls[1]]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert calls[1][3]["content"].startswith("Cancelled")
    assert calls[1][4]["content"] == main.STOPPED_NOTE
    chat.close()


def test_approval_still_works_with_reader_loop(mocker, client):
    run = mocker.patch.object(main, "run_shell_command", return_value={"stdout": "a.py\n", "stderr": "", "exit_code": 0})
    responses = iter([None, "Files: a.py"])

    async def tool_then_text(text):
        if text is None:
            yield chunk(tool_call=("execute_bash", {"command": "ls"}))
        else:
            yield chunk(text)
        yield chunk(empty=True, usage_obj=DONE)

    mocker.patch("litellm.acompletion", side_effect=lambda **kw: tool_then_text(next(responses)))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "list files", "model": "auto"}))
    read_until(chat.ws, "approval_request")
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    msgs = collect(chat.ws)
    run.assert_called_once()
    assert run.call_args.args == ("ls",) and run.call_args.kwargs["timeout"] is None
    assert "".join(m["content"] for m in msgs if m["type"] == "token") == "Files: a.py"
    chat.close()


def test_stop_when_idle_just_releases_ui(client):
    chat = Conversation(client)
    msgs = chat.send({"type": "stop"})
    assert [m["type"] for m in msgs] == ["done"]
    chat.close()


def test_second_prompt_while_busy_is_refused(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: hanging_stream("working"))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "long task", "model": "auto"}))
    read_until(chat.ws, "token")
    chat.ws.send_text(json.dumps({"prompt": "another", "model": "auto"}))
    msgs = read_until(chat.ws, "system")
    assert "Still working" in msgs[-1]["content"]
    chat.ws.send_text(json.dumps({"type": "stop"}))
    collect(chat.ws)
    chat.close()


def test_close_stopped_turn_before_any_output():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    main.close_stopped_turn(msgs, "")
    assert msgs[-1] == {"role": "assistant", "content": main.STOPPED_NOTE}


@pytest.mark.asyncio
async def test_cancel_kills_the_running_command(tmp_path):
    pid_file = tmp_path / "pid"
    task = asyncio.create_task(run_shell_command(f"sleep 30 & echo $! > {pid_file}; wait", cwd=str(tmp_path)))
    for _ in range(50):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().split()[2]
    except FileNotFoundError:
        state = "gone"
    assert state in ("gone", "Z")


@pytest.mark.asyncio
async def test_stop_while_the_command_starts_still_kills_it(tmp_path, monkeypatch):
    """Stop pressed after the command started but before its handle came back
    (the start is slow on some machines) must not leave it running."""
    real = asyncio.create_subprocess_shell

    async def slow_start(*args, **kwargs):
        process = await real(*args, **kwargs)
        await asyncio.sleep(0.5)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_shell", slow_start)
    pid_file = tmp_path / "pid"
    task = asyncio.create_task(run_shell_command(f"sleep 30 & echo $! > {pid_file}; wait", cwd=str(tmp_path)))
    for _ in range(50):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pid_file.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().split()[2]
    except FileNotFoundError:
        state = "gone"
    assert state in ("gone", "Z")


def test_keep_waiting_at_the_time_limit(mocker, client, tmp_path):
    """IM-03b end to end: the limit is shown before approval and on the running
    command; at the limit the page is asked, and "more" lets it finish."""
    import json as _json
    mocker.patch.object(main, "command_timeout_for", return_value=1)
    mocker.patch("optimux_backend.core.executor.command_timeout_for", return_value=1)
    mocker.patch.object(main, "current_workdir", return_value=str(tmp_path))
    mocker.patch("optimux_backend.core.executor.get_active_cwd", return_value=str(tmp_path))
    from tests.test_routing_session import Recorder, reply, tool_reply
    rec = Recorder(tool_reply("execute_bash", {"command": "sleep 1.6; echo done"}), reply("Finished."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.ws.send_text(_json.dumps({"prompt": "build it", "model": "auto"}))
    while True:
        m = _json.loads(chat.ws.receive_text())
        if m["type"] == "approval_request":
            break
    assert m["time_limit"] == 1
    chat.ws.send_text(_json.dumps({"type": "approval_response", "approved": True}))
    seen = []
    while True:
        m = _json.loads(chat.ws.receive_text())
        seen.append(m)
        if m["type"] == "command_limit":
            assert m["limit"] == 1 and m["command"] == "sleep 1.6; echo done"
            chat.ws.send_text(_json.dumps({"type": "limit_response", "choice": "more"}))
        if m["type"] == "done":
            break
    chat.close()
    start = [x for x in seen if x["type"] == "command_start"][0]
    out = [x for x in seen if x["type"] == "command_output"][0]
    assert start["time_limit"] == 1 and out["output"] == "done\n" and out["exit_code"] == 0
    assert any(x["type"] == "command_limit_done" for x in seen)
    assert any(x["type"] == "system" and "Waiting 1s more" in x["content"] for x in seen)


def test_a_time_limit_chosen_in_the_approval_box(mocker, client, tmp_path):
    import json as _json
    run = mocker.patch.object(main, "run_shell_command", return_value={"stdout": "ok\n", "stderr": "", "exit_code": 0})
    from tests.test_routing_session import Recorder, reply, tool_reply
    rec = Recorder(tool_reply("execute_bash", {"command": "make"}), reply("ok"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.ws.send_text(_json.dumps({"prompt": "build", "model": "auto"}))
    while _json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(_json.dumps({"type": "approval_response", "approved": True, "time_limit": 0}))
    from tests.ws_helpers import collect
    msgs = collect(chat.ws)
    chat.close()
    assert run.call_args.kwargs["timeout"] == 0
    assert [m for m in msgs if m["type"] == "command_start"][0]["time_limit"] == 0
    assert main.chosen_time_limit({"time_limit": 99}) is None and main.chosen_time_limit({"time_limit": True}) is None
    assert main.chosen_time_limit({"time_limit": 600}) == 600 and main.chosen_time_limit({}) is None
