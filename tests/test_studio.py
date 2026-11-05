"""v0.8: the Studio. The active project's files (core/project_files.py, /api/files/*),
the user's saves (files.prepare_user_save) and the sandboxed preview (/preview/...)."""
import os
import re

import pytest

from optimux_backend.core import config_manager, files, project_files as pf, security
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
KEY = "sk-ant-0123456789abcdefghij"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    p = root / "site"
    (p / "css").mkdir(parents=True)
    (p / "index.html").write_text('<!doctype html><link rel="stylesheet" href="css/a.css"><h1>Hi</h1>\n')
    (p / "css" / "a.css").write_text("h1 { color: red }\n")
    (p / "config.js").write_text(f'const key = "{KEY}";\n')
    (p / ".env").write_text(f"ANTHROPIC_API_KEY={KEY}\n")
    (p / ".git").mkdir()
    (p / ".git" / "config").write_text("[core]\n")
    (p / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0")
    (tmp_path / "outside.txt").write_text("secret outside")
    os.symlink(tmp_path / "outside.txt", p / "link.txt")
    (root / "other").mkdir()
    settings = {"projects_root": str(root), "active_project": "site"}
    monkeypatch.setattr(config_manager, "get_settings", lambda: dict(settings))
    monkeypatch.setattr(security, "secret_values", lambda: [("ANTHROPIC_API_KEY", KEY)])
    monkeypatch.setattr(files, "secret_values", lambda: [("ANTHROPIC_API_KEY", KEY)])   # files.py imported it by name
    return p, settings


# ---------------------------------------------------------------- files

def test_listing_hides_git_and_marks_links_out(ws):
    p, _ = ws
    names = {e["name"]: e["type"] for e in pf.list_dir(str(p))["entries"]}
    assert ".git" not in names and names["css"] == "dir" and names["index.html"] == "file"
    assert names["link.txt"] == "outside"
    assert [e["name"] for e in pf.list_dir(str(p), "css")["entries"]] == ["a.css"]
    for bad, status in (("../other", 403), (".git", 403), ("/etc", 400), ("nope", 404)):
        with pytest.raises(pf.FilesError) as e:
            pf.list_dir(str(p), bad)
        assert e.value.status == status, bad


def test_reading_hides_saved_keys_and_refuses_what_is_not_text(ws):
    p, _ = ws
    r = pf.read_file(str(p), "config.js")
    assert KEY not in r["text"] and "••••(ANTHROPIC_API_KEY)" in r["text"] and r["hidden"] == 1
    assert r["digest"] == files._digest((p / "config.js").read_text())          # of the file on disk
    assert pf.read_file(str(p), "logo.png")["binary"] is True
    for bad in ("link.txt", ".git/config", "../outside.txt"):
        with pytest.raises(pf.FilesError):
            pf.read_file(str(p), bad)


def test_saving_keeps_the_key_and_refuses_stale_versions(ws):
    p, _ = ws
    r = pf.read_file(str(p), "config.js")
    out = pf.save_file(str(p), "config.js", r["text"].replace("const", "let"), r["digest"])
    assert out["changed"] and (p / "config.js").read_text() == f'let key = "{KEY}";\n'      # the mark became the key
    with pytest.raises(pf.FilesError) as e:
        pf.save_file(str(p), "config.js", "x", r["digest"])                                      # opened before the save
    assert e.value.status == 409
    with pytest.raises(pf.FilesError) as e:
        pf.save_file(str(p), "index.html", "<p>••••(ANTHROPIC_API_KEY)</p>", pf.read_file(str(p), "index.html")["digest"])
    assert e.value.status == 400 and "can't be added" in str(e.value)                       # a key never copied in
    new = pf.save_file(str(p), "js/app.js", "console.log(1)\n", None)
    assert new["op"] == "create" and (p / "js" / "app.js").exists()
    with pytest.raises(pf.FilesError) as e:
        pf.save_file(str(p), "js/app.js", "again", None)
    assert e.value.status == 409
    same = pf.read_file(str(p), "css/a.css")
    assert pf.save_file(str(p), "css/a.css", same["text"], same["digest"])["changed"] is False
    for bad in (".git/hooks/x", "../escape.txt", "link.txt"):
        with pytest.raises(pf.FilesError):
            pf.save_file(str(p), bad, "x", None)


def test_api(mocker, tmp_path, ws):
    p, settings = ws
    client = make_client(mocker, tmp_path)
    for url in ("/api/files/tree", "/api/files/read?path=index.html", "/api/files/status", "/api/files/preview"):
        assert client.get(url).status_code == 401
    tree = client.get("/api/files/tree", headers=H).json()
    assert tree["project"] == "site" and "index.html" in [e["name"] for e in tree["entries"]]
    r = client.get("/api/files/read", params={"path": "css/a.css"}, headers=H).json()
    s = client.post("/api/files/save", headers=H, json={"path": "css/a.css", "text": "h1 {}\n", "digest": r["digest"], "project": "site"})
    assert s.status_code == 200 and (p / "css" / "a.css").read_text() == "h1 {}\n"
    stale = client.post("/api/files/save", headers=H, json={"path": "css/a.css", "text": "x", "digest": r["digest"]})
    assert stale.status_code == 409
    other = client.post("/api/files/save", headers=H, json={"path": "a.txt", "text": "x", "project": "other"})
    assert other.status_code == 409 and "active project" in other.json()["detail"]
    assert client.get("/api/files/read", params={"path": "../outside.txt"}, headers=H).status_code == 403
    assert client.get("/api/files/status", headers=H).json()["project"] == "site"
    pv = client.get("/api/files/preview", headers=H).json()
    assert pv["index"] == "index.html" and pv["base"] == f"/preview/site/{pf.preview_token('site')}/"
    settings["active_project"] = ""
    assert client.get("/api/files/tree", headers=H).status_code == 409


# ---------------------------------------------------------------- the preview

def test_preview_is_sandboxed_and_needs_its_token(mocker, tmp_path, ws):
    p, _ = ws
    client = make_client(mocker, tmp_path)
    base = pf.preview_base("site")
    page = client.get(base)                                       # no sign-in: the token is the key
    assert page.status_code == 200 and "<h1>Hi</h1>" in page.text
    csp = page.headers["content-security-policy"]
    assert csp.startswith("sandbox ") and "allow-same-origin" not in csp and "frame-ancestors 'self'" in csp
    assert page.headers["x-frame-options"] == "SAMEORIGIN" and page.headers["cross-origin-resource-policy"] == "cross-origin"
    assert client.get(base + "css/a.css").headers["content-type"].startswith("text/css")
    js = client.get(base + "config.js")
    assert KEY not in js.text and "••••(ANTHROPIC_API_KEY)" in js.text                    # saved keys hidden
    assert client.get(base + "logo.png").content.startswith(b"\x89PNG")
    for bad, status in ((".env", 403), (".git/config", 403), ("link.txt", 403), ("nope.html", 404)):
        assert client.get(base + bad).status_code == status, bad
    assert client.get(base + "../../other/x").status_code in (403, 404)
    assert client.get(f"/preview/site/{'0' * 32}/").status_code == 404                    # a wrong token
    assert client.get(f"/preview/other/{pf.preview_token('site')}/").status_code == 404     # one project's token only
    redirect = client.get(base + "css", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"].endswith("/css/")


def test_the_page_carries_a_fresh_style_nonce(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    mocker.patch("optimux_backend.core.auth.check_session", return_value="login-1")
    pages = [client.get("/") for _ in range(2)]
    nonces = []
    for r in pages:
        n = re.search(r'<meta name="optimux-style-nonce" content="([^"]+)">', r.text).group(1)
        assert f"style-src 'self' 'nonce-{n}'" in r.headers["content-security-policy"]
        assert "script-src 'self' 'unsafe-eval';" in r.headers["content-security-policy"]   # scripts unchanged
        nonces.append(n)
    assert nonces[0] != nonces[1]
    api = client.get("/api/files/preview", headers=H)
    assert "nonce-" not in api.headers.get("content-security-policy", "")
