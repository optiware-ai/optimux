"""The kept prompts, listed by chat (Dashboard -> History): one row per chat, filtered like the chat list."""
import time

from optimux_backend.core import sent, sessions
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
MSG = [{"role": "user", "content": "hello there"}]


def _call(chat, role, model, project="alpha", ts=None):
    sid = sent.record(chat, role, model, MSG, None, project=project)
    if ts is not None:
        from optimux_backend.core import store
        conn = store.connect()
        with conn:
            conn.execute("UPDATE sent_calls SET ts = ? WHERE id = ?", (ts, sid))
        conn.close()
    return sid


def _world():
    a = sessions.create("alpha", "fix the menu links")
    b = sessions.create("beta", "add some tests")
    old = time.time() - 40 * 86400
    ids = {"a1": _call(a, "simple", "model-s"), "a2": _call(a, "complex", "model-c"), "a3": _call(a, "simple", "model-s"),
           "b1": _call(b, "simple", "model-s", project="beta", ts=old),
           "none": _call("", "commit_message", "model-s", project="alpha")}
    return a, b, ids


def test_one_row_per_chat_newest_first_with_a_title_and_counts():
    a, b, ids = _world()
    rows = sent.chats()
    by = {r["session"]: r for r in rows}
    assert set(by) == {a, b, ""}
    assert by[a]["title"] == "fix the menu links" and by[a]["calls"] == 3 and by[a]["project"] == "alpha"
    assert by[a]["chars"] == 3 * len("hello there") and sorted(by[a]["models"]) == ["model-c", "model-s"]
    assert by[a]["known"] is True and by[a]["archived"] is False and by[""]["title"] == "" and by[""]["calls"] == 1     # no chat: the UI names it
    assert [r["session"] for r in rows][-1] == b                                             # the oldest last


def test_the_filters_work_like_the_chat_list():
    a, b, ids = _world()
    assert {r["session"] for r in sent.chats(project="beta")} == {b}
    assert {r["session"] for r in sent.chats(project="alpha")} == {a, ""}
    assert {r["session"] for r in sent.chats(since=time.time() - 7 * 86400)} == {a, ""}       # Updated: 7 days
    assert {r["session"] for r in sent.chats(query="tests")} == {b}                           # words in the title
    assert {r["session"] for r in sent.chats(query="model-c")} == {a}                         # or in a model's name
    sessions.set_archived(a, True)
    assert {r["session"] for r in sent.chats(archived=True)} == {a}
    assert {r["session"] for r in sent.chats()} == {b, ""}
    assert len(sent.chats(limit=1)) == 1


def test_the_calls_of_one_chat_are_listed_alone():
    a, b, ids = _world()
    got = sent.recent(50, session=a)
    assert sorted(r["id"] for r in got) == sorted([ids["a1"], ids["a2"], ids["a3"]])
    assert [r["id"] for r in sent.recent(50, session="")] == [ids["none"]]                   # outside any chat
    assert len(sent.recent(50)) == 5                                                          # no filter: all


def test_the_title_hides_a_saved_key(mocker):
    mocker.patch("optimux_backend.core.security.secret_values", return_value=[("ANTHROPIC_API_KEY", "sk-ant-very-secret-1234567890")])
    a = sessions.create("alpha", "use key sk-ant-very-secret-1234567890 please")
    _call(a, "simple", "m")
    title = [r for r in sent.chats() if r["session"] == a][0]["title"]
    assert "sk-ant-very-secret-1234567890" not in title


def test_the_api_lists_chats_and_the_calls_of_one(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    a, b, ids = _world()
    rows = client.get("/api/sent/chats?all_projects=true", headers=H).json()["chats"]
    assert {r["session"] for r in rows} == {a, b, ""}
    only = client.get("/api/sent/chats?project=beta", headers=H).json()["chats"]
    assert [r["session"] for r in only] == [b]
    week = client.get(f"/api/sent/chats?all_projects=true&since={time.time() - 7 * 86400}", headers=H).json()["chats"]
    assert {r["session"] for r in week} == {a, ""}
    calls = client.get(f"/api/sent?session={a}&limit=50", headers=H).json()["calls"]
    assert len(calls) == 3
    assert client.get("/api/sent/chats").status_code in (401, 403)


def test_calls_of_a_deleted_chat_are_listed_as_not_known():
    _call("gone-chat", "simple", "m")
    row = [r for r in sent.chats() if r["session"] == "gone-chat"][0]
    assert row["known"] is False and row["title"] == "" and row["calls"] == 1
