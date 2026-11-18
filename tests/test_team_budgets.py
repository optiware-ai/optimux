"""Team version, step 7: a monthly budget for each member, next to the project budgets.
A project's budget counts everyone who works in it; a member's budget counts what they spend
in every project. Either one, used up and set to block, stops that member's next model call."""
import asyncio
import contextvars
import time
from datetime import datetime

import pytest

from optimux_backend.core import budgets, team, usage
from optimux_backend.core.security import ValidationError
from tests.test_team_chat import crew, ws_root  # noqa: F401
from tests.test_team_users import ORIGIN, app_client, home, invite_and_activate, solo, teamed  # noqa: F401


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(budgets, "BUDGETS_FILE", str(tmp_path / "budgets.json"))
    monkeypatch.setattr(budgets, "USER_BUDGETS_FILE", str(tmp_path / "user_budgets.json"))


def spend(uid, project, cost, ts=None):
    def run():
        team.bind_user(uid)
        usage.insert({"ts": ts or time.time(), "project": project, "role": "simple", "model": "m",
                      "prompt_tokens": 1, "completion_tokens": 1, "cost": cost})
    contextvars.copy_context().run(run)


def as_user(uid, fn, project="alpha"):
    def run():
        team.bind_user(uid)
        return fn()
    return contextvars.copy_context().run(run)


def test_a_member_budget_is_saved_and_removed(files):
    budgets.save_user_budget(7, 5, "warn")
    assert budgets.get_user_budgets() == {"7": {"monthly_usd": 5.0, "on_limit": "warn"}}
    budgets.save_user_budget(7, None)
    assert budgets.get_user_budgets() == {}


@pytest.mark.parametrize("amount,mode", [(0, "block"), (-1, "block"), (5, "explode")])
def test_a_member_budget_is_checked(files, amount, mode):
    with pytest.raises(ValidationError):
        budgets.save_user_budget(7, amount, mode)


def test_a_members_spending_counts_across_projects_and_this_month_only(files, home):
    budgets.save_user_budget(7, 1.0)
    last_month = datetime.now().replace(day=1).timestamp() - 86400
    spend(7, "alpha", 5.0, ts=last_month)           # not this month
    spend(8, "alpha", 5.0)                          # another member
    spend(None, "alpha", 5.0)                       # the admin
    spend(7, "alpha", 0.3)
    spend(7, "beta", 0.3)
    s = budgets.user_status(7)
    assert round(s["spent"], 2) == 0.6 and s["state"] == "ok" and s["on_limit"] == "block"
    spend(7, "gamma", 0.3)
    assert budgets.user_status(7)["state"] == "warning"
    spend(7, "gamma", 0.2)
    assert budgets.user_status(7)["state"] == "over"
    assert budgets.user_status(8) is None            # no budget set for them


def test_the_budget_that_blocks_a_call(files, home):
    budgets.save_budget("alpha", 1.0)
    budgets.save_user_budget(7, 1.0)
    spend(8, "alpha", 1.5)                           # someone else used up the project's money
    assert as_user(7, lambda: budgets.blocking("alpha"))["scope"] == "project"
    assert as_user(7, lambda: budgets.blocking("beta")) is None
    spend(7, "beta", 1.5)                            # now their own is used up too
    own = as_user(7, lambda: budgets.blocking("beta"))
    assert own["scope"] == "user" and own["spent"] >= 1.5
    assert as_user(9, lambda: budgets.blocking("beta")) is None          # another member, no budget
    assert budgets.blocking("beta") is None                              # the admin has no member budget


def test_warn_mode_never_blocks_a_member(files, home):
    budgets.save_user_budget(7, 1.0, "warn")
    spend(7, "alpha", 2.0)
    assert budgets.user_status(7)["state"] == "over"
    assert as_user(7, lambda: budgets.blocking("alpha")) is None


def test_the_admin_sets_and_sees_a_members_budget(crew, files):
    admin, anna = crew["admin"], crew["anna"][1]
    r = admin.post(f"/api/team/users/{anna}/budget", json={"monthly_usd": 3, "on_limit": "block"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["budget"]["monthly_usd"] == 3
    rows = {u["name"]: u for u in admin.get("/api/team/users").json()["users"]}
    assert rows["anna"]["budget"]["monthly_usd"] == 3 and rows["anna"]["budget"]["spent"] == 0
    assert rows["bob"]["budget"] is None
    bad = admin.post(f"/api/team/users/{anna}/budget", json={"monthly_usd": 0}, headers=ORIGIN)
    assert bad.status_code == 400
    admin.post(f"/api/team/users/{anna}/budget", json={"monthly_usd": None}, headers=ORIGIN)
    rows = {u["name"]: u for u in admin.get("/api/team/users").json()["users"]}
    assert rows["anna"]["budget"] is None


def test_only_the_admin_sets_budgets_and_only_for_members(crew, files):
    anna, bob = crew["anna"], crew["bob"]
    for who in (anna, bob):
        r = who[0].post(f"/api/team/users/{bob[1]}/budget", json={"monthly_usd": 9}, headers=ORIGIN)
        assert r.status_code == 403
    admin_id = [u["id"] for u in crew["admin"].get("/api/team/users").json()["users"] if u["role"] == "admin"][0]
    r = crew["admin"].post(f"/api/team/users/{admin_id}/budget", json={"monthly_usd": 9}, headers=ORIGIN)
    assert r.status_code == 400
    assert crew["admin"].post("/api/team/users/9999/budget", json={"monthly_usd": 9}, headers=ORIGIN).status_code == 400


def test_a_member_sees_only_their_own_budget(crew, files):
    anna = crew["anna"]
    crew["admin"].post(f"/api/team/users/{anna[1]}/budget", json={"monthly_usd": 2}, headers=ORIGIN)
    spend(anna[1], "alpha", 1.0)
    me = anna[0].get("/api/me").json()
    assert me["budget"]["monthly_usd"] == 2 and round(me["budget"]["spent"], 2) == 1.0
    assert crew["bob"][0].get("/api/me").json().get("budget") is None
    assert "budget" not in crew["admin"].get("/api/me").json()


def test_a_blocked_member_is_told_to_ask_the_admin(app_client, files, home):
    budgets.save_user_budget(7, 1.0)
    spend(7, "alpha", 2.0)
    sent = []

    class Socket:
        pass

    async def fake_send(ws, kind, content="", **kw):
        sent.append((kind, content, kw))

    app_client_send = app_client.send
    app_client.send = fake_send
    try:
        ok = as_user(7, lambda: asyncio.run(app_client.check_budget_before_request(Socket(), {})))
    finally:
        app_client.send = app_client_send
    assert ok is False
    kind, text, kw = sent[0]
    assert kind == "budget" and kw["state"] == "blocked" and "admin" in text and "raise it" not in text
