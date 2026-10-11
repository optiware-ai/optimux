"""Sandbox (bubblewrap) for model-run commands."""
import asyncio
import os

import pytest

from optimux_backend.core import executor, paths, sandbox


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    """HOME with an Optimux folder holding a 'secret', and a workspace with two projects."""
    home = tmp_path / "home"
    cm = home / ".optimux"
    (cm / "skills" / "deploy").mkdir(parents=True)
    (cm / "skills" / "deploy" / "SKILL.md").write_text("skill")
    (cm / "secrets.env").write_text("GEMINI_API_KEY=very-secret\n")
    (home / ".gitconfig").write_text("[user]\n\tname = Me\n")
    (home / "ws" / "proj").mkdir(parents=True)
    (home / "ws" / "other").mkdir()
    (home / "notes.txt").write_text("private")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(paths, "HOME", str(cm))
    monkeypatch.setattr(paths, "SKILLS_DIR", str(cm / "skills"))
    monkeypatch.delenv(sandbox.REQUIRE_ENV, raising=False)
    sandbox._STATUS = None
    return home


def _idx(argv, *item):
    for i in range(len(argv) - len(item) + 1):
        if tuple(argv[i:i + len(item)]) == item:
            return i
    return -1


# ---------- command line

def test_argv_hides_homes_then_mounts_workdir(fake_home):
    wd = str(fake_home / "ws" / "proj")
    a = sandbox.build_argv("ls", wd, "/bin/bash", bwrap="bwrap")
    assert a[:5] == ["bwrap", "--ro-bind", "/", "/", "--dev"]
    assert 0 < _idx(a, "--tmpfs", str(fake_home)) < _idx(a, "--bind", wd, wd)
    assert _idx(a, "--ro-bind", str(fake_home / ".gitconfig"), sandbox.SANDBOX_HOME + "/.gitconfig") > 0
    assert _idx(a, "--setenv", "HOME", sandbox.SANDBOX_HOME) > 0
    assert _idx(a, "--remount-ro", str(fake_home)) > _idx(a, "--bind", wd, wd)        # after the mounts inside it
    assert _idx(a, "--ro-bind", paths.SKILLS_DIR, paths.SKILLS_DIR) > _idx(a, "--bind", wd, wd)
    assert _idx(a, "--tmpfs", "/run") > 0 and "--die-with-parent" in a and "--new-session" in a
    assert "--unshare-net" not in a and a[-3:] == ["/bin/bash", "-c", "ls"]
    assert "--unshare-net" in sandbox.build_argv("ls", wd, "/bin/bash", network=False)


def test_optimux_folder_hidden_again_when_inside_workdir(fake_home):
    a = sandbox.build_argv("ls", str(fake_home), "/bin/bash")
    b = _idx(a, "--bind", str(fake_home), str(fake_home))
    assert b > 0 and _idx(a[b:], "--tmpfs", paths.HOME) > 0


def test_extra_ro_paths(fake_home, tmp_path):
    lib = tmp_path / "shared-lib"
    lib.mkdir()
    assert sandbox.check_ro_path(str(lib)) == str(lib)
    a = sandbox.build_argv("ls", str(fake_home / "ws" / "proj"), "/bin/bash", ro_paths=[str(lib)])
    assert _idx(a, "--ro-bind", str(lib), str(lib)) > 0
    for bad in ("relative/path", str(tmp_path / "missing"), str(fake_home), paths.HOME, "/"):
        with pytest.raises(sandbox.SandboxError):
            sandbox.check_ro_path(bad)


# ---------- availability, fail closed

