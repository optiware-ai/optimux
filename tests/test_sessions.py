"""Stored chat sessions: recording, saving, reopening, managing, surviving restarts."""
import json

import pytest

import optimux_backend.main as main
from optimux_backend.core import sessions, store, usage
from tests.ws_helpers import Conversation, collect, make_client
from tests.test_routing_session import Recorder, reply

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(mocker, tmp_path):
    c = make_client(mocker, tmp_path)
    mocker.patch.object(main, "active_project", return_value="alpha")
    return c


def test_recorder_merges_streamed_chunks_and_skips_transient():
    r = sessions.Recorder()
    for p in ({"type": "prompt", "content": "hi"}, {"type": "token", "content": "Hel"}, {"type": "token", "content": "lo"},
              {"type": "approval_request", "command": "ls"}, {"type": "thinking", "content": "a"},
              {"type": "thinking", "content": "b"}, {"type": "token", "content": "!"}, {"type": "done"}):
        r.add(p)
    assert r.pending == [{"type": "prompt", "content": "hi"}, {"type": "token", "content": "Hello"},
                         {"type": "thinking", "content": "ab"}, {"type": "token", "content": "!"}]


def test_chat_is_stored_with_events_state_and_cost(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("Hello there")))
    chat = Conversation(client)
    chat.prompt("hello world")
    chat.close()
    [row] = sessions.list_sessions("alpha")
    assert row["title"] == "hello world" and row["turns"] == 1 and row["cost"] == 0.0
    data = sessions.get(row["id"])
    kinds = [e["type"] for e in data["events"]]
    assert kinds[0] == "prompt" and "token" in kinds and "usage" in kinds and "done" not in kinds
    assert "".join(e["content"] for e in data["events"] if e["type"] == "token") == "Hello there"
    assert [m["role"] for m in data["history"]] == ["user", "assistant"]
    # usage rows belong to the stored session
    assert {e["session"] for e in usage.read_entries()} == {row["id"]}


