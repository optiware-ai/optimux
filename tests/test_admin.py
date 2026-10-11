"""Feature 24: the setup commands the installer uses (python -m optimux_backend.admin)."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def admin(home, *args, stdin=""):
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(OPTIMUX_HOME=str(home), OPTIMUX_NO_MIGRATE="1")
    env.pop("OPTIMUX_TOKEN", None)
    env.pop("OPTIMUX_SERVER", None)
    return subprocess.run([sys.executable, "-m", "optimux_backend.admin", *args], input=stdin, cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=120)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    r = admin(h, "init")
    assert r.returncode == 0, r.stderr
    return h


def test_init_creates_a_private_home_without_printing_the_token(home):
    assert (home / "secrets.env").exists() and (home / "data" / "optimux.db").exists()
    assert oct(os.stat(home).st_mode & 0o777) == "0o700"
    assert oct(os.stat(home / "secrets.env").st_mode & 0o777) == "0o600"
    token = admin(home, "token").stdout.strip()
    assert len(token) > 30 and token not in admin(home, "init").stdout


def test_password_from_stdin(home):
    assert admin(home, "set-password", stdin="short\n").returncode == 1
    r = admin(home, "set-password", stdin="correct horse battery\n")
    assert r.returncode == 0 and "correct horse" not in r.stdout + r.stderr
    assert json.loads(admin(home, "status").stdout)["password_set"] is True
    assert admin(home, "reset-password").returncode == 0
    assert json.loads(admin(home, "status").stdout)["password_set"] is False


def test_key_model_routing_workspace_and_status(home, tmp_path):
    assert admin(home, "set-key", "OPENAI_API_KEY", stdin="sk-secret-value\n").returncode == 0
    assert "sk-secret-value" in (home / "secrets.env").read_text()
    assert admin(home, "add-model", "openai/gpt-5-mini", "GPT-5 mini", "OPENAI_API_KEY").returncode == 0
    assert admin(home, "set-routing", "openai/gpt-5-mini", "openai/gpt-5-mini", "openai/gpt-5-mini").returncode == 0
    assert admin(home, "set-routing", "nope/x", "nope/x", "nope/x").returncode == 1
    ws = tmp_path / "ws"
    assert admin(home, "set-workspace", str(ws), "--create").returncode == 0 and ws.is_dir()
    status = admin(home, "status").stdout
    assert "sk-secret-value" not in status                      # never prints secrets
    s = json.loads(status)
    assert s["workspace"] == str(ws) and s["routing"]["complex_model"] == "openai/gpt-5-mini"
    assert s["models"]["openai/gpt-5-mini"]["key_set"] is True


def test_bad_key_name_refused(home):
    assert admin(home, "set-key", "PATH", stdin="x\n").returncode == 1
    assert admin(home, "set-key", "OPENAI_API_KEY", stdin="\n").returncode == 1


def test_setting_one_thing_keeps_the_others(home, tmp_path):
    ws = tmp_path / "ws"
    admin(home, "set-workspace", str(ws), "--create")
    settings = home / "config" / "settings.json"
    data = json.loads(settings.read_text())
    data["max_steps_complex"] = 40
    settings.write_text(json.dumps(data))
    admin(home, "sandbox", "off")
    after = json.loads(settings.read_text())
    assert after["max_steps_complex"] == 40 and after["projects_root"] == str(ws)


def test_installer_models_are_routable():
    """The models install.sh offers must be IDs LiteLLM can route."""
    from optimux_backend.core.config_manager import model_id_problem
    text = open(os.path.join(ROOT, "install.sh")).read()
    block = text[text.index("CM_PROVIDERS=("):text.index("\n)", text.index("CM_PROVIDERS=("))]
    entries = [line.strip().strip('"') for line in block.splitlines()[1:] if line.strip().startswith('"')]
    assert len(entries) >= 3
    for e in entries:
        label, env_key, small, big, *_ = e.split("|")
        assert env_key.endswith("_API_KEY")
        assert model_id_problem(small) is None and model_id_problem(big) is None, e


def test_github_commands(home):
    """Offline: a malformed token is refused before any network call; status never needs one."""
    r = admin(home, "set-github", stdin="not a token\n")
    assert r.returncode == 1 and "doesn't look like a GitHub token" in r.stderr
    assert admin(home, "set-github", stdin="\n").returncode == 1
    st = json.loads(admin(home, "github-status").stdout)
    assert set(st) >= {"gh", "ready", "reason", "token_saved"} and st["token_saved"] is False
    assert admin(home, "remove-github").returncode == 0
    assert json.loads(admin(home, "status").stdout)["github"] is None
