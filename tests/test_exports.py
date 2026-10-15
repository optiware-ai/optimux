"""v0.3: snapshot downloads, encrypted with age."""
import os
import shutil
import stat
import subprocess
import tarfile
import time

import pyrage
import pytest

from optimux_backend.core import exports, snapshots
from tests.test_snapshots import H, home, client  # noqa: F401  (fixtures)


def decrypt(path, identity, tmp_path):
    out = tmp_path / "out.tar.gz"
    with open(path, "rb") as i, open(out, "wb") as o:
        pyrage.decrypt_io(i, o, [identity])
    return out


def test_one_time_key_download_opens_with_that_key_only(home, tmp_path):
    sid = snapshots.create("data")["id"]
    r = exports.prepare(sid, "key")
    assert r["key"].startswith("AGE-SECRET-KEY-1") and r["filename"] == f"optimux-snapshot-{sid}.tar.gz.age"
    path, name = exports.export_file(r["url"].rsplit("/", 1)[1])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    out = decrypt(path, pyrage.x25519.Identity.from_str(r["key"]), tmp_path)
    with tarfile.open(out) as tar:
        assert "secrets.env" in tar.getnames()
    with pytest.raises(pyrage.DecryptError):
        decrypt(path, pyrage.x25519.Identity.generate(), tmp_path)        # any other key: nothing
    # the secret key is kept nowhere on disk
    for root, _, files in os.walk(home[0]):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                assert r["key"].encode() not in fh.read(), f


DATA = os.path.join(os.path.dirname(__file__), "data")