def test_status_without_bwrap(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    st = sandbox.status(refresh=True)
    assert st["works"] is False and "apt install bubblewrap" in st["error"]
    sandbox._STATUS = None


def test_required_env_forces_on(monkeypatch):
    monkeypatch.setenv(sandbox.REQUIRE_ENV, "1")
    assert sandbox.enabled({"sandbox": False})
    monkeypatch.delenv(sandbox.REQUIRE_ENV)
    assert not sandbox.enabled({}) and sandbox.enabled({"sandbox": True})


def test_refuses_when_on_but_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(executor, "get_settings", lambda: {"sandbox": True})
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": False, "error": "not installed", "path": None})
    r = asyncio.run(executor.run_shell_command("touch hacked", cwd=str(tmp_path)))
    assert r["exit_code"] == 126 and "Refused" in r["stderr"] and not (tmp_path / "hacked").exists()


def test_cannot_enable_without_working_bwrap(monkeypatch):
    from optimux_backend.core.config_manager import SettingsPayload, save_settings
    from optimux_backend.core.security import ValidationError
    monkeypatch.setattr(sandbox, "status", lambda refresh=False: {"works": False, "error": "not installed"})
    with pytest.raises(ValidationError, match="Can't turn the sandbox on"):
        save_settings(SettingsPayload(sandbox=True))


# ---------- real runs (only where bubblewrap works)

needs_bwrap = pytest.mark.skipif(not sandbox.status(refresh=True)["works"], reason="bubblewrap not available here")


def _run(monkeypatch, cmd, cwd, network=True):
    monkeypatch.setattr(executor, "get_settings", lambda: {"sandbox": True, "sandbox_network": network})
    return asyncio.run(executor.run_shell_command(cmd, cwd=cwd))


@needs_bwrap
def test_sandbox_really_isolates(monkeypatch, fake_home):
    sandbox._STATUS = None
    proj = str(fake_home / "ws" / "proj")
    r = _run(monkeypatch, "echo ok > made.txt && cat made.txt", proj)
    assert r["exit_code"] == 0 and (fake_home / "ws" / "proj" / "made.txt").read_text() == "ok\n"
    r = _run(monkeypatch, "cat ~/.optimux/secrets.env; cat ~/notes.txt", proj)
    assert "very-secret" not in r["stdout"] and "private" not in r["stdout"] and r["exit_code"] != 0
    assert _run(monkeypatch, "touch ../other/x", proj)["exit_code"] != 0 and not (fake_home / "ws" / "other" / "x").exists()
    assert "Read-only" in _run(monkeypatch, "touch /usr/optimux-test", proj)["stderr"]
    assert _run(monkeypatch, "cat ~/.gitconfig", proj)["stdout"].startswith("[user]")
    assert _run(monkeypatch, f"cat {fake_home}/.optimux/skills/deploy/SKILL.md", proj)["stdout"] == "skill"
    assert int(_run(monkeypatch, "wc -l < /proc/net/dev", proj, network=False)["stdout"]) == 3   # loopback only


@needs_bwrap
def test_workspace_at_home_still_hides_secrets(monkeypatch, fake_home):
    sandbox._STATUS = None
    r = _run(monkeypatch, f"ls -A {fake_home}/.optimux; cat {fake_home}/.optimux/secrets.env", str(fake_home))
    assert "very-secret" not in r["stdout"] and r["stdout"].split() == ["skills"]


@needs_bwrap
def test_writes_outside_the_project_fail_loudly(monkeypatch, fake_home):
    """Writes to the hidden home used to 'succeed' into a throwaway folder and
    vanish (a model then saw `git init` work and the folder missing). Now they fail."""
    sandbox._STATUS = None
    proj = str(fake_home / "ws" / "proj")
    r = _run(monkeypatch, f"mkdir -p {fake_home}/ws/new-project && git init {fake_home}/ws/new-project", proj)
    assert r["exit_code"] != 0 and "Read-only" in r["stderr"] and not (fake_home / "ws" / "new-project").exists()
    assert "only the working folder" in r["stderr"]                  # the explanation for the model
    r = _run(monkeypatch, f"touch {fake_home}/.optimux/x", str(fake_home))     # Optimux's folder re-hidden inside the workspace
    assert r["exit_code"] != 0
    # Tool caches still work (scratch HOME), and the project itself is writable
    r = _run(monkeypatch, "mkdir -p ~/.cache/pip && echo hi > ~/.cache/pip/x && cat ~/.cache/pip/x && echo $HOME && touch here", proj)
    assert r["exit_code"] == 0 and "hi" in r["stdout"] and sandbox.SANDBOX_HOME in r["stdout"]
    assert (fake_home / "ws" / "proj" / "here").exists()
    assert _run(monkeypatch, "git config --global user.name", proj)["stdout"].strip() == "Me"
