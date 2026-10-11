"""Skills: format, discovery, Conductor selection, overrides, load_skill, metering, API, drafts."""
import json
import os

import pytest

import optimux_backend.core.skills as sk
import optimux_backend.main as main
from optimux_backend.core import paths, sessions, usage
from tests.ws_helpers import Conversation, chunk, fake_stream, make_client
from tests.test_routing_session import Recorder, reply

H = {"Authorization": "Bearer test-token"}
DONE = {"prompt_tokens": 1, "completion_tokens": 1}


def write(root, name, text):
    os.makedirs(os.path.join(root, name), exist_ok=True)
    with open(os.path.join(root, name, "SKILL.md"), "w") as f:
        f.write(text)


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    g = tmp_path / "global"
    proj = tmp_path / "proj"
    g.mkdir(); proj.mkdir()
    monkeypatch.setattr(paths, "SKILLS_DIR", str(g))
    monkeypatch.setattr(paths, "LEGACY_DIR", str(tmp_path / "legacy"))
    return str(g), str(proj)


SKILL = "---\nname: deploy\ndescription: Deploy the app to staging.\nmode: auto\ntier: complex\nmax_steps: 3\nthinking: low\n---\n## Steps\n1. run ./deploy.sh\n"


# ---------- module

def test_parse_and_meta(dirs):
    g, _ = dirs
    write(g, "deploy", SKILL)
    open(os.path.join(g, "deploy", "deploy.sh"), "w").close()
    write(g, "broken", "---\nname: broken\ntier: huge\n---\nno description")
    write(g, "Bad_Name", "---\ndescription: x\n---\n")
    found = sk.discover()
    assert list(found) == ["deploy"]
    d = found["deploy"]
    assert (d["tier"], d["max_steps"], d["thinking"], d["scope"]) == ("complex", 3, "low", "global")
    loaded = sk.load("deploy")
    assert loaded["instructions"].startswith("## Steps") and loaded["files"] == ["deploy.sh"]
    assert sk.catalog_text(found) == "- deploy: Deploy the app to staging."
    with pytest.raises(sk.SkillError):
        sk.load("nope")


def test_project_skill_overrides_global(dirs):
    g, proj = dirs
    write(g, "deploy", SKILL)
    write(os.path.join(proj, sk.PROJECT_SKILLS_DIR), "deploy", "---\ndescription: Project deploy.\n---\nlocal")
    assert sk.discover(proj)["deploy"]["scope"] == "project"
    assert sk.discover()["deploy"]["scope"] == "global"


def test_save_remove_and_validation(dirs):
    g, proj = dirs
    with pytest.raises(sk.SkillError, match="description"):
        sk.save_raw("global", "x", "---\nname: x\n---\nbody", None)
    with pytest.raises(sk.SkillError):
        sk.save_raw("global", "Bad Name", SKILL, None)
    with pytest.raises(sk.SkillError):
        sk.save_raw("project", "x", SKILL, None)
    sk.save_raw("project", "deploy", SKILL, proj)
    assert os.path.isfile(os.path.join(proj, sk.PROJECT_SKILLS_DIR, "deploy", "SKILL.md"))
    moved = sk.remove("project", "deploy", proj)
    assert os.path.isdir(moved) and "legacy" in moved and not sk.discover(proj)


def test_starters_only_on_empty_folder(dirs):
    g, _ = dirs
    assert sorted(sk.seed_starters()) == ["commit-message", "run-tests"]
    assert sk.seed_starters() == []
    assert set(sk.discover()) == {"commit-message", "run-tests"}


# ---------- in the chat

@pytest.fixture
def client(mocker, tmp_path, dirs):
    c = make_client(mocker, tmp_path)
    g, _ = dirs
    write(g, "deploy", SKILL)
    return c


