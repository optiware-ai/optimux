"""The chat's "it needs you" notice (web/attention.js), run with Node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "web" / "attention.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def run(expr: str):
    code = f"const A = require({json.dumps(str(JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def text(data: dict):
    return run(f"A.attentionText({json.dumps(data)})")


def notify(**state):
    full = {"hidden": False, "settingsOpen": False, "studioOpen": False, "studioChat": False,
            "replay": False, "enabled": True, "permission": "granted"}
    return run(f"A.shouldNotify({json.dumps({**full, **state})})")


def test_it_says_what_is_waiting():
    assert text({"command": "rm -rf build\nnpm run build"}) == "May it run: rm -rf build ⏎ npm run build"
    assert text({"command": "x" * 200}).endswith("…") and len(text({"command": "x" * 200})) < 160
    assert text({"command": "git push", "action": "push_project"}) == "May it do this: git push"
    assert text({"command": "✎ web/app.js", "file_change": {"path": "web/app.js"}}) == "May it change web/app.js?"
    assert text({"form": {"title": "New project"}}) == "A form in the chat is waiting for you."
    assert text({"sync": {"kind": "push", "project": "site"}}) == "Push site to GitHub?"
    assert text({"sync": {"kind": "update", "project": "site"}}) == "Update site from GitHub?"
    assert text({"action": "model_fallback", "fallback": {"model": "flash", "alt": "sonnet", "kind": "quota"}}) == \
        "flash isn't answering. Use sonnet for this request?"
    assert text({"action": "model_fallback", "fallback": {"model": "flash", "alt": "sonnet", "kind": "stuck"}}) == \
        "flash is stuck. Continue on sonnet?"
    assert text({}) == "The chat needs your answer."


def test_only_when_the_chat_is_out_of_sight():
    assert notify(hidden=True)                                  # another tab or window
    assert notify(settingsOpen=True)                            # the Dashboard covers the chat
    assert notify(studioOpen=True, studioChat=False)            # the Studio without its chat column
    assert not notify()                                         # looking right at it
    assert not notify(studioOpen=True, studioChat=True)
    assert not notify(hidden=True, replay=True)                 # reopening an old chat replays its events
    assert not notify(hidden=True, enabled=False)               # turned off in the Dashboard
    assert not notify(hidden=True, permission="default")        # never allowed in this browser
    assert not notify(hidden=True, permission="denied")


def test_the_tab_title_says_it_too():
    assert run('A.tabTitle("Optimux", "site", true)') == "⚠ site · Optimux"
    assert run('A.tabTitle("Optimux", "", true)') == "⚠ Optimux"
    assert run('A.tabTitle("Optimux", "site", false)') == "Optimux"
