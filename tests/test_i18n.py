"""v0.8: the page's languages. Every text marked for translation ($t, tr or tk
with a written string) has its Italian, the placeholders match, and the
language is saved only through its own endpoint."""
import ast
import glob
import html
import json
import os
import re

import pytest

import optimux_backend.core.config_manager as cm
from tests.ws_helpers import make_client

H = {"Authorization": "Bearer test-token"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ("index.html", "web/app.js", "web/boot.js", "web/i18n.js", "web/login.js")
# The sign-in page marks whole elements: their inner HTML (spaces collapsed) is the text
MARKED = re.compile(r"<(\w+)\b[^>]*\bdata-i18n\b[^>]*>(.*?)</\1>", re.S)
# $t('...') in templates, tr('...') in scripts, tk('...') for texts kept as data
CALL = re.compile(r"""(?:\$th|\$t|\btrh|\btr|\btk)\(\s*(['"])((?:\\.|(?!\1).)*?)\1""", re.S)
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text)


def marked_texts() -> set[str]:
    found = set()
    for rel in SOURCES:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            source = f.read()
        texts = {_unescape(m.group(2)) for m in CALL.finditer(source)}
        if rel.endswith(".html"):                  # the browser decodes &quot; and the like first
            texts = {html.unescape(t) for t in texts}
        found |= texts
    with open(os.path.join(ROOT, "web", "login.html"), encoding="utf-8") as f:
        found |= {re.sub(r"\s+", " ", m.group(2)).strip() for m in MARKED.finditer(f.read())}
    return found | server_texts()


def server_texts() -> set[str]:
    """tr("…") and tk("…") in the server (core/i18n.py): the first argument, a written string."""
    found = set()
    for path in glob.glob(os.path.join(ROOT, "optimux_backend", "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("tr", "tk") and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    return found


def catalog(code: str) -> dict:
    with open(os.path.join(ROOT, "web", "i18n", f"{code}.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("code", [c for c in cm.LANGUAGES if c != "en"])
def test_every_marked_text_is_translated(code):
    texts, have = marked_texts(), catalog(code)
    assert len(texts) > 50
    missing = sorted(t for t in texts if not have.get(t, "").strip())
    assert not missing, f"{len(missing)} texts without {code}: {missing[:20]}"


@pytest.mark.parametrize("code", [c for c in cm.LANGUAGES if c != "en"])
def test_translations_keep_their_placeholders_and_are_all_used(code):
    texts, have = marked_texts(), catalog(code)
    for source, target in have.items():
        # every placeholder of the translation exists; one may be left out (an English plural "s")
        assert set(PLACEHOLDER.findall(target)) <= set(PLACEHOLDER.findall(source)), source
    unused = sorted(set(have) - texts)
    assert not unused, f"no longer on the page: {unused[:20]}"


def test_each_language_has_a_file_and_a_name():
    with open(os.path.join(ROOT, "web", "i18n.js"), encoding="utf-8") as f:
        source = f.read()
    for code in cm.LANGUAGES:
        assert f"code: '{code}'" in source
        if code != "en":
            assert isinstance(catalog(code), dict)


def test_the_language_is_saved_only_through_its_endpoint(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    mocker.patch.object(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert client.get("/api/settings", headers=H).json().get("language", "en") == "en"
    assert client.post("/api/language", json={"language": "it"}, headers=H).json() == {"language": "it"}
    assert client.get("/api/settings", headers=H).json()["language"] == "it"
    assert cm.language() == "it"
    # A page with a stale copy of the settings can't change it back
    settings = client.get("/api/settings", headers=H).json()
    client.post("/api/settings", json={**settings, "language": "en", "skills_mode": "manual"}, headers=H)
    assert cm.language() == "it" and cm.get_settings()["skills_mode"] == "manual"
    assert client.post("/api/language", json={"language": "xx"}, headers=H).status_code == 422
    assert client.post("/api/language", json={"language": "en"}).status_code == 401


def test_the_sign_in_page_learns_the_language(mocker, tmp_path):
    client = make_client(mocker, tmp_path)
    mocker.patch.object(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert client.get("/auth/status").json()["language"] == "en"
    client.post("/api/language", json={"language": "it"}, headers=H)
    assert client.get("/auth/status").json()["language"] == "it"


def test_the_server_speaks_the_language(mocker, tmp_path):
    from optimux_backend.core import i18n
    mocker.patch.object(cm, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert i18n.tr("Unknown action: {action}", action="x") == "Unknown action: x"
    cm.set_language("it")
    assert i18n.tr("Unknown action: {action}", action="x") == catalog("it")["Unknown action: {action}"].replace("{action}", "x")
    assert i18n.tr("not a marked text") == "not a marked text"
