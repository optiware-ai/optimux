"""Push and Update from GitHub (v0.4-5t), on real repositories with a local
bare repository as "GitHub"."""
import os
import subprocess

import pytest

from optimux_backend.core import gitsync, paths

ENV = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T",
       "GIT_COMMITTER_EMAIL": "t@x"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A Workspace with project 'site' (clone of a bare 'GitHub' repo named site)."""
    remote = tmp_path / "github" / "site.git"
    remote.mkdir(parents=True)
    git("init", "-q", "--bare", "-b", "main", cwd=remote)
    root = tmp_path / "ws"
    root.mkdir()
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    (seed / "index.html").write_text("<p>2024</p>\n")
    (seed / "commit-template.txt").write_text("[ TYPE ] Area: Summary\n\nDETAILS:\n")
    git("add", "-A", cwd=seed)
    git("commit", "-qm", "start", cwd=seed)
    git("remote", "add", "origin", str(remote), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    git("clone", "-q", str(remote), "site", cwd=root)
    for d in (root / "site", seed):
        git("config", "user.email", "t@x", cwd=d)
        git("config", "user.name", "T", cwd=d)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("ANTHROPIC_API_KEY=sk-ant-very-secret-123\nSHORT_KEY=abc\n")
    monkeypatch.setattr(gitsync, "projects_root", lambda: str(root))
    monkeypatch.setattr(paths, "SECRETS_FILE", str(secrets))
    return {"root": root, "proj": root / "site", "remote": remote, "seed": seed}


def test_push_shows_the_files_then_commits_and_pushes(ws):
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    (ws["proj"] / "menu.js").write_text("// menu\n")
    prep = gitsync.push_prepare("site")
    assert prep["problems"] == [] and prep["target"] == "main" and prep["template"] is True
    assert {f["path"]: f["status"] for f in prep["files"]} == {"index.html": "changed", "menu.js": "new"}
    result = gitsync.push_run("site", "[ FIX ] Site: Year 2026\n\n# kept: not a comment", prep["tree"], False)
    assert result["ok"] and result["branch"] == "main" and result["files"] == 2
    log = git("log", "-1", "--format=%B", "main", cwd=ws["remote"])
    assert log.startswith("[ FIX ] Site: Year 2026") and "# kept" in log
    assert git("show", "main:menu.js", cwd=ws["remote"]) == "// menu"
    assert gitsync.push_prepare("site")["nothing"]


def test_push_refuses_when_files_changed_after_the_dialog(ws):
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    prep = gitsync.push_prepare("site")
    (ws["proj"] / "late.txt").write_text("added after\n")
    with pytest.raises(gitsync.GitSyncError, match="changed since you opened"):
        gitsync.push_run("site", "msg", prep["tree"], False)
    assert git("rev-list", "--count", "main", cwd=ws["remote"]) == "1"


def test_a_saved_key_in_a_file_blocks_the_push_without_showing_it(ws):
    (ws["proj"] / "config.py").write_text("KEY = 'sk-ant-very-secret-123'\n")
    prep = gitsync.push_prepare("site")
    assert any("config.py contains your saved key ANTHROPIC_API_KEY" in p for p in prep["problems"])
    assert "sk-ant" not in str(prep)
    with pytest.raises(gitsync.GitSyncError, match="ANTHROPIC_API_KEY"):
        gitsync.push_run("site", "msg", prep["tree"], True)
    with pytest.raises(gitsync.GitSyncError, match="ANTHROPIC_API_KEY"):
        gitsync.message_material("site")          # never sent to a model either


def test_secret_looking_names_need_a_confirmation(ws):
    (ws["proj"] / ".env").write_text("DEBUG=1\n")
    prep = gitsync.push_prepare("site")
    assert prep["problems"] == [] and any(".env looks like a file of secrets" in w for w in prep["warnings"])
    with pytest.raises(gitsync.GitSyncError, match="Confirm the warnings"):
        gitsync.push_run("site", "msg", prep["tree"], False)
    assert gitsync.push_run("site", "msg", prep["tree"], True)["ok"]


def test_push_refuses_when_github_is_ahead_and_never_forces(ws):
    (ws["seed"] / "index.html").write_text("<p>from elsewhere</p>\n")
    git("commit", "-qam", "elsewhere", cwd=ws["seed"])
    git("push", "-q", "origin", "main", cwd=ws["seed"])
    (ws["proj"] / "mine.txt").write_text("x\n")
    prep = gitsync.push_prepare("site")
    assert prep["behind"] == 1 and any("Update first" in p for p in prep["problems"])
    with pytest.raises(gitsync.GitSyncError, match="Update first"):
        gitsync.push_run("site", "msg", prep["tree"], False)
    assert git("log", "-1", "--format=%s", "main", cwd=ws["remote"]) == "elsewhere"


def test_a_copy_pushes_to_its_own_branch(ws):
    git("clone", "-q", str(ws["remote"]), "t10", cwd=ws["root"])
    copy = ws["root"] / "t10"
    git("config", "user.email", "t@x", cwd=copy)
    git("config", "user.name", "T", cwd=copy)
    (copy / "index.html").write_text("<p>copy</p>\n")
    prep = gitsync.push_prepare("t10")
    assert prep["copy_of"] == "site" and prep["target"] == "t10" and prep["upstream"] is None
    gitsync.push_run("t10", "copy work", prep["tree"], False)
    assert git("show", "t10:index.html", cwd=ws["remote"]) == "<p>copy</p>"
    assert git("show", "main:index.html", cwd=ws["remote"]) == "<p>2024</p>"     # main untouched
    assert gitsync.update_prepare("t10")["up_to_date"]


def test_update_lists_incoming_commits_and_fast_forwards(ws):
    (ws["seed"] / "index.html").write_text("<p>new</p>\n")
    git("commit", "-qam", "New year", cwd=ws["seed"])
    git("push", "-q", "origin", "main", cwd=ws["seed"])
    prep = gitsync.update_prepare("site")
    assert prep["problems"] == [] and prep["behind"] == 1
    assert prep["commits"][0]["subject"] == "New year" and prep["files"] == [{"status": "changed", "path": "index.html"}]
    result = gitsync.update_run("site", prep["sha"])
    assert result["ok"] and (ws["proj"] / "index.html").read_text() == "<p>new</p>\n"
    assert gitsync.update_prepare("site")["up_to_date"]


def test_update_refuses_to_overwrite_local_changes_or_merge(ws):
    (ws["seed"] / "index.html").write_text("<p>new</p>\n")
    git("commit", "-qam", "New year", cwd=ws["seed"])
    git("push", "-q", "origin", "main", cwd=ws["seed"])
    (ws["proj"] / "index.html").write_text("<p>mine</p>\n")
    prep = gitsync.update_prepare("site")
    assert any("You changed files this update also changes: index.html" in p for p in prep["problems"])
    with pytest.raises(gitsync.GitSyncError, match="also changes"):
        gitsync.update_run("site", prep["sha"])
    assert (ws["proj"] / "index.html").read_text() == "<p>mine</p>\n"
    git("commit", "-qam", "mine", cwd=ws["proj"])                            # now the histories diverge
    prep = gitsync.update_prepare("site")
    assert any("went apart" in p for p in prep["problems"])


def test_update_refuses_a_sha_that_changed(ws):
    (ws["seed"] / "a.txt").write_text("a\n")
    git("add", "-A", cwd=ws["seed"]); git("commit", "-qm", "a", cwd=ws["seed"]); git("push", "-q", "origin", "main", cwd=ws["seed"])
    prep = gitsync.update_prepare("site")
    (ws["seed"] / "b.txt").write_text("b\n")
    git("add", "-A", cwd=ws["seed"]); git("commit", "-qm", "b", cwd=ws["seed"]); git("push", "-q", "origin", "main", cwd=ws["seed"])
    with pytest.raises(gitsync.GitSyncError, match="changed since"):
        gitsync.update_run("site", prep["sha"])


def test_message_material_reads_the_template_and_the_diff(ws):
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    m = gitsync.message_material("site")
    assert m["template"].startswith("[ TYPE ] Area: Summary") and "+<p>2026</p>" in m["diff"]
    prompt = gitsync.message_prompt(m)
    assert "Follow this commit template exactly" in prompt and "RECENT COMMIT SUBJECTS" in prompt
    assert gitsync.clean_message("```\n[ FIX ] x\n```") == "[ FIX ] x"


def test_template_outside_the_project_is_ignored(ws, tmp_path):
    (tmp_path / "evil.txt").write_text("outside\n")
    os.remove(ws["proj"] / "commit-template.txt")
    git("config", "commit.template", str(tmp_path / "evil.txt"), cwd=ws["proj"])
    (ws["proj"] / "x.txt").write_text("x\n")
    assert gitsync.message_material("site")["template"] == ""


def test_not_a_repository_and_no_remote(ws):
    (ws["root"] / "plain").mkdir()
    with pytest.raises(gitsync.GitSyncError, match="isn't a git repository"):
        gitsync.push_prepare("plain")
    git("remote", "remove", "origin", cwd=ws["proj"])
    assert "no origin" in gitsync.push_prepare("site")["problems"][0]


# ---------- through the API

def test_the_api_prepares_runs_and_writes_the_message(ws, mocker, tmp_path):
    from unittest.mock import MagicMock
    import optimux_backend.main as main
    from tests.ws_helpers import make_client
    client = make_client(mocker, tmp_path)
    H = {"Authorization": "Bearer test-token"}
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    prep = client.get("/api/projects/site/push", headers=H).json()
    assert prep["files"][0]["path"] == "index.html"
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "```\n[ FIX ] Site: Year 2026\n```"
    resp.usage = {"prompt_tokens": 400, "completion_tokens": 20}
    call = mocker.patch("litellm.acompletion", return_value=resp)
    m = client.post("/api/projects/site/commit-message", headers=H).json()
    assert m["message"] == "[ FIX ] Site: Year 2026" and m["template"] is True
    assert "Follow this commit template exactly" in call.call_args.kwargs["messages"][0]["content"]
    main.BUSY_PROJECTS["site"] = 1
    try:
        r = client.post("/api/projects/site/push", json={"message": m["message"], "tree": prep["tree"]}, headers=H)
        assert r.status_code == 422 and "A request is running" in r.json()["detail"]
    finally:
        main.BUSY_PROJECTS.pop("site", None)
    r = client.post("/api/projects/site/push", json={"message": m["message"], "tree": prep["tree"]}, headers=H).json()
    assert r["ok"] and git("log", "-1", "--format=%s", "main", cwd=ws["remote"]) == "[ FIX ] Site: Year 2026"
    r = client.post("/api/projects/site/update", json={"sha": "nope"}, headers=H)
    assert r.status_code == 422


@pytest.mark.parametrize("config", [
    [],                                    # no identity at all (no guessing from the host name)
    [("user.email", "t@x"), ("user.name", "")],   # an email but no usable name, as for an
])                                         # account without a full name (CI's runner, a service user)
def test_an_identity_git_cannot_commit_with_is_a_problem(ws, monkeypatch, config):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("user.email", "user.name"):
        subprocess.run(["git", "config", "--unset", k], cwd=ws["proj"], capture_output=True)
    for k, v in config:
        git("config", k, v, cwd=ws["proj"])
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    prep = gitsync.push_prepare("site")
    assert prep["identity"] is False and any("no name and email" in p for p in prep["problems"])
    with pytest.raises(gitsync.GitSyncError, match="no name and email"):
        gitsync.push_run("site", "x", prep["tree"], False)
    # the name and email from Dashboard -> GitHub are enough
    monkeypatch.setattr("optimux_backend.core.workspace.get_settings",
                        lambda: {"git_name": "Masoud", "git_email": "m@example.com"})
    assert gitsync.push_prepare("site")["identity"] is True


# ---- Status (v0.8): the Project page's terminal window

def test_status_fetches_and_shows_git_status_and_the_last_commits(ws):
    git("commit", "-q", "--allow-empty", "-m", "on github", cwd=ws["seed"])
    git("push", "-q", "origin", "main", cwd=ws["seed"])
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    (ws["proj"] / "new.txt").write_text("new\n")
    before = git("rev-parse", "HEAD", cwd=ws["proj"])
    r = gitsync.status_report("site")
    assert [s["command"] for s in r["steps"]] == ["git fetch origin", "git status", "git log --oneline -n 5"]
    assert all(s["ok"] for s in r["steps"])
    status = r["steps"][1]["output"]
    assert "On branch main" in status and "behind 'origin/main' by 1 commit" in status
    assert "modified:   index.html" in status and "new.txt" in status
    assert "start" in r["steps"][2]["output"]
    assert (r["ahead"], r["behind"], r["target"]) == (0, 1, "main")
    # nothing in the project changed: same commit, same files, nothing staged
    assert git("rev-parse", "HEAD", cwd=ws["proj"]) == before
    assert (ws["proj"] / "index.html").read_text() == "<p>2026</p>\n"
    assert git("diff", "--cached", "--name-only", cwd=ws["proj"]) == ""


def test_status_masks_saved_keys_and_tokens_in_urls(ws):
    git("commit", "-q", "--allow-empty", "-m", "uses sk-ant-very-secret-123", cwd=ws["proj"])
    git("remote", "set-url", "origin", "https://user:tok123@127.0.0.1:9/none.git", cwd=ws["proj"])
    r = gitsync.status_report("site")
    text = str(r)
    assert "sk-ant-very-secret-123" not in text and "••••(ANTHROPIC_API_KEY)" in r["steps"][2]["output"]
    assert "tok123" not in text and r["steps"][0]["ok"] is False


def test_status_without_github_and_outside_git(ws, tmp_path):
    git("remote", "remove", "origin", cwd=ws["proj"])
    r = gitsync.status_report("site")
    assert [s["command"] for s in r["steps"]] == ["git status", "git log --oneline -n 5"]
    assert r["ahead"] is None and r["remote"] is None
    (ws["root"] / "plain").mkdir()
    with pytest.raises(gitsync.GitSyncError, match="isn't a git repository"):
        gitsync.status_report("plain")


def test_status_waits_for_a_running_push(ws):
    lock = gitsync._lock("site")
    lock.acquire()
    try:
        with pytest.raises(gitsync.GitSyncError, match="push or update"):
            gitsync.status_report("site")
    finally:
        lock.release()


def test_the_status_api(ws, mocker, tmp_path):
    from tests.ws_helpers import make_client
    client = make_client(mocker, tmp_path)
    H = {"Authorization": "Bearer test-token"}
    r = client.get("/api/projects/site/status", headers=H).json()
    assert r["steps"][1]["command"] == "git status" and "On branch main" in r["steps"][1]["output"]
    (ws["root"] / "plain").mkdir()
    r = client.get("/api/projects/plain/status", headers=H)
    assert r.status_code == 422 and "isn't a git repository" in r.json()["detail"]
    assert client.get("/api/projects/..%2Fx/status", headers=H).status_code in (404, 422)


# ---- Identity (v0.8): who talks to the remote

def _no_rewrites(monkeypatch):
    """No url.insteadOf from the machine running the tests (it would turn ssh remotes into HTTPS)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)


