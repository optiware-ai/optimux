"""Team version, step 6: every member has personal and technical notes of their own.
They go to the member's own requests only; the admin's notes and other members' notes stay apart."""
import contextvars

import pytest

from optimux_backend.core import config_manager as cm, memory_manager as mm, team
from tests.test_team_chat import crew, ws_root  # noqa: F401
from tests.test_team_users import ORIGIN, app_client, home, invite_and_activate, solo, teamed  # noqa: F401


@pytest.fixture
def notes(tmp_path, monkeypatch):
    for attr, text in (("PERSONAL_MD", "ADMIN-PERSONAL-SECRET"), ("PROFESSIONAL_MD", "ADMIN-TECH-SECRET")):
        f = tmp_path / (attr.lower() + ".md")
        f.write_text(text)
        monkeypatch.setattr(mm, attr, str(f))
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "alpha.md").write_text("ALPHA-NOTES")
    monkeypatch.setattr(mm, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(mm, "MEMBERS_DIR", str(tmp_path / "members"))
    monkeypatch.setattr(mm, "init_memory", lambda: None)
    return tmp_path


def as_member(uid, fn, project="alpha"):
    def run():
        team.bind_user(uid)
        cm.bind_project(project)
        return fn()
    return contextvars.copy_context().run(run)


def test_a_new_member_starts_with_the_default_notes(notes):
    assert mm.member_notes(7, "personal") == mm.DEFAULT_PERSONAL
    assert mm.member_notes(7, "professional") == mm.DEFAULT_PROFESSIONAL


def test_a_member_notes_are_saved_apart_from_everyone_else(notes):
    mm.save_member_notes(7, "personal", "I like short answers")
    mm.save_member_notes(8, "personal", "Other person")
    assert mm.member_notes(7, "personal") == "I like short answers"
    assert mm.member_notes(8, "personal") == "Other person"
    assert (notes / "personal_md.md").read_text() == "ADMIN-PERSONAL-SECRET"


def test_a_members_model_gets_their_own_notes_and_the_projects(notes):
    mm.save_member_notes(7, "personal", "MY-PERSONAL")
    mm.save_member_notes(7, "professional", "MY-TECH")
    mm.save_member_notes(8, "personal", "NOT-MINE")
    text = as_member(7, mm.get_memory_context)
    assert "MY-PERSONAL" in text and "MY-TECH" in text and "ALPHA-NOTES" in text
    assert "NOT-MINE" not in text and "ADMIN-PERSONAL-SECRET" not in text and "ADMIN-TECH-SECRET" not in text
    assert "ADMIN-PERSONAL-SECRET" in mm.get_memory_context()          # the admin keeps theirs


def test_the_model_saves_to_the_members_own_notes(notes):
    assert as_member(7, lambda: mm.update_memory("personal", "likes tabs")).startswith("Successfully")
    assert as_member(7, lambda: mm.update_memory("professional", "uses pytest")).startswith("Successfully")
    assert "likes tabs" in mm.member_notes(7, "personal") and "uses pytest" in mm.member_notes(7, "professional")
    assert (notes / "personal_md.md").read_text() == "ADMIN-PERSONAL-SECRET"
    assert (notes / "professional_md.md").read_text() == "ADMIN-TECH-SECRET"
    assert mm.member_notes(8, "personal") == mm.DEFAULT_PERSONAL


def test_a_members_notes_have_the_same_size_limit(notes):
    with pytest.raises(mm.MemoryTooLarge):
        mm.save_member_notes(7, "personal", "x" * (mm.MEMORY_MAX_CHARS + 1))
    assert as_member(7, lambda: mm.update_memory("personal", "x" * (mm.MEMORY_MAX_CHARS + 1))).startswith("Error")


def test_a_member_reads_and_saves_their_notes_in_the_page(crew, notes):
    anna = crew["anna"][0]
    first = anna.get("/api/me/memory/personal").json()
    assert first["content"] == mm.DEFAULT_PERSONAL
    r = anna.post("/api/me/memory", json={"file_type": "personal", "content": "Anna likes plain words"}, headers=ORIGIN)
    assert r.status_code == 200
    assert anna.get("/api/me/memory/personal").json()["content"] == "Anna likes plain words"
    assert crew["bob"][0].get("/api/me/memory/personal").json()["content"] == mm.DEFAULT_PERSONAL
    assert (notes / "personal_md.md").read_text() == "ADMIN-PERSONAL-SECRET"


def test_the_notes_page_refuses_bad_requests(crew, notes):
    anna = crew["anna"][0]
    assert anna.get("/api/me/memory/secrets").status_code == 400
    assert anna.post("/api/me/memory", json={"file_type": "project", "content": "x"}, headers=ORIGIN).status_code == 400
    big = anna.post("/api/me/memory", json={"file_type": "personal", "content": "x" * 30000}, headers=ORIGIN)
    assert big.status_code == 400 and "limit" in big.json()["detail"]
    assert crew["admin"].get("/api/me/memory/personal").status_code == 409


def test_a_member_still_cannot_reach_the_admins_notes_or_anyone_elses(crew, notes):
    anna = crew["anna"][0]
    assert anna.get("/api/memory/personal").status_code == 403
    assert anna.post("/api/memory", json={"file_type": "personal", "content": "x"}, headers=ORIGIN).status_code == 403
    assert "ADMIN-PERSONAL-SECRET" not in anna.get("/api/me/memory/personal").text


def test_a_disabled_member_cannot_read_their_notes(crew, notes):
    anna = crew["anna"][0]
    crew["admin"].post(f"/api/team/users/{crew['anna'][1]}/disabled", json={"disabled": True}, headers=ORIGIN)
    assert anna.get("/api/me/memory/personal").status_code == 403
