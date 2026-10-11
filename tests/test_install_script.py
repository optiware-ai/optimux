"""Feature 24: install.sh and the optimux command (static and offline checks;
the full installs are run by hand before each release)."""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(ROOT, "install.sh")
CLI = os.path.join(ROOT, "scripts", "optimux")


def bash(script, **env):
    e = dict(os.environ, NO_COLOR="1", **env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=e, timeout=60, stdin=subprocess.DEVNULL)


def test_syntax():
    for f in (INSTALL, CLI):
        assert subprocess.run(["bash", "-n", f]).returncode == 0, f


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    r = subprocess.run(["shellcheck", "-x", "-S", "warning", INSTALL, CLI], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_help_and_bad_option():
    r = bash(f"bash {INSTALL} --help")
    assert r.returncode == 0 and "--server" in r.stdout and "--uninstall" in r.stdout
    assert bash(f"bash {INSTALL} --bogus").returncode == 2


def test_runs_only_on_the_last_line():
    """A download cut off halfway must not run anything: every statement is in a
    function, and the only top-level call is the last line."""
    lines, end = [], None
    for l in open(INSTALL).read().splitlines():
        if end is not None:                       # inside a here-document: text, not code
            if l.strip() == end:
                end = None
            continue
        m = re.search(r"<<-?\s*'?\"?(\w+)", l)
        if m:
            end = m.group(1)
        if l.strip() and not l.lstrip().startswith("#"):
            lines.append(l)
    assert lines[-1].startswith('if [[ "${OPTIMUX_LIB_ONLY:-}" != 1 ]]; then main "$@"; fi')
    top_level = [l for l in lines if not l.startswith((" ", "\t", "}", ")")) and "()" not in l
                 and not re.match(r"^[A-Z_]+=", l) and not l.startswith(("CM_PROVIDERS", '"', "if [["))]
    assert top_level == [], top_level


def test_no_function_ends_in_a_bare_test():
    """With set -e, a function whose last command is `[[ x ]] && y` returns 1 when
    x is false and stops the installer (this happened twice)."""
    lines = open(INSTALL).read().split("\n")
    bad = []
    for i, line in enumerate(lines):
        if line == "}":
            j = i - 1
            while not lines[j].strip():
                j -= 1
            prev = lines[j].strip()
            if re.match(r"^(\[\[|\(\().*(&&)", prev) and not prev.endswith("|| true"):
                bad.append(f"{j + 1}: {prev}")
    assert bad == []


def test_captured_pipelines_cannot_stop_the_installer():
    """With pipefail, x="$(a | b)" fails when a fails; each one needs `|| true`."""
    bad = [l.strip() for l in open(INSTALL) if re.search(r'="\$\([^)]*[^|]\|[^|]', l)
           and "|| true" not in l and "strip_ansi" not in l]
    assert bad == []


# Tests never reach the real sudo (it would ask for a password when they run as a
# normal user): "sudo cmd" runs cmd unprivileged, and sudo -n / -v succeed silently.
FAKE_SUDO = 'sudo() { case "$1" in -n|-v) return 0 ;; esac; "$@"; }; '


def lib(script, **env):
    return bash(f'OPTIMUX_LIB_ONLY=1 source "{INSTALL}"; tui_init; set -Eeuo pipefail; {FAKE_SUDO}{script}', **env)


def test_library_mode_defines_functions_without_running():
    r = lib("type -t cli_update; type -t main")
    assert r.returncode == 0 and r.stdout.split() == ["function", "function"] and "Step" not in r.stdout


def test_manifest_roundtrip(tmp_path):
    f = tmp_path / "install.env"
    r = lib(f'''CM_SUDO=""; declare -gA M=([MODE]=local [APP_DIR]="/a b/c" [DATA_DIR]=/d [PORT]=8001 [URL]=http://127.0.0.1:8001 [SERVICE]=process)
              manifest_write "{f}"; unset M; manifest_read "{f}"; echo "${{M[APP_DIR]}}|${{M[PORT]}}|${{M[MODE]}}"''')
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/a b/c|8001|local"
    assert oct(os.stat(f).st_mode & 0o777) == "0o600"


def test_manifest_is_read_not_executed(tmp_path):
    f = tmp_path / "install.env"
    marker = tmp_path / "pwned"
    f.write_text(f"MODE=local\n$(touch {marker})\nAPP_DIR=$(touch {marker})\n")
    lib(f'manifest_read "{f}"; echo "${{M[APP_DIR]}}"')
    assert not marker.exists()


@pytest.mark.parametrize("pm,want,expected", [
    ("apt-get", "python git bubblewrap", ["python3", "python3-venv", "git", "bubblewrap"]),
    ("dnf", "python bubblewrap", ["python3", "bubblewrap"]),
    ("pacman", "python caddy", ["python", "caddy"]),
    ("brew", "python bubblewrap", ["python"]),
])
def test_package_names(pm, want, expected):
    r = lib(f"CM_PM={pm}; packages_for {want}")
    assert r.stdout.split() == expected


def test_ssh_port_guess_is_flagged():
    r = lib('CM_SUDO=""; ss() { return 1; }; sshd() { return 1; }; unset SSH_CONNECTION; ssh_ports; echo "$CM_SSH_PORTS|${CM_SSH_SURE:-no}"')
    assert r.stdout.strip() == "22|no"
    r = lib('CM_SUDO=""; ss() { return 1; }; sshd() { return 1; }; SSH_CONNECTION="1.2.3.4 5555 10.0.0.1 2222"; ssh_ports; echo "$CM_SSH_PORTS|${CM_SSH_SURE:-no}"')
    assert r.stdout.strip() == "2222|1"


def test_card_lines_line_up():
    r = lib('card "Title" "short" "a much longer line here" "${CB}bold${C0} text"', TERM="xterm-256color")
    rows = [l for l in r.stdout.splitlines() if l.strip()]
    widths = {len(re.sub(r"\x1b\[[0-9;]*m", "", l)) for l in rows}
    assert len(widths) == 1, rows


def test_secrets_never_go_through_run_or_arguments():
    """Passwords and keys reach Optimux on stdin only, never as arguments or in the log."""
    text = open(INSTALL).read()
    assert "admin set-password" in text and "admin set-key" in text and "admin set-github" in text
    for line in text.splitlines():
        if any(f"admin {c}" in line for c in ("set-password", "set-key", "set-github")):
            assert re.search(r"printf '%s\\n' \"\$(p1|key)\" \|", line), line
        assert not re.search(r"\brun\b.*\$(p1|p2|key)\b", line), line


def test_cli_without_install_says_so(tmp_path):
    r = bash(f"HOME={tmp_path} XDG_CONFIG_HOME={tmp_path}/cfg OPTIMUX_ETC={tmp_path}/etc bash {CLI} status")
    assert r.returncode == 1 and "isn't installed" in (r.stdout + r.stderr)


@pytest.mark.parametrize("path,deleted", [("/", False), ("$HOME", False), ("/var/lib", False), ("/tmp/x/notours", False),
                                         ("/tmp/x/optimux", True), ("/tmp/x/.optimux", True)])
def test_uninstall_deletes_only_optimux_folders(tmp_path, path, deleted):
    target = path.replace("/tmp/x", str(tmp_path))
    if target.startswith(str(tmp_path)):
        os.makedirs(target, exist_ok=True)
    r = lib(f'CM_SUDO=""; rm() {{ echo "RM $*"; }}; safe_rm "{target}" || true', HOME=str(tmp_path / "home"))
    assert ("RM -rf --" in r.stdout) == deleted, r.stdout + r.stderr


def test_damaged_manifest_is_ignored(tmp_path):
    f = tmp_path / "install.env"
    f.write_text("APP_DIR=/\nMODE=weird\n")
    r = lib(f'manifest_read "{f}" && echo TRUSTED || echo IGNORED')
    assert "IGNORED" in r.stdout


@pytest.mark.parametrize("args,ok", [("--domain https://Code.Example.com/x", True), ("--domain 'a b'", False),
                                     ("--port 80", False), ("--port abc", False), ("--port 8080", True)])
def test_option_validation(args, ok):
    r = bash(f'OPTIMUX_LIB_ONLY=1 source "{INSTALL}"; parse_args {args} && echo "OK $CM_DOMAIN"')
    assert (r.returncode == 0 and "OK" in r.stdout) == ok, r.stdout + r.stderr
    if ok and "Code.Example" in args:
        assert "OK code.example.com" in r.stdout


@pytest.mark.parametrize("content,example", [
    (":80 {\n\troot * /usr/share/caddy\n\tfile_server\n}\n", True),
    ("# comment\nimport Caddyfile.d/*.caddyfile\n\n:80 {\n  root * /usr/share/caddy\n  file_server\n}\n", True),
    ("shop.example.com {\n  reverse_proxy :3000\n}\n", False),
])
def test_caddy_example_detection(tmp_path, content, example):
    f = tmp_path / "Caddyfile"
    f.write_text(content)
    script = open(INSTALL).read().replace("/etc/caddy/Caddyfile", str(f))
    s = tmp_path / "i.sh"
    s.write_text(script)
    r = bash(f'OPTIMUX_LIB_ONLY=1 source "{s}"; tui_init; caddy_is_example && echo EX || echo CUSTOM')
    assert ("EX" in r.stdout) == example, r.stdout + r.stderr


# ---------------------------------------------------------------- Feature 25: GitHub

@pytest.mark.parametrize("pm,pkg", [("apt-get", "gh"), ("dnf", "gh"), ("pacman", "github-cli"), ("brew", "gh"), ("", "")])
def test_gh_package_names(pm, pkg):
    assert lib(f"CM_PM={pm}; gh_package").stdout.strip() == pkg


@pytest.fixture(scope="module")
def no_gh_path(tmp_path_factory):
    """A PATH with every program except gh (it may be installed where the tests run),
    an apt-get that does nothing, and a sudo that never asks for a password."""
    bin_dir = tmp_path_factory.mktemp("nogh") / "bin"
    bin_dir.mkdir()
    for d in os.environ["PATH"].split(os.pathsep):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            target, link = os.path.join(d, name), bin_dir / name
            if name not in ("gh", "apt-get", "sudo") and not link.exists() and os.access(target, os.X_OK) and os.path.isfile(target):
                link.symlink_to(target)
    for name in ("apt-get", "sudo"):    # reached through `env`, which can't see shell functions
        fake = bin_dir / name
        fake.write_text("#!/bin/sh\nexit 0\n" if name == "apt-get" else
                        '#!/bin/sh\ncase "$1" in -n|-v) exit 0 ;; esac\nexec "$@"\n')
        fake.chmod(0o755)
    return str(bin_dir)


def test_installing_gh_is_optional(tmp_path, no_gh_path):
    """A failed gh install is a warning; the installer carries on."""
    log = tmp_path / "log"
    r = lib(f'''CM_LOG="{log}"; CM_SUDO=""; CM_PM=apt-get; apt-get() {{ :; }}; apt-cache() {{ echo "  Candidate: 2.4.0"; }}
              pm_install() {{ echo "E: no gh" >&2; return 1; }}
              install_gh && echo INSTALLED || echo "CARRIED ON"''', PATH=no_gh_path)
    assert "CARRIED ON" in r.stdout and "Couldn't install gh" in r.stdout, r.stdout + r.stderr


def test_github_setup_with_yes_never_asks(tmp_path):
    r = lib('''CM_YES=1; install_gh() { echo GH-INSTALL; }; ask_secret() { echo ASKED; }
              configure_github; echo END''')
    assert "GH-INSTALL" in r.stdout and "ASKED" not in r.stdout and "END" in r.stdout and "Dashboard → GitHub" in r.stdout


def test_github_token_goes_to_optimux_on_stdin_only(tmp_path):
    """The token reaches `admin set-github` on stdin; it isn't printed or passed as an argument."""
    seen = tmp_path / "seen"
    r = lib(f'''CM_YES=""; CM_TTY=1; install_gh() {{ :; }}; confirm() {{ return 0; }}
              ask_secret() {{ REPLY="ghp_SECRETSECRETSECRETSECRET"; }}
              admin() {{ echo "ARGS $*" >"{seen}"; cat >>"{seen}"; echo '{{"login": "octo", "warning": "", "identity_set": true}}'; }}
              configure_github''')
    assert r.returncode == 0, r.stderr
    assert "signed in as @octo" in r.stdout and "private address" in r.stdout
    assert "SECRET" not in r.stdout + r.stderr
    assert seen.read_text() == "ARGS set-github\nghp_SECRETSECRETSECRETSECRET\n"


def test_github_token_refused_then_skipped(tmp_path):
    r = lib('''CM_YES=""; CM_TTY=1; install_gh() { :; }; n=0
              confirm() { n=$((n + 1)); [[ $n == 1 ]]; }       # yes to "Set up", no to "Try another"
              ask_secret() { REPLY="ghp_wrongwrongwrongwrongwrong"; }
              admin() { cat >/dev/null; echo "GitHub refused the token: it's wrong, expired or revoked." >&2; return 1; }
              configure_github; echo END''')
    assert "refused the token" in r.stdout and "END" in r.stdout and "wrongwrong" not in r.stdout


def test_cli_lists_github():
    text = open(CLI).read()
    assert "github) cli_github ;;" in text and "${CB}github${C0}" in text


@pytest.mark.parametrize("policy,adds_repo", [("gh:\n  Installed: (none)\n  Candidate: 2.45.0-1ubuntu0.3\n", False),
                                              ("gh:\n  Installed: (none)\n  Candidate: (none)\n", True), ("", True)])
def test_gh_repo_only_when_apt_has_no_gh(tmp_path, no_gh_path, policy, adds_repo):
    r = lib(f'''CM_LOG="{tmp_path}/log"; CM_SUDO=""; CM_PM=apt-get; apt-get() {{ :; }}
              apt-cache() {{ printf "{policy}"; for i in $(seq 2000); do echo "  500 http://archive.ubuntu.com/ubuntu noble/universe amd64 Packages"; done; }}
              add_gh_repo() {{ echo ADD-REPO; }}; pm_install() {{ echo "INSTALL $*"; }}; confirm() {{ return 0; }}
              install_gh''', PATH=no_gh_path)
    assert ("ADD-REPO" in open(f"{tmp_path}/log").read()) == adds_repo, r.stdout + r.stderr


def test_no_third_party_repo_unattended(tmp_path, no_gh_path):
    r = lib(f'''CM_LOG="{tmp_path}/log"; CM_SUDO=""; CM_YES=1; CM_PM=apt-get; apt-cache() {{ :; }}
              add_gh_repo() {{ echo ADD-REPO; }}; pm_install() {{ echo INSTALL; }}
              install_gh || echo SKIPPED''', PATH=no_gh_path)
    assert "SKIPPED" in r.stdout and "ADD-REPO" not in r.stdout + open(f"{tmp_path}/log").read()


def test_a_failed_gh_repo_is_removed_again(tmp_path, no_gh_path):
    """If the key can't be downloaded, nothing is written; if apt then fails, the
    source and key are removed, so apt keeps working."""
    etc = tmp_path / "etc"
    script = open(INSTALL).read().replace("/etc/apt/", f"{etc}/apt/")
    s = tmp_path / "i.sh"
    s.write_text(script)
    (etc / "apt" / "sources.list.d").mkdir(parents=True)
    common = f'OPTIMUX_LIB_ONLY=1 source "{s}"; tui_init; set -Eeuo pipefail; {FAKE_SUDO}CM_SUDO=""; dpkg() {{ echo amd64; }}; '
    r = bash(common + 'curl() { return 22; }; add_gh_repo && echo ADDED || echo FAILED', PATH=no_gh_path)
    assert "FAILED" in r.stdout and not (etc / "apt" / "sources.list.d" / "github-cli.list").exists()
    r = bash(common + 'curl() { while [[ $1 != -o ]]; do shift; done; echo KEY >"$2"; }; '
                      'env() { return 100; }; add_gh_repo && echo ADDED || echo FAILED', PATH=no_gh_path)
    assert "FAILED" in r.stdout, r.stdout + r.stderr
    assert not (etc / "apt" / "sources.list.d" / "github-cli.list").exists() and not (etc / "apt" / "keyrings" / "githubcli-archive-keyring.gpg").exists()


def test_tests_cannot_reach_the_real_sudo(no_gh_path):
    """As a normal user, install_gh switches to sudo; in tests that must be the fake one."""
    r = lib('install_gh() { local CM_SUDO=sudo; type -t sudo; $CM_SUDO echo RAN; }; install_gh')
    assert r.stdout.split() == ["function", "RAN"]
    assert open(os.path.join(no_gh_path, "sudo")).read().startswith("#!/bin/sh")


# ---------- Feature 27: installs from before the rename (CodeMux)

def old_local_install(tmp_path, service="process"):
    """A local install made as CodeMux, with the new code already downloaded into it."""
    home = tmp_path / "home"
    app = home / ".local/share/codemux"
    (app / "optimux_backend").mkdir(parents=True)
    (app / "scripts").mkdir()
    (app / ".git").mkdir()
    (app / ".venv/bin").mkdir(parents=True)
    (app / ".venv/bin/python").write_text("old venv")
    (home / ".codemux/config").mkdir(parents=True)
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/codemux").write_text("old command")
    cfg = home / ".config/codemux"
    cfg.mkdir(parents=True)
    (cfg / "install.env").write_text(f"MODE=local\nAPP_DIR={app}\nDATA_DIR={home}/.codemux\nWORKSPACE={home}/codemux-projects\n"
                                     f"PORT=8000\nURL=http://127.0.0.1:8000\nSERVICE={service}\nREF=main\n")
    return home


def run_upgrade(home, venv_ok=True, **env):
    make_venv = 'find_python() { CM_PY=python3; }; make_venv_only() { mkdir -p "$1/.venv/bin"; echo new >"$1/.venv/bin/python"; }' \
        if venv_ok else 'find_python() { CM_PY=python3; }; make_venv_only() { mkdir -p "$1/.venv"; return 1; }'
    return lib(f'''CM_SUDO=""; CM_LOG=/dev/null; svc() {{ echo "SVC $1 $(nm)" >>"$HOME/calls"; [[ "$1" != is-active ]]; }}
                 systemctl() {{ echo "SYSTEMCTL $*" >>"$HOME/calls"; }}; {make_venv}
                 find_install && echo "FOUND ${{M[NAME]}}"; upgrade_old_install; echo "NOW ${{M[NAME]}} ${{M[APP_DIR]}} ${{M[DATA_DIR]}}"''',
               HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), OPTIMUX_ETC=str(home / "etc"),
               OPTIMUX_OLD_ETC=str(home / "oldetc"), **env)


