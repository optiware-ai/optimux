"""The View button on a pane of text (web/sent-view.js, run with Node) and the buttons on the page."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.jsmod import prologue

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "web" / "sent-view.js"
PAGE = (ROOT / "index.html").read_text()
APP = (ROOT / "web" / "app.js").read_text()
node = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def run(expr: str):
    code = prologue(JS, "V") + f"console.log(JSON.stringify({expr}));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def kind(text):
    return run(f"V.detect({json.dumps(text)})")


@node
def test_json_is_found_and_made_readable():
    assert kind('{"stdout":"ok","code":0}') == "json"
    assert kind('[{"type":"function"}]') == "json"
    assert run('V.pretty(\'{"a":1,"b":[1,2]}\')') == '{\n  "a": 1,\n  "b": [\n    1,\n    2\n  ]\n}'
    assert kind('{"broken": ') != "json"


@node
def test_a_tool_call_part_is_json_after_its_name():
    text = 'execute_bash\n{"command": "ls -la"}'
    assert kind(text) == "json"
    assert run(f"V.pretty({json.dumps(text)})") == 'execute_bash\n{\n  "command": "ls -la"\n}'


@node
def test_markdown_is_found_by_its_marks_not_by_length():
    assert kind("# Title\n\nSome **bold** words") == "md"
    assert kind("Steps:\n- one\n- two") == "md"
    assert kind("| a | b |\n|---|---|\n| 1 | 2 |") == "md"
    assert kind("```py\nprint(1)\n```") == "md"
    assert kind("make the pattern pentagon like") is None
    assert kind("a - b and a * b") is None                  # a dash in a sentence is not a list


@node
def test_unfenced_code_is_found_by_how_its_lines_look():
    assert kind("def f(x):\n    return x + 1\n\nclass A:\n    pass") == "code"
    assert kind("const a = 1;\nfunction g() {\n  return a;\n}") == "code"
    assert kind("Please change the color of the exit button to red") is None
    assert kind("") is None and kind(None) is None


def test_every_text_pane_has_a_copy_and_a_view_button():
    tpl = PAGE[PAGE.index('id="sent-call-tpl"'):]
    pane = tpl[tpl.index("data-sent-part"):tpl.index("</template>")]
    assert "<copy-btn" in pane and ':text="r.p.text"' in pane
    assert "data-sent-view" in pane and "viewKind(r.p)" in pane          # shown only when there is something to show
    assert "viewed[r.i]" in pane and "v-html" in pane                    # click again: the original text
    assert '<script src="/static/sent-view.js"></script>' in PAGE
    assert PAGE.index("sent-view.js") < PAGE.index("/static/app.js")


def test_the_answer_is_a_part_of_its_call():
    assert "answer:" in APP[APP.index("const SENT_KINDS"):APP.index("const SENT_OWN")]
    load = APP[APP.index("const loadSentRequest"):APP.index("const toggleSentRequest")]
    assert "st.answer" in load and "kind: 'answer'" in load


def test_the_tools_say_who_offers_them():
    kinds = APP[APP.index("const SENT_KINDS"):APP.index("const SENT_OWN")]
    assert "Tools Optimux offers the model" in kinds
