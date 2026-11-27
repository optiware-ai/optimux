"""install-tui.sh: the terminal client's own installer (offline checks: a fake
release made of a local file stands in for GitHub)."""
import hashlib
import os
import re
import shutil
import subprocess
import tarfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "install-tui.sh")


def bash(script, **env):
    e = dict(os.environ, NO_COLOR="1", **env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e, timeout=60, stdin=subprocess.DEVNULL)


def lib(script, **env):
    return bash(f'OPTIMUX_LIB_ONLY=1 source "{SCRIPT}"; {script}', **env)


def test_syntax():
    assert subprocess.run(["bash", "-n", SCRIPT]).returncode == 0


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    r = subprocess.run(["shellcheck", "-x", "-S", "warning", SCRIPT], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_help_and_bad_option():
    r = bash(f"bash {SCRIPT} --help")
    assert r.returncode == 0 and "--from-source" in r.stdout and "--uninstall" in r.stdout
    assert bash(f"bash {SCRIPT} --bogus").returncode == 2


def test_runs_only_on_the_last_line():
    """A download cut off halfway must not run anything: every statement is in a
    function, and the only top-level call is the last line."""
    lines = [l for l in open(SCRIPT).read().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert lines[-1] == 'if [[ "${OPTIMUX_LIB_ONLY:-}" != 1 ]]; then main "$@"; fi'
    top = [l for l in lines[:-1] if not l.startswith((" ", "\t", "}")) and "()" not in l and not re.match(r"^[A-Z_]+=", l)]
    assert top == [], top


@pytest.mark.parametrize("os_,arch,want", [
    ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
    ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
    ("Darwin", "arm64", "aarch64-apple-darwin"),
    ("Darwin", "x86_64", "x86_64-apple-darwin"),
    ("Linux", "riscv64", ""),
    ("FreeBSD", "amd64", ""),
])
def test_target_for(os_, arch, want):
    r = lib(f"target_for {os_} {arch}")
    assert r.returncode == 0 and r.stdout.strip() == want


def test_version_ge():
    r = lib('version_ge 1.86.0 1.85 && echo a; version_ge 1.85.0 1.85 && echo b; version_ge 1.84.1 1.85 || echo c; '
            'version_ge 2.39 2.35 && echo d; version_ge 2.31 2.35 || echo e')
    assert r.stdout.split() == ["a", "b", "c", "d", "e"]


def fake_release(tmp_path, target, body=b"#!/bin/sh\necho 'optimux-tui 9.9.9'\n", bad_sum=False):
    """A release folder like GitHub's: <tag>/<name>.tar.gz and its .sha256."""
    tag = "v9.9.9"
    name = f"optimux-tui-{tag}-{target}"
    d = tmp_path / "rel" / tag
    (d / name).mkdir(parents=True)
    exe = d / name / "optimux-tui"
    exe.write_bytes(body)
    exe.chmod(0o755)
    tgz = d / f"{name}.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(d / name, arcname=name)
    digest = "0" * 64 if bad_sum else hashlib.sha256(tgz.read_bytes()).hexdigest()
    (d / f"{name}.tar.gz.sha256").write_text(f"{digest}  {name}.tar.gz\n")
    return tmp_path / "rel"


def this_target():
    r = lib('target_for "$(uname -s)" "$(uname -m)"')
    return r.stdout.strip()


needs_target = pytest.mark.skipif(not this_target(), reason="no ready-made build for this machine")


@needs_target
def test_installs_a_checked_download(tmp_path):
    rel = fake_release(tmp_path, this_target())
    dest = tmp_path / "bin"
    r = bash(f"bash {SCRIPT} --version v9.9.9 --dir {dest}", OPTIMUX_TUI_DOWNLOAD=f"file://{rel}", HOME=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "Checksum (SHA-256) checked" in r.stdout and "optimux-tui 9.9.9" in r.stdout
    assert oct((dest / "optimux-tui").stat().st_mode & 0o777) == "0o755"
    # uninstall removes the command, never the sign-in folder
    conf = tmp_path / "conf"
    conf.mkdir()
    r = bash(f"bash {SCRIPT} --uninstall --dir {dest}", OPTIMUX_TUI_HOME=str(conf))
    assert r.returncode == 0 and not (dest / "optimux-tui").exists() and conf.exists()


@needs_target
def test_a_wrong_checksum_installs_nothing(tmp_path):
    rel = fake_release(tmp_path, this_target(), bad_sum=True)
    dest = tmp_path / "bin"
    r = bash(f"bash {SCRIPT} --version v9.9.9 --dir {dest}", OPTIMUX_TUI_DOWNLOAD=f"file://{rel}", HOME=str(tmp_path))
    assert r.returncode != 0 and "checksum doesn't match" in r.stderr
    assert not (dest / "optimux-tui").exists()


@needs_target
def test_a_build_that_does_not_run_falls_back_to_the_source(tmp_path):
    """A ready-made build this system can't run (an old glibc) is not installed: the
    source build takes over (here it stops early: no git)."""
    rel = fake_release(tmp_path, this_target(), body=b"#!/bin/sh\nexit 1\n")
    dest = tmp_path / "bin"
    r = bash(f'PATH=/usr/bin:/bin; git() {{ return 127; }}; command() {{ [[ "$2" == git ]] && return 1; builtin command "$@"; }}; '
             f'export -f git command; bash {SCRIPT} --version v9.9.9 --dir {dest}',
             OPTIMUX_TUI_DOWNLOAD=f"file://{rel}", HOME=str(tmp_path))
    assert "doesn't run on this computer" in r.stderr
    assert not (dest / "optimux-tui").exists()
