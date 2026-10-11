"""Usage per project, local models as free, price status per model."""
import json

import pytest

import optimux_backend.core.usage as usage
import optimux_backend.core.config_manager as cm
from optimux_backend.core.config_manager import price_source
from tests.ws_helpers import Conversation, make_client
from tests.test_routing_session import Recorder, reply

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def log(mocker, tmp_path):
    mocker.patch.object(usage, "USAGE_FILE", str(tmp_path / "u.jsonl"))
    mocker.patch.object(usage, "_custom_prices", return_value=None)


def test_record_keeps_project(log):
    assert usage.record("s", "simple", "m", {}, project="alpha")["project"] == "alpha"
    assert usage.record("s", "simple", "m", {})["project"] == usage.NO_PROJECT


def test_summary_by_project_and_filter(log, mocker):
    mocker.patch.object(usage, "cost_for", side_effect=lambda m, t: 0.01)
    usage.record("s", "simple", "m", {"prompt_tokens": 10}, project="alpha")
    usage.record("s", "simple", "m", {"prompt_tokens": 20}, project="alpha")
    usage.record("s", "complex", "m", {"prompt_tokens": 5}, project="beta")
    usage.insert({"ts": 1e9, "role": "simple", "model": "m", "prompt_tokens": 1, "cost": 0.5})  # before projects were recorded
    s = usage.summary()["all_time"]["by_project"]
    assert s["alpha"]["calls"] == 2 and s["alpha"]["cost"] == pytest.approx(0.02)
    assert s["beta"]["prompt_tokens"] == 5 and s["(unknown)"]["calls"] == 1
    only = usage.summary(project="alpha")["all_time"]
    assert only["total"]["calls"] == 2 and list(only["by_project"]) == ["alpha"]


def test_local_models_cost_nothing(log):
    tokens = {"prompt_tokens": 1000, "completion_tokens": 100, "cached_tokens": 0}
    assert usage.cost_for("ollama/phi3", tokens) == 0.0
    assert usage.cost_for("nonexistent/model", tokens) is None


def test_price_source():
    assert price_source("x/y", {"input_cost_per_mtok": 1, "output_cost_per_mtok": 2}) == "custom"
    assert price_source("ollama/phi3", {}) == "local"
    assert price_source("gpt-4o", {}) == "litellm"
    assert price_source("made-up/model-zzz", {}) == "unknown"


def test_meter_records_active_project_and_api_filters(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    import optimux_backend.main as main
    mocker.patch.object(main, "get_settings", return_value={"active_project": "alpha"})
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("hi")))
    Conversation(client).prompt("hello")
    by_project = client.get("/api/usage", headers=H).json()["today"]["by_project"]
    assert set(by_project) == {"alpha"}
    assert client.get("/api/usage?project=beta", headers=H).json()["today"]["total"]["calls"] == 0
