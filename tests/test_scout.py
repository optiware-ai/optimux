"""v0.8: the scout. Before a Complex task the Simple model reads the project with
read-only tools and writes a brief, so the Complex model doesn't pay for the looking around."""
import json
import os
from types import SimpleNamespace as NS

import pytest

import optimux_backend.main as main
from optimux_backend.core import config_manager, paths, scout
from tests.test_routing_session import Recorder, notes, reply
from tests.ws_helpers import ROUTING, Conversation, make_client


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HOME", str(tmp_path / "home"))
    root = tmp_path / "ws" / "app"
    (root / "src").mkdir(parents=True)
    (root / "src" / "player.py").write_text("from engine import Input\n\ndef physics_process(body, delta):\n"
                                           "    body.velocity_x = Input.get_axis(\"left\", \"right\") * SPEED\n")
    (root / "settings").mkdir()
    (root / "settings" / "app.toml").write_text("name = \"Main\"\ntype = \"service\"\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    return root


# ---------------------------------------------------------------- read-only tools

def test_read_list_and_search_stay_inside_the_project(project, tmp_path):
    out = scout.read_file(str(project), "src/player.py")
    assert out.startswith("src/player.py (4 lines)") and "3: def physics_process(body, delta):" in out
    assert "2: " in scout.read_file(str(project), "src/player.py", start_line=2).splitlines()[1]
    assert scout.list_files(str(project)).splitlines() == ["settings/app.toml", "src/player.py"]
    assert scout.list_files(str(project), "src") == "src/player.py"
    assert scout.search_files(str(project), "get_axis") == \
        'src/player.py:4: body.velocity_x = Input.get_axis("left", "right") * SPEED'
    (tmp_path / "secret.txt").write_text("x")
    os.symlink(tmp_path / "secret.txt", project / "link.txt")
    for name, args in [("read_file", {"path": "../secret.txt"}), ("read_file", {"path": "link.txt"}),
                       ("read_file", {"path": ".git/config"}), ("list_files", {"path": "../"}),
                       ("search_files", {"text": "x", "path": "/etc"})]:
        assert scout.run_tool(str(project), name, json.dumps(args)).startswith("Error:"), name
    assert "[core]" not in scout.search_files(str(project), "core")      # .git is never searched
    assert scout.run_tool(str(project), "run_command", "{}").startswith("Error: there is no tool")


def test_search_finds_names_too_and_every_lookup_is_logged(project):
    """The Complex model ran find -name *player* after the scout: names count in the search,
    and each lookup's real outcome is written down by Optimux."""
    assert scout.search_files(str(project), "PLAYER").splitlines() == [
        "src/player.py: (the file's name or folder matches)"]
    assert scout.search_files(str(project), "controller") == "No file name or line contains 'controller'."
    def log(name, args):
        return scout.outcome(name, json.dumps(args), scout.run_tool(str(project), name, json.dumps(args)))
    assert log("search_files", {"text": "controller"}) == 'search "controller" → nothing found'
    assert log("search_files", {"text": "velocity"}) == 'search "velocity" → 1 match in src/player.py'
    assert log("search_files", {"text": "e", "path": "settings"}) == 'search "e" in settings/ → 3 matches in settings/app.toml'
    assert log("list_files", {}) == "list the project → 2 files"
    assert log("read_file", {"path": "src/player.py"}) == "read src/player.py → 4 lines"
    assert log("read_file", {"path": "nope.py"}) == "read nope.py → nope.py doesn't exist."
    assert scout.outcome("read_file", '{"path": "a"}', "Not run: the reading budget is used up.") == \
        "read a → not run (budget used up)"


def test_a_long_file_is_read_in_parts(project):
    (project / "big.py").write_text("\n".join(f"v{i} = {i}" for i in range(500)))
    out = scout.read_file(str(project), "big.py")
    assert f"{scout.READ_LINES}: v{scout.READ_LINES - 1}" in out
    assert out.endswith(f"(continues: read again from line {scout.READ_LINES + 1})")


def test_requests_that_name_files_are_recognised():
    files = ["src/player.py", "settings/app.toml", "README.md"]
    assert scout.names_files("refactor ./src/player.py into a state machine", files) == ["src/player.py"]
    assert scout.names_files("fix the jump in player.py", files) == ["src/player.py"]
    assert scout.names_files("refactor the player controller into a state machine", files) == []
    assert scout.names_files("bump the version to 0.1.0 and e.g. tidy up", files) == []


def test_skip_reasons(project):
    files = ["src/player.py"]
    assert scout.skip_reason("refactor the controller", ROUTING, str(project), files) == ""
    assert scout.skip_reason("x", {**ROUTING, "simple_model": "complex-model"}, str(project), files) == \
        "Simple and Complex are the same model"
    assert scout.skip_reason("x", ROUTING, "", []) == "no project is open"
    assert scout.skip_reason("x", ROUTING, str(project), []) == "the project has no files yet"
    assert scout.skip_reason("fix player.py", ROUTING, str(project), files) == "the request names src/player.py"


# ---------------------------------------------------------------- the scout's loop

def answer(content="", tools=(), prompt_tokens=100):
    calls = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(tools)]
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls or None))],
              usage=NS(prompt_tokens=prompt_tokens, completion_tokens=10))


