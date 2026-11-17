"""Team version, step 3a: which members are on which projects (many to many).
The admin assigns; a member sees only their own projects; nothing else changes yet."""
import os
import time

import pytest

from optimux_backend.core import store, team, workspace
from tests.test_team_users import (ORIGIN, TestClient, app_client, home, invite_and_activate, solo,  # noqa: F401
                                   teamed)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    for name in ("alpha", "beta", "gamma"):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(workspace, "projects_root", lambda: str(root))
    return root


def put(admin, project, user_id, member=True):
    return admin.post(f"/api/team/projects/{project}/members/{user_id}", json={"member": member}, headers=ORIGIN)


def test_the_table_exists():
    conn = store.connect()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(project_members)")}
    finally:
        conn.close()
    assert cols == {"project", "user_id", "added"}


def test_the_admin_assigns_and_removes_members(teamed, ws):
    anna, a = invite_and_activate(teamed, "anna")
    bob, b = invite_and_activate(teamed, "bob")
    assert put(teamed, "alpha", a["id"]).status_code == 200
    assert put(teamed, "alpha", b["id"]).status_code == 200
    assert put(teamed, "beta", a["id"]).status_code == 200
    data = teamed.get("/api/team/projects").json()
    assert data["projects"] == ["alpha", "beta", "gamma"]
    assert sorted(data["members"]["alpha"]) == sorted([a["id"], b["id"]])
    assert data["members"]["beta"] == [a["id"]] and data["members"].get("gamma", []) == []
    assert put(teamed, "alpha", b["id"], member=False).status_code == 200
    assert teamed.get("/api/team/projects").json()["members"]["alpha"] == [a["id"]]


def test_assigning_twice_is_harmless(teamed, ws):
    _, a = invite_and_activate(teamed)
    assert put(teamed, "alpha", a["id"]).status_code == 200
    assert put(teamed, "alpha", a["id"]).status_code == 200
    assert teamed.get("/api/team/projects").json()["members"]["alpha"] == [a["id"]]
    assert put(teamed, "alpha", a["id"], member=False).status_code == 200
    assert put(teamed, "alpha", a["id"], member=False).status_code == 200


def test_unknown_projects_and_people_are_refused(teamed, ws):
    _, a = invite_and_activate(teamed)
    assert put(teamed, "nope", a["id"]).status_code == 404
    assert put(teamed, "alpha", 9999).status_code == 404
    assert put(teamed, "..", a["id"]).status_code in (400, 404)
    admin_id = team.first_admin_id()
    assert put(teamed, "alpha", admin_id).status_code == 400, "the admin is on every project already"


def test_a_member_sees_only_their_own_projects(teamed, ws):
    anna, a = invite_and_activate(teamed, "anna")
    bob, b = invite_and_activate(teamed, "bob")
    put(teamed, "alpha", a["id"])
    put(teamed, "beta", b["id"])
    assert anna.get("/api/me").json()["projects"] == ["alpha"]
    assert bob.get("/api/me").json()["projects"] == ["beta"]
    assert "projects" not in teamed.get("/api/me").json()


def test_who_may_use_a_project(teamed, ws):
    _, a = invite_and_activate(teamed, "anna")
    _, b = invite_and_activate(teamed, "bob")
    put(teamed, "alpha", a["id"])
    assert team.can_use_project(a["id"], "alpha") is True
    assert team.can_use_project(b["id"], "alpha") is False
    assert team.can_use_project(a["id"], "beta") is False
    assert team.can_use_project(team.first_admin_id(), "beta") is True
    assert team.can_use_project(a["id"], "missing") is False


def test_access_survives_archiving_and_restoring(teamed, ws):
    anna, a = invite_and_activate(teamed, "anna")
    put(teamed, "alpha", a["id"])
    (ws / ".archive").mkdir()
    os.rename(ws / "alpha", ws / ".archive" / "alpha-20260101-000000")        # moved aside
    assert anna.get("/api/me").json()["projects"] == []
    assert teamed.get("/api/team/projects").json()["projects"] == ["beta", "gamma"]
    os.rename(ws / ".archive" / "alpha-20260101-000000", ws / "alpha")        # and comes back
    assert anna.get("/api/me").json()["projects"] == ["alpha"]


def test_no_workspace_folder_means_no_projects(teamed, monkeypatch):
    def broken():
        raise workspace.WorkspaceError("not set up")
    monkeypatch.setattr(workspace, "projects_root", broken)
    anna, _ = invite_and_activate(teamed)
    assert teamed.get("/api/team/projects").json()["projects"] == []
    assert anna.get("/api/me").json()["projects"] == []


def test_solo_has_no_project_assignments(solo, ws):
    assert solo.get("/api/team/projects").status_code == 409
    assert put(solo, "alpha", 1).status_code == 409


def test_a_disabled_member_keeps_their_assignments(teamed, ws):
    _, a = invite_and_activate(teamed)
    put(teamed, "alpha", a["id"])
    teamed.post(f"/api/team/users/{a['id']}/disabled", json={"disabled": True}, headers=ORIGIN)
    teamed.post(f"/api/team/users/{a['id']}/disabled", json={"disabled": False}, headers=ORIGIN)
    assert teamed.get("/api/team/projects").json()["members"]["alpha"] == [a["id"]]


def test_the_pages_show_the_assignments():
    root = os.path.join(os.path.dirname(__file__), "..")
    html = open(os.path.join(root, "web", "account.html")).read()
    account = open(os.path.join(root, "web", "account.js")).read()
    index = open(os.path.join(root, "index.html")).read()
    assert 'id="projects"' in html and "data.projects" in account
    assert "teamAssign" in index and "data-assign" in index
