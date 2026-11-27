"""The terminal client (tui/) calls the same REST routes as the web page. This test
reads the Rust sources and checks that every /api/... path the client uses exists on
the server, with the method it uses: a route renamed or removed on the server breaks
this test, not the user's terminal."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TUI = ROOT / "tui" / "src"
MAIN = ROOT / "optimux_backend" / "main.py"


def server_routes():
    """(METHOD, regex) for every @app.<method>("/api/...") route of main.py."""
    routes = []
    tree = ast.parse(MAIN.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"):
                continue
            method = dec.func.attr.upper()
            if method not in {"GET", "POST", "DELETE", "PUT", "PATCH"} or not dec.args:
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and str(arg.value).startswith("/api/"):
                pattern = re.sub(r"\{[^}:]+:path\}", r".+", re.escape(arg.value).replace(r"\{", "{").replace(r"\}", "}"))
                pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pattern)
                routes.append((method, re.compile(f"^{pattern}$")))
    return routes


# Method::Get, "/api/x"   or   Method::Delete, format!("/api/models/{}", ...)
DASH_CALL = re.compile(r'Method::(Get|Post|Delete),\s*(?:format!\(\s*)?"(/api/[^"]*)"', re.S)
# api.rs: .get(self.server.url("/api/x")) / .post(...) / .delete(...)
HTTP_CALL = re.compile(r'\.(get|post|delete)\(\s*self\.server\.url\(\s*(?:&format!\(\s*)?"(/api/[^"]*)"', re.S)
# cli/: c.get("/api/x"), c.post(&format!("/api/x/{}", ...), body), c.delete(...)
CLI_CALL = re.compile(r'\b(?:c|self)\s*\.(get|post|delete|text)\(\s*&?(?:format!\(\s*)?"(/api/[^"]*)"', re.S)
# api.rs: self.get_json(auth, "/api/x")
GET_JSON = re.compile(r'get_json\(\s*\w+,\s*"(/api/[^"]*)"')


def client_calls():
    calls = set()
    for path in TUI.rglob("*.rs"):
        text = path.read_text()
        for method, url in DASH_CALL.findall(text):
            calls.add((method.upper(), url, path.name))
        for method, url in HTTP_CALL.findall(text):
            calls.add((method.upper(), url, path.name))
        for method, url in CLI_CALL.findall(text):
            calls.add(("GET" if method == "text" else method.upper(), url, path.name))
        for url in GET_JSON.findall(text):
            calls.add(("GET", url, path.name))
    return calls


def concrete(url):
    """A path as the client sends it: no query, each {} filled in."""
    return url.split("?")[0].replace("{}", "x")


def test_the_scan_finds_the_clients_calls():
    calls = {(m, u) for m, u, _ in client_calls()}
    # a few calls the client is known to make: if the scan misses them, it is broken
    for known in [("GET", "/api/config"), ("POST", "/api/keys"), ("DELETE", "/api/models/{}"), ("GET", "/api/auth"), ("GET", "/api/settings"),
                  ("DELETE", "/api/keys/{}"), ("GET", "/api/tasks.csv"), ("GET", "/api/models/key-suggestion?id={}&base={}")]:
        assert known in calls, f"the scan no longer sees {known}: fix the patterns of this test"


def test_every_call_of_the_terminal_exists_on_the_server():
    routes = server_routes()
    missing = []
    for method, url, where in sorted(client_calls()):
        path = concrete(url)
        if not any(m == method and rx.match(path) for m, rx in routes):
            missing.append(f"{method} {url} (tui/src/.../{where})")
    assert not missing, "The terminal client calls routes the server doesn't have:\n" + "\n".join(missing)


# Any "/api/..." text in the client (also paths built in a helper, like path_of)
ANY_PATH = re.compile(r'"(/api/[^"\s]*)"')


def test_every_api_path_written_in_the_client_exists():
    routes = server_routes()
    missing = []
    for path in TUI.rglob("*.rs"):
        for url in ANY_PATH.findall(path.read_text()):
            if url.endswith(":"):                     # an error text, like "/api/settings: unexpected answer"
                continue
            concrete_url = re.sub(r"\{[^}]*\}", "x", url.split("?")[0])
            if not any(rx.match(concrete_url) for _, rx in routes):
                missing.append(f"{url} (tui/src/.../{path.name})")
    assert not missing, "Paths in the terminal client that the server doesn't have:\n" + "\n".join(missing)
