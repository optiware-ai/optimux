"""Team version, step 5a: the server side of the member chat.
A member's /ws connection has its own project and owner, can only use the projects they are on,
sees only their own chats, and gets no Workspace tools and none of the admin's personal memory."""
import asyncio
import contextlib
import contextvars
import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from optimux_backend.core import config_manager as cm, memory_manager as mm, sessions, team, workspace
from tests.test_team_users import ORIGIN, app_client, home, invite_and_activate, solo, teamed  # noqa: F401


@pytest.fixture
def ws_root(home, monkeypatch):
    root = home / "workspace"
    for name in ("alpha", "beta", "gamma"):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(workspace, "projects_root", lambda: str(root))
    # the install's own (the admin's) active project is "gamma"
    path = home / "settings.json"
    path.write_text(json.dumps({"projects_root": str(root), "active_project": "gamma"}))
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(path))
    return root


@pytest.fixture
def seen(app_client, monkeypatch):
    """handle_prompt is replaced: it records where and for whom it ran, takes `delay` seconds, and answers."""
    class Calls(list):
        state = {"delay": 0.0}

    calls = Calls()
    state = calls.state

    async def fake(websocket, session, prompt, model_override):
        if not session["chat_id"]:
            session["chat_id"] = sessions.create(app_client.active_project(), prompt)
        calls.append({"prompt": prompt, "project": app_client.active_project(), "user": team.current_user_id()})
        await asyncio.sleep(state["delay"])
        await app_client.send(websocket, "assistant", f"echo {prompt}")

    monkeypatch.setattr(app_client, "handle_prompt", fake)
    monkeypatch.setattr(app_client, "sandbox_problem", lambda: "")
    return calls


def put(admin, project, uid, member=True):
    assert admin.post(f"/api/team/projects/{project}/members/{uid}", json={"member": member}, headers=ORIGIN).status_code == 200


@pytest.fixture
def crew(teamed, ws_root):
    """anna on alpha and beta, bob on beta, carol on nothing."""
    out = {"admin": teamed}
    for name in ("anna", "bob", "carol"):
        client, user = invite_and_activate(teamed, name)
        out[name] = (client, user["id"])
    put(teamed, "alpha", out["anna"][1]); put(teamed, "beta", out["anna"][1]); put(teamed, "beta", out["bob"][1])
    return out


def talk(ws, payload=None):
    """Send a message and read until `done`."""
    if payload is not None:
        ws.send_text(json.dumps(payload))
    msgs = []
    while True:
        m = json.loads(ws.receive_text())
        msgs.append(m)
        if m["type"] == "done":
            return msgs


@contextlib.contextmanager
def opened(client):
    """A member's connection, past auth_ok, with the member_state it starts with."""
    with client.websocket_connect("/ws") as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "auth_ok", first
        yield ws, json.loads(ws.receive_text())


def test_a_member_connects_and_is_told_their_projects(crew, seen):
    with opened(crew["anna"][0]) as (ws, state):
        assert state["type"] == "member_state" and state["projects"] == ["alpha", "beta"] and state["project"] == "alpha"


def test_a_member_with_no_project_is_told_and_closed(crew, seen):
    with pytest.raises(WebSocketDisconnect) as e:
        with crew["carol"][0].websocket_connect("/ws") as ws:
            assert json.loads(ws.receive_text())["type"] == "no_projects"
            ws.receive_text()
    assert e.value.code == 4403


def test_a_prompt_runs_in_the_members_project_as_the_member(crew, seen, app_client):
    with opened(crew["anna"][0]) as (ws, _):
        msgs = talk(ws, {"prompt": "hi"})
    assert any(m["type"] == "assistant" for m in msgs)
    assert seen == [{"prompt": "hi", "project": "alpha", "user": crew["anna"][1]}]
    assert cm.get_settings()["active_project"] == "gamma", "the install's own project is untouched"


