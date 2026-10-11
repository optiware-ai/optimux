"""Feature 17: memory curator, project memory history and undo."""
import asyncio
import json
import os
import time
from types import SimpleNamespace as NS

import pytest

import optimux_backend.core.curator as curator
import optimux_backend.core.memory_manager as mm
import optimux_backend.main as main
from optimux_backend.core import budgets, sessions, usage
from tests.ws_helpers import Conversation, make_client
from tests.test_routing_session import Recorder, reply

H = {"Authorization": "Bearer test-token"}
MEMORY = "# alpha\n\n## Commands\n- Run tests: `pytest -q`\n"


@pytest.fixture
def mem(mocker, tmp_path):
    mocker.patch.object(mm, "GLOBAL_DIR", str(tmp_path / "memory"))
    mocker.patch.object(mm, "PROJECTS_DIR", str(tmp_path / "memory" / "projects"))
    mocker.patch.object(mm, "PERSONAL_MD", str(tmp_path / "memory" / "personal.md"))
    mocker.patch.object(mm, "PROFESSIONAL_MD", str(tmp_path / "memory" / "professional.md"))
    return tmp_path / "memory" / "projects"


def chat(project="alpha", turns=1, long=True):
    sid = sessions.create(project, "fix tests")
    events = []
    for i in range(turns):
        events += [{"type": "prompt", "content": f"request {i}: make the tests pass"},
                   {"type": "command_output", "command": "pytest -q", "output": "3 passed" + (" ." * 150 if long else ""), "exit_code": 0},
                   {"type": "token", "content": "Tests pass with `pytest -q`; the project uses Python 3.10."}]
    sessions.append_events(sid, events)
    sessions.save_state(sid, [], "simple", False, True, turns)
    return sid


def llm_reply(text):
    return NS(choices=[NS(message=NS(content=text))], usage={"prompt_tokens": 900, "completion_tokens": 120})


# ---------- pure helpers

def test_transcript_skips_curated_turns():
    events = [{"type": "prompt", "content": "first"}, {"type": "token", "content": "a1"},
              {"type": "prompt", "content": "second"}, {"type": "command_output", "command": "ls", "output": "x", "exit_code": 0}]
    t = curator.transcript(events, skip_turns=1)
    assert "first" not in t and "USER: second" in t and "COMMAND: ls (exit 0)" in t


def test_parse_reply_and_checks():
    assert curator.parse_reply("NO_CHANGES") == (None, "No durable changes.")
    new, summary = curator.parse_reply("```markdown\n# alpha\n- x\nSUMMARY: added x\n```")
    assert new == "# alpha\n- x\n" and summary == "added x"
    assert curator.acceptable("# alpha\n- x\n") is None
    assert "markdown" in curator.acceptable("just text")
    assert "too long" in curator.acceptable("# a\n" + "x" * (curator.MAX_CHARS * 2))


def test_prompt_has_rules_sections_and_current_memory():
    p = curator.build_prompt("alpha", MEMORY, "USER: hi")
    assert "Known issues" in p and "pytest -q" in p and "NO_CHANGES" in p and "secrets" in p


# ---------- versions

def test_versions_record_diff_and_undo(mem):
    v1 = curator.write_memory("alpha", MEMORY, "user", "first")
    assert curator.write_memory("alpha", MEMORY, "user", "same") is None          # no change, no version
    curator.write_memory("alpha", MEMORY + "- Lint: `ruff check .`\n", "curator", "added lint")
    versions = curator.list_versions("alpha")
    assert [v["source"] for v in versions] == ["curator", "user"]
    assert "+- Lint: `ruff check .`" in versions[0]["diff"]
    out = curator.undo(versions[0]["id"])
    assert curator.read_memory("alpha") == MEMORY and out["project"] == "alpha"
    assert curator.list_versions("alpha")[0]["source"] == "undo"
    with pytest.raises(KeyError):
        curator.undo(999)


def test_model_tool_additions_are_versioned(mem, mocker):
    mocker.patch.object(mm, "get_settings", return_value={"active_project": "alpha"})
    mm.update_memory("project", "- Deploy with `make deploy`")
    v = curator.list_versions("alpha")[0]
    assert v["source"] == "model" and "make deploy" in v["diff"]


