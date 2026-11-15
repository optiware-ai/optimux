"""Starter files of a new project: one template per kind of project, and the
first lines of the project's memory."""
import subprocess

import pytest

import optimux_backend.core.workspace as ws
from optimux_backend.core import memory_manager
from tests.test_workspace import root          # noqa: F401  (the fixture)

FOLDERS = {
    "basic": [],
    "python": ["src", "tests"],
    "web": ["css", "js", "assets"],
    "node": ["src", "tests"],
    "java": ["src/main/java", "src/test/java"],
    "c": ["src", "include", "tests"],
    "cpp": ["src", "include", "tests"],
    "rust": ["src", "tests"],
    "go": ["cmd", "internal"],
    "firmware": ["src", "include", "lib", "test", "docs"],
}
IGNORED = {"python": "__pycache__/", "web": "node_modules/", "node": "node_modules/", "java": "*.class",
           "c": "*.o", "cpp": "CMakeCache.txt", "rust": "target/", "go": "*.test", "firmware": "*.hex"}


@pytest.fixture
def memory(tmp_path, mocker):
    d = tmp_path / "memory-projects"
    d.mkdir()
    mocker.patch.object(memory_manager, "PROJECTS_DIR", str(d))
    return d


def test_every_kind_is_offered_to_the_form_and_the_model():
    assert list(ws.TEMPLATES) == list(FOLDERS)
    tool = next(t["function"] for t in ws.WORKSPACE_TOOLS if t["function"]["name"] == "create_project")
    assert tool["parameters"]["properties"]["template"]["enum"] == list(FOLDERS)
    listed = ws.template_list()
    assert [t["id"] for t in listed] == list(FOLDERS)
    assert all(t["label"] and t["creates"] for t in listed)


@pytest.mark.parametrize("kind", list(FOLDERS))
def test_a_template_makes_its_folders_and_its_gitignore(root, memory, kind):
    r, _ = root
    out = ws.create_project("p-" + kind, template=kind, git_init=True)
    assert out["ok"], out["steps"]
    p = r / ("p-" + kind)
    for folder in FOLDERS[kind]:
        assert (p / folder).is_dir()
    text = (p / ".gitignore").read_text()
    assert ".env" in text and (kind == "basic" or IGNORED[kind] in text)
    # git keeps no empty folder: each one holds a file, so the first commit has them all
    tracked = subprocess.run(["git", "ls-files"], cwd=p, capture_output=True, text=True).stdout.split()
    for folder in FOLDERS[kind]:
        assert any(f.startswith(folder + "/") for f in tracked), (folder, tracked)


def test_the_memory_starts_with_the_kind_and_the_purpose(root, memory):
    ws.create_project("fw", template="firmware", git_init=False, description="Blink controller for the STM32 board")
    text = (memory / "fw.md").read_text()
    assert text.startswith(memory_manager.DEFAULT_PROJECT)
    assert "- Kind of project: Firmware" in text and "src/" in text and "include/" in text
    assert "- Purpose: Blink controller for the STM32 board" in text
    assert len(text) < 700                     # it is sent with every request: a few lines, no more


def test_a_plain_project_without_description_gets_no_memory_file(root, memory):
    ws.create_project("plain", git_init=False)
    assert not (memory / "plain.md").exists()
    ws.create_project("plain2", git_init=False, description="Notes")
    assert "- Purpose: Notes" in (memory / "plain2.md").read_text() and "Kind of project" not in (memory / "plain2.md").read_text()


def test_memory_that_is_already_there_is_never_replaced(root, memory):
    (memory / "old.md").write_text("# Project Memory\nmine\n")
    out = ws.create_project("old", template="c", git_init=False, description="x")
    assert out["ok"] and (memory / "old.md").read_text() == "# Project Memory\nmine\n"


def test_an_unknown_kind_is_refused(root):
    with pytest.raises(ws.WorkspaceError, match="firmware"):
        ws.check_create("zeta", template="cobol")


def test_the_form_offers_the_same_kinds_as_the_server():
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const PROJECT_KINDS = \[(.*?)\n\];", js, re.S).group(1)
    rows = re.findall(r"\{ id: '(\w+)', label: (?:tk\()?'([^']+)'\)?, folders: '([^']*)' \}", block)
    assert [(i, label) for i, label, _ in rows] == [(k, v["label"]) for k, v in ws.TEMPLATE_SPECS.items()]
    assert [f for _, _, f in rows] == [", ".join(d + "/" for d in v["dirs"]) for v in ws.TEMPLATE_SPECS.values()]