def test_reopen_after_restart_restores_view_and_context(mocker, client):
    rec = Recorder(reply("first answer"), reply("second answer"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("first question")
    chat.close()
    store._ready_for = None  # as after a server restart
    chat = Conversation(client)
    loaded = chat.send({"type": "open_session", "id": "latest"})
    msg = next(m for m in loaded if m["type"] == "session_loaded")
    assert msg["session"]["title"] == "first question"
    assert any(e["type"] == "prompt" for e in msg["events"])
    chat.prompt("follow-up")
    chat.close()
    sent = rec.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert len(sessions.list_sessions("alpha")) == 1                        # same session continued
    assert sessions.list_sessions("alpha")[0]["turns"] == 2


def test_open_latest_with_no_sessions(client):
    chat = Conversation(client)
    msg = next(m for m in chat.send({"type": "open_session", "id": "latest"}) if m["type"] == "session_loaded")
    assert msg["session"] is None
    chat.close()


def test_new_chat_starts_a_new_session(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    chat = Conversation(client)
    chat.prompt("one")
    chat.send({"type": "new_chat"})
    chat.prompt("two")
    chat.close()
    assert [s["title"] for s in sessions.list_sessions("alpha")] == ["two", "one"]


def test_escalate_is_recorded(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("weak"), reply("strong")))
    chat = Conversation(client)
    chat.prompt("q")
    chat.send({"type": "escalate"})
    chat.close()
    data = sessions.get(sessions.latest("alpha"))
    assert [e["type"] for e in data["events"]].count("escalate") == 1 and data["turns"] == 2


def test_opening_a_session_of_another_project_switches_project(mocker, client, tmp_path):
    (tmp_path / "ws" / "beta").mkdir(parents=True)
    sid = sessions.create("beta", "old chat")
    saved = mocker.patch.object(main, "save_settings")
    mocker.patch.object(main, "get_settings", return_value={"projects_root": str(tmp_path / "ws"), "active_project": "alpha"})
    chat = Conversation(client)
    loaded = next(m for m in chat.send({"type": "open_session", "id": sid}) if m["type"] == "session_loaded")
    chat.close()
    assert saved.call_args.args[0].active_project == "beta"
    assert not loaded.get("read_only")


def _age(sid, seconds_ago):
    conn = store.connect()
    try:
        conn.execute("UPDATE sessions SET updated = strftime('%s','now') - ? WHERE id = ?", (seconds_ago, sid))
        conn.commit()
    finally:
        conn.close()


def test_logo_opens_the_newest_chat_of_any_project(mocker, client, tmp_path):
    """The logo ("last"): the most recent chat in any project, skipping chats whose
    project folder is gone, and the active project follows it."""
    (tmp_path / "ws" / "alpha").mkdir(parents=True)
    (tmp_path / "ws" / "beta").mkdir(parents=True)
    _age(sessions.create("alpha", "old alpha chat"), 300)
    beta = sessions.create("beta", "beta chat")
    _age(beta, 100)
    _age(sessions.create("gone", "chat of a deleted project"), 10)          # newest, but its folder is gone
    mocker.patch.object(main.workspace, "list_archived", return_value=[])
    saved = mocker.patch.object(main, "save_settings")
    mocker.patch.object(main, "get_settings", return_value={"projects_root": str(tmp_path / "ws"), "active_project": "alpha"})
    chat = Conversation(client)
    loaded = next(m for m in chat.send({"type": "open_session", "id": "last"}) if m["type"] == "session_loaded")
    chat.close()
    assert loaded["session"]["id"] == beta and not loaded.get("read_only")
    assert loaded["active_project_changed"] is True
    assert saved.call_args.args[0].active_project == "beta"


def test_logo_with_no_chats_goes_to_the_workspace(mocker, client, tmp_path):
    saved = mocker.patch.object(main, "save_settings")
    mocker.patch.object(main, "get_settings", return_value={"projects_root": str(tmp_path / "ws"), "active_project": "alpha"})
    chat = Conversation(client)
    loaded = next(m for m in chat.send({"type": "open_session", "id": "last"}) if m["type"] == "session_loaded")
    chat.close()
    assert loaded["session"] is None and loaded["active_project_changed"] is True
    assert saved.call_args.args[0].active_project == ""


def test_logo_already_in_the_newest_chats_project_switches_nothing(mocker, client, tmp_path):
    (tmp_path / "ws" / "alpha").mkdir(parents=True)
    sid = sessions.create("alpha", "alpha chat")
    saved = mocker.patch.object(main, "save_settings")
    mocker.patch.object(main, "get_settings", return_value={"projects_root": str(tmp_path / "ws"), "active_project": "alpha"})
    chat = Conversation(client)
    loaded = next(m for m in chat.send({"type": "open_session", "id": "last"}) if m["type"] == "session_loaded")
    chat.close()
    assert loaded["session"]["id"] == sid and loaded["active_project_changed"] is False
    saved.assert_not_called()


def test_frontend_logo_and_name_are_links_home():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "index.html")).read()
    js = open(os.path.join(root, "web", "app.js")).read()
    assert html.count('@click.prevent="goHome"') == 2          # chat header and Dashboard sidebar
    assert "openSession('last')" in js and "if (data.active_project_changed) fetchConfig();" in js


@pytest.mark.parametrize("archived, reason", [(True, "archived"), (False, "missing")])
def test_chat_of_a_removed_project_opens_read_only(mocker, client, tmp_path, archived, reason):
    """fix38: the project's folder is gone, so the chat is shown but not continued,
    and the active project doesn't change to a folder that doesn't exist."""
    (tmp_path / "ws" / "alpha").mkdir(parents=True)
    sid = sessions.create("beta", "chat of a removed project")
    mocker.patch.object(main.workspace, "list_archived",
                        return_value=[{"folder": "beta-20261001-120000", "name": "beta", "archived": 0}] if archived else [])
    saved = mocker.patch.object(main, "save_settings")
    mocker.patch.object(main, "get_settings", return_value={"projects_root": str(tmp_path / "ws"), "active_project": "alpha"})
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("fresh answer")))
    chat = Conversation(client)
    loaded = next(m for m in chat.send({"type": "open_session", "id": sid}) if m["type"] == "session_loaded")
    assert loaded["read_only"] is True and loaded["reason"] == reason
    assert loaded["session"]["id"] == sid and loaded["session"]["project"] == "beta"
    saved.assert_not_called()                                  # still in alpha
    chat.prompt("next question")                               # starts a new chat in alpha
    chat.close()
    assert sessions.get(sid)["turns"] == 0                     # the old chat wasn't continued
    assert [s["title"] for s in sessions.list_sessions("alpha")] == ["next question"]


