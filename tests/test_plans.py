"""v0.4 plan mode, commit 1: parsing, checking and storing plans."""
import json

import pytest

from optimux_backend.core import plans, store, usage

PLAN = {
    "goal": "Tests for src/parser.py, run with pytest",
    "steps": [
        {"id": 7, "title": "List the functions", "tier": "simple", "do": "List every public function.",
         "done_when": "A list of functions", "files": ["src/parser.py"]},
        {"id": 9, "title": "Choose the tricky cases", "tier": "complex", "do": "Pick the edge cases.",
         "done_when": "A list of cases"},
        {"id": 3, "title": "Write the tests", "tier": "simple", "do": "One test per case.",
         "done_when": "The file exists", "files": ["tests/test_parser.py"]},
    ],
    "check": "pytest -q passes",
}


def test_parse_finds_the_json_in_a_model_answer():
    text = json.dumps(PLAN)
    assert plans.parse(text)["goal"] == PLAN["goal"]
    assert plans.parse(f"Here is the plan:\n```json\n{text}\n```\nGood luck.")["check"] == "pytest -q passes"
    assert plans.parse(f"Sure! {text} Done.")["steps"][0]["tier"] == "simple"
    with pytest.raises(plans.PlanError, match="not valid JSON"):
        plans.parse("I would first read the files, then ...")


def test_validate_cleans_and_renumbers():
    clean = plans.validate(dict(PLAN, goal="  Tests   for\nparser "))
    assert clean["goal"] == "Tests for parser"
    assert [s["id"] for s in clean["steps"]] == [1, 2, 3]           # the planner's ids are not trusted
    assert clean["steps"][1]["files"] == [] and clean["steps"][0]["files"] == ["src/parser.py"]
    assert set(clean["steps"][0]) == {"id", "title", "tier", "do", "done_when", "files"}


@pytest.mark.parametrize("change,match", [
    (lambda p: p.update(steps=p["steps"][:1]), "at least 2"),
    (lambda p: p.update(steps=p["steps"] * 3), "at most 8"),
    (lambda p: p["steps"][0].update(tier="medium"), "tier"),
    (lambda p: [s.update(tier="complex") for s in p["steps"]], "nothing to save"),
    (lambda p: p["steps"][0].update(do="x" * 601), "longer than 600"),
    (lambda p: p["steps"][1].pop("done_when"), "done_when is missing"),
    (lambda p: p["steps"][0].update(files="src/parser.py"), "files"),
    (lambda p: p.update(goal=""), "goal is missing"),
    (lambda p: p.update(steps="do it all"), "no steps list"),
])
def test_validate_refuses_bad_plans_with_a_reason(change, match):
    p = json.loads(json.dumps(PLAN))
    change(p)
    with pytest.raises(plans.PlanError, match=match):
        plans.validate(p)


def test_create_get_and_step_updates_survive_reading_back():
    p = plans.create("chat-1", "alpha", PLAN, request="write tests for the parser")
    assert p["status"] == "waiting" and len(p["id"]) == 16 and p["project"] == "alpha"
    assert [s["status"] for s in p["steps"]] == ["pending"] * 3 and p["steps"][0]["cost"] == 0.0
    plans.update_step(p["id"], 1, status="running", started=1.0)
    plans.add_cost(p["id"], 1, 0.004, model="gemini/gemini-2.5-flash")
    plans.add_cost(p["id"], 1, None)                                  # a call without a known price
    q = plans.update_step(p["id"], 1, status="done", result="parse(), tokenize()", finished=2.0)
    s1 = plans.get(p["id"])["steps"][0]
    assert s1["status"] == "done" and s1["calls"] == 2 and s1["cost"] == 0.004 and s1["model"].startswith("gemini/")
    assert plans.next_step(q)["id"] == 2 and plans.total_cost(q) == 0.004
    with pytest.raises(plans.PlanError, match="Unknown step fields"):
        plans.update_step(p["id"], 1, goal="x")
    with pytest.raises(plans.PlanError, match="no step 9"):
        plans.update_step(p["id"], 9, status="done")
    with pytest.raises(plans.PlanError, match="No such plan"):
        plans.get("../../etc/passwd")