# ---------- curation

@pytest.fixture
def client(mocker, tmp_path, mem):
    c = make_client(mocker, tmp_path)
    mocker.patch.object(main, "get_routing_config", return_value={"simple_model": "simple-model"})
    return c


def run(coro):
    return asyncio.run(coro)


def test_curation_updates_memory_once(mocker, client, mem):
    sid = chat()
    new = "# alpha\n\n## Commands\n- Run tests: `pytest -q`\n\n## Stack\n- Python 3.10\n\nSUMMARY: Added stack."
    call = mocker.patch("litellm.acompletion", return_value=llm_reply(new))
    out = run(main.curate_session(sid, force=True))
    assert out["changed"] and out["summary"] == "Added stack."
    assert "Python 3.10" in curator.read_memory("alpha")
    assert usage.read_entries()[-1]["role"] == "curator" and usage.read_entries()[-1]["project"] == "alpha"
    assert "request 0" in call.call_args.kwargs["messages"][0]["content"]
    assert run(main.curate_session(sid, force=True))["reason"] == "Nothing new in the chat since the last update." and call.call_count == 1
    v = curator.list_versions("alpha")[0]
    assert v["source"] == "curator" and v["session_id"] == sid


def test_only_new_turns_are_sent(mocker, client, mem):
    sid = chat(turns=1)
    mocker.patch("litellm.acompletion", return_value=llm_reply("NO_CHANGES"))
    run(main.curate_session(sid, force=True))
    sessions.append_events(sid, [{"type": "prompt", "content": "second request about the Makefile " + "x" * 300}])
    sessions.save_state(sid, [], "simple", False, True, 1)
    call = mocker.patch("litellm.acompletion", return_value=llm_reply("NO_CHANGES"))
    run(main.curate_session(sid, force=True))
    sent = call.call_args.kwargs["messages"][0]["content"]
    assert "Makefile" in sent and "request 0" not in sent


def _short_chat():
    sid = sessions.create("alpha", "hello")
    sessions.append_events(sid, [{"type": "prompt", "content": "hello"}, {"type": "token", "content": "Hi!"}])
    sessions.save_state(sid, [], "simple", False, True, 1)
    return sid


@pytest.mark.parametrize("setup,reason", [
    (lambda: chat(project=""), "Workspace chats have no project memory."),           # chats at the projects root
    (_short_chat, "The chat is too short to learn from."),
])
def test_skips_without_a_call(mocker, client, mem, setup, reason):
    sid = setup()
    call = mocker.patch("litellm.acompletion")
    assert run(main.curate_session(sid, force=True))["reason"] == reason
    call.assert_not_called()


def test_budget_and_disabled_skip(mocker, client, mem):
    sid = chat()
    call = mocker.patch("litellm.acompletion")
    assert run(main.curate_session(sid))["reason"] == "Automatic updates are off."            # disabled in tests, not forced
    budgets.save_budget("alpha", 0.01)
    usage.insert({"project": "alpha", "cost": 1.0})
    assert run(main.curate_session(sid, force=True))["reason"] == "The project's budget is reached."
    call.assert_not_called()


def test_bad_results_are_rejected_and_no_changes_kept(mocker, client, mem):
    curator.write_memory("alpha", MEMORY, "user")
    sid = chat()
    mocker.patch("litellm.acompletion", return_value=llm_reply("Sure! Here is the memory."))
    assert "rejected" in run(main.curate_session(sid, force=True))["reason"]
    assert curator.read_memory("alpha") == MEMORY
    sid2 = chat()
    mocker.patch("litellm.acompletion", return_value=llm_reply("NO_CHANGES"))
    assert run(main.curate_session(sid2, force=True))["changed"] is False
    assert len(curator.list_versions("alpha")) == 1


def test_errors_never_raise(mocker, client, mem):
    sid = chat()
    mocker.patch("litellm.acompletion", side_effect=RuntimeError("boom"))
    assert "error" in run(main.curate_session(sid, force=True))["reason"]


