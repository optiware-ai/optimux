"""Add-on plan (v0.8): the core must not depend on the team code.

The team version is moving, a piece at a time, into the add-on `optimux_team`
(see core/plugins.py). While it moves, a few core files still import it. This
list may only shrink: when a piece has moved, remove its file from the list.
It must end empty, which is what lets the add-on live in its own repository.
"""
import os
import re

import pytest

from tests.conftest import flat_routes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files of the core that still import the add-on or its team module. It is empty: the core imports none of
# it (the add-on is reached only through core/plugins.py). A file added here is a step backwards.
STILL_IMPORTING: set = set()

IMPORT = re.compile(r"^\s*(?:from\s+optimux_backend\.core\s+import\s+[^\n]*\bteam\b"
                    r"|from\s+optimux_backend\.core\.team\b"
                    r"|from\s+optimux_team\b|import\s+optimux_team\b)", re.M)


def core_files():
    for folder, _, names in os.walk(os.path.join(ROOT, "optimux_backend")):
        for name in names:
            if name.endswith(".py"):
                yield os.path.join(folder, name)


def test_only_the_listed_core_files_import_the_team_code():
    found = set()
    for path in core_files():
        with open(path, encoding="utf-8") as f:
            if IMPORT.search(f.read()):
                found.add(os.path.relpath(path, ROOT))
    assert found == STILL_IMPORTING, (
        f"new: {sorted(found - STILL_IMPORTING)}; gone (remove from the list): {sorted(STILL_IMPORTING - found)}")


def mine_paths(main):
    return [r.path for r in flat_routes(main.app.routes)
            if r.path.startswith(("/api/team", "/api/me", "/auth/invite", "/auth/activate", "/team-assets"))
            and not r.path.startswith("/api/memory")]


def test_the_team_routes_come_from_the_addon_and_the_core_defines_none_of_them():
    """/api/team and /api/me are the add-on's: with no add-on the core has no such route."""
    from optimux_backend import main
    from optimux_backend.core import plugins
    if not plugins.loaded():
        assert not mine_paths(main), "the core defines team routes of its own"
        return
    mine = [r for r in flat_routes(main.app.routes) if r.path.startswith(("/api/team", "/api/me"))
            and not r.path.startswith("/api/memory")]
    assert mine, "the add-on's routes are not mounted"
    assert all(r.endpoint.__module__.startswith("optimux_team") for r in mine), \
        [r.path for r in mine if not r.endpoint.__module__.startswith("optimux_team")]


def test_a_team_install_without_its_addon_does_not_start():
    """Startup refuses (with the way out) rather than run with no member guard; solo, and team with the add-on, start."""
    from starlette.testclient import TestClient
    from optimux_backend import main
    from optimux_backend.core import plugins, store
    with TestClient(main.app):              # solo: nothing to check
        pass
    store.set_meta("mode", "team")
    real = (dict(plugins._loaded), dict(plugins._answers))
    plugins.reset()
    try:
        with pytest.raises(plugins.PluginError, match="optimux disable-team"):
            with TestClient(main.app):
                pass
    finally:
        plugins._loaded.update(real[0])
        plugins._answers.update(real[1])
    if "optimux_team" in plugins.loaded():
        with TestClient(main.app):          # the add-on is there: it starts
            pass
