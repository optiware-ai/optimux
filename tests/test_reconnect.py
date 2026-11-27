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



# ---- leaving on purpose (detach): optimux-tui's "leave it running"

def test_a_window_that_leaves_on_purpose_is_waited_for_longer(client, mocker):
    mocker.patch.object(main, "PAGE_AWAY_GRACE", 0.2)
    mocker.patch.object(main, "detach_grace", return_value=30)
    with client:
        cm, a, q = asking(client)
        a.send_text(json.dumps({"type": "detach"}))
        assert until(a, "detached") == {"type": "detached", "running": True, "wait": 30}
        cm.__exit__(None, None, None)
        assert wait_for(nobody_serves_it), main.RUNNING
        time.sleep(0.6)                                         # three times the ordinary wait
        assert nobody_serves_it(), "still running for the window that left on purpose"
        cm2, b = page(client)
        b.send_text(json.dumps({"type": "open_session", "id": "latest"}))
        assert until(b, "session_loaded")["running"] is True
        until(b, "approval_request")
        entry = next(iter(main.RUNNING.values()))
        assert entry["session"]["detached"] is False            # picked up: the next drop is an ordinary one
        b.send_text(json.dumps({"type": "approval_response", "approved": False}))
        collect(b)
        assert not main.RUNNING
        cm2.__exit__(None, None, None)


def test_a_request_left_on_purpose_still_stops_after_its_wait(client, mocker):
    mocker.patch.object(main, "PAGE_AWAY_GRACE", 30)
    mocker.patch.object(main, "detach_grace", return_value=0.3)
    with client:
        cm, a, q = asking(client)
        a.send_text(json.dumps({"type": "detach"}))
        until(a, "detached")
        cm.__exit__(None, None, None)
        assert wait_for(lambda: not main.RUNNING), main.RUNNING     # stopped after detach_grace, not 30 s
        assert not main.BUSY_PROJECTS


def test_leaving_with_nothing_running_keeps_nothing(client):
    with client:
        cm, sock = page(client)
        sock.send_text(json.dumps({"type": "detach"}))
        assert json.loads(sock.receive_text()) == {"type": "detached", "running": False, "wait": 0}
        cm.__exit__(None, None, None)


def test_the_chat_list_says_which_chats_wait_and_have_no_window(client):
    with client:
        cm, a, q = asking(client)
        sid = chat_store.latest("alpha")
        auth = {"Authorization": "Bearer test-token"}
        row = next(r for r in client.get("/api/sessions", headers=auth).json() if r["id"] == sid)
        assert (row["state"], row["away"]) == ("waiting", False)      # a question is open, this window has it
        cm.__exit__(None, None, None)
        assert wait_for(nobody_serves_it)
        row = next(r for r in client.get("/api/sessions", headers=auth).json() if r["id"] == sid)
        assert (row["state"], row["away"]) == ("waiting", True)
        cm2, b = page(client)
        b.send_text(json.dumps({"type": "open_session", "id": sid}))
        until(b, "approval_request")
        b.send_text(json.dumps({"type": "approval_response", "approved": False}))
        collect(b)
        row = next(r for r in client.get("/api/sessions", headers=auth).json() if r["id"] == sid)
        assert "state" not in row and "away" not in row                 # finished: no state
        cm2.__exit__(None, None, None)


def test_the_wait_comes_from_the_setting(mocker):
    mocker.patch.object(main, "get_settings", return_value={"detach_wait_minutes": 45})
    assert main.detach_grace() == 45 * 60
    mocker.patch.object(main, "get_settings", return_value={})
    assert main.detach_grace() == 30 * 60


# ---- a page gets back only the request of the chat it opens

def test_opening_the_newest_chat_does_not_take_another_chats_request(client):
    # A request left without a window in an older chat (optimux-tui left it running to
    # open a new chat): a page opening "latest" shows the newest chat, not that one
    with client:
        cm, a, q = asking(client)
        old = chat_store.latest("alpha")
        cm.__exit__(None, None, None)
        assert wait_for(nobody_serves_it)
        newer = chat_store.create("alpha", "a newer chat")
        cm2, b = page(client)
        b.send_text(json.dumps({"type": "open_session", "id": "latest"}))
        loaded = until(b, "session_loaded")
        assert loaded["session"]["id"] == newer and not loaded.get("running")
        assert nobody_serves_it(), "the older chat's request is still free, for its own chat"
        b.send_text(json.dumps({"type": "open_session", "id": old}))
        assert until(b, "session_loaded")["running"] is True       # opened by its id: it comes back
        until(b, "approval_request")
        b.send_text(json.dumps({"type": "approval_response", "approved": False}))
        collect(b)
        cm2.__exit__(None, None, None)


def test_the_logo_gets_the_last_chats_request_in_its_project(client, mocker):
    told = mocker.patch.object(main, "announce_active_project", new=mocker.AsyncMock())
    with client:
        cm, a, q = asking(client)                                   # running in alpha
        sid = chat_store.latest("alpha")
        cm.__exit__(None, None, None)
        assert wait_for(nobody_serves_it)
        main.switch_active_project("beta")                           # meanwhile another window moved on
        cm2, b = page(client)
        b.send_text(json.dumps({"type": "open_session", "id": "last"}))
        loaded = until(b, "session_loaded")
        assert loaded["session"]["id"] == sid and loaded["running"] is True
        assert main.active_project() == "alpha"                      # the chat's project is active again
        told.assert_awaited_with("alpha")                            # and the other windows are told
        until(b, "approval_request")
        b.send_text(json.dumps({"type": "approval_response", "approved": False}))
        collect(b)
        cm2.__exit__(None, None, None)
