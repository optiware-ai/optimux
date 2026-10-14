"""Feature 23: password sign-in, sessions, two-step codes, rate limits,
the HTTP guard and security headers."""
import json
import os
import re
import time

import pytest
from fastapi.testclient import TestClient

from optimux_backend.core import auth, paths

ORIGIN = {"Origin": "http://testserver"}
PW = "correct horse battery"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HOME", str(tmp_path))
    for lim in (auth.limiter, auth.global_limiter, auth.code_limiter):
        lim.__init__(lim.limit, lim.window)
    return tmp_path


@pytest.fixture
def client(home, monkeypatch):
    monkeypatch.setenv("OPTIMUX_TOKEN", "test-token")
    from optimux_backend import main
    main.LIVE_SOCKETS.clear()
    return TestClient(main.app)


def setup_password(client):
    r = client.post("/auth/setup", json={"token": "test-token", "password": PW}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    return r


# ---------------------------------------------------------------- passwords

def test_password_hash_roundtrip_and_salt():
    a, b = auth.hash_password("x" * 12), auth.hash_password("x" * 12)
    assert a != b and a.startswith("scrypt$")
    assert auth.verify_password("x" * 12, a)
    assert not auth.verify_password("y" * 12, a)
    assert not auth.verify_password("x", "garbage")


def test_password_file_is_private(home):
    auth.set_password(PW)
    assert oct(os.stat(auth.auth_file()).st_mode & 0o777) == "0o600"
    assert PW not in open(auth.auth_file()).read()
    assert auth.check_password(PW) and not auth.check_password(PW + "!")


def test_short_password_refused(home):
    with pytest.raises(auth.AuthError):
        auth.set_password("short")


# ---------------------------------------------------------------- TOTP

def test_totp_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890", T=59 -> 94287082 (8 digits); 6 digits -> 287082
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth.totp_code(secret, 59 // 30) == "287082"


def test_totp_window_and_replay(home):
    secret = auth.new_totp_secret()
    now = 1_000_000.0
    step = int(now // 30)
    assert auth.match_totp(secret, auth.totp_code(secret, step), now) == step
    assert auth.match_totp(secret, auth.totp_code(secret, step - 1), now) == step - 1
    assert auth.match_totp(secret, auth.totp_code(secret, step - 2), now) is None
    assert auth.match_totp(secret, auth.totp_code(secret, step), now, after_step=step) is None
    assert auth.match_totp(secret, "abc", now) is None


def test_totp_enable_check_once_disable(home):
    started = auth.start_totp()
    assert started["uri"].startswith("otpauth://totp/") and not auth.totp_enabled()
    step = int(time.time() // 30)
    assert auth.confirm_totp(auth.totp_code(started["secret"], step))
    assert auth.totp_enabled()
    nxt = auth.totp_code(started["secret"], step + 1)
    assert auth.check_totp(nxt)
    assert not auth.check_totp(nxt)              # the same code works once
    auth.disable_totp()
    assert not auth.totp_enabled()


# ---------------------------------------------------------------- sessions

def test_session_lifecycle(home):
    token = auth.create_session("1.2.3.4", "test", now=1000)
    sid = auth.check_session(token, now=1000)
    assert sid and sid != token                    # only the hash is stored
    assert auth.check_session("nope", now=1000) is None
    assert auth.check_session(token, now=1000 + auth.IDLE_SECONDS + 5) is None   # idle too long
    token2 = auth.create_session(now=2000)
    assert auth.check_session(token2, now=2000 + auth.MAX_AGE_SECONDS + 1) is None


def test_end_all_sessions_keeps_current(home):
    a, b = auth.create_session(), auth.create_session()
    keep = auth.check_session(a)
    assert auth.end_all_sessions(keep_id=keep) == 1
    assert auth.check_session(a) and not auth.check_session(b)


def test_rate_limiter():
    rl = auth.RateLimiter(limit=3, window=100)
    for _ in range(3):
        assert rl.retry_after("ip", now=0) == 0
        rl.fail("ip", now=0)
    assert rl.retry_after("ip", now=10) == 90
    assert rl.retry_after("other", now=10) == 0
    assert rl.retry_after("ip", now=100) == 0     # window passed
    rl.fail("ip", now=200); rl.clear("ip")
    assert rl.retry_after("ip", now=200) == 0


# ---------------------------------------------------------------- HTTP

def test_api_needs_sign_in(client):
    assert client.get("/api/settings").status_code == 401
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/static/login.js").status_code == 200
    assert client.get("/docs").status_code in (401, 404)
    assert client.get("/openapi.json").status_code in (401, 404)


def test_bearer_token_still_works_for_scripts(client):
    assert client.get("/api/settings", headers={"Authorization": "Bearer test-token"}).status_code == 200
    assert client.get("/api/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_setup_then_login_and_logout(client):
    st = client.get("/auth/status").json()
    assert st["setup_needed"] and not st["signed_in"]
    assert client.post("/auth/setup", json={"token": "bad", "password": PW}, headers=ORIGIN).status_code == 401
    r = setup_password(client)
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert client.get("/api/settings").status_code == 200          # cookie kept by the client
    assert client.post("/auth/setup", json={"token": "test-token", "password": PW}, headers=ORIGIN).status_code == 409
    assert client.post("/auth/logout", headers=ORIGIN).status_code == 200
    assert client.get("/api/settings").status_code == 401
    assert client.post("/auth/login", json={"password": "wrong password!"}, headers=ORIGIN).status_code == 401
    assert client.post("/auth/login", json={"password": PW}, headers=ORIGIN).status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_cross_site_changes_refused(client):
    setup_password(client)
    assert client.post("/api/settings", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/settings", json={}).status_code == 403            # no Origin, no Referer
    assert client.post("/auth/login", json={"password": PW}, headers={"Origin": "https://evil.example"}).status_code == 403
    # Reads don't change anything and are allowed
    assert client.get("/api/settings").status_code == 200


def test_login_lockout(client):
    setup_password(client)
    client.post("/auth/logout", headers=ORIGIN)
    for _ in range(5):
        assert client.post("/auth/login", json={"password": "wrong password!"}, headers=ORIGIN).status_code == 401
    r = client.post("/auth/login", json={"password": PW}, headers=ORIGIN)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_login_with_two_step_code(client):
    setup_password(client)
    started = client.post("/api/auth/totp/start", json={"password": PW}, headers=ORIGIN).json()
    step = int(time.time() // 30)
    assert client.post("/api/auth/totp/confirm", json={"code": auth.totp_code(started["secret"], step)}, headers=ORIGIN).status_code == 200
    client.post("/auth/logout", headers=ORIGIN)
    r = client.post("/auth/login", json={"password": PW}, headers=ORIGIN)
    assert r.status_code == 401 and r.json().get("need_code")
    assert client.post("/auth/login", json={"password": PW, "code": "000000"}, headers=ORIGIN).status_code == 401
    ok = client.post("/auth/login", json={"password": PW, "code": auth.totp_code(started["secret"], step + 1)}, headers=ORIGIN)
    assert ok.status_code == 200


def test_change_password_signs_out_others(client, home):
    setup_password(client)
    other = auth.create_session()
    r = client.post("/api/auth/password", json={"current": PW, "new": PW + " again"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["ended"] == 1
    assert auth.check_session(other) is None
    assert client.get("/api/settings").status_code == 200          # this browser stays in
    assert client.post("/api/auth/password", json={"current": "wrong", "new": PW}, headers=ORIGIN).status_code == 400


def test_security_headers(client):
    r = client.get("/login")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-eval'" in csp and "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers           # plain http here
    assert client.get("/api/settings").headers["content-security-policy"]   # also on refusals


def test_own_static_files_are_checked_on_every_load(client):
    """After an update the page must never run an old app.js from the browser's cache."""
    r = client.get("/static/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    again = client.get("/static/app.js", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304                                     # unchanged: nothing re-sent
    vendor = client.get("/static/vendor/vue-3.5.13.global.prod.js")
    assert vendor.headers["cache-control"] == "public, max-age=31536000, immutable"   # versioned name


def test_websocket_uses_cookie(client):
    setup_password(client)
    with client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "auth_ok"


def test_websocket_without_cookie_needs_token(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "wrong"}))
        assert json.loads(ws.receive_text())["type"] == "error"


def test_security_checklist(client):
    setup_password(client)
    checks = {c["id"]: c for c in client.get("/api/security").json()["checks"]}
    assert {"https", "totp", "sandbox", "approve", "server"} <= set(checks)
    assert checks["approve"]["ok"] and not checks["server"]["ok"]


# ---------------------------------------------------------------- page assets

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_pages_load_nothing_from_other_sites():
    for name in ("index.html", "web/login.html"):
        html = open(os.path.join(ROOT, name)).read()
        assert not re.search(r"<script[^>]+src=\"(https?:)?//", html), name
        assert not re.search(r"<link[^>]+href=\"(https?:)?//", html), name
        assert not re.search(r"<script>(?!\s*</script>)", html), f"inline script in {name}"


def test_built_css_has_the_classes_in_the_html():
    """web/app.css is built from the HTML (scripts/build-css.sh); a class added
    without rebuilding would silently do nothing."""
    css = open(os.path.join(ROOT, "web", "app.css")).read()
    missing = set()
    for name in ("index.html", "web/login.html"):
        html = open(os.path.join(ROOT, name)).read()
        for attr in re.findall(r'\sclass="([^"]*)"', html):
            for cls in attr.split():
                if "{" in cls or cls.startswith(("cm-", "hljs", "prose-blue")) or cls in ("group", "hidden-x"):
                    continue
                esc = re.sub(r"([:\[\]/.%#()'!])", r"\\\1", cls).replace(",", "\\2c ")
                if "." + esc not in css:
                    missing.add(cls)
    assert not missing, f"rebuild web/app.css (scripts/build-css.sh); missing: {sorted(missing)[:20]}"


def test_guide_matches_the_code():
    text = open(os.path.join(ROOT, "docs", "GUIDE.md")).read()
    assert "\n## Running on a server\n" in text and "\n### Signing in\n" in text
    assert f"**{auth.limiter.limit} wrong attempts**" in text and f"wait {int(auth.limiter.window // 60)} minutes" in text
    assert f"{auth.MIN_PASSWORD} characters or more" in text
    assert f"**{auth.MAX_AGE_SECONDS // 86400} days** ({auth.IDLE_SECONDS // 86400} days without use)" in text
    from optimux_backend.core import config_manager as cm
    for name in ("PATH", "HOME", "TERM", "TZ"):
        assert name in cm.BASE_COMMAND_ENV and f"`{name}`" in text
    assert "| File size (MB) | 1024 |" in text and cm.SettingsPayload().limit_file_mb == 1024
    for f in ("optimux.service", "optimux.env.example", "Caddyfile", "apparmor-bwrap"):
        assert os.path.exists(os.path.join(ROOT, "deploy", f)) and f in open(os.path.join(ROOT, "deploy", "README.md")).read()


# ---------------------------------------------------------------- review fixes

def test_client_key_groups_ipv6():
    assert auth.client_key("203.0.113.9") == "203.0.113.9"
    assert auth.client_key("2001:db8:1:2::1") == auth.client_key("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert auth.client_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert auth.client_key("unknown") == "unknown"


def test_global_limit_stops_address_hopping(home):
    for i in range(auth.global_limiter.limit):
        auth.password_failed(f"10.0.{i // 250}.{i % 250}")
    assert auth.password_retry_after("192.0.2.77") > 0   # a fresh address can't guess passwords either
    assert auth.retry_after("192.0.2.77") == 0           # but the token and setup still work


def test_junk_tokens_cannot_lock_everyone_out(client):
    for i in range(40):
        client.get("/api/settings", headers={"Authorization": "Bearer junk"})
        auth.limiter.clear(auth.client_key("testclient"))  # as if from many addresses
    assert client.get("/api/settings", headers={"Authorization": "Bearer test-token"}).status_code == 200
    setup_password(client)                               # setup with the token isn't blocked
    client.post("/auth/logout", headers=ORIGIN)
    assert client.post("/auth/login", json={"password": PW}, headers=ORIGIN).status_code == 200


def test_limiter_prunes_expired_keys():
    rl = auth.RateLimiter(limit=3, window=10)
    for i in range(10001):
        rl.fail(f"k{i}", now=0)
    rl.fail("late", now=100)                              # over the cap: expired keys dropped
    assert len(rl._fails) == 1


def test_codes_limited_for_the_account(client):
    setup_password(client)
    started = client.post("/api/auth/totp/start", json={"password": PW}, headers=ORIGIN).json()
    step = int(time.time() // 30)
    client.post("/api/auth/totp/confirm", json={"code": auth.totp_code(started["secret"], step)}, headers=ORIGIN)
    client.post("/auth/logout", headers=ORIGIN)
    for i in range(auth.code_limiter.limit):
        auth.limiter.clear(auth.client_key("testclient"))  # pretend each try comes from a new address
        client.post("/auth/login", json={"password": PW, "code": "000000"}, headers=ORIGIN)
    auth.limiter.clear(auth.client_key("testclient"))
    r = client.post("/auth/login", json={"password": PW, "code": auth.totp_code(started["secret"], step + 1)}, headers=ORIGIN)
    assert r.status_code == 429


def test_totp_cannot_be_replaced_while_on(client):
    setup_password(client)
    started = client.post("/api/auth/totp/start", json={"password": PW}, headers=ORIGIN).json()
    client.post("/api/auth/totp/confirm", json={"code": auth.totp_code(started["secret"], int(time.time() // 30))}, headers=ORIGIN)
    assert client.post("/api/auth/totp/start", json={"password": PW}, headers=ORIGIN).status_code == 409


def test_non_ascii_digits_are_refused():
    secret = auth.new_totp_secret()
    assert auth.match_totp(secret, "\u0661\u0662\u0663\u0664\u0665\u0666") is None


def test_unreadable_auth_file_fails_closed(home):
    auth.set_password(PW)
    with open(auth.auth_file(), "w") as f:
        f.write("{ not json")
    assert auth.password_set() and auth.totp_enabled()
    assert not auth.check_password(PW) and not auth.check_totp("123456")
    with pytest.raises(auth.AuthError):
        auth.set_password(PW)


def test_auth_file_written_atomically(home):
    auth.set_password(PW)
    before = os.stat(auth.auth_file()).st_ino
    auth.set_password(PW + "x")
    assert os.stat(auth.auth_file()).st_ino != before      # replaced, not rewritten in place
    assert [n for n in os.listdir(home) if n.endswith(".tmp")] == []


def test_setup_closes_open_chats(client, home):
    setup_password(client)
    with client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "auth_ok"
        os.remove(auth.auth_file())                     # "forgot the password" reset
        other = TestClient(client.app)
        assert other.post("/auth/setup", json={"token": "test-token", "password": PW}, headers=ORIGIN).status_code == 200
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_chat_closes_when_its_sign_in_is_gone(client, home):
    setup_password(client)
    with client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "auth_ok"
        conn = __import__("optimux_backend.core.store", fromlist=["x"]).connect()
        with conn:
            conn.execute("DELETE FROM logins")             # e.g. expired
        conn.close()
        ws.send_text(json.dumps({"type": "new_chat"}))
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            for _ in range(5):
                ws.receive_text()


def test_deploy_unit_keeps_the_sandbox_working():
    unit = open(os.path.join(ROOT, "deploy", "optimux.service")).read()
    active = [l.split("=", 1)[0] for l in unit.splitlines() if l and not l.startswith("#") and "=" in l]
    for breaks_proc in ("ProtectKernelTunables", "ProtectKernelLogs", "ProtectProc", "ProcSubset", "RestrictNamespaces"):
        assert breaks_proc not in active
    assert "--uds /run/optimux/optimux.sock" in unit and "--port" not in unit
    assert "unix//run/optimux/optimux.sock" in open(os.path.join(ROOT, "deploy", "Caddyfile")).read()


def test_other_browsers_hear_about_a_new_sign_in(client):
    setup_password(client)
    with client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive_text())["type"] == "auth_ok"
        other = TestClient(client.app, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/131.0"})
        assert other.post("/auth/login", json={"password": PW}, headers=ORIGIN).status_code == 200
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "auth_event" and msg["event"] == "signin"
        assert "Firefox" in msg["agent"] and msg["ip"]
        assert other.post("/auth/logout", headers=ORIGIN).status_code == 200
        assert json.loads(ws.receive_text())["event"] == "signout"
