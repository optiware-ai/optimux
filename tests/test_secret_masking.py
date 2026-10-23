"""IM-02 (v0.5.1): saved keys never reach the chat, the database or a model,
even when a file a command prints holds one."""
import json

import pytest

import optimux_backend.core.files as files
from optimux_backend.core import paths, security
from optimux_backend.core.executor import run_shell_command

KEY = "sk-ant-api03-REALSECRETVALUE1234567890"
GH = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
HASH = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@pytest.fixture
def saved(tmp_path, monkeypatch):
    f = tmp_path / "secrets.env"
    f.write_text(f"# keys\nANTHROPIC_API_KEY={KEY}\nexport OPTIMUX_GITHUB_TOKEN='{GH}'\nSHORT_KEY=abc\n"
                 "OPENAI_API_BASE=https://api.example.com/v1\n")
    monkeypatch.setattr(paths, "SECRETS_FILE", str(f))
    for k in [k for k in list(__import__("os").environ) if security.SECRET_ENV_NAME.search(k)]:
        monkeypatch.delenv(k)
    return f


def test_what_counts_as_a_saved_key(saved, monkeypatch):
    assert security.secret_values() == [("OPTIMUX_GITHUB_TOKEN", GH), ("ANTHROPIC_API_KEY", KEY)]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-0123456789abcdef")     # the server's own environment
    monkeypatch.setenv("HOME_DIR", "/home/someone/very/long")          # not a key name
    assert ("DEEPSEEK_API_KEY", "ds-0123456789abcdef") in security.secret_values()
    assert all(n != "HOME_DIR" for n, _ in security.secret_values())


def test_a_changed_secrets_file_is_read_again(saved):
    assert len(security.secret_values()) == 2
    saved.write_text(saved.read_text() + "MISTRAL_API_KEY=mistral-key-0000001\n")
    assert ("MISTRAL_API_KEY", "mistral-key-0000001") in security.secret_values()


def test_mask_exact_values_only(saved):
    text = f"key={KEY}\ntoken: {GH} and again {KEY}\ncommit {HASH}\nhttps://api.example.com/v1 abc"
    out, n = security.mask_secrets(text)
    assert n == 3 and KEY not in out and GH not in out
    assert "key=••••(ANTHROPIC_API_KEY)" in out and "token: ••••(OPTIMUX_GITHUB_TOKEN)" in out
    assert HASH in out and "https://api.example.com/v1 abc" in out    # hashes, addresses, short values stay
    assert security.unmask_secrets(out) == text


def test_a_key_cut_at_an_edge(saved):
    out, n = security.mask_secrets("start " + KEY[:15], edges=True)
    assert out == "start ••••(ANTHROPIC_API_KEY, cut)" and n == 1
    out, n = security.mask_secrets(KEY[-12:] + "\nmore", edges=True)
    assert out == "••••(ANTHROPIC_API_KEY, cut)\nmore" and n == 1
    assert security.mask_secrets("start " + KEY[:5])[0] == "start " + KEY[:5]   # too short to tell


@pytest.mark.asyncio
async def test_command_output_hides_keys(saved, tmp_path):
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={KEY}\nDEBUG=1\n")
    r = await run_shell_command("cat .env; echo " + GH + " >&2", cwd=str(tmp_path))
    assert r["stdout"] == "ANTHROPIC_API_KEY=••••(ANTHROPIC_API_KEY)\nDEBUG=1\n"
    assert r["stderr"] == "••••(OPTIMUX_GITHUB_TOKEN)\n" and r["hidden"] == 2


@pytest.mark.asyncio
async def test_a_key_at_the_output_cut_is_hidden(saved, tmp_path, monkeypatch):
    from optimux_backend.core import executor
    monkeypatch.setitem(executor.OUTPUT_CAPS, "stdout", (100, 100))
    # one long line: the cut falls inside the key, not at a line end
    r = await run_shell_command(f"printf '%s' \"$(printf 'a%.0s' $(seq 1 90)){KEY}$(printf 'b%.0s' $(seq 1 500))\"",
                                cwd=str(tmp_path))
    assert KEY[:6] not in r["stdout"] and "••••(ANTHROPIC_API_KEY, cut)" in r["stdout"] and r["hidden"] >= 1