def test_old_manifest_is_found_as_codemux(tmp_path):
    home = old_local_install(tmp_path)
    r = lib('find_install && echo "NAME=${M[NAME]}"', HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
            OPTIMUX_ETC=str(home / "etc"), OPTIMUX_OLD_ETC=str(home / "oldetc"))
    assert "NAME=codemux" in r.stdout, r.stdout + r.stderr


def test_old_local_install_moves_to_the_new_names(tmp_path):
    home = old_local_install(tmp_path)
    r = run_upgrade(home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FOUND codemux" in r.stdout
    assert f"NOW optimux {home}/.local/share/optimux {home}/.optimux" in r.stdout
    assert "SVC stop codemux" in (home / "calls").read_text()  # the old service was stopped by its old name
    assert (home / ".local/share/optimux/.venv/bin/python").read_text().strip() == "new"
    assert (home / ".local/share/optimux/.git").is_dir() and (home / ".local/share/optimux/optimux_backend").is_dir()
    assert not (home / ".local/share/codemux").exists() and not (home / ".codemux").exists()
    assert (home / ".optimux/config").is_dir()
    assert not (home / ".local/bin/codemux").exists()
    assert "scripts/optimux" in (home / ".local/bin/optimux").read_text()
    assert not (home / ".config/codemux").exists()
    manifest = (home / ".config/optimux/install.env").read_text()
    assert "NAME=optimux" in manifest and f"APP_DIR={home}/.local/share/optimux" in manifest
    assert f"WORKSPACE={home}/codemux-projects" in manifest    # the projects folder never moves
    r = run_upgrade(home)                                      # next time: nothing to do
    assert "FOUND optimux" in r.stdout and "CodeMux is now Optimux" not in r.stdout


def test_failed_environment_puts_the_program_back(tmp_path):
    home = old_local_install(tmp_path)
    r = run_upgrade(home, venv_ok=False)
    assert r.returncode == 0, r.stdout + r.stderr
    app = home / ".local/share/codemux"
    assert (app / ".venv/bin/python").read_text() == "old venv"   # the install is as it was
    assert (app / "optimux_backend").is_dir() and (app / ".git").is_dir()
    assert not (home / ".local/share/optimux").exists()
    assert f"APP_DIR={app}" in (home / ".config/optimux/install.env").read_text()


def test_nothing_moves_before_the_new_code_is_there(tmp_path):
    home = old_local_install(tmp_path)
    (home / ".local/share/codemux/optimux_backend").rmdir()   # still the old version
    r = run_upgrade(home)
    assert "NOW codemux" in r.stdout and (home / ".codemux").is_dir() and (home / ".local/bin/codemux").exists()


def test_user_service_is_renamed(tmp_path):
    home = old_local_install(tmp_path, service="systemd-user")
    units = home / ".config/systemd/user"
    units.mkdir(parents=True)
    (units / "codemux.service").write_text("old")
    run_upgrade(home)
    calls = (home / "calls").read_text()
    assert "SYSTEMCTL --user disable codemux.service" in calls and "SYSTEMCTL --user enable optimux.service" in calls
    assert not (units / "codemux.service").exists()
    unit = (units / "optimux.service").read_text()
    assert f"OPTIMUX_HOME={home}/.optimux" in unit and "optimux_backend.main:app" in unit


@pytest.mark.parametrize("name", ["optimux", "codemux"])
def test_server_unit_uses_the_install_name(name):
    r = lib(f'declare -gA M=([APP_DIR]="{ROOT}"); server_unit {name}')
    unit = r.stdout
    assert f"User={name}" in unit and f"Group={name}" in unit and f"RuntimeDirectory={name}" in unit
    assert f"EnvironmentFile=/etc/{name}/{name}.env" in unit and f"ReadWritePaths=/var/lib/{name}" in unit
    assert f"--uds /run/{name}/{name}.sock" in unit and f"/opt/{name}/.venv/bin/uvicorn optimux_backend.main:app" in unit
    if name == "optimux":
        assert unit == open(os.path.join(ROOT, "deploy", "optimux.service")).read()


def test_old_server_keeps_its_names_and_starts_the_new_module(tmp_path):
    units = tmp_path / "units"
    units.mkdir()
    (units / "codemux.service").write_text("ExecStart=/opt/codemux/.venv/bin/uvicorn codemux_backend.main:app --uds /run/codemux/codemux.sock\n")
    app = tmp_path / "opt-codemux"
    (app / "optimux_backend").mkdir(parents=True)
    r = lib(f'''CM_SUDO=""; systemctl() {{ echo "SYSTEMCTL $*"; }}; install_cli() {{ echo "CLI $1"; }}
              declare -gA M=([MODE]=server [NAME]=codemux [APP_DIR]="{app}")
              CM_UNIT_DIR="{units}"; upgrade_old_install; echo "svc=$(nm).service sock=$(server_socket) site=$(caddy_site)"''')
    assert r.returncode == 0, r.stdout + r.stderr
    assert "optimux_backend.main:app --uds /run/codemux/codemux.sock" in (units / "codemux.service").read_text()
    assert "SYSTEMCTL daemon-reload" in r.stdout
    assert "svc=codemux.service sock=/run/codemux/codemux.sock site=/etc/caddy/codemux.caddyfile" in r.stdout


@pytest.mark.parametrize("path", ["/tmp/x/codemux", "/tmp/x/.codemux"])
def test_uninstall_may_delete_old_folders(tmp_path, path):
    target = path.replace("/tmp/x", str(tmp_path))
    os.makedirs(target)
    r = lib(f'CM_SUDO=""; rm() {{ echo "RM $*"; }}; safe_rm "{target}"', HOME=str(tmp_path / "home"))
    assert "RM -rf --" in r.stdout



def test_nothing_moves_while_the_server_still_runs(tmp_path):
    home = old_local_install(tmp_path)
    r = lib('''CM_SUDO=""; CM_LOG=/dev/null; svc() { [[ "$1" == is-active ]]; }
              find_python() { CM_PY=python3; }; make_venv_only() { mkdir -p "$1/.venv"; }
              find_install; upgrade_old_install; echo "NOW ${M[NAME]}"''',
            HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), OPTIMUX_ETC=str(home / "etc"),
            OPTIMUX_OLD_ETC=str(home / "oldetc"))
    assert "NOW codemux" in r.stdout and "didn't stop" in r.stdout, r.stdout + r.stderr
    assert (home / ".local/share/codemux/.venv/bin/python").exists() and (home / ".codemux").is_dir()
    assert not (home / ".local/share/optimux").exists()


