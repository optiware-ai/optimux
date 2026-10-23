import asyncio
import pytest
from optimux_backend.core.executor import run_shell_command

# cwd is passed explicitly so these tests don't depend on local settings.json.

@pytest.mark.asyncio
async def test_run_shell_command_success(tmp_path):
    result = await run_shell_command("echo 'test output'", cwd=str(tmp_path))
    assert result["exit_code"] == 0
    assert "test output" in result["stdout"]
    assert result["stderr"] == ""

@pytest.mark.asyncio
async def test_run_shell_command_failure(tmp_path):
    result = await run_shell_command("ls /path/to/nonexistent/directory/that/will/fail", cwd=str(tmp_path))
    assert result["exit_code"] != 0
    assert result["stderr"] != ""

@pytest.mark.asyncio
async def test_run_shell_command_timeout(tmp_path):
    result = await run_shell_command("sleep 3", timeout=1, cwd=str(tmp_path))
    assert result["exit_code"] == 124
    assert "Command timed out" in result["stderr"]

# ---- Output cap (v0.5.1): a huge output must not be resent in full at every step

@pytest.mark.asyncio
async def test_small_output_is_untouched(tmp_path):
    result = await run_shell_command("seq 1 2000", cwd=str(tmp_path))
    assert result["stdout"] == "\n".join(str(i) for i in range(1, 2001)) + "\n"
    assert "cut" not in result


@pytest.mark.asyncio
async def test_a_huge_output_keeps_its_start_and_end(tmp_path):
    from optimux_backend.core import executor
    head, tail = executor.OUTPUT_CAPS["stdout"]
    result = await run_shell_command("echo FIRST; head -c 3000000 /dev/zero | tr '\\0' x; echo; echo LAST; echo oops >&2",
                                     cwd=str(tmp_path))
    out = result["stdout"]
    assert result["exit_code"] == 0
    assert out.startswith("FIRST\n") and out.endswith("LAST\n")
    assert len(out) < head + tail + 400
    assert result["cut"] == 3000012 - head - tail                       # FIRST\n + 3 MB + \n + LAST\n
    assert f"{result['cut']:,} bytes of output cut here" in out and "grep, head, tail" in out
    assert result["stderr"] == "oops\n"                                  # its own cap, not affected


@pytest.mark.asyncio
async def test_stderr_has_its_own_cap(tmp_path):
    from optimux_backend.core import executor
    head, tail = executor.OUTPUT_CAPS["stderr"]
    result = await run_shell_command("head -c 100000 /dev/zero | tr '\\0' e >&2; echo END >&2; exit 3", cwd=str(tmp_path))
    assert result["exit_code"] == 3 and result["stderr"].endswith("END\n")
    assert len(result["stderr"]) < head + tail + 400 and result["cut"] == 100004 - head - tail


@pytest.mark.asyncio
async def test_a_timeout_keeps_what_was_printed(tmp_path):
    result = await run_shell_command("echo started; echo warn >&2; sleep 5", timeout=1, cwd=str(tmp_path))
    assert result["exit_code"] == 124
    assert result["stdout"] == "started\n"
    assert result["stderr"].startswith("warn\n") and result["stderr"].endswith("Command timed out after 1s.")


def test_capped_split_keeps_utf8_readable():
    from optimux_backend.core.executor import _Capped
    c = _Capped(5, 5)
    for piece in ("é" * 50).encode():
        c.add(bytes([piece]))
    assert c.total == 100 and c.cut == 90
    text = c.text()
    assert "90 bytes of output cut" in text and "�" in text      # a split character is replaced, never an error


def test_the_model_and_the_chat_get_the_capped_output(mocker, tmp_path):
    """A command printing 2 MB: the tool message stays small for every later step."""
    import json as _json
    from tests.ws_helpers import Conversation, collect, make_client
    from tests.test_routing_session import Recorder, reply, tool_reply
    import optimux_backend.main as main
    client = make_client(mocker, tmp_path)
    mocker.patch.object(main, "current_workdir", return_value=str(tmp_path))
    mocker.patch("optimux_backend.core.executor.get_active_cwd", return_value=str(tmp_path))
    rec = Recorder(tool_reply("execute_bash", {"command": "head -c 2000000 /dev/zero | tr '\\0' y; echo; echo DONE"}),
                   reply("It printed a lot."))
    mocker.patch("litellm.acompletion", side_effect=rec)
    chat = Conversation(client)
    chat.ws.send_text(_json.dumps({"prompt": "print a lot", "model": "auto"}))
    while _json.loads(chat.ws.receive_text())["type"] != "approval_request":
        pass
    chat.ws.send_text(_json.dumps({"type": "approval_response", "approved": True}))
    msgs = collect(chat.ws)
    chat.close()
    shown = [m for m in msgs if m["type"] == "command_output"][0]
    assert shown["cut"] > 1_900_000 and len(shown["output"]) < 30_000 and shown["output"].endswith("DONE\n")
    tool_msg = [m for m in rec.calls[1]["messages"] if m["role"] == "tool"][0]
    assert len(tool_msg["content"]) < 31_000 and '"cut"' in tool_msg["content"]