def _fake(tmp_path, monkeypatch, name, script):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    f = bin_dir / name
    f.write_text("#!/bin/sh\n" + script)
    f.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    _no_rewrites(monkeypatch)
    return bin_dir


@pytest.mark.parametrize("remote, dest, port", [
    ("git@github.com:o/site.git", "git@github.com", None),
    ("ssh://git@git.example.org:2222/o/site.git", "git@git.example.org", "2222"),
    ("gh-work:o/site.git", "gh-work", None),          # an alias from ~/.ssh/config
    ("https://github.com/o/site.git", None, None),
    ("/srv/git/site.git", None, None),
    ("-oProxyCommand=touch /tmp/x:o/site", None, None),   # never an ssh option
    ("git@-oProxyCommand=x:o/site", None, None),
])
def test_ssh_targets(remote, dest, port):
    assert gitsync.ssh_target(remote) == ((dest, port) if dest else None)


def test_identity_runs_ssh_t_without_prompts(ws, tmp_path, monkeypatch):
    bin_dir = _fake(tmp_path, monkeypatch, "ssh",
                    'echo "$@" > "$(dirname "$0")/args"\n'
                    'echo "Hi octo! You\'ve successfully authenticated, but GitHub does not provide shell access." >&2\n'
                    "exit 1\n")
    git("remote", "set-url", "origin", "git@github.com:o/site.git", cwd=ws["proj"])
    r = gitsync.identity_report("site")
    cmds = {s["command"]: s for s in r["steps"]}
    assert r["kind"] == "ssh" and cmds["git var GIT_AUTHOR_IDENT"]["output"] == "T <t@x>"
    assert cmds["git remote get-url origin"]["output"] == "git@github.com:o/site.git"
    ssh = cmds["ssh -T git@github.com"]
    assert ssh["ok"] and "Hi octo!" in ssh["output"]
    assert (bin_dir / "args").read_text().split() == ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                                      "--", "git@github.com"]


