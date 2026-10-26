"""IM-12 (v0.7): "Approve all edits in this reply"."""
import json

from tests.test_files import _tool, _until, client, proj  # noqa: F401  (fixtures)
from tests.test_routing_session import reply
from tests.ws_helpers import Conversation, collect


def answer(chat, seen=None, **extra):
    m, before = _until(chat.ws, "approval_request")
    if seen is not None:
        seen.extend(before)
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True, **extra}))
    return m


def test_one_approval_covers_the_edits_of_one_reply(mocker, client, proj):  # noqa: F811
    mocker.patch("optimux_backend.core.executor.get_active_cwd", return_value=str(proj))
    responses = iter([_tool("write_file", {"path": "a.txt", "content": "a\n"}),
                      _tool("write_file", {"path": "b.txt", "content": "b\n"}),
                      _tool("execute_bash", {"command": "true"}),
                      _tool("edit_file", {"path": "b.txt", "old_text": "b", "new_text": "bb"}),
                      reply("Done."),
                      _tool("write_file", {"path": "c.txt", "content": "c\n"}),
                      reply("Done again.")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "write a and b, run true, edit b", "model": "auto"}))
    seen = []
    first = answer(chat, seen, all_edits=True)
    assert first["file_change"]["path"] == "a.txt"
    second = answer(chat, seen)                                 # the command still asks
    assert second["command"] == "true" and not second.get("file_change")
    msgs = seen + collect(chat.ws)
    asked = [m for m in msgs if m["type"] == "approval_request"]
    assert asked == [first, second]                             # b.txt (write and edit) didn't ask
    assert (proj / "a.txt").read_text() == "a\n" and (proj / "b.txt").read_text() == "bb\n"
    assert any("this and the next file edits of this reply" in (m.get("content") or "")
               for m in msgs if m["type"] == "system")
    autos = [m for m in msgs if m["type"] == "command_start" and (m.get("file") or {}).get("auto")]
    assert len(autos) == 2
    # the next reply asks again
    chat.ws.send_text(json.dumps({"prompt": "write c", "model": "auto"}))
    again = answer(chat)
    collect(chat.ws)
    chat.close()
    assert again["file_change"]["path"] == "c.txt" and (proj / "c.txt").exists()


def test_a_plain_approve_keeps_asking(mocker, client, proj):  # noqa: F811
    responses = iter([_tool("write_file", {"path": "a.txt", "content": "a\n"}),
                      _tool("write_file", {"path": "b.txt", "content": "b\n"}),
                      reply("Done.")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "write a and b", "model": "auto"}))
    answer(chat)
    answer(chat)                                                # asked for b.txt too
    collect(chat.ws)
    chat.close()
    assert (proj / "b.txt").exists()
