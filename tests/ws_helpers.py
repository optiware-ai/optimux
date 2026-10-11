"""Shared helpers for WebSocket tests: fake LiteLLM streams and a client."""
import json
import os
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

ROUTING = {"conductor_model": "conductor-model", "simple_model": "simple-model", "complex_model": "complex-model"}


def chunk(text=None, usage_obj=None, empty=False, tool_call=None):
    tool_calls = None
    if tool_call:
        name, args = tool_call
        tool_calls = [NS(index=0, id="call_1", function=NS(name=name, arguments=json.dumps(args)))]
    choices = [] if empty else [NS(delta=NS(content=text, tool_calls=tool_calls))]
    return NS(choices=choices, usage=usage_obj)


async def fake_stream(chunks):
    for c in chunks:
        yield c


def make_client(mocker, tmp_path, tier="simple", routing=None):
    os.environ["OPTIMUX_TOKEN"] = "test-token"
    import optimux_backend.core.usage as usage
    import optimux_backend.main as main
    from optimux_backend.core import providers, budgets
    providers.clear()
    mocker.patch.object(budgets, "BUDGETS_FILE", str(tmp_path / "budgets.json"))
    main.BUDGET_NOTES_SHOWN.clear()
    # The memory curator makes its own background model calls; tests opt in explicitly
    mocker.patch.object(main, "curator_enabled", return_value=False)
    mocker.patch.object(usage, "USAGE_FILE", str(tmp_path / "usage.jsonl"))
    mocker.patch.object(usage, "_custom_prices", return_value=None)
    mocker.patch.object(main, "get_memory_context", return_value="MEMORY")
    mocker.patch.object(main, "get_routing_config", return_value=routing or dict(ROUTING))
    mocker.patch.object(main, "RETRY_DELAY", 0)
    main._test_conductor = mocker.patch.object(main, "route_model_with_usage", return_value=(
        tier, "conductor-model", {"prompt_tokens": 50, "completion_tokens": 5}, 12))
    return TestClient(main.app)


def collect(ws) -> list:
    msgs = []
    while True:
        m = json.loads(ws.receive_text())
        msgs.append(m)
        if m["type"] == "done":
            return msgs


def run_prompt(client, prompt, model="auto"):
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "test-token"}))
        assert json.loads(ws.receive_text())["type"] == "auth_ok"
        ws.send_text(json.dumps({"prompt": prompt, "model": model}))
        return collect(ws)


class Conversation:
    """Keeps one WebSocket open across several prompts."""

    def __init__(self, client):
        self._cm = client.websocket_connect("/ws")
        self.ws = self._cm.__enter__()
        self.ws.send_text(json.dumps({"type": "auth", "token": "test-token"}))
        assert json.loads(self.ws.receive_text())["type"] == "auth_ok"

    def prompt(self, text, model="auto"):
        self.ws.send_text(json.dumps({"prompt": text, "model": model}))
        return collect(self.ws)

    def send(self, payload):
        self.ws.send_text(json.dumps(payload))
        return collect(self.ws)

    def close(self):
        self._cm.__exit__(None, None, None)