# ---------- triggers

def test_new_chat_and_disconnect_trigger_curation(mocker, client):
    mocker.patch.object(main, "curator_enabled", return_value=True)
    mocker.patch.object(main, "active_project", return_value="alpha")
    mocker.patch.object(main, "curator_idle_seconds", return_value=3600)
    seen = []

    async def fake(sid, websocket=None, force=False):
        seen.append(sid)
        return {}

    mocker.patch.object(main, "curate_session", side_effect=fake)
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    c = Conversation(client)
    c.prompt("one")
    first = sessions.latest("alpha")
    c.send({"type": "new_chat"})
    c.prompt("two")
    second = sessions.latest("alpha")
    c.close()
    time.sleep(0.2)
    assert seen[0] == first and second in seen


def test_idle_timer_triggers_curation(mocker, client):
    mocker.patch.object(main, "curator_enabled", return_value=True)
    mocker.patch.object(main, "active_project", return_value="alpha")
    mocker.patch.object(main, "curator_idle_seconds", return_value=0.05)
    seen = []

    async def fake(sid, websocket=None, force=False):
        seen.append(sid)
        return {}

    mocker.patch.object(main, "curate_session", side_effect=fake)
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a")))
    c = Conversation(client)
    c.prompt("one")
    time.sleep(0.3)
    assert seen == [sessions.latest("alpha")]
    c.close()


# ---------- API

def test_api_versions_undo_and_curate_now(mocker, client, mem):
    assert client.post("/api/memory/project/alpha", json={"content": MEMORY}, headers=H).json()["version_id"]
    client.post("/api/memory/project/alpha", json={"content": MEMORY + "- more\n"}, headers=H)
    vs = client.get("/api/memory/project/alpha/versions", headers=H).json()
    assert len(vs) == 2 and vs[0]["source"] == "user" and "+- more" in vs[0]["diff"]
    assert client.post(f"/api/memory/versions/{vs[0]['id']}/undo", headers=H).status_code == 200
    assert curator.read_memory("alpha") == MEMORY
    assert client.post("/api/memory/versions/9999/undo", headers=H).status_code == 404
    assert client.post("/api/memory/project/alpha/curate", headers=H).json()["reason"] == "There are no chats in this project yet."
    sid = chat()
    mocker.patch("litellm.acompletion", return_value=llm_reply(MEMORY + "- new fact\nSUMMARY: s"))
    assert client.post("/api/memory/project/alpha/curate", headers=H).json()["changed"] is True
    assert client.get("/api/memory/project/alpha/versions").status_code == 401


def test_curator_settings_validation():
    from optimux_backend.core.config_manager import SettingsPayload, save_settings
    from optimux_backend.core.security import ValidationError
    with pytest.raises(ValidationError):
        save_settings(SettingsPayload(curator_idle_minutes=0))


# ---------- fix 17b: Update now after an undo, costs, Usage group

def test_update_now_rereads_the_whole_chat_after_undo(mocker, client, mem):
    """Update now must not answer 'nothing new' just because the chat was read before
    (e.g. the user undid that update and wants it again)."""
    mocker.patch.object(usage, "cost_for", return_value=0.0012)
    curator.write_memory("alpha", MEMORY, "user", "start")
    sid = chat()
    new = MEMORY + "- Python 3.10\nSUMMARY: Added Python version."
    call = mocker.patch("litellm.acompletion", return_value=llm_reply(new))
    out = run(main.curate_session(sid, force=True))                   # automatic-style run
    assert out["changed"] and out["cost"] == 0.0012
    assert curator.list_versions("alpha")[0]["cost"] == 0.0012
    curator.undo(out["version_id"])
    assert curator.read_memory("alpha") == MEMORY
    r = client.post("/api/memory/project/alpha/curate", headers=H).json()
    assert r["changed"] is True and r["chat_title"] == "fix tests" and call.call_count == 2
    assert "request 0" in call.call_args.kwargs["messages"][0]["content"]   # read from the start again
    assert "Python 3.10" in curator.read_memory("alpha")


