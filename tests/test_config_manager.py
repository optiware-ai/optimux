import pytest
import os
import json
from optimux_backend.core.config_manager import (
    get_models_config, save_api_key_to_env, ApiKeyPayload, LEGACY_SUPPORTED_MODELS, get_routing_config,
    save_model, ModelPayload, save_routing_config, RoutingPayload, model_id_problem, api_key_for, litellm_key_kwargs,
)
from optimux_backend.core.security import ValidationError
import optimux_backend.core.config_manager as config_manager

def test_get_models_config_all_missing(mocker, tmp_path):
    mocker.patch.dict(os.environ, {}, clear=True)
    temp_models = tmp_path / "models.json"
    mocker.patch.object(config_manager, 'MODELS_FILE', str(temp_models))
    
    config = get_models_config()
    assert len(config["available"]) == 0
    assert len(config["requires_setup"]) == len(LEGACY_SUPPORTED_MODELS)

def test_get_models_config_some_available(mocker, tmp_path):
    mocker.patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"}, clear=True)
    temp_models = tmp_path / "models.json"
    mocker.patch.object(config_manager, 'MODELS_FILE', str(temp_models))
    
    config = get_models_config()
    
    assert len(config["available"]) == 1
    assert config["available"][0]["id"] == "gemini/gemini-3.6-flash"
    assert len(config["requires_setup"]) == len(LEGACY_SUPPORTED_MODELS) - 1

def test_save_api_key_to_env_creates_new(mocker, tmp_path):
    env = tmp_path / "secrets.env"
    mocker.patch.object(config_manager, "SECRETS_FILE", str(env))   # ~/.optimux/secrets.env, not the repo
    mocker.patch.dict(os.environ, {}, clear=False)

    save_api_key_to_env(ApiKeyPayload(env_key="TEST_KEY", api_key="test_value"))

    assert os.environ["TEST_KEY"] == "test_value"
    assert env.read_text() == "TEST_KEY=test_value\n"
    assert oct(env.stat().st_mode & 0o777) == "0o600"

def test_save_api_key_to_env_updates_existing(mocker, tmp_path):
    env = tmp_path / "secrets.env"
    env.write_text("OTHER_KEY=123\nTEST_KEY=old_value\n")
    mocker.patch.object(config_manager, "SECRETS_FILE", str(env))
    mocker.patch.dict(os.environ, {}, clear=False)

    save_api_key_to_env(ApiKeyPayload(env_key="TEST_KEY", api_key="new_value"))

    assert os.environ["TEST_KEY"] == "new_value"
    assert env.read_text() == "OTHER_KEY=123\nTEST_KEY=new_value\n"

def test_get_routing_config(mocker, tmp_path):
    temp_routing = tmp_path / "routing.json"
    mocker.patch.object(config_manager, 'ROUTING_FILE', str(temp_routing))
    
    temp_routing.write_text(json.dumps({
        "conductor_model": "test-conductor",
        "simple_model": "test-simple",
        "tool_model": "test-tool",
        "complex_model": "test-complex"
    }))
    
    config = get_routing_config()
    assert config["conductor_model"] == "test-conductor"
    assert config["tool_model"] == "test-tool"

# ---------- keys

def test_save_api_key_strips_whitespace(mocker):
    mocker.patch("builtins.open", mocker.mock_open())
    mocker.patch("os.path.exists", return_value=False)
    save_api_key_to_env(ApiKeyPayload(env_key="TEST_KEY", api_key="  sk-123 \t"))
    assert os.environ["TEST_KEY"] == "sk-123"

def test_save_api_key_rejects_empty(mocker):
    mocker.patch("builtins.open", mocker.mock_open())
    with pytest.raises(ValidationError):
        save_api_key_to_env(ApiKeyPayload(env_key="TEST_KEY", api_key="   "))

