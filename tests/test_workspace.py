"""Workspace mode: project tools (list, create, switch, archive), approvals, prompt and tool sets."""
import json
import os
import subprocess

import pytest

import optimux_backend.core.workspace as ws
import optimux_backend.main as main
from optimux_backend.core.agents import get_executor_prompt
from tests.ws_helpers import Conversation, chunk, collect, fake_stream, make_client
from tests.test_routing_session import Recorder, reply

DONE = {"prompt_tokens": 1, "completion_tokens": 1}


@pytest.fixture
def root(mocker, tmp_path, monkeypatch):
    r = tmp_path / "projects"
    (r / "alpha").mkdir(parents=True)
    (r / "beta").mkdir()
    (r / ".hidden").mkdir()
    settings = {"projects_root": str(r), "active_project": ""}
    mocker.patch.object(ws, "get_settings", side_effect=lambda: dict(settings))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "T"); monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "T"); monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.com")
    ws._STATUS_CACHE["value"] = None
    return r, settings


# ---------- module

def test_list_projects(root):
    r, _ = root
    out = ws.list_projects()
    assert [p["name"] for p in out["projects"]] == ["alpha", "beta"] and out["active"] is None


@pytest.mark.parametrize("bad", ["", ".x", "-x", "a/b", "../up", "a b", "x" * 101])
def test_invalid_names(root, bad):
    with pytest.raises(ws.WorkspaceError):
        ws.check_create(bad)


def test_create_basic_with_git(root):
    r, _ = root
    out = ws.create_project("gamma", description="A test")
    assert out["ok"], out["steps"]
    p = r / "gamma"
    assert (p / "README.md").read_text() == "# gamma\n\nA test\n" and (p / ".git").is_dir()
    assert (p / "commit-template.txt").exists() and ".env" in (p / ".gitignore").read_text()
    log = subprocess.run(["git", "log", "--oneline"], cwd=p, capture_output=True, text=True).stdout
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=p, capture_output=True, text=True).stdout.strip()
    assert "Initial commit" in log and branch == "main"
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        ws.check_create("gamma")


def test_create_python_without_git(root):
    r, _ = root
    out = ws.create_project("delta", template="python", git_init=False)
    assert out["ok"] and (r / "delta" / "src").is_dir() and (r / "delta" / "tests" / "__init__.py").exists()
    assert not (r / "delta" / ".git").exists() and "__pycache__/" in (r / "delta" / ".gitignore").read_text()


def test_github_without_gh_keeps_local_project(root, mocker):
    r, _ = root
    mocker.patch.object(ws.shutil, "which", return_value=None)
    out = ws.create_project("eps", github="private", description="d")
    assert not out["ok"] and (r / "eps" / ".git").is_dir()
    assert "isn't installed" in out["steps"][-1]["output"] and "created locally" in out["steps"][-1]["output"]