def test_data_already_moved_by_the_server_is_used(tmp_path):
    home = old_local_install(tmp_path)
    (home / ".codemux").rename(home / ".optimux")
    run_upgrade(home)
    assert f"DATA_DIR={home}/.optimux" in (home / ".config/optimux/install.env").read_text()


# ---------- The optimux command right after a local install (~/.local/bin not in PATH yet)

def path_hint_in(home, path, **extra):
    return lib(f'confirm() {{ echo "ASKED $1"; return 0; }}; path_hint; echo "LATER=${{CM_PATH_LATER:-}}"',
               HOME=str(home), PATH=path, SHELL="/bin/bash", **extra)


def test_new_terminals_find_the_command_even_with_ubuntus_profile(tmp_path):
    """Ubuntu's ~/.profile mentions ~/.local/bin but only adds it at the next login."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text('if [ -d "$HOME/.local/bin" ] ; then PATH="$HOME/.local/bin:$PATH"; fi\n')
    r = path_hint_in(home, "/usr/bin:/bin")
    assert "ASKED Add ~/.local/bin to .bashrc?" in r.stdout and "LATER=1" in r.stdout, r.stdout + r.stderr
    assert 'export PATH="$HOME/.local/bin:$PATH"' in (home / ".bashrc").read_text()
    path_hint_in(home, "/usr/bin:/bin")                      # a second install doesn't add it twice
    assert (home / ".bashrc").read_text().count("Added by the Optimux installer") == 1


def test_nothing_to_do_when_the_command_is_already_found(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = path_hint_in(home, f"{home}/.local/bin:/usr/bin:/bin")
    assert "ASKED" not in r.stdout and "LATER=" in r.stdout and "LATER=1" not in r.stdout
    assert not (home / ".bashrc").exists()


@pytest.mark.parametrize("later", ["1", ""])
def test_final_card_says_how_to_use_the_command_in_this_terminal(tmp_path, later):
    r = lib(f'''CM_PATH_LATER="{later}"; CM_PW_SET=1; CM_YES=1; CM_NO_START=1; CM_LOG=/dev/null; CM_OS=Linux
              declare -gA M=([URL]=http://127.0.0.1:8000 [APP_DIR]=/a [DATA_DIR]=/d); finish_local''')
    assert r.returncode == 0, r.stderr
    assert ('export PATH="$HOME/.local/bin:$PATH"' in r.stdout) == bool(later), r.stdout


# ---------- Reaching a server without a domain (SSH tunnel)

@pytest.mark.parametrize("sudo_user,ssh,expected", [
    ("bob", "22", "ssh -N -L 8000:127.0.0.1:8000 bob@203.0.113.7"),
    ("", "2222", "ssh -N -p 2222 -L 8000:127.0.0.1:8000 root@203.0.113.7"),
])
def test_tunnel_command_uses_the_real_login_address_and_ssh_port(sudo_user, ssh, expected):
    r = lib(f'''public_ip() {{ echo 203.0.113.7; }}; ssh_ports() {{ CM_SSH_PORTS="{ssh} "; CM_SSH_SURE=1; }}
              id() {{ echo root; }}; declare -gA M=([PORT]=8000); tunnel_command''', SUDO_USER=sudo_user)
    assert r.stdout.strip() == expected, r.stdout + r.stderr


def test_server_card_and_open_say_where_to_run_the_tunnel():
    common = '''public_ip() { echo 203.0.113.7; }; ssh_ports() { CM_SSH_PORTS="22 "; CM_SSH_SURE=1; }
                declare -gA M=([MODE]=server [PORT]=8000 [URL]=http://127.0.0.1:8000); CM_PW_SET=1; CM_LOG=/dev/null; '''
    card = lib(common + 'finish_server', SUDO_USER="bob").stdout
    assert "On your own computer" in card and "ssh -N -L 8000:127.0.0.1:8000 bob@203.0.113.7" in card
    assert "http://localhost:8000" in card
    opened = lib(common + 'cli_open', SUDO_USER="bob").stdout
    assert "ssh -N -L 8000:127.0.0.1:8000 bob@203.0.113.7" in opened and "http://localhost:8000" in opened
