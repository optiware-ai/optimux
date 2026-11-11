"""The chat box's list typing (web/chat-input.js), run with Node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "web" / "chat-input.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def run(expr: str):
    code = f"const C = require({json.dumps(str(JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def enter(text: str, cursor: int | None = None):
    cursor = len(text) if cursor is None else cursor
    return run(f"C.listEnter({json.dumps(text)}, {cursor}, {cursor})")


def test_shift_enter_continues_a_numbered_list():
    assert enter("Do this:\n1. first") == {"value": "Do this:\n1. first\n2. ", "cursor": 21}
    assert enter("9. nine")["value"] == "9. nine\n10. "
    assert enter("  3) three")["value"] == "  3) three\n  4) "


def test_shift_enter_continues_bullets():
    assert enter("- apples")["value"] == "- apples\n- "
    assert enter("* pears")["value"] == "* pears\n* "


def test_shift_enter_on_an_empty_item_ends_the_list():
    assert enter("1. first\n2. ") == {"value": "1. first\n", "cursor": 9}
    assert enter("- a\n- ") == {"value": "- a\n", "cursor": 4}


def test_shift_enter_in_the_middle_of_an_item_splits_it():
    assert enter("1. first second", 9) == {"value": "1. first\n2. second", "cursor": 12}


def test_plain_lines_and_selections_keep_the_normal_newline():
    assert enter("just text") is None
    assert enter("1.5 is a number") is None
    assert run('C.listEnter("1. a", 0, 4)') is None


def test_an_unused_last_marker_is_not_sent():
    assert run('C.cleanForSend("Steps:\\n1. a\\n2. ")') == "Steps:\n1. a"
    assert run('C.cleanForSend("- a\\n- \\n")') == "- a"
    assert run('C.cleanForSend("keep - this")') == "keep - this"


def test_a_line_in_backticks_becomes_a_terminal_block():
    assert run('C.userMarkdown("run this:\\n`npm test`\\nthen tell me")') == \
        "run this:\n```term\nnpm test\n```\nthen tell me"
    assert run('C.userMarkdown("use `npm test` here")') == "use `npm test` here"      # inline stays inline
    assert run('C.userMarkdown("```\\n`x`\\n```")') == "```\n`x`\n```"                 # not inside a code block
