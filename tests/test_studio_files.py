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


# ---- search by name and the dev-server preview (v0.8)

def test_search_finds_names_first_and_skips_ignored_and_hidden(ws):
    p, _ = ws
    for f in ("src/app/Button.vue", "src/app/button-test.js", "docs/buttons.md", "node_modules/button/index.js",
              ".github/button.yml", "about.html"):
        (p / f).parent.mkdir(parents=True, exist_ok=True)
        (p / f).write_text("x")
    r = pf.search(str(p), "button")                     # no git: hidden folders skipped
    paths = [x["path"] for x in r["results"]]
    assert ".github/button.yml" not in paths and "node_modules/button" in paths   # without git, nothing is "ignored"
    subprocess.run(["git", "init", "-q"], cwd=p, check=True)
    (p / ".gitignore").write_text("node_modules/\n")
    r = pf.search(str(p), "BUTTON")
    paths = [x["path"] for x in r["results"]]
    assert not any(x.startswith("node_modules") for x in paths) and ".github/button.yml" not in paths
    assert set(paths[:3]) == {"src/app/Button.vue", "src/app/button-test.js", "docs/buttons.md"}   # names that start with it
    assert r["total"] == len(paths)
    assert [x["path"] for x in pf.search(str(p), "app vue")["results"]] == ["src/app/Button.vue"]   # every word, in the path
    assert pf.search(str(p), "src")["results"][0] == {"path": "src", "name": "src", "type": "dir"}  # folders first
    assert pf.search(str(p), "   ")["results"] == []


def test_the_search_api_and_the_dev_server_frames(mocker, tmp_path, ws):
    p, _ = ws
    client = make_client(mocker, tmp_path)
    r = client.get("/api/files/search", params={"q": "a.css"}, headers=H).json()
    assert r["project"] == "site" and r["results"][0]["path"] == "css/a.css"
    mocker.patch("optimux_backend.core.auth.check_session", return_value="login-1")
    csp = client.get("/").headers["content-security-policy"]
    assert "frame-src 'self' http://localhost:* http://127.0.0.1:*;" in csp and "frame-ancestors 'none'" in csp


# ---- "Open in file": where an element of the preview comes from (v0.8)

PAGE = """<!doctype html>
<html><body>
  <section class="hero">
    <h1 class="title big">Build. Share.</h1>
    <p class="lead">First paragraph</p>
    <p class="lead">Second
       paragraph</p>
    <img src="logo.png" alt="logo">
  </section>
  <div id="cards"><div class="card"><h3>Share</h3></div></div>
</body></html>
"""


def test_locate_finds_the_start_tag_and_the_whole_element(ws):
    p, _ = ws
    (p / "index.html").write_text(PAGE)
    r = pf.locate(str(p), "index.html", {"tag": "h1", "classes": ["title", "big"], "text": "Build. Share.", "index": 0})
    assert r == {"found": True, "path": "index.html", "from": {"line": 4, "col": 4}, "to": {"line": 4, "col": 44}, "how": "page"}
    second = pf.locate(str(p), "index.html", {"tag": "p", "classes": ["lead"], "text": "Second paragraph", "index": 1})
    assert (second["from"]["line"], second["to"]["line"]) == (6, 7)              # the text decides between the two
    img = pf.locate(str(p), "index.html", {"tag": "img", "attrs": {"src": "logo.png", "alt": "logo"}, "index": 0})
    assert img["from"]["line"] == 8 and img["to"] == {"line": 8, "col": 35}      # a void element: its tag
    div = pf.locate(str(p), "index.html", {"tag": "div", "id": "cards", "classes": [], "index": 0})
    assert div["from"] == {"line": 10, "col": 2} and div["to"]["col"] == len('  <div id="cards"><div class="card"><h3>Share</h3></div></div>')


def test_locate_falls_back_to_the_text_in_the_project_files(ws):
    p, _ = ws
    (p / "index.html").write_text("<!doctype html><div id=app></div><script src=app.js></script>\n")
    (p / "app.js").write_text("const x = 1;\nrender(`<h2 class=\"made\">Made by\n    a script</h2>`);\n")
    r = pf.locate(str(p), "index.html", {"tag": "h2", "classes": ["made"], "text": "Made by a script", "index": 0})
    assert r["path"] == "app.js" and r["how"] == "text" and r["from"] == {"line": 2, "col": 25} and r["to"]["line"] == 3
    assert pf.locate(str(p), "index.html", {"tag": "h2", "text": "nowhere at all"}) == {"found": False}
    with pytest.raises(pf.FilesError):
        pf.locate(str(p), "index.html", {"tag": "<script>"})
    assert pf.locate(str(p), "../../outside.txt", {"tag": "h1", "text": "secret outside"}) == {"found": False}   # never read outside


