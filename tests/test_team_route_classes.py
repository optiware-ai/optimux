"""The team-version audit (tests/team_route_classes.json) stays in step with the real app: every endpoint,
websocket message and model tool is classified, and nothing classified has gone. Runtime behaviour is untouched.
A new endpoint fails here until it gets a class: public (P), own token (T), signed-in user (S), read-only for
everyone (R), project member (M) or admin (A); see "_about" in the file."""
import json
import re
from pathlib import Path

from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

from optimux_backend.core import workspace
from optimux_backend.core import plugins
from optimux_backend.main import PUBLIC_PATHS, app

ROOT = Path(__file__).resolve().parent.parent
AUDIT = json.loads((ROOT / "tests" / "team_route_classes.json").read_text())
# The routes the team add-on defines are audited in its own file; with no add-on installed there are none
_ADDON_AUDIT = ROOT / "optimux_team" / "tests" / "team_route_classes.json"
if _ADDON_AUDIT.exists():
    AUDIT["routes"] = {**AUDIT["routes"], **json.loads(_ADDON_AUDIT.read_text())["routes"]}
CLASSES = {"P", "T", "S", "R", "M", "A"}

from tests.conftest import flat_routes  # noqa: E402


def _real_routes() -> set[str]:
    found = set()
    for r in flat_routes(app.routes):
        if isinstance(r, APIRoute):
            found |= {f"{m} {r.path}" for m in r.methods if m not in ("HEAD", "OPTIONS")}
        elif isinstance(r, WebSocketRoute):
            found.add(f"WEBSOCKET {r.path}")
    return found


def test_every_endpoint_has_a_class_and_none_is_left_behind():
    real, listed = _real_routes(), set(AUDIT["routes"])
    assert not real - listed, "classify these in tests/team_route_classes.json (P, T, S, R, M or A): " + ", ".join(sorted(real - listed))
    assert not listed - real, "no longer endpoints, remove from tests/team_route_classes.json: " + ", ".join(sorted(listed - real))


def test_the_classes_are_known_and_every_entry_says_why_when_it_is_not_obvious():
    for section in ("routes", "ws_messages", "model_tools"):
        for name, entry in AUDIT[section].items():
            assert entry["class"] in CLASSES, (section, name)
    for name, entry in AUDIT["routes"].items():
        if entry["class"] in ("R", "T"):
            assert entry["note"], f"{name}: say what a user may read, or how the token is checked"


def test_what_the_audit_calls_public_is_what_the_sign_in_guard_lets_through():
    public = {k for k, v in AUDIT["routes"].items() if v["class"] == "P"}
    open_paths = PUBLIC_PATHS | plugins.public_paths()      # the core's, and what the add-ons declare
    guard = {k for k in AUDIT["routes"] if k.split(" ", 1)[1] in open_paths}
    assert public == guard
    assert {k for k, v in AUDIT["routes"].items() if v["class"] == "T"} == {k for k in AUDIT["routes"] if k.split(" ", 1)[1].startswith("/preview/")}


def test_every_websocket_message_the_server_reads_is_classified():
    src = (ROOT / "optimux_backend" / "main.py").read_text()
    body = src[src.index("async def websocket_endpoint"):]
    kinds = set(re.findall(r'kind\s*==\s*"([a-z_]+)"', body))
    assert kinds <= set(AUDIT["ws_messages"]), sorted(kinds - set(AUDIT["ws_messages"]))
    assert "prompt" in AUDIT["ws_messages"]


def test_every_tool_a_model_can_call_is_classified():
    seen = set()
    for path in (ROOT / "optimux_backend" / "core").glob("*.py"):
        seen |= set(re.findall(r'"name": "([a-z_]+)",', path.read_text()))
    offered = {t["function"]["name"] for proj in ("", "alpha") for t in workspace.tools_for(proj)}
    assert offered <= seen
    assert seen == set(AUDIT["model_tools"]), sorted(seen ^ set(AUDIT["model_tools"]))
