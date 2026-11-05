"""v0.6.1: Getting started, the first-run checklist."""
import pytest

import optimux_backend.core.config_manager as cm
import optimux_backend.core.onboarding as ob
from optimux_backend.core import auth, autonomy, budgets, sandbox, snapshots, workspace
from optimux_backend.core import usage
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}


@pytest.fixture
def world(mocker, tmp_path):
    """A fresh install: nothing set. Tests fill in what they need."""
    w = {"models": {"available": [], "requires_setup": []},
         "routing": {"conductor_model": "", "simple_model": "", "complex_model": ""},
         "settings": {"projects_root": "", "active_project": "", "memory_curator": True, "skills_mode": "auto"},
         "sandbox": {"works": False}, "enabled": False, "budgets": {}, "server": False, "totp": False,
         "autonomy": {"default": "supervised", "default_saved": False, "projects": {}},
         "github": {"ready": False, "reason": "GitHub isn't set up."}, "snapshots": []}
    mocker.patch.object(autonomy, "overview", lambda: w["autonomy"])
    mocker.patch.object(workspace, "github_status", lambda fresh=False: w["github"])
    mocker.patch.object(snapshots, "list_snapshots", lambda: w["snapshots"])
    mocker.patch.object(cm, "get_models_config", lambda: w["models"])
    mocker.patch.object(cm, "get_routing_config", lambda: w["routing"])
    mocker.patch.object(cm, "get_settings", lambda: w["settings"])
    mocker.patch.object(sandbox, "status", lambda refresh=False: w["sandbox"])
    mocker.patch.object(sandbox, "enabled", lambda s: w["enabled"])
    mocker.patch.object(budgets, "get_budgets", lambda: w["budgets"])
    mocker.patch.object(auth, "server_mode", lambda: w["server"])
    mocker.patch.object(auth, "totp_enabled", lambda: w["totp"])
    mocker.patch.object(ob.platform, "system", return_value="Linux")
    w["dir"] = str(tmp_path)
    return w


def steps(st):
    return {s["id"]: s for s in st["steps"]}


def test_a_fresh_install_starts_at_the_models(world):
    st = ob.status()
    assert st["next"] == "models" and st["done"] == 0 and st["total"] == 7
    assert st["show_in_chat"] and not st["existing"] and not st["ready"]
    assert [s["id"] for s in st["steps"]] == ["models", "routing", "workspace", "sandbox", "features", "project",
                                             "first", "budget", "autonomy", "backups", "github"]
    assert steps(st)["budget"]["optional"] and steps(st)["models"]["target"] == "models"


def test_steps_tick_from_the_real_settings(world):
    world["models"]["available"] = [{"id": "m"}]
    st = ob.status()
    assert steps(st)["models"]["done"] and st["next"] == "routing"
    world["routing"].update(simple_model="m", complex_model="m")
    assert not steps(ob.status())["routing"]["done"]               # the Conductor too (or Off)
    world["routing"]["conductor_model"] = "off"
    world["settings"].update(projects_root=world["dir"], active_project="demo")
    world["sandbox"]["works"] = True
    st = ob.status()
    assert steps(st)["routing"]["done"] and steps(st)["workspace"]["done"] and steps(st)["project"]["done"]
    assert not steps(st)["sandbox"]["done"]                         # works, but not on
    world["enabled"] = True
    st = ob.status()
    assert steps(st)["sandbox"]["done"] and st["next"] == "features"
    ob.act("features_ok")
    assert ob.status()["next"] == "first"
    usage.insert({"role": "simple", "model": "m", "cost": 0.001})
    st = ob.status()
    assert st["ready"] and st["done"] == 7 and st["next"] == "budget" and st["show_in_chat"]   # "You're ready" once
    st = ob.act("ready_seen")
    assert not st["show_in_chat"]


def test_no_sandbox_outside_linux_is_information(world, mocker):
    mocker.patch.object(ob.platform, "system", return_value="Darwin")
    s = steps(ob.status())["sandbox"]
    assert s["done"] and "Linux-only" in s["info"]


