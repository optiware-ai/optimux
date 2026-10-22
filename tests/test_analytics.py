"""Dashboard → Performance: cost and performance per model (v0.5)."""
import time
from datetime import datetime

import pytest

import optimux_backend.core.analytics as an
import optimux_backend.core.llm_client as llm
import optimux_backend.core.usage as usage
import optimux_backend.main as main
from optimux_backend.core import store
from tests.test_routing_session import Recorder, notes, overloaded, reply, tool_reply
from tests.ws_helpers import Conversation, make_client

H = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 10, 20, 15, 0).timestamp()
DAY = 86400


def add(model, cost=0.01, ms=1000, out=100, prompt=1000, cached=0, thinking=0, project="alpha",
        role="simple", ts=NOW - 3600):
    usage.insert({"ts": ts, "session": "s", "project": project, "role": role, "model": model,
                  "prompt_tokens": prompt, "completion_tokens": out, "cached_tokens": cached,
                  "reasoning_tokens": thinking, "cost": cost, "duration_ms": ms})


def events():
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM model_events ORDER BY id")]
    finally:
        conn.close()


def test_percentile_interpolates():
    assert an.percentile([], 0.5) is None
    assert an.percentile([5], 0.9) == 5
    assert an.percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert an.percentile([10, 20, 30, 40, 50], 0.1) == pytest.approx(14)
    assert an.percentile([3, 1, 2], 0.9) == pytest.approx(2.8)        # unsorted input


def test_per_model_figures():
    for ms in (500, 1000, 1500, 2000, 10000):
        add("cheap", cost=0.001, ms=ms, out=200, prompt=1000, cached=600)
    add("strong", cost=0.05, ms=4000, out=500, prompt=2000, thinking=300, role="complex")
    add("strong", cost=0.03, ms=0, out=10, role="complex")             # untimed, too short for speed
    an.record_event("error", "strong", "complex", "transient", project="alpha")
    an.record_event("escalation", "cheap", "simple", "step limit (8) reached", project="alpha")
    r = an.analytics(30, now=NOW)
    rows = {m["model"]: m for m in r["models"]}
    assert [m["model"] for m in r["models"]] == ["strong", "cheap"]     # most spent first
    c, s = rows["cheap"], rows["strong"]
    assert c["calls"] == 5 and c["cost"] == pytest.approx(0.005)
    assert c["cost_per_call"] == pytest.approx(0.001)
    assert c["cost_per_1k_out"] == pytest.approx(0.005)                 # $0.005 for 1,000 output tokens
    assert c["p50_ms"] == 1500 and c["p10_ms"] == pytest.approx(700) and c["p90_ms"] == pytest.approx(6800)
    assert c["speed_tps"] == pytest.approx(133.33, rel=1e-3)            # 200 tokens in 1.5 s
    assert c["cache_share"] == pytest.approx(0.6)
    assert c["escalations"] == 1 and c["escalation_rate"] == pytest.approx(0.2)
    assert c["reliability"] == 1
    assert s["errors"] == 1 and s["error_kinds"] == {"transient": 1}
    assert s["reliability"] == pytest.approx(2 / 3)
    assert s["timed_calls"] == 1 and s["speed_calls"] == 1 and s["roles"] == {"complex": 2}
    assert s["share"] == pytest.approx(0.08 / 0.085)
    t = r["totals"]
    assert t["calls"] == 7 and t["errors"] == 1 and t["escalations"] == 1
    assert t["cost"] == pytest.approx(0.085) and t["reliability"] == pytest.approx(7 / 8)
    assert r["events_since"] is not None and r["color_order"] == ["strong", "cheap"]
    assert r["projects"] == ["alpha"]


def test_unknown_prices_are_not_counted_as_free():
    add("priced", cost=0.02)
    usage.insert({"ts": NOW - 60, "project": "alpha", "role": "simple", "model": "custom", "prompt_tokens": 10,
                  "completion_tokens": 50, "cost": None, "duration_ms": 900})
    rows = {m["model"]: m for m in an.analytics(30, now=NOW)["models"]}
    assert rows["custom"]["calls_without_cost"] == 1
    assert rows["custom"]["cost_per_call"] is None and rows["custom"]["cost_per_1k_out"] is None


