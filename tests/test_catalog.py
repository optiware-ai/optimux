"""v0.3: the model catalog (Models screen): offline list, opt-in online refresh."""
import copy
import json
import os

import httpx
import litellm
import pytest

from optimux_backend.core import catalog, paths


@pytest.fixture
def cat(monkeypatch, tmp_path):
    """A private data folder, LiteLLM's price table restored after the test, and
    any real network call made impossible."""
    monkeypatch.setattr(paths, "HOME", str(tmp_path / "home"))
    monkeypatch.setattr(litellm, "model_cost", copy.deepcopy(litellm.model_cost))
    for group in ("open_ai_chat_completion_models", "anthropic_models"):
        if isinstance(getattr(litellm, group, None), set):
            monkeypatch.setattr(litellm, group, set(getattr(litellm, group)))
    calls = []

    def no_network(*a, **k):
        raise AssertionError(f"unexpected network call: {a}")
    monkeypatch.setattr(httpx, "get", no_network)
    monkeypatch.setattr(catalog, "online_allowed", lambda: False)
    return calls


class Resp:
    def __init__(self, status=200, data=None, raw=None):
        self.status_code, self._data = status, data
        self.content = raw if raw is not None else json.dumps(data).encode()

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def fake_get(monkeypatch, cat, answer):
    def get(url, headers=None, **k):
        cat.append((url, dict(headers or {})))
        return answer(url) if callable(answer) else answer
    monkeypatch.setattr(httpx, "get", get)


def test_offline_list_needs_no_network(cat):
    rows = catalog.offline_models("openai")
    ids = [r["id"] for r in rows]
    assert "gpt-5" in ids and not any(i.startswith("ft:") for i in ids)
    gpt5 = next(r for r in rows if r["id"] == "gpt-5")
    assert gpt5["input"] == 1.25 and gpt5["output"] == 10.0 and gpt5["tools"] is True and gpt5["context"]
    # prefixed providers: every ID routes in LiteLLM, bare keys get the prefix
    for pid in ("gemini", "deepseek", "xai", "openrouter", "ollama"):
        p = catalog.provider(pid)
        for r in catalog.offline_models(pid):
            assert r["id"].startswith(p["prefix"]), r["id"]
    inputs = [r["input"] for r in rows if r["input"] is not None]
    assert inputs == sorted(inputs)                                   # cheapest first
    assert cat == []


