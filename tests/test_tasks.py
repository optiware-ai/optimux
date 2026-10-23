"""IM-09 (v0.6): one record per request, 👍 / 👎, and the CSV export."""
import csv
import io
import json

import pytest

import optimux_backend.core.analytics as an
import optimux_backend.core.tasks as tasks
import optimux_backend.core.usage as usage
import optimux_backend.main as main
from optimux_backend.core import store
from tests.test_routing_session import Recorder, reply, tool_reply
from tests.ws_helpers import Conversation, collect, make_client

H = {"Authorization": "Bearer test-token"}


def rows(table="tasks"):
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    finally:
        conn.close()


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


# ---- the module


def test_a_record_holds_what_happened():
    t = tasks.new("prompt", "alpha")
    tasks.add_call(t, {"cost": 0.002})
    tasks.add_call(t, {"cost": None})
    tasks.escalate(t, "step limit (8) reached")
    tasks.set_changes(t, {"git": True, "files": [{"added": 3, "removed": 1}, {"added": None, "removed": None}],
                          "more": 2})
    t.update(tier="simple", model="m", outcome="ok", steps=4)
    assert tasks.finish(t, "chat-1")
    r = rows()[0]
    assert (r["session"], r["project"], r["kind"], r["tier"], r["outcome"]) == ("chat-1", "alpha", "prompt", "simple", "ok")
    assert r["calls"] == 2 and r["calls_without_cost"] == 1 and r["cost"] == pytest.approx(0.002)
    assert r["escalated"] == 1 and r["escalation_reason"] == "step limit (8) reached"
    assert (r["files_changed"], r["lines_added"], r["lines_removed"]) == (4, 3, 1)
    assert r["duration_ms"] >= 0 and r["steps"] == 4 and r["rating"] is None


def test_a_request_that_did_nothing_is_not_recorded():
    assert not tasks.finish(tasks.new("prompt"), "chat-1")          # e.g. /simple alone
    assert not tasks.finish(None, "chat-1")
    assert rows() == []


def test_ratings_and_export(tmp_path):
    t = tasks.new("prompt", "alpha")
    t.update(outcome="ok", escalation_reason="=HYPERLINK(1)")
    tasks.finish(t, "chat-1")
    assert tasks.rate(t["id"], 1) == {"id": t["id"], "rating": 1}
    assert tasks.ratings("chat-1") == {t["id"]: 1}
    tasks.rate(t["id"], 0)                                           # cleared
    assert tasks.ratings("chat-1") == {t["id"]: None} and rows()[0]["rated_ts"] is None
    with pytest.raises(tasks.TaskError):
        tasks.rate(t["id"], 5)
    with pytest.raises(tasks.TaskError):
        tasks.rate("nope", 1)
    tasks.rate(t["id"], -1)
    data = list(csv.DictReader(io.StringIO(tasks.export_csv())))
    assert len(data) == 1 and data[0]["id"] == t["id"] and data[0]["rating"] == "-1"
    assert data[0]["escalation_reason"] == "'=HYPERLINK(1)"            # never a formula in a spreadsheet
    assert data[0]["time_utc"].endswith("Z")
    assert tasks.stats()["thumbs_down"] == 1
    assert tasks.delete_all() == 1 and rows() == []


# ---- recorded from the chat


def test_a_chat_request_is_recorded_and_its_calls_linked(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("Hello!")))
    chat = Conversation(client)
    msgs = chat.prompt("say hello")
    done = [m for m in msgs if m["type"] == "task_done"]
    assert len(done) == 1
    r = rows()
    assert len(r) == 1 and r[0]["id"] == done[0]["task_id"]
    t = r[0]
    assert (t["kind"], t["tier"], t["model"], t["outcome"], t["steps"]) == ("prompt", "simple", "simple-model", "ok", 1)
    assert (t["conductor_model"], t["conductor_tier"], t["conductor_plan"]) == ("conductor-model", "simple", 0)
    assert t["calls"] == 2 and t["session"]                         # the Conductor and the Simple model
    assert {u["task_id"] for u in rows("usage")} == {t["id"]}
    # 👍, then the chat reopened shows it
    assert client.post(f"/api/tasks/{t['id']}/rating", json={"rating": 1}, headers=H).json()["rating"] == 1
    loaded = chat.send({"type": "open_session", "id": t["session"]})
    assert [m for m in loaded if m["type"] == "session_loaded"][0]["ratings"] == {t["id"]: 1}
    chat.close()
    assert client.post("/api/tasks/nope/rating", json={"rating": 1}, headers=H).status_code == 404
    assert client.post(f"/api/tasks/{t['id']}/rating", json={"rating": 1}).status_code == 401


def test_a_tier_command_alone_is_not_a_task(client):
    chat = Conversation(client)
    chat.prompt("/complex")
    chat.close()
    assert rows() == []


