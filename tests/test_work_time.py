"""v0.8: working time per request, kept in the task records (ported from OmniLatch p94).
Each approval counts 2 s, whatever the real wait; model time comes from the request's
usage rows (task_id)."""
import json

import optimux_backend.core.analytics as an
import optimux_backend.core.tasks as tasks
from optimux_backend.core import store
from tests.test_files import _tool, _until, client, proj  # noqa: F401  (fixtures)
from tests.test_routing_session import Recorder, reply
from tests.test_tasks import rows
from tests.ws_helpers import Conversation, collect, make_client


def task_row(tid, ts, project, duration_ms, wait_ms, waits):
    conn = store.connect()
    with conn:
        conn.execute("INSERT INTO tasks (id, ts, project, kind, duration_ms, wait_ms, waits) "
                     "VALUES (?, ?, ?, 'prompt', ?, ?, ?)", (tid, ts, project, duration_ms, wait_ms, waits))
    conn.close()


def usage_row(ts, task_id, ms, project="demo", model="haiku"):
    conn = store.connect()
    with conn:
        conn.execute("INSERT INTO usage (ts, session, project, role, model, duration_ms, task_id) "
                     "VALUES (?, 's', ?, 'simple', ?, ?, ?)", (ts, project, model, ms, task_id))
    conn.close()


def test_an_approval_counts_two_seconds_whatever_the_wait():
    # 20 minutes from the message to the end, 15 of them waiting for one approval
    r = {"duration_ms": 1_200_000, "wait_ms": 900_000, "waits": 1, "model_ms": 120_000}
    assert tasks.counted(1_200_000, 900_000, 1) == 300_000 + 2000
    p = tasks.parts(r)
    assert p == {"counted_ms": 302_000, "model_ms": 120_000, "approval_ms": 2000, "other_ms": 180_000}
    assert p["model_ms"] + p["approval_ms"] + p["other_ms"] == p["counted_ms"]


def test_parts_never_go_past_the_counted_time():
    p = tasks.parts({"duration_ms": 5000, "wait_ms": 2000, "waits": 1, "model_ms": 9000})
    assert p == {"counted_ms": 5000, "model_ms": 3000, "approval_ms": 2000, "other_ms": 0}


def test_analytics_has_the_time_per_day_and_per_project():
    now = 1_800_000_000.0
    task_row("t1", now - 110, "demo", 20_000, 0, 0)
    usage_row(now - 100, "t1", 4000)
    task_row("t2", now - 60, "other", 60_000, 30_000, 2)
    usage_row(now - 50, "t2", 6000, project="other")
    usage_row(now - 40, None, 1000)                         # a call outside any request (memory update)
    task_row("t0", now - 200, "demo", 99_000, None, None)   # a record from before v0.8: no working time
    t = an.analytics(days=0, now=now)["time"]
    assert t["totals"]["requests"] == 2 and t["totals"]["waits"] == 2 and t["totals"]["wait_ms"] == 30_000
    assert t["totals"]["counted_ms"] == 20_000 + 30_000 + 4000 and t["approval_ms"] == 2000
    assert t["totals"]["model_ms"] == 10_000 and t["model_calls_ms"] == 11_000
    assert [p["project"] for p in t["projects"]] == ["other", "demo"]
    assert sum(b["counted_ms"] for b in t["timeline"]) == t["totals"]["counted_ms"]
    assert t["recorded_since"] == now - 110 and t["avg_request_ms"] == 27_000
    one = an.analytics(days=0, project="demo", now=now)["time"]
    assert one["totals"]["counted_ms"] == 20_000 and one["projects"] == []


def test_the_workspace_has_its_own_row():
    task_row("w1", 1_800_000_000.0, None, 5000, 0, 0)
    assert [p["project"] for p in an.analytics(days=0, now=1_800_000_100.0)["time"]["projects"]] == ["(no project)"]
    assert an.analytics(days=0, project="(no project)", now=1_800_000_100.0)["time"]["totals"]["requests"] == 1


def test_a_request_from_the_chat_is_timed(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("hello")))
    Conversation(client).prompt("say hi")
    [t] = rows()
    assert t["wait_ms"] == 0 and t["waits"] == 0 and t["duration_ms"] >= 0


def test_the_approval_wait_is_kept_apart(mocker, client, proj):  # noqa: F811
    import time
    mocker.patch("optimux_backend.core.executor.get_active_cwd", return_value=str(proj))
    responses = iter([_tool("execute_bash", {"command": "true"}), reply("Done.")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "run true", "model": "auto"}))
    _until(chat.ws, "approval_request")
    time.sleep(0.3)                                          # the user thinks
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    collect(chat.ws)
    chat.close()
    [t] = rows()
    assert t["waits"] == 1 and 250 <= t["wait_ms"] <= t["duration_ms"]
    assert tasks.counted(t["duration_ms"], t["wait_ms"], t["waits"]) < t["duration_ms"] - 250 + 2000 + 50
