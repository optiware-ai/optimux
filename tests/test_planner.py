"""v0.4 plan mode, commit 2: when to plan, the planner, /plan and /noplan."""
import asyncio
import json
import os
from types import SimpleNamespace as NS

import pytest

import optimux_backend.main as main
from optimux_backend.core import agents, llm_client, planner, plans, store
from tests.test_plans import PLAN
from tests.ws_helpers import Conversation, chunk, fake_stream, make_client

DONE = {"prompt_tokens": 10, "completion_tokens": 2}


def answer(text, usage=None, thinking=None):
    """A non-streamed completion, as the planner gets it."""
    message = NS(content=text, reasoning_content=thinking) if thinking else NS(content=text)
    return NS(choices=[NS(message=message)], usage=usage or {"prompt_tokens": 900, "completion_tokens": 300})


# ------------------------------------------------------------------ the Conductor

def test_the_executor_looks_before_it_asks():
    prompt = agents.get_executor_prompt("", workdir="/w/alpha")
    assert "look before you ask" in prompt and "Don't ask permission to read" in prompt


def test_plan_question_only_when_plan_mode_is_on():
    assert agents.get_router_prompt() == agents.get_router_prompt("", plan=False)       # unchanged when off
    on = agents.get_router_prompt("", plan=True)
    assert '"plan": true or false' in on and "a simple task is never planned" in on
    with_skills = agents.get_router_prompt("- deploy: ship it", plan=True)
    assert with_skills.count("Reply with only JSON") == 1 and '"skill"' in with_skills and "deploy" in with_skills


@pytest.mark.parametrize("text,expected", [
    ('{"complexity": "complex", "plan": true}', True),
    ('{"complexity": "simple", "plan": true}', False),          # a simple task is never planned
    ('{"complexity": "complex", "plan": "yes"}', False),
    ('{"complexity": "complex"}', False),
    ('nonsense', False),
])
def test_parse_plan_flag(text, expected):
    assert llm_client._parse_plan(text) is expected


# ------------------------------------------------------------------ the planner