def test_identity_shows_a_refused_key_and_an_unknown_host(ws, tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, "ssh", 'echo "git@github.com: Permission denied (publickey)." >&2\nexit 255\n')
    git("remote", "set-url", "origin", "ssh://git@github.com/o/site.git", cwd=ws["proj"])
    git("config", "core.sshCommand", "ssh -i ~/.ssh/work", cwd=ws["proj"])
    r = gitsync.identity_report("site")
    assert r["steps"][-1]["ok"] is False and "Permission denied" in r["steps"][-1]["output"]
    assert "core.sshCommand" in r["notes"][0]
    _fake(tmp_path, monkeypatch, "ssh", 'echo "Host key verification failed." >&2\nexit 255\n')
    r = gitsync.identity_report("site")
    assert r["steps"][-1]["ok"] is False and any("Unknown host key" in n for n in r["notes"])


def test_identity_over_https_names_the_saved_token_account(ws, tmp_path, monkeypatch):
    _fake(tmp_path, monkeypatch, "gh", 'echo "github.com"\necho "  ✓ Logged in to github.com account octo (GH_TOKEN) $GH_TOKEN"\n')
    git("remote", "set-url", "origin", "https://user:tok123@github.com/o/site.git", cwd=ws["proj"])
    monkeypatch.setenv(gitsync.GITHUB_TOKEN_ENV, "ghp_saved-token-value-1")
    (tmp_path / "secrets.env").write_text(f"{gitsync.GITHUB_TOKEN_ENV}=ghp_saved-token-value-1\n")
    r = gitsync.identity_report("site")
    gh = r["steps"][-1]
    assert r["kind"] == "https" and gh["command"] == "gh auth status --hostname github.com"
    assert "account octo" in gh["output"] and "ghp_saved" not in str(r) and "tok123" not in str(r)
    monkeypatch.delenv(gitsync.GITHUB_TOKEN_ENV)
    r = gitsync.identity_report("site")
    assert len(r["steps"]) == 2 and "No GitHub token" in r["notes"][0]