@pytest.mark.asyncio
async def test_no_keys_no_extras(saved, tmp_path):
    r = await run_shell_command("echo " + HASH, cwd=str(tmp_path))
    assert r["stdout"] == HASH + "\n" and "hidden" not in r


# ---- file tools: keys stay where they are, never added; diffs hide them

def test_rewriting_a_file_keeps_its_key(saved, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"ANTHROPIC_API_KEY={KEY}\nDEBUG=0\n")
    change = files.prepare_write(str(tmp_path), ".env", "ANTHROPIC_API_KEY=••••(ANTHROPIC_API_KEY)\nDEBUG=1\n")
    assert KEY not in change["diff"] and "-DEBUG=0" in change["diff"]
    files.apply(change)
    assert env.read_text() == f"ANTHROPIC_API_KEY={KEY}\nDEBUG=1\n"


def test_editing_next_to_a_key(saved, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"A=1\nANTHROPIC_API_KEY={KEY}\nDEBUG=0\n")
    change = files.prepare_edit(str(tmp_path), ".env", "ANTHROPIC_API_KEY=••••(ANTHROPIC_API_KEY)\nDEBUG=0",
                                "ANTHROPIC_API_KEY=••••(ANTHROPIC_API_KEY)\nDEBUG=1")
    assert KEY not in change["diff"] and "••••(ANTHROPIC_API_KEY)" in change["diff"]
    files.apply(change)
    assert env.read_text() == f"A=1\nANTHROPIC_API_KEY={KEY}\nDEBUG=1\n"


@pytest.mark.parametrize("op", ["new file", "copy into a file", "edit adds it"])
def test_a_key_cant_be_added(saved, tmp_path, op):
    (tmp_path / "a.py").write_text(f"KEY = '{KEY}'\nx = 1\n")
    with pytest.raises(files.FileToolError, match="can't be added or copied"):
        if op == "new file":
            files.prepare_write(str(tmp_path), "leak.txt", "••••(ANTHROPIC_API_KEY)")
        elif op == "copy into a file":
            files.prepare_write(str(tmp_path), "a.py", "KEY = '••••(ANTHROPIC_API_KEY)'\nx = '••••(ANTHROPIC_API_KEY)'\n")
        else:
            files.prepare_edit(str(tmp_path), "a.py", "x = 1", "x = '••••(ANTHROPIC_API_KEY)'")
    assert (tmp_path / "a.py").read_text() == f"KEY = '{KEY}'\nx = 1\n"


def test_a_key_the_model_never_saw_cant_be_placed(saved, tmp_path):
    (tmp_path / "a.py").write_text(f"KEY = '{KEY}'\n")
    with pytest.raises(files.FileToolError):
        files.prepare_write(str(tmp_path), "a.py", f"KEY = '{KEY}'\nGH = '••••(OPTIMUX_GITHUB_TOKEN)'\n")


# ---- end to end: chat, stored events and the next model call

def test_cat_env_in_a_chat(saved, mocker, tmp_path):
    from tests.ws_helpers import Conversation, collect, make_client
    from tests.test_routing_session import Recorder, reply, tool_reply
    import optimux_backend.main as main
    from optimux_backend.core import store
    client = make_client(mocker, tmp_path)
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / ".env").write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    mocker.patch.object(main, "current_workdir", return_value=str(proj))
    mocker.patch("optimux_backend.core.executor.get_active_cwd", return_value=str(proj))
    rec = Recorder(tool_reply("execute_bash", {"command": "cat .env"}), reply("It has one key."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "show .env", "model": "auto"}))
    while json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    msgs = collect(chat.ws)
    chat.close()
    shown = [m for m in msgs if m["type"] == "command_output"][0]
    assert shown["output"] == "ANTHROPIC_API_KEY=••••(ANTHROPIC_API_KEY)\n" and shown["hidden"] == 1
    assert KEY not in json.dumps(rec.calls[1]["messages"])
    conn = store.connect()
    try:
        stored = " ".join(r[0] for r in conn.execute("SELECT data FROM session_events"))
        stored += " ".join(r[0] or "" for r in conn.execute("SELECT history FROM sessions"))
    finally:
        conn.close()
    assert "(ANTHROPIC_API_KEY)" in stored and KEY not in stored            # stored as JSON (•• escaped)


