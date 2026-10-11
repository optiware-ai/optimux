"""Monthly budgets per project: storage, status, blocking, warnings, pausing mid-turn."""
import json
import time
from datetime import datetime

import pytest

import optimux_backend.core.budgets as budgets
import optimux_backend.core.usage as usage
import optimux_backend.main as main
from optimux_backend.core.security import ValidationError
from tests.ws_helpers import Conversation, chunk, fake_stream, make_client
from tests.test_routing_session import Recorder, reply

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def store(mocker, tmp_path):
    mocker.patch.object(budgets, "BUDGETS_FILE", str(tmp_path / "budgets.json"))
    mocker.patch.object(usage, "USAGE_FILE", str(tmp_path / "usage.jsonl"))
    mocker.patch.object(usage, "_custom_prices", return_value=None)


def spend(project, cost, ts=None):
    usage.insert({"ts": ts or time.time(), "project": project, "role": "simple", "model": "m",
                  "prompt_tokens": 1, "completion_tokens": 1, "cost": cost})


def test_save_and_remove(store):
    budgets.save_budget("alpha", 5, "warn")
    assert budgets.get_budgets() == {"alpha": {"monthly_usd": 5.0, "on_limit": "warn"}}
    budgets.save_budget("alpha", None)
    assert budgets.get_budgets() == {}


@pytest.mark.parametrize("amount,mode", [(0, "block"), (-1, "block"), (5, "explode")])
def test_validation(store, amount, mode):
    with pytest.raises(ValidationError):
        budgets.save_budget("alpha", amount, mode)


def test_status_states_and_month_only(store):
    budgets.save_budget("alpha", 1.0)
    last_month = datetime.now().replace(day=1).timestamp() - 86400
    spend("alpha", 5.0, ts=last_month)        # not this month
    spend("beta", 5.0)                        # another project
    spend("alpha", 0.5)
    assert budgets.status("alpha")["state"] == "ok"
    spend("alpha", 0.35)
    s = budgets.status("alpha")
    assert s["state"] == "warning" and s["spent"] == pytest.approx(0.85)
    spend("alpha", 0.2)
    assert budgets.status("alpha")["state"] == "over"
    assert budgets.blocks("alpha") is not None
    assert budgets.status("beta") is None


def test_unknown_prices_are_reported(store):
    budgets.save_budget("alpha", 1.0)
    spend("alpha", None)
    assert budgets.status("alpha")["calls_without_cost"] == 1


def test_warn_mode_never_blocks(store):
    budgets.save_budget("alpha", 1.0, "warn")
    spend("alpha", 2.0)
    assert budgets.status("alpha")["state"] == "over" and budgets.blocks("alpha") is None


@pytest.fixture
def client(mocker, tmp_path):
    c = make_client(mocker, tmp_path)
    mocker.patch.object(main, "active_project", return_value="alpha")
    return c


def budget_msgs(msgs):
    return [m for m in msgs if m["type"] == "budget"]


def test_blocked_request_makes_no_call(mocker, client):
    budgets.save_budget("alpha", 1.0)
    spend("alpha", 1.5)
    call = mocker.patch("litellm.acompletion")
    msgs = Conversation(client).prompt("hello")
    b = budget_msgs(msgs)
    assert b and b[0]["state"] == "blocked" and "blocked" in b[0]["content"]
    call.assert_not_called()
    assert main._test_conductor.call_count == 0


def test_warning_note_once_per_level(mocker, client):
    budgets.save_budget("alpha", 1.0)
    spend("alpha", 0.9)
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    chat = Conversation(client)
    first = budget_msgs(chat.prompt("one"))
    second = budget_msgs(chat.prompt("two"))
    assert len(first) == 1 and first[0]["state"] == "warning" and "90%" in first[0]["content"]
    assert second == []


def test_turn_pauses_when_budget_runs_out_mid_turn(mocker, client):
    budgets.save_budget("alpha", 1.0)
    mocker.patch.object(main, "update_memory", return_value="saved")

    def first_call(**kw):
        spend("alpha", 2.0)  # this call pushes the project over its budget
        return fake_stream([chunk(tool_call=("update_markdown_memory", {"file_type": "personal", "content": "x"})),
                            chunk(empty=True, usage_obj={"prompt_tokens": 1, "completion_tokens": 1})])

    call = mocker.patch("litellm.acompletion", side_effect=first_call)
    msgs = Conversation(client).prompt("long task")
    assert call.call_count == 1
    b = budget_msgs(msgs)
    assert b and b[-1]["state"] == "paused"
    assert not any(m["type"] == "error" for m in msgs)


def test_budget_api(client):
    r = client.post("/api/budgets", json={"project": "alpha", "monthly_usd": 5, "on_limit": "warn"}, headers=H)
    assert r.status_code == 200 and r.json()["budget"]["monthly_usd"] == 5
    assert client.get("/api/budgets", headers=H).json()["alpha"]["on_limit"] == "warn"
    assert client.post("/api/budgets", json={"project": "alpha", "monthly_usd": 0}, headers=H).status_code == 400
    client.post("/api/budgets", json={"project": "alpha", "monthly_usd": None}, headers=H)
    assert client.get("/api/budgets", headers=H).json() == {}
    assert client.get("/api/budgets").status_code == 401


def test_summary_has_this_month(store):
    spend("alpha", 0.1)
    assert usage.summary()["this_month"]["total"]["calls"] == 1
