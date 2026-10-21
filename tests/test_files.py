"""write_file and edit_file (v0.4-5i): scope, exact edits, atomic writes, and the
whole loop through the WebSocket: approval with a diff, the chat line, the result."""
import json
import os
import stat

import pytest

import optimux_backend.main as main
from optimux_backend.core import files, paths
from optimux_backend.core.files import FileToolError
from tests.ws_helpers import Conversation, chunk, collect, fake_stream, make_client
from tests.test_routing_session import reply

DONE = {"prompt_tokens": 5, "completion_tokens": 1}


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "ws" / "demo"
    (p / "site").mkdir(parents=True)
    (p / "site" / "index.html").write_text("<footer>© 2024 MakerHub</footer>\n")
    (p / ".git").mkdir()
    (p / ".git" / "config").write_text("[core]\n")
    return p


# ---------- where a file may be written

@pytest.mark.parametrize("bad", ["../escape.txt", "site/../../escape.txt", "/etc/passwd", "", "   "])
def test_paths_outside_the_project_are_refused(proj, bad):
    with pytest.raises(FileToolError):
        files.prepare_write(str(proj), bad, "x")


def test_git_and_the_project_folder_itself_are_refused(proj):
    for bad in (".git/config", ".git/hooks/pre-commit", "sub/.git/x", ".", str(proj)):
        with pytest.raises(FileToolError):
            files.prepare_write(str(proj), bad, "x")


