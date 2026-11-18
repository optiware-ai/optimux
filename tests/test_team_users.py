"""Team version, step 1: users, invitations, the two sign-in doors, consent and
the audit log. The solo version (no team mode) must behave as before."""
import hashlib
import json
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


def test_a_disabled_member_is_told_why_and_cannot_use_the_app(teamed):
    member, info = invite_and_activate(teamed)
    assert teamed.post(f"/api/team/users/{info['id']}/disabled", json={"disabled": True}, headers=ORIGIN).status_code == 200
    # the open page learns it from its next request: a clear reason, not a bare "signed out"
    r = member.get("/api/me")
    assert r.status_code == 403 and r.json()["disabled"] is True and "disabled" in r.json()["detail"]
    assert member.get("/api/settings").status_code == 403
    assert member.get("/").status_code == 200, "the account page stays, so it can show the message"
    users = {u["name"]: u for u in teamed.get("/api/team/users").json()["users"]}
    assert users["anna"]["status"] == "disabled" and users["anna"]["signed_in"] is False
    # after the page signs itself out, the session is gone
    member.post("/auth/logout", headers=ORIGIN)
    assert member.get("/api/me").status_code == 401


def test_signing_in_to_a_disabled_account_says_so_only_with_the_right_password(teamed):
    member, info = invite_and_activate(teamed)
    teamed.post(f"/api/team/users/{info['id']}/disabled", json={"disabled": True}, headers=ORIGIN)
    c = TestClient(teamed.app)
    right = c.post("/auth/login", json={"name": "anna", "password": USER_PW}, headers=ORIGIN)
    assert right.status_code == 403 and "disabled by the admin" in right.json()["detail"]
    assert "contact your admin" in right.json()["detail"].lower()
    wrong = c.post("/auth/login", json={"name": "anna", "password": "wrong password!!"}, headers=ORIGIN)
    unknown = c.post("/auth/login", json={"name": "nobody", "password": USER_PW}, headers=ORIGIN)
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json(), "no hint to strangers"
    assert c.get("/api/me").status_code == 401, "not signed in"


def test_enabling_again_needs_a_fresh_sign_in(teamed):
    member, info = invite_and_activate(teamed)
    teamed.post(f"/api/team/users/{info['id']}/disabled", json={"disabled": True}, headers=ORIGIN)
    teamed.post(f"/api/team/users/{info['id']}/disabled", json={"disabled": False}, headers=ORIGIN)
    assert member.get("/api/me").status_code == 401, "the old session is gone"
    c = TestClient(teamed.app)
    assert c.post("/auth/login", json={"name": "anna", "password": USER_PW}, headers=ORIGIN).status_code == 200


def test_the_account_page_shows_the_notice_then_signs_itself_out():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "web")
    js = open(os.path.join(root, "account.js")).read()
    html = open(os.path.join(root, "account.html")).read()
    assert 'id="disabled-note"' in html
    assert "data.disabled" in js and "setTimeout" in js and "disabled=1" in js
    login = open(os.path.join(root, "login.js")).read()
    assert "disabled" in login


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


def test_a_member_with_no_project_cannot_use_the_chat_socket(teamed):
    member, _ = invite_and_activate(teamed)
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as e:
        with member.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as ws:
            assert json.loads(ws.receive_text())["type"] == "no_projects"
            ws.receive_text()
    assert e.value.code == 4403


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

def test_the_command_turns_team_mode_on_and_off(solo, capsys, monkeypatch):
    from optimux_backend import admin
    from optimux_backend.core import sandbox
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": True, "error": "", "path": "bwrap"})
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


# ---------------------------------------------------------------- pages (p84)

def test_a_member_lands_on_their_account_page_and_the_admin_on_the_app(teamed):
    member, _ = invite_and_activate(teamed)
    page = member.get("/")
    assert page.status_code == 200 and "/static/account.js" in page.text
    app_page = teamed.get("/")
    assert app_page.status_code == 200 and "/static/account.js" not in app_page.text


