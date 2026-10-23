#!/usr/bin/env python3
"""End-to-end check of an installed Optimux, with no API key (v0.6, IM-11).

Run with Optimux's own Python, after install.sh:
    ~/.local/share/optimux/.venv/bin/python scripts/smoke-test.py --url http://127.0.0.1:8000

It starts a fake OpenAI-compatible model on 127.0.0.1 (no network, no cost),
signs in with the access token, adds that model, creates a project, sends one
chat message, approves the command the model asks for, and checks that the
command ran in the project (in the sandbox when it is on). It changes the
install it runs against (password, model, routing, a project named
"smoke-test"), so use it on a fresh install or a test machine.

Exit code 0 when everything worked; otherwise the failing step is printed.
"""
import argparse
import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "openai/smoke-model"
PROJECT = "smoke-test"
COMMAND = "echo smoke-ok > smoke.txt && cat smoke.txt"
PASSWORD = "smoke-test-password-1"


# ---- a fake model: first a command, then a short answer

class FakeModel(BaseHTTPRequestHandler):
    calls = 0

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        has_tool_result = any(m.get("role") == "tool" for m in body.get("messages", []))
        FakeModel.calls += 1
        usage = {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}
        if has_tool_result:
            delta = {"role": "assistant", "content": "The command ran: smoke-ok."}
            finish = "stop"
        else:
            delta = {"role": "assistant", "content": None, "tool_calls": [{
                "index": 0, "id": "call_smoke", "type": "function",
                "function": {"name": "execute_bash", "arguments": json.dumps({"command": COMMAND})}}]}
            finish = "tool_calls"
        base = {"id": "chatcmpl-smoke", "created": int(time.time()), "model": "smoke-model"}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            chunks = [{**base, "object": "chat.completion.chunk",
                       "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {**base, "object": "chat.completion.chunk",
                       "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage}]
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            message = {k: v for k, v in delta.items()}
            out = json.dumps({**base, "object": "chat.completion", "usage": usage,
                              "choices": [{"index": 0, "message": message, "finish_reason": finish}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)


def start_fake_model() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/v1"


# ---- talking to Optimux

class Step(Exception):
    pass


def call(url: str, path: str, token: str, method: str = "GET", data: dict | None = None) -> dict:
    req = urllib.request.Request(url + path, method=method, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Step(f"{method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}") from None


async def chat(url: str, token: str, prompt: str) -> list:
    import websockets
    ws_url = url.replace("http", "ws", 1) + "/ws"
    events = []
    async with websockets.connect(ws_url, max_size=2 ** 24) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        first = json.loads(await ws.recv())
        if first.get("type") != "auth_ok":
            raise Step(f"WebSocket sign-in refused: {first}")
        await ws.send(json.dumps({"type": "new_chat"}))
        while json.loads(await asyncio.wait_for(ws.recv(), 30)).get("type") != "done":
            pass
        await ws.send(json.dumps({"prompt": prompt, "model": "auto"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 120))
            events.append(m)
            if m["type"] == "approval_request":
                await ws.send(json.dumps({"type": "approval_response", "approved": True,
                                          "tool_call_id": m.get("tool_call_id")}))
            elif m["type"] == "done":
                return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="", help="the access token (default: read from the data folder)")
    ap.add_argument("--home", default=os.environ.get("OPTIMUX_HOME", os.path.expanduser("~/.optimux")),
                    help="Optimux's data folder, to read the token")
    args = ap.parse_args()
    url = args.url.rstrip("/")
    token = args.token
    if not token:
        try:
            for line in open(os.path.join(args.home, "secrets.env")):
                if line.startswith("OPTIMUX_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError as e:
            print(f"✗ Can't read the access token: {e}. Pass --token.")
            return 1
    steps = []

    def ok(text):
        steps.append(text)
        print(f"✓ {text}")

    try:
        status = json.loads(urllib.request.urlopen(url + "/auth/status", timeout=30).read())
        ok(f"Optimux answers at {url} (server mode: {status.get('server_mode')})")
        api = start_fake_model()
        call(url, "/api/keys", token, "POST", {"env_key": "OPENAI_API_KEY", "api_key": "sk-smoke-not-a-real-key"})
        call(url, "/api/models", token, "POST", {"id": MODEL, "name": "Smoke model", "env_key": "OPENAI_API_KEY",
                                                 "api_base": api, "input_cost_per_mtok": 0, "output_cost_per_mtok": 0})
        call(url, "/api/routing", token, "POST", {"conductor_model": "off", "simple_model": MODEL, "complex_model": MODEL})
        ok("A fake model is set up (no network, no cost)")
        projects = call(url, "/api/projects", token)
        if PROJECT not in projects:
            call(url, "/api/projects", token, "POST", {"name": PROJECT, "template": "basic", "git_init": True})
        settings = call(url, "/api/settings", token)
        call(url, "/api/settings", token, "POST", {**settings, "active_project": PROJECT})
        ok(f"Project {PROJECT} is active")
        sandbox = call(url, "/api/sandbox?refresh=true", token)
        events = asyncio.run(chat(url, token, "Write smoke-ok into smoke.txt"))
        kinds = [e["type"] for e in events]
        errors = [e.get("content") for e in events if e["type"] == "error"]
        if errors:
            raise Step(f"the chat reported an error: {errors[0]}")
        if "approval_request" not in kinds:
            raise Step("no approval was asked for the command")
        ok("The command was shown for approval and approved")
        out = [e for e in events if e["type"] == "command_output"]
        if not out or "smoke-ok" not in (out[-1].get("output") or "") or out[-1].get("exit_code") != 0:
            raise Step(f"the command didn't run as expected: {out[-1] if out else 'no output'}")
        ok(f"The command ran in the project (sandbox {'works' if sandbox.get('works') else 'not working: ' + str(sandbox.get('error'))})")
        if sandbox.get("required") and not sandbox.get("works"):
            raise Step("server mode requires the sandbox, but it doesn't work")
        reply = "".join(e.get("content") or "" for e in events if e["type"] == "token")
        if "smoke-ok" not in reply:
            raise Step(f"the reply is missing: {reply!r}")
        ok("The model answered after the command")
        if FakeModel.calls < 2:
            raise Step("the fake model wasn't called twice")
    except Step as e:
        print(f"✗ {e}")
        return 1
    except Exception as e:                      # anything unexpected is a failure too
        print(f"✗ {type(e).__name__}: {e}")
        return 1
    print(f"All {len(steps)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
