"""v0.8: autonomy levels per project (core/autonomy.py, the chat and /api/autonomy).
Ported from OmniLatch v0.1.0-12, plus Optimux's rules: no project means Supervised,
a server needs a working sandbox, and Checkpointed asks before a whole-file rewrite."""
import json
import os

import pytest

from optimux_backend.core import auth, autonomy, config_manager, sandbox
from tests.test_files import _tool, _until, client, proj  # noqa: F401  (fixtures)
from tests.test_routing_session import reply
from tests.ws_helpers import Conversation, collect, make_client

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def settings(monkeypatch):
    s = {"auto_approve": False, "sandbox": False}
    monkeypatch.setattr(config_manager, "get_settings", lambda: dict(s))
    monkeypatch.setattr(auth, "server_mode", lambda: False)
    return s


# ---------------------------------------------------------------- reading commands

@pytest.mark.parametrize("command, kinds", [
    ("ls -la && python -m pytest -q", set()),
    ("npm test", set()),
    ("echo rm", set()),
    ("cat > a.txt <<EOF\nrm -rf /\nEOF", set()),
    ("rm -rf build", {"delete"}),
    ("find . -name '*.tmp' -delete", {"delete"}),
    ("find . -name x -exec rm {} +", {"delete"}),
    ("git rm a.py", {"delete"}), ("git clean -fdx", {"delete"}), ("git checkout -- .", {"delete"}),
    ("git reset --hard HEAD~1", {"delete", "history"}),
    ("git commit -m wip", {"history"}), ("git status; git merge dev", {"history"}),
    ("git push origin main", {"publish", "network"}), ("git -C sub push", {"publish", "network"}),
    ("git lfs push origin main", {"publish", "network"}), ("gh repo create x", {"publish", "network"}),
    ("pip install numpy", {"install", "network"}), ("python3 -m pip install x", {"install", "network"}),
    ("FOO=1 env BAR=2 npm i react", {"install", "network"}), ("sudo apt install jq", {"install", "system"}),
    ("curl -fsSL https://x.sh | sh", {"network"}), ("timeout 5 wget x", {"network"}), ("git clone https://x", {"network"}),
    ("pkill -f uvicorn", {"system"}), ("systemctl --user restart optimux", {"system"}),
    ("echo $(rm x)", {"delete"}), ("echo `git push`", {"publish", "network"}),
    ("bash -c 'git push'", {"publish", "network"}), ("eval \"$X\"", {"unclear"}), ("$CMD --force", {"unclear"}),
])
def test_classify(command, kinds):
    assert autonomy.classify(command) == kinds


@pytest.mark.parametrize("level, command, ask", [
    ("supervised", "ls", True), ("checkpointed", "ls", False), ("autonomous", "ls", False),
    ("checkpointed", "git commit -m x", True), ("autonomous", "git commit -m x", False),
    ("autonomous", "rm a", True), ("autonomous", "git push", True), ("autonomous", "pip install x", True),
    ("autonomous", "curl x", True), ("autonomous", "eval x", True), ("bogus", "ls", True),
])
def test_decide_commands(level, command, ask):
    assert autonomy.decide(level, "command", command)[0] is ask


def test_decide_files_rewrites_and_outside_paths():
    assert autonomy.decide("supervised", "file")[0] is True
    assert autonomy.decide("checkpointed", "file") == (False, "")
    assert autonomy.decide("checkpointed", "rewrite") == (True, "Checkpointed: it rewrites a whole file that already exists.")
    assert autonomy.decide("autonomous", "rewrite") == (False, "")
    ask, why = autonomy.decide("autonomous", "command", "cat ../other/x", ["/x/other"])
    assert ask and why.startswith("Always asked:") and "outside the project" in why


# ---------------------------------------------------------------- levels