def test_models_config_reports_id_problems_and_prices(mocker, tmp_path):
    mocker.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "apikey_01x"}, clear=True)
    models = tmp_path / "models.json"
    models.write_text(json.dumps({
        "claude-3-5-sonnet-20240620": {"name": "Old", "env_key": "ANTHROPIC_API_KEY"},
        "anthropic/claude-sonnet-5-5": {"name": "New", "env_key": "ANTHROPIC_API_KEY", "input_cost_per_mtok": 3.0},
    }))
    mocker.patch.object(config_manager, 'MODELS_FILE', str(models))
    by_id = {m["id"]: m for m in get_models_config()["available"]}
    assert "provider prefix" in by_id["claude-3-5-sonnet-20240620"]["problem"]
    assert by_id["anthropic/claude-sonnet-5-5"]["problem"] is None
    assert "key_warning" not in by_id["anthropic/claude-sonnet-5-5"]  # key formats are not guessed
    assert by_id["anthropic/claude-sonnet-5-5"]["input_cost_per_mtok"] == 3.0

# ---------- model IDs and routing

@pytest.mark.parametrize("model_id,ok", [
    ("anthropic/claude-sonnet-5-5", True),
    ("gemini/gemini-3.6-flash", True),
    ("ollama/phi3", True),
    ("claude-3-5-sonnet-20240620", False),
    ("not-a-model", False),
    ("", False),
])
def test_model_id_problem(model_id, ok):
    assert (model_id_problem(model_id) is None) == ok

def test_save_model_rejects_unroutable_id(mocker, tmp_path):
    mocker.patch.object(config_manager, 'MODELS_FILE', str(tmp_path / "models.json"))
    with pytest.raises(ValidationError, match="provider prefix"):
        save_model(ModelPayload(id="claude-3-5-sonnet-20240620", name="x"))

def test_save_routing_requires_configured_models(mocker, tmp_path):
    mocker.patch.object(config_manager, 'MODELS_FILE', str(tmp_path / "models.json"))
    mocker.patch.object(config_manager, 'ROUTING_FILE', str(tmp_path / "routing.json"))
    with pytest.raises(ValidationError, match="not a configured model"):
        save_routing_config(RoutingPayload(conductor_model="off", simple_model="ghost/model"))
    save_routing_config(RoutingPayload(conductor_model="off", simple_model="gemini/gemini-3.6-flash",
                                       complex_model="anthropic/claude-sonnet-5-5"))
    assert get_routing_config()["conductor_model"] == "off"


# ---------- per-model API keys

def test_api_key_for_uses_the_models_own_env_key(mocker, tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps({
        "anthropic/claude-sonnet-5-5": {"name": "C", "env_key": "CLAUDE_API_KEY"},
        "ollama/phi3": {"name": "P", "env_key": ""},
        "gemini/x": {"name": "G", "env_key": "GEMINI_API_KEY"},
    }))
    mocker.patch.object(config_manager, 'MODELS_FILE', str(models))
    mocker.patch.dict(os.environ, {"CLAUDE_API_KEY": "secret-c"}, clear=True)
    assert api_key_for("anthropic/claude-sonnet-5-5") == "secret-c"
    assert litellm_key_kwargs("anthropic/claude-sonnet-5-5") == {"api_key": "secret-c"}
    assert api_key_for("ollama/phi3") is None          # no env key: LiteLLM default
    assert litellm_key_kwargs("gemini/x") == {}         # env key unset: LiteLLM default
    assert litellm_key_kwargs("unknown/model") == {}


# ---------- models behind their own server: proxies, resellers, own servers (v0.4-5o)

@pytest.fixture
def models_file(mocker, tmp_path):
    f = tmp_path / "models.json"
    f.write_text(json.dumps({"anthropic/claude-sonnet-5": {"name": "Sonnet", "env_key": "ANTHROPIC_API_KEY"}}))
    mocker.patch.object(config_manager, 'MODELS_FILE', str(f))
    mocker.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "real-anthropic", "APMIX_API_KEY": "apx"}, clear=True)
    return f


def test_a_base_url_is_saved_and_sent_with_the_models_own_key(models_file):
    save_model(ModelPayload(id="anthropic/claude-sonnet-4-6-free", name="Apmix", env_key="APMIX_API_KEY",
                            api_base=" https://api.apmix.ai/ "))
    saved = json.loads(models_file.read_text())["anthropic/claude-sonnet-4-6-free"]
    assert saved["api_base"] == "https://api.apmix.ai"
    assert litellm_key_kwargs("anthropic/claude-sonnet-4-6-free") == {"api_base": "https://api.apmix.ai",
                                                                     "api_key": "apx"}
    assert litellm_key_kwargs("anthropic/claude-sonnet-5") == {"api_key": "real-anthropic"}   # unchanged
    entry = [m for m in get_models_config()["available"] if m["id"] == "anthropic/claude-sonnet-4-6-free"][0]
    assert entry["api_host"] == "api.apmix.ai" and entry["price_source"] == "proxy"


