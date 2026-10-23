"""IM-10 (v0.6): the Host allow-list against DNS rebinding."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import optimux_backend.main as main
from optimux_backend.core.security import host_allowed, split_host


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "LOCALHOST:8000", "app.localhost:9",
                                  "127.0.0.1:8000", "192.168.1.20:8000", "[::1]:8000", "::1", "10.0.0.5", ""])
def test_safe_hosts_need_no_setting(host):
    assert host_allowed(host, extra="")


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "localhost.evil.example",
                                  "127.0.0.1.nip.io", "optimux.example.com"])
def test_other_names_are_refused(host):
    assert not host_allowed(host, extra="")


def test_the_setting_adds_names():
    extra = " optimux.example.com , box.lan:8000"
    assert host_allowed("optimux.example.com", extra=extra) and host_allowed("Optimux.Example.com:443", extra=extra)
    assert host_allowed("box.lan", extra=extra) and not host_allowed("other.lan", extra=extra)
    assert host_allowed("anything.example", extra="*")
    assert split_host("[fe80::1]:80") == "fe80::1" and split_host("Example.COM.:443") == "example.com"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPTIMUX_ALLOWED_HOSTS", "optimux.example.com")
    return TestClient(main.app)


def test_http_refuses_an_unknown_host(client):
    r = client.get("/auth/status", headers={"Host": "evil.example"})
    assert r.status_code == 400 and "OPTIMUX_ALLOWED_HOSTS" in r.text and "evil.example" in r.text
    assert client.get("/login", headers={"Host": "evil.example:8000"}).status_code == 400
    assert client.get("/auth/status", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/auth/status", headers={"Host": "optimux.example.com"}).status_code == 200


def test_websocket_refuses_an_unknown_host(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws", headers={"Host": "evil.example"}):
            pass
    assert e.value.code == 1008
    with client.websocket_connect("/ws", headers={"Host": "localhost:8000"}) as ws:
        ws.send_text('{"type": "auth", "token": "wrong"}')
        assert "Unauthorized" in ws.receive_text()                       # reached Optimux itself


def test_the_unix_socket_behind_caddy_is_not_checked():
    sent = []

    async def app(scope, receive, send):
        sent.append("app")

    async def run(server):
        scope = {"type": "http", "server": server, "headers": [(b"host", b"optimux.example.org")]}
        await main.HostGuard(app)(scope, None, lambda m: asyncio.sleep(0, sent.append(m["type"])))

    asyncio.run(run(None))                                  # Unix socket: Caddy already checked the domain
    assert sent == ["app"]
    sent.clear()
    asyncio.run(run(("127.0.0.1", 8000)))
    assert sent == ["http.response.start", "http.response.body"]