def test_the_locate_api(mocker, tmp_path, ws):
    p, _ = ws
    (p / "index.html").write_text(PAGE)
    client = make_client(mocker, tmp_path)
    r = client.post("/api/files/locate", headers=H, json={"page": "index.html", "element": {"tag": "h3", "text": "Share", "index": 0}})
    assert r.status_code == 200 and r.json()["from"]["line"] == 10


# ---- "Open the CSS" and "Open the script" (v0.8)

def test_style_rules_lists_local_sheets_and_style_blocks_in_order(ws):
    p, _ = ws
    (p / "pages").mkdir()
    (p / "css" / "site.css").write_text("/* top */\n.lead { color: red }\n@media (max-width: 600px) {\n  p.lead, h1 { margin: 0 }\n}\n"
                                        "@font-face { font-family: x }\n.btn:hover::after { content: \"}\" }\n")
    (p / "pages" / "about.html").write_text(
        '<!doctype html><link rel="stylesheet" href="../css/site.css"><link rel="stylesheet" href="https://cdn.example/x.css">\n'
        '<link rel=icon href=fav.ico><style>\n.lead { font-size: 2rem }\n</style><!-- <style>.no{}</style> -->\n<p class="lead">x</p>\n')
    r = pf.style_rules(str(p), "pages/about.html")
    assert r["sheets"] == ["css/site.css"] and r["truncated"] is False
    got = [(x["path"], x["selectors"], x["media"], x["from"]["line"]) for x in r["rules"]]
    assert got == [("css/site.css", ".lead", "", 2), ("css/site.css", "p.lead, h1", "@media (max-width: 600px)", 4),
                   ("css/site.css", ".btn:hover::after", "", 7), ("pages/about.html", ".lead", "", 3)]
    assert r["rules"][1]["to"] == {"line": 4, "col": 26}
    with pytest.raises(pf.FilesError):
        pf.style_rules(str(p), "css/site.css")
    (p / "pages" / "bad.html").write_text('<link rel=stylesheet href="../../../outside.txt"><link rel=stylesheet href="/css/site.css">')
    assert pf.style_rules(str(p), "pages/bad.html")["sheets"] == ["css/site.css"]      # never outside the project


def test_script_refs_finds_lines_naming_the_element(ws):
    p, _ = ws
    (p / "js").mkdir()
    (p / "js" / "app.js").write_text("const cards = document.getElementById('cards');\nconst x = 'cardsx';\n"
                                     "document.querySelectorAll('.card').forEach(c => c.addEventListener('click', go));\n"
                                     "el.dataset.action; // data-action\n")
    (p / "index.html").write_text('<div id="cards" class="card" data-action="open"></div>\n<script src="js/app.js"></script>\n'
                                  "<script>\n  document.querySelector('#cards').hidden = false;\n</script>\n")
    r = pf.script_refs(str(p), "index.html", {"tag": "div", "id": "cards", "classes": ["card"], "attrs": {"data-action": "open"}})
    where = [(m["path"], m["from"]["line"], m["why"]) for m in r["matches"]]
    assert r["where"] == "page" and ("js/app.js", 2, "#cards") not in where           # cardsx isn't cards
    assert where[:2] == [("index.html", 4, "#cards"), ("js/app.js", 1, "#cards")]       # the id first
    assert ("js/app.js", 3, ".card") in where and ("js/app.js", 4, "[data-action]") in where
    (p / "src").mkdir()
    (p / "src" / "Card.jsx").write_text("export default () => <div className=\"widget\">x</div>;\n")
    r = pf.script_refs(str(p), "index.html", {"tag": "div", "classes": ["widget"]})
    assert r["where"] == "project" and r["matches"][0]["path"] == "src/Card.jsx"
    assert pf.script_refs(str(p), "index.html", {"tag": "div"}) == {"matches": [], "where": "page"}


def test_the_styles_and_scripts_api(mocker, tmp_path, ws):
    p, _ = ws
    client = make_client(mocker, tmp_path)
    r = client.get("/api/files/styles", params={"page": "index.html"}, headers=H).json()
    assert r["sheets"] == ["css/a.css"] and r["rules"][0]["selectors"] == "h1"
    r = client.post("/api/files/scripts", headers=H, json={"page": "index.html", "element": {"tag": "h1", "classes": ["nothing"]}})
    assert r.status_code == 200 and r.json()["matches"] == []