def test_summary_and_key_status(cat, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = {p["id"]: p for p in catalog.summary()["providers"]}
    assert s["anthropic"]["key_saved"] and not s["openai"]["key_saved"]
    assert s["ollama"]["key_saved"] and s["ollama"]["env"] is None    # local: no key
    assert catalog.models("openai")["key_saved"] is False


def test_refresh_is_refused_while_offline(cat, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    for call in (catalog.refresh_prices, lambda: catalog.refresh_models("openai")):
        with pytest.raises(catalog.CatalogError, match="online catalog is off"):
            call()
    assert cat == []                                                   # nothing contacted


def test_provider_models_with_the_users_key(cat, monkeypatch):
    monkeypatch.setattr(catalog, "online_allowed", lambda: True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    fake_get(monkeypatch, cat, Resp(data={"data": [{"id": "gpt-5"}, {"id": "gpt-6-brand-new"}]}))
    assert catalog.refresh_models("openai") == {"count": 2}
    url, headers = cat[0]
    assert url == "https://api.openai.com/v1/models" and headers == {"Authorization": "Bearer sk-secret"}
    m = {r["id"]: r for r in catalog.models("openai")["models"]}
    assert m["gpt-5"]["available"] is True and m["gpt-4o"]["available"] is False
    assert m["gpt-6-brand-new"]["new"] and m["gpt-6-brand-new"]["input"] is None   # not priced yet
    saved = open(os.path.join(paths.HOME, "catalog", "models-openai.json")).read()
    assert "sk-secret" not in saved                                    # the key is never stored here


def test_a_request_with_a_key_never_follows_a_redirect(cat, monkeypatch):
    monkeypatch.setattr(catalog, "online_allowed", lambda: True)
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret")
    seen = []
    def get(url, headers=None, follow_redirects=None, **k):
        seen.append((url, follow_redirects))
        return Resp(data={"models": []}) if headers else Resp(data={})
    monkeypatch.setattr(httpx, "get", get)
    catalog.refresh_models("gemini")
    assert seen[-1][1] is False         # x-goog-api-key would be sent on to another site
    catalog._http_get("https://example.org/list")
    assert seen[-1][1] is True          # no key: a moved public list is still found


@pytest.mark.parametrize("pid, header", [("anthropic", "x-api-key"), ("gemini", "x-goog-api-key")])
def test_anthropic_and_gemini_formats(cat, monkeypatch, pid, header):
    monkeypatch.setattr(catalog, "online_allowed", lambda: True)
    env = catalog.provider(pid)["env"]
    monkeypatch.setenv(env, "k-123")
    data = ({"data": [{"id": "claude-sonnet-4-5"}]} if pid == "anthropic" else
            {"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]}]})
    fake_get(monkeypatch, cat, Resp(data=data))
    assert catalog.refresh_models(pid) == {"count": 1}
    assert cat[0][1][header] == "k-123"
    expected = "claude-sonnet-4-5" if pid == "anthropic" else "gemini/gemini-2.5-flash"
    assert next(r for r in catalog.models(pid)["models"] if r["id"] == expected)["available"]


def test_openrouter_gives_live_prices(cat, monkeypatch):
    monkeypatch.setattr(catalog, "online_allowed", lambda: True)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake_get(monkeypatch, cat, Resp(data={"data": [{"id": "acme/super-1", "context_length": 128000,
                                                     "pricing": {"prompt": "0.000002", "completion": "0.000008"}}]}))
    catalog.refresh_models("openrouter")                               # its list needs no key
    row = next(r for r in catalog.models("openrouter")["models"] if r["id"] == "openrouter/acme/super-1")
    assert row["input"] == 2.0 and row["output"] == 8.0 and row["context"] == 128000


def test_ollama_is_asked_on_this_machine_even_offline(cat, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:11434")
    fake_get(monkeypatch, cat, Resp(data={"models": [{"name": "qwen2.5-coder:7b"}]}))
    assert catalog.refresh_models("ollama") == {"count": 1}
    assert cat[0][0] == "http://127.0.0.1:11434/api/tags"


def test_clear_errors(cat, monkeypatch):
    monkeypatch.setattr(catalog, "online_allowed", lambda: True)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(catalog.CatalogError, match="Save your Mistral API key"):
        catalog.refresh_models("mistral")
    monkeypatch.setenv("MISTRAL_API_KEY", "bad")
    fake_get(monkeypatch, cat, Resp(status=401, data={}))
    with pytest.raises(catalog.CatalogError, match="refused the API key"):
        catalog.refresh_models("mistral")

    def down(url, **k):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(httpx, "get", down)
    with pytest.raises(catalog.CatalogError, match="proxy"):
        catalog.refresh_models("mistral")
    with pytest.raises(catalog.CatalogError, match="Unknown provider"):
        catalog.refresh_models("nope")


def test_price_refresh_updates_metering_and_survives_a_restart(cat, monkeypatch):
    monkeypatch.setattr(catalog, "online_allowed", lambda: True)
    fresh = copy.deepcopy(litellm.model_cost)
    fresh["gpt-5"]["input_cost_per_token"] = 9e-06
    fresh["gpt-7-future"] = {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 1e-06,
                             "output_cost_per_token": 2e-06, "supports_function_calling": True,
                             "evil": "x" * 10}
    fake_get(monkeypatch, cat, Resp(data=fresh))
    assert catalog.refresh_prices()["count"] > 1000
    assert cat[0][0] == litellm.model_cost_map_url
    assert litellm.cost_per_token(model="gpt-5", prompt_tokens=1_000_000, completion_tokens=0)[0] == 9.0
    assert litellm.get_llm_provider("gpt-7-future")[1] == "openai"    # a new model routes
    assert "evil" not in litellm.model_cost["gpt-7-future"]           # unknown fields dropped
    # after a restart: the saved list is used again, with no network
    monkeypatch.setattr(litellm, "model_cost", copy.deepcopy(litellm.model_cost))
    litellm.model_cost["gpt-5"]["input_cost_per_token"] = 1.25e-06
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert catalog.load_saved_prices() > 1000
    assert litellm.cost_per_token(model="gpt-5", prompt_tokens=1_000_000, completion_tokens=0)[0] == 9.0


@pytest.mark.parametrize("bad", [[1, 2, 3], {"a": {"litellm_provider": "x"}}, "not json"])
def test_a_broken_price_list_keeps_the_current_prices(cat, monkeypatch, bad):
    monkeypatch.setattr(catalog, "online_allowed", lambda: True)
    before = litellm.model_cost["gpt-5"]["input_cost_per_token"]
    fake_get(monkeypatch, cat, Resp(data=None, raw=b"<html>") if bad == "not json" else Resp(data=bad))
    with pytest.raises(catalog.CatalogError):
        catalog.refresh_prices()
    assert litellm.model_cost["gpt-5"]["input_cost_per_token"] == before
    assert not os.path.exists(os.path.join(paths.HOME, "catalog", "prices.json"))


def test_wrong_field_types_are_dropped(cat):
    raw = {f"m{i}": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 1e-6} for i in range(150)}
    raw["m0"]["input_cost_per_token"] = "free"                         # a string where a number belongs
    raw["m1"]["supports_function_calling"] = "yes"
    raw["m2"]["input_cost_per_token"] = -1
    cleaned = catalog._clean_prices(raw)
    assert "input_cost_per_token" not in cleaned["m0"] and "supports_function_calling" not in cleaned["m1"]
    assert "input_cost_per_token" not in cleaned["m2"]


# ---------- the API

def test_api(mocker, tmp_path, cat, monkeypatch):
    from tests.ws_helpers import make_client
    H = {"Authorization": "Bearer test-token"}
    client = make_client(mocker, tmp_path)
    s = client.get("/api/catalog", headers=H).json()
    assert s["online"] is False and {p["id"] for p in s["providers"]} >= {"openai", "anthropic", "gemini", "ollama"}
    rows = client.get("/api/catalog/anthropic", headers=H).json()["models"]
    assert any(r["id"] == "claude-sonnet-4-5" for r in rows)
    assert client.get("/api/catalog/nope", headers=H).status_code == 404
    r = client.post("/api/catalog/refresh", json={"what": "prices"}, headers=H)
    assert r.status_code == 422 and "off" in r.json()["detail"]
    assert client.post("/api/catalog/refresh", json={"what": "x"}, headers=H).status_code == 422
    assert client.get("/api/catalog").status_code == 401
    # turning it on is a setting (saved like any other)
    from optimux_backend.core.config_manager import get_settings, update_settings
    try:
        assert client.post("/api/catalog/online", json={"on": True}, headers=H).json() == {"online": True}
        assert get_settings()["online_catalog"] is True
    finally:
        update_settings(online_catalog=False)


def test_retired_models_are_marked(cat, monkeypatch):
    litellm.model_cost["gpt-old-test"] = {"litellm_provider": "openai", "mode": "chat", "deprecation_date": "2020-01-01",
                                          "input_cost_per_token": 1e-6}
    litellm.model_cost["gpt-later-test"] = {"litellm_provider": "openai", "mode": "chat", "deprecation_date": "2999-01-01",
                                            "input_cost_per_token": 1e-6}
    rows = {r["id"]: r for r in catalog.offline_models("openai")}
    assert rows["gpt-old-test"]["retired"] is True
    assert rows["gpt-later-test"]["retired"] is False and rows["gpt-later-test"]["deprecated"] == "2999-01-01"
    assert rows["gpt-5"]["retired"] is False


def test_a_zero_price_is_not_listed_not_free(cat):
    """LiteLLM lists some switched-off experimental models at 0: shown as no
    price (last in the list), the same way cost metering reads them."""
    import litellm
    litellm.model_cost["gemini/gemini-exp-test"] = {"litellm_provider": "gemini", "mode": "chat",
                                                    "input_cost_per_token": 0, "output_cost_per_token": 0}
    litellm.model_cost["openrouter/acme/model:free"] = {"litellm_provider": "openrouter", "mode": "chat",
                                                         "input_cost_per_token": 0, "output_cost_per_token": 0}
    rows = catalog.offline_models("gemini")
    exp = next(r for r in rows if r["id"] == "gemini/gemini-exp-test")
    assert exp["unpriced"] and exp["input"] is None and exp["output"] is None
    assert rows.index(exp) > max(i for i, r in enumerate(rows) if r["input"] is not None)
    from optimux_backend.core.config_manager import price_source
    assert price_source("gemini/gemini-exp-test", {}) == "unknown"
    free = next(r for r in catalog.offline_models("openrouter") if r["id"] == "openrouter/acme/model:free")
    assert free["input"] == 0 and not free["unpriced"]                # really free
    assert all(r["input"] == 0 and not r["unpriced"] for r in catalog.offline_models("ollama"))   # local
