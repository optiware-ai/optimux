"""v0.4 plan mode, commit 3: running a plan step by step."""
import asyncio
import json

import pytest

import optimux_backend.main as main
from optimux_backend.core import agents, plans, store
from tests.test_plans import PLAN
from tests.test_planner import answer, conductor_says, notes
from tests.ws_helpers import Conversation, chunk, collect, fake_stream, make_client

DONE = {"prompt_tokens": 10, "completion_tokens": 2}


def say(text):
    return fake_stream([chunk(text), chunk(empty=True, usage_obj=DONE)])


def call(name, args):
    return fake_stream([chunk(tool_call=(name, args)), chunk(empty=True, usage_obj=DONE)])


def finish(result="ok", status="done", details=None):
    args = {"status": status, "result": result}
    if details:
        args["details"] = details
    return call("finish_step", args)


async def hanging():
    yield chunk("working")
    await asyncio.sleep(60)
    yield chunk(empty=True, usage_obj=DONE)


class Model:
    """Fake litellm.acompletion: the planner's answer, then the queued step replies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, **kw):
        self.calls.append({**kw, "messages": json.loads(json.dumps(kw["messages"], default=str))})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def step_calls(self):
        return [c for c in self.calls if c.get("stream")]


@pytest.fixture
def client(mocker, tmp_path):
    c = make_client(mocker, tmp_path)
    real = main.get_settings
    mode = {"plan_mode": "ask", "active_project": "alpha"}
    mocker.patch.object(main, "get_settings", lambda: {**real(), **mode})
    mocker.patch.object(main, "plan_mode", lambda: mode["plan_mode"])
    c.mode = mode
    return c


def make_plan(mocker, chat, model):
    """Send a big task; the fake planner answers with PLAN. Returns the plan id."""
    conductor_says(mocker, "complex", True)
    mocker.patch("litellm.acompletion", side_effect=model)
    msgs = chat.prompt("write tests for the parser")
    return [m for m in msgs if m["type"] == "plan"][-1]["plan"]["id"]


def act(chat, pid, action):
    return chat.send({"type": "plan_action", "action": action, "plan_id": pid})


def test_ask_first_waits_for_run_then_runs_every_step(mocker, client):
    model = Model(answer(json.dumps(PLAN)),
                  finish("parse() and tokenize() are public."),
                  finish("Three cases chosen.", details="1. empty input\n2. nesting\n3. bad token"),
                  say("I wrote tests/test_parser.py with 3 tests."),
                  say("All 3 tests pass. Step 1 listed the functions, step 2 chose the cases, step 3 wrote them."))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    assert plans.get(pid)["status"] == "waiting" and not model.step_calls()       # nothing ran yet
    msgs = act(chat, pid, "run")
    chat.close()

    steps = model.step_calls()
    assert [c["model"] for c in steps] == ["simple-model", "complex-model", "simple-model", "simple-model"]
    # every step starts fresh: system prompt + one user message, never the chat
    assert all(len(c["messages"]) == 2 for c in steps)
    card3 = steps[2]["messages"][1]["content"]
    assert "Your step: 3 of 3: Write the tests" in card3
    assert "- Step 1 (List the functions): parse() and tokenize() are public." in card3
    assert "Details from step 2 (Choose the tricky cases):\n1. empty input" in card3
    assert "finish_step" in [t["function"]["name"] for t in steps[0]["tools"]]
    assert "finish_step" not in [t["function"]["name"] for t in steps[3]["tools"]]       # the final check
    assert "Final check: pytest -q passes" in steps[3]["messages"][1]["content"]
    assert not any("create_project" == t["function"]["name"] for c in steps for t in c["tools"])

    p = plans.get(pid)
    assert p["status"] == "done" and [s["status"] for s in p["steps"]] == ["done"] * 3
    assert p["steps"][1]["details"].startswith("1. empty input")
    assert p["steps"][2]["result"] == "I wrote tests/test_parser.py with 3 tests."            # plain text end
    assert [s["calls"] for s in p["steps"]] == [1, 1, 1] and p["check_calls"] == 1
    tokens = "".join(m.get("content", "") for m in msgs if m["type"] == "token")
    assert "All 3 tests pass" in tokens

    conn = store.connect()
    rows = [tuple(r) for r in conn.execute(
        "SELECT role, step FROM usage WHERE plan_id = ? AND role != 'planner' ORDER BY id", (pid,))]
    conn.close()
    assert rows == [("simple", 1), ("complex", 2), ("simple", 3), ("simple", plans.CHECK_STEP)]


def test_history_keeps_the_record_and_reply_not_the_steps(mocker, client):
    model = Model(answer(json.dumps(PLAN)), finish("a"), finish("b"), finish("c"), say("Done: all pass."),
                  say("Sure."))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    act(chat, pid, "run")
    conductor_says(mocker, "simple", False)
    chat.prompt("thanks, what changed?")
    chat.close()
    follow_up = model.calls[-1]["messages"]
    texts = [m.get("content") for m in follow_up if m["role"] == "assistant"]
    assert len(texts) == 1 and texts[0].startswith("[Plan] Tests for src/parser.py, run with pytest (done)")
    assert "1. List the functions [simple, simple-model, done]" in texts[0] and texts[0].endswith("Done: all pass.")
    assert not any(m["role"] == "tool" for m in follow_up)


def test_a_blocked_step_pauses_and_can_be_tried_again(mocker, client):
    model = Model(answer(json.dumps(PLAN)), finish("src/parser.py doesn't exist.", status="blocked"),
                  finish("Found it in lib/parser.py."), finish("b"), finish("c"), say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    msgs = act(chat, pid, "run")
    p = plans.get(pid)
    assert p["status"] == "paused" and p["steps"][0]["status"] == "blocked"
    assert "Step 1 is blocked: src/parser.py doesn't exist." in p["note"]
    assert any(n.startswith("Plan paused.") for n in notes(msgs))
    act(chat, pid, "resume")
    chat.close()
    retry = model.step_calls()[1]
    assert retry["model"] == "simple-model"                  # a blocked step retries on its own tier
    assert "An earlier try of this step ended with: src/parser.py doesn't exist." in retry["messages"][1]["content"]
    assert plans.get(pid)["status"] == "done"


def test_skip_moves_on_and_the_next_steps_know(mocker, client):
    model = Model(answer(json.dumps(PLAN)), finish("can't", status="blocked"), finish("b"), finish("c"), say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    act(chat, pid, "run")
    act(chat, pid, "skip")
    chat.close()
    p = plans.get(pid)
    assert [s["status"] for s in p["steps"]] == ["skipped", "done", "done"] and p["status"] == "done"
    assert "- Step 1 (List the functions): skipped by the user." in model.step_calls()[1]["messages"][1]["content"]


def test_a_stuck_simple_step_escalates_that_step_only(mocker, client):
    loop = [call("nope", {"x": 1}), call("nope", {"x": 1})]       # the same call twice: aborted
    model = Model(answer(json.dumps(PLAN)), *loop, finish("done on complex"), finish("b"), finish("c"), say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    msgs = act(chat, pid, "run")
    chat.close()
    models = [c["model"] for c in model.step_calls()]
    assert models == ["simple-model", "simple-model", "complex-model", "complex-model", "simple-model", "simple-model"]
    s1 = plans.get(pid)["steps"][0]
    assert s1["escalated"] is True and s1["model"] == "complex-model" and s1["status"] == "done"
    assert any(n.startswith("Escalated this step to complex-model") for n in notes(msgs))
    # the escalation continued from the step's own messages
    assert len(model.step_calls()[2]["messages"]) > 2


def test_stuck_after_escalation_pauses_and_retries_on_complex(mocker, client):
    def loop():
        return [call("nope", {"x": 1}), call("nope", {"x": 1})]     # fresh streams each time
    model = Model(answer(json.dumps(PLAN)), *loop(), *loop(), finish("now it works"), finish("b"), finish("c"),
                  say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    act(chat, pid, "run")
    p = plans.get(pid)
    assert p["status"] == "paused" and "Step 1 got stuck (the model repeated the same tool call)" in p["note"]
    act(chat, pid, "resume")
    chat.close()
    assert model.step_calls()[4]["model"] == "complex-model"
    assert plans.get(pid)["status"] == "done"


def test_stop_pauses_at_the_step_and_resume_redoes_it(mocker, client):
    model = Model(answer(json.dumps(PLAN)), finish("a"), hanging(), finish("b again"), finish("c"), say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    chat.ws.send_text(json.dumps({"type": "plan_action", "action": "run", "plan_id": pid}))
    while json.loads(chat.ws.receive_text())["type"] != "token":
        pass
    chat.ws.send_text(json.dumps({"type": "stop"}))
    collect(chat.ws)
    p = plans.get(pid)
    assert p["status"] == "paused" and p["note"] == "Stopped by you at step 2. Resume to do it again."
    assert [s["status"] for s in p["steps"]] == ["done", "pending", "pending"]
    act(chat, pid, "resume")
    chat.close()
    assert plans.get(pid)["status"] == "done" and len(model.step_calls()) == 5


def test_a_block_budget_pauses_the_plan(mocker, client):
    model = Model(answer(json.dumps(PLAN)), finish("a"), finish("b"), finish("c"), say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    status = {"project": "alpha", "spent": 5.0, "monthly_usd": 5.0, "ratio": 1.0, "calls_without_cost": 0,
              "state": "over", "on_limit": "block"}
    blocks = mocker.patch.object(main.budgets, "blocks", side_effect=[None, status])
    msgs = act(chat, pid, "run")
    p = plans.get(pid)
    assert p["status"] == "paused" and "budget is used up" in p["note"]
    assert [s["status"] for s in p["steps"]] == ["done", "pending", "pending"]
    assert any(m["type"] == "budget" and m.get("state") == "paused" for m in msgs)
    blocks.side_effect = None
    blocks.return_value = None
    act(chat, pid, "resume")
    chat.close()
    assert plans.get(pid)["status"] == "done"


def test_a_provider_failure_falls_back_for_that_step(mocker, client):
    import litellm
    down = litellm.ServiceUnavailableError(message="overloaded", llm_provider="x", model="simple-model")
    mocker.patch.object(main.asyncio, "sleep", side_effect=lambda *_: asyncio.sleep(0))
    model = Model(answer(json.dumps(PLAN)), down, down, finish("a on complex"), finish("b"), finish("c"), say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    msgs = act(chat, pid, "run")
    chat.close()
    p = plans.get(pid)
    assert p["status"] == "done" and p["steps"][0]["model"] == "complex-model"
    assert any(n.startswith("Fell back to complex-model") for n in notes(msgs))


def test_cancel_and_actions_that_dont_apply(mocker, client):
    model = Model(answer(json.dumps(PLAN)))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    assert any("This plan is waiting, so Resume doesn't apply now." in n for n in notes(act(chat, pid, "resume")))
    act(chat, pid, "cancel")
    assert plans.get(pid)["status"] == "cancelled"
    assert any("This plan is cancelled, so Run doesn't apply now." in n for n in notes(act(chat, pid, "run")))
    errors = [m["content"] for m in act(chat, "AAAAAAAAAAAAAAAA", "run") if m["type"] == "error"]
    chat.close()
    assert errors == ["That plan no longer exists."]
    assert not model.step_calls()


def test_a_plan_of_another_chat_is_refused(mocker, client):
    other = plans.create("someone-elses-chat", "alpha", PLAN)
    chat = Conversation(client)
    mocker.patch("litellm.acompletion", side_effect=Model(say("hi")))
    chat.prompt("hello")
    msgs = act(chat, other["id"], "run")
    chat.close()
    assert [m["content"] for m in msgs if m["type"] == "error"] == [
        "That plan belongs to another chat; open that chat to use it."]
    assert plans.get(other["id"])["status"] == "waiting"


def test_automatic_runs_the_plan_at_once(mocker, client):
    client.mode["plan_mode"] = "auto"
    model = Model(answer(json.dumps(PLAN)), finish("a"), finish("b"), finish("c"), say("All done."))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    chat.close()
    assert plans.get(pid)["status"] == "done" and len(model.step_calls()) == 4


def test_without_a_check_optimux_writes_the_summary(mocker, client):
    no_check = dict(PLAN, check="")
    model = Model(answer(json.dumps(no_check)), finish("listed"), finish("chosen"), finish("written"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    msgs = act(chat, pid, "run")
    chat.close()
    assert len(model.step_calls()) == 3                                 # no model call for the summary
    tokens = "".join(m.get("content", "") for m in msgs if m["type"] == "token")
    assert tokens.startswith("Plan done: Tests for src/parser.py") and "3. Write the tests: written" in tokens


def test_reopening_a_chat_sends_the_plans_as_they_are_now(mocker, client):
    model = Model(answer(json.dumps(PLAN)))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    act(chat, pid, "cancel")
    sid = plans.get(pid)["session"]
    loaded = [m for m in chat.send({"type": "open_session", "id": sid}) if m["type"] == "session_loaded"][0]
    chat.close()
    assert [(p["id"], p["status"]) for p in loaded["plans"]] == [(pid, "cancelled")]


# ------------------------------------------------------------------ the pieces

def test_earlier_work_keeps_the_newest_details_when_too_long():
    p = plans.create("c", "", PLAN)
    plans.update_step(p["id"], 1, status="done", result="r1", details="A" * 7000)
    plans.update_step(p["id"], 2, status="done", result="r2", details="B" * 6000)
    plans.update_step(p["id"], 3, status="done", result="r3", details="C" * 6000)
    assert len(plans.get(p["id"])["steps"][0]["details"]) == plans.MAX_DETAILS
    results, details = plans.earlier_work(plans.get(p["id"]), 4)
    assert results[0] == "- Step 1 (List the functions): r1" and len(results) == 3
    assert len(details) == 2 and details[1].startswith("Details from step 3")         # the newest in full
    assert details[0].startswith("Details from step 2") and details[0].endswith("[cut: too long to pass on in full]")
    assert sum(len(d) for d in details) <= plans.MAX_DETAILS_IN_CONTEXT + 100
    assert len(plans.earlier_work(plans.get(p["id"]), 3)[1]) == 2                     # both fit before step 3


def test_last_words_and_the_step_prompt():
    assert plans.last_words("a b c", 2) == "... b c" and plans.last_words("a b", 5) == "a b"
    p = dict(plans.create("c", "", PLAN, request="write tests"), check="")
    text = agents.get_step_prompt(p, p["steps"][0], [], [])
    assert text.startswith("You are doing one step of a larger plan. The user asked: write tests")
    assert "Files: src/parser.py" in text and "Results so far" not in text
    assert "Look over the work of the steps above." in agents.get_step_prompt(p, None, ["- Step 1 (x): y"], [])


def test_a_restart_says_where_the_plan_was_cut():
    p = plans.create("c-restart", "", PLAN)
    plans.set_status(p["id"], "running")
    plans.update_step(p["id"], 2, status="running")
    plans.mark_interrupted()
    assert plans.get(p["id"])["note"] == "Interrupted by a restart at step 2. Resume to do it again."


def test_check_cost_counts_in_the_total():
    p = plans.create("c-cost", "", PLAN)
    plans.add_cost(p["id"], 1, 0.01)
    plans.add_cost(p["id"], plans.CHECK_STEP, 0.002)
    q = plans.get(p["id"])
    assert q["check_calls"] == 1 and plans.total_cost(q) == 0.012


def test_stop_during_the_final_check_says_so(mocker, client):
    model = Model(answer(json.dumps(PLAN)), finish("a"), finish("b"), finish("c"), hanging(), say("All pass."))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    chat.ws.send_text(json.dumps({"type": "plan_action", "action": "run", "plan_id": pid}))
    while json.loads(chat.ws.receive_text())["type"] != "token":
        pass
    chat.ws.send_text(json.dumps({"type": "stop"}))
    collect(chat.ws)
    p = plans.get(pid)
    assert p["status"] == "paused" and p["note"] == "Stopped by you during the final check. Resume to do it again."
    assert [s["status"] for s in p["steps"]] == ["done"] * 3
    act(chat, pid, "resume")                                    # only the check runs again
    chat.close()
    assert plans.get(pid)["status"] == "done" and len(model.step_calls()) == 5


def test_no_model_for_a_tier_pauses_with_the_reason(mocker, client):
    model = Model(answer(json.dumps(PLAN)))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    mocker.patch.object(main, "get_routing_config", return_value={
        "conductor_model": "conductor-model", "simple_model": "", "complex_model": ""})
    act(chat, pid, "run")
    chat.close()
    p = plans.get(pid)
    assert p["status"] == "paused" and p["note"] == "the simple model is not set in Routing. Resume to try again."
    assert p["steps"][0]["status"] == "pending" and not model.step_calls()


# ------------------------------------------------------------------ the plan card (commit 4)

def test_estimate_ranges_by_tier_and_says_what_is_missing():
    p = plans.create("c-est", "", PLAN)                      # 2 simple steps, 1 complex, a check
    est = plans.estimate(p, {"simple": 0.001, "complex": 0.01})
    # low: simple (2+2) + check 1 = 5 x 0.001, complex 1 x 0.01; high: simple 4+4+3 = 11, complex 3
    assert est == {"low": 0.015, "high": 0.041, "missing": []}
    assert plans.estimate(p, {"simple": 0.001, "complex": None}) == {"low": None, "high": None,
                                                                     "missing": ["complex"]}
    plans.update_step(p["id"], 1, status="done")
    plans.update_step(p["id"], 2, status="done")
    rest = plans.estimate(plans.get(p["id"]), {"simple": 0.001, "complex": None})   # only simple left
    assert rest == {"low": 0.003, "high": 0.007, "missing": []}


def test_a_waiting_plan_carries_its_estimate_and_planning_cost(mocker, client):
    mocker.patch.object(main.usage_meter, "recent_average",
                        side_effect=lambda role, n=5: {"avg": {"simple": 0.001, "complex": 0.01}[role], "n": 5,
                                                       "model": role})
    mocker.patch.object(main.usage_meter, "plan_spent", return_value={"calls": 1, "cost": 0.011, "unpriced": 0})
    model = Model(answer(json.dumps(PLAN)))
    chat = Conversation(client)
    conductor_says(mocker, "complex", True)
    mocker.patch("litellm.acompletion", side_effect=model)
    card = [m for m in chat.prompt("write tests for the parser") if m["type"] == "plan"][-1]["plan"]
    chat.close()
    assert card["estimate"] == {"low": 0.015, "high": 0.041, "missing": []} and card["planned_cost"] == 0.011


def test_edit_before_run_changes_a_tier_and_removes_a_step(mocker, client):
    model = Model(answer(json.dumps(PLAN)))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    chat.close()
    r = client.post(f"/api/plans/{pid}/edit", json={"tiers": {"2": "simple"}, "remove": [1]},
                    headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert [(s["id"], s["tier"]) for s in r.json()["steps"]] == [(1, "simple"), (2, "simple")]
    refused = client.post(f"/api/plans/{pid}/edit", json={"remove": [1]}, headers={"Authorization": "Bearer test-token"})
    assert refused.status_code == 400 and "at least 2" in refused.json()["detail"]
    assert client.post(f"/api/plans/{pid}/edit", json={"remove": [1]}).status_code == 401


def test_each_step_is_marked_so_the_page_can_fold_it(mocker, client):
    model = Model(answer(json.dumps(PLAN)), finish("a"), finish("b"), finish("c"), say("ok"))
    chat = Conversation(client)
    pid = make_plan(mocker, chat, model)
    msgs = act(chat, pid, "run")
    chat.close()
    marks = [(m["plan_step"], m["step_title"]) for m in msgs if m["type"] == "system" and "plan_step" in m]
    assert marks == [(1, "List the functions"), (2, "Choose the tricky cases"), (3, "Write the tests"),
                     (plans.CHECK_STEP, "Final check")]


def test_ask_first_is_the_default():
    from optimux_backend.core.config_manager import SettingsPayload
    assert SettingsPayload().plan_mode == "ask"


def test_prompts_keep_handover_notes_out_of_the_project_and_run_the_check():
    planner_rules = agents.get_planner_prompt()
    assert "Don't plan files that just carry notes from one step to the next" in planner_rules
    assert "make it something commands can check" in planner_rules
    p = plans.create("c-prompts", "", PLAN, request="x")
    step = agents.get_step_prompt(p, p["steps"][0], [], [])
    assert "not in a new file in the project: write files only when the task itself needs them" in step
    check = agents.get_step_prompt(p, None, [], [])
    assert "never ask for confirmation in text" in check and "tell the user what to look at themselves" in check
