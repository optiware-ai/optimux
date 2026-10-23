"""SQLite store: schema, legacy log import, persistence across restarts, backups."""
import json
import os
import time

import pytest

from optimux_backend.core import store, usage


def restart():
    """Forget per-process state, as a server restart would."""
    store._ready_for = None
    usage._imported_for = None


def test_schema_version_and_wal():
    conn = store.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()
    assert store.get_meta("schema_version") == str(len(store.MIGRATIONS))


def test_usage_survives_restart():
    usage.record("s", "simple", "m", {"prompt_tokens": 10, "completion_tokens": 2}, 100, project="alpha")
    restart()
    s = usage.summary()["all_time"]
    assert s["total"]["calls"] == 1 and s["by_project"]["alpha"]["prompt_tokens"] == 10


def test_legacy_jsonl_imported_once_and_kept():
    with open(usage.USAGE_FILE, "w") as f:
        f.write(json.dumps({"ts": time.time(), "role": "simple", "model": "m", "prompt_tokens": 5, "cost": 0.1}) + "\n")
        f.write("not json\n")
        f.write(json.dumps({"ts": time.time(), "project": "alpha", "role": "complex", "model": "c",
                            "prompt_tokens": 7, "cost": None, "estimated": True}) + "\n")
    s = usage.summary()["all_time"]
    assert s["total"]["calls"] == 2
    assert s["by_project"]["(unknown)"]["calls"] == 1 and s["by_project"]["alpha"]["calls_without_cost"] == 1
    assert not os.path.exists(usage.USAGE_FILE) and os.path.exists(usage.USAGE_FILE + ".migrated")
    restart()
    assert usage.summary()["all_time"]["total"]["calls"] == 2  # not imported twice


def test_estimated_flag_round_trip():
    usage.record("s", "simple", "m", {"prompt_tokens": 3, "estimated": True})
    assert usage.read_entries()[0]["estimated"] is True


def test_month_totals_sql():
    usage.insert({"project": "alpha", "cost": 0.5})
    usage.insert({"project": "alpha", "cost": None})
    usage.insert({"project": "beta", "cost": 9.0})
    usage.insert({"project": "alpha", "cost": 3.0, "ts": usage.month_start() - 10})
    t = usage.month_totals("alpha")
    assert t == {"calls": 2, "cost": 0.5, "calls_without_cost": 1}


def test_backup_and_rotation(monkeypatch):
    usage.record("s", "simple", "m", {"prompt_tokens": 1})
    monkeypatch.setattr(store, "KEEP_BACKUPS", 3)
    day = 86400
    paths = [store.backup(now=1_700_000_000 + i * day) for i in range(5)]
    files = sorted(n for n in os.listdir(store.BACKUP_DIR) if n.endswith(".db"))     # last-check.json beside them
    assert len(files) == 3 and os.path.basename(paths[-1]) == files[-1]
    import sqlite3
    conn = sqlite3.connect(paths[-1])
    assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 1
    conn.close()
    info = store.info()
    assert info["usage_rows"] == 1 and info["backups"] == 3 and info["latest_backup"] == paths[-1]


def test_storage_endpoint(mocker, tmp_path):
    from tests.ws_helpers import make_client
    client = make_client(mocker, tmp_path)
    r = client.get("/api/storage", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200 and r.json()["path"].endswith("optimux.db")
    assert client.get("/api/storage").status_code == 401


@pytest.mark.asyncio
async def test_conductor_uses_minimal_thinking_for_gemini(mocker):
    import optimux_backend.core.llm_client as llm
    from tests.test_llm_client import create_mock_response
    for model, expected in (("gemini/g", {"reasoning_effort": "minimal"}), ("anthropic/c", {}),
                            ("gemini/gemma-4-26b-it", {})):
        mocker.patch.object(llm, "get_routing_config", return_value={"conductor_model": model})
        call = mocker.patch.object(llm, "acompletion", return_value=create_mock_response('{"complexity": "simple"}'))
        await llm.route_model("hi")
        assert call.call_args.kwargs.get("reasoning_effort") == expected.get("reasoning_effort")


# ---- IM-05 (v0.5.2): every backup is checked before it replaces anything

def test_a_backup_is_checked(tmp_path):
    from optimux_backend.core import store
    path = store.backup(now=1_700_000_000)
    st = store.backup_status()
    assert st["ok"] is True and st["file"] == path and st["error"] == ""
    assert store.info()["backup_check"]["ok"] is True
    assert not [n for n in os.listdir(store.BACKUP_DIR) if n.endswith(".new")]


def test_a_failed_check_keeps_the_good_backups(tmp_path, monkeypatch):
    from optimux_backend.core import store
    day = 86400
    good = [store.backup(now=1_700_000_000 + i * day) for i in range(3)]
    before = {p: open(p, "rb").read() for p in good}
    monkeypatch.setattr(store, "KEEP_BACKUPS", 2)          # a successful backup would prune now
    monkeypatch.setattr(store, "_quick_check", lambda p: "*** in database main ***\nPage 7: btree corrupt")
    with pytest.raises(store.BackupCheckFailed, match="failed its check"):
        store.backup(now=1_700_000_000 + 2 * day)           # same day as the newest good one
    assert all(open(p, "rb").read() == b for p, b in before.items())   # not replaced, not pruned
    failed = os.listdir(os.path.join(store.BACKUP_DIR, "failed"))
    assert len(failed) == 1 and failed[0].startswith("optimux-")
    st = store.backup_status()
    assert st["ok"] is False and "btree corrupt" in st["error"] and st["file"].endswith(failed[0])
    assert store.info()["backups"] == 3                     # failed/ isn't counted as a backup


def test_quick_check_on_a_damaged_file(tmp_path):
    from optimux_backend.core import store
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4000)
    assert store._quick_check(str(bad)) != "ok"
    good = store.backup(now=1_700_000_000)
    assert store._quick_check(good) == "ok"