def test_period_project_filter_and_timeline():
    add("m1", cost=0.01, ts=NOW - 3600, project="alpha")
    add("m2", cost=0.02, ts=NOW - 2 * DAY, project="beta")
    add("m1", cost=0.04, ts=NOW - 40 * DAY, project="alpha")             # outside 30 days
    r = an.analytics(7, now=NOW)
    assert r["totals"]["calls"] == 2 and not r["weekly"]
    assert len(r["timeline"]) == 7                                       # zero-filled days
    today = r["timeline"][-1]
    assert today["bucket"] == "2026-10-20" and today["models"]["m1"]["cost"] == pytest.approx(0.01)
    assert r["timeline"][-3]["models"]["m2"]["calls"] == 1
    assert set(r["matrix"]["projects"]) == {"alpha", "beta"} and r["matrix"]["projects"][0] == "beta"
    only = an.analytics(7, "alpha", now=NOW)
    assert only["totals"]["calls"] == 1 and only["matrix"] is None
    everything = an.analytics(0, now=NOW)
    assert everything["totals"]["calls"] == 3


def test_long_ranges_group_by_week():
    add("m", ts=NOW - 200 * DAY)
    add("m", ts=NOW - 3600)
    r = an.analytics(0, now=NOW)
    assert r["weekly"] is True
    keys = [b["bucket"] for b in r["timeline"]]
    assert all(datetime.fromisoformat(k).weekday() == 0 for k in keys)   # weeks start on Monday
    assert sum(c["calls"] for b in r["timeline"] for c in b["models"].values()) == 2


def test_empty_database():
    r = an.analytics(30, now=NOW)
    assert r["models"] == [] and r["totals"]["calls"] == 0 and r["totals"]["reliability"] is None
    assert r["events_since"] is None and len(r["timeline"]) == 30


def test_record_event_never_raises(mocker):
    mocker.patch.object(store, "connect", side_effect=OSError("disk full"))
    an.record_event("error", "m", "simple", "transient", project="alpha")    # no exception
    an.record_event("error", "", "simple")                                   # no model: ignored


def test_events_default_to_the_active_project(mocker):
    mocker.patch("optimux_backend.core.config_manager.get_settings", return_value={"active_project": "gamma"})
    an.record_event("error", "m", "simple", "quota")
    assert events()[0]["project"] == "gamma"


# ---- recording points


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_failed_calls_and_escalations_are_recorded(mocker, client):
    rec = Recorder(overloaded(), overloaded(), reply("answer from complex"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    Conversation(client).prompt("explain asyncio")
    ev = events()
    # both failed attempts count; a provider fallback is not an escalation for difficulty
    assert [(e["kind"], e["model"], e["role"], e["detail"]) for e in ev] == \
        [("error", "simple-model", "simple", "transient")] * 2


def test_a_stuck_model_records_an_escalation(mocker, client):
    mocker.patch.object(main, "update_memory", return_value="saved")
    rec = Recorder(tool_reply("update_markdown_memory", {"file_type": "personal", "content": "x"}),
                   tool_reply("update_markdown_memory", {"file_type": "personal", "content": "x"}),
                   reply("Saved."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("remember x")
    assert any("Escalated to complex-model" in n for n in notes(msgs))
    ev = events()
    assert len(ev) == 1 and ev[0]["kind"] == "escalation" and ev[0]["model"] == "simple-model"
    assert ev[0]["detail"] == "model repeated the same tool call"


@pytest.mark.asyncio
async def test_a_failed_conductor_is_recorded(mocker):
    mocker.patch.object(llm, "get_routing_config", return_value={"conductor_model": "test-conductor"})
    mocker.patch.object(llm, "acompletion", side_effect=TimeoutError("slow"))
    await llm.route_model_with_usage("hi")
    ev = events()
    assert ev and ev[0]["model"] == "test-conductor" and ev[0]["role"] == "conductor" and ev[0]["kind"] == "error"


def test_analytics_endpoint(client):
    add("m1", cost=0.01, ts=time.time() - 60)
    r = client.get("/api/analytics?days=7", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["days"] == 7 and body["models"][0]["model"] == "m1"
    assert client.get("/api/analytics?days=0&project=alpha", headers=H).json()["matrix"] is None
    assert client.get("/api/analytics").status_code == 401