def test_github_create_command(root, mocker, monkeypatch):
    """gh creates the repository; git pushes over HTTPS with gh as the credential
    helper, so the token is never written to a git config or a command line.
    Only the two calls that talk to GitHub get the token."""
    calls, envs = [], []
    real = ws._run

    def fake_run(cmd, cwd, timeout=60, github=False):
        envs.append((cmd[:3], github, ws._env(github)))
        if cmd[0] == "gh" or "push" in cmd:
            calls.append(cmd)
            return {"step": " ".join(cmd), "ok": True, "output": "gh version 2.60.0" if "--version" in cmd else ""}
        return real(cmd, cwd, timeout, github)

    monkeypatch.setenv("OPTIMUX_GITHUB_TOKEN", "ghp_" + "a" * 36)
    mocker.patch.object(ws.shutil, "which", return_value="/usr/bin/gh")
    mocker.patch.object(ws, "_run", side_effect=fake_run)
    out = ws.create_project("zeta", github="public", description="My app")
    assert out["ok"], out
    assert ["gh", "repo", "create", "zeta", "--public", "--description", "My app", "--source=.", "--remote=origin"] in calls
    push = calls[-1]
    assert push[:5] == ["git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    assert push[-4:] == ["push", "-u", "origin", "main"] and "url.https://github.com/.pushInsteadOf=git@github.com:" in push
    assert not any("ghp_" in " ".join(c) for c in calls)
    with_token = [c for c, gh, e in envs if "GH_TOKEN" in e]
    assert with_token == [["gh", "repo", "create"], ["git", "-c", "credential.helper="]]
    assert all(e["GH_PROMPT_DISABLED"] == "1" and e["GIT_TERMINAL_PROMPT"] == "0" for _, _, e in envs)


def test_own_git_calls_never_run_project_hooks(root, mocker):
    seen = []
    real_run = ws.subprocess.run
    mocker.patch.object(ws.subprocess, "run", side_effect=lambda argv, **kw: seen.append(argv) or real_run(argv, **kw))
    ws.create_project("hooked")
    gits = [a for a in seen if a[0] == "git"]
    assert gits and all(a[1:5] == ws.GIT_SAFE for a in gits)


def test_github_status_is_cached(root, mocker, monkeypatch):
    monkeypatch.delenv("OPTIMUX_GITHUB_TOKEN", raising=False)
    mocker.patch.object(ws.shutil, "which", return_value="/usr/bin/gh")
    run = mocker.patch.object(ws, "_run", return_value={"step": "gh", "ok": True, "output": "gh version 2.4.0"})
    assert ws.github_status()["source"] == "gh"
    assert ["gh", "auth", "status", "--hostname", "github.com"] in [c.args[0] for c in run.call_args_list]
    n = run.call_count
    ws.github_status()
    assert run.call_count == n
    ws.github_status(fresh=True)
    assert run.call_count > n


def test_github_needs_a_sign_in(root, mocker, monkeypatch):
    monkeypatch.delenv("OPTIMUX_GITHUB_TOKEN", raising=False)
    mocker.patch.object(ws.shutil, "which", return_value="/usr/bin/gh")
    mocker.patch.object(ws, "_run", return_value={"step": "gh", "ok": False, "output": "not logged in"})
    st = ws.github_status()
    assert st["gh"] and not st["ready"] and "Dashboard → GitHub" in st["reason"]


def test_github_token_stays_away_from_model_commands(monkeypatch):
    from optimux_backend.core.config_manager import command_env
    from optimux_backend.core.security import scrubbed_env
    monkeypatch.setenv("OPTIMUX_GITHUB_TOKEN", "ghp_secret")
    assert "OPTIMUX_GITHUB_TOKEN" not in scrubbed_env()
    env = command_env({"command_env": []})
    assert "ghp_secret" not in env.values() and "GH_TOKEN" not in env


def test_git_identity(monkeypatch):
    from optimux_backend.core.config_manager import check_git_identity, command_env
    from optimux_backend.core.security import ValidationError
    assert check_git_identity(" Ann ", "ann@example.com") == ("Ann", "ann@example.com")
    assert check_git_identity("", "") == ("", "")
    for name, email in (("Ann", ""), ("", "a@b.c"), ("Ann\nX", "a@b.c"), ("Ann", "not-an-email"), ("<Ann>", "a@b.c"),
                        ("Ann", "a\x00b@c.d"), ("Ann\x7f", "a@b.c")):
        with pytest.raises(ValidationError):
            check_git_identity(name, email)
    env = command_env({"command_env": [], "git_name": "Ann", "git_email": "ann@example.com"})
    assert env["GIT_AUTHOR_EMAIL"] == env["GIT_COMMITTER_EMAIL"] == "ann@example.com"
    assert "GIT_AUTHOR_NAME" not in command_env({"command_env": []}, base={"PATH": "/bin"})


def test_normalize_create():
    f = ws.normalize_create({"name": " app ", "github": "private", "git_init": False, "description": "two\nlines  here"})
    assert f == {"name": "app", "template": "basic", "git_init": True, "github": "private",
                 "description": "two lines here", "switch": True}
    assert ws.normalize_create({"name": "x", "switch": False})["switch"] is False
    with pytest.raises(ws.WorkspaceError):
        ws.normalize_create({"name": "x", "description": "d" * 351})


class FakeResponse:
    def __init__(self, data, scopes):
        self.data, self.headers = data, ({"X-OAuth-Scopes": scopes} if scopes is not None else {})

    def read(self):
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("scopes,warning", [("repo, workflow", ""), ("public_repo", "public repositories only"),
                                            ("gist", "missing the 'repo' scope"), (None, "")])
def test_check_github_token(mocker, scopes, warning):
    seen = {}

    def fake_open(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        return FakeResponse({"login": "octo", "name": "Octo Cat", "id": 42}, scopes)

    mocker.patch.object(ws, "_github_get", side_effect=fake_open)
    info = ws.check_github_token("ghp_" + "b" * 36)
    assert info["login"] == "octo" and seen["auth"] == "Bearer ghp_" + "b" * 36
    assert (warning in info["warning"]) if warning else info["warning"] == ""
    assert ("Fine-grained" in info["hint"]) == (scopes is None)


def test_check_github_token_refused(mocker):
    import urllib.error
    with pytest.raises(ws.GitHubError, match="look like"):
        ws.check_github_token("hello world")
    mocker.patch.object(ws, "_github_get", side_effect=urllib.error.HTTPError("u", 401, "Unauthorized", {}, None))
    with pytest.raises(ws.GitHubError, match="refused"):
        ws.check_github_token("ghp_" + "c" * 36)
    mocker.patch.object(ws, "_github_get", side_effect=urllib.error.URLError("no route"))
    with pytest.raises(ws.GitHubError, match="proxy"):
        ws.check_github_token("ghp_" + "c" * 36)


@pytest.mark.parametrize("has_identity", [False, True])
def test_connect_github_sets_commit_identity_only_when_missing(mocker, root, has_identity):
    _, settings = root
    mocker.patch.object(ws, "check_github_token", return_value={"login": "octo", "name": "", "id": 42, "scopes": ["repo"], "warning": "", "hint": ""})
    saved = mocker.patch.object(ws, "save_api_key_to_env")
    upd = mocker.patch.object(ws, "set_github_fields")
    mocker.patch.object(ws, "git_has_identity", return_value=has_identity)
    out = ws.connect_github(" ghp_" + "d" * 36 + " ")
    assert saved.call_args[0][0].api_key == "ghp_" + "d" * 36
    assert saved.call_args[0][0].env_key == "OPTIMUX_GITHUB_TOKEN"
    if has_identity:
        upd.assert_called_once_with(github_login="octo")
    else:
        upd.assert_called_once_with(github_login="octo", git_name="octo", git_email="42+octo@users.noreply.github.com")
    assert out["identity_set"] is (not has_identity)


def test_github_requires_git_and_valid_visibility(root):
    with pytest.raises(ws.WorkspaceError):
        ws.check_create("x", git_init=False, github="private")
    with pytest.raises(ws.WorkspaceError):
        ws.check_create("x", github="internal")


def test_switch_and_archive_checks(root):
    r, _ = root
    assert ws.check_switch("") == "" and ws.check_switch("alpha") == "alpha"
    with pytest.raises(ws.WorkspaceError):
        ws.check_switch("nope")
    out = ws.archive_project("beta")
    assert not (r / "beta").exists() and os.path.isdir(out["archived_to"])
    assert ws.list_projects()["archived"] and [p["name"] for p in ws.list_projects()["projects"]] == ["alpha"]


def test_tool_sets_and_prompt():
    assert {t["function"]["name"] for t in ws.tools_for("")} == {"list_projects", "create_project", "switch_project", "archive_project"}
    # Inside a project: projects can still be created (always with approval); archiving stays at the Workspace
    assert {t["function"]["name"] for t in ws.tools_for("alpha")} == {"list_projects", "create_project", "switch_project"}
    assert "workspace mode" in get_executor_prompt("", "/p", workspace=True)
    inside = get_executor_prompt("", "/p/alpha")
    assert "switch_project" in inside and "create_project" in inside and "workspace mode" not in inside
    assert "never mkdir, git init" in inside and "sandbox" not in inside
    boxed = get_executor_prompt("", "/p/alpha", sandboxed=True)
    assert "only /p/alpha can be changed" in boxed and "Read-only file system" in boxed


# ---------- through the WebSocket

@pytest.fixture
def client(mocker, tmp_path, root):
    c = make_client(mocker, tmp_path)
    r, settings = root
    mocker.patch.object(main, "active_project", side_effect=lambda: settings["active_project"])
    mocker.patch.object(main, "get_settings", side_effect=lambda: dict(settings))
    mocker.patch.object(main, "save_settings", side_effect=lambda p: settings.update(active_project=p.active_project))
    return c


def tool_turn(name, args, then="Done."):
    return Recorder(fake_stream([chunk(tool_call=(name, args)), chunk(empty=True, usage_obj=DONE)]), reply(then))


def run_with_approval(chat, prompt, approve, form=None, answers=None):
    """Send a prompt and answer approval requests: `answers` is a list of
    (approve, form) pairs for successive requests (default: one answer)."""
    answers = list(answers or [(approve, form)])
    chat.ws.send_text(json.dumps({"prompt": prompt, "model": "auto"}))
    seen = []
    while True:
        m = json.loads(chat.ws.receive_text())
        seen.append(m)
        if m["type"] == "approval_request":
            ok, f = answers.pop(0)
            reply_msg = {"type": "approval_response", "approved": ok}
            if f is not None:
                reply_msg["form"] = f
            chat.ws.send_text(json.dumps(reply_msg))
            if not answers:
                break
        if m["type"] == "done":
            return seen
    return seen + collect(chat.ws)


def tool_result(rec):
    return json.loads([m for m in rec.calls[1]["messages"] if m["role"] == "tool"][-1]["content"])


def test_create_via_chat_shows_a_form_even_with_auto_approve(mocker, client, root):
    r, settings = root
    rec = tool_turn("create_project", {"name": "newapp", "template": "python"})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    msgs = run_with_approval(chat, "create a python project called newapp", approve=True)
    req = next(m for m in msgs if m["type"] == "approval_request")
    assert req["always_ask"] is True and req["action"] == "create_project"
    assert req["form"]["name"] == "newapp" and req["form"]["template"] == "python" and req["form"]["switch"] is True
    assert req["error"] == "" and "ready" in req["github"]
    assert (r / "newapp" / "src").is_dir()
    assert any(m["type"] == "projects_changed" for m in msgs)
    created = next(m for m in msgs if m["type"] == "project_created")
    assert created["project"] == "newapp" and created["ok"] is True
    # Switched (the default), and the model is told the chat moves there
    assert settings["active_project"] == "newapp"
    assert any(m["type"] == "project_switched" and m["project"] == "newapp" for m in msgs)
    result = tool_result(rec)
    assert result["ok"] is True and "switched" in result["note"]
    chat.close()


def test_the_form_values_win_over_the_model(mocker, client, root):
    r, settings = root
    rec = tool_turn("create_project", {"name": "guess", "template": "python"})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    form = {"name": "chosen", "template": "basic", "git_init": False, "github": "", "description": "Mine", "switch": False}
    run_with_approval(chat, "make me a project", approve=True, form=form)
    assert (r / "chosen").is_dir() and not (r / "guess").exists() and not (r / "chosen" / ".git").exists()
    assert "Mine" in (r / "chosen" / "README.md").read_text()
    assert settings["active_project"] == ""
    result = tool_result(rec)
    assert result["form"]["name"] == "chosen" and "don't offer to switch" in result["note"]
    chat.close()


def test_create_cancelled(mocker, client, root):
    r, _ = root
    rec = tool_turn("create_project", {"name": "nope"})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    run_with_approval(chat, "create nope", approve=False)
    assert not (r / "nope").exists()
    assert tool_result(rec)["declined"]
    chat.close()


def test_a_bad_name_is_fixed_in_the_form(mocker, client, root):
    """An existing or invalid name doesn't fail the request: the form shows the
    problem and asks again until the values work (or the user cancels)."""
    r, _ = root
    rec = tool_turn("create_project", {"name": "alpha"})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    base = {"template": "basic", "git_init": False, "github": "", "description": "", "switch": False}
    msgs = run_with_approval(chat, "create alpha", approve=True,
                             answers=[(True, {**base, "name": "bad name"}), (True, {**base, "name": "alpha2"})])
    reqs = [m for m in msgs if m["type"] == "approval_request"]
    assert "already exists" in reqs[0]["error"] and "valid project name" in reqs[1]["error"]
    assert (r / "alpha2").is_dir() and tool_result(rec)["ok"]
    chat.close()


def test_github_in_the_form_needs_github_set_up(mocker, client, root):
    r, _ = root
    mocker.patch.object(ws, "github_status", return_value={"ready": False, "reason": "GitHub isn't set up: add a token in Dashboard → GitHub."})
    rec = tool_turn("create_project", {"name": "gh1", "github": "private"})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    msgs = run_with_approval(chat, "create gh1 on github", approve=True,
                             answers=[(True, None), (False, None)])
    reqs = [m for m in msgs if m["type"] == "approval_request"]
    assert "isn't set up" in reqs[0]["error"] and len(reqs) == 2
    assert not (r / "gh1").exists()
    chat.close()


def test_switch_via_chat_updates_settings_and_ui(mocker, client, root):
    _, settings = root
    mocker.patch("litellm.acompletion", side_effect=tool_turn("switch_project", {"name": "alpha"}))
    msgs = Conversation(client).prompt("let's work on alpha")
    assert settings["active_project"] == "alpha"
    assert any(m["type"] == "project_switched" and m["project"] == "alpha" for m in msgs)
    assert not any(m["type"] == "approval_request" for m in msgs)


def test_archive_active_project_returns_to_root(mocker, client, root):
    r, settings = root
    settings["active_project"] = "alpha"
    mocker.patch("litellm.acompletion", side_effect=tool_turn("archive_project", {"name": "alpha"}))
    chat = Conversation(client)
    msgs = run_with_approval(chat, "archive alpha", approve=True)
    assert not (r / "alpha").exists() and settings["active_project"] == ""
    assert any(m["type"] == "project_switched" and m["project"] == "" for m in msgs)
    chat.close()


def test_keyword_intercept_is_gone(mocker, client):
    mocker.patch("litellm.acompletion", side_effect=Recorder(reply("Sure, what should it be called?")))
    msgs = Conversation(client).prompt("create a new project")
    assert not any("I need all the details" in (m.get("content") or "") for m in msgs)
    assert any(m["type"] == "token" for m in msgs)


def test_create_from_inside_a_project(mocker, client, root):
    """Inside a project the model creates projects with the tool (not shell commands);
    staying in the current project is the user's choice in the form."""
    r, settings = root
    settings["active_project"] = "alpha"
    rec = tool_turn("create_project", {"name": "fromalpha"})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    form = {"name": "fromalpha", "template": "basic", "git_init": False, "github": "", "description": "", "switch": False}
    msgs = run_with_approval(chat, "create a project called fromalpha", approve=True, form=form)
    req = next(m for m in msgs if m["type"] == "approval_request")
    assert req["always_ask"] and req["active_project"] == "alpha"
    assert (r / "fromalpha").is_dir() and settings["active_project"] == "alpha"
    result = tool_result(rec)
    assert result["ok"] and "alpha" in result["note"]
    chat.close()


# ---------- the New project button and the GitHub settings (REST)

AUTH = {"Authorization": "Bearer test-token"}

def test_create_project_api(client, root):
    r, settings = root
    res = client.post("/api/projects", headers=AUTH, json={"name": "viaform", "template": "python", "git_init": False})
    assert res.status_code == 200, res.text
    assert (r / "viaform" / "src").is_dir() and res.json()["ok"] and "✓" in res.json()["report"]
    assert settings["active_project"] == ""          # switching is the page's job (it asks the server like a manual switch)
    assert client.post("/api/projects", headers=AUTH, json={"name": "viaform"}).status_code == 422
    assert client.post("/api/projects", headers=AUTH, json={"name": "../x"}).status_code == 422
    assert client.post("/api/projects", headers=AUTH, json={"name": "y", "template": "rust"}).status_code == 422


def test_github_api(mocker, client, monkeypatch):
    mocker.patch.object(ws, "github_status", return_value={"gh": True, "ready": False, "reason": "r", "token_saved": False})
    info = client.get("/api/github", headers=AUTH).json()
    assert info["gh"] is True and "git_name" in info and "token" not in json.dumps(info).lower().replace("token_saved", "")
    connect = mocker.patch.object(ws, "connect_github", return_value={"login": "octo", "name": "", "warning": "", "hint": "", "identity_set": True})
    res = client.post("/api/github", headers=AUTH, json={"token": "ghp_" + "e" * 36})
    assert res.status_code == 200 and res.json()["login"] == "octo" and "ghp_" not in res.text
    connect.side_effect = ws.GitHubError("GitHub refused the token: it's wrong, expired or revoked.")
    res = client.post("/api/github", headers=AUTH, json={"token": "ghp_" + "e" * 36})
    assert res.status_code == 422 and "refused" in res.json()["detail"]
    monkeypatch.delenv("OPTIMUX_GITHUB_TOKEN", raising=False)
    assert client.post("/api/github/check", headers=AUTH).status_code == 422
    off = mocker.patch.object(ws, "disconnect_github")
    assert client.delete("/api/github", headers=AUTH).status_code == 200 and off.called


def test_connect_github_with_an_unusable_display_name(mocker, root):
    mocker.patch.object(ws, "check_github_token", return_value={"login": "bob", "name": "Bob <the builder>", "id": 7, "scopes": None, "warning": "", "hint": "h"})
    mocker.patch.object(ws, "save_api_key_to_env")
    upd = mocker.patch.object(ws, "set_github_fields")
    mocker.patch.object(ws, "git_has_identity", return_value=False)
    ws.connect_github("ghp_" + "f" * 36)
    assert upd.call_args.kwargs["git_name"] == "bob"


def test_github_fields_are_saved_on_their_own(tmp_path, mocker):
    """A Dashboard save with stale values keeps the GitHub fields; they change only
    through their own endpoint, which validates only them."""
    import optimux_backend.core.config_manager as cm
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"projects_root": "/does/not/exist", "git_name": "Old", "git_email": "old@x.y"}))
    mocker.patch.object(cm, "SETTINGS_FILE", str(f))
    cm.set_github_fields(git_name="Ann", git_email="ann@example.com", github_login="ann")   # despite the bad root
    data = json.loads(f.read_text())
    assert data["git_name"] == "Ann" and data["github_login"] == "ann" and data["projects_root"] == "/does/not/exist"
    with pytest.raises(cm.ValidationError):
        cm.set_github_fields(git_name="Ann", git_email="")
    with pytest.raises(cm.ValidationError):
        cm.set_github_fields(projects_root="/")


