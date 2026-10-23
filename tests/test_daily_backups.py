"""v0.5.2: daily database backups in Dashboard -> Backups: list, check, back up
now, keep N, restore, encrypted download, delete."""
import os

import pyrage
import pytest

from optimux_backend.core import config_manager as cm, restore, store
from tests.test_restore import H, OK, change_everything, home, rows  # noqa: F401 (fixtures)
from tests.ws_helpers import make_client

DAY = 86400
T0 = 1_700_000_000


@pytest.fixture
def client(home, mocker, tmp_path):  # noqa: F811
    mocker.patch.object(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    return make_client(mocker, tmp_path)


def test_list_check_and_delete(home):  # noqa: F811
    names = [os.path.basename(store.backup(now=T0 + i * DAY)) for i in range(3)]
    listed = store.list_backups()
    assert [b["name"] for b in listed] == names[::-1]                    # newest first
    assert all(b["checked"] is True for b in listed) and listed[0]["date"] == names[-1][8:18]
    os.remove(os.path.join(store.BACKUP_DIR, "last-check.json"))         # as if made before v0.5.2
    assert store.list_backups()[1]["checked"] is None
    assert store.check_backup(names[1])["ok"] is True
    assert {b["name"]: b["checked"] for b in store.list_backups()}[names[1]] is True
    store.delete_backup(names[0])
    assert [b["name"] for b in store.list_backups()] == [names[2], names[1]]
    for bad in ("../optimux.db", "optimux-2023-11-14.db.new", names[0], ""):
        with pytest.raises(store.BackupError):
            store.backup_path(bad)


def test_keep_setting_prunes_at_the_next_backup(home, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    for i in range(6):
        store.backup(now=T0 + i * DAY)
    cm.update_settings(backups_keep=3)
    assert cm.backups_keep_for() == 3
    store.backup(now=T0 + 6 * DAY, keep=cm.backups_keep_for())
    assert len(store.list_backups()) == 3
    with pytest.raises(cm.ValidationError, match="between 3 and 60"):
        cm.update_settings(backups_keep=2)


def test_restore_a_daily_backup(home):  # noqa: F811
    h, ws = home
    name = os.path.basename(store.backup(now=T0))
    change_everything(h, ws)                                            # a new chat, new usage, a new sign-in
    with pytest.raises(restore.RestoreError, match="Type"):
        restore.restore_daily(name, "no")
    result = restore.restore_daily(name, OK)
    assert result["restart_needed"] and result["before_snapshot"] and result["data"] == ["data/optimux.db"]
    assert restore.pending()["source"] == f"daily backup {name[8:18]}"
    assert os.path.exists(os.path.join(store.BACKUP_DIR, name))          # the backup stays in the list
    log = restore.apply_pending()
    assert "from daily backup" in log[0]
    assert rows("SELECT id FROM sessions ORDER BY id") == [("old-chat",)]       # chats from that day
    assert sorted(r[0] for r in rows("SELECT cost FROM usage")) == [0.25, 0.5]  # usage never rolled back
    assert rows("SELECT id FROM logins") == [("new-login",)]                    # nor sign-ins


def test_api(client, home):  # noqa: F811
    r = client.post("/api/backups", headers=H)
    assert r.status_code == 200
    body = client.get("/api/backups", headers=H).json()
    assert body["keep"] == 14 and body["keep_range"] == [3, 60] and len(body["backups"]) == 1
    name = body["backups"][0]["name"]
    assert client.post(f"/api/backups/{name}/check", headers=H).json()["ok"] is True
    assert client.post("/api/backups/keep", headers=H, json={"keep": 5}).json() == {"keep": 5}
    assert client.post("/api/backups/keep", headers=H, json={"keep": 100}).status_code == 422
    # an encrypted download only, opened with the one-time key
    assert client.post(f"/api/backups/{name}/export", headers=H, json={"mode": "plain"}).status_code == 422
    r = client.post(f"/api/backups/{name}/export", headers=H, json={"mode": "key"})
    assert r.status_code == 200 and r.json()["filename"] == f"optimux-backup-{name[8:18]}.db.age"
    data = client.get(r.json()["url"], headers=H).content
    ident = pyrage.x25519.Identity.from_str(r.json()["key"])
    assert pyrage.decrypt(data, [ident]).startswith(b"SQLite format 3")
    assert client.post(f"/api/backups/{name}/restore", headers=H, json={"confirm": "x"}).status_code == 422
    assert client.post(f"/api/backups/{name}/restore", headers=H, json={"confirm": OK}).status_code == 200
    assert client.post("/api/restore/cancel", headers=H).status_code == 200
    assert client.post(f"/api/backups/{name}/delete", headers=H).status_code == 200
    assert client.post(f"/api/backups/{name}/delete", headers=H).status_code == 422
    assert client.get("/api/backups").status_code == 401
