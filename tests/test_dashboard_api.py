"""Dashboard endpoints: daily usage, project memory, projects overview, system, token rotation."""
import os
import time
from datetime import datetime, timedelta

import pytest

import optimux_backend.main as main
import optimux_backend.core.memory_manager as mm
from optimux_backend.core import budgets, sessions, usage
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_daily_series_groups_by_local_day_and_model():
    today = datetime.now().replace(hour=12)
    yesterday = today - timedelta(days=1)
    usage.insert({"ts": today.timestamp(), "model": "g", "project": "alpha", "cost": 0.1, "prompt_tokens": 10})
    usage.insert({"ts": today.timestamp(), "model": "g", "project": "alpha", "cost": 0.2, "prompt_tokens": 5})
    usage.insert({"ts": today.timestamp(), "model": "c", "project": "beta", "cost": None})
    usage.insert({"ts": yesterday.timestamp(), "model": "g", "project": "alpha", "cost": 1.0})
    usage.insert({"ts": (today - timedelta(days=40)).timestamp(), "model": "g", "project": "alpha", "cost": 9.0})
    rows = usage.daily(30)
    by = {(r["day"], r["model"]): r for r in rows}
    t, y = today.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")
    assert by[(t, "g")]["calls"] == 2 and by[(t, "g")]["cost"] == pytest.approx(0.3) and by[(t, "g")]["prompt_tokens"] == 15
    assert by[(t, "c")]["calls_without_cost"] == 1 and by[(y, "g")]["cost"] == 1.0
    assert len(rows) == 3                                   # the 40-day-old call is outside the window
    assert {r["model"] for r in usage.daily(30, project="beta")} == {"c"}


def test_daily_endpoint(client):
    usage.insert({"model": "g", "cost": 0.5})
    r = client.get("/api/usage/daily?days=7", headers=H)
    assert r.status_code == 200 and r.json()[0]["cost"] == 0.5
    assert client.get("/api/usage/daily").status_code == 401


def test_project_memory_round_trip_and_validation(mocker, client, tmp_path):
    mocker.patch.object(mm, "PROJECTS_DIR", str(tmp_path / "projects"))
    mocker.patch.object(mm, "GLOBAL_DIR", str(tmp_path))
    mocker.patch.object(mm, "PERSONAL_MD", str(tmp_path / "personal.md"))
    mocker.patch.object(mm, "PROFESSIONAL_MD", str(tmp_path / "professional.md"))
    r = client.get("/api/memory/project/alpha", headers=H).json()
    assert r["exists"] is False and r["content"].startswith("# Project Memory")
    assert client.post("/api/memory/project/alpha", json={"content": "# Alpha\nuse pytest"}, headers=H).status_code == 200
    assert client.get("/api/memory/project/alpha", headers=H).json() == {"content": "# Alpha\nuse pytest", "exists": True}
    assert open(tmp_path / "projects" / "alpha.md").read() == "# Alpha\nuse pytest"
    assert client.get("/api/memory/project/..", headers=H).status_code != 200   # normalized away by the URL layer
    assert client.post("/api/memory/project/.hidden", json={"content": "x"}, headers=H).status_code == 400


def test_projects_overview(mocker, client, tmp_path):
    root = tmp_path / "root"
    (root / "alpha").mkdir(parents=True)
    (root / "empty").mkdir()
    mocker.patch.object(main, "get_settings", return_value={"projects_root": str(root), "active_project": "alpha"})
    usage.insert({"project": "alpha", "cost": 0.4})
    usage.insert({"project": "gone", "cost": 1.0})          # folder removed, history kept
    sessions.create("alpha", "chat one")
    budgets.save_budget("alpha", 2.0)
    rows = {r["project"]: r for r in client.get("/api/projects/overview", headers=H).json()}
    assert set(rows) == {"alpha", "empty", "gone"}
    a = rows["alpha"]
    assert a["active"] and a["exists"] and a["chats"] == 1 and a["month_cost"] == pytest.approx(0.4)
    assert a["budget"]["monthly_usd"] == 2.0 and a["last_activity"]
    assert rows["gone"]["exists"] is False and rows["gone"]["all_time_cost"] == 1.0
    assert rows["empty"]["calls"] == 0 and rows["empty"]["last_activity"] is None


def test_system_info(client):
    r = client.get("/api/system", headers=H).json()
    assert r["litellm"] and r["python"] and r["storage"]["path"].endswith("optimux.db")


def test_token_rotation(mocker, client, tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=x\nOPTIMUX_TOKEN=test-token\n")
    from optimux_backend.core import security
    real = security.rotate_token
    mocker.patch.object(main, "rotate_token", side_effect=lambda: real(str(env)))
    assert client.post("/api/system/rotate-token", json={"current": "wrong"}, headers=H).status_code == 400
    new = client.post("/api/system/rotate-token", json={"current": "test-token"}, headers=H).json()["token"]
    assert new != "test-token" and env.read_text() == f"GEMINI_API_KEY=x\nOPTIMUX_TOKEN={new}\n"
    assert client.get("/api/settings", headers=H).status_code == 401        # old token no longer works
    assert client.get("/api/settings", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    os.environ["OPTIMUX_TOKEN"] = "test-token"


def test_budget_note_once_per_server_not_per_connection(mocker, client):
    from tests.ws_helpers import Conversation
    from tests.test_routing_session import Recorder, reply
    mocker.patch.object(main, "active_project", return_value="alpha")
    budgets.save_budget("alpha", 1.0)
    usage.insert({"project": "alpha", "cost": 0.85})
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    first = [m for m in Conversation(client).prompt("one") if m["type"] == "budget"]
    second = [m for m in Conversation(client).prompt("two") if m["type"] == "budget"]   # e.g. after a reload
    assert len(first) == 1 and second == []