def test_file_list_skips_noise_and_never_follows_links(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("x")
    for skipped in (".git", "node_modules", ".venv", "__pycache__", ".secret"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "f").write_text("x")
    (tmp_path / "outside").symlink_to("/etc", target_is_directory=True)
    files, total = planner.file_list(str(tmp_path))
    assert sorted(files) == [".github/workflows/ci.yml", "src/a.py"] and total == 2
    assert planner.file_list("") == ([], 0) and planner.file_list(str(tmp_path / "nope")) == ([], 0)


def test_file_list_is_capped(tmp_path):
    for i in range(planner.MAX_FILES_LISTED + 25):
        (tmp_path / f"f{i:04}.txt").write_text("x")
    files, total = planner.file_list(str(tmp_path))
    assert len(files) == planner.MAX_FILES_LISTED and total == planner.MAX_FILES_LISTED + 25
    msgs = planner.build_messages("do it", "memo", files, total)
    assert "(and 25 more not listed)" in msgs[1]["content"] and msgs[0]["content"] == agents.get_planner_prompt()


def test_planner_prompt_orders_setup_first():
    prompt = agents.get_planner_prompt()
    assert "set up and install what is needed before the steps that use it" in prompt
    assert "Merge small steps" in prompt


def test_make_plan_retries_once_with_the_reason(tmp_path):
    seen = []

    async def call(**kwargs):
        seen.append(kwargs["messages"])
        if len(seen) == 1:
            return answer('{"goal": "x", "steps": []}')
        return answer("```json\n" + json.dumps(PLAN) + "\n```")
    result = asyncio.run(planner.make_plan("write tests", "complex-model", "memory", str(tmp_path), call=call))
    assert result["plan"]["goal"] == PLAN["goal"] and result["error"] == "" and len(result["calls"]) == 2
    assert "That plan can't be used: A plan needs at least 2 steps." in seen[1][-1]["content"]


def test_make_plan_gives_up_after_the_retry(tmp_path):
    async def call(**kwargs):
        return answer("I think we should first look at the files.")
    result = asyncio.run(planner.make_plan("x", "m", "", str(tmp_path), call=call))
    assert result["plan"] is None and "not valid JSON" in result["error"] and len(result["calls"]) == 2


# ------------------------------------------------------------------ in the chat

@pytest.fixture
def client(mocker, tmp_path):
    c = make_client(mocker, tmp_path)
    real = main.get_settings
    mode = {"plan_mode": "ask", "active_project": "alpha"}
    mocker.patch.object(main, "get_settings", lambda: {**real(), **mode})
    c.mode = mode
    return c


def conductor_says(mocker, tier, plan):
    main._test_conductor.return_value = (tier, "conductor-model", {"prompt_tokens": 50, "completion_tokens": 5}, 12,
                                         None, plan)


def calls_by(mocker, responses):
    """litellm.acompletion: the planner's (non-streamed) answers, then executor streams."""
    seen = []

    def call(**kwargs):
        seen.append(kwargs)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r                       # litellm.acompletion is patched with an AsyncMock: it awaits for us
    mocker.patch("litellm.acompletion", side_effect=call)
    return seen


def notes(msgs):
    return [m.get("content", "") for m in msgs if m["type"] == "system"]


def test_a_big_task_gets_a_plan_shown_and_nothing_runs(mocker, client):
    conductor_says(mocker, "complex", True)
    seen = calls_by(mocker, [answer(json.dumps(PLAN))])
    chat = Conversation(client)
    msgs = chat.prompt("write tests for the parser")
    chat.close()
    assert len(seen) == 1 and seen[0]["model"] == "complex-model" and "tools" not in seen[0]
    shown = [m for m in msgs if m["type"] == "plan"]
    assert len(shown) == 1 and shown[0]["plan"]["status"] == "waiting" and len(shown[0]["plan"]["steps"]) == 3
    card = shown[0]["plan"]
    assert card["steps"][0]["do"] == "List every public function." and card["steps"][0]["files"] == ["src/parser.py"]
    assert card["check"] == "pytest -q passes"
    assert any(n.startswith("Plan ready: 3 steps. Check them, then press Run") for n in notes(msgs))
    pid = shown[0]["plan"]["id"]
    conn = store.connect()
    rows = [tuple(r) for r in conn.execute("SELECT role, plan_id FROM usage WHERE role IN ('planner', 'conductor')")]
    conn.close()
    assert ("planner", pid) in rows and ("conductor", None) in rows
    assert plans.get(pid)["request"] == "write tests for the parser"


def test_off_never_plans_but_plan_command_does(mocker, client):
    client.mode["plan_mode"] = "off"
    conductor_says(mocker, "complex", True)                    # ignored: plan mode is off
    seen = calls_by(mocker, [fake_stream([chunk("done"), chunk(empty=True, usage_obj=DONE)]),
                             answer(json.dumps(PLAN))])
    chat = Conversation(client)
    first = chat.prompt("write tests for the parser")
    assert not [m for m in first if m["type"] == "plan"] and seen[0].get("stream")
    conductor_says(mocker, "simple", False)
    second = chat.prompt("/plan write tests for the parser")
    chat.close()
    assert [m for m in second if m["type"] == "plan"]
    assert seen[1]["messages"][1]["content"].startswith("Task:\nwrite tests for the parser")   # the command is gone


def test_noplan_and_the_skip_rules(mocker, client):
    conductor_says(mocker, "complex", True)
    calls_by(mocker, [fake_stream([chunk("ok"), chunk(empty=True, usage_obj=DONE)])])
    chat = Conversation(client)
    msgs = chat.prompt("/noplan write tests for the parser")
    chat.close()
    assert not [m for m in msgs if m["type"] == "plan"]
    assert main._test_conductor.call_args.kwargs == {}            # the plan question isn't even asked


def test_plan_command_explains_why_not(mocker, client):
    mocker.patch.object(main, "get_routing_config", return_value={
        "conductor_model": "conductor-model", "simple_model": "same", "complex_model": "same"})
    conductor_says(mocker, "simple", False)
    calls_by(mocker, [fake_stream([chunk("ok"), chunk(empty=True, usage_obj=DONE)])])
    chat = Conversation(client)
    msgs = chat.prompt("/plan write tests")
    chat.close()
    assert any("Not planned: Simple and Complex are the same model" in n for n in notes(msgs))
    assert not [m for m in msgs if m["type"] == "plan"]


def test_no_active_project_is_not_planned(mocker, client):
    client.mode["active_project"] = ""
    conductor_says(mocker, "complex", True)
    calls_by(mocker, [fake_stream([chunk("ok"), chunk(empty=True, usage_obj=DONE)])])
    chat = Conversation(client)
    msgs = chat.prompt("write tests for the parser")
    chat.close()
    assert not [m for m in msgs if m["type"] == "plan"]


def test_a_failed_planner_falls_back_to_the_usual_way(mocker, client):
    conductor_says(mocker, "complex", True)
    seen = calls_by(mocker, [answer("no plan"), answer("still no plan"),
                             fake_stream([chunk("did it"), chunk(empty=True, usage_obj=DONE)])])
    chat = Conversation(client)
    msgs = chat.prompt("write tests for the parser")
    chat.close()
    assert any("The plan couldn't be used" in n for n in notes(msgs))
    assert seen[2]["model"] == "complex-model" and seen[2].get("stream")              # done the usual way
    conn = store.connect()
    n = conn.execute("SELECT COUNT(*) FROM usage WHERE role = 'planner' AND plan_id IS NULL").fetchone()[0]
    conn.close()
    assert n == 2                                                                     # both calls metered


def test_a_new_request_replaces_a_waiting_plan(mocker, client):
    conductor_says(mocker, "complex", True)
    calls_by(mocker, [answer(json.dumps(PLAN)), answer(json.dumps(PLAN))])
    chat = Conversation(client)
    first = [m for m in chat.prompt("write tests for the parser") if m["type"] == "plan"][0]["plan"]["id"]
    chat.prompt("write tests for the lexer")
    chat.close()
    old = plans.get(first)
    assert old["status"] == "cancelled" and old["note"] == "Replaced by a newer request."


def test_the_setting_is_checked():
    from optimux_backend.core.config_manager import SettingsPayload, save_settings, ValidationError
    with pytest.raises(ValidationError, match="plan_mode"):
        save_settings(SettingsPayload(plan_mode="sometimes"))


def test_the_planner_thinking_is_shown(mocker, client):
    conductor_says(mocker, "complex", True)
    calls_by(mocker, [answer(json.dumps(PLAN), thinking="The parser has two entry points, so ...")])
    chat = Conversation(client)
    msgs = chat.prompt("write tests for the parser")
    chat.close()
    thought = [m["content"] for m in msgs if m["type"] == "thinking"]
    assert thought == ["The parser has two entry points, so ..."]
    kinds = [m["type"] for m in msgs]
    assert kinds.index("thinking") < kinds.index("plan")


def test_make_plan_keeps_the_thinking_of_both_tries(tmp_path):
    replies = [answer('{"goal": "x", "steps": []}', thinking="first"), answer(json.dumps(PLAN), thinking="second")]

    async def call(**kwargs):
        return replies.pop(0)
    result = asyncio.run(planner.make_plan("x", "m", "", str(tmp_path), call=call))
    assert result["plan"] and result["thinking"] == "first\n\nsecond"
