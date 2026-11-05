"""v0.8: the Studio's file operations: Expand all, new folder, rename and Delete
(moved to the Trash, never deleted for good). core/project_files.py, core/trash.py,
/api/files/expand|folder|rename|delete."""
import os
import subprocess

import pytest

from optimux_backend.core import paths, project_files as pf, trash
from tests.test_studio import H, ws  # noqa: F401  (the fixture)
from tests.ws_helpers import make_client


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The desktop Trash in the test's folder (same filesystem as the Workspace)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    return tmp_path / "share" / "Trash"


def test_expand_all_opens_every_folder_but_ignored_hidden_and_linked_ones(ws, tmp_path):
    p, _ = ws
    for d in ("src/app/parts", "node_modules/x", ".github/workflows", "docs"):
        (p / d).mkdir(parents=True)
    os.symlink(p / "docs", p / "docs-link")
    subprocess.run(["git", "init", "-q"], cwd=p, check=True)
    (p / ".gitignore").write_text("node_modules/\n")
    r = pf.expand_all(str(p))
    assert set(r["expanded"]) == {"css", "src", "src/app", "src/app/parts", "docs"}
    assert set(r["folders"]) == {"", "css", "src", "src/app", "src/app/parts", "docs"}
    assert r["truncated"] is False


def test_new_folder_and_rename_stay_inside_the_project(ws):
    p, _ = ws
    assert pf.make_folder(str(p), "assets/img")["path"] == "assets/img" and (p / "assets" / "img").is_dir()
    r = pf.rename_path(str(p), "css/a.css", "assets/style/main.css")      # moved, folders made on the way
    assert r == {"path": "css/a.css", "to": "assets/style/main.css", "kind": "file"}
    assert (p / "assets" / "style" / "main.css").read_text() == "h1 { color: red }\n"
    assert pf.rename_path(str(p), "assets", "static")["kind"] == "folder" and (p / "static" / "img").is_dir()
    for args, status in ((("index.html", "config.js"), 409),            # taken
                         (("index.html", "../escape.html"), 403),        # out of the project
                         (("index.html", ".git/hooks/x"), 403),
                         ((".git", "git2"), 403),
                         (("", "x"), 400),
                         (("static", "static/inner"), 400),              # a folder into itself
                         (("nope.txt", "x.txt"), 404)):
        with pytest.raises(pf.FilesError) as e:
            pf.rename_path(str(p), *args)
        assert e.value.status == status, args
    for bad, status in (("css", 409), ("../x", 403), (".git/x", 403), ("", 400)):
        with pytest.raises(pf.FilesError) as e:
            pf.make_folder(str(p), bad if bad != "css" else "index.html/x")
        assert e.value.status == status, bad


def test_delete_moves_to_the_desktop_trash_with_its_record(ws, home):
    p, _ = ws
    (p / "old").mkdir()
    (p / "old" / "a.txt").write_text("a")
    info = pf.delete_info(str(p), "old")
    assert info == {"path": "old", "kind": "folder", "files": 1, "folders": 0, "size": 1, "truncated": False,
                    "tracked": 0, "trash": "home"}
    r = pf.delete_path(str(p), "old")
    assert r["trash"] == "home" and not (p / "old").exists()
    assert (home / "files" / "old" / "a.txt").read_text() == "a"
    record = (home / "info" / "old.trashinfo").read_text()
    assert record.startswith("[Trash Info]\nPath=") and str(p / "old") in record and "DeletionDate=" in record
    (p / "old").mkdir()                                                   # the same name again: a new name in the Trash
    pf.delete_path(str(p), "old")
    assert (home / "files" / "old.2").is_dir()
    pf.delete_path(str(p), "link.txt")                                    # a link goes, not what it points to
    assert os.path.islink(home / "files" / "link.txt") and (p.parent.parent / "outside.txt").exists()


def test_delete_falls_back_to_the_workspace_trash_and_never_deletes_for_good(ws, home, monkeypatch):
    p, settings = ws
    real_same = trash._same_device
    monkeypatch.setattr(trash, "_same_device", lambda path, t: t != trash.home_trash() and real_same(path, t))
    assert pf.delete_info(str(p), "config.js")["trash"] == "workspace"
    r = pf.delete_path(str(p), "config.js")
    ws_trash = os.path.join(settings["projects_root"], ".trash")
    assert r["trash"] == "workspace" and os.path.isfile(os.path.join(ws_trash, "files", "config.js"))
    monkeypatch.setattr(trash, "_same_device", lambda path, t: False)
    assert pf.delete_info(str(p), "index.html")["trash"] is None
    with pytest.raises(pf.FilesError) as e:
        pf.delete_path(str(p), "index.html")
    assert e.value.status == 409 and "Nothing was deleted" in str(e.value) and (p / "index.html").exists()


def test_delete_refuses_git_the_project_and_optimux_data(ws, home, monkeypatch):
    p, _ = ws
    (p / "data").mkdir()
    monkeypatch.setattr(paths, "HOME", str(p / "data"))
    for bad, status in ((".git", 403), (".git/config", 403), ("", 400), ("data", 403), ("../other", 403)):
        with pytest.raises(pf.FilesError) as e:
            pf.delete_path(str(p), bad)
        assert e.value.status == status, bad
    assert (p / ".git" / "config").exists() and (p / "data").is_dir()


def test_the_file_operations_api(mocker, tmp_path, ws, home):
    p, settings = ws
    client = make_client(mocker, tmp_path)
    assert client.get("/api/files/expand", headers=H).json()["expanded"] == ["css"]
    r = client.post("/api/files/folder", headers=H, json={"path": "img", "project": "site"})
    assert r.status_code == 200 and (p / "img").is_dir()
    new = client.post("/api/files/save", headers=H, json={"path": "img/notes.md", "text": "", "digest": None, "project": "site"})
    assert new.status_code == 200 and (p / "img" / "notes.md").read_text() == ""
    assert client.post("/api/files/rename", headers=H, json={"path": "img/notes.md", "to": "img/readme.md", "project": "site"}).json()["to"] == "img/readme.md"
    assert client.get("/api/files/delete", params={"path": "img"}, headers=H).json()["files"] == 1
    other = client.post("/api/files/delete", headers=H, json={"path": "img", "project": "other"})
    assert other.status_code == 409 and (p / "img").is_dir()              # asked for another project
    assert client.post("/api/files/delete", headers=H, json={"path": "img", "project": "site"}).json()["trash"] == "home"
    assert not (p / "img").exists() and (home / "files" / "img" / "readme.md").exists()
    assert client.post("/api/files/rename", headers=H, json={"path": "index.html", "to": "../x.html"}).status_code == 403
