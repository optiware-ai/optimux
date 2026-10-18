"""v0.3: snapshots of the data and of the Workspace, kept on this machine."""
import json
import os
import re
import sqlite3
import stat
import tarfile
import time

import pytest

from optimux_backend.core import paths, snapshots, store, workspace
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
GB = 1024 ** 3


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A data folder and a Workspace with two projects, in tmp_path."""
    h = tmp_path / "home"
    for d in ("config", "memory/projects", "skills/notes", "data", "logs", "backups", "legacy"):
        (h / d).mkdir(parents=True)
    (h / "config" / "settings.json").write_text('{"x": 1}')
    (h / "memory" / "personal.md").write_text("I like tea")
    (h / "memory" / "projects" / "alpha.md").write_text("alpha memory")
    (h / "skills" / "notes" / "SKILL.md").write_text("skill")
    (h / "secrets.env").write_text("GEMINI_API_KEY=secret\n")
    (h / "auth.json").write_text("{}")
    (h / "logs" / "server.log").write_text("log")
    (h / "backups" / "optimux-2026-10-01.db").write_text("old")
    (h / "legacy" / "old.txt").write_text("old")
    (h / "data" / "optimux.db").write_text("")          # the real copy comes from store.connect()
    ws = tmp_path / "ws"
    (ws / "alpha" / "src").mkdir(parents=True)
    (ws / "alpha" / "src" / "a.py").write_text("print(1)")
    (ws / "alpha" / ".git").mkdir()
    (ws / "alpha" / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (ws / "alpha" / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "alpha" / "node_modules" / "pkg" / "big.js").write_text("x" * 5000)
    (ws / "alpha" / "outside").symlink_to("/etc/hostname")
    (ws / "beta").mkdir()
    (ws / "beta" / "README.md").write_text("beta")
    (ws / ".archive" / "old-20260101-000000").mkdir(parents=True)
    for name, value in (("HOME", h), ("MEMORY_DIR", h / "memory"), ("SNAPSHOT_DIR", h / "snapshots")):
        monkeypatch.setattr(paths, name, str(value))
    monkeypatch.setattr(workspace, "projects_root", lambda: str(ws))
    monkeypatch.setattr(snapshots, "limit_bytes", lambda: 5 * GB)
    store.connect().close()                               # creates the test database
    return h, ws


def members(path):
    with tarfile.open(path) as tar:
        return {m.name: m for m in tar.getmembers()}


def read(path, name):
    with tarfile.open(path) as tar:
        return tar.extractfile(name).read().decode()


def test_data_snapshot_holds_settings_memory_skills_keys_and_a_real_database(home):
    h, _ = home
    meta = snapshots.create("data", note="before testing GPT routing")
    assert meta["status"] == "ready" and meta["includes_secrets"] is True
    path = snapshots.archive_path(meta["id"])
    names = members(path)
    assert list(names)[:2] == ["manifest.json", "BACKUP-README.md"]       # first, so they are quick to read
    for n in ("config/settings.json", "memory/personal.md", "memory/projects/alpha.md",
              "skills/notes/SKILL.md", "secrets.env", "auth.json", "data/optimux.db"):
        assert n in names, n
    assert not any(n.startswith(("logs", "backups", "legacy", "snapshots")) for n in names)
    with tarfile.open(path) as tar:
        tar.extract("data/optimux.db", h / "check", filter="data") if hasattr(tarfile, "data_filter") \
            else tar.extract("data/optimux.db", h / "check")
    tables = {r[0] for r in sqlite3.connect(h / "check" / "data" / "optimux.db").execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"usage", "sessions", "memory_versions"} <= tables                # a consistent SQLite copy
    manifest = json.loads(read(path, "manifest.json"))
    assert manifest["format"] == "optimux-snapshot" and manifest["kind"] == "data"
    assert manifest["note"] == "before testing GPT routing"
    assert all(m.uid == 0 and m.uname == "" for m in names.values())          # no owner names inside
    # private: only Optimux's own user can read them
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(paths.SNAPSHOT_DIR).st_mode) == 0o700


def test_readme_explains_contents_and_how_to_restore_by_hand(home):
    h, _ = home
    meta = snapshots.create("data")
    text = read(snapshots.archive_path(meta["id"]), "BACKUP-README.md")
    assert "contains your API keys" in text and "### By hand" in text and "sha256sum -c" in text
    assert f"mv {h} {h}.before-restore" in text
    assert f"cp -a restore/config restore/memory restore/skills restore/data restore/secrets.env restore/auth.json {h}/" in text
    assert "secrets.env" in text and "Not included" in text


def test_workspace_snapshot_holds_projects_with_their_memory(home):
    h, ws = home
    meta = snapshots.create("workspace")
    path = snapshots.archive_path(meta["id"])
    names = members(path)
    assert "workspace/alpha/src/a.py" in names and "workspace/alpha/.git/HEAD" in names
    assert "workspace/beta/README.md" in names and "memory/projects/alpha.md" in names
    assert not any("node_modules" in n for n in names)                       # rebuildable: skipped
    assert not any(".archive" in n for n in names)                           # archived projects aren't included
    link = names["workspace/alpha/outside"]
    assert link.issym() and link.linkname == "/etc/hostname"                  # kept as a link, never followed
    manifest = json.loads(read(path, "manifest.json"))
    assert [p["name"] for p in manifest["projects"]] == ["alpha", "beta"]
    alpha = manifest["projects"][0]
    assert alpha["memory"] is True and alpha["git"] is True
    text = read(path, "BACKUP-README.md")
    assert "**Projects:** `alpha`, `beta`" in text and "node_modules" in text and f"{ws}/" in text


def test_rebuildable_folders_can_be_kept_and_projects_chosen(home):
    meta = snapshots.create("workspace", projects=["alpha"], skip_rebuildable=False)
    names = members(snapshots.archive_path(meta["id"]))
    assert "workspace/alpha/node_modules/pkg/big.js" in names
    assert not any(n.startswith("workspace/beta") for n in names)
    with pytest.raises(snapshots.SnapshotError, match="Not in the Workspace: gamma"):
        snapshots.create("workspace", projects=["gamma"])


def test_preview_lists_contents_and_sizes_without_writing(home):
    p = snapshots.preview("workspace")
    assert {i["path"] for i in p["items"]} >= {"workspace/alpha", "workspace/beta", "memory/projects/alpha.md"}
    assert all("source" not in i for i in p["items"])                       # no local paths in the answer
    assert p["skipped"]["dirs"] == 1 and p["skipped"]["bytes"] == 5000 and p["fits"] is True
    assert not os.path.exists(paths.SNAPSHOT_DIR) or not os.listdir(paths.SNAPSHOT_DIR)
    data = snapshots.preview("data")
    assert [i["path"] for i in data["items"]][:3] == ["config", "memory", "skills"]
    assert "logs/" in data["left_out"]


def test_no_room_on_disk_or_over_the_limit_is_refused(home, monkeypatch):
    monkeypatch.setattr(snapshots, "free_bytes", lambda: 10)
    p = snapshots.preview("data")
    assert p["fits"] is False and "free disk" in p["reason"]
    with pytest.raises(snapshots.SnapshotError, match="free disk"):
        snapshots.create("data")
    monkeypatch.setattr(snapshots, "free_bytes", lambda: 10 * GB)
    monkeypatch.setattr(snapshots, "limit_bytes", lambda: 1)
    with pytest.raises(snapshots.SnapshotError, match="size limit"):
        snapshots.create("data")


def test_automatic_snapshots_make_room_the_users_own_never(home, monkeypatch):
    mine = snapshots.create("data", note="mine")
    auto = snapshots.create("data", auto=True)
    estimate = snapshots.preview("data")["estimate_bytes"]
    used = snapshots.used_bytes()
    # one byte too small for a third: the automatic one goes, the user's own stays
    monkeypatch.setattr(snapshots, "limit_bytes", lambda: used + estimate - 1)
    snapshots.create("data", note="third")
    left = snapshots.list_snapshots()
    assert sorted(s["note"] for s in left) == ["mine", "third"]
    assert not any(s.get("auto") for s in left)


def test_list_delete_and_bad_ids(home):
    a = snapshots.create("data")
    time.sleep(1.1)
    b = snapshots.create("workspace")
    assert [s["id"] for s in snapshots.list_snapshots()] == [b["id"], a["id"]]       # newest first
    snapshots.delete(a["id"])
    assert [s["id"] for s in snapshots.list_snapshots()] == [b["id"]]
    for bad in ("../../etc/passwd", "x", a["id"] + "/..", ""):
        with pytest.raises(snapshots.SnapshotError):
            snapshots.delete(bad)


def test_two_snapshots_in_the_same_second_get_different_ids(home):
    a, b = snapshots.create("data"), snapshots.create("data")
    assert a["id"] != b["id"] and len(snapshots.list_snapshots()) == 2


def test_a_snapshot_interrupted_by_a_stop_shows_as_failed(home):
    snapshots._ensure_dir()
    sid = "2026-10-14_120000-data"
    snapshots._write_meta(sid, {"id": sid, "kind": "data", "created": time.time(), "status": "running"})
    [s] = snapshots.list_snapshots()
    assert s["status"] == "failed" and "Interrupted" in s["error"]
    snapshots.delete(sid)                                      # and it can be removed
    assert snapshots.list_snapshots() == []


def test_a_failure_leaves_no_partial_archive(home, monkeypatch):
    monkeypatch.setattr(snapshots, "_copy_database", lambda target: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(snapshots.SnapshotError, match="disk full"):
        snapshots.create("data")
    [s] = snapshots.list_snapshots()
    assert s["status"] == "failed" and "disk full" in s["error"]
    assert [f for f in os.listdir(paths.SNAPSHOT_DIR) if f.endswith((".tar.gz", ".partial", ".db"))] == []


def test_start_runs_in_the_background(home):
    sid = snapshots.start("workspace", note="bg")
    for _ in range(100):
        s = snapshots.get(sid)
        if s["status"] != "running":
            break
        time.sleep(0.05)
    assert s["status"] == "ready" and s["note"] == "bg" and s["projects"] == ["alpha", "beta"]


# ---------- the API

@pytest.fixture
def client(mocker, tmp_path, home):
    return make_client(mocker, tmp_path)


def test_api_preview_take_list_delete(client):
    r = client.post("/api/snapshots/preview", json={"kind": "workspace"}, headers=H)
    assert r.status_code == 200 and r.json()["fits"] is True
    sid = client.post("/api/snapshots", json={"kind": "data", "note": "api"}, headers=H).json()["id"]
    for _ in range(100):
        listed = client.get("/api/snapshots", headers=H).json()
        if listed["snapshots"] and listed["snapshots"][0]["status"] != "running":
            break
        time.sleep(0.05)
    snap = listed["snapshots"][0]
    assert snap["id"] == sid and snap["status"] == "ready" and snap["size_bytes"] > 0
    assert listed["limit_bytes"] == 5 * GB and listed["used_bytes"] == snap["size_bytes"]
    assert client.post(f"/api/snapshots/{sid}/delete", headers=H).json() == {"status": "deleted"}
    assert client.get("/api/snapshots", headers=H).json()["snapshots"] == []


def test_api_refuses_unknown_kind_and_bad_ids(client):
    # 422, so the form shows the message inline (the page alerts on 400)
    assert client.post("/api/snapshots", json={"kind": "everything"}, headers=H).status_code == 422
    assert client.post("/api/snapshots/..%2F..%2Fetc/delete", headers=H).status_code in (404, 422)


def test_api_saves_only_the_limit(client):
    from optimux_backend.core.config_manager import get_settings
    assert client.post("/api/snapshots/limit", json={"gb": 12.5}, headers=H).json() == {"snapshot_limit_gb": 12.5}
    assert get_settings()["snapshot_limit_gb"] == 12.5
    assert client.post("/api/snapshots/limit", json={"gb": 0}, headers=H).status_code == 422


def test_api_needs_sign_in(client):
    assert client.get("/api/snapshots").status_code == 401


# ---------- review fixes

def test_hard_links_are_stored_as_full_copies(home):
    _, ws = home
    os.link(ws / "beta" / "README.md", ws / "beta" / "SAME.md")
    meta = snapshots.create("workspace", projects=["beta"])
    names = members(snapshots.archive_path(meta["id"]))
    assert names["workspace/beta/README.md"].isfile() and names["workspace/beta/SAME.md"].isfile()
    assert read(snapshots.archive_path(meta["id"]), "workspace/beta/SAME.md") == "beta"


def test_a_file_that_shrinks_while_copied_keeps_the_archive_valid(home, monkeypatch):
    _, ws = home
    big = ws / "beta" / "app.log"
    big.write_text("y" * 4000)
    real = tarfile.TarFile.gettarinfo

    def shrink_after_stat(self, name=None, arcname=None, fileobj=None):
        info = real(self, name, arcname, fileobj)
        if str(name).endswith("app.log"):
            big.write_text("y" * 10)          # truncated between the size check and the copy
        return info
    monkeypatch.setattr(tarfile.TarFile, "gettarinfo", shrink_after_stat)
    meta = snapshots.create("workspace", projects=["beta"])
    assert meta["status"] == "ready" and meta["changed"] == ["workspace/beta/app.log"]
    path = snapshots.archive_path(meta["id"])
    data = read(path, "workspace/beta/app.log")
    assert data == "y" * 10 + "\0" * 3990                  # padded to the recorded size
    assert "workspace/beta/README.md" in members(path)      # the rest is still there


def test_checksums_cover_every_file(home, tmp_path):
    import hashlib
    meta = snapshots.create("data")
    path = snapshots.archive_path(meta["id"])
    names = members(path)
    assert list(names)[-1] == "SHA256SUMS"
    sums = dict(reversed(l.split("  ", 1)) for l in read(path, "SHA256SUMS").splitlines())
    files = [n for n, m in names.items() if m.isfile() and n not in ("manifest.json", "BACKUP-README.md", "SHA256SUMS")]
    assert sorted(sums) == sorted(files)
    with tarfile.open(path) as tar:
        for n in files:
            assert hashlib.sha256(tar.extractfile(n).read()).hexdigest() == sums[n]


def test_data_snapshot_members_are_private(home):
    h, _ = home
    os.chmod(h / "config" / "settings.json", 0o644)
    meta = snapshots.create("data")
    for name, m in members(snapshots.archive_path(meta["id"])).items():
        if name.startswith(("config", "memory", "skills", "data", "secrets", "auth")):
            assert m.mode & 0o077 == 0, name


def test_symlinked_projects_are_not_followed(home, tmp_path):
    _, ws = home
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "big.bin").write_text("z" * 10000)
    (ws / "linked").symlink_to(elsewhere)
    p = snapshots.preview("workspace")
    assert [x["name"] for x in p["projects"]] == ["alpha", "beta"]
    assert any(x.startswith("linked/") for x in p["left_out"])
    with pytest.raises(snapshots.SnapshotError, match="links to another folder"):
        snapshots.create("workspace", projects=["linked"])


def test_leftovers_of_an_interrupted_snapshot_are_removed(home):
    snapshots._ensure_dir()
    sid = "2026-10-14_120000-data"
    snapshots._write_meta(sid, {"id": sid, "kind": "data", "created": time.time(), "status": "running"})
    for name in (f".{sid}.db", f"{sid}.tar.gz.partial", "unrelated.txt"):
        open(os.path.join(paths.SNAPSHOT_DIR, name), "w").write("x")
    snapshots.summary()
    assert sorted(os.listdir(paths.SNAPSHOT_DIR)) == [f"{sid}.json", "unrelated.txt"]


def test_delete_also_removes_a_database_copy(home):
    snapshots._ensure_dir()
    sid = "2026-10-14_120000-data"
    snapshots._write_meta(sid, {"id": sid, "kind": "data", "created": time.time(), "status": "running"})
    open(os.path.join(paths.SNAPSHOT_DIR, f".{sid}.db"), "w").write("x")
    snapshots.delete(sid)
    assert os.listdir(paths.SNAPSHOT_DIR) == []


def test_a_background_failure_before_writing_keeps_its_reason(home, monkeypatch):
    def boom(needed):
        raise snapshots.SnapshotError("The Workspace folder changed.")
    monkeypatch.setattr(snapshots, "_make_room", boom)
    sid = snapshots.start("data")
    for _ in range(100):
        s = snapshots.get(sid)
        if s["status"] != "running":
            break
        time.sleep(0.05)
    assert s["status"] == "failed" and s["error"] == "The Workspace folder changed."


def test_ids_with_a_trailing_newline_are_refused(home):
    with pytest.raises(snapshots.SnapshotError):
        snapshots.archive_path("2026-10-14_120000-data\n")


def test_readme_moves_aside_and_quotes_paths(home, monkeypatch):
    monkeypatch.setattr(paths, "HOME", "/srv/my data/.optimux")
    meta = {"kind": "data", "created": time.time(), "optimux_version": "x", "items": [], "home": paths.HOME,
            "workspace": "", "includes_secrets": True, "left_out": []}
    text = snapshots.readme(meta)
    assert "mv '/srv/my data/.optimux' '/srv/my data/.optimux'.before-restore" in text
    assert "mkdir -m 700 restore" in text


# ---------- Everything (full): data and all projects in one archive

def test_everything_holds_data_and_all_projects_once(home):
    h, ws = home
    meta = snapshots.create("full", note="before the experiment")
    assert meta["kind"] == "full" and meta["includes_secrets"] and meta["projects"] == ["alpha", "beta"]
    path = snapshots.archive_path(meta["id"])
    with tarfile.open(path) as tar:
        names = [m.name for m in tar.getmembers()]
    assert len(names) == len(set(names))                                    # project memory only once
    for n in ("config/settings.json", "data/optimux.db", "secrets.env", "memory/projects/alpha.md",
              "workspace/alpha/src/a.py", "workspace/beta/README.md"):
        assert n in names, n
    assert not any("node_modules" in n for n in names)
    m = members(path)
    assert m["config/settings.json"].mode & 0o077 == 0                         # data: private
    assert m["workspace/alpha/src/a.py"].mode & 0o044                         # projects: modes kept
    text = read(path, "BACKUP-README.md")
    assert "**Type:** Everything" in text and "**Projects:** `alpha`, `beta`" in text
    assert "restore/workspace" not in text.split("Put the snapshot's data")[1].split("\n")[1]   # data step skips workspace/
    assert f"cp -a restore/workspace/<project> {ws}/" in text


def test_everything_without_a_workspace_is_the_data_alone(home, monkeypatch):
    def no_root():
        raise workspace.WorkspaceError("No Workspace folder is set.")
    monkeypatch.setattr(workspace, "projects_root", no_root)
    meta = snapshots.create("full")
    assert meta["status"] == "ready" and meta["projects"] == []
    with pytest.raises(snapshots.SnapshotError, match="No Workspace"):
        snapshots.create("workspace")


def test_everything_cannot_pick_projects(home):
    with pytest.raises(snapshots.SnapshotError, match="choose Workspace"):
        snapshots.preview("full", projects=["alpha"])


def test_api_takes_everything(client):
    sid = client.post("/api/snapshots", json={"kind": "full"}, headers=H).json()["id"]
    assert sid.endswith("-full")
    for _ in range(100):
        s = client.get("/api/snapshots", headers=H).json()["snapshots"][0]
        if s["status"] != "running":
            break
        time.sleep(0.05)
    assert s["status"] == "ready" and s["kind"] == "full"


def test_the_list_says_what_is_inside_without_opening_the_archive(home):
    snapshots.create("workspace", projects=["alpha"])
    [s] = snapshots.list_snapshots()
    assert s["projects"] == ["alpha"]
    assert [i["path"] for i in s["items"]] == ["workspace/alpha", "memory/projects/alpha.md"]
    assert all("source" not in i and "private" not in i and "project" in i or i["path"].startswith("memory") for i in s["items"])
    assert s["skip_rebuildable"] is True


def test_older_snapshots_get_their_parts_from_the_manifest_once(home):
    meta = snapshots.create("workspace", projects=["alpha"])
    sid = meta["id"]
    with open(snapshots._meta_path(sid)) as f:
        stored = json.load(f)
    for k in ("items", "left_out", "skip_rebuildable"):
        stored.pop(k)                                   # as written before the list kept them
    snapshots._write_meta(sid, stored)
    [s] = snapshots.list_snapshots()
    assert [i["path"] for i in s["items"]] == ["workspace/alpha", "memory/projects/alpha.md"]
    with open(snapshots._meta_path(sid)) as f:
        assert "items" in json.load(f)                  # saved: the archive is opened only once
    assert "size_bytes" not in json.load(open(snapshots._meta_path(sid)))


def test_the_readme_can_be_read_without_unpacking(home):
    sid = snapshots.create("data")["id"]
    assert snapshots.read_head_member(sid, "BACKUP-README.md").startswith("# Optimux snapshot")
    with pytest.raises(snapshots.SnapshotError, match="Only manifest"):
        snapshots.read_head_member(sid, "secrets.env")
    with pytest.raises(snapshots.SnapshotError):
        snapshots.read_head_member("../../etc/passwd", "BACKUP-README.md")


def test_api_readme(client):
    sid = snapshots.create("data")["id"]
    r = client.get(f"/api/snapshots/{sid}/readme", headers=H)
    assert r.status_code == 200 and r.json()["format"] == "md" and "## Restore" in r.json()["text"]
    assert client.get("/api/snapshots/2026-01-01_000000-data/readme", headers=H).status_code == 422
    assert client.get(f"/api/snapshots/{sid}/readme").status_code == 401


def test_older_snapshots_with_a_text_readme_still_show_it(home):
    """Snapshots taken before the Markdown README hold BACKUP-README.txt."""
    snapshots._ensure_dir()
    sid = "2026-10-14_120000-data"
    path = snapshots.archive_path(sid)
    with tarfile.open(path, "w:gz") as tar:
        snapshots._add_bytes(tar, "manifest.json", b'{"items": []}')
        snapshots._add_bytes(tar, "BACKUP-README.txt", b"OPTIMUX SNAPSHOT\n")
    snapshots._write_meta(sid, {"id": sid, "kind": "data", "created": time.time(), "status": "ready", "items": []})
    assert snapshots.read_readme(sid) == {"text": "OPTIMUX SNAPSHOT\n", "format": "txt"}


def test_readme_escapes_the_note_in_markdown(home):
    meta = snapshots.create("data", note="a | b `c`\nd")
    text = read(snapshots.archive_path(meta["id"]), "BACKUP-README.md")
    assert "- **Note:** a \\| b 'c' d" in text


def test_a_failure_is_recorded_before_the_snapshot_stops_counting_as_running(home, monkeypatch):
    """No moment where a failed background snapshot reads as "Interrupted": the
    reason is written while its id is still in _running (slow machines showed
    the gap between the two)."""
    def boom(needed):
        raise snapshots.SnapshotError("The Workspace folder changed.")
    monkeypatch.setattr(snapshots, "_make_room", boom)
    seen = []

    class Watch(set):
        def discard(self, sid):
            if sid in self:                       # the moment it stops counting as running
                with open(snapshots._meta_path(sid)) as f:
                    seen.append(json.load(f)["status"])
            super().discard(sid)
    monkeypatch.setattr(snapshots, "_running", Watch())
    sid = snapshots.start("data")
    for _ in range(100):
        if not snapshots._running:
            break
        time.sleep(0.02)
    assert seen and seen[0] == "failed"           # already failed when it left _running
    assert snapshots.get(sid)["error"] == "The Workspace folder changed."


def _git(*args, cwd):
    import subprocess
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "T",
                        "GIT_COMMITTER_EMAIL": "t@x"})


def test_version_names_the_tag_and_commit(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    _git("init", "-q", cwd=app)
    (app / "a.txt").write_text("a")
    _git("add", "-A", cwd=app)
    _git("commit", "-qm", "one", cwd=app)
    _git("tag", "v0.1.0", cwd=app)                      # a lightweight tag, like the published one
    (app / "b.txt").write_text("b")
    _git("add", "-A", cwd=app)
    _git("commit", "-qm", "two", cwd=app)
    monkeypatch.setattr(snapshots, "APP_DIR", str(app))
    assert re.fullmatch(r"v0\.1\.0-1-g[0-9a-f]+", snapshots.optimux_version())


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to give the folder to another user")
def test_version_works_when_root_owns_the_program_folder(tmp_path, monkeypatch):
    """The server case: the folder belongs to another user than the one running
    Optimux. Plain git refuses it ("dubious ownership"), so the page said unknown."""
    import subprocess
    app = tmp_path / "app"
    app.mkdir()
    _git("init", "-q", cwd=app)
    (app / "a.txt").write_text("a")
    _git("add", "-A", cwd=app)
    _git("commit", "-qm", "one", cwd=app)
    subprocess.run(["chown", "-R", "nobody", str(app)], check=True)
    plain = subprocess.run(["git", "describe", "--always"], cwd=app, capture_output=True, text=True)
    assert plain.returncode != 0 and "dubious ownership" in plain.stderr
    monkeypatch.setattr(snapshots, "APP_DIR", str(app))
    assert re.fullmatch(r"[0-9a-f]{7,}", snapshots.optimux_version())


def test_version_without_git_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "APP_DIR", str(tmp_path))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert snapshots.optimux_version() == "unknown"
