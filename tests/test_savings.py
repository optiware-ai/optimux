"""IM-08 (v0.6): spent against what the same calls would have cost on the
reference model (the Complex model at the time of each call)."""
import time

import pytest

import optimux_backend.core.analytics as an
import optimux_backend.core.usage as usage
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
# $ per million tokens: input, cached input, output
PRICES = {"cheap": (0.1, 0.01, 0.4), "strong": (3.0, 0.3, 15.0)}


@pytest.fixture
def priced(mocker):
    def custom(model):
        if model not in PRICES:
            return None
        i, c, o = PRICES[model]
        return {"input_cost_per_mtok": i, "cached_input_cost_per_mtok": c, "output_cost_per_mtok": o}
    mocker.patch.object(usage, "_custom_prices", side_effect=custom)
    mocker.patch.object(usage, "reference_model", return_value="strong")


TOKENS = {"prompt_tokens": 10_000, "completion_tokens": 1_000, "cached_tokens": 4_000}


def test_each_call_stores_its_reference(priced):
    e = usage.record("s", "simple", "cheap", dict(TOKENS), project="alpha")
    # cheap: 6000*0.1 + 4000*0.01 + 1000*0.4 = 1040 -> $0.00104; strong: 6000*3 + 4000*0.3 + 1000*15 = 34200
    assert e["cost"] == pytest.approx(0.00104) and e["ref_model"] == "strong" and e["ref_cost"] == pytest.approx(0.0342)
    c = usage.record("s", "conductor", "cheap", dict(TOKENS))
    assert c["ref_cost"] == 0.0                                          # routing itself: overhead
    t = usage.record("s", "test", "cheap", dict(TOKENS))
    assert "ref_cost" not in t                                           # a model test isn't work
    s = usage.record("s", "complex", "strong", dict(TOKENS))
    assert s["ref_cost"] == pytest.approx(s["cost"])                     # the reference itself: no saving
    u = usage.record("s", "simple", "unpriced-model", dict(TOKENS))
    assert u["cost"] is None and u["ref_cost"] == pytest.approx(0.0342)
    stored = {r["role"]: r for r in usage.read_entries()}
    assert stored["simple"]["ref_cost"] is not None and stored["simple"]["ref_model"] == "strong"


def test_savings_by_hand(priced):
    usage.record("s", "simple", "cheap", dict(TOKENS), project="alpha")      # 0.00104 vs 0.0342
    usage.record("s", "simple", "cheap", dict(TOKENS), project="beta")       # 0.00104 vs 0.0342
    usage.record("s", "conductor", "cheap", dict(TOKENS), project="alpha")   # 0.00104 vs 0
    usage.record("s", "complex", "strong", dict(TOKENS), project="alpha")    # 0.0342 vs 0.0342
    usage.record("s", "simple", "unpriced-model", dict(TOKENS))              # not measured
    usage.record("s", "test", "cheap", dict(TOKENS))                         # not counted at all
    s = an.savings(0)
    assert s["calls"] == 4 and s["not_measured_calls"] == 1 and s["estimated_calls"] == 0
    assert s["spent"] == pytest.approx(0.00104 * 3 + 0.0342)
    assert s["reference"] == pytest.approx(0.0342 * 3)
    assert s["saved"] == pytest.approx(0.0342 * 2 - 0.00104 * 3)
    assert s["saved_share"] == pytest.approx((0.0342 * 2 - 0.00104 * 3) / (0.0342 * 3))
    assert s["by_role"]["conductor"]["saved"] == pytest.approx(-0.00104)    # overhead shows as a cost
    assert s["by_project"]["beta"]["saved"] == pytest.approx(0.0342 - 0.00104)
    assert s["reference_models"] == {"strong": 4}
    only = an.savings(0, "beta")
    assert only["calls"] == 1 and set(only["by_project"]) == {"beta"}


def test_calls_from_before_are_estimated_with_todays_reference(priced):
    usage.insert({"ts": time.time() - 60, "project": "alpha", "role": "simple", "model": "cheap",
                  **TOKENS, "cost": 0.00104})                              # stored before v0.6: no ref columns
    s = an.savings(0)
    assert s["estimated_calls"] == 1 and s["reference"] == pytest.approx(0.0342)


def test_the_performance_page_and_the_endpoint(priced, mocker, tmp_path):
    usage.record("s", "simple", "cheap", dict(TOKENS), project="alpha")
    assert an.analytics(30)["savings"]["calls"] == 1
    client = make_client(mocker, tmp_path)
    r = client.get("/api/savings?period=month", headers=H).json()
    assert r["calls"] == 1 and r["saved"] == pytest.approx(0.0342 - 0.00104)
    assert client.get("/api/savings").status_code == 401
