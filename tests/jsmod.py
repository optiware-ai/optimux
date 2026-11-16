"""Load one of web/*.js (plain scripts that export with `module.exports` under Node) for a test.

`require()` is not used: Node reads a .js file as an ES module when any parent folder has a package.json
with "type": "module", and then the file exports nothing. The text is run as a CommonJS function instead."""
import json


def prologue(path, var: str) -> str:
    """Node code that binds `var` to the exports of the file at `path`."""
    return (f"const __fs = require('fs'); const __m = {{ exports: {{}} }}; "
            f"new Function('module', 'exports', __fs.readFileSync({json.dumps(str(path))}, 'utf8'))(__m, __m.exports); "
            f"const {var} = __m.exports; ")
