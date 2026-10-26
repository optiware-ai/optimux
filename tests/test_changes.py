"""The changed-files summary (v0.4-5m): real git repositories, and the whole
loop through the WebSocket."""
import json
import os
import subprocess

import pytest

import optimux_backend.main as main
from optimux_backend.core import changes
from tests.ws_helpers import Conversation, chunk, collect, fake_stream, make_client
from tests.test_routing_session import reply

DONE = {"prompt_tokens": 5, "completion_tokens": 1}
ENV = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T",
       "GIT_COMMITTER_EMAIL": "t@x"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    (r / "site").mkdir(parents=True)
    (r / "site" / "index.html").write_text("".join(f"<p>line {i}</p>\n" for i in range(285)))
    (r / "style.css").write_text("a {}\nb {}\n")
    (r / "old.txt").write_text("bye\n")
    (r / ".gitignore").write_text("build/\n")
    git("init", "-q", cwd=r)
    git("add", "-A", cwd=r)
    git("commit", "-qm", "start", cwd=r)
    return r


def by_path(result):
    return {f["path"]: f for f in result["files"]}


def test_lists_new_changed_and_deleted_files(repo):
    base = changes.snapshot(str(repo))
    (repo / "style.css").write_text("a {}\nb { color: red }\nc {}\n")
    (repo / "notes.md").write_text("# Notes\n\none\n")
    (repo / "old.txt").unlink()
    files = by_path(changes.compare(base))
    assert files["style.css"] == {"path": "style.css", "status": "changed", "added": 2, "removed": 1,
                                  "lines_before": 2, "lines_after": 3, "warn": False}
    assert files["notes.md"]["status"] == "new" and files["notes.md"]["lines_after"] == 3
    assert files["old.txt"]["status"] == "deleted" and files["old.txt"]["warn"]
    assert set(files) == {"style.css", "notes.md", "old.txt"}


def test_an_emptied_file_is_flagged_and_listed_first(repo):
    """Task 3's bug: index.html went from 285 lines to 0 and the reply said done."""
    base = changes.snapshot(str(repo))
    (repo / "site" / "index.html").write_text("")
    (repo / "a-new.txt").write_text("x\n")
    result = changes.compare(base)
    first = result["files"][0]
    assert first["path"] == "site/index.html" and first["warn"]
    assert (first["lines_before"], first["lines_after"]) == (285, 0)


def test_shrinking_by_half_is_flagged_small_edits_are_not(repo):
    base = changes.snapshot(str(repo))
    lines = (repo / "site" / "index.html").read_text().splitlines(keepends=True)
    (repo / "site" / "index.html").write_text("".join(lines[:140]))
    assert by_path(changes.compare(base))["site/index.html"]["warn"]
    base = changes.snapshot(str(repo))
    (repo / "site" / "index.html").write_text("".join(lines[:150]))      # grew back: not a loss
    assert not by_path(changes.compare(base))["site/index.html"]["warn"]
    base = changes.snapshot(str(repo))
    (repo / "style.css").write_text("")                                  # 2 lines: too short to flag
    assert not by_path(changes.compare(base))["style.css"]["warn"]


def test_nothing_changed_ignored_files_and_binaries(repo):
    base = changes.snapshot(str(repo))
    assert changes.compare(base) == {"git": True, "files": [], "more": 0}
    (repo / "build").mkdir()
    (repo / "build" / "out.js").write_text("ignored\n")
    (repo / "logo.png").write_bytes(b"\x89PNG\x00\x01\x02")
    files = by_path(changes.compare(base))
    assert set(files) == {"logo.png"}
    assert files["logo.png"]["added"] is None and files["logo.png"]["status"] == "new"


def test_the_users_own_index_is_untouched(repo):
    (repo / "style.css").write_text("staged by the user\n")
    git("add", "style.css", cwd=repo)
    staged = git("diff", "--cached", "--name-only", cwd=repo)
    base = changes.snapshot(str(repo))
    (repo / "unstaged.txt").write_text("u\n")
    changes.compare(base)
    assert git("diff", "--cached", "--name-only", cwd=repo) == staged == "style.css\n"
    assert "?? unstaged.txt" in git("status", "--porcelain", cwd=repo)


def test_a_repository_without_commits_works(tmp_path):
    r = tmp_path / "fresh"
    r.mkdir()
    git("init", "-q", cwd=r)
    base = changes.snapshot(str(r))
    (r / "main.py").write_text("print(1)\n")
    assert by_path(changes.compare(base))["main.py"]["status"] == "new"


