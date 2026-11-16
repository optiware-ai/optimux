"""Dashboard -> History: the kept prompts as a flat list of requests (one row per thing the user asked, no
chat level), and one request opened as a timeline: the meta-prompt, the Conductor, the executor."""
import json
import time

from optimux_backend.core import sent, sessions, store
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
SYSTEM = "You are Optimux.\nMEMORY: likes pytest\nBe brief."
MEMORY = "MEMORY: likes pytest"
TOOLS = [{"type": "function", "function": {"name": "execute_bash"}}]


def _msgs(*items):
    out = [{"role": "system", "content": SYSTEM}]
    for who, text in items:
        if who == "call":
            out.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "execute_bash", "arguments": json.dumps({"command": text})}}]})
        else:
            out.append({"role": who, "content": text})
    return out


def _call(chat, role, model, items, task=None, tools=None, project="alpha"):
    return sent.record(chat, role, model, _msgs(*items), tools, project=project, memory=MEMORY, task_id=task)


def _chat():
    a = sessions.create("alpha", "background pattern")
    q1 = [("user", "make the pattern pentagon like")]
    q2 = q1 + [("assistant", "Done."), ("user", "nope, use hexagons " + "x" * 400 + " tail-word")]
    ids = {"c1": _call(a, "conductor", "m-cond", q1, "T1"),
           "s1": _call(a, "simple", "m-simple", q1, "T1", TOOLS),
           "s2": _call(a, "simple", "m-simple", q1 + [("call", "ls"), ("tool", "a.css")], "T1", TOOLS),
           "c2": _call(a, "conductor", "m-cond", q2[-1:], "T2"),
           "x1": _call(a, "complex", "m-big", q2, "T2", TOOLS),
           "cur": _call(a, "curator", "m-cond", [("user", "update the memory")])}
    return a, ids


def test_one_row_per_request_newest_first_and_no_chat_level():
    a, ids = _chat()
    rows = sent.requests()
    assert [r["key"] for r in rows[:3]] == ["call-%d" % ids["cur"], "T2", "T1"]
    t1 = rows[2]
    assert t1["session"] == a and t1["title"] == "background pattern" and t1["known"] is True and t1["project"] == "alpha"
    assert t1["prompt"] == "make the pattern pentagon like" and t1["reply"] is True and t1["calls"] == 3
    assert sorted(t1["models"]) == ["m-cond", "m-simple"] and t1["ts"] <= t1["last"]
    assert rows[1]["prompt"].startswith("nope, use hexagons") and len(rows[1]["prompt"]) <= 300     # the last thing said, cut for a list
    assert rows[0]["reply"] is False and rows[0]["prompt"] == "" and rows[0]["calls"] == 1 and rows[0]["role"] == "curator"


def test_calls_outside_any_chat_are_rows_of_their_own():
    sent.record("", "commit_message", "m1", _msgs(("user", "write the message")), None, project="alpha")
    row = [r for r in sent.requests() if r["session"] == ""][0]
    assert row["known"] is False and row["reply"] is False and row["calls"] == 1


def test_the_filters_are_the_chat_lists():
    a, ids = _chat()
    b = sessions.create("beta", "tests")
    _call(b, "simple", "m-simple", [("user", "add tests")], "T7", project="beta")
    old = time.time() - 40 * 86400
    conn = store.connect()
    with conn:
        conn.execute("UPDATE sent_calls SET ts = ? WHERE task_id = 'T7'", (old,))
    conn.close()
    assert {r["key"] for r in sent.requests(project="beta")} == {"T7"}
    assert "T7" not in {r["key"] for r in sent.requests(since=time.time() - 7 * 86400)}
    sessions.set_archived(a, True)
    assert {r["key"] for r in sent.requests(archived=True)} >= {"T1", "T2"} and "T1" not in {r["key"] for r in sent.requests()}
    assert len(sent.requests(limit=1)) == 1


def test_the_search_reads_the_whole_request_not_the_cut_one():
    _chat()
    assert [r["key"] for r in sent.requests(query="tail-word")] == ["T2"]        # past the 300 characters shown
    assert [r["key"] for r in sent.requests(query="Pentagon")] == ["T1"]          # not T2, which sends it again as history
    assert {"T1", "T2"} <= {r["key"] for r in sent.requests(query="background pattern")}        # the chat's title
    assert {r["key"] for r in sent.requests(query="m-big")} == {"T2"}             # a model's name
    assert sent.requests(query="Be brief") == []                                  # Optimux's own text is never the match
    assert sent.requests(query="likes pytest") == []


