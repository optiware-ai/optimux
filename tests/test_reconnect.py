"""A request outlives its connection for a while, and a page picks it up again (v0.8).

Before, a dropped connection cancelled the request as soon as the server noticed,
and until it noticed the request ran on with no page: the page reconnected to a
chat without it, and the Push window the model asked for said "a request is
running". Each test runs inside `with client:` so both connections share one
event loop, as they do on the server."""
import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

import optimux_backend.core.workspace as ws
import optimux_backend.main as main
from optimux_backend.core import sessions as chat_store
from tests.ws_helpers import chunk, collect, fake_stream, make_client
from tests.test_routing_session import Recorder, reply

DONE = {"prompt_tokens": 1, "completion_tokens": 1}


@pytest.fixture
def client(mocker, tmp_path, monkeypatch):
    r = tmp_path / "projects"
    (r / "alpha").mkdir(parents=True)
    settings = {"projects_root": str(r), "active_project": "alpha"}
    mocker.patch.object(ws, "get_settings", side_effect=lambda: dict(settings))
    ws._STATUS_CACHE["value"] = None
    c = make_client(mocker, tmp_path)
    mocker.patch.object(main, "active_project", side_effect=lambda: settings["active_project"])
    mocker.patch.object(main, "get_settings", side_effect=lambda: dict(settings))
    mocker.patch.object(main, "save_settings", side_effect=lambda p: settings.update(active_project=p.active_project))
    mocker.patch("litellm.acompletion", side_effect=Recorder(
        fake_stream([chunk(tool_call=("push_project", {"message": "Add the menu"})), chunk(empty=True, usage_obj=DONE)]),
        reply("Done.")))
    main.RUNNING.clear()
    yield c
    main.RUNNING.clear()
    main.BUSY_PROJECTS.clear()


def page(client):
    cm = client.websocket_connect("/ws")
    sock = cm.__enter__()
    sock.send_text(json.dumps({"type": "auth", "token": "test-token"}))
    assert json.loads(sock.receive_text())["type"] == "auth_ok"
    return cm, sock


def until(sock, kind):
    while True:
        m = json.loads(sock.receive_text())
        if m["type"] == kind:
            return m


