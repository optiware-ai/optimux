"""Test isolation.

OPTIMUX_HOME points at a throwaway folder before any Optimux module is
imported, and the one-time file move is disabled, so running the tests never
touches the real ~/.optimux, the repo's .optimux/ or its .env. Each test also
gets its own database, backup folder and legacy usage path.

Git reads no global or system configuration either: a CI runner has no git name
and email, so a test that leaned on this machine's would pass here and fail there.
"""
import importlib
import importlib.util
import os
import tempfile

os.environ["OPTIMUX_HOME"] = tempfile.mkdtemp(prefix="optimux-test-home-")
os.environ["OPTIMUX_NO_MIGRATE"] = "1"
# As on a CI runner: no git identity or settings from this machine
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
# FastAPI's TestClient sends "Host: testserver" (IM-10 allow-list)
os.environ.setdefault("OPTIMUX_ALLOWED_HOSTS", "testserver")

import pytest  # noqa: E402

from optimux_backend.core import autonomy, gitsync, plugins, project_settings, store, usage  # noqa: E402
# An installed team add-on keeps its own file for the member rules: isolate it too. No add-on: nothing to do.
member_rules = importlib.import_module("optimux_team.rules") if importlib.util.find_spec("optimux_team") else None

plugins.load()      # as main.py does at start: an installed optimux_team answers the core's questions


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "optimux.db"))
    monkeypatch.setattr(store, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(usage, "USAGE_FILE", str(tmp_path / "usage.jsonl"))
    monkeypatch.setattr(autonomy, "AUTONOMY_FILE", str(tmp_path / "autonomy.json"))
    if member_rules:
        monkeypatch.setattr(member_rules, "MEMBER_RULES_FILE", str(tmp_path / "member_rules.json"))
    monkeypatch.setattr(project_settings, "PROJECT_SETTINGS_FILE", str(tmp_path / "project_settings.json"))
    monkeypatch.setattr(gitsync, "REMOTES_FILE", str(tmp_path / "git_remotes.json"))


def flat_routes(routes):
    """The routes of an app with an add-on's router looked into: it is listed as one included entry holding
    its own routes, so a test that walks `app.routes` must use this or the add-on's routes escape it."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from flat_routes(inner.routes)
        else:
            yield r
