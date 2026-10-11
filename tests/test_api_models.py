"""Model test endpoint, key save response, and the usage summary durations."""
import os

import litellm
import pytest

import optimux_backend.core.usage as usage
from optimux_backend.core import providers
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_model_test_ok(mocker, client):
    resp = mocker.MagicMock()
    resp.usage = {"prompt_tokens": 8, "completion_tokens": 1}
    mocker.patch("litellm.acompletion", return_value=resp)
    r = client.post("/api/models/test", json={"id": "anthropic/claude-sonnet-5-5"}, headers=H).json()
    assert r["ok"] is True and r["message"].startswith("Works")
    assert usage.summary()["today"]["by_role"]["test"]["calls"] == 1


def test_model_test_bad_key(mocker, client):
    err = litellm.AuthenticationError(message='{"type":"authentication_error","message":"invalid x-api-key"}',
                                      llm_provider="anthropic", model="anthropic/claude-sonnet-5-5")
    mocker.patch("litellm.acompletion", side_effect=err)
    r = client.post("/api/models/test", json={"id": "anthropic/claude-sonnet-5-5"}, headers=H).json()
    assert r["ok"] is False and r["kind"] == "fatal" and "invalid x-api-key" in r["message"]
    assert providers.is_available("anthropic/claude-sonnet-5-5")  # a test doesn't start a cooldown


def test_model_test_unroutable_id_makes_no_call(mocker, client):
    call = mocker.patch("litellm.acompletion")
    r = client.post("/api/models/test", json={"id": "claude-3-5-sonnet-20240620"}, headers=H).json()
    assert r["ok"] is False and r["kind"] == "config"
    call.assert_not_called()


def test_model_test_requires_token(client):
    assert client.post("/api/models/test", json={"id": "x"}).status_code == 401


def test_save_key_response_has_no_format_warning(mocker, client):
    mocker.patch("optimux_backend.core.config_manager.open", mocker.mock_open(), create=True)
    mocker.patch("os.path.exists", return_value=False)
    r = client.post("/api/keys", json={"env_key": "ANTHROPIC_API_KEY", "api_key": "apikey_01x"}, headers=H).json()
    assert r["status"] == "success" and "warning" not in r
    os.environ.pop("ANTHROPIC_API_KEY", None)


def test_model_test_passes_the_models_api_key(mocker, client):
    mocker.patch("optimux_backend.main.litellm_key_kwargs", return_value={"api_key": "secret-c"})
    resp = mocker.MagicMock()
    resp.usage = {"prompt_tokens": 1, "completion_tokens": 1}
    call = mocker.patch("litellm.acompletion", return_value=resp)
    client.post("/api/models/test", json={"id": "anthropic/claude-sonnet-5-5"}, headers=H)
    assert call.call_args.kwargs["api_key"] == "secret-c"


def test_save_model_with_bad_id_is_400(client):
    r = client.post("/api/models", json={"id": "claude-3-5-sonnet-20240620", "name": "x"}, headers=H)
    assert r.status_code == 400 and "provider prefix" in r.json()["detail"]


def test_summary_average_duration(mocker, tmp_path):
    mocker.patch.object(usage, "USAGE_FILE", str(tmp_path / "u.jsonl"))
    mocker.patch.object(usage, "_custom_prices", return_value=None)
    usage.record("s", "simple", "m", {"prompt_tokens": 1}, 1000)
    usage.record("s", "simple", "m", {"prompt_tokens": 1}, 3000)
    usage.record("s", "simple", "m", {"prompt_tokens": 1})  # untimed
    b = usage.summary()["today"]["by_role"]["simple"]
    assert (b["timed_calls"], b["avg_duration_ms"]) == (2, 2000)


# ---------- clearing cooldowns

GEMINI_QUOTA = 'Quota exceeded for metric: generate_content_free_tier_requests, limit: 20 "retryDelay": "22030s"'


def quota_error(model):
    return litellm.RateLimitError(message=GEMINI_QUOTA, llm_provider="gemini", model=model)


def test_saving_a_key_clears_cooldowns_of_models_using_it(mocker, client, tmp_path):
    import optimux_backend.core.config_manager as cm
    import json as _json
    models = tmp_path / "models.json"
    models.write_text(_json.dumps({
        "gemini/a": {"name": "A", "env_key": "GEMINI_API_KEY"},
        "gemini/b": {"name": "B", "env_key": "GEMINI_API_KEY"},
        "anthropic/c": {"name": "C", "env_key": "ANTHROPIC_API_KEY"},
    }))
    mocker.patch.object(cm, "MODELS_FILE", str(models))
    mocker.patch.object(cm, "open", mocker.mock_open(read_data=models.read_text()), create=True)
    mocker.patch("os.path.exists", return_value=False)
    for m in ("gemini/a", "gemini/b", "anthropic/c"):
        providers.record_failure(m, quota_error(m))
    mocker.patch("optimux_backend.main.get_all_models", return_value=_json.loads(models.read_text()))
    r = client.post("/api/keys", json={"env_key": "GEMINI_API_KEY", "api_key": "AIzaNew"}, headers=H).json()
    assert sorted(r["cooldowns_cleared"]) == ["gemini/a", "gemini/b"]
    assert providers.is_available("gemini/a") and not providers.is_available("anthropic/c")
    os.environ.pop("GEMINI_API_KEY", None)


def test_successful_test_clears_cooldown(mocker, client):
    providers.record_failure("gemini/a", quota_error("gemini/a"))
    resp = mocker.MagicMock()
    resp.usage = {"prompt_tokens": 5, "completion_tokens": 1}
    mocker.patch("litellm.acompletion", return_value=resp)
    r = client.post("/api/models/test", json={"id": "gemini/a"}, headers=H).json()
    assert r["ok"] and r["cooldown_cleared"] and "Cooldown cleared" in r["message"]
    assert providers.is_available("gemini/a")


def test_failed_test_keeps_cooldown(mocker, client):
    providers.record_failure("gemini/a", quota_error("gemini/a"))
    mocker.patch("litellm.acompletion", side_effect=quota_error("gemini/a"))
    r = client.post("/api/models/test", json={"id": "gemini/a"}, headers=H).json()
    assert not r["ok"] and not providers.is_available("gemini/a")


def test_clear_endpoint(client):
    providers.record_failure("x/a", quota_error("x/a"))
    providers.record_failure("x/b", quota_error("x/b"))
    assert client.post("/api/cooldowns/clear", json={"model": "x/a"}, headers=H).json() == {"cleared": ["x/a"]}
    assert client.post("/api/cooldowns/clear", json={"model": "x/a"}, headers=H).json() == {"cleared": []}
    assert client.post("/api/cooldowns/clear", json={}, headers=H).json() == {"cleared": ["x/b"]}
    assert providers.active_cooldowns() == {}
    assert client.post("/api/cooldowns/clear", json={}).status_code == 401