def make_rsa_key(tmp_path, bits=2048):
    """An RSA key pair made with openssl: (PEM private key bytes, ssh-rsa public line)."""
    import base64
    import re
    import struct
    if not shutil.which("openssl"):
        pytest.skip("openssl is not installed")
    pem = tmp_path / f"rsa{bits}.pem"
    subprocess.run(["openssl", "genrsa", "-traditional", "-out", str(pem), str(bits)], check=True,
                   capture_output=True)
    text = subprocess.run(["openssl", "rsa", "-in", str(pem), "-noout", "-text"], check=True,
                          capture_output=True, text=True).stdout
    n = int(re.sub(r"[\s:]", "", re.search(r"modulus:\n((?:\s+[0-9a-f:]+\n)+)", text).group(1)), 16)
    def s(b): return struct.pack(">I", len(b)) + b
    def mpint(v): return s(v.to_bytes((v.bit_length() + 8) // 8, "big"))
    blob = s(b"ssh-rsa") + mpint(65537) + mpint(n)
    return pem.read_bytes(), "ssh-rsa " + base64.b64encode(blob).decode() + " me@laptop"


def test_own_public_key_age(home, tmp_path):
    sid = snapshots.create("data")["id"]
    ident = pyrage.x25519.Identity.generate()
    r = exports.prepare(sid, "recipient", str(ident.to_public()))
    assert r["key"] is None
    path, _ = exports.export_file(r["url"].rsplit("/", 1)[1])
    assert tarfile.is_tarfile(decrypt(path, ident, tmp_path))


def test_own_ssh_rsa_key_opens_the_download(home, tmp_path):
    sid = snapshots.create("data")["id"]
    private, public = make_rsa_key(tmp_path)
    # as pasted from id_rsa.pub, from an authorized_keys line with options, or with odd spacing
    for pasted in (public, 'from="10.0.0.1",no-pty ' + public, public.replace(" ", "\t  ")):
        r = exports.prepare(sid, "recipient", pasted)
        path, _ = exports.export_file(r["url"].rsplit("/", 1)[1])
        assert tarfile.is_tarfile(decrypt(path, pyrage.ssh.Identity.from_buffer(private), tmp_path))


def test_small_rsa_keys_are_refused_with_a_reason(home, tmp_path):
    sid = snapshots.create("data")["id"]
    _, public = make_rsa_key(tmp_path, bits=1024)
    with pytest.raises(exports.ExportError, match="at least 2048 bits"):
        exports.prepare(sid, "recipient", public)


def test_own_ssh_ed25519_public_key_is_accepted(home):
    sid = snapshots.create("data")["id"]
    with open(os.path.join(DATA, "test-only-ssh-ed25519.pub")) as f:
        public = f.read()
    r = exports.prepare(sid, "recipient", public)
    path, _ = exports.export_file(r["url"].rsplit("/", 1)[1])
    with open(path, "rb") as f:
        assert b"-> ssh-ed25519 " in f.read(400)              # an age stanza for that key


@pytest.mark.parametrize("bad, words", [
    ("", "Paste a public key"),
    ("AGE-SECRET-KEY-1QQQQ", "secret key"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "secret key"),
    ("age1notreal", "not valid"),
    ("hello", "must start with age1"),
    ("sk-ssh-ed25519@openssh.com AAAAGnNrLXNzaC1lZDI1NTE5QG9wZW5zc2guY29t me", "hardware token"),
])
def test_bad_public_keys_are_refused_clearly(home, bad, words):
    sid = snapshots.create("data")["id"]
    with pytest.raises(exports.ExportError, match=words):
        exports.prepare(sid, "recipient", bad)


def test_plain_only_for_workspace_snapshots_without_keys(home):
    data, full = snapshots.create("data")["id"], snapshots.create("full")["id"]
    ws = snapshots.create("workspace")["id"]
    for sid in (data, full):
        with pytest.raises(exports.ExportError, match="only be downloaded encrypted"):
            exports.prepare(sid, "plain")
    assert exports.prepare(ws, "plain")["url"] == f"/api/snapshots/{ws}/download"


def test_plain_refused_for_older_workspace_snapshots(home):
    """Taken before projects were checked for the data folder inside them."""
    import json as _json
    sid = snapshots.create("workspace")["id"]
    with open(snapshots._meta_path(sid)) as f:
        meta = _json.load(f)
    meta.pop("home_excluded")
    snapshots._write_meta(sid, meta)
    with pytest.raises(exports.ExportError, match="older Optimux"):
        exports.prepare(sid, "plain")
    assert exports.prepare(sid, "key")["key"]                      # encrypted still works


def test_a_project_holding_the_data_folder_never_carries_the_keys(home, monkeypatch):
    """A Workspace such as /home can contain ~/.optimux inside a project."""
    h, ws = home
    inner = ws / "alpha" / "dotoptimux"
    os.rename(h, inner)                                            # the data folder now lives in a project
    from optimux_backend.core import paths
    for name, value in (("HOME", inner), ("MEMORY_DIR", inner / "memory"), ("SNAPSHOT_DIR", inner / "snapshots")):
        monkeypatch.setattr(paths, name, str(value))
    p = snapshots.preview("workspace", ["alpha"])
    assert any("dotoptimux" in x and "own data folder" in x for x in p["left_out"])
    sid = snapshots.create("workspace", projects=["alpha"])["id"]
    with tarfile.open(snapshots.archive_path(sid)) as tar:
        names = tar.getnames()
    assert "workspace/alpha/src/a.py" in names
    assert not any("dotoptimux" in n for n in names)              # no secrets.env, no auth.json, no database
    assert exports.prepare(sid, "plain")["mode"] == "plain"       # safe to hand out plain


def test_expired_and_bad_tokens(home, monkeypatch):
    sid = snapshots.create("data")["id"]
    token = exports.prepare(sid, "key")["url"].rsplit("/", 1)[1]
    for bad in ("../../etc/passwd", "x", token + "\n"):
        with pytest.raises(exports.ExportError):
            exports.export_file(bad)
    later = time.time() + exports.EXPORT_TTL + 5
    monkeypatch.setattr(exports.time, "time", lambda: later)
    with pytest.raises(exports.ExportError, match="expired"):
        exports.export_file(token)
    for f in os.listdir(exports.exports_dir()):
        os.utime(os.path.join(exports.exports_dir(), f), (later - exports.EXPORT_TTL - 10,) * 2)
    exports.sweep(now=later)
    assert os.listdir(exports.exports_dir()) == []


def test_one_download_per_snapshot_and_one_at_a_time(home):
    sid = snapshots.create("data")["id"]
    first = exports.prepare(sid, "key")["url"].rsplit("/", 1)[1]
    second = exports.prepare(sid, "key")["url"].rsplit("/", 1)[1]
    with pytest.raises(exports.ExportError):
        exports.export_file(first)                                 # replaced by the newer one
    exports.export_file(second)
    assert exports._lock.acquire(blocking=False)
    try:
        with pytest.raises(exports.ExportError, match="Another download"):
            exports.prepare(sid, "key")
    finally:
        exports._lock.release()
    exports.remove_for(sid)
    assert [f for f in os.listdir(exports.exports_dir())] == []


def test_preparing_a_download_does_not_freeze_the_server(home, monkeypatch):
    """encrypt_io lets other threads run (encrypt_file held Python's lock throughout)."""
    import threading
    sid = snapshots.create("data")["id"]
    big = home[0] / "big.bin"
    big.write_bytes(os.urandom(64 * 1024 * 1024))
    monkeypatch.setattr(snapshots, "archive_path", lambda s: str(big))
    ticks, stop = [0], threading.Event()
    def tick():
        while not stop.is_set():
            ticks[0] += 1
            time.sleep(0.005)
    t = threading.Thread(target=tick)
    t.start()
    started = time.monotonic()
    exports.prepare(sid, "key")
    took = time.monotonic() - started
    stop.set()
    t.join()
    expected = took / 0.005
    assert expected < 4 or ticks[0] >= expected * 0.5, (ticks[0], expected)


def test_not_enough_disk_is_refused(home, monkeypatch):
    sid = snapshots.create("data")["id"]
    monkeypatch.setattr(snapshots, "free_bytes", lambda: 10)
    with pytest.raises(exports.ExportError, match="free disk"):
        exports.prepare(sid, "key")
    assert [f for f in os.listdir(exports.exports_dir()) if f.endswith((".age", ".partial"))] == []


def test_api_export_and_download(client, tmp_path):
    sid = snapshots.create("data")["id"]
    r = client.post(f"/api/snapshots/{sid}/export", json={"mode": "key"}, headers=H)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    dl = client.get(body["url"], headers=H)
    assert dl.status_code == 200 and body["filename"] in dl.headers["content-disposition"]
    (tmp_path / "f.age").write_bytes(dl.content)
    assert tarfile.is_tarfile(decrypt(tmp_path / "f.age", pyrage.x25519.Identity.from_str(body["key"]), tmp_path))
    assert client.post(f"/api/snapshots/{sid}/export", json={"mode": "plain"}, headers=H).status_code == 422
    assert client.get(f"/api/snapshots/{sid}/download", headers=H).status_code == 422
    assert client.get("/api/snapshots/exports/" + "a" * 32, headers=H).status_code == 404
    assert client.get(body["url"]).status_code == 401                     # sign-in needed
    assert client.post(f"/api/snapshots/{sid}/export", json={"mode": "key"}).status_code == 401


def test_api_remembers_the_public_key_when_asked(client):
    from optimux_backend.core.config_manager import get_settings
    sid = snapshots.create("data")["id"]
    pub = str(pyrage.x25519.Identity.generate().to_public())
    r = client.post(f"/api/snapshots/{sid}/export", json={"mode": "recipient", "recipient": pub, "remember": True},
                    headers=H)
    assert r.status_code == 200 and get_settings()["snapshot_recipient"] == pub
    assert client.post("/api/settings", json={**get_settings(), "snapshot_recipient": "AGE-SECRET-KEY-1X"},
                       headers=H).status_code in (400, 422)


def test_plain_workspace_download_api(client):
    sid = snapshots.create("workspace")["id"]
    r = client.get(f"/api/snapshots/{sid}/download", headers=H)
    assert r.status_code == 200 and r.content[:2] == b"\x1f\x8b"          # a gzip file


def test_the_age_tool_opens_a_download(home, tmp_path):
    """The file works with the standard age command, as the instructions say."""
    if not shutil.which("age"):
        pytest.skip("age is not installed")
    sid = snapshots.create("data")["id"]
    r = exports.prepare(sid, "key")
    path, _ = exports.export_file(r["url"].rsplit("/", 1)[1])
    (tmp_path / "optimux-key.txt").write_text(r["key"] + "\n")
    subprocess.run(["age", "-d", "-i", str(tmp_path / "optimux-key.txt"), "-o", str(tmp_path / "s.tar.gz"), path],
                   check=True)
    assert tarfile.is_tarfile(tmp_path / "s.tar.gz")


def test_a_workspace_set_to_the_data_folder_never_carries_its_parts(home, monkeypatch):
    """Workspace = ~/.optimux: its data/, snapshots/, config/... look like projects."""
    h, _ = home
    snapshots.create("data")                                  # a snapshot with keys inside the "Workspace"
    (h / "workspace").mkdir()
    (h / "workspace" / "ok").mkdir()
    (h / "workspace" / "ok" / "a.txt").write_text("fine")     # ~/.optimux/workspace/<project> is allowed
    from optimux_backend.core import workspace
    monkeypatch.setattr(workspace, "projects_root", lambda: str(h))
    p = snapshots.preview("workspace")
    names = [x["name"] for x in p["projects"]]
    assert "workspace" in names and not set(names) & set(snapshots.HOME_PRIVATE)
    assert any(x.startswith("snapshots/") and "own data folder" in x for x in p["left_out"])
    sid = snapshots.create("workspace")["id"]
    with tarfile.open(snapshots.archive_path(sid)) as tar:
        members = tar.getnames()
    assert "workspace/workspace/ok/a.txt" in members
    assert not any(m.split("/")[1] in snapshots.HOME_PRIVATE for m in members if m.startswith("workspace/"))


def test_the_workspace_folder_cannot_be_set_to_the_data_folder(home):
    from optimux_backend.core import config_manager
    from optimux_backend.core.security import ValidationError
    h, _ = home
    with pytest.raises(ValidationError, match="own data folder"):
        config_manager.update_settings(projects_root=str(h))
    with pytest.raises(ValidationError, match="own data folder"):
        config_manager.update_settings(projects_root=str(h / "memory"))


def test_a_failed_new_download_keeps_the_earlier_one(home, monkeypatch):
    sid = snapshots.create("data")["id"]
    first = exports.prepare(sid, "key")["url"].rsplit("/", 1)[1]
    monkeypatch.setattr(snapshots, "free_bytes", lambda: 10)
    with pytest.raises(exports.ExportError):
        exports.prepare(sid, "key")
    exports.export_file(first)                                # still there


def test_deleting_the_snapshot_during_encryption_drops_the_download(home, monkeypatch):
    sid = snapshots.create("data")["id"]
    real = pyrage.encrypt_io
    def encrypt_then_delete(src, dst, recipients):
        real(src, dst, recipients)
        snapshots.delete(sid)                                 # deleted while this ran
    monkeypatch.setattr(exports.pyrage, "encrypt_io", encrypt_then_delete)
    with pytest.raises(exports.ExportError, match="deleted"):
        exports.prepare(sid, "key")
    assert [f for f in os.listdir(exports.exports_dir())] == []
