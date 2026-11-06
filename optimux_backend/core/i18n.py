"""Languages for the texts the server writes (v0.8).

The page translates its own texts (web/i18n.js); this does the same for what
the server sends it: error messages, notes in the chat, Getting started, the
security checklist. English is the source, and the translations are the same
files the page uses, web/i18n/<code>.json, so one catalog holds everything.

tr("text {name}", name=value) gives the text in the language chosen in
Dashboard -> System, with {name} filled in. tk("text") returns the text as it is:
it marks a text kept as data (a table built at import) so the catalog test finds
it; it is translated with tr() where it is used.

What is sent to the models (instructions, tool descriptions) is not marked and
stays English.
"""
import json
import os
import re

CATALOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "web", "i18n")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_catalogs: dict = {}     # code -> (mtime, messages)
_lang = {"stamp": None, "code": "en"}


def _messages(code: str) -> dict:
    path = os.path.join(CATALOG_DIR, f"{code}.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _catalogs.get(code)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            messages = json.load(f)
    except (OSError, ValueError):
        messages = {}
    _catalogs[code] = (mtime, messages if isinstance(messages, dict) else {})
    return _catalogs[code][1]


def language() -> str:
    """The chosen language, read again only when the settings file changes."""
    from optimux_backend.core import config_manager
    try:
        st = os.stat(config_manager.SETTINGS_FILE)
        stamp = (config_manager.SETTINGS_FILE, st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (config_manager.SETTINGS_FILE, None, None)
    if stamp != _lang["stamp"]:
        try:
            _lang["code"] = config_manager.language()
        except Exception:                          # noqa: BLE001 (a text never breaks a request)
            _lang["code"] = "en"
        _lang["stamp"] = stamp
    return _lang["code"]


def tr(text: str, **values) -> str:
    code = language()
    out = (_messages(code).get(text) if code != "en" else None) or text
    if values:
        out = _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), out)
    return out


def tk(text: str) -> str:
    return text