def test_open_plan_and_statuses():
    p = plans.create("chat-2", "", PLAN)
    assert plans.open_plan("chat-2")["id"] == p["id"]
    plans.set_status(p["id"], "cancelled", note="Cancelled by the user")
    assert plans.open_plan("chat-2") is None and plans.get(p["id"])["note"] == "Cancelled by the user"
    with pytest.raises(plans.PlanError, match="Unknown plan status"):
        plans.set_status(p["id"], "exploded")
    assert [x["id"] for x in plans.for_session("chat-2")] == [p["id"]]


def test_edit_before_run_changes_tiers_and_removes_steps():
    p = plans.create("chat-3", "", PLAN)
    q = plans.edit_waiting(p["id"], tiers={"2": "simple"}, remove=[1])
    assert [(s["id"], s["title"], s["tier"]) for s in q["steps"]] == [
        (1, "Choose the tricky cases", "simple"), (2, "Write the tests", "simple")]
    with pytest.raises(plans.PlanError, match="at least 2"):
        plans.edit_waiting(p["id"], remove=[1])
    with pytest.raises(plans.PlanError, match="Cancel it instead"):
        plans.edit_waiting(p["id"], tiers={"1": "complex", "2": "complex"})
    plans.set_status(p["id"], "running")
    with pytest.raises(plans.PlanError, match="hasn't started"):
        plans.edit_waiting(p["id"], remove=[2])


def test_a_restart_pauses_running_plans_for_resume():
    p = plans.create("chat-4", "", PLAN)
    plans.set_status(p["id"], "running")
    plans.update_step(p["id"], 1, status="done", result="ok")
    plans.update_step(p["id"], 2, status="running")
    assert plans.mark_interrupted() == 1
    q = plans.get(p["id"])
    assert q["status"] == "paused" and q["interrupted"] is True
    assert [s["status"] for s in q["steps"]] == ["done", "pending", "pending"]
    assert plans.next_step(q)["id"] == 2
    assert plans.set_status(p["id"], "running")["interrupted"] is False
    assert plans.mark_interrupted() == 1 and plans.mark_interrupted() == 0


def test_history_record_is_compact():
    p = plans.create("chat-5", "", PLAN)
    plans.update_step(p["id"], 1, status="done", result="Found parse() and tokenize().", model="gemini/x")
    text = plans.summary_for_history(plans.get(p["id"]))
    assert text.splitlines()[0] == "[Plan] Tests for src/parser.py, run with pytest (waiting)"
    assert "1. List the functions [simple, gemini/x, done]" in text and "Found parse()" in text
    assert text.splitlines()[-1] == "Check: pytest -q passes"
    assert "Do:" not in text                                         # the history stays compact


def test_details_show_what_each_step_will_do():
    text = plans.details_text(plans.create("chat-7", "", PLAN))
    assert "   Do: List every public function." in text and "   Done when: A list of functions" in text
    assert "   Files: src/parser.py" in text and text.splitlines()[-1] == "Check: pytest -q passes"
    assert "Check: (none)" in plans.details_text(plans.create("chat-8", "", dict(PLAN, check="")))


def test_usage_rows_carry_the_plan_and_step():
    usage.record("chat-6", "simple", "gpt-5", {"prompt_tokens": 10, "completion_tokens": 2},
                 plan_id="abcdefghijklmnop", step=2)
    usage.record("chat-6", "simple", "gpt-5", {"prompt_tokens": 10, "completion_tokens": 2})
    conn = store.connect()
    rows = [tuple(r) for r in conn.execute("SELECT plan_id, step FROM usage WHERE session = 'chat-6' ORDER BY id")]
    conn.close()
    assert rows == [("abcdefghijklmnop", 2), (None, None)]
