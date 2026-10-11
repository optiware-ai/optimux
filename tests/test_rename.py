"""Feature 27: CodeMux is now Optimux. Installs and data from before the rename keep working."""
import importlib
import os
import stat

import pytest

import optimux_backend.core.paths as paths_module
from optimux_backend.core import skills
from optimux_backend.core.config_manager import check_command_env
from optimux_backend.core.security import ValidationError, scrubbed_env


@pytest.fixture
def fresh_paths(monkeypatch, tmp_path):
    """paths reloaded with a temporary home folder and moves enabled; the caller
    sets OPTIMUX_HOME (or not) before calling reload()."""
    saved = {k: os.environ.get(k) for k in ("OPTIMUX_HOME", "OPTIMUX_NO_MIGRATE", "HOME")}
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.delenv("OPTIMUX_NO_MIGRATE", raising=False)
    monkeypatch.delenv("OPTIMUX_HOME", raising=False)
    os.makedirs(tmp_path / "user")
    yield lambda: importlib.reload(paths_module)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    os.environ["OPTIMUX_NO_MIGRATE"] = "1"
    importlib.reload(paths_module)


def test_old_variables_are_used_under_the_new_names():
    env = {"CODEMUX_SERVER": "1", "CODEMUX_HOME": "/old", "OPTIMUX_HOME": "/new", "OTHER": "x"}
    paths_module.adopt_old_env(env)
    assert env["OPTIMUX_SERVER"] == "1"          # only the old name was set
    assert env["OPTIMUX_HOME"] == "/new"         # the new name wins
    assert "OPTIMUX_OTHER" not in env


def test_old_home_folder_moves_once(fresh_paths, tmp_path):
    old = tmp_path / "user" / ".codemux"
    (old / "config").mkdir(parents=True)
    (old / "config" / "settings.json").write_text('{"kept": true}')
    (old / "secrets.env").write_text("CODEMUX_TOKEN=t\nCODEMUX_GITHUB_TOKEN=g\nGEMINI_API_KEY=k\n")
    (old / "data").mkdir()
    (old / "data" / "codemux.db").write_text("db")
    p = fresh_paths()
    assert p.HOME == str(tmp_path / "user" / ".optimux")
    moves = p.prepare_home(repo_dir=str(tmp_path))
    assert not old.exists()
    assert open(p.SETTINGS_FILE).read() == '{"kept": true}'
    assert open(p.SECRETS_FILE).read() == "OPTIMUX_TOKEN=t\nOPTIMUX_GITHUB_TOKEN=g\nGEMINI_API_KEY=k\n"
    assert stat.S_IMODE(os.stat(p.SECRETS_FILE).st_mode) == 0o600
    assert open(p.DB_PATH).read() == "db" and p.DB_PATH.endswith("optimux.db")
    assert any("CodeMux is now Optimux" in m for m in moves)
    assert p.prepare_home(repo_dir=str(tmp_path)) == []      # nothing left to do


def test_new_home_is_never_replaced(fresh_paths, tmp_path):
    (tmp_path / "user" / ".codemux").mkdir()
    (tmp_path / "user" / ".optimux").mkdir()
    p = fresh_paths()
    p.prepare_home(repo_dir=str(tmp_path))
    assert (tmp_path / "user" / ".codemux").is_dir()       # left alone, not merged


def test_a_chosen_home_folder_is_not_moved(fresh_paths, tmp_path, monkeypatch):
    (tmp_path / "user" / ".codemux").mkdir()
    monkeypatch.setenv("OPTIMUX_HOME", str(tmp_path / "elsewhere"))
    fresh_paths().prepare_home(repo_dir=str(tmp_path))
    assert (tmp_path / "user" / ".codemux").is_dir()


def test_a_key_saved_under_the_new_name_wins(fresh_paths, tmp_path):
    p = fresh_paths()
    os.makedirs(p.HOME)
    open(p.SECRETS_FILE, "w").write("CODEMUX_TOKEN=old\nOPTIMUX_TOKEN=new\n")
    p.prepare_home(repo_dir=str(tmp_path))
    assert open(p.SECRETS_FILE).read() == "OPTIMUX_TOKEN=new\n"


def test_backups_of_both_names_are_listed_by_date(tmp_path):
    for name in ("codemux-2026-10-01.db", "optimux-2026-10-03.db", "codemux-2026-10-02.db", "notes.txt"):
        (tmp_path / name).write_text("")
    assert paths_module.backup_files(str(tmp_path)) == [
        "codemux-2026-10-01.db", "codemux-2026-10-02.db", "optimux-2026-10-03.db"]


def test_old_project_skills_folder_is_still_read(tmp_path):
    project = tmp_path / "p"
    (project / ".codemux-skills").mkdir(parents=True)
    assert skills.project_skills_dir(str(project)) == str(project / ".codemux-skills")
    (project / ".optimux-skills").mkdir()
    assert skills.project_skills_dir(str(project)) == str(project / ".optimux-skills")
    assert skills.project_skills_dir(str(tmp_path / "new")) == str(tmp_path / "new" / ".optimux-skills")


@pytest.mark.parametrize("name", ["CODEMUX_TOKEN", "CODEMUX_GITHUB_TOKEN", "CODEMUX_HOME"])
def test_old_names_are_never_given_to_commands(name):
    with pytest.raises(ValidationError):
        check_command_env([name])
    assert name not in scrubbed_env(base={name: "secret", "PATH": "/bin"})