def test_switching_is_limited_to_the_members_projects(crew, seen):
    with opened(crew["anna"][0]) as (ws, _):
        ws.send_text(json.dumps({"type": "switch_project", "project": "beta"}))
        while True:
            m = json.loads(ws.receive_text())
            if m["type"] == "member_state":
                assert m["project"] == "beta"
                break
        talk(ws, {"prompt": "in beta"})
        ws.send_text(json.dumps({"type": "switch_project", "project": "gamma"}))      # not anna's
        refused = json.loads(ws.receive_text())
        assert refused["type"] == "system" and "not" in refused["content"].lower()
        talk(ws, {"prompt": "still beta"})
    assert [(c["prompt"], c["project"]) for c in seen] == [("in beta", "beta"), ("still beta", "beta")]


def test_two_members_at_once_each_keep_their_project(crew, seen):
    seen.state["delay"] = 0.3
    with opened(crew["anna"][0]) as (wa, _), opened(crew["bob"][0]) as (wb, _):      # alpha, beta
        wa.send_text(json.dumps({"prompt": "from anna"}))
        wb.send_text(json.dumps({"prompt": "from bob"}))
        talk(wa); talk(wb)
    got = {c["prompt"]: (c["project"], c["user"]) for c in seen}
    assert got == {"from anna": ("alpha", crew["anna"][1]), "from bob": ("beta", crew["bob"][1])}


def test_one_run_per_project_at_a_time(crew, seen):
    seen.state["delay"] = 0.6
    with opened(crew["anna"][0]) as (wa, _):
        wa.send_text(json.dumps({"type": "switch_project", "project": "beta"}))
        while json.loads(wa.receive_text())["type"] != "member_state":
            pass
        with opened(crew["bob"][0]) as (wb, _):                  # beta too
            wa.send_text(json.dumps({"prompt": "first"}))
            time.sleep(0.15)
            msgs = talk(wb, {"prompt": "second"})
            assert any(m["type"] == "system" and "already" in m["content"].lower() for m in msgs)
            talk(wa)
    assert [c["prompt"] for c in seen] == ["first"]


def test_losing_a_project_stops_the_next_prompt(crew, seen):
    with opened(crew["anna"][0]) as (ws, _):
        talk(ws, {"prompt": "one"})
        put(crew["admin"], "alpha", crew["anna"][1], member=False)
        msgs = talk(ws, {"prompt": "two"})
        assert any(m["type"] == "system" for m in msgs)
    assert [c["prompt"] for c in seen] == ["one"]


def test_a_member_opens_only_their_own_chats(crew, seen):
    def make(uid, project, title):
        def run():
            team.bind_user(uid)
            return sessions.create(project, title)
        return contextvars.copy_context().run(run)
    mine = make(crew["anna"][1], "alpha", "anna-chat")
    other = make(crew["bob"][1], "beta", "bob-chat")
    boss = make(None, "alpha", "boss-chat")
    with opened(crew["anna"][0]) as (ws, _):
        got = talk(ws, {"type": "open_session", "id": mine})
        assert [m for m in got if m["type"] == "session_loaded"][0]["session"]["id"] == mine
        for sid in (other, boss):
            got = talk(ws, {"type": "open_session", "id": sid})
            assert [m for m in got if m["type"] == "session_loaded"][0]["session"] is None
        got = talk(ws, {"type": "open_session", "id": "latest"})
        assert [m for m in got if m["type"] == "session_loaded"][0]["session"]["id"] == mine


def test_a_members_chat_belongs_to_them(crew, seen):
    with opened(crew["anna"][0]) as (ws, _):
        ws.send_text(json.dumps({"prompt": "hello"}))
        talk(ws)
    owners = [sessions.owner_of(s["id"]) for s in sessions.list_sessions(None, False, "", 50)]
    assert owners and set(owners) == {crew["anna"][1]}


def test_the_admin_is_unchanged(crew, seen, app_client):
    with crew["admin"].websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "auth_ok"
        talk(ws, {"prompt": "boss"})
    assert seen == [{"prompt": "boss", "project": "gamma", "user": None}]


# ---- what a member's model may do