def test_settings_save_keeps_github_fields(mocker, client, tmp_path):
    import optimux_backend.core.config_manager as cm
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"git_name": "Ann", "git_email": "ann@example.com", "github_login": "ann"}))
    mocker.patch.object(cm, "SETTINGS_FILE", str(f))
    mocker.patch.object(main, "_read_settings", side_effect=cm._read_settings)
    saved = mocker.patch.object(main, "save_settings")
    assert client.post("/api/settings", headers=AUTH, json={"git_name": "", "git_email": "", "github_login": ""}).status_code == 200
    p = saved.call_args[0][0]
    assert (p.git_name, p.git_email, p.github_login) == ("Ann", "ann@example.com", "ann")
    ident = mocker.patch.object(main, "set_github_fields")
    assert client.post("/api/github/identity", headers=AUTH, json={"git_name": "B", "git_email": "b@c.d"}).status_code == 200
    ident.assert_called_once_with(git_name="B", git_email="b@c.d")


def test_a_refused_form_is_shown_again_as_typed(mocker, client, root):
    r, _ = root
    rec = tool_turn("create_project", {"name": "x" * 3, "description": "d" * 400})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    typed = {"name": "mine", "template": "python", "git_init": False, "github": "", "description": "e" * 400, "switch": False}
    msgs = run_with_approval(chat, "make it", approve=True, answers=[(True, typed), (False, None)])
    reqs = [m for m in msgs if m["type"] == "approval_request"]
    assert "too long" in reqs[0]["error"] and reqs[0]["form"]["description"] == "d" * 400   # the model's values, kept
    assert reqs[1]["form"]["name"] == "mine" and reqs[1]["form"]["template"] == "python" and "too long" in reqs[1]["error"]
    assert not (r / "mine").exists()
    chat.close()