def wait_for(check, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


def asking(client):
    """A page whose request waits for the Push window the model asked for."""
    cm, sock = page(client)
    sock.send_text(json.dumps({"prompt": "push it", "model": "auto"}))
    return cm, sock, until(sock, "approval_request")


def nobody_serves_it():
    return len(main.RUNNING) == 1 and all(e["owner"] is None for e in main.RUNNING.values())


def test_a_page_checks_the_line(client):
    with client:
        cm, sock = page(client)
        sock.send_text(json.dumps({"type": "ping", "t": 41}))
        assert json.loads(sock.receive_text()) == {"type": "pong", "t": 41}
        cm.__exit__(None, None, None)


def test_a_dropped_connection_does_not_stop_the_request(client):
    with client:
        cm, a, q = asking(client)
        cm.__exit__(None, None, None)                       # the Wi-Fi drops
        assert wait_for(nobody_serves_it), main.RUNNING
        assert main.BUSY_PROJECTS.get("alpha") == 1         # it still counts as running
        cm2, b = page(client)                               # the page reconnects...
        b.send_text(json.dumps({"type": "open_session", "id": "latest"}))
        loaded = until(b, "session_loaded")                 # ...and gets the request back, as far as it went
        assert loaded["running"] is True and loaded["session"]["project"] == "alpha"
        assert loaded["events"][0] == {"type": "prompt", "content": "push it"}
        again = until(b, "approval_request")                # the question it was waiting on, asked again
        assert again["action"] == "push_project" and again["sync"] == q["sync"]
        # one request runs in the project, the one waiting for this window: its pass still opens it
        assert main.BUSY_PROJECTS.get("alpha") == 1 and not main._busy("alpha", "push", q["sync"]["sync_pass"])
        b.send_text(json.dumps({"type": "approval_response", "approved": False}))
        rest = collect(b)                                   # the same reply goes on, to its end
        assert "".join(m.get("content", "") for m in rest if m["type"] == "token") == "Done."
        assert not main.RUNNING and not main.BUSY_PROJECTS
        stored = chat_store.get(loaded["session"]["id"])
        assert stored["events"][0]["type"] == "prompt" and any(e["type"] == "token" for e in stored["events"])
        cm2.__exit__(None, None, None)


def test_with_nobody_back_it_stops_and_nothing_is_lost(client, mocker):
    mocker.patch.object(main, "PAGE_AWAY_GRACE", 0.3)
    with client:
        cm, a, q = asking(client)
        cm.__exit__(None, None, None)
        assert wait_for(lambda: not main.RUNNING), main.RUNNING     # stopped after the wait
        assert not main.BUSY_PROJECTS and not main.SYNC_PASSES
        sid = chat_store.latest("alpha")
        stored = chat_store.get(sid)                                # what was done is in the chat
        assert stored["events"][0] == {"type": "prompt", "content": "push it"}
        notes = [e["content"] for e in stored["events"] if e["type"] == "system"]
        assert any("page was away" in n for n in notes), notes


def test_a_page_that_is_there_keeps_its_request(client):
    with client:
        cm, a, q = asking(client)
        cm2, b = page(client)                               # a second tab opens the same project
        b.send_text(json.dumps({"type": "open_session", "id": "latest"}))
        loaded = until(b, "session_loaded")
        assert not loaded.get("running")
        until(b, "done")
        a.send_text(json.dumps({"type": "approval_response", "approved": False}))
        assert collect(a)[-1]["type"] == "done"             # the first tab still has it
        cm2.__exit__(None, None, None)
        cm.__exit__(None, None, None)


def test_a_page_that_went_silent_is_replaced(client, mocker):
    """The network died without closing the socket: the server still holds it, the
    page already gave up and came back on a new one."""
    mocker.patch.object(main, "QUIET_AFTER", 0.2)
    with client:
        cm, a, q = asking(client)
        a.send_text(json.dumps({"type": "ping", "t": 1}))   # a page that checks its line...
        until(a, "pong")
        time.sleep(0.4)                                     # ...and then says nothing
        cm2, b = page(client)
        b.send_text(json.dumps({"type": "open_session", "id": "latest"}))
        assert until(b, "session_loaded")["running"] is True
        assert until(b, "approval_request")["sync"] == q["sync"]
        with pytest.raises(WebSocketDisconnect):            # the old socket is closed, not left half open
            while True:
                a.receive_text()
        cm.__exit__(None, None, None)
        assert len(main.RUNNING) == 1 and main.BUSY_PROJECTS.get("alpha") == 1
        b.send_text(json.dumps({"type": "approval_response", "approved": False}))
        assert collect(b)[-1]["type"] == "done"
        assert not main.RUNNING and not main.BUSY_PROJECTS
        cm2.__exit__(None, None, None)


def test_a_script_that_never_pings_is_not_replaced(client, mocker):
    mocker.patch.object(main, "QUIET_AFTER", 0.2)
    with client:
        cm, a, q = asking(client)                           # no ping: an API client, or an old page
        time.sleep(0.4)
        cm2, b = page(client)
        b.send_text(json.dumps({"type": "open_session", "id": "latest"}))
        assert not until(b, "session_loaded").get("running")
        until(b, "done")
        a.send_text(json.dumps({"type": "approval_response", "approved": False}))
        assert collect(a)[-1]["type"] == "done"
        cm2.__exit__(None, None, None)
        cm.__exit__(None, None, None)


def test_another_chat_is_not_handed_the_request(client):
    with client:
        cm, a, q = asking(client)
        cm.__exit__(None, None, None)
        assert wait_for(nobody_serves_it)
        cm2, b = page(client)
        b.send_text(json.dumps({"type": "open_session", "id": "no-such-chat"}))
        assert not until(b, "session_loaded").get("running")
        until(b, "done")
        assert nobody_serves_it()                           # still waiting for its own page
        cm2.__exit__(None, None, None)


def test_the_model_host_to_check():
    assert main.model_host("anthropic/claude-sonnet-5") == "api.anthropic.com"
    assert main.model_host("gemini/gemini-3-flash") == "generativelanguage.googleapis.com"
    assert main.model_host("gpt-5-nano") == "api.openai.com"
    assert main.model_host("some-unknown/model") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("reached,net", [(True, "ok"), (False, "down")])
async def test_a_silent_model_call_says_whether_the_provider_answers(mocker, reached, net):
    sent = []

    async def fake_send(websocket, kind, content=None, **extra):
        sent.append({"type": kind, **extra})

    mocker.patch.object(main, "send", side_effect=fake_send)
    mocker.patch.object(main, "SILENCE_CHECK", 0.05)
    mocker.patch.object(main, "host_answers", return_value=reached)
    import asyncio
    heard = {"at": time.monotonic()}
    task = asyncio.create_task(main.watch_silence(None, "anthropic/claude-sonnet-5", heard))
    await asyncio.sleep(0.2)
    task.cancel()
    assert sent and sent[0] == {"type": "status", "stage": "model", "model": "anthropic/claude-sonnet-5",
                                "net": net, "host": "api.anthropic.com", "ago": sent[0]["ago"]}
    assert len(sent) == 1 or all(m["net"] == net for m in sent)