def as_member(fn, project="alpha"):
    def run():
        team.bind_user(5)
        cm.bind_project(project)
        return fn()
    return contextvars.copy_context().run(run)


@pytest.fixture
def notes(tmp_path, monkeypatch):
    for attr, text in (("PERSONAL_MD", "ADMIN-PERSONAL-SECRET"), ("PROFESSIONAL_MD", "ADMIN-TECH-SECRET")):
        f = tmp_path / (attr.lower() + ".md"); f.write_text(text)
        monkeypatch.setattr(mm, attr, str(f))
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "alpha.md").write_text("ALPHA-NOTES")
    monkeypatch.setattr(mm, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(mm, "init_memory", lambda: None)
    return tmp_path


def test_a_member_gets_the_project_notes_and_not_the_admins(notes, ws_root):
    text = as_member(mm.get_memory_context)
    assert "ALPHA-NOTES" in text and "SECRET" not in text
    assert "ADMIN-PERSONAL-SECRET" in mm.get_memory_context()        # the admin still has theirs


def test_a_member_cannot_write_the_admins_notes(notes, ws_root, monkeypatch):
    monkeypatch.setattr(mm, "MEMBERS_DIR", str(notes / "members"))
    for kind in ("personal", "professional"):
        assert as_member(lambda: mm.update_memory(kind, "x")).startswith("Successfully")     # into their own notes
    assert (notes / "personal_md.md").read_text() == "ADMIN-PERSONAL-SECRET"
    assert (notes / "professional_md.md").read_text() == "ADMIN-TECH-SECRET"
    assert as_member(lambda: mm.update_memory("project", "from a member")).startswith("Successfully")


def test_a_member_is_offered_no_workspace_tools(app_client, ws_root):
    names = lambda tools: {t["function"]["name"] for t in tools}
    mine = names(as_member(app_client.offered_tools))
    assert not mine & {"list_projects", "create_project", "switch_project", "archive_project", "push_project"}
    assert {"execute_bash", "project_status"} <= mine
    assert {"create_project", "push_project"} <= names(app_client.offered_tools())


def test_a_members_workspace_tool_call_is_refused(app_client, ws_root):
    for tool in ("create_project", "switch_project", "archive_project", "push_project", "list_projects"):
        res = as_member(lambda: asyncio.run(app_client.run_workspace_tool(None, {}, tool, "{}", "t1")))
        assert res["ok"] is False and "team member" in res["error"], tool


# ---- the member's pages and own chat list

def test_a_member_gets_the_chat_page_and_the_account_page(crew):
    anna = crew["anna"][0]
    home_page = anna.get("/")
    assert home_page.status_code == 200 and 'id="chat"' in home_page.text and "member.js" in home_page.text
    acc = anna.get("/account")
    assert acc.status_code == 200 and "account.js" in acc.text
    assert 'href="/account"' in home_page.text and 'href="/"' in acc.text
    # the admin has no use for it
    assert "member.js" not in crew["admin"].get("/").text


def test_a_member_lists_their_own_chats_in_their_projects(crew):
    def make(uid, project, title):
        def run():
            team.bind_user(uid)
            return sessions.create(project, title)
        return contextvars.copy_context().run(run)
    mine = make(crew["anna"][1], "alpha", "anna-alpha")
    make(crew["anna"][1], "gamma", "anna-gamma")          # a project she is not on (any more)
    make(crew["bob"][1], "beta", "bob-chat")
    make(None, "alpha", "boss-chat")
    rows = crew["anna"][0].get("/api/me/chats").json()["chats"]
    assert [(r["id"], r["title"], r["project"]) for r in rows] == [(mine, "anna-alpha", "alpha")]
    assert crew["admin"].get("/api/me/chats").status_code == 409


def test_a_disabled_member_still_reaches_the_account_page(crew):
    anna, uid = crew["anna"]
    crew["admin"].post(f"/api/team/users/{uid}/disabled", json={"disabled": True}, headers=ORIGIN)
    assert anna.get("/account").status_code == 200
    assert anna.get("/api/me/chats").status_code == 403
