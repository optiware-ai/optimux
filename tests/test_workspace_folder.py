"""Changing the Workspace folder (Dashboard → Scope)."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import optimux_backend.main as main
from optimux_backend.core import config_manager as cm


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(settings))
    old, new = tmp_path / "workspace", tmp_path / "projects"
    (old / "optimux.si").mkdir(parents=True)
    new.mkdir()
    settings.write_text(json.dumps({"projects_root": str(old), "active_project": "optimux.si"}))
    os.environ["OPTIMUX_TOKEN"] = "test-token"
    client = TestClient(main.app)
    client.headers["Authorization"] = "Bearer test-token"
    return client, old, new, settings


def post(client, **fields):
    body = {**cm.get_settings(), **fields}
    return client.post("/api/settings", json=body)


def test_a_new_folder_without_the_active_project_selects_the_workspace(setup):
    client, old, new, settings = setup
    r = post(client, projects_root=str(new))
    assert r.status_code == 200
    assert r.json() == {"status": "success", "active_project": "",
                        "note": "optimux.si isn't in the new Workspace folder, so no project is selected now."}
    saved = json.loads(settings.read_text())
    assert saved["projects_root"] == str(new) and saved["active_project"] == ""


def test_the_project_stays_when_the_new_folder_has_it(setup):
    client, old, new, settings = setup
    (new / "optimux.si").mkdir()
    r = post(client, projects_root=str(new))
    assert r.json() == {"status": "success", "active_project": "optimux.si"}


def test_a_missing_project_in_the_same_folder_is_still_refused(setup):
    client, old, new, settings = setup
    r = post(client, active_project="nope")
    assert r.status_code == 400 and "Project folder not found" in r.json()["detail"]


def test_a_folder_optimux_cannot_write_in_is_refused(setup, monkeypatch):
    client, old, new, settings = setup
    real = os.access
    monkeypatch.setattr(cm.os, "access", lambda p, mode: False if str(p) == str(new) else real(p, mode))
    r = post(client, projects_root=str(new))
    assert r.status_code == 400 and r.json()["detail"].startswith(f"Optimux can't write in {new}")
    monkeypatch.setenv("OPTIMUX_SERVER", "1")
    r = post(client, projects_root=str(new))
    assert "On a server, the service may only write inside its own folder" in r.json()["detail"]
    assert json.loads(settings.read_text())["projects_root"] == str(old)          # nothing saved