def test_bad_tool_arguments_still_open_the_form(mocker, client, root):
    rec = tool_turn("create_project", ["not", "a", "dict"])
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    msgs = run_with_approval(chat, "make one", approve=False)
    assert next(m for m in msgs if m["type"] == "approval_request")["form"]["name"] == ""
    chat.close()


def test_create_failing_after_the_checks(mocker, client, root):
    mocker.patch.object(ws, "create_project", side_effect=ws.WorkspaceError("Couldn't create /x: File exists."))
    rec = tool_turn("create_project", {"name": "race"})
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    msgs = run_with_approval(chat, "make race", approve=True)
    out = next(m for m in msgs if m["type"] == "command_output")
    assert out["exit_code"] == 1 and "File exists" in out["output"] and "File exists" in tool_result(rec)["error"]
    chat.close()
    res = client.post("/api/projects", headers=AUTH, json={"name": "race2"})
    assert res.status_code == 422 and "File exists" in res.json()["detail"]


# ---------- fix25a: archive, restore and delete from the Dashboard

def _records(name, tmp_path, mocker):
    """A chat, two model calls, a memory version, a memory file and a budget for `name`."""
    from optimux_backend.core import budgets, project_records, sessions, store, usage
    mocker.patch.object(budgets, "BUDGETS_FILE", str(tmp_path / "budgets.json"))
    mem = tmp_path / "memprojects"
    mem.mkdir(exist_ok=True)
    mocker.patch.object(project_records, "PROJECTS_DIR", str(mem))
    (mem / f"{name}.md").write_text("# memory")
    sid = sessions.create(name, "hello")
    sessions.append_events(sid, [{"type": "token", "content": "hi"}])
    usage.insert({"project": name, "role": "simple", "model": "m", "cost": 0.5})
    usage.insert({"project": "other", "role": "simple", "model": "m", "cost": 0.25})
    conn = store.connect()
    with conn:
        conn.execute("INSERT INTO memory_versions (project, ts, source, before, after) VALUES (?, ?, ?, ?, ?)", (name, 1.0, "user", "a", "b"))
    conn.close()
    budgets.save_budget(name, 10, "block")
    return mem


def test_archive_restore_cycle(root):
    r, _ = root
    ws.archive_project("alpha")
    arch = ws.list_archived()
    assert [a["name"] for a in arch] == ["alpha"] and arch[0]["archived"] > 0
    out = ws.restore_archived(arch[0]["folder"])
    assert (r / "alpha").is_dir() and out["project"] == "alpha" and ws.list_archived() == []
    ws.archive_project("alpha")
    (r / "alpha").mkdir()
    with pytest.raises(ws.WorkspaceError, match="exists already"):
        ws.restore_archived(ws.list_archived()[0]["folder"])


@pytest.mark.parametrize("typed,ok", [("yes I want to delete beta", True), ("  Yes  i want to DELETE   beta ", True),
                                      ("yes I want to delete Beta", False), ("yes delete beta", False), ("beta", False), ("", False)])
def test_delete_phrase(typed, ok):
    assert ws.phrase_matches(typed, "beta") is ok


