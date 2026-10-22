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


@pytest.mark.parametrize("ours", [True, False])
def test_caddy_remove_takes_back_only_its_own_site(tmp_path, ours):
    """A reinstall in tunnel mode removes the site a domain setup left."""
    etc = tmp_path / "caddy"
    etc.mkdir()
    site = etc / "optimux.caddyfile"
    site.write_text("optimux.example.com {\n  reverse_proxy unix//run/optimux/optimux.sock\n}\n")
    other = "shop.example.com {\n  reverse_proxy :3000\n}\n"
    if ours:      # the installer replaced the package's example Caddyfile
        (etc / "Caddyfile.before-optimux").write_text(":80 {\n  file_server\n}\n")
        (etc / "Caddyfile").write_text(f"# Written by the Optimux installer (the previous file: x)\nimport {site}\n")
    else:         # the user's own Caddyfile, with a line added
        (etc / "Caddyfile").write_text(f"{other}\n# Added by the Optimux installer\nimport {site}\n")
    script = open(INSTALL).read().replace("/etc/caddy", str(etc))
    s = tmp_path / "i.sh"
    s.write_text(script)
    r = bash(f'OPTIMUX_LIB_ONLY=1 source "{s}"; tui_init; CM_SUDO=""; systemctl() {{ :; }}; '
             'declare -gA M=([NAME]=optimux); caddy_remove && echo DONE')
    assert "DONE" in r.stdout, r.stderr
    assert not site.exists()
    left = (etc / "Caddyfile").read_text()
    assert "optimux" not in left.lower()
    assert left == (":80 {\n  file_server\n}\n" if ours else other + "\n")
    assert not (etc / "Caddyfile.before-optimux").exists()


def test_services_let_optimux_restart_itself_and_update_refreshes_them():
    """Restore's "Restart now": Optimux exits with 75 and systemd starts it again.
    Only units that say so (OPTIMUX_SUPERVISED=1) offer it."""
    unit = open(os.path.join(ROOT, "deploy", "optimux.service")).read()
    assert "Environment=OPTIMUX_SUPERVISED=1" in unit and "RestartForceExitStatus=75" in unit
    script = open(INSTALL).read()
    user = script.split("write_user_unit() {", 1)[1].split("\n}\n", 1)[0]
    assert "Environment=OPTIMUX_SUPERVISED=1" in user and "RestartForceExitStatus=75" in user
    update = script.split("cli_update() {", 1)[1].split("\n}\n", 1)[0]
    assert 'install_server_unit "${M[DOMAIN]:-}"' in update and "write_user_unit" in update


def test_tunnel_reinstall_removes_an_earlier_caddy_site():
    body = open(INSTALL).read().split("install_server() {", 1)[1].split("\n}\n", 1)[0]
    assert 'elif [[ -f "$(caddy_site)" ]]; then' in body and "caddy_remove" in body


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


# ---------- Firewall: other public services are listed before ufw is turned on (v0.2)

SS_OUT = """\
tcp   LISTEN 0 4096        0.0.0.0:22       0.0.0.0:*  users:(("sshd",pid=1,fd=3))
tcp   LISTEN 0 4096           [::]:22          [::]:*  users:(("sshd",pid=1,fd=4))
tcp   LISTEN 0 511         0.0.0.0:8080     0.0.0.0:*  users:(("node",pid=2,fd=3))
tcp   LISTEN 0 511               *:80             *:*  users:(("nginx",pid=6,fd=7))
tcp   LISTEN 0 4096      127.0.0.1:8000     0.0.0.0:*  users:(("python3",pid=3,fd=3))
tcp   LISTEN 0 4096          [::1]:631         [::]:*
tcp   LISTEN 0 4096 [fe80::1%eth0]:9000        [::]:*
udp   UNCONN 0 0     127.0.0.53%lo:53       0.0.0.0:*  users:(("systemd-resolve",pid=4,fd=13))
udp   UNCONN 0 0           0.0.0.0:68       0.0.0.0:*  users:(("dhclient",pid=5,fd=6))
udp   UNCONN 0 0           0.0.0.0:51820    0.0.0.0:*
"""
SS_SSH_ONLY = SS_OUT.splitlines()[0] + "\n" + SS_OUT.splitlines()[4] + "\n"


