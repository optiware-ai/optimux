"""v0.8: how much of a model call is reading the project's files and writing them
(core/activity.py), and what that cost in the savings view."""
import json

import pytest

import optimux_backend.core.activity as activity
import optimux_backend.core.analytics as an
import optimux_backend.core.usage as usage
from tests.test_savings import PRICES, priced  # noqa: F401  (the fixture)


def call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.mark.parametrize("cmd,is_read", [
    ("cat app.py", True), ("cd src && sed -n 1,40p main.py", True), ("grep -rn TODO . | head -5", True),
    ("ls -la; find . -name '*.py'", True), ("rg foo && wc -l a.txt", True), ("git diff", True), ("git status", True),
    ("pytest -q", False), ("cat a.txt > b.txt", False), ("sed -i s/a/b/ x.py", False), ("cat a.py && npm test", False),
    ("git commit -m x", False), ("", False), ("echo hi | tee out.txt", False),
])
def test_which_commands_only_read(cmd, is_read):
    assert activity.is_read_command(cmd) is is_read


def test_shares_of_one_call():
    messages = [
        {"role": "system", "content": "s" * 1000},
        {"role": "user", "content": "u" * 1000},
        {"role": "assistant", "content": "", "tool_calls": [call("a", "execute_bash", {"command": "cat big.py"}),
                                                            call("b", "execute_bash", {"command": "pytest -q"})]},
        {"role": "tool", "tool_call_id": "a", "name": "execute_bash", "content": "x" * 6000},     # a read
        {"role": "tool", "tool_call_id": "b", "name": "execute_bash", "content": "y" * 2000},     # not a read
    ]
    total = sum(activity.chars(m) for m in messages)
    out_calls = {0: call("c", "write_file", {"path": "a.py", "content": "z" * 900})}
    wrote = len(out_calls[0]["function"]["arguments"])
    tokens = {"prompt_tokens": 5000, "completion_tokens": 600, "reasoning_tokens": 100}
    got = activity.shares(messages, "t" * 100, out_calls, tokens)
    assert got["read_tokens"] == round(5000 * 6000 / total)
    assert got["write_tokens"] == round(500 * wrote / (wrote + 100))           # thinking isn't file text
    # nothing read or written: zeros, never None (None means "from before this was measured")
    assert activity.shares(messages[:2], "hello", {}, tokens) == {"read_tokens": 0, "write_tokens": 0}
    assert activity.shares([], "", {}, {"prompt_tokens": 0, "completion_tokens": 0}) == {"read_tokens": 0, "write_tokens": 0}


def test_record_stores_the_shares_and_older_rows_have_none(priced):
    usage.record("s", "simple", "cheap", {"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 0},
                 parts={"read_tokens": 40, "write_tokens": 5})
    usage.record("s", "simple", "cheap", {"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 0})
    rows = usage.read_entries()
    assert (rows[0]["read_tokens"], rows[0]["write_tokens"]) == (40, 5)
    assert rows[1]["read_tokens"] is None and rows[1]["write_tokens"] is None


def test_file_work_in_the_savings_by_hand(priced):
    T = {"prompt_tokens": 10_000, "completion_tokens": 1_000, "cached_tokens": 4_000}
    # Simple, half its input is file text, a fifth of its output is written files
    usage.record("s", "simple", "cheap", dict(T), parts={"read_tokens": 5_000, "write_tokens": 200})
    # Complex reads too: it costs, and saves nothing
    usage.record("s", "complex", "strong", dict(T), parts={"read_tokens": 2_500, "write_tokens": 0})
    usage.record("s", "simple", "cheap", dict(T))                                  # from before: not in file work
    f = an.savings(0)["by_activity"]
    # input cost: cheap 6000*0.1 + 4000*0.01 = 640 ; strong 6000*3 + 4000*0.3 = 19200 (per million)
    read = f["read"]
    assert read["calls"] == 2
    assert read["spent"] == pytest.approx((640 * 0.5 + 19200 * 0.25) / 1e6)
    assert read["reference"] == pytest.approx((19200 * 0.5 + 19200 * 0.25) / 1e6)
    assert read["saved"] == pytest.approx(read["reference"] - read["spent"])
    assert read["by_role"]["complex"]["spent"] == pytest.approx(19200 * 0.25 / 1e6)
    write = f["write"]
    assert write["calls"] == 1 and write["spent"] == pytest.approx(200 * 0.4 / 1e6)
    assert write["reference"] == pytest.approx(200 * 15 / 1e6)
    assert f["measured_calls"] == 2 and f["unmeasured_calls"] == 1


def test_a_chat_call_is_measured(mocker, tmp_path):
    """The executor's calls get the two numbers (0 when nothing was read or written);
    the Conductor's call isn't measured."""
    from tests.ws_helpers import Conversation, chunk, fake_stream, make_client
    client = make_client(mocker, tmp_path)
    mocker.patch("litellm.acompletion", return_value=fake_stream([chunk("hello"), chunk(empty=True, usage_obj=None)]))
    Conversation(client).prompt("hi")
    rows = [r for r in usage.read_entries() if r["role"] == "simple"]
    assert rows and rows[-1]["read_tokens"] == 0 and rows[-1]["write_tokens"] == 0