def test_identity_of_a_local_remote_and_the_api(ws, mocker, tmp_path, monkeypatch):
    _no_rewrites(monkeypatch)
    r = gitsync.identity_report("site")
    assert r["kind"] == "local" and "no account" in r["notes"][0]
    from tests.ws_helpers import make_client
    client = make_client(mocker, tmp_path)
    r = client.get("/api/projects/site/identity", headers={"Authorization": "Bearer test-token"}).json()
    assert r["steps"][0]["command"] == "git var GIT_AUTHOR_IDENT"


def test_push_warns_when_origin_changed_since_the_last_push(ws, tmp_path):
    """v0.8: a command can change origin; Push names the old and new remote and
    needs the warning confirmed before sending the code elsewhere."""
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    prep = gitsync.push_prepare("site")
    assert not prep["warnings"]
    gitsync.push_run("site", "first", prep["tree"], False)
    assert gitsync._known_remotes()["site"] == str(ws["remote"])
    other = tmp_path / "github" / "elsewhere.git"
    other.mkdir()
    git("init", "-q", "--bare", "-b", "main", cwd=other)
    git("remote", "set-url", "origin", str(other), cwd=ws["proj"])
    (ws["proj"] / "index.html").write_text("<p>2027</p>\n")
    prep = gitsync.push_prepare("site")
    assert "origin changed since the last push" in prep["warnings"][0] and str(other) in prep["warnings"][0]
    with pytest.raises(gitsync.GitSyncError, match="Confirm the warnings"):
        gitsync.push_run("site", "second", prep["tree"], False)


