"""IM-06 (v0.5.2): a tab opened before an update offers to reload."""
import json
import os
import shutil

import optimux_backend.main as main
from tests.ws_helpers import make_client


def test_auth_ok_carries_the_build_and_version(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "test-token"}))
        ok = json.loads(ws.receive_text())
    assert ok["type"] == "auth_ok" and len(ok["build"]) == 12 and ok["build"] == main.page_build()
    assert "version" in ok


def test_the_build_changes_with_the_page_files(tmp_path, monkeypatch):
    repo = os.path.dirname(main.WEB_DIR)
    copy = tmp_path / "repo"
    (copy / "web").mkdir(parents=True)
    for rel in main.PAGE_FILES:
        if os.path.exists(os.path.join(repo, rel)):
            shutil.copy2(os.path.join(repo, rel), copy / rel)
    monkeypatch.setattr(main, "WEB_DIR", str(copy / "web"))
    main._build_cache["key"] = None
    first = main.page_build()
    assert main.page_build() == first                              # cached, same files
    with open(copy / "web" / "app.js", "a") as f:
        f.write("\n// a new release\n")
    assert main.page_build() != first
    main._build_cache["key"] = None