def test_delete_keeping_records(root, tmp_path, mocker):
    from optimux_backend.core import budgets, project_records, sessions, usage
    r, _ = root
    mem = _records("beta", tmp_path, mocker)
    ws.archive_project("beta")
    folder = ws.list_archived()[0]["folder"]
    with pytest.raises(ws.WorkspaceError, match="yes I want to delete beta"):
        ws.delete_archived(folder, "yes")
    out = ws.delete_archived(folder, "yes I want to delete beta", keep_records=True)
    assert out["records_kept"] and not (r / ".archive" / folder).exists()
    assert "beta" in project_records.deleted_projects()
    assert sessions.project_counts()["beta"]["chats"] == 1 and usage.project_activity()["beta"]["calls"] == 1
    assert (mem / "beta.md").exists() and "beta" not in budgets.get_budgets()   # nothing can spend there
    # The name used again: its records belong to the new project
    ws.create_project("beta", git_init=False)
    assert "beta" not in project_records.deleted_projects()


def test_delete_forgetting_records(root, tmp_path, mocker):
    from optimux_backend.core import budgets, project_records, sessions, store, usage
    mem = _records("beta", tmp_path, mocker)
    ws.archive_project("beta")
    folder = ws.list_archived()[0]["folder"]
    out = ws.delete_archived(folder, "yes I want to delete beta", keep_records=False)
    assert out["removed"]["chats"] == 1 and out["removed"]["calls"] == 1 and out["removed"]["memory_versions"] == 1
    assert "beta" not in sessions.project_counts() and "beta" not in usage.project_activity()
    assert usage.project_activity()["other"]["calls"] == 1                     # others untouched
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) FROM session_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memory_versions").fetchone()[0] == 0
    conn.close()
    assert not (mem / "beta.md").exists() and "beta" not in budgets.get_budgets()
    assert "beta" not in project_records.deleted_projects()


def test_forgetting_records_refused_while_another_copy_exists(root, tmp_path, mocker):
    r, _ = root
    _records("beta", tmp_path, mocker)
    ws.archive_project("beta")
    folder = ws.list_archived()[0]["folder"]
    (r / "beta").mkdir()                                  # a new beta in the Workspace
    with pytest.raises(ws.WorkspaceError, match="shares these chats"):
        ws.delete_archived(folder, "yes I want to delete beta", keep_records=False)
    out = ws.delete_archived(folder, "yes I want to delete beta", keep_records=True)
    from optimux_backend.core import project_records
    assert out["ok"] and "beta" not in project_records.deleted_projects()   # the live beta isn't "deleted"


def test_delete_stays_inside_the_archive(root, tmp_path):
    r, _ = root
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    (r / ".archive").mkdir(exist_ok=True)
    os.symlink(outside, r / ".archive" / "evil-20260101-000000")
    for folder in ("evil-20260101-000000", "../precious", "..", "alpha", "", ".archive"):
        with pytest.raises(ws.WorkspaceError):
            ws.delete_archived(folder, "yes I want to delete evil")
    assert (outside / "keep.txt").exists() and (r / "alpha").is_dir()
    # A symlink inside an archived folder is removed, not followed
    ws.archive_project("beta")
    folder = ws.list_archived()[0]["folder"]
    os.symlink(outside, r / ".archive" / folder / "link")
    ws.delete_archived(folder, "yes I want to delete beta")
    assert (outside / "keep.txt").exists()


def test_no_model_tool_restores_or_deletes():
    names = {t["function"]["name"] for t in ws.WORKSPACE_TOOLS}
    assert names == {"list_projects", "create_project", "switch_project", "archive_project"}


def test_archive_restore_delete_api(mocker, client, root, tmp_path):
    r, settings = root
    _records("alpha", tmp_path, mocker)
    settings["active_project"] = "alpha"
    res = client.post("/api/projects/alpha/archive", headers=AUTH)
    assert res.status_code == 200 and res.json()["was_active"] and settings["active_project"] == ""
    arch = client.get("/api/projects/archived", headers=AUTH).json()
    assert arch[0]["name"] == "alpha" and arch[0]["phrase"] == "yes I want to delete alpha"
    assert arch[0]["records"]["chats"] == 1
    overview = {p["project"]: p for p in client.get("/api/projects/overview", headers=AUTH).json()}
    assert overview["alpha"]["archived"] and not overview["alpha"]["deleted"]
    folder = arch[0]["folder"]
    bad = client.post(f"/api/projects/archived/{folder}/delete", headers=AUTH, json={"confirm": "yes"})
    assert bad.status_code == 422 and "yes I want to delete alpha" in bad.json()["detail"]
    ok = client.post(f"/api/projects/archived/{folder}/delete", headers=AUTH,
                     json={"confirm": "yes I want to delete alpha", "keep_records": True})
    assert ok.status_code == 200 and not (r / ".archive" / folder).exists()
    assert "alpha" in client.get("/api/projects/deleted", headers=AUTH).json()
    overview = {p["project"]: p for p in client.get("/api/projects/overview", headers=AUTH).json()}
    assert overview["alpha"]["deleted"] and not overview["alpha"]["exists"]
    client.post("/api/projects/beta/archive", headers=AUTH)
    folder = client.get("/api/projects/archived", headers=AUTH).json()[0]["folder"]
    assert client.post(f"/api/projects/archived/{folder}/restore", headers=AUTH).status_code == 200 and (r / "beta").is_dir()
    assert client.post("/api/projects/archived/..%2F..%2Fetc/restore", headers=AUTH).status_code in (404, 422)


@pytest.mark.parametrize("remote,url", [("https://github.com/me/app.git", "https://github.com/me/app/settings"),
                                        ("git@github.com:me/app.git", "https://github.com/me/app/settings"),
                                        ("https://github.com/me/my.app", "https://github.com/me/my.app/settings"),
                                        ("https://gitlab.com/me/app.git", None), (None, None),
                                        ("https://github.com/me/app/../../evil", None)])
def test_github_settings_link(remote, url):
    assert ws.github_settings_url(remote) == url


def test_a_linked_archive_folder_is_refused(root, tmp_path):
    """A model-run command could replace .archive with a link to elsewhere."""
    r, _ = root
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "important-20250101-000000").mkdir(parents=True)
    os.symlink(elsewhere, r / ".archive")
    for call in (ws.list_archived, lambda: ws.delete_archived("important-20250101-000000", "yes I want to delete important"),
                 lambda: ws.restore_archived("important-20250101-000000"), lambda: ws.archive_project("alpha")):
        with pytest.raises(ws.WorkspaceError, match="isn't a plain folder"):
            call()
    assert (elsewhere / "important-20250101-000000").is_dir() and (r / "alpha").is_dir()


def test_odd_folders_in_the_archive_are_skipped(root):
    r, _ = root
    (r / ".archive" / "foo-99999999-999999").mkdir(parents=True)
    (r / ".archive" / "notes").mkdir()
    ws.archive_project("beta")
    assert [a["name"] for a in ws.list_archived()] == ["beta"]


def test_archiving_twice_in_one_second_keeps_both(root):
    r, _ = root
    ws.archive_project("alpha")
    (r / "alpha").mkdir()
    ws.archive_project("alpha")
    assert len([a for a in ws.list_archived() if a["name"] == "alpha"]) == 2