def test_the_account_page_gets_the_consent_text_with_the_member_only(teamed):
    member, _ = invite_and_activate(teamed)
    me = member.get("/api/me").json()
    assert me["consent_text"] == team.tr(team.CONSENT_TEXT)
    assert "consent_text" not in teamed.get("/api/me").json()


def test_the_sign_in_page_has_the_member_door_the_admin_door_and_the_invitation():
    import os
    html = open(os.path.join(os.path.dirname(__file__), "..", "web", "login.html")).read()
    for ident in ("member-form", "login-form", "invite-form", "to-admin", "to-member"):
        assert f'id="{ident}"' in html, ident
    assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", "web", "account.js"))


def test_the_invitation_link_keeps_the_token_out_of_the_address_the_server_sees():
    import os
    js = open(os.path.join(os.path.dirname(__file__), "..", "web", "login.js")).read()
    assert "location.hash" in js and "replaceState" in js

# ---------------------------------------------------------------- every route, not a sample (team step 2)

def test_a_member_is_refused_on_every_route_except_their_own_account(teamed):
    """Deny by default: the guard's allow-list is the only way in. A new endpoint is closed to
    members until someone opens it on purpose (and then this test needs the new name)."""
    import re
    from optimux_backend import main
    member, _ = invite_and_activate(teamed)
    allowed_prefixes = ("/static/", "/preview/") + tuple(main.MEMBER_PATHS)
    seen = 0
    for route in main.app.routes:
        path = getattr(route, "path", None)
        if path is None or not hasattr(route, "methods"):
            continue
        if path == "/" or path in main.PUBLIC_PATHS or path.startswith(allowed_prefixes):
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            r = member.request(method, url, json={} if method != "GET" else None, headers=ORIGIN)
            assert r.status_code == 403, f"{method} {path} gave a member {r.status_code}"
            seen += 1
    assert seen > 120, seen


def test_the_member_allow_list_is_what_the_audit_says_members_may_do():
    """What the guard lets a member reach is exactly: the page, their own account, and the
    public sign-in endpoints. Opening more means changing this on purpose."""
    from optimux_backend import main
    assert main.MEMBER_PATHS == ("/api/me",)


# ---------------------------------------------------------------- team mode needs the sandbox

def test_team_mode_makes_the_sandbox_required(solo):
    from optimux_backend.core import sandbox
    assert not sandbox.required()
    team.enable("boss")
    assert sandbox.required() and sandbox.enabled({})
    team.disable()
    assert not sandbox.required()


def test_the_command_refuses_team_mode_where_the_sandbox_does_not_work(solo, monkeypatch):
    from optimux_backend import admin
    from optimux_backend.core import sandbox
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": False, "error": "no bwrap", "path": None})
    with pytest.raises(SystemExit, match="sandbox"):
        admin.main(["enable-team", "boss"])
    assert team.mode() == "solo"


# ---------------------------------------------------------------- feedback and live updates (p86)

def test_the_account_page_always_shows_the_choice_in_force():
    import os
    root = os.path.join(os.path.dirname(__file__), "..", "web")
    html = open(os.path.join(root, "account.html")).read()
    js = open(os.path.join(root, "account.js")).read()
    assert 'id="current"' in html
    assert "$('current')" in js and "disabled" in js, "the button is off while saving and the line says what is in force"


def test_the_team_page_refreshes_itself_while_it_is_open():
    import os
    js = open(os.path.join(os.path.dirname(__file__), "..", "web", "app.js")).read()
    assert "settingsTab.value === 'team'" in js and "setInterval" in js


def test_the_status_says_when_the_browser_is_signed_in_as_a_member(teamed):
    member, _ = invite_and_activate(teamed)
    assert member.get("/auth/status").json()["member"] is True
    assert teamed.get("/auth/status").json()["member"] is False
    assert TestClient(teamed.app).get("/auth/status").json()["member"] is False


def test_a_member_who_opens_the_admin_door_is_shown_the_form_not_sent_to_their_page():
    import os
    js = open(os.path.join(os.path.dirname(__file__), "..", "web", "login.js")).read()
    assert "st.member" in js
