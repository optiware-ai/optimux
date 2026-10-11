"""The ~/.optimux layout and the one-time move from older locations."""
import importlib
import os
import stat

import pytest

import optimux_backend.core.paths as paths_module


@pytest.fixture
def paths(monkeypatch, tmp_path):
    """A fresh paths module rooted in a temporary OPTIMUX_HOME, with moves enabled."""
    test_home = os.environ["OPTIMUX_HOME"]          # the throwaway home from conftest
    monkeypatch.setenv("OPTIMUX_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPTIMUX_NO_MIGRATE", raising=False)
    p = importlib.reload(paths_module)
    yield p
    os.environ["OPTIMUX_HOME"] = test_home
    os.environ["OPTIMUX_NO_MIGRATE"] = "1"
    importlib.reload(paths_module)


def make_repo(tmp_path, env="GEMINI_API_KEY=g\nOPTIMUX_TOKEN=t\n"):
    repo = tmp_path / "repo"
    (repo / ".codemux").mkdir(parents=True)
    for name in ("settings.json", "models.json", "routing.json", "budgets.json"):
        (repo / ".codemux" / name).write_text(f'{{"from": "{name}"}}')
    (repo / ".codemux" / "context.json").write_text("{}")   # old memory manager leftovers
    (repo / ".env").write_text(env)
    return repo


def test_fresh_layout_is_created(paths):
    assert paths.prepare_home(repo_dir=paths.HOME) == []
    for d in ("config", "data", "backups", "memory", "skills", "logs"):
        assert os.path.isdir(os.path.join(paths.HOME, d))


def test_repo_config_and_env_move_out_of_the_repo(paths, tmp_path):
    repo = make_repo(tmp_path)
    moves = paths.prepare_home(repo_dir=str(repo))
    assert open(paths.MODELS_FILE).read() == '{"from": "models.json"}'
    assert os.path.exists(paths.BUDGETS_FILE) and os.path.exists(paths.SETTINGS_FILE)
    assert open(paths.SECRETS_FILE).read() == "GEMINI_API_KEY=g\nOPTIMUX_TOKEN=t\n"
    assert stat.S_IMODE(os.stat(paths.SECRETS_FILE).st_mode) == 0o600
    assert not (repo / ".env").exists() and not (repo / ".codemux").exists()
    assert os.path.exists(os.path.join(paths.LEGACY_DIR, "repo-codemux-context.json"))
    assert any("secrets.env" in m for m in moves)
    assert len(paths.migration_log()) == len(moves)
    assert paths.prepare_home(repo_dir=str(repo)) == []          # second start: nothing to do


def test_existing_destination_wins_and_nothing_is_lost(paths, tmp_path):
    os.makedirs(paths.CONFIG_DIR)
    open(paths.MODELS_FILE, "w").write('{"kept": true}')
    open(paths.SECRETS_FILE, "w").write("GEMINI_API_KEY=new\n")
    repo = make_repo(tmp_path, env="GEMINI_API_KEY=old\nANTHROPIC_API_KEY=a")
    paths.prepare_home(repo_dir=str(repo))
    assert open(paths.MODELS_FILE).read() == '{"kept": true}'
    assert os.path.exists(os.path.join(paths.LEGACY_DIR, "repo-models.json"))
    assert open(paths.SECRETS_FILE).read() == "GEMINI_API_KEY=new\nANTHROPIC_API_KEY=a\n"   # merged, home wins
    legacy_env = os.path.join(paths.LEGACY_DIR, "repo.env")
    assert open(legacy_env).read().startswith("GEMINI_API_KEY=old")
    assert stat.S_IMODE(os.stat(legacy_env).st_mode) == 0o600


def test_home_root_leftovers_move(paths, tmp_path):
    os.makedirs(paths.HOME, exist_ok=True)
    open(os.path.join(paths.HOME, "codemux.db"), "w").write("db")
    open(os.path.join(paths.HOME, "codemux.db-wal"), "w").write("wal")
    open(os.path.join(paths.HOME, "usage.jsonl.migrated"), "w").write("{}")
    os.makedirs(os.path.join(paths.HOME, "projects", "Optimux_964801ae"))
    paths.prepare_home(repo_dir=str(tmp_path))
    assert open(paths.DB_PATH).read() == "db" and open(paths.DB_PATH + "-wal").read() == "wal"
    assert os.path.exists(os.path.join(paths.LEGACY_DIR, "usage.jsonl.migrated"))
    assert os.path.isdir(os.path.join(paths.LEGACY_DIR, "projects", "Optimux_964801ae"))


def test_no_migrate_switch(paths, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    monkeypatch.setenv("OPTIMUX_NO_MIGRATE", "1")
    assert paths.prepare_home(repo_dir=str(repo)) == []
    assert (repo / ".env").exists()


def test_tests_never_use_the_real_home():
    assert "optimux-test-home-" in paths_module.HOME


# ---------- starting over: permissions and a deleted folder

def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_fresh_home_is_private(paths):
    paths.prepare_home(repo_dir=paths.HOME)
    assert _mode(paths.HOME) == 0o700


def test_loose_secrets_file_is_tightened_on_start(paths):
    os.makedirs(paths.HOME)
    with open(paths.SECRETS_FILE, "w") as f:
        f.write("GEMINI_API_KEY=x\n")
    os.chmod(paths.SECRETS_FILE, 0o644)
    paths.prepare_home(repo_dir=paths.HOME)
    assert _mode(paths.SECRETS_FILE) == 0o600


def test_write_private_creates_and_tightens(tmp_path):
    p = tmp_path / "s.env"
    paths_module.write_private(str(p), "A=1\n")
    assert _mode(p) == 0o600 and p.read_text() == "A=1\n"
    os.chmod(p, 0o644)
    paths_module.write_private(str(p), "B=2\n", append=True)
    assert _mode(p) == 0o600 and p.read_text() == "A=1\nB=2\n"


def test_new_token_file_is_private(tmp_path, monkeypatch):
    from optimux_backend.core import security
    monkeypatch.delenv(security.TOKEN_ENV, raising=False)
    env = tmp_path / "fresh" / "secrets.env"
    tok = security.get_or_create_token(str(env))
    assert _mode(env) == 0o600 and tok in env.read_text()
    os.chmod(env, 0o644)
    new = security.rotate_token(str(env))
    assert _mode(env) == 0o600 and new in env.read_text() and tok not in env.read_text()


def test_saved_api_key_is_private(tmp_path, monkeypatch):
    from optimux_backend.core import config_manager as cm
    env = tmp_path / "secrets.env"
    monkeypatch.setattr(cm, "SECRETS_FILE", str(env))
    cm.save_api_key_to_env(cm.ApiKeyPayload(env_key="GEMINI_API_KEY", api_key="k-123"))
    assert _mode(env) == 0o600
    cm.delete_api_key_from_env("GEMINI_API_KEY")
    assert _mode(env) == 0o600 and "k-123" not in env.read_text()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_database_recreated_if_deleted_while_running(tmp_path):
    from optimux_backend.core import store, usage
    usage.record("s", "simple", "m", {"prompt_tokens": 1, "completion_tokens": 1})
    assert usage.summary()["all_time"]["total"]["calls"] == 1
    os.remove(store.DB_PATH)                  # the folder was deleted under the server
    assert usage.summary()["all_time"]["total"]["calls"] == 0   # empty, but no "no such table"
    usage.record("s", "simple", "m", {"prompt_tokens": 1, "completion_tokens": 1})
    assert usage.summary()["all_time"]["total"]["calls"] == 1
