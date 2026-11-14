"""What was sent to the models (v0.8): every call's prompt is kept, part by part,
so the user can read exactly what left the machine."""
import json
import time

import pytest

import optimux_backend.main as main
from optimux_backend.core import sent, store
from tests.ws_helpers import chunk, collect, fake_stream, make_client

H = {"Authorization": "Bearer test-token"}
DONE = {"prompt_tokens": 9, "completion_tokens": 2}
SYSTEM = "You are Optimux.\nGuidelines: be brief.\n\nMEMORY: prefers pytest\n\nSkills: none"
MESSAGES = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "run the tests"},
    {"role": "assistant", "content": "I'll run them.",
     "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "execute_bash", "arguments": '{"command": "pytest -q"}'}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "3 passed"},
]
TOOLS = [{"type": "function", "function": {"name": "execute_bash", "description": "run", "parameters": {}}}]


def rows(table):
    conn = store.connect()
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    finally:
        conn.close()


def test_a_call_is_kept_part_by_part():
    sid = sent.record("chat-1", "simple", "simple-model", MESSAGES, TOOLS, project="alpha",
                      memory="MEMORY: prefers pytest")
    got = sent.get(sid)
    assert got["role"] == "simple" and got["model"] == "simple-model" and got["project"] == "alpha"
    kinds = [(p["kind"], p["text"]) for p in got["parts"]]
    assert kinds == [
        ("instructions", "You are Optimux.\nGuidelines: be brief.\n\n"),
        ("memory", "MEMORY: prefers pytest"),                     # the memory, apart from Optimux's own text
        ("instructions", "\n\nSkills: none"),
        ("user", "run the tests"),
        ("assistant", "I'll run them."),
        ("call", 'execute_bash\n{\n  "command": "pytest -q"\n}'),
        ("result", "3 passed"),
        ("tools", json.dumps(TOOLS, indent=2)),
    ]
    assert all(p["chars"] == len(p["text"]) for p in got["parts"])
    assert got["tools"] == ["execute_bash"] and got["chars"] == sum(len(t) for _, t in kinds)
    assert sent.get(sid + 999) is None


def test_the_next_call_stores_only_what_is_new():
    sent.record("chat-1", "simple", "m", MESSAGES, TOOLS, memory="MEMORY: prefers pytest")
    before = rows("sent_parts")
    more = MESSAGES + [{"role": "assistant", "content": "All 3 pass."}, {"role": "user", "content": "thanks"}]
    sid = sent.record("chat-1", "simple", "m", more, TOOLS, memory="MEMORY: prefers pytest")
    assert rows("sent_parts") == before + 2                       # the history is not stored again
    assert [p["text"] for p in sent.get(sid)["parts"]][-3:-1] == ["All 3 pass.", "thanks"]


def test_blocks_and_odd_content_are_read_as_text():
    blocks = [{"role": "system", "content": [{"type": "text", "text": "Rules.", "cache_control": {"type": "ephemeral"}}]},
              {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": " there"}]},
              {"role": "assistant", "content": None}]
    got = sent.get(sent.record("c", "complex", "anthropic/claude", blocks, None))
    assert [(p["kind"], p["text"]) for p in got["parts"]] == [("instructions", "Rules."), ("user", "hello there")]
    assert got["tools"] == []


def test_old_calls_are_dropped_with_the_parts_nobody_uses(monkeypatch):
    old = sent.record("c", "simple", "m", [{"role": "user", "content": "long ago"}], None)
    conn = store.connect()
    with conn:
        conn.execute("UPDATE sent_calls SET ts = ? WHERE id = ?", (time.time() - 40 * 86400, old))
    conn.close()
    new = sent.record("c", "simple", "m", [{"role": "user", "content": "today"}], None)
    assert sent.prune(14) == 1
    assert sent.get(old) is None and sent.get(new)["parts"][0]["text"] == "today"
    assert rows("sent_parts") == 1


def test_the_chat_says_which_prompt_each_call_sent(mocker, tmp_path):
    """The usage line of a call carries the id of what was sent; the page reads it by that id."""
    client = make_client(mocker, tmp_path)
    mocker.patch("optimux_backend.core.security.secret_values", return_value=[("ANTHROPIC_API_KEY", "sk-ant-very-secret-123")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: fake_stream([chunk("Hi."), chunk(empty=True, usage_obj=DONE)]))
    with client.websocket_connect("/ws") as sock:
        sock.send_text(json.dumps({"type": "auth", "token": "test-token"}))
        sock.receive_text()
        sock.send_text(json.dumps({"prompt": "hello, my key is sk-ant-very-secret-123", "model": "auto"}))
        msgs = collect(sock)
    usage = {m["role"]: m for m in msgs if m["type"] == "usage"}
    assert usage["conductor"]["sent"] and usage["simple"]["sent"] != usage["conductor"]["sent"]
    got = client.get(f"/api/sent/{usage['simple']['sent']}", headers=H).json()
    assert got["role"] == "simple" and got["model"] == "simple-model"
    kinds = [p["kind"] for p in got["parts"]]
    assert kinds[0] == "instructions" and "memory" in kinds and kinds[-1] == "tools"
    assert [p["text"] for p in got["parts"] if p["kind"] == "memory"] == ["MEMORY"]
    said = [p["text"] for p in got["parts"] if p["kind"] == "user"]
    assert said == ["hello, my key is ••••(ANTHROPIC_API_KEY)"]            # as it left: the key hidden
    assert "sk-ant-very-secret" not in json.dumps(got)
    conductor = client.get(f"/api/sent/{usage['conductor']['sent']}", headers=H).json()
    assert conductor["role"] == "conductor" and conductor["parts"][-1]["kind"] == "user"
    assert client.get("/api/sent/999999", headers=H).status_code == 404
    assert client.get(f"/api/sent/{usage['simple']['sent']}").status_code in (401, 403)


def test_keeping_can_be_turned_off(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    real = main.get_settings
    mocker.patch.object(main, "get_settings", side_effect=lambda: {**real(), "keep_sent_days": 0})
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: fake_stream([chunk("Hi."), chunk(empty=True, usage_obj=DONE)]))
    before = rows("sent_calls")
    with client.websocket_connect("/ws") as sock:
        sock.send_text(json.dumps({"type": "auth", "token": "test-token"}))
        sock.receive_text()
        sock.send_text(json.dumps({"prompt": "hello", "model": "auto"}))
        msgs = collect(sock)
    assert all(not m.get("sent") for m in msgs if m["type"] == "usage")
    assert rows("sent_calls") == before