def test_symlinks_out_of_the_project_are_refused(proj, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("keep\n")
    os.symlink(outside / "secret.txt", proj / "link.txt")
    os.symlink(outside, proj / "linkdir")
    for bad in ("link.txt", "linkdir/new.txt", "linkdir/secret.txt"):
        with pytest.raises(FileToolError, match="outside the project"):
            files.prepare_write(str(proj), bad, "x")
    assert (outside / "secret.txt").read_text() == "keep\n"


def test_a_symlink_inside_the_project_writes_its_target(proj):
    os.symlink(proj / "site" / "index.html", proj / "alias.html")
    change = files.prepare_edit(str(proj), "alias.html", "2024", "2026")
    assert change["path"] == "site/index.html"


def test_optimux_data_folder_is_refused(proj, monkeypatch):
    monkeypatch.setattr(paths, "HOME", str(proj / "site"))
    with pytest.raises(FileToolError, match="Optimux's own data"):
        files.prepare_write(str(proj), "site/new.txt", "x")


def test_absolute_path_inside_the_project_is_fine(proj):
    change = files.prepare_write(str(proj), str(proj / "notes.md"), "hi\n")
    assert change["path"] == "notes.md" and change["op"] == "create"


def test_folders_binary_and_non_utf8_files_are_refused(proj):
    (proj / "img.bin").write_bytes(b"\x89PNG\x00\x01")
    (proj / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    with pytest.raises(FileToolError, match="folder"):
        files.prepare_write(str(proj), "site", "x")
    with pytest.raises(FileToolError, match="binary"):
        files.prepare_edit(str(proj), "img.bin", "PNG", "JPG")
    with pytest.raises(FileToolError, match="UTF-8"):
        files.prepare_edit(str(proj), "latin.txt", "caf", "bar")


def test_size_limit(proj, monkeypatch):
    monkeypatch.setattr(files, "MAX_BYTES", 10)
    with pytest.raises(FileToolError, match="over 10 bytes"):
        files.prepare_write(str(proj), "big.txt", "x" * 11)
    (proj / "big2.txt").write_text("y" * 20)
    with pytest.raises(FileToolError, match="20 bytes"):
        files.prepare_edit(str(proj), "big2.txt", "y", "z")


# ---------- edit_file: exactly one match

def test_edit_needs_exactly_one_match(proj):
    (proj / "a.txt").write_text("one two two\n")
    with pytest.raises(FileToolError, match="found 0 times.*copy the exact text"):
        files.prepare_edit(str(proj), "site/index.html", "&copy; 2024", "&copy; 2026")
    with pytest.raises(FileToolError, match="found 2 times.*more surrounding"):
        files.prepare_edit(str(proj), "a.txt", "two", "three")
    with pytest.raises(FileToolError, match="empty"):
        files.prepare_edit(str(proj), "a.txt", "", "x")
    with pytest.raises(FileToolError, match="the same"):
        files.prepare_edit(str(proj), "a.txt", "one", "one")
    with pytest.raises(FileToolError, match="doesn't exist"):
        files.prepare_edit(str(proj), "missing.txt", "a", "b")


def test_prepare_writes_nothing_and_apply_writes(proj):
    target = proj / "site" / "index.html"
    change = files.prepare_edit(str(proj), "site/index.html", "© 2024", "© 2026")
    assert target.read_text() == "<footer>© 2024 MakerHub</footer>\n"     # nothing yet
    assert change["added"] == 1 and change["removed"] == 1
    assert "-<footer>© 2024 MakerHub</footer>" in change["diff"] and "+<footer>© 2026" in change["diff"]
    assert files.describe(change) == "✎ edit site/index.html (+1 −1)"
    assert "new_text" not in files.public(change)
    result = files.apply(change)
    assert target.read_text() == "<footer>© 2026 MakerHub</footer>\n"
    assert result == {"ok": True, "path": "site/index.html", "op": "edit", "lines_before": 1, "lines_after": 1,
                      "added": 1, "removed": 1}


# ---------- write_file and apply

def test_create_makes_folders_and_keeps_text_exactly(proj):
    text = "line with 'quotes' and \"doubles\" and $vars and `ticks`\nno newline at end"
    change = files.prepare_write(str(proj), "tests/fixtures/sample.txt", text)
    assert files.describe(change) == "✎ create tests/fixtures/sample.txt (2 lines)"
    files.apply(change)
    assert (proj / "tests" / "fixtures" / "sample.txt").read_text() == text


def test_same_content_is_refused(proj):
    with pytest.raises(FileToolError, match="already has exactly"):
        files.prepare_write(str(proj), "site/index.html", "<footer>© 2024 MakerHub</footer>\n")


def test_mode_is_kept_and_new_files_follow_the_umask(proj):
    script = proj / "run.sh"
    script.write_text("echo a\n")
    script.chmod(0o750)
    files.apply(files.prepare_write(str(proj), "run.sh", "echo b\n"))
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    files.apply(files.prepare_write(str(proj), "new.txt", "n\n"))
    assert stat.S_IMODE((proj / "new.txt").stat().st_mode) == 0o666 & ~files._UMASK


def test_a_file_changed_after_prepare_is_not_overwritten(proj):
    change = files.prepare_edit(str(proj), "site/index.html", "2024", "2026")
    (proj / "site" / "index.html").write_text("<footer>© 2025 changed meanwhile</footer>\n")
    with pytest.raises(FileToolError, match="changed while waiting"):
        files.apply(change)
    assert "2025" in (proj / "site" / "index.html").read_text()
    created = files.prepare_write(str(proj), "fresh.txt", "a\n")
    (proj / "fresh.txt").write_text("someone else\n")
    with pytest.raises(FileToolError, match="changed while waiting"):
        files.apply(created)


def test_a_failed_write_leaves_the_old_file_and_no_temp_file(proj, mocker):
    target = proj / "site" / "index.html"
    change = files.prepare_edit(str(proj), "site/index.html", "2024", "2026")
    mocker.patch("os.replace", side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        files.apply(change)
    assert target.read_text() == "<footer>© 2024 MakerHub</footer>\n"
    assert not [n for n in os.listdir(proj / "site") if n.startswith(".optimux-write-")]


def test_a_folder_swapped_for_a_link_after_approval_is_refused(proj, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    change = files.prepare_write(str(proj), "sub/new.txt", "x\n")
    os.symlink(outside, proj / "sub")
    with pytest.raises(FileToolError, match="replaced by a link"):
        files.apply(change)
    assert not (outside / "new.txt").exists()


def test_long_diffs_are_cut(proj, monkeypatch):
    monkeypatch.setattr(files, "DIFF_LINES", 5)
    change = files.prepare_write(str(proj), "many.txt", "".join(f"{i}\n" for i in range(50)))
    assert change["diff"].splitlines()[-1].startswith("… ") and change["added"] == 50


# ---------- mangled text and whole-file rewrites (t9)

def test_control_characters_the_file_did_not_have_are_refused(proj):
    with pytest.raises(FileToolError, match=r"control characters \(U\+0014, first on line 2\)"):
        files.prepare_write(str(proj), "site/index.html", "<footer>\n\x14 2026 MakerHub</footer>\n")
    with pytest.raises(FileToolError, match="U\\+0000"):
        files.prepare_edit(str(proj), "site/index.html", "© 2024", "\x00 2026")
    (proj / "term.log").write_text("\x1b[31mred\x1b[0m\n")              # already there: fine
    files.prepare_edit(str(proj), "term.log", "red", "\x1b[1mbold red")
    files.prepare_write(str(proj), "tabs.txt", "a\tb\r\n\x0cpage\n")   # tab, CR, form feed: normal


def test_emoji_escape_codes_and_broken_emoji_are_refused(proj):
    with pytest.raises(FileToolError, match="escape codes"):
        files.prepare_edit(str(proj), "site/index.html", "© 2024", "\\ud83d\\udce1 2026")
    with pytest.raises(FileToolError, match="broken character"):
        files.prepare_write(str(proj), "x.txt", "half \ud83d of an emoji\n")
    files.prepare_edit(str(proj), "site/index.html", "© 2024", "📡 — © 2026")   # the real characters


def test_write_file_may_not_cut_a_long_file_to_half(proj):
    css = proj / "style.css"
    css.write_text("".join(f".c{i} {{}}\n" for i in range(790)))
    with pytest.raises(FileToolError, match="has 790 lines and this would leave 270. .*Use edit_file"):
        files.prepare_write(str(proj), "style.css", "".join(f".c{i} {{}}\n" for i in range(270)))
    files.prepare_write(str(proj), "style.css", "".join(f".c{i} {{}}\n" for i in range(400)))    # more than half
    small = proj / "small.txt"
    small.write_text("a\nb\nc\n")
    files.prepare_write(str(proj), "small.txt", "")                                            # short file: fine


# ---------- through the WebSocket

def _tool(name, args):
    return fake_stream([chunk(tool_call=(name, args)), chunk(empty=True, usage_obj=DONE)])


@pytest.fixture
def client(mocker, tmp_path, proj):
    c = make_client(mocker, tmp_path)
    mocker.patch.object(main, "current_workdir", return_value=str(proj))
    mocker.patch.object(main, "active_project", return_value="demo")
    return c


def _until(ws, kind):
    seen = []
    while True:
        m = json.loads(ws.receive_text())
        seen.append(m)
        if m["type"] == kind:
            return m, seen


def test_edit_is_approved_with_its_diff_then_written(mocker, client, proj):
    calls = []
    responses = iter([_tool("edit_file", {"path": "site/index.html", "old_text": "© 2024", "new_text": "© 2026"}),
                      reply("Changed the year.")])

    def fake(**kw):
        calls.append(kw)
        return next(responses)
    mocker.patch("litellm.acompletion", side_effect=fake)
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "change the footer year to 2026", "model": "auto"}))
    m, _ = _until(chat.ws, "approval_request")
    assert m["command"] == "✎ edit site/index.html (+1 −1)"
    assert m["file_change"]["path"] == "site/index.html" and "+<footer>© 2026" in m["file_change"]["diff"]
    assert (proj / "site" / "index.html").read_text().count("2024") == 1      # not before approval
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": True}))
    msgs = collect(chat.ws)
    chat.close()
    assert "2026" in (proj / "site" / "index.html").read_text()
    out = [x for x in msgs if x["type"] == "command_output"][0]
    assert out["exit_code"] == 0 and out["file"]["op"] == "edit" and "+<footer>" in out["output"]
    names = {t["function"]["name"] for t in calls[0]["tools"]}
    assert {"write_file", "edit_file", "execute_bash"} <= names
    result = json.loads([m for m in calls[1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert result["ok"] and result["note"] == "site/index.html: 1 -> 1 lines."


def test_a_wrong_old_text_is_shown_and_asks_nothing(mocker, client, proj):
    """Task 4's silent miss: '&copy;' isn't in the page. Now the model and the user see it."""
    calls = []
    responses = iter([_tool("edit_file", {"path": "site/index.html", "old_text": "&copy; 2024", "new_text": "&copy; 2026"}),
                      reply("It failed.")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: calls.append(kw) or next(responses))
    chat = Conversation(client)
    msgs = chat.prompt("change the year")
    chat.close()
    assert not [m for m in msgs if m["type"] == "approval_request"]
    out = [m for m in msgs if m["type"] == "command_output"][0]
    assert out["exit_code"] == 1 and "found 0 times" in out["output"] and out["file"]["op"] == "edit"
    result = json.loads([m for m in calls[1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert result["ok"] is False and "found 0 times" in result["error"]


def test_a_rejected_write_writes_nothing(mocker, client, proj):
    calls = []
    responses = iter([_tool("write_file", {"path": "new.txt", "content": "x\n"}), reply("ok")])
    mocker.patch("litellm.acompletion", side_effect=lambda **kw: calls.append(kw) or next(responses))
    chat = Conversation(client)
    chat.ws.send_text(json.dumps({"prompt": "make a file", "model": "auto"}))
    m, _ = _until(chat.ws, "approval_request")
    assert m["command"] == "✎ create new.txt (1 lines)"
    chat.ws.send_text(json.dumps({"type": "approval_response", "approved": False}))
    collect(chat.ws)
    chat.close()
    assert not (proj / "new.txt").exists()
    result = json.loads([m for m in calls[1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert result["declined"] is True


def test_without_an_active_project_the_tools_are_not_offered(mocker, client):
    mocker.patch.object(main, "active_project", return_value="")
    names = {t["function"]["name"] for t in main.offered_tools({})}
    assert "write_file" not in names and "edit_file" not in names and "execute_bash" in names


def test_prompts_steer_file_writing_to_the_tools():
    from optimux_backend.core.agents import get_executor_prompt
    p = get_executor_prompt("MEM", "/w/demo")
    assert "write_file / edit_file" in p and "never shell here-docs" in p
