import json
import time

import pytest

import optimux_backend.core.usage as usage


@pytest.fixture(autouse=True)
def isolated_log(mocker, tmp_path):
    mocker.patch.object(usage, "USAGE_FILE", str(tmp_path / "usage.jsonl"))
    mocker.patch.object(usage, "_custom_prices", return_value=None)


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_extract_usage_openai_style():
    u = Obj(prompt_tokens=1000, completion_tokens=50, prompt_tokens_details=Obj(cached_tokens=800))
    assert usage.extract_usage(u) == {"prompt_tokens": 1000, "completion_tokens": 50, "cached_tokens": 800, "reasoning_tokens": 0}


def test_extract_usage_anthropic_style_and_dict():
    u = {"prompt_tokens": 10, "completion_tokens": 5, "cache_read_input_tokens": 7}
    assert usage.extract_usage(u)["cached_tokens"] == 7


def test_extract_usage_ignores_non_ints():
    from unittest.mock import MagicMock
    assert usage.extract_usage(MagicMock()) == {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0}
    assert usage.extract_usage(None)["prompt_tokens"] == 0


def test_custom_price_with_cache_rate(mocker):
    mocker.patch.object(usage, "_custom_prices", return_value={
        "input_cost_per_mtok": 2.0, "output_cost_per_mtok": 10.0, "cached_input_cost_per_mtok": 0.5,
    })
    tokens = {"prompt_tokens": 1_000_000, "completion_tokens": 100_000, "cached_tokens": 500_000}
    # 0.5M * 2 + 0.5M * 0.5 + 0.1M * 10 = 1 + 0.25 + 1
    assert usage.cost_for("x", tokens) == pytest.approx(2.25)


def test_custom_price_without_cache_rate_bills_cached_as_input(mocker):
    mocker.patch.object(usage, "_custom_prices", return_value={
        "input_cost_per_mtok": 1.0, "output_cost_per_mtok": 1.0,
    })
    tokens = {"prompt_tokens": 1_000_000, "completion_tokens": 0, "cached_tokens": 1_000_000}
    assert usage.cost_for("x", tokens) == pytest.approx(1.0)


def test_unknown_model_cost_is_none():
    tokens = {"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 0}
    assert usage.cost_for("nonexistent-provider/made-up-model", tokens) is None


def test_record_and_summary_round_trip():
    usage.record("s1", "conductor", "m1", {"prompt_tokens": 100, "completion_tokens": 5})
    usage.record("s1", "simple", "m2", {"prompt_tokens": 1000, "completion_tokens": 200, "cached_tokens": 600})
    s = usage.summary()
    assert s["today"]["total"]["calls"] == 2
    assert s["all_time"]["total"]["prompt_tokens"] == 1100
    assert s["today"]["by_role"]["conductor"]["completion_tokens"] == 5
    assert s["today"]["by_model"]["m2"]["cached_tokens"] == 600
    assert s["today"]["total"]["calls_without_cost"] == 2  # unknown models


def test_summary_periods():
    with open(usage.USAGE_FILE, "w") as f:
        old = {"ts": time.time() - 10 * 86400, "role": "simple", "model": "m", "prompt_tokens": 1, "completion_tokens": 1, "cost": 0.5}
        f.write(json.dumps(old) + "\n")
        f.write("not json\n")
    usage.record("s", "simple", "m", {"prompt_tokens": 1, "completion_tokens": 1})
    s = usage.summary()
    assert s["all_time"]["total"]["calls"] == 2
    assert s["last_7_days"]["total"]["calls"] == 1
    assert s["all_time"]["total"]["cost"] == pytest.approx(0.5)


def test_estimated_flag_kept():
    entry = usage.record("s", "simple", "m", {"prompt_tokens": 3, "completion_tokens": 2, "estimated": True})
    assert entry["estimated"] is True


def test_record_never_raises(mocker):
    mocker.patch.object(usage, "USAGE_FILE", "/proc/forbidden/usage.jsonl")
    entry = usage.record("s", "simple", "m", None)
    assert entry["prompt_tokens"] == 0
