"""#3084 (המשך #2488): 64 החריצים של 8443 לא נתפסים בלי אימות על ידי מקור אחד.

* תקרה **לפי כתובת מקור**, לפני ה-TLS ובלי תהליכון: מקור שהגיע ל-`MAX_CONNECTIONS_PER_ADDR`
  נסגר מיד, והחריצים של שאר המקורות נשארים פנויים.
* סירוב אימות (401/403) סוגר את החיבור (`Connection: close`) -- חיבור אחד לא מחזיק חריץ
  לתמיד בבקשות קטנות לא מאומתות.
* שליטה: חיבור שאומת ממשיך ב-keep-alive כמו קודם (ping עם טוקן נכון).

הטסטים החיים רצים על loopback עם TLS אמיתי, כמו test_interserver_2488_2489.
"""

from __future__ import annotations

import socket
import time

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL is needed for the inter-server mTLS (#740)")

from server import db
from server.interserver import api as interserver_api, auth as interserver_auth, storage_nodes
from test_interserver_2488_2489 import (_closed_by_the_server, _handler_threads,  # noqa: F401
                                        _wait_threads)
from test_interserver_tls import _Ctx, _client, primary_certs, secondary  # noqa: F401

live = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="no tls-exporter (pyOpenSSL) -- the live channel cannot be built")


@pytest.fixture()
def per_addr_secondary(tmp_path, monkeypatch):
    """A secondary whose per-source cap is 2 (the global one stays high)."""
    monkeypatch.setattr(interserver_api, "MAX_CONNECTIONS_PER_ADDR", 2, raising=False)
    monkeypatch.setattr(interserver_api, "HANDSHAKE_TIMEOUT_SECONDS", 5.0)
    conn = db.connect(tmp_path / "sec.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    ident = storage_nodes.ensure_identity(conn, tmp_path / "sec", host_sans=["127.0.0.1"])
    server = interserver_api.InterserverTLSServer(
        _Ctx(conn, str(tmp_path / "sec")), "127.0.0.1", 0,
        ident["cert_pem"], ident["key_pem"], data_dir=str(tmp_path / "sec")).start()
    yield {"conn": conn, "ident": ident, "server": server, "port": server.port}
    server.stop()


@live
def test_one_source_cannot_take_more_than_its_share_of_the_slots(per_addr_secondary, primary_certs):
    base = _handler_threads()
    holders = [socket.create_connection(("127.0.0.1", per_addr_secondary["port"]), timeout=10)
               for _ in range(2)]
    try:
        deadline = time.monotonic() + 3
        while _handler_threads() < base + 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _handler_threads() == base + 2
        third = socket.create_connection(("127.0.0.1", per_addr_secondary["port"]), timeout=10)
        try:
            third.settimeout(3)
            started = time.monotonic()
            try:
                data = third.recv(1)
            except ConnectionError:
                data = b""
            except socket.timeout:
                pytest.fail("a third connection from one source was held open")
            assert data == b"" and time.monotonic() - started < 2
            assert _handler_threads() == base + 2, "a thread was started past the per-source cap"
        finally:
            third.close()
    finally:
        for sock in holders:
            sock.close()
    assert _wait_threads(base) == base
    # the per-source counter came back down: a real handshake gets through again (and is refused auth).
    with _client(per_addr_secondary, primary_certs) as client:
        status, _headers, _body = client._request("GET", "/ping")
    assert status == 401


@live
def test_the_per_source_count_returns_to_zero_when_connections_end(per_addr_secondary, primary_certs):
    for _ in range(5):                                   # more than the cap, one after another
        with _client(per_addr_secondary, primary_certs) as client:
            assert client._request("GET", "/ping")[0] == 401
    deadline = time.monotonic() + 3
    server = per_addr_secondary["server"]
    while server._per_addr and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server._per_addr == {}


@live
def test_an_authentication_refusal_closes_the_connection(secondary, primary_certs):
    base = _handler_threads()
    with _client(secondary, primary_certs) as client:
        status, headers, _body = client._request("GET", "/ping")
        assert status == 401
        assert headers.get("connection", "").lower() == "close"
        client._conn.limit(3.0, total=True, waiting_for="close")
        assert _closed_by_the_server(client._conn), "the secondary kept a refused connection open"
    assert _wait_threads(base) == base, "the refused connection's thread was not released"


@live
def test_control_an_authenticated_connection_still_keeps_alive(secondary, primary_certs):
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        token = client.pair(code=code, primary_id="primary-live")["token"]
    with _client(secondary, primary_certs) as client:
        assert client.ping(token)["ok"] is True
        assert client.ping(token)["ok"] is True            # the same connection, twice