def test_a_stuck_model_marks_the_request_and_its_calls(mocker, client):
    mocker.patch.object(main, "update_memory", return_value="saved")
    mocker.patch.object(usage, "reference_model", return_value="complex-model")
    mocker.patch.object(usage, "reference_cost", return_value=0.05)
    rec = Recorder(tool_reply("update_markdown_memory", {"file_type": "personal", "content": "x"}),
                   tool_reply("update_markdown_memory", {"file_type": "personal", "content": "x"}),
                   reply("Saved."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("remember x")
    chat.close()
    t = rows()[0]
    assert t["escalated"] == 1 and t["escalation_reason"] == "model repeated the same tool call"
    assert t["model"] == "complex-model" and t["outcome"] == "ok"
    refs = {(u["role"], u["ref_cost"]) for u in rows("usage")}
    assert ("simple", 0) in refs and ("complex", 0.05) in refs     # the stuck calls are pure overhead
    assert ("simple", 0.05) not in refs


def test_approved_and_rejected_commands_are_counted(mocker, client, tmp_path):
    mocker.patch.object(main, "current_workdir", return_value=str(tmp_path))
    mocker.patch("optimux_backend.core.executor.get_active_cwd", return_value=str(tmp_path))
    rec = Recorder(tool_reply("execute_bash", {"command": "echo one"}),
                   tool_reply("execute_bash", {"command": "echo two"}),
                   reply("Done."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "run things", "model": "auto"}))
    for answer in (True, False):
        while json.loads(chat.ws.receive_text())["type"] != "approval_request":
            pass
        chat.ws.send_text(json.dumps({"type": "approval_response", "approved": answer}))
    collect(chat.ws)
    chat.close()
    t = rows()[0]
    assert (t["approved"], t["rejected"], t["steps"]) == (1, 1, 3)


def test_the_user_escalating_marks_the_previous_request(mocker, client):
    mocker.patch.object(usage, "reference_model", return_value="complex-model")
    mocker.patch.object(usage, "reference_cost", return_value=0.05)
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("Weak answer."), reply("Strong answer.")))
    chat = Conversation(client)
    chat.prompt("hard question")
    chat.send({"type": "escalate"})
    chat.close()
    first, second = rows()
    assert first["escalated"] == 1 and first["escalation_reason"] == "the user pressed Escalate"
    assert (second["kind"], second["tier"], second["escalated"]) == ("escalate", "complex", 1)
    by_task = {(u["task_id"], u["role"]): u["ref_cost"] for u in rows("usage")}
    assert by_task[(first["id"], "simple")] == 0 and by_task[(second["id"], "complex")] == 0.05


def test_endpoints(client):
    t = tasks.new("prompt")
    t["outcome"] = "ok"
    tasks.finish(t, "s")
    assert client.get("/api/tasks", headers=H).json()["count"] == 1
    r = client.get("/api/tasks.csv", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and t["id"] in r.text
    assert client.get("/api/tasks.csv").status_code == 401
    assert client.delete("/api/tasks", headers=H).json() == {"deleted": 1}


def test_only_escalations_before_task_records_are_left_in_the_note():
    an.record_event("escalation", "m", "simple", "old")
    assert an.savings(0)["escalations"] == 1
    t = tasks.new("prompt")
    t["outcome"] = "ok"
    tasks.finish(t, "s")
    an.record_event("escalation", "m", "simple", "new, linked to its calls")
    assert an.savings(0)["escalations"] == 1


def test_a_plan_is_two_requests_linked_by_the_plan(mocker, tmp_path):
    from tests import test_plan_runner as pr
    from tests.test_plans import PLAN
    from tests.test_planner import answer
    client = pr.client.__wrapped__(mocker, tmp_path)
    mocker.patch.object(usage, "reference_model", return_value="complex-model")
    mocker.patch.object(usage, "reference_cost", return_value=0.05)
    loop = [pr.call("nope", {"x": 1}), pr.call("nope", {"x": 1})]       # step 1 gets stuck on Simple
    model = pr.Model(answer(json.dumps(PLAN)), *loop, pr.finish("a"), pr.finish("b"), pr.finish("c"), pr.check("ok"))
    chat = Conversation(client)
    pid = pr.make_plan(mocker, chat, model)
    pr.act(chat, pid, "run")
    chat.close()
    proposed, ran = rows()
    assert (proposed["kind"], proposed["plan_id"], proposed["tier"], proposed["outcome"]) == \
        ("prompt", pid, "complex", "plan_waiting")
    assert (ran["kind"], ran["plan_id"], ran["outcome"]) == ("plan_action", pid, "plan_done")
    assert ran["escalated"] == 1 and ran["escalation_reason"].startswith("step 1:")
    step_refs = {(u["role"], u["step"], u["ref_cost"]) for u in rows("usage") if u["task_id"] == ran["id"]}
    assert ("simple", 1, 0) in step_refs and ("simple", 3, 0.05) in step_refs   # only the stuck step's calls


def test_a_request_with_no_known_price_has_an_unknown_cost():
    t = tasks.new("prompt")
    tasks.add_call(t, {"cost": None})
    tasks.finish(t, "s")
    assert rows()[0]["cost"] is None and rows()[0]["calls_without_cost"] == 1
