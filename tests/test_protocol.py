"""docs/PROTOCOL.md must match the code (the client protocol of /ws).

The terminal client is built from docs/PROTOCOL.md, not from the web page. These
tests read the server and the web page and fail when a message type is used
there but missing from the page, or listed on the page but no longer used.
Fix: update docs/PROTOCOL.md in the same commit as the code."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "PROTOCOL.md"
SERVER = [ROOT / "optimux_backend" / "main.py", *sorted((ROOT / "optimux_backend" / "core").glob("*.py"))]
WEB = sorted((ROOT / "web").glob("*.js"))

SENDERS = {"send", "notify"}                  # send(websocket, "kind", ...) and notify(...)
WIRE = {"dumps", "_broadcast", "send_text"}   # json.dumps({...}), _broadcast({...})
RECORD = {"add"}                              # recorder.add({...}): recorded with the chat only


# ---------------------------------------------------------------- the page

def doc_types(section: str) -> set[str]:
    """The `type` names in the first column of the tables under the heading `## <section>`."""
    text = DOC.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(section)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"docs/PROTOCOL.md has no section '## {section}'"
    return set(re.findall(r"^\| `([a-z_]+)` \|", m.group(1), re.M))


# ---------------------------------------------------------------- the server

def _call_name(node: ast.Call) -> str | None:
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def _dict_type(node) -> str | None:
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == "type" and isinstance(v, ast.Constant) \
                    and isinstance(v.value, str):
                return v.value
    return None


def _scan_server():
    """(sent, recorded, received) message types, read from the server's code."""
    sent, recorded, received = set(), set(), set()
    trees = [ast.parse(p.read_text(encoding="utf-8")) for p in SERVER]
    funcs = {n.name: n for t in trees for n in ast.walk(t) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def returned_types(name: str) -> set[str]:
        f = funcs.get(name)
        return {_dict_type(n.value) for n in ast.walk(f) if isinstance(n, ast.Return)} - {None} if f else set()

    for tree in trees:
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            named = {}      # msg = {"type": ...} then json.dumps(msg) / _broadcast(msg)
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    if (t := _dict_type(n.value)):
                        named[n.targets[0].id] = t
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                name = _call_name(n)
                if name in SENDERS and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) \
                        and isinstance(n.args[1].value, str):
                    sent.add(n.args[1].value)
                elif name in WIRE | RECORD and n.args:
                    a = n.args[0]
                    found = {_dict_type(a)} | ({named.get(a.id)} if isinstance(a, ast.Name) else set())
                    if isinstance(a, ast.Call) and isinstance(a.func, ast.Name):
                        found |= returned_types(a.func.id)      # json.dumps(_member_state(...))
                    found -= {None}
                    (recorded if name in RECORD else sent).update(found)
        # What the reader loop and the job compare the incoming type with
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name in ("websocket_endpoint", "_run_job"):
                for c in ast.walk(n):
                    if isinstance(c, ast.Compare) and _is_incoming_type(c.left):
                        for right in c.comparators:
                            vals = right.elts if isinstance(right, (ast.Tuple, ast.List, ast.Set)) else [right]
                            received |= {v.value for v in vals if isinstance(v, ast.Constant) and isinstance(v.value, str)}
    sent.discard("http.response.start")
    sent.discard("http.response.body")
    return sent, recorded - sent, received


def _is_incoming_type(node) -> bool:
    """`kind` or `<payload>.get("type")`."""
    if isinstance(node, ast.Name):
        return node.id == "kind"
    return (isinstance(node, ast.Call) and _call_name(node) == "get" and node.args
            and isinstance(node.args[0], ast.Constant) and node.args[0].value == "type")


SENT, RECORDED, RECEIVED = _scan_server()


# ---------------------------------------------------------------- the web page

def web_handled() -> set[str]:
    text = "\n".join(p.read_text(encoding="utf-8") for p in WEB)
    return set(re.findall(r"\bdata\.type === ['\"]([a-z_]+)['\"]", text))


def web_sent() -> set[str]:
    text = "\n".join(p.read_text(encoding="utf-8") for p in WEB)
    out = set()
    for m in re.finditer(r"\.send\(JSON\.stringify\(", text):
        chunk = text[m.end():m.end() + 400].split(");")[0]      # one send, also over a few lines
        out |= set(re.findall(r"\btype: ?['\"]([a-z_]+)['\"]", chunk))
    return out


# ---------------------------------------------------------------- the tests

def test_the_scan_finds_the_basics():
    # A guard for the scanner itself: if these vanish, the scan is broken, not the protocol
    assert {"token", "done", "approval_request", "session_loaded", "auth_ok", "pong"} <= SENT
    assert {"stop", "approval_response", "open_session", "ping", "auth"} <= RECEIVED
    assert "prompt" in RECORDED


def test_every_message_the_server_sends_is_on_the_page():
    listed = doc_types("3. Server → client")
    assert SENT - listed == set(), "add these to '## 3. Server → client' in docs/PROTOCOL.md"


def test_the_page_lists_no_message_the_server_no_longer_sends():
    listed = doc_types("3. Server → client")
    assert listed - SENT == set(), "the server no longer sends these: remove them from docs/PROTOCOL.md"


def test_every_message_the_server_reads_is_on_the_page():
    listed = doc_types("2. Client → server")
    assert RECEIVED - listed == set(), "add these to '## 2. Client → server' in docs/PROTOCOL.md"
    assert listed - RECEIVED == set(), "the server no longer reads these: remove them from docs/PROTOCOL.md"


def test_recorded_only_events_are_on_the_page():
    assert RECORDED == doc_types("6. Chat events")


def test_the_web_page_only_uses_documented_messages():
    assert web_handled() - doc_types("3. Server → client") == set()
    assert web_sent() - doc_types("2. Client → server") == set()
