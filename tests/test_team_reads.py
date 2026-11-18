"""Team version, step 4b: what the admin sees of members' work.
Numbers (usage, cost) are always visible. Text (chat titles, prompts, what was sent) only for members
who agreed; every admin opening of such a member's data is logged. Nothing changes in solo mode."""
import contextvars

import pytest

from optimux_backend.core import sent, sessions, team, usage
from tests.test_team_users import ORIGIN, app_client, home, invite_and_activate, solo, teamed  # noqa: F401


def as_user(uid, fn):
    def run():
        team.bind_user(uid)
        return fn()
    return contextvars.copy_context().run(run)


@pytest.fixture
def world(teamed):
    """boss (admin, rows with no owner), anna (no consent), bob (agreed). One chat, call and usage row each."""
    _, anna = invite_and_activate(teamed, "anna", chat_access=False)
    _, bob = invite_and_activate(teamed, "bob", chat_access=True)
    ids = {"boss": None, "anna": anna["id"], "bob": bob["id"]}
    out = {"client": teamed, "ids": ids, "sid": {}, "call": {}}
    for name, uid in ids.items():
        def seed(name=name):
            sid = sessions.create("alpha", f"title-{name}")
            call = sent.record(sid, "simple", "m", [{"role": "user", "content": f"secret-{name}"}], None,
                               project="alpha", task_id=f"task-{name}")
            usage.insert({"project": "alpha", "model": "m", "session": sid, "cost": {"boss": 1.0, "anna": 2.0, "bob": 4.0}[name],
                          "task_id": f"task-{name}"})
            return sid, call
        out["sid"][name], out["call"][name] = as_user(uid, seed)
    return out


def get(w, url):
    return w["client"].get(url, headers=ORIGIN)


def by_user(rows):
    return {r["user"]: r for r in rows}


def test_the_chat_list_names_the_owner_and_hides_private_titles(world):
    rows = by_user(get(world, "/api/sessions?all_projects=true").json())
    assert set(rows) == {"boss", "anna", "bob"}
    assert rows["bob"]["title"] == "title-bob" and not rows["bob"]["private"]
    assert rows["boss"]["title"] == "title-boss" and not rows["boss"]["private"]
    assert rows["anna"]["title"] == "" and rows["anna"]["private"] is True
    assert "title-anna" not in get(world, "/api/sessions?all_projects=true").text


def test_the_chat_list_filters_by_member(world):
    ids = world["ids"]
    assert [r["user"] for r in get(world, "/api/sessions?all_projects=true&user=me").json()] == ["boss"]
    assert [r["user"] for r in get(world, f"/api/sessions?all_projects=true&user={ids['bob']}").json()] == ["bob"]
    assert get(world, "/api/sessions?all_projects=true&user=nobody").status_code == 422


def test_a_search_cannot_find_private_titles(world):
    assert get(world, "/api/sessions?all_projects=true&q=title-anna").json() == []
    assert [r["user"] for r in get(world, "/api/sessions?all_projects=true&q=title-bob").json()] == ["bob"]


def test_the_request_list_hides_private_text(world):
    rows = by_user(get(world, "/api/sent/requests?all_projects=true").json()["requests"])
    assert rows["bob"]["prompt"] == "secret-bob" and rows["bob"]["title"] == "title-bob"
    assert rows["anna"]["prompt"] == "" and rows["anna"]["title"] == "" and rows["anna"]["private"] is True
    assert get(world, "/api/sent/requests?all_projects=true&q=secret-anna").json()["requests"] == []
    assert len(get(world, "/api/sent/requests?all_projects=true&q=secret-bob").json()["requests"]) == 1


def test_the_request_list_filters_by_member(world):
    uid = world["ids"]["bob"]
    rows = get(world, f"/api/sent/requests?all_projects=true&user={uid}").json()["requests"]
    assert [r["user"] for r in rows] == ["bob"]
    assert get(world, f"/api/sent/requests?all_projects=true&user={uid}&count_only=true").json()["count"] == 1


def test_what_was_sent_is_refused_without_agreement(world):
    r = get(world, f"/api/sent/{world['call']['anna']}")
    assert r.status_code == 403 and "agreed" in r.json()["detail"]
    r = get(world, f"/api/sent/request?session={world['sid']['anna']}&key=task-anna")
    assert r.status_code == 403
    assert team.audit_all() == [], "a refusal is not an opening"
    assert get(world, "/api/sent?limit=50").json()["calls"] and "secret-anna" not in get(world, "/api/sent").text


def test_what_was_sent_by_an_agreeing_member_opens_and_is_logged(world):
    assert get(world, f"/api/sent/{world['call']['bob']}").status_code == 200
    assert get(world, f"/api/sent/request?session={world['sid']['bob']}&key=task-bob").status_code == 200
    log = team.audit_all()
    assert [(l["kind"], l["user"], l["admin"]) for l in log] == [("sent", "bob", "boss"), ("sent", "bob", "boss")]


def test_the_admins_own_work_opens_without_a_log(world):
    assert get(world, f"/api/sent/{world['call']['boss']}").status_code == 200
    assert team.audit_all() == []


def test_revoking_agreement_closes_it_again(world):
    bob = world["ids"]["bob"]
    team.set_chat_access(bob, False)
    assert get(world, f"/api/sent/{world['call']['bob']}").status_code == 403
    rows = by_user(get(world, "/api/sessions?all_projects=true").json())
    assert rows["bob"]["private"] is True and rows["bob"]["title"] == ""


def test_usage_numbers_are_always_visible_and_filterable(world):
    allt = get(world, "/api/usage").json()["all_time"]["total"]["cost"]
    assert allt == pytest.approx(7.0)
    ids = world["ids"]
    assert get(world, "/api/usage?user=me").json()["all_time"]["total"]["cost"] == pytest.approx(1.0)
    assert get(world, f"/api/usage?user={ids['anna']}").json()["all_time"]["total"]["cost"] == pytest.approx(2.0)
    daily = get(world, f"/api/usage/daily?days=2&user={ids['bob']}").json()
    assert sum(d["cost"] for d in daily) == pytest.approx(4.0)
    assert get(world, "/api/usage?user=nobody").status_code == 422


def test_looking_at_a_members_usage_is_logged_only_when_asked_for_by_name(world):
    get(world, "/api/usage")
    assert team.audit_all() == []
    get(world, f"/api/usage?user={world['ids']['anna']}")
    assert [(l["kind"], l["user"]) for l in team.audit_all()] == [("usage", "anna")]


def test_opening_a_chat_follows_the_same_rule(world, app_client):
    guard = app_client.chat_read_block
    assert guard(world["sid"]["boss"]) == ""
    assert "agreed" in guard(world["sid"]["anna"])
    assert guard(world["sid"]["bob"]) == ""
    assert [(l["kind"], l["user"]) for l in team.audit_all()] == [("chat", "bob")]


def test_solo_is_unchanged(solo):
    sessions.create("alpha", "mine")
    rows = solo.get("/api/sessions?all_projects=true", headers=ORIGIN).json()
    assert [r["title"] for r in rows] == ["mine"]
    assert not any(r.get("private") for r in rows) and solo.get("/api/usage?user=me", headers=ORIGIN).status_code == 200


def test_the_admins_latest_chat_is_never_a_members(world, app_client, monkeypatch):
    from optimux_backend.core import sessions
    monkeypatch.setattr(app_client, "missing_project_reason", lambda name: None)
    assert sessions.latest("alpha") == world["sid"]["boss"]          # bob's and anna's are newer
    assert app_client.last_open_chat() == world["sid"]["boss"]
