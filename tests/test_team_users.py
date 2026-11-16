"""Team version, step 1: users, invitations, the two sign-in doors, consent and
the audit log. The solo version (no team mode) must behave as before."""
import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from optimux_backend.core import auth, paths, store, team

ORIGIN = {"Origin": "http://testserver"}
ADMIN_PW = "correct horse battery"
USER_PW = "another long password"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HOME", str(tmp_path))
    for lim in (auth.limiter, auth.global_limiter, auth.code_limiter):
        lim.__init__(lim.limit, lim.window)
    return tmp_path


@pytest.fixture
def app_client(home, monkeypatch):
    monkeypatch.setenv("OPTIMUX_TOKEN", "test-token")
    from optimux_backend import main
    main.LIVE_SOCKETS.clear()
    return main


def fresh(main):
    return TestClient(main.app)


@pytest.fixture
def solo(app_client):
    c = fresh(app_client)
    assert c.post("/auth/setup", json={"token": "test-token", "password": ADMIN_PW}, headers=ORIGIN).status_code == 200
    return c


@pytest.fixture
def teamed(app_client, solo):
    """A team install: the existing password became the first admin's, and `solo` is that admin, signed in."""
    team.enable("boss")
    return solo


def invite_and_activate(admin, name="anna", chat_access=False, password=USER_PW):
    r = admin.post("/api/team/users", json={"name": name}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    c = TestClient(admin.app)
    a = c.post("/auth/activate", json={"token": token, "password": password, "chat_access": chat_access}, headers=ORIGIN)
    assert a.status_code == 200, a.text
    return c, r.json()


# ---------------------------------------------------------------- schema and mode

def test_the_users_tables_exist():
    conn = store.connect()
    try:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(logins)")}
    finally:
        conn.close()
    assert {"users", "admin_access"} <= tables
    assert "user_id" in cols


def test_a_new_install_is_solo(home):
    assert team.mode() == "solo" and not team.enabled()


def test_solo_sign_in_ignores_names_and_flags(solo):
    r = TestClient(solo.app).post("/auth/login", json={"password": ADMIN_PW, "name": "anna", "admin": False}, headers=ORIGIN)
    assert r.status_code == 200
    me = TestClient(solo.app)
    me.post("/auth/login", json={"password": ADMIN_PW}, headers=ORIGIN)
    body = me.get("/api/me").json()
    assert body["mode"] == "solo" and body["admin"] is True and body["user"] is None


def test_team_endpoints_do_nothing_in_solo(solo):
    assert solo.get("/api/team/users").status_code == 409
    assert solo.post("/api/team/users", json={"name": "anna"}, headers=ORIGIN).status_code == 409


# ---------------------------------------------------------------- turning team mode on and off

def test_team_mode_needs_a_password_first(home):
    with pytest.raises(team.TeamError):
        team.enable("boss")
    assert team.mode() == "solo"


def test_enabling_makes_the_existing_login_the_first_admin(solo):
    out = team.enable("boss")
    assert team.mode() == "team" and out["name"] == "boss" and out["role"] == "admin"
    assert team.enable("boss")["id"] == out["id"], "running it twice changes nothing"
    assert [u["name"] for u in team.list_users()] == ["boss"]
    # the browser that was already signed in is the admin
    assert solo.get("/api/me").json()["admin"] is True


def test_disabling_returns_to_solo_and_signs_the_members_out(teamed):
    member, _ = invite_and_activate(teamed)
    assert member.get("/api/me").status_code == 200
    team.disable()
    assert team.mode() == "solo"
    assert member.get("/api/me").status_code == 401
    assert teamed.get("/api/me").json()["mode"] == "solo"


# ---------------------------------------------------------------- invitations

def test_an_invitation_gives_a_link_token_once_and_stores_only_its_hash(teamed):
    r = teamed.post("/api/team/users", json={"name": "Anna"}, headers=ORIGIN).json()
    assert r["name"] == "Anna" and len(r["token"]) >= 30 and r["expires"] > time.time()
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE name = 'Anna'").fetchone()
    finally:
        conn.close()
    assert row["status"] == "invited" and row["password"] is None
    assert row["invite_hash"] == hashlib.sha256(r["token"].encode()).hexdigest()
    assert r["token"] not in " ".join(str(v) for v in tuple(row))
    users = teamed.get("/api/team/users").json()["users"]
    assert "token" not in str(users)


@pytest.mark.parametrize("name", ["", "a", "x" * 41, "bad/name", "tab\tname", "<b>x</b>", "  "])
def test_names_must_be_plain(teamed, name):
    assert teamed.post("/api/team/users", json={"name": name}, headers=ORIGIN).status_code == 400


def test_names_are_unique_ignoring_case_and_the_admin_name_is_taken(teamed):
    assert teamed.post("/api/team/users", json={"name": "Anna"}, headers=ORIGIN).status_code == 200
    assert teamed.post("/api/team/users", json={"name": "anna"}, headers=ORIGIN).status_code == 409
    assert teamed.post("/api/team/users", json={"name": "BOSS"}, headers=ORIGIN).status_code == 409


def test_the_invitation_page_shows_the_name_and_the_consent_text(teamed):
    token = teamed.post("/api/team/users", json={"name": "anna"}, headers=ORIGIN).json()["token"]
    r = TestClient(teamed.app).post("/auth/invite", json={"token": token}, headers=ORIGIN)
    body = r.json()
    assert r.status_code == 200 and body["name"] == "anna"
    assert body["consent"]["version"] == team.CONSENT_VERSION
    text = body["consent"]["text"].lower()
    for word in ("usage", "chats", "memory", "revoke"):
        assert word in text, word


def test_a_wrong_or_used_or_expired_token_is_refused(teamed, monkeypatch):
    c = TestClient(teamed.app)
    assert c.post("/auth/invite", json={"token": "nope"}, headers=ORIGIN).status_code == 404
    token = teamed.post("/api/team/users", json={"name": "anna"}, headers=ORIGIN).json()["token"]
    ok = {"token": token, "password": USER_PW, "chat_access": False}
    assert c.post("/auth/activate", json=ok, headers=ORIGIN).status_code == 200
    assert c.post("/auth/activate", json=ok, headers=ORIGIN).status_code == 404, "works once"
    token2 = teamed.post("/api/team/users", json={"name": "bob"}, headers=ORIGIN).json()["token"]
    real = time.time
    monkeypatch.setattr(team.time, "time", lambda: real() + 8 * 24 * 3600)
    assert c.post("/auth/invite", json={"token": token2}, headers=ORIGIN).status_code == 404


def test_a_short_password_does_not_use_up_the_invitation(teamed):
    token = teamed.post("/api/team/users", json={"name": "anna"}, headers=ORIGIN).json()["token"]
    c = TestClient(teamed.app)
    assert c.post("/auth/activate", json={"token": token, "password": "short", "chat_access": False}, headers=ORIGIN).status_code == 400
    assert c.post("/auth/activate", json={"token": token, "password": USER_PW, "chat_access": False}, headers=ORIGIN).status_code == 200


def test_activation_signs_the_user_in_and_records_the_consent_choice(teamed):
    member, _ = invite_and_activate(teamed, chat_access=True)
    me = member.get("/api/me").json()
    assert me["admin"] is False and me["user"]["name"] == "anna"
    assert me["user"]["chat_access"] is True
    assert me["user"]["consent"]["version"] == team.CONSENT_VERSION
    assert abs(me["user"]["consent"]["at"] - time.time()) < 30


def test_the_default_choice_is_usage_and_cost_only(teamed):
    member, _ = invite_and_activate(teamed)
    assert member.get("/api/me").json()["user"]["chat_access"] is False


def test_activation_attempts_with_bad_tokens_are_limited(teamed):
    c = TestClient(teamed.app)
    codes = [c.post("/auth/activate", json={"token": f"bad{i}", "password": USER_PW, "chat_access": False},
                    headers=ORIGIN).status_code for i in range(7)]
    assert 429 in codes


# ---------------------------------------------------------------- the two doors

def test_a_member_signs_in_with_name_and_password(teamed):
    invite_and_activate(teamed)
    c = TestClient(teamed.app)
    assert c.post("/auth/login", json={"name": "ANNA", "password": USER_PW}, headers=ORIGIN).status_code == 200
    assert c.get("/api/me").json()["user"]["name"] == "anna"


def test_wrong_name_or_password_gets_the_same_answer(teamed):
    invite_and_activate(teamed)
    c = TestClient(teamed.app)
    a = c.post("/auth/login", json={"name": "anna", "password": "wrong password!!"}, headers=ORIGIN)
    b = c.post("/auth/login", json={"name": "nobody", "password": USER_PW}, headers=ORIGIN)
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_a_member_door_never_signs_an_admin_in(teamed):
    c = TestClient(teamed.app)
    r = c.post("/auth/login", json={"name": "boss", "password": ADMIN_PW}, headers=ORIGIN)
    assert r.status_code == 401
    assert c.post("/auth/login", json={"password": ADMIN_PW}, headers=ORIGIN).status_code == 401, "no name, no admin"


def test_the_admin_door_uses_the_admin_password_and_codes(teamed):
    c = TestClient(teamed.app)
    assert c.post("/auth/login", json={"admin": True, "password": "wrong password!!"}, headers=ORIGIN).status_code == 401
    assert c.post("/auth/login", json={"admin": True, "password": ADMIN_PW}, headers=ORIGIN).status_code == 200
    me = c.get("/api/me").json()
    assert me["admin"] is True and me["user"]["name"] == "boss"


def test_a_member_password_does_not_open_the_admin_door(teamed):
    invite_and_activate(teamed)
    c = TestClient(teamed.app)
    assert c.post("/auth/login", json={"admin": True, "password": USER_PW}, headers=ORIGIN).status_code == 401


def test_a_disabled_member_cannot_sign_in_and_is_signed_out(teamed):
    member, info = invite_and_activate(teamed)
    assert teamed.post(f"/api/team/users/{info['id']}/disabled", json={"disabled": True}, headers=ORIGIN).status_code == 200
    assert member.get("/api/me").status_code == 401
    c = TestClient(teamed.app)
    assert c.post("/auth/login", json={"name": "anna", "password": USER_PW}, headers=ORIGIN).status_code == 401
    teamed.post(f"/api/team/users/{info['id']}/disabled", json={"disabled": False}, headers=ORIGIN)
    assert c.post("/auth/login", json={"name": "anna", "password": USER_PW}, headers=ORIGIN).status_code == 200


def test_a_new_invitation_replaces_the_password_and_the_old_link(teamed):
    member, info = invite_and_activate(teamed)
    again = teamed.post(f"/api/team/users/{info['id']}/invite", headers=ORIGIN).json()
    assert again["token"] != info["token"]
    assert member.get("/api/me").status_code == 401, "signed out"
    c = TestClient(teamed.app)
    assert c.post("/auth/login", json={"name": "anna", "password": USER_PW}, headers=ORIGIN).status_code == 401
    ok = {"token": again["token"], "password": "a brand new password", "chat_access": False}
    assert c.post("/auth/activate", json=ok, headers=ORIGIN).status_code == 200


def test_the_access_token_is_the_admin(teamed):
    r = TestClient(teamed.app).get("/api/team/users", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200


# ---------------------------------------------------------------- members reach only their own account (until step 2)

@pytest.mark.parametrize("path", ["/api/settings", "/api/projects", "/api/team/users", "/api/team/audit", "/api/sent/requests"])
def test_a_member_is_kept_out_of_everything_else(teamed, path):
    member, _ = invite_and_activate(teamed)
    assert member.get(path).status_code == 403


def test_a_member_cannot_invite_anyone(teamed):
    member, _ = invite_and_activate(teamed)
    assert member.post("/api/team/users", json={"name": "eve"}, headers=ORIGIN).status_code == 403


def test_a_member_cannot_use_the_chat_socket_yet(teamed):
    member, _ = invite_and_activate(teamed)
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with member.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            ws.receive_text()


# ---------------------------------------------------------------- the user list

def test_the_admin_sees_who_is_active_and_who_is_signed_in(teamed):
    member, info = invite_and_activate(teamed, "anna")
    teamed.post("/api/team/users", json={"name": "bob"}, headers=ORIGIN)
    users = {u["name"]: u for u in teamed.get("/api/team/users").json()["users"]}
    assert users["boss"]["role"] == "admin" and users["boss"]["signed_in"] is True
    assert users["anna"]["status"] == "active" and users["anna"]["signed_in"] is True
    assert users["bob"]["status"] == "invited" and users["bob"]["signed_in"] is False
    member.post("/auth/logout", headers=ORIGIN)
    users = {u["name"]: u for u in teamed.get("/api/team/users").json()["users"]}
    assert users["anna"]["signed_in"] is False and users["anna"]["last_login"]


def test_the_list_never_holds_passwords_or_hashes(teamed):
    invite_and_activate(teamed)
    text = str(teamed.get("/api/team/users").json())
    assert "scrypt" not in text and "invite_hash" not in text and "password" not in text


# ---------------------------------------------------------------- consent and the audit log

def test_a_member_can_change_and_revoke_the_chat_consent(teamed):
    member, info = invite_and_activate(teamed, chat_access=True)
    assert team.admin_may_read_chats(info["id"]) is True
    r = member.post("/api/me/consent", json={"chat_access": False}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["chat_access"] is False
    assert team.admin_may_read_chats(info["id"]) is False
    member.post("/api/me/consent", json={"chat_access": True}, headers=ORIGIN)
    assert team.admin_may_read_chats(info["id"]) is True


def test_every_consent_change_is_kept_as_history(teamed):
    member, info = invite_and_activate(teamed, chat_access=True)
    member.post("/api/me/consent", json={"chat_access": False}, headers=ORIGIN)
    hist = team.consent_history(info["id"])
    assert [h["chat_access"] for h in hist] == [True, False]
    assert all(h["version"] == team.CONSENT_VERSION and h["at"] for h in hist)


def test_an_admin_look_is_logged_and_the_member_sees_it(teamed):
    member, info = invite_and_activate(teamed)
    other, other_info = invite_and_activate(teamed, "bob")
    admin_id = team.get_by_name("boss")["id"]
    team.log_admin_access(admin_id, info["id"], "chat", "a-chat-id")
    team.log_admin_access(admin_id, other_info["id"], "usage", "")
    mine = member.get("/api/me/audit").json()["lines"]
    assert len(mine) == 1 and mine[0]["admin"] == "boss" and mine[0]["kind"] == "chat" and mine[0]["ref"] == "a-chat-id"
    assert mine[0]["at"] > 0
    everything = teamed.get("/api/team/audit").json()["lines"]
    assert {line["user"] for line in everything} == {"anna", "bob"}


def test_the_log_records_what_was_opened_but_not_its_content(teamed):
    member, info = invite_and_activate(teamed)
    team.log_admin_access(1, info["id"], "chat", "id-1")
    conn = store.connect()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(admin_access)")}
    finally:
        conn.close()
    assert cols == {"id", "ts", "admin_id", "user_id", "kind", "ref"}


def test_there_is_no_way_to_change_or_erase_the_log(teamed):
    routes = {(m, r.path) for r in teamed.app.routes if hasattr(r, "methods") for m in r.methods}
    audit = [(m, p) for m, p in routes if "audit" in p]
    assert all(m == "GET" for m, _ in audit), audit


# ---------------------------------------------------------------- the command line

def test_the_command_turns_team_mode_on_and_off(solo, capsys):
    from optimux_backend import admin
    assert admin.main(["enable-team", "boss"]) == 0
    assert team.mode() == "team"
    assert admin.main(["disable-team"]) == 0
    assert team.mode() == "solo"


def test_the_command_refuses_without_a_password(home, capsys):
    from optimux_backend import admin
    with pytest.raises(SystemExit):
        admin.main(["enable-team", "boss"])


# ---------------------------------------------------------------- the log can't be rewritten, and admin sessions are separate

def test_the_database_itself_refuses_to_change_or_erase_log_lines(teamed):
    member, info = invite_and_activate(teamed)
    team.log_admin_access(1, info["id"], "chat", "x")
    conn = store.connect()
    try:
        for sql in ("UPDATE admin_access SET kind = 'usage'", "DELETE FROM admin_access"):
            with pytest.raises(Exception, match="append-only"):
                with conn:
                    conn.execute(sql)
    finally:
        conn.close()
    assert len(team.audit_for_user(info["id"])) == 1


def test_the_admin_changing_the_password_keeps_the_members_signed_in(teamed):
    member, _ = invite_and_activate(teamed)
    r = teamed.post("/api/auth/password", json={"current": ADMIN_PW, "new": "a different long one"}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert member.get("/api/me").status_code == 200


def test_the_admins_session_list_does_not_hold_the_members(teamed):
    invite_and_activate(teamed)
    sessions = teamed.get("/api/auth").json()["sessions"]
    assert len(sessions) == 1