def test_failed_delete_is_reported(root, mocker):
    ws.archive_project("beta")
    folder = ws.list_archived()[0]["folder"]
    mocker.patch.object(ws, "_rmtree_at", side_effect=PermissionError(13, "Permission denied"))
    with pytest.raises(ws.WorkspaceError, match="Permission denied"):
        ws.delete_archived(folder, "yes I want to delete beta")


def test_a_deleted_name_with_a_folder_again_is_live(root, tmp_path, mocker):
    from optimux_backend.core import project_records
    r, _ = root
    project_records.mark_deleted("gamma")
    assert "gamma" in project_records.deleted_projects(str(r))
    (r / "gamma").mkdir()                        # cloned or made by hand
    assert "gamma" not in project_records.deleted_projects(str(r))
    assert "gamma" not in project_records.deleted_projects()


def test_archive_refused_while_a_request_runs_there(mocker, client, root):
    r, _ = root
    main.BUSY_PROJECTS["alpha"] = 1
    try:
        res = client.post("/api/projects/alpha/archive", headers=AUTH)
        assert res.status_code == 422 and "running" in res.json()["detail"] and (r / "alpha").is_dir()
    finally:
        main.BUSY_PROJECTS.pop("alpha", None)


def test_open_pages_hear_about_an_archive(mocker, client, root):
    sock = mocker.MagicMock()
    sent = []

    async def send_text(t):
        sent.append(json.loads(t))
    sock.send_text = send_text
    main.LIVE_SOCKETS["test-login"] = {sock}
    try:
        assert client.post("/api/projects/beta/archive", headers=AUTH).status_code == 200
    finally:
        main.LIVE_SOCKETS.pop("test-login", None)
    assert {"type": "project_archived", "project": "beta"} in sent


def test_busy_projects_are_counted_during_a_request(mocker, client, root):
    _, settings = root
    settings["active_project"] = "alpha"
    seen = {}

    def reply_and_peek(*a, **k):
        seen.update(main.BUSY_PROJECTS)
        return fake_stream([chunk("ok"), chunk(empty=True, usage_obj=DONE)])
    mocker.patch("litellm.acompletion", side_effect=reply_and_peek)
    Conversation(client).prompt("hi")
    assert seen.get("alpha") == 1 and "alpha" not in main.BUSY_PROJECTS


def test_delete_removes_a_deep_tree_with_links_and_odd_files(root, tmp_path):
    """The fd-based delete (works on Python 3.10): nested folders, hidden files,
    a read-only file, links to files and folders outside, which survive."""
    r, _ = root
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    proj = r / "beta"
    (proj / "a" / "b" / "c").mkdir(parents=True)
    (proj / "a" / "b" / "c" / "deep.txt").write_text("d")
    (proj / ".git" / "objects").mkdir(parents=True)
    ro = proj / "readonly.txt"
    ro.write_text("r")
    ro.chmod(0o444)
    os.symlink(outside, proj / "a" / "dirlink")
    os.symlink(outside / "keep.txt", proj / "filelink")
    os.symlink(proj / "missing", proj / "dangling")
    ws.archive_project("beta")
    folder = ws.list_archived()[0]["folder"]
    ws.delete_archived(folder, "yes I want to delete beta")
    assert not (r / ".archive" / folder).exists() and (outside / "keep.txt").read_text() == "x"


# ---------- fix25c: does the GitHub repository still exist?

def _http_error(code, headers=None):
    import urllib.error
    return urllib.error.HTTPError("u", code, "x", headers or {}, None)


