"""IM-04 (v0.5.2): memory files have a size limit and are written atomically."""
import os

import pytest

from optimux_backend.core import curator, memory_manager as mm
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
BIG = "x" * (mm.MEMORY_MAX_CHARS + 1)


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "GLOBAL_DIR", str(tmp_path))
    monkeypatch.setattr(mm, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(mm, "PERSONAL_MD", str(tmp_path / "personal.md"))
    monkeypatch.setattr(mm, "PROFESSIONAL_MD", str(tmp_path / "professional.md"))
    monkeypatch.setattr(curator, "memory_path", lambda p: str(tmp_path / "projects" / f"{p}.md"))
    mm.init_memory()
    return tmp_path


@pytest.fixture
def client(mocker, tmp_path):
    return make_client(mocker, tmp_path)


def test_a_save_over_the_limit_is_refused(mem, client):
    (mem / "personal.md").write_text("# me\n")
    r = client.post("/api/memory", json={"file_type": "personal", "content": BIG}, headers=H)
    assert r.status_code == 400
    assert "20,000" in r.json()["detail"] and "sent with every request" in r.json()["detail"]
    assert (mem / "personal.md").read_text() == "# me\n"                      # nothing written
    ok = client.post("/api/memory", json={"file_type": "professional", "content": "x" * mm.MEMORY_MAX_CHARS},
                     headers=H)
    assert ok.status_code == 200 and len((mem / "professional.md").read_text()) == mm.MEMORY_MAX_CHARS


def test_project_memory_over_the_limit_is_refused(mem, client):
    r = client.post("/api/memory/project/alpha", json={"content": BIG}, headers=H)
    assert r.status_code == 400 and not (mem / "projects" / "alpha.md").exists()


def test_the_model_tool_respects_the_limit(mem, monkeypatch):
    (mem / "personal.md").write_text("x" * (mm.MEMORY_MAX_CHARS - 10))
    out = mm.update_memory("personal", "a fact that is longer than ten characters")
    assert out.startswith("Error:") and "Nothing was saved" in out
    assert len((mem / "personal.md").read_text()) == mm.MEMORY_MAX_CHARS - 10
    assert mm.update_memory("personal", "ok") == "Successfully updated personal memory."


def test_undo_and_restore_may_put_back_a_long_file(mem):
    curator.write_memory("alpha", BIG, "restore", check=False)
    assert len((mem / "projects" / "alpha.md").read_text()) == len(BIG)
    with pytest.raises(mm.MemoryTooLarge):
        curator.write_memory("alpha", BIG + "y", "user")


def test_writes_are_atomic(mem, monkeypatch):
    path = str(mem / "personal.md")
    os.chmod(path, 0o600)
    mm.write_atomic(path, "new\n")
    assert open(path).read() == "new\n" and (os.stat(path).st_mode & 0o777) == 0o600     # mode kept
    real = os.replace

    def broken(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        mm.write_atomic(path, "half")
    monkeypatch.setattr(os, "replace", real)
    assert open(path).read() == "new\n"                                           # the old file is intact
    assert not [f for f in os.listdir(mem) if f.endswith(".tmp")]                 # no leftovers