def test_conductor_pick_loads_skill_and_applies_overrides(mocker, client):
    main._test_conductor.side_effect = lambda msg, skills=None: ("simple", "conductor-model", None, 0, "deploy")
    calls = []

    def fake(**kw):
        calls.append(kw)
        return reply("Deployed.")

    mocker.patch("litellm.acompletion", side_effect=fake)
    msgs = Conversation(client).prompt("deploy to staging")
    notes = [m["content"] for m in msgs if m["type"] == "system"]
    assert any(n.startswith("Skill: deploy (uses the complex tier)") for n in notes)
    assert calls[0]["model"] == "complex-model"                       # skill tier wins over the Conductor's
    assert calls[0]["reasoning_effort"] == "low"                      # skill thinking level
    system = calls[0]["messages"][0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert "ACTIVE SKILL: deploy" in system and "run ./deploy.sh" in system
    assert "- deploy: Deploy the app to staging." in system
    assert any(t["function"]["name"] == "load_skill" for t in calls[0]["tools"])
    assert {e.get("skill") for e in usage.read_entries() if e["role"] == "complex"} == {"deploy"}


def test_skill_step_limit(mocker, client):
    main._test_conductor.side_effect = lambda msg, skills=None: ("simple", "conductor-model", None, 0, "deploy")
    mocker.patch.object(main, "update_memory", return_value="saved")
    loop = lambda **kw: fake_stream([chunk(tool_call=("update_markdown_memory", {"file_type": "personal", "content": str(len(kw["messages"]))})),
                                     chunk(empty=True, usage_obj=DONE)])
    rec = mocker.patch("litellm.acompletion", side_effect=loop)
    msgs = Conversation(client).prompt("deploy")
    assert rec.call_count == 3 and any(m["type"] == "step_limit" for m in msgs)


def test_pinned_tier_keeps_its_tier(mocker, client):
    main._test_conductor.side_effect = lambda msg, skills=None: ("simple", "conductor-model", None, 0, "deploy")
    calls = []
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: calls.append(kw) or reply("ok"))
    chat = Conversation(client)
    chat.prompt("/simple")
    chat.prompt("deploy")      # pinned: the Conductor isn't asked, no skill picked
    assert calls[-1]["model"] == "simple-model"


