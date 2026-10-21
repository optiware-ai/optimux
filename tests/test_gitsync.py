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