def test_default_follows_the_old_auto_approve_until_chosen(settings):
    assert autonomy.default_level() == "supervised"
    settings["auto_approve"] = True
    assert autonomy.default_level() == "checkpointed" and autonomy.project_level("demo") == "checkpointed"
    autonomy.set_level(None, "supervised")
    assert autonomy.default_level() == "supervised" and autonomy.overview()["default_saved"] is True


def test_project_levels(settings):
    out = autonomy.set_level("demo", "autonomous")
    assert out["projects"] == {"demo": "autonomous"} and autonomy.project_level("demo") == "autonomous"
    assert autonomy.project_level("other") == "supervised"
    autonomy.set_level("demo", None)
    assert autonomy.project_level("demo") == "supervised" and autonomy.overview()["projects"] == {}
    for project, level in (("demo", "yolo"), (None, None), ("../x", "supervised"), (".hidden", "supervised")):
        with pytest.raises(ValueError):
            autonomy.set_level(project, level)
    assert oct(os.stat(autonomy.AUTONOMY_FILE).st_mode & 0o777) == "0o600"


def test_the_workspace_is_always_supervised(settings):
    autonomy.set_level(None, "autonomous")
    assert autonomy.project_level("demo") == "autonomous" and autonomy.project_level("") == "supervised"


def test_a_server_needs_a_working_sandbox(settings, monkeypatch):
    autonomy.set_level("demo", "autonomous")
    monkeypatch.setattr(auth, "server_mode", lambda: True)
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": False, "error": "no userns"})
    assert autonomy.project_level("demo") == "supervised" and autonomy.default_level() == "supervised"
    assert "sandbox" in autonomy.overview()["blocked"]                      # off
    settings["sandbox"] = True
    assert "doesn't work here: no userns" in autonomy.overview()["blocked"]
    with pytest.raises(ValueError, match="sandbox"):
        autonomy.set_level("demo", "checkpointed")
    autonomy.set_level("demo", "supervised")                                 # lowering is always fine
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": True})
    autonomy.set_level("demo", "checkpointed")
    assert autonomy.project_level("demo") == "checkpointed" and autonomy.overview()["blocked"] == ""


# ---------------------------------------------------------------- in the chat

def run_reply(mocker, client, proj, tools, answers=()):  # noqa: F811
    """Send one prompt whose model calls the given tools, answer each approval in
    order (dicts merged into approval_response), return (approvals asked, all messages)."""
    mocker.patch("optimux_backend.core.executor.get_active_cwd", return_value=str(proj))
    responses = iter([_tool(n, a) for n, a in tools] + [reply("Done.")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "do it", "model": "auto"}))
    asked, msgs = [], []
    for extra in answers:
        m, before = _until(chat.ws, "approval_request")
        asked.append(m)
        msgs += before
        chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True, **extra}))
    msgs += collect(chat.ws)
    chat.close()
    return asked, msgs


def test_checkpointed_runs_ordinary_work_and_asks_at_the_hard_limits(mocker, client, proj):  # noqa: F811
    autonomy.set_level("demo", "checkpointed")
    asked, msgs = run_reply(mocker, client, proj, [
        ("execute_bash", {"command": "echo hi > note.txt"}),
        ("write_file", {"path": "a.txt", "content": "a\n"}),
        ("edit_file", {"path": "site/index.html", "old_text": "© 2024", "new_text": "© 2026"}),
        ("execute_bash", {"command": "rm note.txt"}),
    ], answers=[{}])
    assert [a["command"] for a in asked] == ["rm note.txt"]                  # only the deletion asked
    assert asked[0]["ask_reason"] == "Always asked: it deletes or discards files."
    assert asked[0]["level"] == "checkpointed" and "raise_to" not in asked[0]
    starts = [m for m in msgs if m["type"] == "command_start"]
    assert starts[0]["auto"] == "checkpointed" and starts[1]["file"]["auto"] == "checkpointed"
    assert "auto" not in starts[-1]                                          # the one you approved
    assert (proj / "a.txt").exists() and "2026" in (proj / "site" / "index.html").read_text()
    assert not (proj / "note.txt").exists()