@pytest.mark.asyncio
async def test_the_cut_falls_on_whole_lines(tmp_path):
    result = await run_shell_command("seq 1 2000000", cwd=str(tmp_path))
    before, note, after = result["stdout"].split("\n\n")
    first, last = before.split("\n"), after.rstrip("\n").split("\n")
    assert first[:3] == ["1", "2", "3"] and [int(x) for x in first] == list(range(1, len(first) + 1))
    assert last[-1] == "2000000" and [int(x) for x in last] == list(range(2000001 - len(last), 2000001))
    # the count in the note and in "cut" is exactly what's missing
    shown = len(before) + 1 + len(after)
    assert result["cut"] == len(b"".join(f"{i}\n".encode() for i in range(1, 2000001))) - shown
    assert f"{result['cut']:,} bytes of output cut here" in note


def test_one_long_line_is_not_dropped_for_a_line_end():
    from optimux_backend.core.executor import _Capped
    c = _Capped(100, 100)
    c.add(b"a\n" + b"x" * 1000 + b"\nz")
    head, tail = c._parts()
    assert len(head) == 100 and len(tail) == 100          # the line end is too far back: keep the bytes


# ---- IM-03b: at the time limit the command keeps running while the user chooses

def _asker(*choices, log=None):
    queue = list(choices)

    async def ask(seconds):
        if log is not None:
            log.append(round(seconds))
        choice = queue.pop(0)
        if choice == "never":
            await asyncio.sleep(3600)
        return choice
    return ask


@pytest.mark.asyncio
async def test_keep_waiting_lets_it_finish(tmp_path):
    asked = []
    r = await run_shell_command("sleep 1.6; echo done", timeout=1, cwd=str(tmp_path), on_limit=_asker("more", log=asked))
    assert r["exit_code"] == 0 and r["stdout"] == "done\n" and asked == [1]


@pytest.mark.asyncio
async def test_wait_until_it_ends(tmp_path):
    r = await run_shell_command("sleep 2.5; echo done", timeout=1, cwd=str(tmp_path), on_limit=_asker("until_end"))
    assert r["exit_code"] == 0 and r["stdout"] == "done\n"


@pytest.mark.asyncio
async def test_stop_at_the_limit(tmp_path):
    r = await run_shell_command("echo started; sleep 10", timeout=1, cwd=str(tmp_path), on_limit=_asker("stop"))
    assert r["exit_code"] == 124 and r["stopped"] == "user" and r["stdout"] == "started\n"
    assert "the user chose to stop it" in r["stderr"]


@pytest.mark.asyncio
async def test_no_answer_stops_after_one_more_limit(tmp_path):
    import time as _t
    t0 = _t.monotonic()
    r = await run_shell_command("sleep 10", timeout=1, cwd=str(tmp_path), on_limit=_asker("never"))
    assert r["stopped"] == "no_answer" and "nobody answered" in r["stderr"] and 1.8 < _t.monotonic() - t0 < 4


@pytest.mark.asyncio
async def test_the_question_goes_away_when_it_ends(tmp_path):
    cancelled = []

    async def ask(seconds):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
    r = await run_shell_command("sleep 1.5; echo done", timeout=1, cwd=str(tmp_path), on_limit=ask)
    assert r["stdout"] == "done\n" and r["exit_code"] == 0 and cancelled == [True]


@pytest.mark.asyncio
async def test_no_limit(tmp_path):
    r = await run_shell_command("sleep 1.2; echo done", timeout=0, cwd=str(tmp_path))
    assert r["stdout"] == "done\n"


@pytest.mark.asyncio
async def test_stop_while_asking_ends_the_command(tmp_path):
    marker = tmp_path / "still-running"
    asked = asyncio.Event()

    async def ask(seconds):
        asked.set()
        await asyncio.sleep(3600)
    task = asyncio.ensure_future(run_shell_command(f"sleep 3 && touch {marker}", timeout=1, cwd=str(tmp_path),
                                                   on_limit=ask))
    await asyncio.wait_for(asked.wait(), 5)
    task.cancel()                                     # the user pressed Stop
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(3)
    assert not marker.exists()                        # the command was killed, not left running