def test_load_skill_tool(mocker, client):
    queue = [fake_stream([chunk(tool_call=("load_skill", {"name": "deploy"})), chunk(empty=True, usage_obj=DONE)]),
             reply("Following the deploy skill.")]
    calls = []
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: calls.append(kw) or queue.pop(0))
    msgs = Conversation(client).prompt("ship it")
    assert any(m["type"] == "system" and m["content"] == "Skill loaded: deploy" for m in msgs)
    tool = json.loads([m for m in calls[1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert tool["ok"] and "run ./deploy.sh" in tool["instructions"]
    assert "reasoning_effort" not in calls[0] and calls[1]["reasoning_effort"] == "low"   # applies from the next call
    assert usage.read_entries()[-1]["skill"] == "deploy"


def test_skill_scripts_are_not_flagged_outside(mocker, dirs):
    g, _ = dirs
    mocker.patch.object(main, "active_project", return_value="")
    assert main.in_skill_folder(os.path.join(g, "deploy", "deploy.sh"))
    assert not main.in_skill_folder("/etc/passwd")


def test_skills_api(mocker, client, dirs):
    g, _ = dirs
    usage.insert({"skill": "deploy", "cost": 0.2, "role": "complex"})
    data = client.get("/api/skills", headers=H).json()
    d = next(s for s in data["skills"] if s["name"] == "deploy")
    assert d["scope"] == "global" and d["usable"] and d["all_time"]["cost"] == 0.2
    assert client.get("/api/skills/global/deploy", headers=H).json()["content"] == SKILL
    assert client.post("/api/skills/global/new-one", json={"content": "---\ndescription: New.\n---\nx"}, headers=H).status_code == 200
    assert client.post("/api/skills/global/new-two", json={"content": "no front matter"}, headers=H).status_code == 400
    assert "description:" in client.get("/api/skills/template?name=abc", headers=H).json()["content"]
    assert client.post("/api/skills/global/new-one/remove", headers=H).status_code == 200
    assert client.get("/api/skills/global/new-one", headers=H).status_code == 404
    assert client.get("/api/skills").status_code == 401


def test_draft_from_chat(mocker, client):
    sid = sessions.create("", "release notes")
    sessions.append_events(sid, [{"type": "prompt", "content": "write release notes"},
                                 {"type": "command_start", "command": "git log --oneline -20"},
                                 {"type": "token", "content": "Here are the notes."}])
    from types import SimpleNamespace as NS
    resp = NS(choices=[NS(message=NS(content="```markdown\n---\nname: release-notes\ndescription: Draft release notes.\n---\nsteps\n```"))],
              usage={"prompt_tokens": 50, "completion_tokens": 20})
    call = mocker.patch("litellm.acompletion", return_value=resp)
    r = client.post("/api/skills/draft", json={"session_id": sid, "name": "release-notes"}, headers=H).json()
    assert r["content"].startswith("---\nname: release-notes") and "```" not in r["content"]
    assert "COMMAND: git log --oneline -20" in call.call_args.kwargs["messages"][0]["content"]
    assert usage.read_entries()[-1]["role"] == "skill_draft"
    assert client.post("/api/skills/draft", json={"session_id": "nope"}, headers=H).status_code == 404


def test_loading_the_active_skill_again_is_refused_cheaply(mocker, client):
    main._test_conductor.side_effect = lambda msg, skills=None: ("simple", "conductor-model", None, 0, "deploy")
    queue = [fake_stream([chunk(tool_call=("load_skill", {"name": "deploy"})), chunk(empty=True, usage_obj=DONE)]),
             reply("ok")]
    calls = []
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: calls.append(kw) or queue.pop(0))
    msgs = Conversation(client).prompt("deploy")
    tool = json.loads([m for m in calls[1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert tool["ok"] is False and "already loaded" in tool["error"] and "instructions" not in tool
    assert not any(m["type"] == "system" and m["content"] == "Skill loaded: deploy" for m in msgs)
    system = calls[0]["messages"][0]["content"]
    system = system if isinstance(system, str) else system[0]["text"]
    assert "don't call load_skill for it" in system


def test_drafting_cost_is_separate_from_use(mocker, client):
    sid = sessions.create("", "notes")
    sessions.append_events(sid, [{"type": "prompt", "content": "x"}])
    from types import SimpleNamespace as NS
    resp = NS(choices=[NS(message=NS(content="---\nname: notes\ndescription: d\n---\nx"))],
              usage={"prompt_tokens": 50, "completion_tokens": 20})
    call = mocker.patch("litellm.acompletion", return_value=resp)
    mocker.patch.object(main, "thinking_kwargs", return_value={"reasoning_effort": "low"})
    client.post("/api/skills/draft", json={"session_id": sid, "name": "deploy"}, headers=H)
    assert call.call_args.kwargs["reasoning_effort"] == "low"        # the model's own level, not the costly default
    usage.insert({"skill": "deploy", "role": "simple", "cost": 0.1})
    s = usage.summary()["all_time"]
    assert set(s["by_skill"]) == {"deploy"} and s["by_skill"]["deploy"]["calls"] == 1
    assert s["skill_drafts"]["deploy"]["calls"] == 1
    card = next(x for x in client.get("/api/skills", headers=H).json()["skills"] if x["name"] == "deploy")
    assert card["all_time"]["calls"] == 1 and card["drafting"]["calls"] == 1


def test_prompts_answer_questions_instead_of_doing_the_task():
    """'how do I run the tests?' should be explained, not run (fix 17b)."""
    from optimux_backend.core.agents import get_executor_prompt, get_router_prompt
    router = get_router_prompt("- run-tests: run the project's tests")
    assert "how do I run the tests?" in router and "null" in router
    p = get_executor_prompt("ctx", "/w", active_skill={"name": "run-tests", "folder": "/s", "files": [],
                                                       "instructions": "Run pytest -q."})
    assert "do work only when asked" in p and "read-only commands" in p
    assert "if they only asked about it, answer from it without carrying it out" in p


# ---------- Feature 19: skill modes

MANUAL = "---\nname: notes\ndescription: Write release notes from git log.\n---\n## Steps\n1. git log\n"


def test_modes_listing_and_commands(dirs):
    g, _ = dirs
    write(g, "deploy", SKILL)
    write(g, "notes", MANUAL)
    found = sk.discover()
    assert found["notes"]["mode"] == "manual" and found["deploy"]["mode"] == "auto"   # no mode line = manual
    assert list(sk.listed(found, "auto")) == ["deploy"]
    assert sk.listed(found, "manual") == {} and sk.listed(found, "off") == {}
    assert sk.catalog_tokens({}) == 0 and sk.catalog_tokens(sk.listed(found, "auto")) > 0
    assert sk.parse_command("/notes for v2", found) == ("notes", "for v2")
    assert sk.parse_command("/skill notes", found) == ("notes", "")
    assert sk.parse_command("/NOTES", found) == ("notes", "")
    assert sk.parse_command("/home/me/file is broken", found) == (None, "/home/me/file is broken")
    assert sk.parse_command("/simple notes", found)[0] is None
    assert sk.parse_command("notes please", found)[0] is None
    assert "mode: manual" in sk.template("x") and "mode: manual" in sk.STARTERS["run-tests"]


def test_set_mode_rewrites_front_matter(dirs):
    g, _ = dirs
    write(g, "notes", MANUAL)
    sk.set_mode("global", "notes", "auto", None)
    text = open(os.path.join(g, "notes", "SKILL.md")).read()
    assert "description: Write release notes from git log.\nmode: auto\n" in text and text.endswith("1. git log\n")
    sk.set_mode("global", "notes", "manual", None)
    assert sk.discover()["notes"]["mode"] == "manual"
    assert open(os.path.join(g, "notes", "SKILL.md")).read().count("mode:") == 1
    with pytest.raises(sk.SkillError):
        sk.set_mode("global", "notes", "always", None)


def _system(call):
    s = call["messages"][0]["content"]
    return s if isinstance(s, str) else s[0]["text"]


def test_manual_skill_runs_only_when_asked(mocker, client, dirs):
    g, _ = dirs
    write(g, "notes", MANUAL)
    calls = []
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: calls.append(kw) or reply("Done."))
    conv = Conversation(client)
    conv.prompt("hello")
    assert "- notes:" not in _system(calls[-1]) and "- deploy:" in _system(calls[-1])   # manual isn't listed
    msgs = conv.prompt("/notes for v2.1")
    assert any(m["type"] == "system" and m["content"] == "Skill: notes (you asked for it)" for m in msgs)
    assert "ACTIVE SKILL: notes" in _system(calls[-1])
    assert [m for m in calls[-1]["messages"] if m["role"] == "user"][-1]["content"] == "for v2.1"
    conv.prompt("/skill notes")
    assert [m for m in calls[-1]["messages"] if m["role"] == "user"][-1]["content"] == "Run the notes skill."


def test_global_manual_lists_nothing_and_off_refuses(mocker, client, dirs):
    calls = []
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: calls.append(kw) or reply("Done."))
    mocker.patch.object(main, "skills_mode", return_value="manual")
    conv = Conversation(client)
    conv.prompt("hello")
    assert "Skills (" not in _system(calls[-1])
    assert not any(t["function"]["name"] == "load_skill" for t in calls[-1].get("tools") or [])
    conv.prompt("/deploy")                                 # still runs on request
    assert "ACTIVE SKILL: deploy" in _system(calls[-1])
    mocker.patch.object(main, "skills_mode", return_value="off")
    n = len(calls)
    msgs = conv.prompt("/deploy")
    assert any(m["type"] == "error" and "Skills are turned off" in m["content"] for m in msgs) and len(calls) == n


def test_api_modes(client, dirs):
    g, _ = dirs
    write(g, "notes", MANUAL)
    d = client.get("/api/skills", headers=H).json()
    assert d["skills_mode"] == "auto" and d["listed"] == ["deploy"] and d["catalog_tokens"] > 0
    assert {s["name"]: s["mode"] for s in d["skills"]} == {"deploy": "auto", "notes": "manual"}
    assert client.post("/api/skills/global/notes/mode", json={"mode": "auto"}, headers=H).status_code == 200
    assert client.get("/api/skills", headers=H).json()["listed"] == ["deploy", "notes"]
    assert client.post("/api/skills/global/notes/mode", json={"mode": "x"}, headers=H).status_code == 400
    assert client.post("/api/skills/global/nope/mode", json={"mode": "auto"}, headers=H).status_code == 400
    assert client.post("/api/skills/global/notes/mode", json={"mode": "auto"}).status_code == 401


def test_skills_mode_setting_validation():
    from optimux_backend.core.config_manager import SettingsPayload, save_settings
    from optimux_backend.core.security import ValidationError
    with pytest.raises(ValidationError):
        save_settings(SettingsPayload(skills_mode="sometimes"))
