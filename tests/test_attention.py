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


def notice(**state):
    full = {"hidden": False, "settingsOpen": False, "studioOpen": False, "studioChat": False,
            "replay": False, "enabled": True, "permission": "granted"}
    return run(f"A.noticeFor({json.dumps({**full, **state})})")


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


def test_inside_optimux_the_notice_is_its_own_card():
    # the tab is in front but the chat is covered: Optimux's own card, no permission needed
    assert notice(settingsOpen=True) == "page"                          # the Dashboard covers the chat
    assert notice(studioOpen=True, studioChat=False) == "page"          # the Studio without its chat column
    assert notice(settingsOpen=True, enabled=False) == "page"
    assert notice(settingsOpen=True, permission="default") == "page"
    assert notice(settingsOpen=True, permission="unsupported") == "page"
    assert notice() == "none"                                           # looking right at it
    assert notice(studioOpen=True, studioChat=True) == "none"
    assert notice(settingsOpen=True, replay=True) == "none"             # reopening an old chat replays its events


def test_the_browser_notifies_only_when_the_tab_is_not_in_front():
    assert notice(hidden=True) == "desktop"                             # another tab or window
    assert notice(hidden=True, settingsOpen=True) == "desktop"          # the card can't be seen from there
    assert notice(hidden=True, replay=True) == "none"
    assert notice(hidden=True, enabled=False) == "none"                 # turned off in the Dashboard
    assert notice(hidden=True, permission="default") == "none"          # never allowed in this browser
    assert notice(hidden=True, permission="denied") == "none"


def test_the_card_follows_where_you_are():
    out = lambda **s: run(f"A.chatOutOfSight({json.dumps(s)})")
    assert out(settingsOpen=True) and out(studioOpen=True, studioChat=False)
    assert not out() and not out(studioOpen=True, studioChat=True)


def test_the_notification_icon_is_a_png():
    # browsers draw their own generic icon when a notification's icon is an SVG
    assert run("A.ICON") == "/static/logo-192.png"
    png = JS.parent / "logo-192.png"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_tab_title_says_it_too():
    assert run('A.tabTitle("Optimux", "site", true)') == "⚠ site · Optimux"
    assert run('A.tabTitle("Optimux", "", true)') == "⚠ Optimux"
    assert run('A.tabTitle("Optimux", "site", false)') == "Optimux"
