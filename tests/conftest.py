"""Test isolation.

OPTIMUX_HOME points at a throwaway folder before any Optimux module is
imported, and the one-time file move is disabled, so running the tests never
touches the real ~/.optimux, the repo's .optimux/ or its .env. Each test also
gets its own database, backup folder and legacy usage path.
"""
import os
import tempfile

os.environ["OPTIMUX_HOME"] = tempfile.mkdtemp(prefix="optimux-test-home-")
os.environ["OPTIMUX_NO_MIGRATE"] = "1"
# FastAPI's TestClient sends "Host: testserver" (IM-10 allow-list)
os.environ.setdefault("OPTIMUX_ALLOWED_HOSTS", "testserver")

import pytest  # noqa: E402

from optimux_backend.core import autonomy, gitsync, project_settings, store, usage  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "optimux.db"))
    monkeypatch.setattr(store, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(usage, "USAGE_FILE", str(tmp_path / "usage.jsonl"))
    monkeypatch.setattr(autonomy, "AUTONOMY_FILE", str(tmp_path / "autonomy.json"))
    monkeypatch.setattr(project_settings, "PROJECT_SETTINGS_FILE", str(tmp_path / "project_settings.json"))
    monkeypatch.setattr(gitsync, "REMOTES_FILE", str(tmp_path / "git_remotes.json"))
