"""Provider error classification, cooldowns and quota handling."""
import litellm
import pytest

import optimux_backend.core.providers as providers
import optimux_backend.main as main
from tests.ws_helpers import Conversation, make_client
from tests.test_routing_session import Recorder, notes, reply

# Trimmed copy of the real Gemini free-tier error from the baseline run
GEMINI_QUOTA = (
    'litellm.RateLimitError: vertex_ai_betaException - b\'{"error": {"code": 429, '
    '"message": "You exceeded your current quota, please check your plan and billing details. '
    '* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, '
    'limit: 20, model: gemini-3.6-flash\\nPlease retry in 6h7m10.720244956s.", "status": "RESOURCE_EXHAUSTED", '
    '"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}, '
    '{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "22030s"}]}}\''
)


def rate_limit(message, model="m"):
    return litellm.RateLimitError(message=message, llm_provider="gemini", model=model)


@pytest.fixture(autouse=True)
def clean():
    providers.clear()
    yield
    providers.clear()


# ---------- classification

def test_gemini_free_tier_quota():
    info = providers.classify(rate_limit(GEMINI_QUOTA))
    assert info.kind == "quota"
    assert info.retry_after == 22030
    assert info.summary == "quota exhausted (free tier, 20/day), resets in ~6h07m"
    assert len(info.summary) < 80


def test_openai_insufficient_quota():
    info = providers.classify(rate_limit("Error code: 429 - {'error': {'code': 'insufficient_quota'}}"))
    assert info.kind == "quota" and "out of credit" in info.summary


def test_plain_rate_limit_is_transient():
    info = providers.classify(rate_limit("Rate limit reached, please try again in 1.2s"))
    assert info.kind == "transient" and info.summary.startswith("rate limited")


def test_overload_is_transient():
    e = litellm.ServiceUnavailableError(message="high demand", llm_provider="gemini", model="m")
    assert providers.classify(e).kind == "transient"


def test_long_retry_delay_counts_as_quota():
    info = providers.classify(rate_limit("Please retry in 10m0s"))
    assert info.kind == "quota" and info.retry_after == 600


def test_fatal_is_one_short_line():
    info = providers.classify(ValueError("bad request: " + "x" * 500 + "\nsecond line"))
    assert info.kind == "fatal" and "\n" not in info.summary and len(info.summary) <= 203


@pytest.mark.parametrize("text,seconds", [
    ('"retryDelay": "22030s"', 22030),
    ("Please retry in 6h7m10.5s.", 6 * 3600 + 7 * 60 + 10.5),
    ("retry in 45s", 45),
    ("no hint here", None),
])
def test_parse_retry_after(text, seconds):
    assert providers.parse_retry_after(text) == seconds


# ---------- cooldowns

def test_quota_cooldown_uses_retry_delay(mocker):
    t = [1000.0]
    mocker.patch.object(providers.time, "time", side_effect=lambda: t[0])
    providers.record_failure("g", rate_limit(GEMINI_QUOTA))
    assert not providers.is_available("g")
    t[0] += 22031
    assert providers.is_available("g")


def test_cooldown_summary_counts_down(mocker):
    t = [0.0]
    mocker.patch.object(providers.time, "time", side_effect=lambda: t[0])
    providers.record_failure("g", rate_limit(GEMINI_QUOTA))
    t[0] += 3 * 3600
    assert providers.cooldown("g").summary.endswith("resets in ~3h07m")


def test_fatal_error_has_no_cooldown():
    providers.record_failure("m", ValueError("bad request"))
    assert providers.is_available("m")


# ---------- end to end

