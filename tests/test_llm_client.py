import pytest
from unittest.mock import MagicMock

import optimux_backend.core.llm_client as llm
from optimux_backend.core.llm_client import route_model, route_model_with_usage


def create_mock_response(content: str, usage=None):
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_response.choices = [mock_choice]
    mock_response.usage = usage
    return mock_response


@pytest.fixture
def conductor(mocker):
    """Configure a Conductor and return a setter for its routing config."""
    cfg = {"conductor_model": "test-conductor"}
    mocker.patch.object(llm, "get_routing_config", side_effect=lambda: cfg)
    return cfg


@pytest.mark.asyncio
@pytest.mark.parametrize("content,expected", [
    ('{"complexity": "complex"}', "complex"),
    ('{"complexity": "simple"}', "simple"),
    ('```json\n{"complexity": "complex"}\n```', "complex"),
    ('{"complexity": "tool"}', "simple"),      # retired class maps to simple
    ('I think it should be complex.', "simple"),  # not JSON
])
async def test_route_model_parsing(mocker, conductor, content, expected):
    mocker.patch.object(llm, "acompletion", return_value=create_mock_response(content))
    assert await route_model("anything") == expected


@pytest.mark.asyncio
async def test_no_keyword_fast_path(mocker, conductor):
    call = mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "simple"}'))
    assert await route_model("please run command mkdir x") == "simple"
    call.assert_called_once()


@pytest.mark.asyncio
async def test_conductor_call_settings(mocker, conductor):
    call = mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "simple"}'))
    mocker.patch.object(llm, "_supports_json_mode", return_value=True)
    await route_model("hi")
    kwargs = call.call_args.kwargs
    assert kwargs["temperature"] == 0
    assert kwargs["timeout"] == llm.CONDUCTOR_TIMEOUT
    assert kwargs["response_format"] == {"type": "json_object"}
    assert "stop" not in kwargs
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]


@pytest.mark.asyncio
async def test_json_mode_only_when_supported(mocker, conductor):
    call = mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "simple"}'))
    mocker.patch.object(llm, "_supports_json_mode", return_value=False)
    await route_model("hi")
    assert "response_format" not in call.call_args.kwargs


@pytest.mark.asyncio
async def test_returns_usage_and_duration(mocker, conductor):
    resp = create_mock_response('{"complexity": "complex"}', usage={"prompt_tokens": 40, "completion_tokens": 6})
    mocker.patch.object(llm, "acompletion", return_value=resp)
    tier, model, usage, duration, skill, plan, note = await route_model_with_usage("refactor everything")
    assert (tier, model, usage["prompt_tokens"]) == ("complex", "test-conductor", 40)
    assert isinstance(duration, int) and duration >= 0


@pytest.mark.asyncio
async def test_error_or_timeout_falls_back_to_simple(mocker, conductor):
    mocker.patch.object(llm, "acompletion", side_effect=TimeoutError("slow"))
    tier, model, usage, _, skill, plan, note = await route_model_with_usage("hi")
    assert (tier, model, usage) == ("simple", "test-conductor", None)
    assert note.startswith("the call failed")                     # said in the chat, not hidden


@pytest.mark.asyncio
async def test_conductor_off_makes_no_call(mocker, conductor):
    conductor["conductor_model"] = "off"
    call = mocker.patch.object(llm, "acompletion")
    assert await route_model_with_usage("hi") == ("simple", "off", None, 0, None, False, "")
    call.assert_not_called()


@pytest.mark.asyncio
async def test_conductor_not_configured_raises(mocker, conductor):
    conductor["conductor_model"] = ""
    with pytest.raises(Exception, match="Routing not configured"):
        await route_model("hi")


@pytest.mark.asyncio
async def test_conductor_uses_its_models_api_key(mocker, conductor):
    mocker.patch.object(llm, "litellm_key_kwargs", return_value={"api_key": "cond-key"})
    call = mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "simple"}'))
    await route_model("hi")
    assert call.call_args.kwargs["api_key"] == "cond-key"


@pytest.mark.asyncio
async def test_conductor_picks_only_known_skills(mocker):
    from optimux_backend.core import llm_client as llm
    mocker.patch.object(llm, "get_routing_config", return_value={"conductor_model": "test-conductor"})
    skills = {"commit-message": {"name": "commit-message", "description": "Write a commit message"}}
    call = mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "simple", "skill": "commit-message"}'))
    res = await route_model_with_usage("write a commit message", skills)
    assert res[0] == "simple" and res[4] == "commit-message"
    prompt = call.call_args.kwargs["messages"][0]["content"]
    assert "commit-message: Write a commit message" in prompt and '"skill"' in prompt
    mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "complex", "skill": "made-up"}'))
    assert (await route_model_with_usage("x", skills))[4] is None
    call = mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "simple"}'))
    await route_model_with_usage("x")
    assert "Skills" not in call.call_args.kwargs["messages"][0]["content"]   # no skills: original prompt


# ------------------------------------------------------------------ models that refuse the extras (Gemma)

GEMMA_400 = ('GeminiException BadRequestError - {\n  "error": {\n    "code": 400,\n    "message": '
             '"Thinking budget is not supported for this model.",\n    "status": "INVALID_ARGUMENT"\n  }\n}\n')


def bad_request(text=GEMMA_400):
    import litellm
    return litellm.BadRequestError(message=text, llm_provider="gemini", model="gemini/gemma-4-26b-it")


def test_gemma_gets_no_thinking_level():
    assert llm.conductor_thinking("gemini/gemma-4-26b-it") == {}
    assert llm.conductor_thinking("gemini/gemini-2.5-flash") == {"reasoning_effort": "minimal"}
    assert llm.conductor_thinking("anthropic/claude-haiku-4-5") == {}


@pytest.mark.asyncio
async def test_a_refused_request_is_tried_again_without_the_extras(mocker, conductor):
    conductor["conductor_model"] = "gemini/gemini-2.5-flash"
    mocker.patch.object(llm, "_supports_json_mode", return_value=True)
    call = mocker.patch.object(llm, "acompletion", side_effect=[
        bad_request(), create_mock_response('{"complexity": "complex"}')])
    tier, *_, note = await route_model_with_usage("refactor everything")
    assert (tier, note) == ("complex", "")
    first, second = (c.kwargs for c in call.call_args_list)
    assert first["reasoning_effort"] == "minimal" and "response_format" in first
    assert "reasoning_effort" not in second and "response_format" not in second
    assert second["messages"] == first["messages"]


@pytest.mark.asyncio
async def test_a_refusal_without_extras_says_why(mocker, conductor):
    mocker.patch.object(llm, "_supports_json_mode", return_value=False)
    call = mocker.patch.object(llm, "acompletion", side_effect=bad_request())
    tier, *_, note = await route_model_with_usage("hi")
    assert tier == "simple" and call.call_count == 1                 # nothing to drop: no second call
    assert note == "the call failed: refused (400): Thinking budget is not supported for this model."


@pytest.mark.asyncio
async def test_an_answer_that_is_not_json_is_named(mocker, conductor):
    mocker.patch.object(llm, "acompletion", return_value=create_mock_response("It is complex, I think."))
    tier, *_, note = await route_model_with_usage("hi")
    assert tier == "simple" and "wasn't the JSON" in note