def test_one_request_is_a_timeline_meta_then_conductor_then_executor():
    a, ids = _chat()
    d = sent.request_detail(a, "T1")
    assert [(p["kind"]) for p in d["meta"]] == ["instructions", "memory", "instructions"]
    assert d["meta"][1]["text"] == MEMORY
    assert [s["role"] for s in d["steps"]] == ["conductor", "simple", "simple"]
    cond, s1, s2 = d["steps"]
    assert [p["kind"] for p in cond["parts"]] == ["instructions", "memory", "instructions", "user"]       # its own prompt, whole
    assert [p["kind"] for p in s1["parts"]] == ["user", "tools"] and s1["earlier"] is None
    assert s1["parts"][0]["text"] == "make the pattern pentagon like"
    assert [p["kind"] for p in s2["parts"]] == ["call", "result"]                    # only what is new: no repeated tools
    assert s2["id"] == ids["s2"] and s2["model"] == "m-simple" and set(s2) >= {"id", "ts", "role", "model", "chars", "parts", "earlier"}


def test_the_earlier_conversation_is_counted_not_repeated():
    a, ids = _chat()
    x1 = sent.request_detail(a, "T2")["steps"][-1]
    assert x1["role"] == "complex" and [p["kind"] for p in x1["parts"]] == ["user", "tools"]
    assert x1["parts"][0]["text"].startswith("nope, use hexagons")
    assert x1["earlier"]["count"] == 2 and x1["earlier"]["chars"] == len("make the pattern pentagon like") + len("Done.")


def test_a_call_on_its_own_is_a_timeline_of_one_step():
    a, ids = _chat()
    d = sent.request_detail(a, "call-%d" % ids["cur"])
    assert [s["role"] for s in d["steps"]] == ["curator"] and d["steps"][0]["parts"][-1]["text"] == "update the memory"
    assert sent.request_detail(a, "nope") is None


def test_the_api_lists_requests_and_one_timeline(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    a, ids = _chat()
    rows = client.get("/api/sent/requests?all_projects=true&q=hexagons", headers=H).json()["requests"]
    assert [r["key"] for r in rows] == ["T2"]
    d = client.get(f"/api/sent/request?session={a}&key=T1", headers=H).json()
    assert [s["role"] for s in d["steps"]] == ["conductor", "simple", "simple"]
    assert client.get(f"/api/sent/request?session={a}&key=zzz", headers=H).status_code == 404
    assert client.get("/api/sent/requests").status_code in (401, 403)


def _replied(a, ids):
    """What the page recorded for the chat: text streams in before each call's usage event."""
    ev = lambda **k: k
    sessions.append_events(a, [
        ev(type="prompt", content="make the pattern pentagon like"),
        ev(type="usage", sent=ids["c1"]),
        ev(type="token", content="Looking at "), ev(type="token", content="the css."),
        ev(type="usage", sent=ids["s1"]),
        ev(type="usage", sent=ids["s2"]),                       # a step that wrote nothing
        ev(type="token", content="Done. Key sk-ant-very-secret-123 is hidden."),
        ev(type="usage", sent=ids["x1"]),
    ])


def test_what_each_call_answered_comes_from_the_chat_log_masked(mocker):
    mocker.patch("optimux_backend.core.security.secret_values", return_value=[("ANTHROPIC_API_KEY", "sk-ant-very-secret-123")])
    a, ids = _chat()
    _replied(a, ids)
    got = sessions.answers(a)
    assert got[ids["s1"]] == "Looking at the css."
    assert ids["c1"] not in got and ids["s2"] not in got      # empty answers are left out
    assert "very-secret" not in got[ids["x1"]]
    assert sessions.answers("missing-chat") == {}


def test_the_timeline_puts_the_answer_with_the_call_that_wrote_it(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    a, ids = _chat()
    _replied(a, ids)
    steps = client.get(f"/api/sent/request?session={a}&key=T1", headers=H).json()["steps"]
    assert [s.get("answer", "") for s in steps] == ["", "Looking at the css.", ""]
    out = sent.record("", "commit_message", "m", [{"role": "user", "content": "msg"}], None)
    lone = client.get(f"/api/sent/request?session=&key=call-{out}", headers=H).json()["steps"]
    assert lone[0].get("answer", "") == ""                    # outside a chat nothing is logged