def test_a_model_behind_a_base_url_never_gets_the_providers_default_key(models_file):
    """Without its own key, LiteLLM would fall back to ANTHROPIC_API_KEY and send
    the real Anthropic key to the other server."""
    save_model(ModelPayload(id="openai/my-local", name="Local", env_key="", api_base="http://192.168.1.5:8000/v1"))
    assert litellm_key_kwargs("openai/my-local") == {"api_base": "http://192.168.1.5:8000/v1", "api_key": "none"}


def test_a_key_is_never_shared_between_different_servers(models_file):
    with pytest.raises(ValidationError, match=r"ANTHROPIC_API_KEY is already the key of Sonnet .*APMIX_API_KEY"):
        save_model(ModelPayload(id="anthropic/claude-sonnet-4-6-free", name="Apmix", env_key="ANTHROPIC_API_KEY",
                                api_base="https://api.apmix.ai"))
    save_model(ModelPayload(id="anthropic/a", name="A", env_key="APMIX_API_KEY", api_base="https://api.apmix.ai"))
    save_model(ModelPayload(id="anthropic/b", name="B", env_key="APMIX_API_KEY", api_base="https://api.apmix.ai"))
    with pytest.raises(ValidationError, match="goes to api.apmix.ai"):        # and the other way round
        save_model(ModelPayload(id="anthropic/c", name="C", env_key="APMIX_API_KEY"))


def test_other_saves_keep_the_base_url_and_empty_removes_it(models_file):
    save_model(ModelPayload(id="openai/x", name="X", env_key="APMIX_API_KEY", api_base="https://api.apmix.ai/v1"))
    save_model(ModelPayload(id="openai/x", name="X", env_key="APMIX_API_KEY", input_cost_per_mtok=0,
                            output_cost_per_mtok=0))                       # a price edit sends no api_base
    assert json.loads(models_file.read_text())["openai/x"]["api_base"] == "https://api.apmix.ai/v1"
    save_model(ModelPayload(id="openai/x", name="X", env_key="", api_base=""))
    assert "api_base" not in json.loads(models_file.read_text())["openai/x"]


@pytest.mark.parametrize("bad", ["api.apmix.ai", "ftp://x.y", "https://user:pw@x.y", "https://x.y/v1?k=1",
                                 "https://x y.z", "https://"])
def test_bad_base_urls_are_refused(models_file, bad):
    with pytest.raises(ValidationError):
        save_model(ModelPayload(id="openai/x", name="X", env_key="X_API_KEY", api_base=bad))


def test_suggested_key_names():
    s = config_manager.suggested_env_key
    assert s("groq/llama-3.3-70b") == "GROQ_API_KEY"
    assert s("together_ai/meta-llama/x") == "TOGETHERAI_API_KEY"
    assert s("anthropic/claude-sonnet-5") == "ANTHROPIC_API_KEY"
    assert s("ollama/phi3") == ""
    assert s("anthropic/claude-sonnet-4-6-free", "https://api.apmix.ai") == "APMIX_API_KEY"
    assert s("openai/x", "https://openrouter.ai/api/v1") == "OPENROUTER_API_KEY"
    assert s("openai/x", "http://localhost:1234/v1") == ""
    assert s("not-a-provider-id") == ""


def test_prices_of_a_proxied_model_are_never_litellms(mocker, models_file):
    import optimux_backend.core.usage as usage
    save_model(ModelPayload(id="anthropic/claude-sonnet-5-5", name="via", env_key="APMIX_API_KEY",
                            api_base="https://api.apmix.ai"))
    tokens = {"prompt_tokens": 1000, "completion_tokens": 100, "cached_tokens": 0}
    assert usage.cost_for("anthropic/claude-sonnet-5-5", tokens) is None
    save_model(ModelPayload(id="anthropic/claude-sonnet-5-5", name="via", env_key="APMIX_API_KEY",
                            input_cost_per_mtok=1, output_cost_per_mtok=10))
    assert usage.cost_for("anthropic/claude-sonnet-5-5", tokens) == pytest.approx(0.002)
