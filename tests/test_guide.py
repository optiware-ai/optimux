"""Feature 20: the in-app Guide."""
import re

import optimux_backend.main as main
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}


def test_guide_served_with_auth(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    assert client.get("/api/guide").status_code == 401
    text = client.get("/api/guide", headers=H).json()["content"]
    assert text.startswith("# Optimux Guide")
    for chapter in ("Getting started", "The Workspace and projects", "How routing works", "Memory", "Skills", "How to spend less",
                    "Settings reference", "Troubleshooting", "Glossary"):
        assert f"\n## {chapter}\n" in text


def test_guide_missing(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    mocker.patch.object(main, "GUIDE_PATH", str(tmp_path / "nope.md"))
    assert client.get("/api/guide", headers=H).status_code == 404


def test_guide_defaults_match_code():
    """The settings table must not drift from the real defaults."""
    from optimux_backend.core.config_manager import SettingsPayload
    from optimux_backend.core import curator, store, skills
    from optimux_backend.core.executor import run_shell_command
    import inspect
    text = open(main.GUIDE_PATH).read()
    d = SettingsPayload()
    assert f"| Steps per turn: Simple | Execution | {d.max_steps_simple} |" in text
    assert f"| Steps per turn: Complex | Execution | {d.max_steps_complex} |" in text
    assert f"| Idle minutes | Memory → Project | {d.curator_idle_minutes} |" in text
    assert f"{curator.MAX_TOKENS:,} tokens" in text
    assert f"the newest {store.KEEP_BACKUPS} are kept" in text
    from optimux_backend.core import config_manager as cmgr
    assert inspect.signature(run_shell_command).parameters["timeout"].default is None     # the setting decides
    assert cmgr.DEFAULT_COMMAND_TIMEOUT == 120 and "**120 seconds** by default" in text
    assert cmgr.COMMAND_TIMEOUT_RANGE == (10, 1800) and "from 10 to 1800 (30 minutes)" in text
    assert skills.RESERVED >= {"new", "simple", "complex", "skill"}
    assert re.search(r"\| `/new` \|", text) and "/skill <name>" in text
    assert "| Sandbox | Execution | Off |" in text and not d.sandbox and d.sandbox_network is None
    from optimux_backend.core import sandbox
    assert f"{sandbox.REQUIRE_ENV}=1" in text
    assert "projects folder" not in text.lower() and "projects root" not in text.lower()
