"""Sticky routing, history, overrides, escalation and provider fallback."""
import json

import litellm
import pytest

import optimux_backend.main as main
from tests.ws_helpers import Conversation, chunk, fake_stream, make_client

DONE = {"prompt_tokens": 10, "completion_tokens": 2}


def reply(text):
    return fake_stream([chunk(text), chunk(empty=True, usage_obj=DONE)])


def tool_reply(name, args):
    return fake_stream([chunk(tool_call=(name, args)), chunk(empty=True, usage_obj=DONE)])


class Recorder:
    """Fake litellm.acompletion: returns queued streams, records each call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append({"model": kwargs["model"], "messages": json.loads(json.dumps(kwargs["messages"], default=str))})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def overloaded():
    return litellm.ServiceUnavailableError(message="high demand", llm_provider="gemini", model="simple-model")


def notes(msgs):
    return [m.get("content", "") for m in msgs if m["type"] == "system"]


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_history_is_kept(mocker, client):
    rec = Recorder(reply("Hi!"), reply("You said hello."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("hello")
    chat.prompt("what did I say?")
    chat.close()
    second = rec.calls[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "hello" and second[2]["content"] == "Hi!"


def test_both_tiers_get_tools(mocker, client):
    captured = {}
    def fake(**kw):
        captured.update(kw)
        return reply("ok")
    mocker.patch("litellm.acompletion", side_effect=fake)
    Conversation(client).prompt("list the files")
    names = [t["function"]["name"] for t in captured["tools"]]
    assert names[:len(main.TOOLS)] == [t["function"]["name"] for t in main.TOOLS]
    # no active project in the test settings: workspace mode offers all project tools
    assert {"list_projects", "create_project", "switch_project", "archive_project"} <= set(names)
    assert "stop" not in captured


def test_tier_command_overrides_and_is_stripped(mocker, client):
    rec = Recorder(reply("done"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("/complex refactor the module")
    assert main._test_conductor.call_count == 0
    assert rec.calls[0]["model"] == "complex-model"
    assert rec.calls[0]["messages"][-1]["content"] == "refactor the module"
    assert any("Routed to: complex-model (complex)" in n for n in notes(msgs))


def test_tier_command_alone_sets_tier(mocker, client):
    rec = Recorder(reply("ok"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    assert "Using the complex tier" in notes(chat.prompt("/complex"))[0]
    chat.prompt("hello")
    assert rec.calls[0]["model"] == "complex-model"
    assert main._test_conductor.call_count == 0


def test_new_chat_resets_history_and_tier(mocker, client):
    rec = Recorder(reply("one"), reply("two"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("first")
    msgs = chat.prompt("/new")
    assert any(m["type"] == "chat_reset" for m in msgs)
    chat.prompt("second")
    assert [m["role"] for m in rec.calls[1]["messages"]] == ["system", "user"]


def test_new_chat_message_type(mocker, client):
    rec = Recorder(reply("one"), reply("two"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("first")
    chat.send({"type": "new_chat"})
    chat.prompt("second")
    assert len(rec.calls[1]["messages"]) == 2


def test_transient_error_retries_then_escalates(mocker, client):
    rec = Recorder(overloaded(), overloaded(), reply("answer from complex"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    msgs = chat.prompt("explain asyncio")
    assert [c["model"] for c in rec.calls] == ["simple-model", "simple-model", "complex-model"]
    n = notes(msgs)
    assert any("retrying" in x for x in n)
    assert any(x.startswith("Escalated to complex-model") for x in n)
    assert not any(m["type"] == "error" for m in msgs)
    # A provider outage is not a difficulty signal: the chat stays on simple,
    # but the cooling-down simple model is skipped without spending a request.
    rec.responses.append(reply("from complex, not sticky"))
    msgs = chat.prompt("and more?")
    assert rec.calls[-1]["model"] == "complex-model"
    assert any(x.startswith("Skipping simple-model") for x in notes(msgs))
    # Once the cooldown is over, the chat is back on the simple model
    from optimux_backend.core import providers
    providers.clear()
    rec.responses.append(reply("simple again"))
    chat.prompt("thanks")
    assert rec.calls[-1]["model"] == "simple-model"




def test_complex_provider_error_falls_back(mocker, tmp_path):
    client = make_client(mocker, tmp_path, tier="complex")
    err = litellm.ServiceUnavailableError(message="down", llm_provider="openai", model="complex-model")
    rec = Recorder(err, err, reply("simple answer"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("refactor")
    assert [c["model"] for c in rec.calls] == ["complex-model", "complex-model", "simple-model"]
    assert any(x.startswith("Fell back to simple-model") for x in notes(msgs))


def test_non_transient_error_reports_without_fallback(mocker, client):
    rec = Recorder(ValueError("bad request"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("hi")
    assert len(rec.calls) == 1
    assert any(m["type"] == "error" and "bad request" in m["content"] for m in msgs)


def test_repeated_tool_call_escalates_and_keeps_progress(mocker, client):
    mocker.patch.object(main, "update_memory", return_value="saved")
    rec = Recorder(
        tool_reply("update_markdown_memory", {"file_type": "personal", "content": "x"}),
        tool_reply("update_markdown_memory", {"file_type": "personal", "content": "x"}),
        reply("Saved your preference."),
    )
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("remember x")
    assert [c["model"] for c in rec.calls] == ["simple-model", "simple-model", "complex-model"]
    assert any("Escalated to complex-model: model repeated" in x for x in notes(msgs))
    # complex model sees the tool call and result already made
    roles = [m["role"] for m in rec.calls[2]["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]


def test_explicit_model_never_falls_back(mocker, client):
    rec = Recorder(overloaded(), overloaded())
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("hi", model="my-model")
    assert [c["model"] for c in rec.calls] == ["my-model", "my-model"]
    assert main._test_conductor.call_count == 0
    assert any(m["type"] == "error" and "Pick another model" in m["content"] for m in msgs)


def test_escalate_reruns_last_request_on_complex(mocker, client):
    rec = Recorder(reply("weak answer"), reply("strong answer"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("design a cache")
    msgs = chat.send({"type": "escalate"})
    assert rec.calls[1]["model"] == "complex-model"
    assert [m["role"] for m in rec.calls[1]["messages"]] == ["system", "user"]
    assert rec.calls[1]["messages"][1]["content"] == "design a cache"
    assert any("Escalated to: complex-model" in n for n in notes(msgs))


def test_escalate_with_empty_chat(mocker, client):
    msgs = Conversation(client).send({"type": "escalate"})
    assert "Nothing to escalate." in notes(msgs)


def test_anthropic_system_prompt_is_cache_marked(mocker, tmp_path):
    routing = {"conductor_model": "off", "simple_model": "anthropic/claude-x", "complex_model": "gpt-x"}
    client = make_client(mocker, tmp_path, routing=routing)
    rec = Recorder(reply("hi"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    Conversation(client).prompt("hello")
    system = rec.calls[0]["messages"][0]
    assert system["content"][0]["cache_control"] == {"type": "ephemeral"}


# ---------- pure helpers

def test_parse_tier_command():
    assert main.parse_tier_command("/COMPLEX do it") == ("complex", "do it")
    assert main.parse_tier_command("/simple") == ("simple", "")
    assert main.parse_tier_command("/simplest thing") == (None, "/simplest thing")
    assert main.parse_tier_command("plain") == (None, "plain")


def test_trim_history_starts_at_user():
    msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": None, "tool_calls": []},
            {"role": "tool", "content": "r"}, {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"}]
    assert main.trim_history(msgs, cap=4) == msgs[4:]
    assert main.trim_history(msgs, cap=10) == msgs


def test_compact_history_truncates_tools_and_drops_system():
    big = "x" * (main.TOOL_OUTPUT_HISTORY_CHARS + 100)
    out = main.compact_history([
        {"role": "user", "content": "u"},
        {"role": "system", "content": "nudge"},
        {"role": "tool", "content": big},
    ])
    assert [m["role"] for m in out] == ["user", "tool"]
    assert out[1]["content"].endswith("[output truncated in history]")
    assert len(out[1]["content"]) < len(big)


# ---------- upgrade-only routing

def test_conductor_checks_every_message_while_simple(mocker, client):
    rec = Recorder(reply("a"), reply("b"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("hello")
    chat.prompt("how are you")
    assert main._test_conductor.call_count == 2
    assert [c["model"] for c in rec.calls] == ["simple-model", "simple-model"]


def test_conductor_moves_up_and_back_down_between_tasks(mocker, client):
    rec = Recorder(reply("hi"), reply("planned"), reply("small answer"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    tiers = iter(["simple", "complex", "simple"])
    main._test_conductor.side_effect = lambda msg, *_: (next(tiers), "conductor-model", None, 0)
    chat = Conversation(client)
    chat.prompt("hello")
    up = chat.prompt("redesign the whole storage layer")
    assert any("moving the chat to the complex tier" in n for n in notes(up))
    down = chat.prompt("delete notes.txt")
    assert any("moving the chat back to the simple tier" in n for n in notes(down))
    assert [c["model"] for c in rec.calls] == ["simple-model", "complex-model", "simple-model"]
    assert main._test_conductor.call_count == 3


def test_no_move_down_after_an_unfinished_turn(mocker, tmp_path):
    client = make_client(mocker, tmp_path, tier="complex")
    mocker.patch.object(main, "max_steps_for", return_value=1)
    mocker.patch.object(main, "update_memory", return_value="saved")
    rec = Recorder(
        fake_stream([chunk(tool_call=("update_markdown_memory", {"file_type": "personal", "content": "x"})),
                     chunk(empty=True, usage_obj=DONE)]),
        reply("continuing on complex"),
    )
    mocker.patch("litellm.acompletion", side_effect=rec)
    tiers = iter(["complex", "simple"])
    main._test_conductor.side_effect = lambda msg, *_: (next(tiers), "conductor-model", None, 0)
    chat = Conversation(client)
    first = chat.prompt("big refactor")
    assert any(m["type"] == "step_limit" for m in first)
    second = chat.prompt("Continue from where you stopped.")
    assert not any("back to the simple tier" in n for n in notes(second))
    assert rec.calls[-1]["model"] == "complex-model"


def test_conductor_off_keeps_the_current_tier(mocker, client):
    rec = Recorder(reply("a"), reply("b"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    main._test_conductor.return_value = ("simple", "off", None, 0)
    chat = Conversation(client)
    chat.prompt("hello")
    chat.prompt("redesign everything")
    # With the Conductor off, nothing but escalation changes the tier
    assert [c["model"] for c in rec.calls] == ["simple-model", "simple-model"]


def test_anthropic_last_message_is_a_cache_breakpoint(mocker, tmp_path):
    routing = {"conductor_model": "off", "simple_model": "anthropic/claude-x", "complex_model": "gpt-x"}
    client = make_client(mocker, tmp_path, routing=routing)
    main._test_conductor.return_value = ("simple", "off", None, 0)
    rec = Recorder(reply("one"), reply("two"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("hello")
    chat.prompt("again")
    sent = rec.calls[1]["messages"]
    assert sent[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in sent[1]  # only the newest user/tool message


def test_non_anthropic_messages_are_not_marked():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert main.with_cache_breakpoint(msgs, "gemini/x") is msgs
    marked = main.with_cache_breakpoint(msgs, "anthropic/x")
    assert marked[1]["cache_control"] and "cache_control" not in msgs[1]


def test_pinned_tier_skips_conductor(mocker, client):
    rec = Recorder(reply("a"), reply("b"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("/simple hello")
    chat.prompt("refactor everything")
    assert main._test_conductor.call_count == 0
    assert [c["model"] for c in rec.calls] == ["simple-model", "simple-model"]


def test_new_chat_unpins(mocker, client):
    rec = Recorder(reply("a"), reply("b"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.prompt("/simple hi")
    chat.prompt("/new")
    chat.prompt("hello")
    assert main._test_conductor.call_count == 1


def test_executor_call_uses_the_models_api_key(mocker, client):
    mocker.patch.object(main, "litellm_key_kwargs", side_effect=lambda m: {"api_key": "key-for-" + m})
    captured = {}
    def fake(**kw):
        captured.update(kw)
        return reply("ok")
    mocker.patch("litellm.acompletion", side_effect=fake)
    Conversation(client).prompt("hello")
    assert captured["api_key"] == "key-for-simple-model"


def test_escalate_reuses_commands_already_run(mocker, client):
    mocker.patch.object(main, "run_shell_command", return_value={"stdout": "print('hi')", "stderr": "", "exit_code": 0})
    rec = Recorder(
        fake_stream([chunk(tool_call=("execute_bash", {"command": "cat a.py"})), chunk(empty=True, usage_obj=DONE)]),
        reply("weak explanation"),
        reply("strong explanation"),
    )
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "explain a.py", "model": "auto"}))
    while json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    from tests.ws_helpers import collect
    collect(chat.ws)
    msgs = chat.send({"type": "escalate"})
    sent = rec.calls[2]["messages"]
    assert rec.calls[2]["model"] == "complex-model"
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]   # no weak answer, no new user msg
    assert sent[3]["content"].count("print('hi')") == 1
    assert any("reusing 1 command result" in n for n in notes(msgs))
    # history after the escalation: request, commands, strong answer
    rec.responses.append(reply("follow-up"))
    chat.prompt("thanks")
    roles = [m["role"] for m in rec.calls[3]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]
    assert rec.calls[3]["messages"][4]["content"] == "strong explanation"
    chat.close()


def test_a_failed_conductor_is_said_in_the_chat(mocker, client):
    rec = Recorder(reply("ok"), reply("ok again"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    main._test_conductor.return_value = ("simple", "conductor-model", None, 40, None, False,
                                         "the call failed: refused (400): Thinking budget is not supported")
    chat = Conversation(client)
    msgs = chat.prompt("write tests for this project")
    notes = [m["content"] for m in msgs if m["type"] == "system"]
    assert any(n.startswith("The Conductor (conductor-model) couldn't decide: the call failed: refused (400)")
               and n.endswith("This request starts on the simple tier.") for n in notes)
    main._test_conductor.return_value = ("simple", "conductor-model", None, 40, None, False, "")
    again = chat.prompt("hello")
    chat.close()
    assert not any("couldn't decide" in m.get("content", "") for m in again if m["type"] == "system")