@pytest.mark.parametrize("token,result,state", [
    ("ghp_" + "t" * 36, "ok", "exists"),
    ("ghp_" + "t" * 36, 404, "missing"),
    ("", "ok", "exists"),
    ("", 404, "deleted_or_private"),         # anonymously, a private repo looks deleted
    ("", 403, "unknown"),
    ("", "limited", "unknown"),
    ("", "offline", "unknown"),
])
def test_github_repo_state(root, mocker, monkeypatch, token, result, state):
    import urllib.error
    if token:
        monkeypatch.setenv("OPTIMUX_GITHUB_TOKEN", token)
    else:
        monkeypatch.delenv("OPTIMUX_GITHUB_TOKEN", raising=False)
        mocker.patch.object(ws, "github_status", return_value={"source": None})
    seen = {}

    def fake_open(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["url"] = req.full_url
        if result == "ok":
            return FakeResponse({}, None)
        if result == "offline":
            raise urllib.error.URLError("no route")
        if result == "limited":
            raise _http_error(403, {"X-RateLimit-Remaining": "0"})
        raise _http_error(result)
    mocker.patch.object(ws, "_github_get", side_effect=fake_open)
    out = ws.github_repo_state("git@github.com:me/app.git")
    assert out["state"] == state and out["repo"] == "me/app"
    assert seen["url"] == "https://api.github.com/repos/me/app"
    assert (seen["auth"] == f"Bearer {token}") if token else seen["auth"] is None
    if result == "limited":
        assert "limit" in out["reason"]
    if result == 403:
        assert out["reason"] == "GitHub answered 403."


@pytest.mark.parametrize("output,ok,state", [("", True, "exists"), ("gh: Not Found (HTTP 404)", False, "missing"),
                                              ("error connecting to api.github.com", False, "unknown")])
def test_github_repo_state_with_gh_sign_in(root, mocker, monkeypatch, output, ok, state):
    monkeypatch.delenv("OPTIMUX_GITHUB_TOKEN", raising=False)
    mocker.patch.object(ws, "github_status", return_value={"source": "gh"})
    run = mocker.patch.object(ws, "_run", return_value={"step": "gh", "ok": ok, "output": output})
    opened = mocker.patch.object(ws, "_github_get")
    assert ws.github_repo_state("https://github.com/me/app.git")["state"] == state
    assert run.call_args.args[0] == ["gh", "api", "repos/me/app", "--silent"] and run.call_args.kwargs["github"] is True
    assert not opened.called


def test_github_repo_state_needs_a_github_remote(mocker):
    opened = mocker.patch.object(ws, "_github_get")
    assert ws.github_repo_state("https://gitlab.com/me/app.git")["state"] == "unknown"
    assert ws.github_repo_state(None)["state"] == "unknown" and not opened.called


def test_archived_github_state_api(mocker, client, root):
    r, _ = root
    ws.archive_project("beta")
    folder = ws.list_archived()[0]["folder"]
    mocker.patch.object(ws, "_git_remote", return_value="https://github.com/me/beta.git")
    mocker.patch.object(ws, "github_repo_state", return_value={"state": "missing", "repo": "me/beta", "reason": ""})
    d = client.get(f"/api/projects/archived/{folder}/github", headers=AUTH).json()
    assert d["state"] == "missing" and d["settings"] == "https://github.com/me/beta/settings"
    assert client.get("/api/projects/archived/nope/github", headers=AUTH).status_code == 422


def test_delete_dialog_mentions_github_only_for_github_projects():
    """fix25d: no "the GitHub repository isn't touched" for a project without one."""
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")).read()
    dialog = html[html.index("fix25a: delete an archived project permanently"):html.index("Feature 25: New project dialog")]
    box = dialog[dialog.index("GitHub remote (fix25d)"):]
    assert box.split("\n")[1].strip().startswith('<div v-if="deleteBox.item.github_settings"')
    assert dialog[:dialog.index("GitHub remote (fix25d)")].count("GitHub") == 0


# ---------- fix25e: an archived project's budget is paused

def test_archived_budget_is_paused(mocker, client, root, tmp_path):
    from optimux_backend.core import budgets
    r, _ = root
    mocker.patch.object(budgets, "BUDGETS_FILE", str(tmp_path / "budgets.json"))
    budgets.save_budget("alpha", 10, "block")
    budgets.save_budget("beta", 5, "warn")
    ws.archive_project("beta")
    got = client.get("/api/budgets", headers=AUTH).json()
    assert got["beta"]["paused"] is True and "paused" not in got["alpha"]
    assert got["beta"]["monthly_usd"] == 5                       # kept for a restore
    ws.restore_archived(ws.list_archived()[0]["folder"])
    assert "paused" not in client.get("/api/budgets", headers=AUTH).json()["beta"]


# ---------- Feature 26: import from GitHub, and the Workspace watcher

class PagedResponse(FakeResponse):
    def __init__(self, data, link=None):
        super().__init__(data, None)
        self.headers = {"Link": link} if link else {}


def test_list_github_repos_with_token(root, mocker, monkeypatch):
    r, _ = root
    monkeypatch.setenv("OPTIMUX_GITHUB_TOKEN", "github_pat_" + "x" * 40)
    mocker.patch.object(ws, "github_status", return_value={"ready": True, "login": "me", "source": "optimux", "reason": ""})
    subprocess.run(["git", "init", "-q", str(r / "alpha")], check=True)
    subprocess.run(["git", "-C", str(r / "alpha"), "remote", "add", "origin", "git@github.com:Me/Alpha.git"], check=True)
    page1 = [{"full_name": "me/alpha", "name": "alpha", "private": True, "updated_at": "2026-01-01T00:00:00Z"},
             {"full_name": "../evil", "name": "evil"}]
    page2 = [{"full_name": "org/tool", "name": "tool", "private": False, "updated_at": "2026-05-01T00:00:00Z"}]
    urls = []

    def fake_open(req, timeout):
        urls.append(req.full_url)
        assert req.get_header("Authorization").startswith("Bearer github_pat_")
        if "page=2" in req.full_url:
            return PagedResponse(page2)
        return PagedResponse(page1, '<https://api.github.com/user/repos?page=2>; rel="next", <https://api.github.com/user/repos?page=2>; rel="last"')
    mocker.patch.object(ws, "_github_get", side_effect=fake_open)
    out = ws.list_github_repos()
    assert [x["full_name"] for x in out["repos"]] == ["org/tool", "me/alpha"]          # newest first; bad name dropped
    assert out["repos"][1]["local"] == "alpha" and out["repos"][0]["local"] is None    # matched case-insensitively
    assert len(urls) == 2 and out["fine_grained"] is True


def test_list_github_repos_never_follows_a_foreign_next_link(root, mocker, monkeypatch):
    monkeypatch.setenv("OPTIMUX_GITHUB_TOKEN", "ghp_" + "x" * 36)
    mocker.patch.object(ws, "github_status", return_value={"ready": True, "login": "me", "source": "optimux", "reason": ""})
    opened = mocker.patch.object(ws, "_github_get", return_value=PagedResponse([], '<https://evil.example/steal>; rel="next"'))
    ws.list_github_repos()
    assert opened.call_count == 1


def test_list_github_repos_with_gh_sign_in(root, mocker, monkeypatch):
    monkeypatch.delenv("OPTIMUX_GITHUB_TOKEN", raising=False)
    mocker.patch.object(ws, "github_status", return_value={"ready": True, "login": "", "source": "gh", "reason": ""})
    lines = '{"full_name":"me/a","name":"a","private":false,"updated_at":"2026-02-01"}\n{"full_name":"me/b","name":"b","private":true,"updated_at":"2026-03-01"}\n'
    run = mocker.patch.object(ws, "_run", return_value={"step": "gh", "ok": True, "output": lines})
    out = ws.list_github_repos()
    assert [x["name"] for x in out["repos"]] == ["b", "a"] and run.call_args.kwargs["github"] is True
    assert run.call_args.args[0][:3] == ["gh", "api", "--paginate"]


def test_list_github_repos_needs_github(root, mocker):
    mocker.patch.object(ws, "github_status", return_value={"ready": False, "reason": "GitHub isn't set up."})
    with pytest.raises(ws.GitHubError, match="isn't set up"):
        ws.list_github_repos()


def test_clone_command(root, mocker):
    r, _ = root
    mocker.patch.object(ws, "github_status", return_value={"ready": True, "reason": ""})
    jid = "a" * 32
    argv, staging, final = ws.clone_command("me/app", "my-app", jid)
    assert final == str(r / "my-app") and staging == str(r / (".clone-" + jid)) and os.path.isdir(staging)
    assert argv[:5] == ["git", *ws.GIT_SAFE] and "--no-recurse-submodules" in argv
    assert argv[-3:] == ["--", "https://github.com/me/app.git", staging]
    assert "credential.helper=!gh auth git-credential" in argv
    assert "my-app" not in ws.project_names(str(r)) and ".clone-" + jid not in ws.project_names(str(r))
    for bad_repo in ("me", "../x/y", "me/app;rm", "-x/y", "me/..", "me/.", "a/b/c", "me/app name"):
        with pytest.raises(ws.WorkspaceError):
            ws.clone_command(bad_repo, "ok", "b" * 32)
    for bad_name in ("alpha", "../x", ".hidden", ""):
        with pytest.raises(ws.WorkspaceError):
            ws.clone_command("me/app", bad_name, "c" * 32)
    with pytest.raises(ws.WorkspaceError):
        ws.clone_command("me/app", "ok", "../../etc")
    assert ws.clone_env()["GIT_LFS_SKIP_SMUDGE"] == "1"


def test_finish_clone_never_replaces_a_project(root, tmp_path):
    r, _ = root
    staging = r / (".clone-" + "d" * 32)
    staging.mkdir()
    (staging / "f").write_text("cloned")
    (r / "taken").mkdir()
    (r / "taken" / "mine.txt").write_text("user data")
    with pytest.raises(ws.WorkspaceError, match="appeared meanwhile"):
        ws.finish_clone(str(staging), str(r / "taken"))
    assert (r / "taken" / "mine.txt").read_text() == "user data"
    ws.finish_clone(str(staging), str(r / "fresh"))
    assert (r / "fresh" / "f").read_text() == "cloned" and not staging.exists()


def test_remove_staging_only_removes_staging(root, tmp_path):
    r, _ = root
    for bad in (str(r / "alpha"), str(r), str(r / ".clone-xyz"), str(r / ".archive")):
        with pytest.raises(ws.WorkspaceError):
            ws.remove_staging(bad)
    assert (r / "alpha").is_dir()
    stale = r / (".clone-" + "e" * 32)
    stale.mkdir()
    keep = r / (".clone-" + "f" * 32)
    keep.mkdir()
    ws.remove_stale_staging({"f" * 32})
    assert not stale.exists() and keep.exists()


def _bare_repo(tmp_path):
    src = tmp_path / "src"
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.name=T", "-c", "user.email=t@e.x", "commit", "-qm", "init"], check=True)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


def _wait_job(client, job_id, timeout=30):
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        st = client.get(f"/api/github/clone/{job_id}", headers=AUTH).json()
        if st["state"] != "running":
            return st
        _t.sleep(0.1)
    raise AssertionError("clone didn't finish")


def _patch_clone(mocker, script_for):
    """clone_command as usual (checks, staging folder), with git replaced by a local command."""
    real = ws.clone_command

    def fake(full_name, name, job_id):
        argv, staging, final = real(full_name, name, job_id)
        return script_for(full_name, staging, argv), staging, final
    mocker.patch.object(ws, "github_status", return_value={"ready": True, "reason": ""})
    mocker.patch.object(ws, "clone_command", side_effect=fake)


def test_clone_job_success_and_failure(mocker, client, root, tmp_path):
    from optimux_backend.core import project_records
    r, _ = root
    bare = _bare_repo(tmp_path)
    _patch_clone(mocker, lambda full, staging, argv: argv[:-3] + ["--", str(bare) if full == "me/good" else str(tmp_path / "missing.git"), staging])
    project_records.mark_deleted("good")
    client.__enter__()      # one event loop for all requests, so the background clone keeps running
    try:
        job = client.post("/api/github/clone", headers=AUTH, json={"full_name": "me/good", "name": "good"}).json()
        st = _wait_job(client, job["id"])
        assert st["state"] == "done" and (r / "good" / "README.md").read_text() == "hi"
        assert "good" not in project_records.deleted_projects()
        assert not [x for x in os.listdir(r) if x.startswith(".clone-")]
        res = client.post("/api/github/clone", headers=AUTH, json={"full_name": "me/good", "name": "good"})
        assert res.status_code == 422 and "already exists" in res.json()["detail"]
        job = client.post("/api/github/clone", headers=AUTH, json={"full_name": "me/bad", "name": "bad"}).json()
        st = _wait_job(client, job["id"])
        assert st["state"] == "failed" and st["error"] and not (r / "bad").exists()
        assert not [x for x in os.listdir(r) if x.startswith(".clone-")]
    finally:
        client.__exit__(None, None, None)


def _slow(staging, root, fail=False):
    return ["sh", "-c", f"echo data > '{staging}/f' && echo 'Receiving objects: 10%' >&2 && sleep 2 && exit {1 if fail else 0}"]


def _wait_for(path, seconds=5):
    import time as _t
    for _ in range(int(seconds * 10)):
        if os.path.exists(path):
            return
        _t.sleep(0.1)


def test_clone_job_cancel_and_double_click(mocker, client, root):
    r, _ = root
    _patch_clone(mocker, lambda full, staging, argv: _slow(staging, r) if full != "me/long" else
                 ["sh", "-c", f"echo x > '{staging}/f' && echo 'Receiving objects: 10%' >&2 && sleep 30"])
    client.__enter__()
    try:
        job = client.post("/api/github/clone", headers=AUTH, json={"full_name": "me/long", "name": "slow"}).json()
        # a second click for the same name is refused while the first runs
        again = client.post("/api/github/clone", headers=AUTH, json={"full_name": "me/long", "name": "slow"})
        assert again.status_code == 422 and "already being cloned" in again.json()["detail"]
        staging = r / (".clone-" + job["id"])
        _wait_for(staging / "f")
        assert not (r / "slow").exists() and "slow" not in ws.project_names(str(r))     # invisible while cloning
        assert client.get(f"/api/github/clone/{job['id']}", headers=AUTH).json()["progress"].startswith("Receiving")
        st = client.delete(f"/api/github/clone/{job['id']}", headers=AUTH).json()
        assert st["state"] == "cancelled" and not staging.exists() and not (r / "slow").exists()
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("fail", [True, False])
def test_a_folder_made_meanwhile_is_never_touched(mocker, client, root, fail):
    """The user creates the same name while the clone runs: whether the clone
    fails or succeeds, their folder stays as it is."""
    r, _ = root
    _patch_clone(mocker, lambda full, staging, argv: _slow(staging, r, fail=fail))
    client.__enter__()
    try:
        job = client.post("/api/github/clone", headers=AUTH, json={"full_name": "me/x", "name": "mine"}).json()
        (r / "mine").mkdir()
        (r / "mine" / "work.txt").write_text("precious")
        st = _wait_job(client, job["id"])
        assert st["state"] == "failed" and (r / "mine" / "work.txt").read_text() == "precious"
        if not fail:
            assert "appeared meanwhile" in st["error"]
        assert not (r / (".clone-" + job["id"])).exists()
    finally:
        client.__exit__(None, None, None)


def test_gh_listing_keeps_the_whole_output(root, mocker, monkeypatch):
    monkeypatch.delenv("OPTIMUX_GITHUB_TOKEN", raising=False)
    mocker.patch.object(ws, "github_status", return_value={"ready": True, "login": "", "source": "gh", "reason": ""})
    lines = "".join('{"full_name":"me/r%d","name":"r%d","updated_at":"2026-01-01"}\n' % (i, i) for i in range(300))
    run = mocker.patch.object(ws, "_run", return_value={"step": "gh", "ok": True, "output": lines})
    out = ws.list_github_repos()
    assert run.call_args.kwargs["keep"] is None and len(out["repos"]) == 300 and out["truncated"] is False


def test_github_requests_never_follow_redirects_elsewhere():
    import urllib.error
    import urllib.request
    h = ws._GitHubOnlyRedirects()
    req = urllib.request.Request("https://api.github.com/user/repos", headers={"Authorization": "Bearer x"})
    with pytest.raises(urllib.error.HTTPError, match="outside api.github.com"):
        h.redirect_request(req, None, 302, "Found", {}, "https://evil.example/steal")
    assert h.redirect_request(req, None, 301, "Moved", {}, "https://api.github.com/repositories/1") is not None


def test_workspace_watcher_tells_open_pages(root, mocker):
    import asyncio
    r, _ = root
    sent = []

    class Sock:
        async def send_text(self, t):
            sent.append(json.loads(t))
    mocker.patch.object(main, "WATCH_INTERVAL", 0.05)
    main.LIVE_SOCKETS["watch-test"] = {Sock()}

    async def scenario():
        main.ensure_workspace_watcher()
        await asyncio.sleep(0.2)
        assert sent == []                         # nothing changed yet
        (r / "made-by-hand").mkdir()
        await asyncio.sleep(0.3)
        main.LIVE_SOCKETS.pop("watch-test")       # last page closed: the watcher stops
        await asyncio.sleep(0.2)
        return main.WATCH["task"]
    try:
        assert asyncio.run(scenario()) is None
    finally:
        main.LIVE_SOCKETS.pop("watch-test", None)
    assert {"type": "projects_changed"} in sent
