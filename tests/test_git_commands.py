"""Push and pull one-liners for a project card (Dashboard → Projects)."""
import os
import shlex
import subprocess

import pytest

import optimux_backend.core.workspace as ws
from optimux_backend.core import paths
from optimux_backend.core.security import GITHUB_TOKEN_ENV


SETTINGS = {}


@pytest.fixture
def root(mocker, tmp_path, monkeypatch):
    r = tmp_path / "projects"
    (r / "plain").mkdir(parents=True)
    SETTINGS.clear()
    SETTINGS.update({"projects_root": str(r), "active_project": ""})
    mocker.patch.object(ws, "get_settings", side_effect=lambda: dict(SETTINGS))
    for k in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{k}_NAME", "T"); monkeypatch.setenv(f"GIT_{k}_EMAIL", "t@example.com")
    monkeypatch.delenv(GITHUB_TOKEN_ENV, raising=False)
    monkeypatch.delenv("OPTIMUX_SERVER", raising=False)
    return r


def repo(root, name, remote=None):
    p = root / name
    p.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(p)], check=True)
    (p / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(p), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p), "commit", "-qm", "first"], check=True)
    if remote:
        subprocess.run(["git", "-C", str(p), "remote", "add", "origin", remote], check=True)
    return p


def test_no_git_or_no_origin_says_why(root):
    assert ws.git_commands("plain")["problem"] == "This project isn't a git repository."
    repo(root, "lonely")
    out = ws.git_commands("lonely")
    assert out["push"] is None and out["pull"] is None and out["problem"].startswith("It has no origin.")


def test_github_one_liners_on_a_server_with_a_token(root, monkeypatch):
    repo(root, "makerhub", "https://x-access-token:SECRET@github.com/optiware-ai/makerhub.git")
    (root / "makerhub" / "new.txt").write_text("n\n")
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "SECRET")
    monkeypatch.setenv("OPTIMUX_SERVER", "1")
    monkeypatch.setattr("getpass.getuser", lambda: "optimux")
    out = ws.git_commands("makerhub")
    assert out["remote"] == "https://github.com/optiware-ai/makerhub.git"      # no token shown
    assert out["branch"] == "main" and out["changes"] == 1 and out["server"]
    assert "SECRET" not in repr(out)
    argv = shlex.split(out["push"])
    assert argv[:4] == ["sudo", "-u", "optimux", "bash"] and argv[4] == "-c"
    inner = argv[5]
    assert inner.startswith(f"set -a && . {os.path.abspath(paths.SECRETS_FILE)} && set +a && cd {root / 'makerhub'} && ")
    assert "git commit -m 'Update from Optimux'" in inner
    assert inner.endswith("GH_TOKEN=\"$OPTIMUX_GITHUB_TOKEN\" git -c credential.helper= "
                          "-c 'credential.helper=!gh auth git-credential' push -u origin HEAD")
    assert out["pull"] == ("if [ -d makerhub/.git ]; then git -C makerhub pull; "
                           "else git clone https://github.com/optiware-ai/makerhub.git makerhub; fi")
    assert "git@github.com:optiware-ai/makerhub.git" in out["pull_ssh"]


def test_ssh_remote_without_token_is_a_plain_push(root):
    repo(root, "tool", "git@github.com:me/tool.git")
    out = ws.git_commands("tool")
    assert out["push"] == f"cd {root / 'tool'} && git add -A && (git diff --cached --quiet || " \
                          "git commit -m 'Update from Optimux') && git push -u origin HEAD"
    assert "git clone https://github.com/me/tool.git tool" in out["pull"]


def test_the_push_and_pull_really_work(root, tmp_path):
    """Run the commands as a user would: a local bare repository stands in for GitHub."""
    bare = tmp_path / "remote" / "shared.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    p = repo(root, "shared", str(bare))
    (p / "b.txt").write_text("b\n")
    out = ws.git_commands("shared")
    subprocess.run(out["push"], shell=True, check=True, capture_output=True)
    log = subprocess.run(["git", "-C", str(bare), "log", "--format=%s", "main"], capture_output=True, text=True).stdout
    assert log.split("\n")[:2] == ["Update from Optimux", "first"]
    subprocess.run(out["push"], shell=True, check=True, capture_output=True)       # nothing new: no empty commit
    elsewhere = tmp_path / "laptop"
    elsewhere.mkdir()
    subprocess.run(out["pull"], shell=True, check=True, cwd=elsewhere, capture_output=True)
    assert (elsewhere / "shared" / "b.txt").read_text() == "b\n"
    subprocess.run(out["pull"], shell=True, check=True, cwd=elsewhere, capture_output=True)   # second time: pull