def test_frontend_locks_the_project_selector_and_read_only_chats():
    """fix38: no project switch while a reply runs; a read-only chat can't be continued."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "index.html")).read()
    js = open(os.path.join(root, "web", "app.js")).read()
    select = html[html.index('<select v-model="appSettings.active_project"'):]
    select = select[:select.index(">")]
    assert ':disabled="isGenerating || pendingApproval"' in select
    assert '|| !!readOnlyChat" rows="1"></textarea>' in html
    assert 'v-if="readOnlyChat"' in html and '@click="leaveReadOnly"' in html
    assert "if (!userInput.value.trim() || readOnlyChat.value) return;" in js


def test_rename_archive_delete(mocker, client):
    sid = sessions.create("alpha", "my chat")
    usage.insert({"session": sid, "project": "alpha", "cost": 0.5})
    assert client.post(f"/api/sessions/{sid}/rename", json={"title": "Renamed"}, headers=H).status_code == 200
    assert client.get("/api/sessions", headers=H).json()[0]["title"] == "Renamed"
    assert client.get("/api/sessions", headers=H).json()[0]["cost"] == 0.5
    client.post(f"/api/sessions/{sid}/archive", json={"archived": True}, headers=H)
    assert client.get("/api/sessions", headers=H).json() == []
    assert client.get("/api/sessions?archived=true", headers=H).json()[0]["id"] == sid
    assert sessions.latest("alpha") is None                                  # archived isn't reopened
    assert client.post(f"/api/sessions/{sid}/delete", json={"confirm": "wrong"}, headers=H).status_code == 400
    assert client.post(f"/api/sessions/{sid}/delete", json={"confirm": "Renamed"}, headers=H).status_code == 200
    assert sessions.get(sid) is None
    assert usage.summary()["all_time"]["total"]["cost"] == 0.5               # cost records are kept
    assert client.post(f"/api/sessions/{sid}/rename", json={"title": "x"}, headers=H).status_code == 404
    assert client.get("/api/sessions").status_code == 401


def test_list_filters(client):
    sessions.create("alpha", "fix the parser")
    sessions.create("beta", "write docs")
    assert [s["title"] for s in client.get("/api/sessions", headers=H).json()] == ["fix the parser"]
    assert len(client.get("/api/sessions?all_projects=true", headers=H).json()) == 2
    assert [s["title"] for s in client.get("/api/sessions?all_projects=true&q=docs", headers=H).json()] == ["write docs"]


def test_deleted_while_open_starts_fresh_session(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    chat = Conversation(client)
    chat.prompt("first")
    sid = sessions.latest("alpha")
    sessions.delete(sid)
    chat.prompt("second")
    chat.close()
    [row] = sessions.list_sessions("alpha")
    assert row["id"] != sid and row["title"] == "second"


def test_startup_imports_before_backup(monkeypatch, tmp_path):
    with open(usage.USAGE_FILE, "w") as f:
        f.write(json.dumps({"ts": 1.0, "role": "simple", "model": "m", "prompt_tokens": 1, "cost": 0.1}) + "\n")
    usage._imported_for = None
    usage.ensure_ready()
    path = store.backup()
    import sqlite3
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 1
    conn.close()


def test_first_prompt_announces_the_new_session(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    chat = Conversation(client)
    first = [m for m in chat.prompt("plan the release") if m["type"] == "session_started"]
    second = [m for m in chat.prompt("next") if m["type"] == "session_started"]
    chat.close()
    assert first and first[0]["session"]["title"] == "plan the release" and second == []
    assert all(e["type"] != "session_started" for e in sessions.get(first[0]["session"]["id"])["events"])
