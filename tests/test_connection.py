"""The page's own check of its line to Optimux (web/connection.js), run with Node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "web" / "connection.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


def run(expr: str):
    code = f"const C = require({json.dumps(str(JS))}); console.log(JSON.stringify({expr}));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def state(**s):
    full = {"socket": "connected", "online": True, "now": 100_000, "pingAt": 0, "heardAt": 99_000, "rtt": 40}
    return run(f"C.linkState({json.dumps({**full, **s})})")


def test_a_line_that_answers_is_fine():
    assert state() == "ok"
    assert state(pingAt=99_500, heardAt=99_540) == "ok"            # asked, answered
    assert state(pingAt=99_000, heardAt=98_000) == "ok"            # asked a second ago: still waiting, fine


def test_a_check_that_waits_is_a_weak_line():
    assert state(pingAt=96_500, heardAt=95_000) == "weak"          # 3.5 s with no answer
    assert state(rtt=2600) == "weak"                               # the last answer took 2.6 s
    assert state(pingAt=96_500, heardAt=97_000) == "ok"            # something arrived after the check


def test_no_answer_for_long_is_no_line():
    assert state(pingAt=87_000, heardAt=86_000) == "down"          # 13 s with no answer
    assert state(socket="disconnected") == "down"
    assert state(socket="connecting") == "down"
    assert state(online=False) == "down"                           # the browser itself says it is offline


def test_the_page_gives_up_on_a_dead_socket_itself():
    give_up = lambda **s: run(f"C.shouldReconnect({json.dumps({'socket': 'connected', 'online': True, 'now': 100_000, 'pingAt': 0, 'heardAt': 99_000, **s})})")
    assert give_up(pingAt=87_000, heardAt=86_000)                  # the socket never said it closed
    assert not give_up(pingAt=96_500, heardAt=95_000)              # weak is not dead
    assert not give_up(socket="disconnected", pingAt=87_000, heardAt=86_000)   # already reconnecting
    assert not give_up()


def test_it_pings_when_nothing_is_pending():
    ping = lambda **s: run(f"C.shouldPing({json.dumps({'socket': 'connected', 'now': 100_000, 'pingAt': 0, 'heardAt': 99_000, **s})})")
    assert ping(pingAt=94_000, heardAt=94_050)                     # the last one was answered 5 s ago
    assert not ping(pingAt=98_000, heardAt=98_040)                 # too soon
    assert not ping(pingAt=94_000, heardAt=93_000)                 # one is still out: its wait is the measure
    assert not ping(socket="connecting")


def note(**info):
    return run(f"C.linkNote({json.dumps({'state': 'ok', 'secs': 0, 'working': False, 'grace': 120, **info})})")


def test_the_notice_says_what_happens_to_the_request():
    assert note() == ""
    assert note(state="weak", working=True) == \
        "Weak connection to Optimux. The reply may arrive late; nothing is lost."
    assert note(state="weak") == "Weak connection to Optimux."
    assert note(state="down", secs=9, working=True) == \
        ("No connection to Optimux for 9 s. The request goes on there for up to 120 s "
         "and comes back here when the connection does.")
    assert note(state="down", secs=9) == "No connection to Optimux for 9 s. Reconnecting…"
    assert note(state="down", secs=1) == ""                        # a blink is not worth a banner


def test_a_silent_model_says_where_the_wait_is():
    wait = lambda **st: run(f"C.waitNote({json.dumps(st)})")
    assert wait(net="down", host="api.anthropic.com") == \
        "This Optimux machine can't reach api.anthropic.com: check its internet connection."
    assert wait(net="ok", host="api.anthropic.com") == \
        "api.anthropic.com answers, so the model itself is slow or thinking."
    assert wait() == ""