class Calls:
    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    async def __call__(self, **kw):
        self.seen.append(json.loads(json.dumps(kw, default=str)))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.mark.asyncio
async def test_the_scout_reads_then_writes_the_brief(project):
    call = Calls(answer(tools=[("search_files", {"text": "velocity"}), ("read_file", {"path": "src/player.py"})]),
                 answer("Relevant files: src/player.py — the player's movement."))
    out = await scout.run("refactor the player controller", "simple-model", "MEMORY", str(project), call=call)
    assert out["brief"].startswith("Relevant files: src/player.py") and out["tools"] == 2
    assert out["read"] == ["src/player.py"] and len(out["calls"]) == 2
    assert out["log"] == ['search "velocity" → 1 match in src/player.py', "read src/player.py → 4 lines"]
    assert out["files"] == ["settings/app.toml", "src/player.py"] and out["total"] == 2
    first, second = call.seen
    assert first["model"] == "simple-model" and first["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in first["tools"]} == {"list_files", "read_file", "search_files"}
    assert "src/player.py" in first["messages"][1]["content"] and "MEMORY" in first["messages"][1]["content"]
    results = [m["content"] for m in second["messages"] if m["role"] == "tool"]
    assert "get_axis" in results[0] and results[1].startswith("src/player.py (4 lines)")


@pytest.mark.asyncio
async def test_the_reading_budget_is_kept(project):
    def reads(turn):
        return [("read_file", {"path": "src/player.py", "start_line": turn * 3 + i + 1}) for i in range(3)]
    call = Calls(*[answer(tools=reads(t)) for t in range(3)], answer("the brief"))
    out = await scout.run("x", "simple-model", "", str(project), call=call)
    assert out["tools"] == scout.MAX_TOOL_CALLS and out["brief"] == "the brief"
    assert call.seen[-1]["tool_choice"] == "none"
    assert "write the brief now" in call.seen[-1]["messages"][-1]["content"]
    skipped = [m["content"] for m in call.seen[-1]["messages"] if m["role"] == "tool"][-1]
    assert skipped == "Not run: the reading budget is used up."


@pytest.mark.asyncio
async def test_a_lookup_already_run_is_not_run_or_counted_again(project):
    """gpt-5-nano once ran the same search and the same read twice each."""
    same = ("search_files", {"text": "velocity"})
    call = Calls(answer(tools=[same]), answer(tools=[("search_files", {"text": "velocity"}), ("list_files", {})]),
                 answer("the brief"))
    out = await scout.run("x", "simple-model", "", str(project), call=call)
    assert out["tools"] == 2 and len(out["log"]) == 2
    results = [m["content"] for m in call.seen[-1]["messages"] if m["role"] == "tool"]
    assert results[1] == "Already run above, with the same result: use that one."


@pytest.mark.asyncio
async def test_a_large_context_ends_the_reading_early(project):
    call = Calls(answer(tools=[("list_files", {})], prompt_tokens=scout.MAX_PROMPT_TOKENS), answer("short brief"))
    out = await scout.run("x", "simple-model", "", str(project), call=call)
    assert out["tools"] == 1 and call.seen[1]["tool_choice"] == "none" and out["brief"] == "short brief"


def overloaded():
    import litellm
    return litellm.ServiceUnavailableError(message="The model is overloaded. Please try again later.",
                                           llm_provider="gemini", model="simple-model")


@pytest.mark.asyncio
async def test_a_passing_error_is_tried_again(project, monkeypatch):
    """Gemini answered the scout with 503 "overloaded" and the scout gave up at once."""
    monkeypatch.setattr(scout, "RETRY_DELAYS", (0, 0))
    call = Calls(overloaded(), overloaded(), answer("the brief"))
    out = await scout.run("x", "simple-model", "", str(project), call=call)
    assert out["brief"] == "the brief" and len(call.seen) == 3 and len(out["calls"]) == 1
    call = Calls(overloaded(), overloaded(), overloaded())
    with pytest.raises(Exception, match="overloaded"):
        await scout.run("x", "simple-model", "", str(project), call=call)
    assert len(call.seen) == 3


@pytest.mark.asyncio
async def test_a_refusal_is_not_tried_again(project, monkeypatch):
    import litellm
    monkeypatch.setattr(scout, "RETRY_DELAYS", (0, 0))
    call = Calls(litellm.BadRequestError(message="tool_choice none is not supported", llm_provider="x", model="m"))
    with pytest.raises(litellm.BadRequestError):
        await scout.run("x", "simple-model", "", str(project), call=call)
    assert len(call.seen) == 1


# ---------------------------------------------------------------- in the chat

@pytest.fixture
def chat(mocker, tmp_path, project, monkeypatch):
    settings = {"projects_root": str(project.parent), "active_project": "app"}
    monkeypatch.setattr(config_manager, "get_settings", lambda: dict(settings))
    client = make_client(mocker, tmp_path, tier="complex")
    mocker.patch.object(main, "get_settings", side_effect=lambda: dict(settings))
    mocker.patch.object(main, "active_project", side_effect=lambda: settings["active_project"])
    mocker.patch.object(main, "current_workdir", return_value=str(project))
    run = mocker.patch.object(scout, "run", return_value={
        "brief": "Relevant files: src/player.py", "read": ["src/player.py"], "tools": 2,
        "calls": [({"prompt_tokens": 900, "completion_tokens": 80}, 40)],
        "log": ['search "player" → 2 matches in src/player.py', "read src/player.py → 4 lines"],
        "files": ["settings/app.toml", "src/player.py"], "total": 2})
    return client, settings, run


def test_a_complex_task_starts_with_the_scouts_brief(mocker, chat):
    client, _settings, run = chat
    rec = Recorder(reply("Done."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat_ = Conversation(client)
    msgs = chat_.prompt("refactor the player controller into a state machine")
    chat_.close()
    assert run.call_args.args[:2] == ("refactor the player controller into a state machine", "simple-model")
    said = notes(msgs)
    assert "Scout: simple-model reads the project first, so complex-model starts with a brief." in said
    brief = next(m for m in msgs if m["type"] == "system" and m.get("brief"))
    assert brief["content"] == "Scout brief: 1 file read, 2 lookups."
    assert brief["brief"] == ("Relevant files: src/player.py\n\nLookups run:\n"
                              '- search "player" → 2 matches in src/player.py\n- read src/player.py → 4 lines')
    scouted = [m for m in msgs if m["type"] == "usage" and m["role"] == "scout"]
    assert len(scouted) == 1 and scouted[0]["model"] == "simple-model"
    sent = rec.calls[0]
    assert sent["model"] == "complex-model"
    user = sent["messages"][-1]["content"]
    assert user.startswith("refactor the player controller into a state machine\n\n---\nScout report.")
    # Optimux's own record (exact), then the scout's brief, then how to use them
    assert "Project files (2 in all):\nsettings/app.toml\nsrc/player.py" in user
    assert '- search "player" → 2 matches in src/player.py' in user
    assert "Scout's brief:\nRelevant files: src/player.py" in user
    assert "don't repeat these lookups" in user and "ask the user how to go on" in user


def test_no_scout_when_off_on_simple_or_when_files_are_named(mocker, chat):
    client, settings, run = chat
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b"), reply("c")))
    msgs = Conversation(client).prompt("fix the jump in player.py")
    assert "Scout skipped: the request names src/player.py." in notes(msgs)
    settings["scout"] = False
    msgs = Conversation(client).prompt("refactor the player controller")
    assert not [n for n in notes(msgs) if n.startswith("Scout")]
    settings["scout"] = True
    main._test_conductor.return_value = ("simple", "conductor-model", {"prompt_tokens": 5, "completion_tokens": 1}, 3)
    msgs = Conversation(client).prompt("what is a state machine?")
    assert not [n for n in notes(msgs) if n.startswith("Scout")]
    assert not run.called


def test_a_follow_up_on_complex_is_not_scouted_again(mocker, chat):
    client, _settings, run = chat
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    c = Conversation(client)
    c.prompt("refactor the player controller into a state machine")
    msgs = c.prompt("now split the states into their own files")
    c.close()
    assert run.call_count == 1
    assert "Scout skipped: the chat was already on Complex, which has the project in view." in notes(msgs)


def test_a_failing_scout_lets_the_complex_model_start_anyway(mocker, chat):
    client, _settings, run = chat
    import litellm
    run.side_effect = litellm.ServiceUnavailableError(message="high demand", llm_provider="gemini", model="simple-model")
    rec = Recorder(reply("Done."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    msgs = Conversation(client).prompt("refactor the player controller")
    assert any(n.startswith("Scout failed (") and n.endswith("complex-model starts without a brief.") for n in notes(msgs))
    assert rec.calls[0]["messages"][-1]["content"] == "refactor the player controller"
    from optimux_backend.core import providers
    assert providers.is_available("simple-model")             # no cooldown for a passing error


def test_a_chat_pinned_to_complex_is_not_scouted(mocker, chat):
    """/complex pins the tier and skips the Conductor: only the Conductor's own "complex"
    starts a scout, so an older decision of this chat never does."""
    client, _settings, run = chat
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("a"), reply("b")))
    c = Conversation(client)
    c.prompt("refactor the player controller into a state machine")     # the Conductor said complex: scouted
    c.prompt("/complex")
    msgs = c.prompt("now add a jump state")
    c.close()
    assert run.call_count == 1 and not [n for n in notes(msgs) if n.startswith("Scout")]


def test_a_saved_key_in_the_brief_is_hidden_in_the_chat(mocker, chat, monkeypatch):
    from optimux_backend.core import security
    key = "sk-ant-0123456789abcdef"
    monkeypatch.setattr(security, "secret_values", lambda: [("ANTHROPIC_API_KEY", key)])
    client, _settings, run = chat
    run.return_value = {**run.return_value, "brief": f"settings/app.toml holds the key {key}"}
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("Done.")))
    msgs = Conversation(client).prompt("refactor the player controller")
    brief = next(m for m in msgs if m["type"] == "system" and m.get("brief"))
    assert key not in brief["brief"] and "••••(ANTHROPIC_API_KEY)" in brief["brief"]


def test_the_scouts_calls_are_compared_with_the_complex_model():
    """Savings view: with everything on Complex, the Complex model would have done this
    reading itself, so the scout's tokens are priced at the reference model (not overhead)."""
    from optimux_backend.core import usage
    assert "scout" not in usage.REF_ZERO_ROLES and "scout" not in usage.REF_SKIP_ROLES
