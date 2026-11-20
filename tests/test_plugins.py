"""Add-ons (v0.8): the sockets the core leaves open for a separate package.

A tiny fake package is written to a temporary folder for each test, so these
tests show the sockets work without the real team add-on being present.
"""
import importlib
import logging
import sys
import textwrap

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from optimux_backend.core import plugins


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    real = (dict(plugins._loaded), dict(plugins._answers))     # the real add-ons stay loaded for other tests
    plugins.reset()
    monkeypatch.syspath_prepend(str(tmp_path))
    before = set(sys.modules)
    yield
    plugins.reset()
    plugins._loaded.update(real[0])
    plugins._answers.update(real[1])
    for name in set(sys.modules) - before:
        if name.startswith("fake_addon") or name == "optimux_team":
            sys.modules.pop(name, None)


def make_addon(tmp_path, name, body):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "__init__.py").write_text(textwrap.dedent(body))
    importlib.invalidate_caches()      # the finder caches directory listings; a file made a moment ago may be invisible


GOOD = """
    from fastapi import APIRouter
    seen = []

    def register(api):
        router = APIRouter()

        @router.get("/api/fake/ping")
        def ping():
            return {"pong": True}

        api.add_router(router)
        api.answer("owner", lambda: 7)
        api.answer("double", lambda n, plus=0: n * 2 + plus)
        api.listen("saved", lambda **kw: seen.append(kw))
"""


def test_no_addon_means_defaults_and_no_changes():
    assert plugins.load(["fake_addon_missing"]) == []
    assert plugins.loaded() == []
    assert plugins.ask("owner") is None
    assert plugins.ask("owner", default="me") == "me"
    plugins.emit("saved", id=1)                              # nobody listens: nothing happens
    app = FastAPI()
    before = len(app.routes)
    plugins.mount(app)
    assert len(app.routes) == before


def test_the_real_team_addon_loads_and_answers():
    """In the private repository the real add-on is there; in the public core there is none and this is skipped."""
    pytest.importorskip("optimux_team")
    assert plugins.load() == ["optimux_team"]
    assert plugins.ask("member_rules", "no-such-project", default=None) == {"level": "supervised", "network": False}


def test_an_installed_addon_answers_questions_and_gets_events(tmp_path):
    make_addon(tmp_path, "fake_addon_one", GOOD)
    assert plugins.load(["fake_addon_one"]) == ["fake_addon_one"]
    assert plugins.ask("owner", default=None) == 7
    assert plugins.ask("double", 5, plus=1) == 11
    assert plugins.ask("unknown", default="d") == "d"
    plugins.emit("saved", id=3)
    import fake_addon_one
    assert fake_addon_one.seen == [{"id": 3}]


def test_routers_are_mounted_on_the_app(tmp_path):
    make_addon(tmp_path, "fake_addon_one", GOOD)
    plugins.load(["fake_addon_one"])
    app = FastAPI()
    plugins.mount(app)
    assert TestClient(app).get("/api/fake/ping").json() == {"pong": True}


def test_loading_twice_registers_once(tmp_path):
    make_addon(tmp_path, "fake_addon_one", """
        calls = []

        def register(api):
            calls.append(1)
    """)
    plugins.load(["fake_addon_one"])
    plugins.load(["fake_addon_one"])
    import fake_addon_one
    assert fake_addon_one.calls == [1]


def test_a_question_has_one_answerer(tmp_path):
    make_addon(tmp_path, "fake_addon_one", GOOD)
    make_addon(tmp_path, "fake_addon_two", GOOD)
    plugins.load(["fake_addon_one"])
    with pytest.raises(plugins.PluginError, match="already answers"):
        plugins.load(["fake_addon_two"])
    assert plugins.loaded() == ["fake_addon_one"]            # the second left no trace
    assert plugins.ask("owner") == 7


def test_a_broken_addon_stops_the_start(tmp_path):
    make_addon(tmp_path, "fake_addon_noimport", "import module_that_does_not_exist\n")
    with pytest.raises(plugins.PluginError, match="can't be loaded"):
        plugins.load(["fake_addon_noimport"])
    make_addon(tmp_path, "fake_addon_noregister", "x = 1\n")
    with pytest.raises(plugins.PluginError, match="no register"):
        plugins.load(["fake_addon_noregister"])
    make_addon(tmp_path, "fake_addon_raises", "def register(api):\n    raise ValueError('boom')\n")
    with pytest.raises(plugins.PluginError, match="failed to register"):
        plugins.load(["fake_addon_raises"])
    assert plugins.loaded() == []                            # none of them counts as loaded


def test_a_failing_answer_is_not_swallowed(tmp_path):
    make_addon(tmp_path, "fake_addon_one", """
        def register(api):
            api.answer("owner", lambda: 1 / 0)
    """)
    plugins.load(["fake_addon_one"])
    with pytest.raises(ZeroDivisionError):
        plugins.ask("owner", default=None)                   # the default must not stand in


def test_a_failing_listener_is_logged_and_the_others_still_run(tmp_path, caplog):
    make_addon(tmp_path, "fake_addon_one", """
        seen = []

        def register(api):
            api.listen("saved", lambda **kw: 1 / 0)
            api.listen("saved", lambda **kw: seen.append(kw))
    """)
    plugins.load(["fake_addon_one"])
    with caplog.at_level(logging.ERROR, logger="optimux.plugins"):
        plugins.emit("saved", id=9)
    import fake_addon_one
    assert fake_addon_one.seen == [{"id": 9}]
    assert "failed on the event saved" in caplog.text


def test_team_mode_needs_the_team_addon(tmp_path):
    plugins.check_mode("solo")                               # solo needs nothing
    plugins.check_mode("")
    with pytest.raises(plugins.PluginError, match="team mode, but the add-on optimux_team is not installed"):
        plugins.check_mode("team")
    make_addon(tmp_path, "optimux_team", "def register(api):\n    pass\n")
    plugins.load(["optimux_team"])
    plugins.check_mode("team")                               # now it passes


def test_a_missing_addon_is_told_from_a_broken_one(tmp_path):
    """A package that is not there is skipped; one that is there but whose own import fails is an error."""
    assert plugins.load(["fake_addon_absent"]) == []
    make_addon(tmp_path, "fake_addon_dep", "import fake_addon_absent\n\ndef register(api):\n    pass\n")
    with pytest.raises(plugins.PluginError):
        plugins.load(["fake_addon_dep"])


def test_an_addon_can_declare_paths_open_without_sign_in():
    from optimux_backend.core import plugins
    real_loaded, real_answers = dict(plugins._loaded), dict(plugins._answers)
    try:
        plugins.reset()
        api = plugins.Api("fake")
        api.public_path("/fake/open")
        plugins._loaded["fake"] = api
        assert plugins.is_public("/fake/open") and not plugins.is_public("/fake/other")
        assert plugins.public_paths() == {"/fake/open"}
    finally:
        plugins._loaded.clear(); plugins._answers.clear()
        plugins._loaded.update(real_loaded); plugins._answers.update(real_answers)


def test_the_core_lists_no_path_of_the_team_addon_as_open():
    from optimux_backend import main
    assert not {"/auth/invite", "/auth/activate"} & main.PUBLIC_PATHS
