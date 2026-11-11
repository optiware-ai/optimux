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
    # Plan mode (default Off) is off for chat tests; the plan tests turn it on themselves
    mocker.patch.object(main, "plan_mode", return_value="off")
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


def collect_answering(ws, answers) -> list:
    """Like collect, answering each approval_request in turn from `answers`
    (True = yes). A request with no answer left fails the test instead of hanging."""
    answers, msgs = list(answers), []
    while True:
        m = json.loads(ws.receive_text())
        msgs.append(m)
        if m["type"] == "approval_request":
            assert answers, f"an unexpected question: {m.get('command')}"
            ws.send_text(json.dumps({"type": "approval_response", "approved": answers.pop(0)}))
        if m["type"] == "done":
            assert not answers, f"{len(answers)} answer(s) not used"
            return msgs


def asked(msgs, action="model_fallback") -> list:
    return [m for m in msgs if m["type"] == "approval_request" and m.get("action") == action]


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

    def send(self, payload, answers=()):
        self.ws.send_text(json.dumps(payload))
        return collect_answering(self.ws, answers)

    def prompt_answering(self, text, answers, model="auto"):
        self.ws.send_text(json.dumps({"prompt": text, "model": model}))
        return collect_answering(self.ws, answers)

    def close(self):
        self._cm.__exit__(None, None, None)
