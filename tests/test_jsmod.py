"""The web/*.js helpers are tested with Node. They must load the same wherever the checkout is: a
package.json with "type": "module" in some parent folder (~/git, ~) makes Node read a plain
`require("web/x.js")` as an ES module, which exports nothing, and every test of it failed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.jsmod import prologue

WEB = Path(__file__).resolve().parent.parent / "web"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def test_a_helper_loads_under_a_parent_package_of_type_module(tmp_path):
    (tmp_path / "package.json").write_text('{"type": "module"}')
    (tmp_path / "web").mkdir()
    for name in ("attention.js", "chat-input.js", "connection.js"):
        (tmp_path / "web" / name).write_text((WEB / name).read_text(encoding="utf-8"), encoding="utf-8")
    for name, var, expr in (("attention.js", "A", "A.ICON"), ("chat-input.js", "C", "typeof C.listEnter"),
                            ("connection.js", "C", "typeof C.linkState")):
        code = prologue(tmp_path / "web" / name, var) + f"console.log(JSON.stringify({expr}));"
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        assert json.loads(out) in ("/static/logo-192.png", "function"), name