def test_update_now_reports_no_changes_with_cost(mocker, client, mem):
    mocker.patch.object(usage, "cost_for", return_value=0.0004)
    chat()
    mocker.patch("litellm.acompletion", return_value=llm_reply("NO_CHANGES"))
    r = client.post("/api/memory/project/alpha/curate", headers=H).json()
    assert r["changed"] is False and r["reason"] == "The chat had nothing durable to add." and r["cost"] == 0.0004


def test_memory_updates_usage_group(mocker, client, mem):
    mocker.patch.object(usage, "cost_for", return_value=0.001)
    sid = chat()
    mocker.patch("litellm.acompletion", return_value=llm_reply("NO_CHANGES"))
    run(main.curate_session(sid, force=True))
    usage.record("s", "simple", "simple-model", {"prompt_tokens": 10, "completion_tokens": 1}, project="alpha")
    s = usage.summary()["all_time"]
    assert s["memory_updates"]["alpha"]["calls"] == 1 and s["memory_updates"]["alpha"]["cost"] == 0.001
    assert s["by_role"]["curator"]["calls"] == 1 and "simple" not in s["memory_updates"]


# ---------- Feature 18: background status and cost estimates

class _WS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def test_page_sees_updating_then_outcome(mocker, client, mem):
    mocker.patch.object(usage, "cost_for", return_value=0.002)
    sid = chat()
    ws = _WS()
    mocker.patch("litellm.acompletion", return_value=llm_reply(MEMORY + "SUMMARY: s"))
    run(main.curate_session(sid, ws, force=True))
    assert [m["type"] for m in ws.sent] == ["memory_updating", "memory_updated"]
    assert ws.sent[1]["cost"] == 0.002 and ws.sent[0]["project"] == "alpha"
    sessions.append_events(sid, [{"type": "prompt", "content": "more " + "x" * 300}])
    sessions.save_state(sid, [], "simple", False, True, 2)
    mocker.patch("litellm.acompletion", return_value=llm_reply("NO_CHANGES"))
    ws2 = _WS()
    run(main.curate_session(sid, ws2, force=True))
    assert [m["type"] for m in ws2.sent] == ["memory_updating", "memory_unchanged"]
    assert ws2.sent[1]["reason"] == "The chat had nothing durable to add."


def test_failed_update_still_ends_the_indicator(mocker, client, mem):
    sid = chat()
    ws = _WS()
    mocker.patch("litellm.acompletion", side_effect=RuntimeError("boom"))
    run(main.curate_session(sid, ws, force=True))
    assert [m["type"] for m in ws.sent] == ["memory_updating", "memory_unchanged"]
    assert ws.sent[1]["reason"] == "The update failed."


def test_skips_send_nothing(mocker, client, mem):
    ws = _WS()
    run(main.curate_session(_short_chat(), ws, force=True))
    assert ws.sent == []


def test_cost_estimates(mocker, client, mem):
    assert client.get("/api/costs/estimates", headers=H).json()["memory_update"] is None
    for cost in (0.001, 0.003):
        usage.insert({"ts": time.time(), "session": "s", "project": "alpha", "role": "curator", "model": "m",
                      "prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0, "reasoning_tokens": 0,
                      "cost": cost, "duration_ms": 1})
    usage.insert({"ts": time.time(), "session": "s", "project": "alpha", "role": "complex", "model": "big",
                  "prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0, "reasoning_tokens": 0,
                  "cost": None, "duration_ms": 1})
    r = client.get("/api/costs/estimates", headers=H).json()
    assert r["memory_update"]["n"] == 2 and abs(r["memory_update"]["avg"] - 0.002) < 1e-9
    assert r["complex_call"] is None                       # unpriced calls don't count
    assert r["memory_this_month"]["calls"] == 2 and abs(r["memory_this_month"]["cost"] - 0.004) < 1e-9
    assert client.get("/api/costs/estimates").status_code == 401


def test_confirm_settings_default_on():
    from optimux_backend.core.config_manager import SettingsPayload
    p = SettingsPayload()
    assert p.confirm_update_memory is True and p.confirm_escalate is True
