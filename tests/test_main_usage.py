"""End-to-end check that every LLM call is metered and pushed to the UI."""
import json
import os

import pytest

from tests.ws_helpers import chunk, fake_stream, make_client, run_prompt

import optimux_backend.core.usage as usage


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_conductor_and_executor_are_metered(mocker, client):
    final = chunk(empty=True, usage_obj={"prompt_tokens": 200, "completion_tokens": 20})
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: fake_stream([chunk("Hi"), chunk(" there"), final]))
    msgs = run_prompt(client, "hello")
    usage_msgs = [m for m in msgs if m["type"] == "usage"]
    assert [m["role"] for m in usage_msgs] == ["conductor", "simple"]
    assert usage_msgs[1]["prompt_tokens"] == 200
    assert usage_msgs[1]["session_tokens"] == 275
    assert isinstance(usage_msgs[1]["duration_ms"], int)
    assert "".join(m["content"] for m in msgs if m["type"] == "token") == "Hi there"
    s = usage.summary()["today"]["total"]
    assert s["calls"] == 2 and s["prompt_tokens"] == 250


def test_missing_usage_falls_back_to_estimate(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: fake_stream([chunk("Hello")]))
    mocker.patch.object(usage, "estimate", return_value={
        "prompt_tokens": 9, "completion_tokens": 1, "cached_tokens": 0, "estimated": True})
    msgs = run_prompt(client, "hello")
    executor = [m for m in msgs if m["type"] == "usage"][-1]
    assert executor["estimated"] is True and executor["prompt_tokens"] == 9


def test_usage_endpoint_requires_token(client):
    assert client.get("/api/usage").status_code == 401
    r = client.get("/api/usage", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200 and "today" in r.json()
