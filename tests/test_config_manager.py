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