def firewall(tmp_path, ss_out=SS_OUT, yes="", choice=0, domain="", ss_fails=False, ssh_ports=""):
    """configure_firewall with ufw installed but off; ss, prompts and run are fakes."""
    (tmp_path / "ss.txt").write_text(ss_out)
    # ss -Hltunp has a Netid column; ssh_ports' ss -Htlnp (TCP only) doesn't
    fail = "return 1" if ss_fails else ":"
    ss = (f'ss() {{ if [[ "$1" == -Hltunp ]]; then {fail}; cat "{tmp_path}/ss.txt"; '
          f'elif [[ "$1" == -Htlnp ]]; then awk \'$1 == "tcp" {{ $1 = ""; print }}\' "{tmp_path}/ss.txt"; '
          f'else echo "UNEXPECTED ss $*"; return 1; fi; }}')
    if ssh_ports:
        ss += f'; ssh_ports() {{ CM_SSH_PORTS="{ssh_ports}"; CM_SSH_SURE=1; }}'
    return lib(f'''CM_SUDO=""; CM_YES="{yes}"; CM_TTY=1; {ss}
              ufw() {{ echo "Status: inactive"; }}; sshd() {{ return 1; }}
              choose() {{ echo "CHOOSE $1"; CHOICE={choice}; }}; confirm() {{ echo "CONFIRM $1"; return 0; }}
              run() {{ echo "RUN $4"; }}
              configure_firewall "{domain}"''')


def test_public_listeners_skip_loopback_link_local_and_dhcp(tmp_path):
    extra = ("udp UNCONN 0 0 0.0.0.0:41234 0.0.0.0:* users:((\"dhclient\",pid=8,fd=7))\n"
             "tcp LISTEN 0 4096 0.0.0.0:9090 0.0.0.0:* users:((\"systemd\",pid=1,fd=50))\n")
    (tmp_path / "ss.txt").write_text(SS_OUT + extra)
    r = lib(f'CM_SUDO=""; ss() {{ [[ "$1" == -Hltunp ]] || return 1; cat "{tmp_path}/ss.txt"; }}; public_listeners')
    assert r.returncode == 0, r.stderr
    assert r.stdout.split("\n")[:-1] == ["22/tcp sshd", "8080/tcp node", "80/tcp nginx", "51820/udp ", "9090/tcp "]


def test_other_services_can_stay_reachable(tmp_path):
    r = firewall(tmp_path, choice=0)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "8080/tcp  (node)" in r.stdout and "51820/udp\n" in r.stdout and "80/tcp  (nginx)" in r.stdout
    assert "CHOOSE" in r.stdout and "CONFIRM" not in r.stdout          # one question, not two
    run = next(l for l in r.stdout.splitlines() if l.startswith("RUN"))
    for rule in ("ufw allow 22/tcp", "ufw allow 8080/tcp", "ufw allow 80/tcp", "ufw allow 51820/udp"):
        assert rule in run
    assert run.endswith("ufw --force enable")


def test_firewall_can_be_left_off_for_them(tmp_path):
    r = firewall(tmp_path, choice=1)
    out = " ".join(r.stdout.split())                      # messages wrap at the terminal width
    assert "RUN" not in out and "Firewall left off" in out
    assert ("sudo ufw allow 8080/tcp && sudo ufw allow 80/tcp && sudo ufw allow 51820/udp"
            " && sudo ufw allow 22/tcp && sudo ufw enable") in out


def test_they_can_be_blocked_on_purpose(tmp_path):
    r = firewall(tmp_path, choice=2)
    run = next(l for l in r.stdout.splitlines() if l.startswith("RUN"))
    assert "ufw allow 22/tcp" in run and "8080" not in run and "51820" not in run