# ---- v0.8: the Studio's Git pane (local state, commit only)

def test_local_state_lists_uncommitted_files_without_the_network(ws, monkeypatch):
    """The pane is refreshed after every reply: it must never fetch (no network, no wait)."""
    calls = []
    real = gitsync._git
    monkeypatch.setattr(gitsync, "_git", lambda path, *a, **k: calls.append(a) or real(path, *a, **k))
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    (ws["proj"] / "menu.js").write_text("// menu\n")
    s = gitsync.local_state("site")
    assert {f["path"]: f["status"] for f in s["files"]} == {"index.html": "changed", "menu.js": "new"}
    assert s["branch"] == "main" and s["remote"] and s["ahead"] == 0 and s["behind"] == 0
    assert s["tree"] and s["problems"] == [] and s["can_commit"]
    assert not any(a and a[0] in ("fetch", "push", "pull") for a in calls)
    git("add", "-A", cwd=ws["proj"])
    git("commit", "-qm", "local", cwd=ws["proj"])
    s = gitsync.local_state("site")
    assert s["files"] == [] and s["ahead"] == 1 and not s["can_commit"]


def test_commit_only_commits_what_was_shown_and_never_pushes(ws):
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    s = gitsync.local_state("site")
    r = gitsync.commit_run("site", "[ FIX ] Site: Year 2026", s["tree"], False)
    assert r["ok"] and r["files"] == 1 and r["commit"]
    assert git("log", "-1", "--format=%s", cwd=ws["proj"]) == "[ FIX ] Site: Year 2026"
    assert git("rev-list", "--count", "main", cwd=ws["remote"]) == "1"        # GitHub untouched
    assert gitsync.local_state("site")["ahead"] == 1
    (ws["proj"] / "a.txt").write_text("a\n")
    s = gitsync.local_state("site")
    (ws["proj"] / "late.txt").write_text("after\n")
    with pytest.raises(gitsync.GitSyncError, match="changed since"):
        gitsync.commit_run("site", "msg", s["tree"], False)
    with pytest.raises(gitsync.GitSyncError, match="commit message"):
        gitsync.commit_run("site", "  ", gitsync.local_state("site")["tree"], False)