@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_quota_error_is_not_retried_and_falls_back(mocker, client):
    rec = Recorder(rate_limit(GEMINI_QUOTA, "simple-model"), reply("from complex"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("explain asyncio")
    assert [c["model"] for c in rec.calls] == ["simple-model", "complex-model"]
    n = notes(msgs)
    assert not any("retrying" in x for x in n)
    assert any("Fell back" in x or "Escalated to complex-model: simple-model quota exhausted" in x for x in n)


def test_model_in_cooldown_is_skipped_without_a_request(mocker, client):
    providers.record_failure("simple-model", rate_limit(GEMINI_QUOTA))
    rec = Recorder(reply("from complex"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("hello")
    assert [c["model"] for c in rec.calls] == ["complex-model"]
    assert any(x.startswith("Skipping simple-model quota exhausted") for x in notes(msgs))


def test_both_models_down_gives_one_clear_error(mocker, tmp_path):
    client = make_client(mocker, tmp_path, tier="complex")
    providers.record_failure("simple-model", rate_limit(GEMINI_QUOTA))
    rec = Recorder(rate_limit("Error code: 429 insufficient_quota", "complex-model"))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("refactor")
    errors = [m["content"] for m in msgs if m["type"] == "error"]
    assert len(errors) == 1
    assert "complex-model out of credit" in errors[0]
    assert "Fallback simple-model quota exhausted" in errors[0]
    assert "vertex_ai" not in errors[0] and len(errors[0]) < 250


def test_cooldowns_endpoint(client):
    providers.record_failure("simple-model", rate_limit(GEMINI_QUOTA))
    r = client.get("/api/cooldowns", headers={"Authorization": "Bearer test-token"})
    assert r.json()["simple-model"]["kind"] == "quota"
    assert client.get("/api/cooldowns").status_code == 401


@pytest.mark.asyncio
async def test_conductor_skipped_when_cooling_down(mocker):
    import optimux_backend.core.llm_client as llm
    mocker.patch.object(llm, "get_routing_config", return_value={"conductor_model": "cond"})
    providers.record_failure("cond", rate_limit(GEMINI_QUOTA))
    call = mocker.patch.object(llm, "acompletion")
    result = await llm.route_model_with_usage("hi")
    assert result[:6] == ("simple", "cond", None, 0, None, False)
    assert result[6].startswith("it is paused after errors: quota exhausted")
    call.assert_not_called()


@pytest.mark.asyncio
async def test_conductor_quota_error_starts_cooldown(mocker):
    import optimux_backend.core.llm_client as llm
    mocker.patch.object(llm, "get_routing_config", return_value={"conductor_model": "cond"})
    mocker.patch.object(llm, "acompletion", side_effect=rate_limit(GEMINI_QUOTA))
    assert (await llm.route_model_with_usage("hi"))[0] == "simple"
    assert not providers.is_available("cond")


def test_anthropic_credit_balance_is_out_of_credit():
    e = litellm.BadRequestError(
        message='{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low '
                'to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."}}',
        llm_provider="anthropic", model="anthropic/claude-sonnet-5-5")
    info = providers.classify(e)
    assert info.kind == "quota"
    assert info.summary.startswith("out of credit (credit balance too low)")


def test_model_not_found_is_one_clear_line():
    e = litellm.NotFoundError(
        message='AnthropicException - {"type":"error","error":{"type":"not_found_error","message":"model: claude-3-5-sonnet-20240620"}}',
        llm_provider="anthropic", model="anthropic/claude-3-5-sonnet-20240620")
    info = providers.classify(e)
    assert info.kind == "fatal"
    assert info.summary == "model not found: check the model ID (it may be retired or misspelled)"


def test_a_provider_message_inside_json_becomes_the_summary():
    e = litellm.BadRequestError(
        message='GeminiException BadRequestError - {\n  "error": {\n    "code": 400,\n    "message": '
                '"Thinking budget is not supported for this model.",\n    "status": "INVALID_ARGUMENT"\n  }\n}',
        llm_provider="gemini", model="gemini/gemma-4-26b-it")
    info = providers.classify(e)
    assert info.kind == "fatal" and info.summary == "refused (400): Thinking budget is not supported for this model."