def test_unattended_install_never_blocks_other_services(tmp_path):
    r = firewall(tmp_path, yes="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RUN" not in r.stdout and "CHOOSE" not in r.stdout
    out = " ".join(r.stdout.split())
    assert "Firewall left off so they keep working" in out and "sudo ufw allow 8080/tcp" in out


def test_only_ssh_listening_keeps_the_old_question(tmp_path):
    r = firewall(tmp_path, ss_out=SS_SSH_ONLY)
    assert "CHOOSE" not in r.stdout and "CONFIRM Turn on the firewall (ufw)?" in r.stdout
    run = next(l for l in r.stdout.splitlines() if l.startswith("RUN"))
    assert "ufw allow 22/tcp" in run and run.endswith("ufw --force enable")


def test_web_ports_are_expected_with_a_domain(tmp_path):
    caddy = SS_SSH_ONLY + "tcp LISTEN 0 4096 *:443 *:* users:((\"caddy\",pid=7,fd=8))\n" \
        + "udp UNCONN 0 0 *:443 *:* users:((\"caddy\",pid=7,fd=9))\n" + SS_OUT.splitlines()[3] + "\n"
    r = firewall(tmp_path, ss_out=caddy, domain="example.com")
    assert "CHOOSE" not in r.stdout, r.stdout
    run = next(l for l in r.stdout.splitlines() if l.startswith("RUN"))
    assert "ufw allow 80/tcp" in run and "ufw allow 443/tcp" in run


def test_printed_command_has_every_rule_the_installer_would_add(tmp_path):
    """Pasting it must never block SSH on a second port, or the site's own HTTPS."""
    r = firewall(tmp_path, yes="1", domain="example.com", ssh_ports="22 2222 ")
    out = " ".join(r.stdout.split())
    assert ("sudo ufw allow 8080/tcp && sudo ufw allow 51820/udp && sudo ufw allow 22/tcp && sudo ufw allow 2222/tcp"
            " && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable") in out, out


def test_no_listeners_at_all_keeps_the_old_question(tmp_path):
    r = firewall(tmp_path, ss_out="", ssh_ports="22 ")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CHOOSE" not in r.stdout and "CONFIRM Turn on the firewall (ufw)?" in r.stdout


@pytest.mark.parametrize("yes", ["1", ""])
def test_services_that_cannot_be_listed(tmp_path, yes):
    r = firewall(tmp_path, yes=yes, ss_fails=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Couldn't list the services" in r.stdout
    if yes:
        assert "RUN" not in r.stdout and "Firewall left off" in r.stdout
    else:
        assert "CONFIRM Turn on the firewall (ufw)?" in r.stdout


# ---------- One name: Optimux (the CodeMux upgrade path was removed in v0.2)

def test_server_unit_uses_the_install_name():
    r = lib(f'declare -gA M=([APP_DIR]="{ROOT}"); server_unit optimux')
    assert r.stdout == open(os.path.join(ROOT, "deploy", "optimux.service")).read()


def test_a_manifest_with_another_name_is_ignored(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".config/optimux"
    cfg.mkdir(parents=True)
    (cfg / "install.env").write_text(f"MODE=local\nNAME=codemux\nAPP_DIR={home}/app\nDATA_DIR={home}/data\n"
                                     "PORT=8000\nSERVICE=process\n")
    r = lib('find_install && echo FOUND || echo NONE', HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
            OPTIMUX_ETC=str(home / "etc"))
    assert "NONE" in r.stdout and "incomplete or damaged" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("path", ["/tmp/x/codemux", "/tmp/x/.codemux"])
def test_uninstall_refuses_folders_that_are_not_optimux(tmp_path, path):
    target = path.replace("/tmp/x", str(tmp_path))
    os.makedirs(target)
    r = lib(f'CM_SUDO=""; rm() {{ echo "RM $*"; }}; safe_rm "{target}"', HOME=str(tmp_path / "home"))
    assert "RM -rf" not in r.stdout and "not an Optimux folder" in r.stdout + r.stderr


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
    ("bob", "22", "ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 bob@203.0.113.7"),
    ("", "2222", "ssh -N -o ExitOnForwardFailure=yes -p 2222 -L 8000:127.0.0.1:8000 root@203.0.113.7"),
])
def test_tunnel_command_uses_the_real_login_address_and_ssh_port(sudo_user, ssh, expected):
    r = lib(f'''public_ip() {{ echo 203.0.113.7; }}; ssh_ports() {{ CM_SSH_PORTS="{ssh} "; CM_SSH_SURE=1; }}
              ssh_listening() {{ return 0; }}; hostname() {{ echo "203.0.113.7 fd00::1"; }}
              id() {{ echo root; }}; declare -gA M=([PORT]=8000); tunnel_plan; echo "$CM_TUNNEL_CMD"''', SUDO_USER=sudo_user)
    assert r.stdout.strip() == expected, r.stdout + r.stderr


def tunnel(script, **env):
    base = '''ssh_ports() { CM_SSH_PORTS="22 "; CM_SSH_SURE=1; }; id() { echo root; }
               declare -gA M=([MODE]=server [PORT]=8000 [URL]=http://127.0.0.1:8000); CM_PW_SET=1; CM_LOG=/dev/null; '''
    r = lib(base + script, **env)
    assert r.returncode == 0, r.stdout + r.stderr
    return " ".join(r.stdout.split())                    # messages wrap at the terminal width


def test_behind_nat_the_command_stays_but_a_note_names_the_own_address():
    """Office network, home router or container: the public address is the gateway's."""
    out = tunnel('''public_ip() { echo 84.18.153.125; }; hostname() { echo "10.227.207.228 172.17.0.1 fd42::1"; }
                    ssh_listening() { return 0; }; tunnel_plan; echo "CMD=$CM_TUNNEL_CMD"; echo "NOTE=$CM_TUNNEL_NOTE"''')
    assert "CMD=ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 root@84.18.153.125" in out     # still right for AWS-style 1:1 NAT
    assert "84.18.153.125 is the address this server uses to reach the internet" in out and "(10.227.207.228, 172.17.0.1)" in out


def test_no_note_when_the_public_address_is_this_servers_own():
    out = tunnel('''public_ip() { echo 203.0.113.7; }; hostname() { echo "203.0.113.7"; }
                    ssh_listening() { return 0; }; tunnel_plan; echo "NOTE=[$CM_TUNNEL_NOTE]"''')
    assert "NOTE=[]" in out


def test_no_public_address_uses_the_servers_own():
    out = tunnel('''public_ip() { :; }; hostname() { echo "10.0.0.5"; }; ssh_listening() { return 0; }
                    tunnel_plan; echo "CMD=$CM_TUNNEL_CMD"; echo "NOTE=[$CM_TUNNEL_NOTE]"''')
    assert "root@10.0.0.5" in out and "NOTE=[]" in out


@pytest.mark.parametrize("container", [True, False])
def test_no_ssh_server_means_no_ssh_command(container):
    detect = "return 0" if container else "return 1"
    common = f'''public_ip() {{ echo 84.18.153.125; }}; ssh_listening() {{ return 1; }}; CM_PM=dnf
                 hostname() {{ if [[ "${{1:-}}" == -I ]]; then echo 10.0.0.5; else echo optimux-test; fi; }}
                 systemd-detect-virt() {{ {detect}; }}; '''
    card = tunnel(common + "finish_server")
    assert "ssh -N" not in card and "No SSH server runs here" in card and "See the note below" in card
    if container:
        assert "incus config device add optimux-test web proxy listen=tcp:127.0.0.1:8000" in card
        assert "From another computer, add an SSH tunnel to the host" in card
    else:
        assert "sudo dnf install openssh-server" in card              # the hint follows the package manager
    opened = tunnel(common + "cli_open")
    assert "ssh -N" not in opened and "No SSH server runs here" in opened


@pytest.mark.parametrize("listening,expected", [
    ("tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*", 0),
    ("tcp LISTEN 0 128 [::]:2222 [::]:*", 0),
    ("tcp LISTEN 0 128 0.0.0.0:2200 0.0.0.0:*", 1),          # 22 must not match 2200
    ("tcp LISTEN 0 128 127.0.0.1:8000 0.0.0.0:*", 1),
])
def test_ssh_listening(tmp_path, listening, expected):
    (tmp_path / "ss.txt").write_text(listening + "\n")
    r = lib(f'CM_SUDO=""; CM_SSH_PORTS="22 2222 "; ss() {{ cat "{tmp_path}/ss.txt"; }}; ssh_listening && echo YES || echo NO')
    assert r.stdout.strip() == ("YES" if expected == 0 else "NO"), r.stdout + r.stderr


def test_ssh_listening_assumes_yes_when_ss_fails():
    r = lib('CM_SUDO=""; CM_SSH_PORTS="22 "; ss() { return 1; }; ssh_listening && echo YES || echo NO')
    assert r.stdout.strip() == "YES"


@pytest.mark.parametrize("state,crashed", [
    ("activating\nstart", False), ("active\nrunning", False),
    ("failed\nfailed", True), ("activating\nauto-restart", True),
])
def test_a_slow_first_start_is_not_reported_as_an_error(state, crashed):
    r = lib(f'CM_SUDO=""; systemctl() {{ printf "{state}\\n"; }}; still_starting optimux.service')
    out = " ".join(r.stdout.split())
    if crashed:
        assert "didn't start" in out and "sudo optimux logs" in out
    else:
        assert "still starting" in out and "sudo optimux status" in out and "didn't start" not in out


def test_server_card_and_open_say_where_to_run_the_tunnel():
    common = '''public_ip() { echo 203.0.113.7; }; ssh_ports() { CM_SSH_PORTS="22 "; CM_SSH_SURE=1; }
                ssh_listening() { return 0; }; hostname() { echo "203.0.113.7"; }
                declare -gA M=([MODE]=server [PORT]=8000 [URL]=http://127.0.0.1:8000); CM_PW_SET=1; CM_LOG=/dev/null; '''
    card = lib(common + 'finish_server', SUDO_USER="bob").stdout
    assert "On your own computer" in card and "ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 bob@203.0.113.7" in card
    assert "http://localhost:8000" in card
    opened = lib(common + 'cli_open', SUDO_USER="bob").stdout
    assert "ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 bob@203.0.113.7" in opened and "http://localhost:8000" in opened


# ---------- optimux open (tunnel options) and optimux port (5v)

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def tunnel_lib(tmp_path, script, domain="", service="systemd", local_port="", mode="server"):
    """A tunnel-only server install with fake network, SSH and service functions."""
    manifest = tmp_path / "install.env"
    return lib(f'''CM_SUDO=""; SUDO_USER=masoud
        public_ip() {{ echo 203.0.113.7; }}; hostname() {{ echo 203.0.113.7; }}
        ssh_listening() {{ return 0; }}; ssh_ports() {{ CM_SSH_PORTS="22"; CM_SSH_SURE=1; }}
        manifest_path() {{ echo "{manifest}"; }}
        declare -gA M=([MODE]={mode} [NAME]=optimux [APP_DIR]=/opt/optimux [DATA_DIR]=/var/lib/optimux
                       [PORT]=8000 [URL]=http://127.0.0.1:8000 [SERVICE]={service} [DOMAIN]="{domain}"
                       [LOCAL_PORT]="{local_port}")
        {script}''')


def clean(r):
    return ANSI.sub("", r.stdout + r.stderr)


def test_open_prints_a_tunnel_that_fails_loudly_and_explains_the_local_port(tmp_path):
    out = clean(tunnel_lib(tmp_path, "cli_open"))
    assert "ssh -N -o ExitOnForwardFailure=yes -L 8000:127.0.0.1:8000 masoud@203.0.113.7" in out
    assert "then open http://localhost:8000" in out
    assert "Port 8000 must be free on your computer" in out and "--local-port 9000" in out
    assert "lsof -nP -iTCP:8000 -sTCP:LISTEN" in out and "findstr :8000" in out


def test_open_options(tmp_path):
    r = tunnel_lib(tmp_path, "cli_open --local-port 9000 --user deploy --host vps --quiet")
    assert clean(r).strip() == "ssh -N -o ExitOnForwardFailure=yes -L 9000:127.0.0.1:8000 deploy@vps"
    r = tunnel_lib(tmp_path, "cli_open --background --quiet")
    assert clean(r).strip().startswith("ssh -f -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes")
    out = clean(tunnel_lib(tmp_path, "cli_open --ssh-config --local-port 9100"))
    assert "Host optimux\n    HostName 203.0.113.7\n    User masoud\n    LocalForward 9100 127.0.0.1:8000" in out
    assert "ssh -N optimux" in out and "http://localhost:9100" in out


@pytest.mark.parametrize("args", ["--local-port 80", "--local-port 70000", "--local-port x", "--host 'a;rm -rf /'",
                                  "--user 'x y'", "--nope"])
def test_open_refuses_bad_options(tmp_path, args):
    assert tunnel_lib(tmp_path, f"cli_open {args}").returncode != 0


def test_open_without_a_tunnel_says_none_is_needed(tmp_path):
    out = clean(tunnel_lib(tmp_path, "cli_open --local-port 9000 --quiet", domain="optimux.example.com"))
    assert "No tunnel needed" in out


def test_the_local_port_is_remembered_and_can_go_back(tmp_path):
    out = clean(tunnel_lib(tmp_path, 'cli_port --local 9000; echo "SAVED=$(grep ^LOCAL_PORT "$(manifest_path)")"'))
    assert "SAVED=LOCAL_PORT=9000" in out and "-L 9000:127.0.0.1:8000" in out
    out = clean(tunnel_lib(tmp_path, "cli_open --quiet", local_port="9000"))
    assert "-L 9000:127.0.0.1:8000" in out
    out = clean(tunnel_lib(tmp_path, 'cli_port --local same; echo "SAVED=$(grep ^LOCAL_PORT "$(manifest_path)")"',
                           local_port="9000"))
    assert "SAVED=LOCAL_PORT=\n" in out + "\n" and "-L 8000:127.0.0.1:8000" in out


FAKE_SVC = ('svc() { echo "SVC $1" >&3; }; exec 3>&1; install_server_unit() { echo "UNIT ${M[PORT]}"; }; '
            'write_user_unit() { echo "USERUNIT ${M[PORT]}"; }; ')


def test_port_moves_optimux_and_prints_the_new_tunnel(tmp_path):
    out = clean(tunnel_lib(tmp_path, FAKE_SVC + 'port_free() { return 0; }; wait_http() { return 0; }; cli_port 8100; '
                                     'grep ^PORT= "$(manifest_path)"'))
    assert "UNIT 8100" in out and "SVC restart" in out and "now listens on 127.0.0.1:8100 (it was 8000)" in out
    assert "-L 8100:127.0.0.1:8100" in out and "old tunnel stops working" in out and "PORT=8100" in out


def test_a_busy_port_is_refused_with_its_owner(tmp_path):
    r = tunnel_lib(tmp_path, FAKE_SVC + 'port_free() { return 1; }; port_owner() { echo nginx; }; cli_port 8100')
    assert r.returncode != 0 and "Port 8100 is already used on this machine by nginx" in clean(r)
    assert "UNIT" not in clean(r)


def test_a_port_that_doesnt_answer_goes_back(tmp_path):
    out = clean(tunnel_lib(tmp_path, FAKE_SVC + 'port_free() { return 0; }; wait_http() { return 1; }; '
                                     'cli_port 8100 || true; grep ^PORT= "$(manifest_path)"'))
    assert "going back to 8000" in out and "UNIT 8100" in out and "UNIT 8000" in out
    assert out.rstrip().endswith("PORT=8000")


def test_port_with_a_domain_or_a_plain_process(tmp_path):
    r = tunnel_lib(tmp_path, FAKE_SVC + "cli_port 8100", domain="optimux.example.com")
    assert r.returncode != 0 and "socket" in clean(r)
    r = tunnel_lib(tmp_path, FAKE_SVC + "port_free() { return 0; }; cli_port 8100 --no-restart", service="process",
                   mode="local")
    assert r.returncode != 0 and "without --no-restart" in clean(r)
    r = tunnel_lib(tmp_path, FAKE_SVC + "cli_port --local 9000", mode="local", service="process")
    assert r.returncode != 0 and "only for an SSH tunnel" in clean(r)


def test_cli_lists_open_and_port():
    text = open(CLI).read()
    assert 'open|url|tunnel|browser) shift; cli_open "$@" ;;' in text and 'port) shift; cli_port "$@" ;;' in text