def test_commit_only_keeps_the_safety_checks(ws):
    (ws["proj"] / "config.py").write_text("KEY = 'sk-ant-very-secret-123'\n")
    s = gitsync.local_state("site")
    assert any("ANTHROPIC_API_KEY" in p for p in s["problems"]) and "sk-ant" not in str(s)
    with pytest.raises(gitsync.GitSyncError, match="ANTHROPIC_API_KEY"):
        gitsync.commit_run("site", "msg", s["tree"], True)
    (ws["proj"] / "config.py").unlink()
    (ws["proj"] / ".env").write_text("X=1\n")
    s = gitsync.local_state("site")
    assert s["warnings"] and not s["problems"]
    with pytest.raises(gitsync.GitSyncError, match="Confirm"):
        gitsync.commit_run("site", "msg", s["tree"], False)
    assert gitsync.commit_run("site", "msg", s["tree"], True)["ok"]


def test_local_state_without_origin_can_still_commit(ws):
    git("remote", "remove", "origin", cwd=ws["proj"])
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    s = gitsync.local_state("site")
    assert s["remote"] is None and s["files"] and s["can_commit"]
    assert gitsync.commit_run("site", "local only", s["tree"], False)["ok"]


def test_the_git_pane_api(ws, mocker, tmp_path):
    import optimux_backend.main as main
    from tests.ws_helpers import make_client
    client = make_client(mocker, tmp_path)
    H = {"Authorization": "Bearer test-token"}
    (ws["proj"] / "index.html").write_text("<p>2026</p>\n")
    s = client.get("/api/projects/site/git-state", headers=H).json()
    assert [f["path"] for f in s["files"]] == ["index.html"]
    main.BUSY_PROJECTS["site"] = 1
    try:
        r = client.post("/api/projects/site/commit", json={"message": "m", "tree": s["tree"]}, headers=H)
        assert r.status_code == 422 and "A request is running" in r.json()["detail"]
    finally:
        main.BUSY_PROJECTS.pop("site", None)
    r = client.post("/api/projects/site/commit", json={"message": "m", "tree": s["tree"]}, headers=H).json()
    assert r["ok"] and git("log", "-1", "--format=%s", cwd=ws["proj"]) == "m"
    assert client.get("/api/projects/nope/git-state", headers=H).status_code == 422