def test_odd_names_and_paths_are_quoted(root):
    out = ws.git_commands  # names are validated, so a path can't inject a command
    with pytest.raises(ws.WorkspaceError):
        out("a;b")
    with pytest.raises(ws.WorkspaceError):
        out("missing")


def test_the_push_commits_as_the_dashboard_identity(root, tmp_path, monkeypatch):
    """Optimux's user often has no git identity on a server (Optimux passes the
    Dashboard → GitHub name as environment variables), so the command must too."""
    bare = tmp_path / "remote" / "who.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    p = repo(root, "who", str(bare))
    (p / "c.txt").write_text("c\n")
    for k in ("AUTHOR", "COMMITTER"):
        monkeypatch.delenv(f"GIT_{k}_NAME"); monkeypatch.delenv(f"GIT_{k}_EMAIL")
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))          # no ~/.gitconfig
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-home" / ".config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("EMAIL", "")
    without = ws.git_commands("who")
    assert without["warning"].startswith("Git has no name and email")
    # (Whether git then fails depends on the machine: with a full name in the
    # account's details, git makes up an identity; on the VPS it refused.)
    SETTINGS.update(git_name="Masoud O'Brien", git_email="m@example.com")
    out = ws.git_commands("who")
    assert out["warning"] is None and "-c user.name='Masoud O'\"'\"'Brien' -c user.email=m@example.com commit" in out["push"]
    subprocess.run(out["push"], shell=True, check=True, capture_output=True)
    who = subprocess.run(["git", "-C", str(bare), "log", "-1", "--format=%an <%ae>"], capture_output=True, text=True).stdout
    assert who.strip() == "Masoud O'Brien <m@example.com>"


def test_copies_push_to_their_own_branch(root, tmp_path, monkeypatch):
    """Two copies of one repository (Clone from GitHub, again) both push, each to
    a branch named after its folder; the original's main is never touched."""
    bare = tmp_path / "remote" / "makerhub2.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    orig = repo(root, "makerhub2", str(bare))
    subprocess.run(["git", "-C", str(orig), "push", "-q", "origin", "main"], check=True)
    head = subprocess.run(["git", "-C", str(bare), "rev-parse", "main"], capture_output=True, text=True).stdout
    for name in ("t3-off", "t3-plan"):
        subprocess.run(["git", "clone", "-q", str(bare), str(root / name)], check=True)
        (root / name / f"{name}.txt").write_text(name + "\n")
    out = ws.git_commands("t3-off")
    assert out["copy_of"] == "makerhub2" and out["push_branch"] == "t3-off"
    assert out["push"].endswith("git push -u origin HEAD:t3-off")
    for name in ("t3-off", "t3-plan"):
        subprocess.run(ws.git_commands(name)["push"], shell=True, check=True, capture_output=True)
    def show(ref, path):
        return subprocess.run(["git", "-C", str(bare), "show", f"{ref}:{path}"], capture_output=True, text=True)
    assert show("t3-off", "t3-off.txt").stdout == "t3-off\n"
    assert show("t3-plan", "t3-plan.txt").stdout == "t3-plan\n"
    assert subprocess.run(["git", "-C", str(bare), "rev-parse", "main"], capture_output=True, text=True).stdout == head
    # The original still pushes its own branch, as before
    plain = ws.git_commands("makerhub2")
    assert plain["copy_of"] is None and plain["push"].endswith("git push -u origin HEAD")
    # Pull on another computer: the copy's branch, in a folder named like the copy
    elsewhere = tmp_path / "laptop"
    elsewhere.mkdir()
    subprocess.run(out["pull"], shell=True, check=True, cwd=elsewhere, capture_output=True)
    assert (elsewhere / "t3-off" / "t3-off.txt").read_text() == "t3-off\n"
    subprocess.run(out["pull"], shell=True, check=True, cwd=elsewhere, capture_output=True)   # then: pull


def test_github_copy_one_liners(root, monkeypatch):
    repo(root, "T3-Off", "https://github.com/optiware-ai/makerhub2.git")
    repo(root, "Makerhub2", "https://github.com/optiware-ai/makerhub2.git")      # same name, other case
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "SECRET")
    out = ws.git_commands("T3-Off")
    assert out["push"].endswith("push -u origin HEAD:T3-Off'")
    assert out["pull"] == ("if [ -d T3-Off/.git ]; then git -C T3-Off pull; "
                           "else git clone -b T3-Off https://github.com/optiware-ai/makerhub2.git T3-Off; fi")
    assert ws.git_commands("Makerhub2")["copy_of"] is None


def test_a_folder_that_cant_be_a_branch_pushes_as_before(root):
    repo(root, "copy..1", "git@github.com:me/tool.git")         # '..' is not allowed in a branch name
    out = ws.git_commands("copy..1")
    assert out["copy_of"] is None and out["push"].endswith("git push -u origin HEAD")