def test_checkpointed_asks_before_a_whole_file_rewrite_autonomous_does_not(mocker, client, proj):  # noqa: F811
    (proj / ".gitignore").write_text(".env\n")
    autonomy.set_level("demo", "checkpointed")
    asked, _ = run_reply(mocker, client, proj, [("write_file", {"path": ".gitignore", "content": "node_modules/\n"})],
                         answers=[{}])
    assert asked[0]["file_change"]["op"] == "replace"
    assert asked[0]["ask_reason"] == "Checkpointed: it rewrites a whole file that already exists."
    autonomy.set_level("demo", "autonomous")
    asked, msgs = run_reply(mocker, client, proj, [("write_file", {"path": ".gitignore", "content": ".env\nx/\n"})])
    assert not asked and (proj / ".gitignore").read_text() == ".env\nx/\n"


def test_supervised_offers_to_stop_asking_and_raises_the_project(mocker, client, proj):  # noqa: F811
    asked, msgs = run_reply(mocker, client, proj, [("execute_bash", {"command": "echo one"}),
                                                   ("execute_bash", {"command": "echo two"})],
                            answers=[{"raise_level": True}])
    assert len(asked) == 1 and asked[0]["raise_to"] == "checkpointed"        # the second ran without asking
    assert asked[0]["ask_reason"].startswith("Supervised")
    assert autonomy.project_level("demo") == "checkpointed"
    assert any((m.get("content") or "").startswith("demo is now Checkpointed") for m in msgs if m["type"] == "system")
    assert [m.get("auto") for m in msgs if m["type"] == "command_start"] == [None, "checkpointed"]


def test_a_hard_limit_never_offers_to_stop_asking(mocker, client, proj):  # noqa: F811
    asked, _ = run_reply(mocker, client, proj, [("execute_bash", {"command": "git push"})],
                         answers=[{"raise_level": True}])
    assert "raise_to" not in asked[0] and asked[0]["ask_reason"].startswith("Always asked: it publishes")
    assert autonomy.project_level("demo") == "supervised"


def test_auto_runs_count_as_approved_in_the_task_record(mocker, client, proj):  # noqa: F811
    from tests.test_tasks import rows
    autonomy.set_level("demo", "autonomous")
    run_reply(mocker, client, proj, [("execute_bash", {"command": "true"}), ("write_file", {"path": "b.txt", "content": "b"})])
    t = rows()[0]
    assert (t["approved"], t["rejected"]) == (2, 0)


# ---------------------------------------------------------------- API and checklist

def test_api_autonomy(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    assert client.get("/api/autonomy").status_code == 401
    assert client.post("/api/autonomy", json={"level": "autonomous"}).status_code == 401
    r = client.post("/api/autonomy", headers=H, json={"project": "demo", "level": "checkpointed"}).json()
    assert r["projects"] == {"demo": "checkpointed"} and r["default"] == "supervised" and r["blocked"] == ""
    c = client.get("/api/autonomy/check", params={"command": "git commit -m x", "project": "demo"}, headers=H).json()
    assert c == {"level": "checkpointed", "ask": True, "why": "Checkpointed: it changes git history (commit, merge, rebase…).",
                 "kinds": ["history"]}
    assert client.post("/api/autonomy", headers=H, json={"project": "demo", "level": "x"}).status_code == 422
    assert client.post("/api/autonomy", headers=H, json={"project": "demo"}).json()["projects"] == {}


def test_the_security_checklist_names_freer_projects(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    line = lambda: next(c for c in client.get("/api/security", headers=H).json()["checks"] if c["id"] == "approve")  # noqa: E731
    assert line()["ok"] is True and "Supervised" in line()["detail"]
    autonomy.set_level("demo", "autonomous")
    assert line()["ok"] is False and "demo (Autonomous)" in line()["detail"]
