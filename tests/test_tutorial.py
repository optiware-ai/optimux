"""The video tutorial (Dashboard -> Help -> Video tutorial)."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from optimux_backend import main

ROOT = Path(__file__).resolve().parent.parent
FILM = ROOT / "web" / "tutorial" / "optimux-tutorial-3.mp4"
URL = "/static/tutorial/optimux-tutorial-3.mp4"

def chapters():
    """The chapter list as the page has it: [[seconds, title], ...]."""
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const TUTORIAL = \{(.*?)\n        \};", js, re.S).group(1)
    return [(float(t), name) for t, name in re.findall(r"\{ t: ([\d.]+), title: tk\('([^']+)'\) \}", block)], block

def test_the_film_ships_with_the_app_and_stays_small():
    assert FILM.is_file() and (ROOT / "web" / "tutorial" / "optimux-tutorial-3.jpg").is_file()
    assert FILM.stat().st_size < 10 * 1024 * 1024         # it travels with every install and update
    head = FILM.read_bytes()[:4096]
    assert head[4:8] == b"ftyp" and b"moov" in head       # the index is first: it plays before it is all there

def test_it_can_be_played_from_any_point():
    client = TestClient(main.app)
    whole = client.get(URL)
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4"
    assert "immutable" in whole.headers["cache-control"]  # the version is in the name
    part = client.get(URL, headers={"Range": "bytes=1000-1999"})  # a jump to a chapter asks for a part
    assert part.status_code == 206 and len(part.content) == 1000
    assert part.content == FILM.read_bytes()[1000:2000]

def test_the_dashboard_has_it_under_help_with_chapters():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    ids = re.findall(r"\{ id: '(\w+)', label: tk\('[^']+'\), icon: '[^']*', group: tk\('Help'\)", js)
    assert ids == ["start", "guide", "video"]
    marks, block = chapters()
    assert URL in block and len(marks) >= 10
    times = [t for t, _ in marks]
    assert times[0] == 0 and times == sorted(times)
    length = float(re.search(r"length: ([\d.]+)", block).group(1))
    assert times[-1] < length
    page = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "data-tutorial-video" in page and "data-tutorial-chapter" in page
    italian = json.loads((ROOT / "web" / "i18n" / "it.json").read_text(encoding="utf-8"))
    assert all(name in italian for _, name in marks) and "Video tutorial" in italian