def test_an_existing_install_doesnt_pop_up(world):
    usage.insert({"role": "complex", "model": "m", "cost": 0.01})
    st = ob.status()
    assert st["existing"] and not st["show_in_chat"]
    assert ob.act("reopen")["show_in_chat"]                         # asked for it on Overview
    st = ob.act("dismiss")
    assert st["dismissed"] and not st["show_in_chat"]
    with pytest.raises(ob.OnboardingError):
        ob.act("nope")


def test_changing_a_feature_counts_as_choosing(world):
    ob.note_settings_change({"plan_mode": "off"}, {"plan_mode": "off", "projects_root": "/x"})
    assert not steps(ob.status())["features"]["done"]
    ob.note_settings_change({"plan_mode": "off"}, {"plan_mode": "ask"})
    assert steps(ob.status())["features"]["done"]


def test_servers_offer_two_step_codes(world):
    world["server"] = True
    s = steps(ob.status())["totp"]
    assert s["optional"] and not s["done"] and s["target"] == "system"


def test_the_endpoints(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    mocker.patch.object(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    st = client.get("/api/onboarding", headers=H).json()
    assert st["next"] and "steps" in st
    assert client.post("/api/onboarding", json={"action": "dismiss"}, headers=H).json()["dismissed"]
    assert client.post("/api/onboarding", json={"action": "bad"}, headers=H).status_code == 422
    assert client.get("/api/onboarding").status_code == 401
    # a feature changed in the settings ticks "Choose your features"
    settings = client.get("/api/settings", headers=H).json()
    client.post("/api/settings", json={**settings, "skills_mode": "manual"}, headers=H)
    st = client.get("/api/onboarding", headers=H).json()
    assert steps(st)["features"]["done"]


def test_each_step_says_what_it_found(world):
    """v0.8 (port plan phase 10): a short check line per step, and three optional steps."""
    st = steps(ob.status())
    assert st["models"]["check"] == "No model with a key yet." and st["project"]["check"] == "None open."
    assert st["workspace"]["check"] == "Not set." and st["first"]["check"] == "None yet."
    assert all(st[i]["optional"] and not st[i]["done"] for i in ("autonomy", "backups", "github"))
    assert "not chosen yet" in st["autonomy"]["check"] and st["github"]["check"] == "GitHub isn't set up."
    world["models"]["available"] = [{"id": "a"}, {"id": "b"}]
    world["routing"].update(simple_model="a", complex_model="b", conductor_model="off")
    world["settings"].update(projects_root=world["dir"] + "/gone")
    world["autonomy"].update(default="checkpointed", default_saved=True, projects={"demo": "autonomous"})
    world["github"] = {"ready": True, "login": "octo"}
    world["snapshots"] = [{"status": "ready"}, {"status": "failed"}]
    st = steps(ob.status())
    assert st["models"]["check"] == "2 models ready."
    assert st["routing"]["check"] == "Simple: a, Complex: b, Conductor: off."
    assert st["workspace"]["check"].endswith("doesn't exist.") and not st["workspace"]["done"]
    assert st["autonomy"]["done"] and st["autonomy"]["check"] == "Default: checkpointed, 1 project with its own level."
    assert st["github"]["done"] and st["github"]["check"] == "Connected as octo."
    assert st["backups"]["done"] and st["backups"]["check"] == "1 snapshot."


def test_optional_steps_never_hold_back_ready(world, mocker):
    world["models"]["available"] = [{"id": "m"}]
    world["routing"].update(simple_model="m", complex_model="m", conductor_model="off")
    world["settings"].update(projects_root=world["dir"], active_project="demo")
    world["sandbox"]["works"] = True
    world["enabled"] = True
    ob.act("features_ok")
    mocker.patch.object(ob, "_requests_made", return_value=3)
    st = ob.status()
    assert st["ready"] and st["done"] == st["total"] == 7 and st["next"] == "budget"
    assert steps(st)["first"]["check"] == "3 requests so far."
