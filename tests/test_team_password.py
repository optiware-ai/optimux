"""Team version, step 8: a member changes their own password, and the admin's sign-in is recorded."""
import pytest
from fastapi.testclient import TestClient

from optimux_backend.core import auth
from tests.test_team_chat import crew, ws_root  # noqa: F401
from tests.test_team_users import ADMIN_PW, ORIGIN, USER_PW, app_client, home, invite_and_activate, solo, teamed  # noqa: F401

NEW_PW = "a brand new long password"


def change(client, current, new):
    return client.post("/api/me/password", json={"current": current, "new": new}, headers=ORIGIN)


def sign_in(main, name, password):
    c = TestClient(main.app)
    return c, c.post("/auth/login", json={"name": name, "password": password}, headers=ORIGIN)


def test_a_member_changes_their_password(crew, app_client):
    anna = crew["anna"][0]
    assert change(anna, USER_PW, NEW_PW).status_code == 200
    assert sign_in(app_client, "anna", USER_PW)[1].status_code == 401
    assert sign_in(app_client, "anna", NEW_PW)[1].status_code == 200
    assert anna.get("/api/me").status_code == 200          # this browser stays signed in


def test_the_other_browsers_of_the_member_are_signed_out(crew, app_client):
    anna = crew["anna"][0]
    other, r = sign_in(app_client, "anna", USER_PW)
    assert r.status_code == 200 and other.get("/api/me").status_code == 200
    assert change(anna, USER_PW, NEW_PW).status_code == 200
    assert other.get("/api/me").status_code == 401
    assert anna.get("/api/me").status_code == 200
    assert crew["bob"][0].get("/api/me").status_code == 200        # nobody else is touched


def test_a_wrong_current_password_changes_nothing(crew, app_client):
    anna = crew["anna"][0]
    r = change(anna, "not the password", NEW_PW)
    assert r.status_code == 403 and "current password" in r.json()["detail"]
    assert sign_in(app_client, "anna", USER_PW)[1].status_code == 200


def test_wrong_current_passwords_are_counted_like_failed_sign_ins(crew, app_client):
    anna = crew["anna"][0]
    codes = [change(anna, "wrong password!", NEW_PW).status_code for _ in range(auth.limiter.limit + 2)]
    assert 429 in codes
    assert change(anna, USER_PW, NEW_PW).status_code == 429       # even the right one waits


def test_the_new_password_is_checked(crew):
    anna = crew["anna"][0]
    assert change(anna, USER_PW, "short").status_code == 400
    assert change(anna, USER_PW, USER_PW).status_code == 400
    assert change(anna, USER_PW, "x" * 2000).status_code == 400


def test_only_a_member_changes_a_member_password(crew):
    assert change(crew["admin"], ADMIN_PW, NEW_PW).status_code == 409
    anna = crew["anna"]
    crew["admin"].post(f"/api/team/users/{anna[1]}/disabled", json={"disabled": True}, headers=ORIGIN)
    assert change(anna[0], USER_PW, NEW_PW).status_code == 403


def test_the_admins_sign_in_is_recorded(teamed, app_client):
    rows = teamed.get("/api/team/users").json()["users"]
    assert [u for u in rows if u["role"] == "admin"][0]["last_login"] is None
    c = TestClient(app_client.app)
    assert c.post("/auth/login", json={"password": ADMIN_PW, "admin": True}, headers=ORIGIN).status_code == 200
    rows = teamed.get("/api/team/users").json()["users"]
    assert [u for u in rows if u["role"] == "admin"][0]["last_login"] is not None