def test_no_git(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    base = changes.snapshot(str(tmp_path))
    assert base == {"git": False} and changes.compare(base) == {"git": False}


def test_outside_git_variables_are_ignored(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    git("init", "-q", cwd=other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "evil-index"))
    base = changes.snapshot(str(repo))
    (repo / "x.txt").write_text("x\n")
    assert set(by_path(changes.compare(base))) == {"x.txt"}
    assert not (tmp_path / "evil-index").exists()


def test_many_files_are_cut_and_counted(repo, monkeypatch):
    monkeypatch.setattr(changes, "MAX_FILES", 3)
    base = changes.snapshot(str(repo))
    for i in range(5):
        (repo / f"f{i}.txt").write_text("x\n")
    result = changes.compare(base)
    assert len(result["files"]) == 3 and result["more"] == 2


def test_a_git_failure_is_reported_not_raised(repo, monkeypatch):
    base = changes.snapshot(str(repo))
    monkeypatch.setattr(changes, "_tree", lambda top: (_ for _ in ()).throw(RuntimeError("index.lock exists")))
    assert changes.compare(base) == {"git": True, "error": "index.lock exists"}


# ---------- through the WebSocket

def _tool(name, args):
    return fake_stream([chunk(tool_call=(name, args)), chunk(empty=True, usage_obj=DONE)])


@pytest.fixture
def client(mocker, tmp_path, repo):
    c = make_client(mocker, tmp_path)
    mocker.patch.object(main, "current_workdir", return_value=str(repo))
    mocker.patch.object(main, "active_project", return_value="proj")
    return c


def test_a_reply_that_edits_a_file_ends_with_the_summary(mocker, client, repo):
    responses = iter([_tool("edit_file", {"path": "style.css", "old_text": "b {}", "new_text": "b { color: red }"}),
                      reply("Done.")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "make b red", "model": "auto"}))
    while json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    msgs = collect(chat.ws)
    chat.close()
    kinds = [m["type"] for m in msgs]
    assert kinds.index("changes") < kinds.index("done")
    summary = msgs[kinds.index("changes")]
    assert summary["files"] == [{"path": "style.css", "status": "changed", "added": 1, "removed": 1,
                                 "lines_before": 2, "lines_after": 2, "warn": False}]


def test_a_command_that_empties_a_file_is_flagged(mocker, client, repo):
    async def empties(cmd, *a, **k):
        (repo / "site" / "index.html").write_text("")
        return {"stdout": "", "stderr": "", "exit_code": 0}
    mocker.patch.object(main, "run_shell_command", side_effect=empties)
    responses = iter([_tool("execute_bash", {"command": "python3 fix.py"}), reply("Done.")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "fix it", "model": "auto"}))
    while json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    msgs = collect(chat.ws)
    chat.close()
    summary = [m for m in msgs if m["type"] == "changes"][0]
    f = summary["files"][0]
    assert f["path"] == "site/index.html" and f["warn"] and (f["lines_before"], f["lines_after"]) == (285, 0)


def test_chat_only_replies_and_rejected_commands_take_no_snapshot(mocker, client):
    snap = mocker.spy(changes, "snapshot")
    responses = iter([reply("Hello!"), _tool("execute_bash", {"command": "rm -rf site"}), reply("ok")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    assert not [m for m in chat.prompt("hi") if m["type"] == "changes"]
    chat.ws.send_text(json.dumps({"prompt": "delete the site", "model": "auto"}))
    while json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": False}))
    msgs = collect(chat.ws)
    chat.close()
    assert not [m for m in msgs if m["type"] == "changes"] and snap.call_count == 0


def test_a_project_without_git_says_so(mocker, client, tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    mocker.patch.object(main, "current_workdir", return_value=str(plain))
    responses = iter([_tool("write_file", {"path": "a.txt", "content": "a\n"}), reply("ok")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "make a file", "model": "auto"}))
    while json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    msgs = collect(chat.ws)
    chat.close()
    assert [m for m in msgs if m["type"] == "changes"] == [{"type": "changes", "git": False}]


def test_a_same_size_edit_right_after_a_clone_is_seen(repo, tmp_path):
    """Racily clean entries (5t found it: '<p>2024</p>' -> '<p>2026</p>' was
    missed now and then right after a clone). The clone writes the index and
    the file is edited, at the same size, in the same second; the snapshot runs
    in the next second. A copy of the index with a new mtime made git trust the
    stale stat data; the copy now keeps the index's mtime."""
    import time
    for i in range(3):
        while time.time() % 1 > 0.2:                              # start early in a second
            time.sleep(0.01)
        dest = tmp_path / f"clone{i}"
        git("clone", "-q", str(repo), str(dest), cwd=tmp_path)
        head = git("rev-parse", "HEAD^{tree}", cwd=dest).strip()
        (dest / "old.txt").write_text("bey\n")                 # same size as "bye\n"
        time.sleep(1.05 - time.time() % 1)                      # the snapshot in the next second
        assert changes._tree(str(dest)) != head, f"missed on round {i}"


def test_a_gitignore_that_loses_rules_and_the_secrets_git_now_sees(repo):
    (repo / ".gitignore").write_text("# local\n.env\n__pycache__/\nbuild/\n")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "ignore", cwd=repo)
    (repo / ".env").write_text("TOKEN=x\n")                 # ignored: not in the summary
    base = changes.snapshot(str(repo))
    (repo / ".gitignore").write_text("node_modules/\nbuild/\n")
    files = by_path(changes.compare(base))
    gi, env = files[".gitignore"], files[".env"]
    assert gi["warn"] and gi["unignored"] == [".env", "__pycache__/"] and gi["unignored_more"] == 0
    assert env["status"] == "new" and env["warn"] and env["secret"]
    assert list(files)[:2] == [".env", ".gitignore"]           # warnings first


def test_a_gitignore_that_only_grows_is_fine(repo):
    base = changes.snapshot(str(repo))
    (repo / ".gitignore").write_text("build/\ndist/\n")
    gi = by_path(changes.compare(base))[".gitignore"]
    assert not gi["warn"] and "unignored" not in gi