# ---- IM-02b: keys in the user's message and in the model's replies

def _chat(mocker, tmp_path, *responses):
    from tests.ws_helpers import Conversation, make_client
    from tests.test_routing_session import Recorder
    client = make_client(mocker, tmp_path)
    rec = Recorder(*responses)
    mocker.patch("litellm.acompletion", side_effect=rec)
    return Conversation(client), rec


def _stored_text():
    from optimux_backend.core import store
    conn = store.connect()
    try:
        return (" ".join(r[0] for r in conn.execute("SELECT data FROM session_events"))
                + " ".join((r[0] or "") + (r[1] or "") for r in conn.execute("SELECT history, title FROM sessions")))
    finally:
        conn.close()


def test_a_key_pasted_in_a_message_is_hidden(saved, mocker, tmp_path):
    from tests.test_routing_session import reply
    chat, rec = _chat(mocker, tmp_path, reply("Noted."))
    msgs = chat.prompt(f"use this key: {KEY} please")
    chat.close()
    sent = json.dumps(rec.calls[0]["messages"])
    assert KEY not in sent and "use this key: ••••(ANTHROPIC_API_KEY) please" in json.loads(sent)[-1]["content"]
    masked = [m for m in msgs if m["type"] == "prompt_masked"]
    assert masked and masked[0]["content"] == "use this key: ••••(ANTHROPIC_API_KEY) please"
    assert any("held a saved key (ANTHROPIC_API_KEY)" in m.get("content", "") for m in msgs if m["type"] == "system")
    stored = _stored_text()
    assert KEY not in stored and "prompt_masked" not in stored


def test_a_key_in_a_streamed_reply_is_hidden_even_when_split(saved, mocker, tmp_path):
    from tests.ws_helpers import chunk, fake_stream
    from tests.test_routing_session import reply
    DONE = {"prompt_tokens": 10, "completion_tokens": 2}
    parts = ["Your key is ", KEY[:9], KEY[9:20], KEY[20:], " and the token ", GH[:3], GH[3:], " end."]
    first = fake_stream([*(chunk(p) for p in parts), chunk(empty=True, usage_obj=DONE)])
    chat, rec = _chat(mocker, tmp_path, first, reply("ok"))
    msgs = chat.prompt("what is my key?")
    chat.prompt("thanks")
    chat.close()
    tokens = [m["content"] for m in msgs if m["type"] == "token"]
    assert all(KEY[:9] not in t and GH[:3] + GH[3:6] not in t for t in tokens)
    assert "".join(tokens) == "Your key is ••••(ANTHROPIC_API_KEY) and the token ••••(OPTIMUX_GITHUB_TOKEN) end."
    assert msgs[-1]["type"] == "done"                                        # nothing left waiting
    assert KEY not in json.dumps(rec.calls[1]["messages"])                  # the history of the next call
    assert KEY not in _stored_text() and GH not in _stored_text()


def test_held_text_keeps_its_place(saved, mocker, tmp_path):
    """A reply that ends with what could be the start of a key ('sk-') still
    comes out whole, before the next event."""
    from tests.ws_helpers import chunk, fake_stream
    DONE = {"prompt_tokens": 10, "completion_tokens": 2}
    chat, _ = _chat(mocker, tmp_path, fake_stream([chunk("prefix is sk-"), chunk(empty=True, usage_obj=DONE)]))
    msgs = chat.prompt("hi")
    chat.close()
    kinds = [m["type"] for m in msgs]
    tokens = [m["content"] for m in msgs if m["type"] == "token"]
    assert "".join(tokens) == "prefix is sk-" and kinds.index("token") < kinds.index("done")


def test_events_are_stored_without_keys(saved, tmp_path):
    from optimux_backend.core import sessions
    sid = sessions.create("alpha")
    sessions.append_events(sid, [{"type": "token", "content": f"k={KEY}"}])
    assert KEY not in _stored_text()
