"""Per-model thinking: settings, streaming to the UI, Anthropic thinking blocks, reasoning tokens."""
import json
from types import SimpleNamespace as NS

import pytest

import optimux_backend.core.config_manager as cm
import optimux_backend.core.usage as usage
import optimux_backend.main as main
from optimux_backend.core.config_manager import ModelPayload, save_model, set_thinking, thinking_kwargs
from optimux_backend.core.security import ValidationError
from tests.ws_helpers import Conversation, fake_stream, make_client
from tests.test_routing_session import Recorder

H = {"Authorization": "Bearer test-token"}
DONE = {"prompt_tokens": 10, "completion_tokens": 5}


def delta_chunk(content=None, reasoning=None, thinking_blocks=None, tool_call=None, usage_obj=None, empty=False):
    tool_calls = None
    if tool_call:
        name, args = tool_call
        tool_calls = [NS(index=0, id="call_1", function=NS(name=name, arguments=json.dumps(args)))]
    if empty:
        return NS(choices=[], usage=usage_obj)
    delta = NS(content=content, tool_calls=tool_calls, reasoning_content=reasoning, thinking_blocks=thinking_blocks)
    return NS(choices=[NS(delta=delta)], usage=usage_obj)


@pytest.fixture
def models(mocker, tmp_path):
    f = tmp_path / "models.json"
    f.write_text(json.dumps({"gemini/g": {"name": "G", "env_key": ""}, "anthropic/c": {"name": "C", "env_key": ""}}))
    mocker.patch.object(cm, "MODELS_FILE", str(f))
    return f


# ---------- settings

def test_thinking_default_sends_nothing(models):
    assert thinking_kwargs("gemini/g") == {}
    set_thinking("gemini/g", "low")
    assert thinking_kwargs("gemini/g") == {"reasoning_effort": "low"}


def test_thinking_validation(models):
    with pytest.raises(ValidationError):
        set_thinking("gemini/g", "turbo")
    with pytest.raises(ValidationError):
        set_thinking("nope/model", "low")


def test_saving_prices_keeps_thinking(models):
    set_thinking("gemini/g", "medium")
    save_model(ModelPayload(id="gemini/g", name="G", input_cost_per_mtok=1, output_cost_per_mtok=2))
    assert thinking_kwargs("gemini/g") == {"reasoning_effort": "medium"}


def test_models_config_reports_thinking(models):
    set_thinking("anthropic/c", "high")
    entries = {m["id"]: m for m in cm.get_models_config()["available"]}
    assert entries["anthropic/c"]["thinking"] == "high" and "supports_thinking" in entries["anthropic/c"]


def test_reasoning_tokens_extracted():
    u = NS(prompt_tokens=10, completion_tokens=300, prompt_tokens_details=None,
           completion_tokens_details=NS(reasoning_tokens=250))
    assert usage.extract_usage(u)["reasoning_tokens"] == 250
    assert usage.extract_usage({"completion_tokens_details": {"reasoning_tokens": 7}})["reasoning_tokens"] == 7


# ---------- streaming

@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_thinking_streams_separately_and_stays_out_of_history(mocker, client):
    mocker.patch.object(main, "thinking_kwargs", return_value={"reasoning_effort": "low"})
    calls = []

    def fake(**kw):
        calls.append(kw)
        return fake_stream([delta_chunk(reasoning="Let me think. "), delta_chunk(reasoning="Two plus two."),
                            delta_chunk(content="4"), delta_chunk(empty=True, usage_obj=DONE)])

    mocker.patch("litellm.acompletion", side_effect=fake)
    chat = Conversation(client)
    msgs = chat.prompt("2+2?")
    assert calls[0]["reasoning_effort"] == "low"
    assert "".join(m["content"] for m in msgs if m["type"] == "thinking") == "Let me think. Two plus two."
    assert "".join(m["content"] for m in msgs if m["type"] == "token") == "4"
    mocker.patch("litellm.acompletion", side_effect=Recorder(fake_stream([delta_chunk(content="ok"), delta_chunk(empty=True, usage_obj=DONE)])))
    chat.prompt("next")
    chat.close()


def test_anthropic_thinking_blocks_resent_within_tool_loop(mocker, tmp_path):
    routing = {"conductor_model": "off", "simple_model": "anthropic/c", "complex_model": "gpt-x"}
    client = make_client(mocker, tmp_path, routing=routing)
    main._test_conductor.return_value = ("simple", "off", None, 0)
    mocker.patch.object(main, "update_memory", return_value="saved")
    signed = {"type": "thinking", "thinking": "I should save it.", "signature": "sig123"}
    seen = []

    def fake(**kw):
        seen.append(json.loads(json.dumps(kw["messages"], default=str)))
        if len(seen) == 1:
            return fake_stream([
                delta_chunk(reasoning="I should ", thinking_blocks=[{"type": "thinking", "thinking": "I should "}]),
                delta_chunk(reasoning="save it.", thinking_blocks=[{"type": "thinking", "thinking": "save it."}]),
                delta_chunk(reasoning="", thinking_blocks=[signed]),
                delta_chunk(tool_call=("update_markdown_memory", {"file_type": "personal", "content": "x"})),
                delta_chunk(empty=True, usage_obj=DONE)])
        return fake_stream([delta_chunk(content="Saved."), delta_chunk(empty=True, usage_obj=DONE)])

    mocker.patch("litellm.acompletion", side_effect=fake)
    chat = Conversation(client)
    chat.prompt("remember x")
    assistant = [m for m in seen[1] if m["role"] == "assistant"][-1]
    assert assistant["thinking_blocks"] == [signed]          # only the complete, signed block
    # after the turn, history keeps no thinking blocks
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: seen.append(kw["messages"]) or
                 fake_stream([delta_chunk(content="hi"), delta_chunk(empty=True, usage_obj=DONE)]))
    chat.prompt("hello")
    assert not any("thinking_blocks" in m for m in seen[-1])
    chat.close()


def test_non_anthropic_models_never_see_thinking_blocks():
    msgs = [{"role": "user", "content": "u"},
            {"role": "assistant", "content": None, "thinking_blocks": [{"type": "thinking"}], "tool_calls": []}]
    out = main.prepare_messages(msgs, "gemini/g")
    assert all("thinking_blocks" not in m for m in out) and "thinking_blocks" in msgs[1]
    assert "thinking_blocks" in main.prepare_messages(msgs, "anthropic/c")[1]


def test_thinking_endpoint(mocker, client, models):
    assert client.post("/api/models/thinking", json={"id": "gemini/g", "thinking": "minimal"}, headers=H).status_code == 200
    assert thinking_kwargs("gemini/g") == {"reasoning_effort": "minimal"}
    assert client.post("/api/models/thinking", json={"id": "gemini/g", "thinking": "max"}, headers=H).status_code == 400
    assert client.post("/api/models/thinking", json={"id": "gemini/g", "thinking": "low"}).status_code == 401
