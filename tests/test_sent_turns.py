"""The kept prompts of one chat, grouped by the user's request (Dashboard -> History): a turn per request,
holding the calls it made; and the search, which also finds the words of a request."""
from optimux_backend.core import sent, sessions
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}


def _call(chat, role, model, history, task=None):
    """history: [(role, text)] after a system message."""
    msgs = [{"role": "system", "content": "You are Optimux, a coding assistant."}] + [{"role": r, "content": t} for r, t in history]
    return sent.record(chat, role, model, msgs, None, project="alpha", task_id=task)


def _chat():
    a = sessions.create("alpha", "background pattern")
    first = [("user", "the pattern is square based, make it pentagon like")]
    again = first + [("assistant", "Done."), ("user", "nope, change it to hexagons")]
    ids = {"c1": _call(a, "conductor", "m-cond", first[-1:], "T1"), "s1": _call(a, "simple", "m-simple", first, "T1"),
           "s2": _call(a, "simple", "m-simple", first, "T1"),
           "c2": _call(a, "conductor", "m-cond", again[-1:], "T2"), "x1": _call(a, "complex", "m-big", again, "T2"),
           "cur": _call(a, "curator", "m-cond", [("user", "update the memory")])}
    return a, ids


def test_a_turn_per_request_with_its_calls_in_order():
    a, ids = _chat()
    turns = sent.turns(a)
    assert [t["key"] for t in turns[:2]] == ["T1", "T2"]                      # the order they were asked in
    t1, t2, cur = turns
    assert t1["prompt"] == "the pattern is square based, make it pentagon like" and t1["reply"] is True
    assert [c["id"] for c in t1["calls"]] == [ids["c1"], ids["s1"], ids["s2"]]
    assert [(c["role"], c["model"]) for c in t2["calls"]] == [("conductor", "m-cond"), ("complex", "m-big")]
    assert t2["prompt"] == "nope, change it to hexagons"                        # the last thing said, not the first
    assert t1["ts"] == t1["calls"][0]["ts"] and set(t1["calls"][0]) == {"id", "ts", "role", "model", "chars"}
    assert cur["reply"] is False and cur["prompt"] == "" and cur["calls"][0]["role"] == "curator"


def test_calls_outside_any_chat_are_each_a_turn_of_their_own():
    _call("", "commit_message", "m1", [("user", "write the message")])
    _call("", "commit_message", "m1", [("user", "write another")])
    turns = sent.turns("")
    assert len(turns) == 2 and all(t["reply"] is False and len(t["calls"]) == 1 for t in turns)


def test_a_long_request_is_cut_for_the_list():
    a = sessions.create("alpha", "x")
    _call(a, "simple", "m", [("user", "word " * 200)], "T9")
    assert len(sent.turns(a)[0]["prompt"]) <= 300


def test_the_search_finds_the_words_of_a_request_but_not_the_instructions():
    a, _ = _chat()
    b = sessions.create("beta", "something else")
    _call(b, "simple", "m-simple", [("user", "add tests please")], "T5")
    assert {r["session"] for r in sent.chats(query="pentagon")} == {a}
    assert {r["session"] for r in sent.chats(query="hexagons")} == {a}
    assert {r["session"] for r in sent.chats(query="add tests")} == {b}
    assert {r["session"] for r in sent.chats(query="coding assistant")} == set()          # Optimux's own text, in every call
    assert {r["session"] for r in sent.chats(query="m-big")} == {a}                       # still: title and model names


def test_the_turns_say_which_ones_hold_the_search():
    a, _ = _chat()
    hits = [t["key"] for t in sent.turns(a, query="hexagons") if t["hit"]]
    assert hits == ["T2"]
    assert not any(t["hit"] for t in sent.turns(a))


def test_the_api_lists_the_turns(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    a, _ = _chat()
    got = client.get(f"/api/sent/turns?session={a}&q=pentagon", headers=H).json()["turns"]
    assert [t["key"] for t in got][:2] == ["T1", "T2"] and got[0]["hit"] is True
    assert client.get("/api/sent/turns?session=", headers=H).status_code == 200
    assert client.get(f"/api/sent/turns?session={a}").status_code in (401, 403)
