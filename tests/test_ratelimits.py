"""Rate limits learned from the providers' replies and errors (no extra calls)."""
import asyncio
import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from optimux_backend.core import paths, ratelimits


@pytest.fixture
def rl(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HOME", str(tmp_path))
    ratelimits.reset_cache()
    yield ratelimits
    ratelimits.reset_cache()


def test_openai_style_headers():
    items = ratelimits.parse_headers("gpt-5", {
        "x-ratelimit-limit-requests": "500", "x-ratelimit-remaining-requests": "499",
        "llm_provider-x-ratelimit-limit-tokens": "200000", "llm_provider-x-ratelimit-remaining-tokens": "199000",
        "llm_provider-x-ratelimit-reset-requests": "120ms", "content-type": "application/json"})
    assert {(i["what"], i["per"], i["limit"], i["remaining"]) for i in items} == {
        ("requests", "minute", 500, 499), ("tokens", "minute", 200000, 199000)}


def test_groq_requests_are_per_day_and_suffixed_periods():
    items = ratelimits.parse_headers("groq/llama-3.3-70b", {"x-ratelimit-limit-requests": "14400",
                                                           "x-ratelimit-limit-tokens": "6000"})
    assert {(i["what"], i["per"]) for i in items} == {("requests", "day"), ("tokens", "minute")}
    items = ratelimits.parse_headers("mistral/x", {"x-ratelimit-limit-tokens-month": "1000000000",
                                                   "x-ratelimit-limit-req-minute": "60"})
    assert {(i["what"], i["per"], i["limit"]) for i in items} == {("tokens", "month", 1000000000),
                                                                 ("requests", "minute", 60)}


def test_anthropic_headers():
    items = ratelimits.parse_headers("claude-sonnet-5", {
        "llm_provider-anthropic-ratelimit-requests-limit": "50",
        "llm_provider-anthropic-ratelimit-input-tokens-limit": "30000",
        "llm_provider-anthropic-ratelimit-output-tokens-limit": "8000",
        "llm_provider-anthropic-ratelimit-input-tokens-remaining": "29000",
        "llm_provider-anthropic-ratelimit-requests-reset": "2026-10-15T16:00:00Z"})
    got = {(i["what"], i["limit"]) for i in items}
    assert got == {("requests", 50), ("input tokens", 30000), ("output tokens", 8000)}


GEMINI_429 = '''litellm.RateLimitError: VertexAIException - {"error": {"code": 429, "message": "You exceeded your current
quota.", "status": "RESOURCE_EXHAUSTED", "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
"violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaDimensions": {"location": "global",
"model": "gemma-4-31b"}, "quotaValue": "15"}, {"quotaMetric": "x", "quotaId":
"GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaDimensions": {"model": "gemma-4-31b"},
"quotaValue": "1500"}]}, {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "21s"}]}}'''


def test_google_limits_come_from_a_429(rl):
    items = rl.parse_error(GEMINI_429)
    assert {(i["what"], i["per"], i["limit"]) for i in items} == {("requests", "minute", 15), ("requests", "day", 1500)}
    assert all(i["hit"] and i["free_tier"] for i in items)
    rl.record_error("gemini/gemma-4-31b-it", Exception(GEMINI_429))
    entry = rl.get("gemini/gemma-4-31b-it")
    assert len(entry["items"]) == 2 and entry["hit_at"]
    # and through the usual failure path (cooldowns)
    from optimux_backend.core import providers
    providers.record_failure("gemini/gemma-4-other", Exception(GEMINI_429))
    assert rl.get("gemini/gemma-4-other")
    providers.clear_model("gemini/gemma-4-other")


def test_recorded_privately_and_never_a_key(rl):
    class Resp:
        _hidden_params = {"additional_headers": {"x-ratelimit-limit-requests": "500",
                                                 "llm_provider-authorization": "Bearer sk-secret"}}
    rl.record_response("openai/gpt-5", Resp())
    path = os.path.join(paths.HOME, "catalog", "ratelimits.json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    text = open(path).read()
    assert "sk-secret" not in text
    assert json.loads(text)["gpt-5"]["items"][0]["limit"] == 500      # stored under the bare ID
    rl.reset_cache()
    assert rl.get("gpt-5")["items"][0]["limit"] == 500                 # survives a restart


def test_a_broken_reply_never_breaks_the_call(rl):
    class Weird:
        _hidden_params = {"additional_headers": {"x-ratelimit-limit-requests": "lots"}}
    rl.record_response("gpt-5", Weird())
    rl.record_response("gpt-5", object())
    rl.record_error("gpt-5", Exception("nothing useful"))
    assert rl.get("gpt-5") is None


class _Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200)
        self.send_header("x-ratelimit-limit-requests", "500")
        self.send_header("x-ratelimit-limit-tokens", "200000")
        chunk = {"id": "1", "object": "chat.completion.chunk", "created": 1, "model": "m",
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        if body.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n")
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "1", "object": "chat.completion", "created": 1, "model": "m",
                                         "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                   "total_tokens": 2}}).encode())


@pytest.mark.parametrize("stream", [False, True])
def test_litellm_passes_the_headers_through(rl, monkeypatch, stream):
    """End to end with LiteLLM and a local OpenAI-style server, as in a chat."""
    import litellm
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    server = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        async def call():
            r = await litellm.acompletion(model="openai/fake-model", api_key="k", stream=stream,
                                          api_base=f"http://127.0.0.1:{server.server_address[1]}/v1",
                                          messages=[{"role": "user", "content": "hi"}])
            if stream:
                async for _ in r:
                    pass
            return r
        rl.record_response("openai/fake-model", asyncio.run(call()))
    finally:
        server.shutdown()
    items = rl.get("fake-model")["items"]
    assert {(i["what"], i["limit"]) for i in items} == {("requests", 500), ("tokens", 200000)}


def test_limits_reach_the_models_screen(rl, monkeypatch):
    from optimux_backend.core import catalog
    import optimux_backend.core.config_manager as cm
    rl.record_error("gemini/gemini-2.5-flash", Exception(GEMINI_429))
    row = next(r for r in catalog.models("gemini")["models"] if r["id"] == "gemini/gemini-2.5-flash")
    assert row["limits"]["items"][0]["limit"] in (15, 1500)
    assert catalog.models("gemini")["provider"]["limits_url"].startswith("https://ai.google.dev/")
    assert catalog.models("openai")["provider"]["limits_url"] is None
    monkeypatch.setattr(cm, "get_all_models", lambda: {"gemini/gemini-2.5-flash": {"name": "f", "env_key": ""}})
    assert cm.get_models_config()["available"][0]["limits"]["items"]
