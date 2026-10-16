"""v0.3: restoring snapshots, from the list or from an uploaded (encrypted) file."""
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import time

import pytest

from optimux_backend.core import exports, memory_manager, paths, restore, snapshots, store, workspace
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
GB = 1024 ** 3
OK = restore.CONFIRM_PHRASE


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A data folder, a database with a chat, usage and a sign-in, and a Workspace."""
    h = tmp_path / "home"
    for d in ("config", "memory/projects", "skills/notes", "data", "logs", "backups", "legacy"):
        (h / d).mkdir(parents=True)
    (h / "config" / "settings.json").write_text('{"v": "old"}')
    (h / "memory" / "personal.md").write_text("old personal")
    (h / "memory" / "projects" / "alpha.md").write_text("old alpha memory")
    (h / "skills" / "notes" / "SKILL.md").write_text("old skill")
    (h / "secrets.env").write_text("GEMINI_API_KEY=old-key\n")
    (h / "auth.json").write_text('{"password": "old"}')
    (h / "data" / "optimux.db").write_text("")          # the real copy comes from store.connect()
    ws = tmp_path / "ws"
    (ws / "alpha" / "src").mkdir(parents=True)
    (ws / "alpha" / "src" / "a.py").write_text("old code")
    (ws / "alpha" / "link").symlink_to("src/a.py")
    (ws / "beta").mkdir()
    (ws / "beta" / "README.md").write_text("old beta")
    for name, value in (("HOME", h), ("MEMORY_DIR", h / "memory"), ("SNAPSHOT_DIR", h / "snapshots")):
        monkeypatch.setattr(paths, name, str(value))
    monkeypatch.setattr(memory_manager, "PROJECTS_DIR", str(h / "memory" / "projects"))
    monkeypatch.setattr(memory_manager, "PERSONAL_MD", str(h / "memory" / "personal.md"))
    monkeypatch.setattr(memory_manager, "PROFESSIONAL_MD", str(h / "memory" / "professional.md"))
    monkeypatch.setattr(workspace, "projects_root", lambda: str(ws))
    monkeypatch.setattr(snapshots, "limit_bytes", lambda: 5 * GB)
    monkeypatch.delenv("OPTIMUX_SUPERVISED", raising=False)
    conn = store.connect()
    with conn:
        conn.execute("INSERT INTO sessions (id, project, title, created, updated) VALUES ('old-chat', 'alpha', "
                     "'Old chat', 1, 1)")
        conn.execute("INSERT INTO usage (ts, session, project, role, model, cost) VALUES (1, 'old-chat', 'alpha', "
                     "'simple', 'm', 0.5)")
        conn.execute("INSERT INTO logins (id, created, last_seen) VALUES ('old-login', 1, 1)")
    conn.close()
    return h, ws


def change_everything(h, ws):
    """After the snapshot: new settings, keys, memory, project files, chats, usage, sign-ins."""
    (h / "config" / "settings.json").write_text('{"v": "new"}')
    (h / "memory" / "personal.md").write_text("new personal")
    (h / "secrets.env").write_text("GEMINI_API_KEY=new-key\n")
    (h / "skills" / "notes" / "SKILL.md").write_text("new skill")
    (ws / "alpha" / "src" / "a.py").write_text("new code")
    (ws / "alpha" / "added.txt").write_text("added later")
    conn = store.connect()
    with conn:
        conn.execute("INSERT INTO sessions (id, project, title, created, updated) VALUES ('new-chat', 'alpha', "
                     "'New chat', 2, 2)")
        conn.execute("INSERT INTO usage (ts, session, project, role, model, cost) VALUES (2, 'new-chat', 'alpha', "
                     "'simple', 'm', 0.25)")
        conn.execute("DELETE FROM logins")
        conn.execute("INSERT INTO logins (id, created, last_seen) VALUES ('new-login', 2, 2)")
    conn.close()


def rows(sql):
    conn = store.connect()
    try:
        return [tuple(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def test_inspect_lists_parts_with_safe_defaults(home):
    sid = snapshots.create("full")["id"]
    info = restore.inspect(sid)
    parts = {p["path"]: p for p in info["parts"]}
    assert {"config", "memory", "skills", "data/optimux.db", "secrets.env", "auth.json",
            "workspace/alpha", "workspace/beta"} <= set(parts)
    assert parts["config"]["default"] and parts["data/optimux.db"]["default"] and parts["workspace/alpha"]["default"]
    assert parts["secrets.env"]["default"] is False and parts["auth.json"]["default"] is False   # never by surprise
    assert "usage and cost history" in parts["data/optimux.db"]["note"]
    assert parts["workspace/alpha"]["exists"] and "Archived projects" in parts["workspace/alpha"]["effect"]
    assert info["confirm_phrase"] == OK and not any(p.startswith("memory/projects/") for p in parts)


def test_full_restore_projects_now_data_after_restart_usage_and_sign_ins_kept(home):
    h, ws = home
    sid = snapshots.create("full")["id"]
    change_everything(h, ws)

    result = restore.restore(sid, ["config", "memory", "data/optimux.db", "workspace/alpha"], OK)
    # A snapshot of the current state was taken first (the user's own, never removed automatically)
    before = snapshots.get(result["before_snapshot"])
    assert before["kind"] == "data" and not before["auto"] and sid in before["note"]
    # The project is back now; the current folder is under Archived projects
    assert (ws / "alpha" / "src" / "a.py").read_text() == "old code"
    assert os.path.islink(ws / "alpha" / "link") and not (ws / "alpha" / "added.txt").exists()
    archived = result["archived"][0]
    assert archived.startswith(str(ws / ".archive" / "alpha-")) and open(f"{archived}/added.txt").read() == "added later"
    # Data waits for the restart; chat turns wait too
    assert result["restart_needed"] and restore.pending()["source"] == sid
    assert "restart" in restore.blocked_reason()
    assert (h / "config" / "settings.json").read_text() == '{"v": "new"}'
    with pytest.raises(restore.RestoreError, match="waiting for the restart"):
        restore.restore(sid, ["config"], OK)

    lines = restore.apply_pending()                       # what happens at the next start
    assert "restored" in lines[0]
    assert (h / "config" / "settings.json").read_text() == '{"v": "old"}'
    assert (h / "memory" / "personal.md").read_text() == "old personal"
    assert (h / "secrets.env").read_text() == "GEMINI_API_KEY=new-key\n"          # not chosen: untouched
    assert (h / "skills" / "notes" / "SKILL.md").read_text() == "new skill"
    # Chats come back as they were; usage and cost history and sign-ins are never rolled back
    assert rows("SELECT id FROM sessions ORDER BY id") == [("old-chat",)]
    assert rows("SELECT session, cost FROM usage ORDER BY ts") == [("old-chat", 0.5), ("new-chat", 0.25)]
    assert rows("SELECT id FROM logins") == [("new-login",)]
    # Nothing is deleted: the replaced files are in the legacy folder
    last = restore.last_result()
    assert last["ok"] and last["aside"].startswith(str(h / "legacy" / "before-restore-"))
    assert open(os.path.join(last["aside"], "config", "settings.json")).read() == '{"v": "new"}'
    assert os.path.exists(os.path.join(last["aside"], "data", "optimux.db"))
    assert restore.pending() is None and not os.path.exists(restore.pending_dir()) and not restore.blocked_reason()
    restore.dismiss_last()
    assert restore.last_result() is None


def test_projects_only_needs_no_restart_and_no_data_snapshot(home):
    h, ws = home
    sid = snapshots.create("workspace", projects=["alpha"])["id"]
    (h / "memory" / "projects" / "alpha.md").write_text("new alpha memory")
    (ws / "alpha" / "src" / "a.py").write_text("new code")
    count = len(snapshots.list_snapshots())
    result = restore.restore(sid, ["workspace/alpha"], OK)
    assert result == {"source": sid, "projects": ["alpha"], "archived": result["archived"], "before_snapshot": None,
                      "data": [], "restart_needed": False, "skipped_links": []}
    assert len(snapshots.list_snapshots()) == count and restore.pending() is None
    assert (ws / "alpha" / "src" / "a.py").read_text() == "old code"
    # Its memory came back with it, and the change is in the memory history (undo)
    assert (h / "memory" / "projects" / "alpha.md").read_text() == "old alpha memory"
    assert rows("SELECT source, before, after FROM memory_versions") == [
        ("restore", "new alpha memory", "old alpha memory")]


def test_a_deleted_project_comes_back_as_new(home):
    h, ws = home
    sid = snapshots.create("workspace")["id"]
    workspace.archive_project("beta")
    assert restore.inspect(sid)["parts"][1]["exists"] is False
    result = restore.restore(sid, ["workspace/beta"], OK)
    assert (ws / "beta" / "README.md").read_text() == "old beta" and result["archived"] == []


def test_refusals(home):
    sid = snapshots.create("full")["id"]
    with pytest.raises(restore.RestoreError, match="Type"):
        restore.restore(sid, ["config"], "yes")
    with pytest.raises(restore.RestoreError, match="reply is running"):
        restore.restore(sid, ["config"], OK, replies_running=True)
    with pytest.raises(restore.RestoreError, match="at least one"):
        restore.restore(sid, [], OK)
    with pytest.raises(restore.RestoreError, match="Not in this snapshot"):
        restore.restore(sid, ["workspace/gamma"], OK)
    with pytest.raises(snapshots.SnapshotError):
        restore.restore("../etc", ["config"], OK)
    assert restore.pending() is None and not restore.busy()


def test_cancel_moves_the_staged_files_aside(home):
    h, _ = home
    sid = snapshots.create("data")["id"]
    restore.restore(sid, ["config"], OK)
    target = restore.cancel_pending()
    assert restore.pending() is None and os.path.isdir(os.path.join(target, restore.PENDING_NAME, "config"))
    assert restore.apply_pending() == []
    with pytest.raises(restore.RestoreError):
        restore.cancel_pending()


def test_a_failure_at_start_puts_everything_back(home, monkeypatch):
    h, ws = home
    sid = snapshots.create("data")["id"]
    change_everything(h, ws)
    restore.restore(sid, ["config", "memory", "skills"], OK)
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        if str(src).endswith(os.path.join(restore.PENDING_NAME, "skills")):
            raise OSError("disk trouble")
        calls["n"] += 1
        return real(src, dst)
    monkeypatch.setattr(restore.os, "replace", flaky)
    lines = restore.apply_pending()
    monkeypatch.setattr(restore.os, "replace", real)
    assert "FAILED" in lines[0]
    assert (h / "config" / "settings.json").read_text() == '{"v": "new"}'       # undone
    assert (h / "memory" / "personal.md").read_text() == "new personal"
    assert (h / "skills" / "notes" / "SKILL.md").read_text() == "new skill"
    last = restore.last_result()
    assert last["ok"] is False and "disk trouble" in last["error"] and restore.pending() is None


# ------------------------------------------------------------------ hostile and damaged archives

def build(path, members, manifest_items=None, sums=True, tamper=None, first=None):
    """A snapshot-shaped archive. members: (name, type, data_or_target)."""
    items = manifest_items or [{"path": "workspace/alpha", "label": "Project alpha", "files": 1, "bytes": 1}]
    manifest = {"format": snapshots.FORMAT, "format_version": 1, "id": "x", "kind": "workspace",
                "created": time.time(), "items": items, "projects": [{"name": "alpha"}]}
    lines = []
    with tarfile.open(path, "w:gz") as tar:
        def add(name, data=b"", kind=tarfile.REGTYPE, target=""):
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = target
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        add(*(first or ("manifest.json", json.dumps(manifest).encode())))
        for name, kind, value in members:
            if kind == "file":
                lines.append(f"{hashlib.sha256(value).hexdigest()}  {name}\n")
                add(name, (tamper or {}).get(name, value))
            elif kind == "dir":
                add(name, kind=tarfile.DIRTYPE)
            elif kind == "sym":
                add(name, kind=tarfile.SYMTYPE, target=value)
            elif kind == "hard":
                add(name, kind=tarfile.LNKTYPE, target=value)
            elif kind == "fifo":
                add(name, kind=tarfile.FIFOTYPE)
        if sums:
            add("SHA256SUMS", "".join(lines).encode())
    return path


GOOD = [("workspace/alpha", "dir", None), ("workspace/alpha/a.py", "file", b"code")]


@pytest.mark.parametrize("members,match", [
    (GOOD + [("workspace/alpha/../../evil", "file", b"x")], "unsafe path"),
    (GOOD + [("/etc/evil", "file", b"x")], "unsafe path"),
    (GOOD + [("workspace/alpha/out", "sym", "../../../victim"),
             ("workspace/alpha/out/passwd", "file", b"x")], "through a link"),
    (GOOD + [("workspace/alpha/h", "hard", "workspace/alpha/a.py")], "special file"),
    (GOOD + [("workspace/alpha/p", "fifo", None)], "special file"),
    (GOOD + [("secrets.env", "file", b"STEAL=1")], "outside the parts"),
    (GOOD + [("workspace/alpha/a.py", "file", b"again")], "twice"),
])
def test_hostile_archives_write_nothing_outside(tmp_path, members, match):
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "victim").mkdir()              # where the link points: must stay empty
    with pytest.raises(restore.RestoreError, match=match):
        restore.scan(build(tmp_path / "x.tar.gz", members), str(out), lambda n: True)
    assert not (tmp_path / "evil").exists() and not os.path.exists("/etc/evil")
    assert os.listdir(tmp_path / "victim") == []


@pytest.mark.parametrize("kwargs,match", [
    ({"tamper": {"workspace/alpha/a.py": b"evil"}}, "doesn't match its checksums"),
    ({"sums": False}, "SHA256SUMS"),
    ({"first": ("README.md", b"hi")}, "manifest.json must come first"),
    ({"manifest_items": [{"path": "../etc"}]}, "unknown part"),
    ({"first": ("manifest.json", json.dumps({"format": "optimux-snapshot", "format_version": 99, "kind": "full",
                                              "items": []}).encode())}, "newer Optimux"),
])
def test_damaged_or_foreign_archives_are_refused(tmp_path, kwargs, match):
    with pytest.raises(restore.RestoreError, match=match):
        restore.scan(build(tmp_path / "x.tar.gz", GOOD, **kwargs))


def test_a_tampered_snapshot_restores_nothing(home, tmp_path):
    h, ws = home
    sid = snapshots.create("workspace", projects=["alpha"])["id"]
    path = snapshots.archive_path(sid)
    with tarfile.open(path) as tar:                         # rebuild with one changed byte, same SHA256SUMS
        members = [(m, tar.extractfile(m).read() if m.isreg() else None) for m in tar.getmembers()]
    with tarfile.open(path, "w:gz") as tar:
        for m, data in members:
            if m.name == "workspace/alpha/src/a.py":
                data = b"evil code"
                m.size = len(data)
            tar.addfile(m, io.BytesIO(data) if data is not None else None)
    (ws / "alpha" / "src" / "a.py").write_text("current code")
    with pytest.raises(restore.RestoreError, match="checksums"):
        restore.restore(sid, ["workspace/alpha"], OK)
    assert (ws / "alpha" / "src" / "a.py").read_text() == "current code"
    assert not (ws / ".archive").exists() and not os.path.exists(h / restore.STAGING_NAME)


def test_a_database_from_a_newer_optimux_is_refused(home):
    h, _ = home
    conn = store.connect()
    with conn:
        conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    conn.close()
    sid = snapshots.create("data")["id"]
    with pytest.raises(restore.RestoreError, match="newer Optimux"):
        restore.restore(sid, ["data/optimux.db"], OK)
    assert restore.pending() is None


# ------------------------------------------------------------------ uploads

def upload(h, data: bytes):
    token, path = restore.new_upload()
    with open(path, "wb") as f:
        f.write(data)
    return token


def test_import_a_plain_download(home):
    h, ws = home
    sid = snapshots.create("workspace")["id"]
    data = open(snapshots.archive_path(sid), "rb").read()
    snapshots.delete(sid)
    meta = restore.import_upload(upload(h, data), filename="optimux-snapshot-x.tar.gz")
    assert meta["status"] == "ready" and meta["uploaded"] and meta["kind"] == "workspace"
    assert meta["note"].startswith("Uploaded: optimux-snapshot-x.tar.gz") and meta["original_id"] == sid
    assert sorted(meta["projects"]) == ["alpha", "beta"]
    assert os.listdir(restore.uploads_dir()) == []                 # nothing left behind
    assert restore.inspect(meta["id"])["parts"]


def test_import_an_encrypted_download_with_its_key(home):
    h, ws = home
    sid = snapshots.create("data")["id"]
    prepared = exports.prepare(sid, "key")
    path, _ = exports.export_file(prepared["url"].rsplit("/", 1)[1])
    data = open(path, "rb").read()
    key_file = f"# Optimux snapshot key\n# Open with: age -d ...\n{prepared['key']}\n"
    with pytest.raises(restore.RestoreError, match="AGE-SECRET-KEY"):
        restore.import_upload(upload(h, data), key="")
    with pytest.raises(restore.RestoreError, match="never paste a private SSH key"):
        restore.import_upload(upload(h, data), key="-----BEGIN OPENSSH PRIVATE KEY-----\nabc")
    restore.sweep_uploads(now=time.time() + restore.UPLOAD_TTL + 1)     # the two left for a retry expire
    import pyrage
    other = str(pyrage.x25519.Identity.generate())
    token = upload(h, data)
    with pytest.raises(restore.RestoreError, match="doesn't open the file"):
        restore.import_upload(token, key=other)
    with pytest.raises(restore.RestoreError, match="not valid"):
        restore.import_upload(token, key="AGE-SECRET-KEY-1WRONG")
    meta = restore.import_upload(token, key=key_file, filename="x.tar.gz.age")   # same upload, right key
    assert meta["kind"] == "data" and meta["includes_secrets"] and meta["status"] == "ready"
    assert os.listdir(restore.uploads_dir()) == []


def test_a_broken_upload_is_refused_and_removed(home):
    h, _ = home
    with pytest.raises(restore.RestoreError, match="damaged"):
        restore.import_upload(upload(h, b"not a snapshot at all"))
    assert os.listdir(restore.uploads_dir()) == []
    with pytest.raises(restore.RestoreError, match="expired"):
        restore.import_upload("A" * 32)
    with pytest.raises(restore.RestoreError, match="not valid"):
        restore.import_upload("../../etc/passwd")


# ------------------------------------------------------------------ API

@pytest.fixture
def client(home, mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_api_inspect_restore_status_restart(client, home, monkeypatch):
    h, ws = home
    sid = snapshots.create("full")["id"]
    r = client.get(f"/api/snapshots/{sid}/restore", headers=H)
    assert r.status_code == 200 and r.json()["confirm_phrase"] == OK
    r = client.post(f"/api/snapshots/{sid}/restore", headers=H, json={"parts": ["config"], "confirm": "no"})
    assert r.status_code == 422 and "Type" in r.json()["detail"]
    import optimux_backend.main as main
    main.BUSY_PROJECTS["alpha"] = 1
    r = client.post(f"/api/snapshots/{sid}/restore", headers=H, json={"parts": ["config"], "confirm": OK})
    main.BUSY_PROJECTS["alpha"] = 0
    assert r.status_code == 422 and "reply is running" in r.json()["detail"]
    r = client.post(f"/api/snapshots/{sid}/restore", headers=H, json={"parts": ["config"], "confirm": OK})
    assert r.status_code == 200 and r.json()["restart_needed"]
    st = client.get("/api/restore/status", headers=H).json()
    assert st["pending"]["source"] == sid and st["can_restart"] is False
    r = client.post("/api/restore/restart", headers=H)
    assert r.status_code == 422 and "optimux restart" in r.json()["detail"]
    exits = []
    monkeypatch.setenv("OPTIMUX_SUPERVISED", "1")                 # the installer's systemd units set it
    monkeypatch.setattr(main.os, "_exit", lambda code: exits.append(code))
    r = client.post("/api/restore/restart", headers=H)
    assert r.status_code == 200 and r.json() == {"restarting": True}
    assert client.post("/api/restore/cancel", headers=H).status_code == 200
    assert client.get("/api/restore/status", headers=H).json()["pending"] is None


def test_api_upload_and_import(client, home):
    sid = snapshots.create("workspace")["id"]
    data = open(snapshots.archive_path(sid), "rb").read()
    r = client.post("/api/restore/upload", headers={**H, "Content-Type": "application/octet-stream"}, content=data)
    assert r.status_code == 200 and r.json()["encrypted"] is False and r.json()["size"] == len(data)
    r = client.post("/api/restore/import", headers=H, json={"token": r.json()["token"], "filename": "s.tar.gz"})
    assert r.status_code == 200 and r.json()["uploaded"]
    assert client.post("/api/restore/upload", headers=H, content=b"").status_code == 422
    r = client.post("/api/restore/import", headers=H, json={"token": "x"})
    assert r.status_code == 422


def test_chat_turns_wait_while_a_restore_is_pending(client, home):
    sid = snapshots.create("data")["id"]
    restore.restore(sid, ["config"], OK)
    from tests.ws_helpers import run_prompt
    msgs = run_prompt(client, "hello")
    assert any(m["type"] == "system" and "restart" in m.get("content", m.get("text", "")) for m in msgs), msgs
    restore.cancel_pending()


# ------------------------------------------------------------------ review fixes

def test_links_in_the_data_are_never_restored(home, tmp_path):
    """A link in place of secrets.env or the database could point the keys or the
    data anywhere: links are left out of data parts (kept inside projects)."""
    h, ws = home
    victim = tmp_path / "victim.txt"
    victim.write_text("victim")
    items = [{"path": p, "label": p, "files": 1, "bytes": 1} for p in ("secrets.env", "memory", "workspace/alpha")]
    arch = build(tmp_path / "x.tar.gz", [("secrets.env", "sym", str(victim)), ("memory", "dir", None),
                                         ("memory/personal.md", "sym", str(h / "secrets.env")),
                                         ("memory/ok.md", "file", b"fine"),
                                         ("workspace/alpha", "dir", None), ("workspace/alpha/l", "sym", "a.py")],
                 manifest_items=items)
    out = tmp_path / "out"
    out.mkdir()
    found = restore.scan(str(arch), str(out), lambda n: True)
    assert sorted(found["skipped_links"]) == ["memory/personal.md", "secrets.env"]
    assert not os.path.lexists(out / "secrets.env") and not os.path.lexists(out / "memory" / "personal.md")
    assert (out / "memory" / "ok.md").read_text() == "fine" and os.path.islink(out / "workspace" / "alpha" / "l")


def test_restoring_an_automatic_snapshot_never_removes_it(home, monkeypatch):
    h, ws = home
    sid = snapshots.create("data", auto=True)["id"]
    size = snapshots.get(sid)["size_bytes"]
    monkeypatch.setattr(snapshots, "limit_bytes", lambda: size + 1)     # the new snapshot needs its room
    with pytest.raises(restore.RestoreError, match="snapshot of the current state"):
        restore.restore(sid, ["config"], OK)
    assert snapshots.get(sid)["status"] == "ready"                       # still there
    assert restore.pending() is None and not snapshots._held


def test_a_damaged_archive_is_found_before_the_safety_snapshot(home):
    h, ws = home
    sid = snapshots.create("data")["id"]
    path = snapshots.archive_path(sid)
    data = open(path, "rb").read()
    open(path, "wb").write(data[: len(data) // 2])                       # cut in half
    count = len(snapshots.list_snapshots())
    with pytest.raises(restore.RestoreError, match="damaged or incomplete"):
        restore.restore(sid, ["config"], OK)
    assert len(snapshots.list_snapshots()) == count                      # no snapshot taken for nothing


def test_an_oversized_header_is_refused_without_using_memory(tmp_path):
    path = tmp_path / "bomb.tar.gz"
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("manifest.json")
        info.pax_headers = {"comment": "x" * (restore.MAX_EXTENDED_HEADER + 10)}
        tar.addfile(info, io.BytesIO(b""))
    with pytest.raises(restore.RestoreError, match="damaged"):
        restore.scan(str(path))


def test_a_failed_copy_into_the_workspace_changes_nothing(home, monkeypatch):
    h, ws = home
    sid = snapshots.create("workspace", projects=["alpha"])["id"]
    (ws / "alpha" / "src" / "a.py").write_text("current code")

    def broken(src, dst):
        os.makedirs(dst)
        open(os.path.join(dst, "half"), "w").close()
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(restore.shutil, "move", broken)
    with pytest.raises(restore.RestoreError, match="No space left"):
        restore.restore(sid, ["workspace/alpha"], OK)
    assert (ws / "alpha" / "src" / "a.py").read_text() == "current code"   # not archived, not replaced
    assert not (ws / ".archive").exists() and not [n for n in os.listdir(ws) if n.startswith(".restore-")]


def test_project_memory_with_the_database_goes_into_the_restored_history(home):
    h, ws = home
    sid = snapshots.create("full")["id"]
    (h / "memory" / "projects" / "alpha.md").write_text("new alpha memory")
    restore.restore(sid, ["data/optimux.db", "workspace/alpha"], OK)
    assert (h / "memory" / "projects" / "alpha.md").read_text() == "new alpha memory"   # not yet
    assert "restore: restored" in restore.apply_pending()[0]
    assert (h / "memory" / "projects" / "alpha.md").read_text() == "old alpha memory"
    assert rows("SELECT source, before, after FROM memory_versions") == [
        ("restore", "new alpha memory", "old alpha memory")]


def test_start_never_fails_because_of_a_restore(home, monkeypatch):
    sid = snapshots.create("data")["id"]
    restore.restore(sid, ["config"], OK)

    def boom(prefix):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(restore, "_aside_dir", boom)
    lines = restore.apply_pending()
    assert "could not be applied" in lines[0] or "FAILED" in lines[0]
    assert restore.pending()                                             # kept for the next start


def test_changes_wait_while_a_restore_is_pending(client, home):
    sid = snapshots.create("data")["id"]
    restore.restore(sid, ["config"], OK)
    r = client.post("/api/settings", headers=H, json={})
    assert r.status_code == 422 and "restart" in r.json()["detail"]
    assert client.post("/api/snapshots/preview", headers=H, json={"kind": "data"}).status_code == 200
    assert client.post("/api/restore/cancel", headers=H).status_code == 200
    r = client.post("/api/settings", headers=H, json={})
    assert "restart" not in str(r.json().get("detail", ""))             # allowed again
