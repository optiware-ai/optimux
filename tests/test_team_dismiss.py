"""Team version, step 10: dismissing a member (someone who has left the team).
Unlike disabling, which is a pause, a dismissed member is closed out: signed out, off every project,
their budget removed, hidden from the Team lists. Their chats and usage stay. The admin can restore them."""
import pytest
from fastapi.testclient import TestClient

from optimux_backend.core import budgets, team
from tests.test_team_chat import crew, ws_root  # noqa: F401
from tests.test_team_users import ADMIN_PW, ORIGIN, USER_PW, app_client, home, invite_and_activate, solo, teamed  # noqa: F401


def dismiss(admin, user_id, dismissed=True):
    return admin.post(f"/api/team/users/{user_id}/dismissed", json={"dismissed": dismissed}, headers=ORIGIN)


def row(admin, name):
    return [u for u in admin.get("/api/team/users").json()["users"] if u["name"] == name][0]


def test_a_dismissed_member_is_closed_out(crew, app_client):
    admin, (anna, aid) = crew["admin"], crew["anna"]
    assert dismiss(admin, aid).status_code == 200
    me = anna.get("/api/me")
    assert me.status_code == 403 and me.json()["disabled"] is True and "closed" in me.json()["detail"]
    fresh = TestClient(app_client.app)
    r = fresh.post("/auth/login", json={"name": "anna", "password": USER_PW}, headers=ORIGIN)
    assert r.status_code == 403 and "closed" in r.json()["detail"]
    assert fresh.post("/auth/login", json={"name": "anna", "password": "wrong password!"}, headers=ORIGIN).status_code == 401
    assert crew["bob"][0].get("/api/me").status_code == 200


def test_dismissing_takes_the_member_off_projects_and_removes_the_budget(crew):
    admin, aid = crew["admin"], crew["anna"][1]
    admin.post(f"/api/team/users/{aid}/budget", json={"monthly_usd": 4}, headers=ORIGIN)
    assert aid in admin.get("/api/team/projects").json()["members"]["alpha"]
    dismiss(admin, aid)
    members = admin.get("/api/team/projects").json()["members"]
    assert all(aid not in ids for ids in members.values())
    assert row(admin, "anna")["status"] == "dismissed" and row(admin, "anna")["budget"] is None
    assert row(admin, "anna")["signed_in"] is False
    dismiss(admin, aid, False)
    assert row(admin, "anna")["status"] == "active"
    assert all(aid not in ids for ids in admin.get("/api/team/projects").json()["members"].values())    # not given back


def test_a_restored_member_signs_in_again(crew, app_client):
    admin, aid = crew["admin"], crew["anna"][1]
    dismiss(admin, aid); dismiss(admin, aid, False)
    c = TestClient(app_client.app)
    assert c.post("/auth/login", json={"name": "anna", "password": USER_PW}, headers=ORIGIN).status_code == 200


def test_a_member_who_never_accepted_comes_back_as_invited(teamed):
    uid = teamed.post("/api/team/users", json={"name": "dora"}, headers=ORIGIN).json()["id"]
    dismiss(teamed, uid)
    assert row(teamed, "dora")["status"] == "dismissed"
    dismiss(teamed, uid, False)
    assert row(teamed, "dora")["status"] == "invited"


def test_only_members_can_be_dismissed_and_only_by_the_admin(crew):
    admin_id = [u["id"] for u in crew["admin"].get("/api/team/users").json()["users"] if u["role"] == "admin"][0]
    assert dismiss(crew["admin"], admin_id).status_code == 404
    assert dismiss(crew["admin"], 9999).status_code == 404
    assert dismiss(crew["anna"][0], crew["bob"][1]).status_code == 403


def test_a_dismissed_member_is_not_disabled_or_invited_again_by_accident(crew):
    admin, aid = crew["admin"], crew["anna"][1]
    dismiss(admin, aid)
    assert admin.post(f"/api/team/users/{aid}/disabled", json={"disabled": True}, headers=ORIGIN).status_code == 409
    assert admin.post(f"/api/team/users/{aid}/invite", headers=ORIGIN).status_code == 409
    assert admin.post("/api/team/users", json={"name": "anna"}, headers=ORIGIN).status_code == 409      # the name stays theirs
    assert admin.post(f"/api/team/projects/alpha/members/{aid}", json={"member": True}, headers=ORIGIN).status_code == 409


def test_disabling_still_works_as_before(crew):
    admin, aid = crew["admin"], crew["anna"][1]
    assert admin.post(f"/api/team/users/{aid}/disabled", json={"disabled": True}, headers=ORIGIN).status_code == 200
    assert row(admin, "anna")["status"] == "disabled"
    assert aid in admin.get("/api/team/projects").json()["members"]["alpha"]          # a pause keeps the projects
    admin.post(f"/api/team/users/{aid}/disabled", json={"disabled": False}, headers=ORIGIN)
    assert row(admin, "anna")["status"] == "active"


def test_a_dismissed_members_chats_stay_private_and_their_history_stays(crew):
    admin, (anna, aid) = crew["admin"], crew["anna"]
    assert anna.post("/api/me/consent", json={"chat_access": True}, headers=ORIGIN).status_code == 200
    assert aid not in team.private_users()                 # anna agreed to chat access
    dismiss(admin, aid)
    assert aid in team.private_users()                     # closed accounts are not read
    assert team.get(aid)["name"] == "anna"
    assert admin.get(f"/api/usage?user={aid}").status_code == 200       # what they used is still there
