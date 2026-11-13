"""#2488 + #2489 (5, 8) — the inter-server channel, before and after authentication.

* **#2488** — on a secondary, port 8443 completes the TLS handshake with any
  self-signed client certificate; authentication comes later, in the request.
  Before it: the head was read with no size cap, there was no timeout after the
  handshake, and every connection got a thread with no cap. Now: a head past
  `MAX_HEAD_BYTES` -> `431` and close; the head (and a JSON body) must arrive
  within `READ_TIMEOUT_SECONDS`; at most `MAX_CONNECTIONS` at once, the rest
  closed at accept, before TLS and with no thread.
* **#2489 (5)** — the primary read a secondary's answer with no cap on the head
  or on `Content-Length`. Now `MAX_RESPONSE_HEAD` / `MAX_RESPONSE_BODY`.
* **#2489 (8)** — the staging folder of a pull is `.incoming/<image_id>`, by the
  image only; two pulls of one image from two secondaries wrote into the same
  folder. Now a pull is refused while one of the same image runs from any node.

The live tests run real TLS on loopback, as `test_interserver_tls`. What is not
measured here: how much memory and how many threads a secondary takes before it
falls, and whether 8443 is reachable from an untrusted host -- that is the VM lab.

Negative control: with every production file of this PR back at origin/main,
every test here fails except the ones marked "control".
"""

from __future__ import annotations

import socket
import time

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL is needed for the inter-server mTLS (#740)")

from server import db
from server.interserver import (api as interserver_api, auth as interserver_auth,
                                storage_client, storage_nodes, storage_transfer)
from test_interserver_tls import _Ctx, _client, primary_certs, secondary  # noqa: F401

live = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="no tls-exporter (pyOpenSSL) -- the live channel cannot be built")


def _handler_threads() -> int:
    import threading
    return sum(1 for t in threading.enumerate() if t.name == interserver_api.HANDLER_THREAD_NAME)


def _wait_threads(at_most: int, seconds: float = 3.0) -> int:
    deadline = time.monotonic() + seconds
    while _handler_threads() > at_most and time.monotonic() < deadline:
        time.sleep(0.05)
    return _handler_threads()


def _closed_by_the_server(conn) -> bool:
    """True = the secondary closed (EOF, close_notify or reset); False = it is
    still holding the connection open when our own deadline ran out."""
    from OpenSSL import SSL
    try:
        return conn.recv(1) == b""
    except storage_client.InterserverTimeout:
        return False
    except (SSL.ZeroReturnError, SSL.SysCallError, ConnectionError):
        return True


# --- #2488: the head ------------------------------------------------------------------


@live
def test_a_head_past_the_cap_without_its_end_is_431(secondary, primary_certs):
    cap = getattr(interserver_api, "MAX_HEAD_BYTES", 64 * 1024)
    base = _handler_threads()
    with _client(secondary, primary_certs) as client:
        conn = client._conn
        conn.limit(5.0, total=True, waiting_for="431")
        start = b"GET /api/interserver/v1/ping HTTP/1.1\r\nX-Pad: "
        conn.sendall(start + b"a" * (cap + 1 - len(start)))
        try:
            status, _headers, _rest = storage_client._read_http_headers(conn)
        except storage_client.InterserverTimeout:
            pytest.fail("the secondary kept reading a head past the cap -- no answer in 5s")
        assert status == 431
        assert _closed_by_the_server(conn)
    assert _wait_threads(base) == base, "the connection's thread was not released"


@live
def test_a_head_under_the_cap_is_answered(secondary, primary_certs):
    """Control: a long header that ends is a request like any other."""
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        token = client.pair(code=code, primary_id="primary-live")["token"]
        status, _h, body = client._request(
            "GET", "/ping", headers={"Authorization": f"Bearer {token}", "X-Pad": "a" * 8000})
    assert status == 200 and body["ok"] is True


# --- #2488: a timeout after the handshake ---------------------------------------------


@live
def test_a_silent_connection_after_the_handshake_is_closed(secondary, primary_certs,
                                                           monkeypatch):
    monkeypatch.setattr(interserver_api, "READ_TIMEOUT_SECONDS", 0.5, raising=False)
    base = _handler_threads()
    with _client(secondary, primary_certs) as client:
        client._conn.limit(5.0, total=True, waiting_for="close")
        started = time.monotonic()
        assert _closed_by_the_server(client._conn), \
            "the secondary held a silent connection open for 5s after the handshake"
        assert time.monotonic() - started < 4
    assert _wait_threads(base) == base, "the connection's thread was not released"


@live
def test_a_body_that_stops_coming_is_400_and_closed(secondary, primary_certs, monkeypatch):
    monkeypatch.setattr(interserver_api, "READ_TIMEOUT_SECONDS", 0.5, raising=False)
    with _client(secondary, primary_certs) as client:
        conn = client._conn
        conn.limit(5.0, total=True, waiting_for="400")
        conn.sendall(b"POST /api/interserver/v1/pair/begin HTTP/1.1\r\n"
                     b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"a\":")
        try:
            status, _headers, _rest = storage_client._read_http_headers(conn)
        except storage_client.InterserverTimeout:
            pytest.fail("the secondary waited for the rest of the body for 5s")
        assert status == 400


@live
def test_keep_alive_inside_the_timeout_still_answers(secondary, primary_certs, monkeypatch):
    """Control: the timeout is per request, not per connection."""
    monkeypatch.setattr(interserver_api, "READ_TIMEOUT_SECONDS", 1.0, raising=False)
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        token = client.pair(code=code, primary_id="primary-live")["token"]
        for _ in range(3):
            time.sleep(0.3)
            assert client.ping(token)["ok"] is True


# --- #2488: the connection cap --------------------------------------------------------


@pytest.fixture()
def capped_secondary(tmp_path, monkeypatch):
    """A secondary with a cap of 2, built after the cap is set."""
    monkeypatch.setattr(interserver_api, "MAX_CONNECTIONS", 2, raising=False)
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
def test_past_the_connection_cap_a_connection_is_closed_at_once(capped_secondary,
                                                                primary_certs):
    base = _handler_threads()
    holders = [socket.create_connection(("127.0.0.1", capped_secondary["port"]), timeout=10)
               for _ in range(2)]
    try:
        deadline = time.monotonic() + 3
        while _handler_threads() < base + 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _handler_threads() == base + 2
        third = socket.create_connection(("127.0.0.1", capped_secondary["port"]), timeout=10)
        try:
            third.settimeout(3)
            started = time.monotonic()
            try:
                data = third.recv(1)
            except ConnectionError:
                data = b""
            except socket.timeout:
                pytest.fail("a connection past the cap was held open (waiting for a handshake)")
            assert data == b"" and time.monotonic() - started < 2
            assert _handler_threads() == base + 2, "a thread was started past the cap"
        finally:
            third.close()
    finally:
        for sock in holders:
            sock.close()
    assert _wait_threads(base) == base
    # the slots came back: a real handshake gets through again.
    with _client(capped_secondary, primary_certs) as client:
        status, _h, _body = client._request("GET", "/ping")
    assert status == 401


# --- #2489 (5): the primary reads a secondary's answer with a cap ---------------------


class _Overread(Exception):
    """Not an `InterserverClientError`: the fake ran out before the cap stopped it."""


class _Endless:
    """A connection that answers `prefix`, then `filler` for ever (up to `limit`)."""

    def __init__(self, prefix: bytes, filler: bytes = b"a", limit: int = 8 * 1024 * 1024):
        self._prefix = prefix
        self._filler = filler
        self.served = 0
        self._limit = limit

    def recv(self, n: int) -> bytes:
        if self.served >= self._limit:
            raise _Overread(f"read {self.served} bytes and still reading")
        if self.served < len(self._prefix):
            out = self._prefix[self.served:self.served + n]
        else:
            out = self._filler * n
        self.served += len(out)
        return out


def test_a_head_with_no_end_from_the_secondary_is_refused():
    conn = _Endless(b"HTTP/1.1 200 OK\r\nX-Pad: ")
    with pytest.raises(storage_client.InterserverClientError):
        storage_client._read_http_headers(conn)
    assert conn.served <= getattr(storage_client, "MAX_RESPONSE_HEAD", 64 * 1024) + 8192


def test_a_huge_content_length_from_the_secondary_is_refused_before_the_body():
    conn = _Endless(b"HTTP/1.1 200 OK\r\nContent-Length: 100000000000\r\n\r\n")
    with pytest.raises(storage_client.InterserverClientError):
        storage_client._read_http_response(conn)
    assert conn.served < 4096 * 4


def test_a_normal_answer_is_read_as_before():
    """Control."""
    conn = _Endless(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"ok":true}')
    status, headers, body = storage_client._read_http_response(conn)
    assert status == 200 and body == b'{"ok":true}'


# --- #2489 (8): one pull of an image at a time, from any secondary --------------------


def _fake_node(server, n: int) -> str:
    return storage_nodes.enroll_node(
        server["ctx"].conn, ("noc", "admin"), label=f"NODE{n}",
        base_url=f"https://127.0.0.{n}:8443/api/interserver/v1",
        node_id=f"sn_{n:016x}", pinned_spki=f"{n:02x}" * 32,
        client_cert_ref="unused", credential_ref="unused", protocol_version="2.1")


def test_a_second_pull_of_the_same_image_from_another_secondary_is_refused(server, tmp_path):
    a, b = _fake_node(server, 1), _fake_node(server, 2)
    image = "img_c0ffee"
    storage_transfer.start_pull(server["ctx"], tmp_path / "data", a, image,
                                ("noc", "admin"), run_in_thread=False)
    with pytest.raises(storage_transfer.TransferError) as exc:
        storage_transfer.start_pull(server["ctx"], tmp_path / "data", b, image,
                                    ("noc", "admin"), run_in_thread=False)
    assert exc.value.status == 409
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) FROM storage_transfers WHERE image_id = ?", (image,)).fetchone()[0] == 1


def test_another_image_from_another_secondary_still_pulls(server, tmp_path):
    """Control: the refusal is by image, not "one pull at a time"."""
    a, b = _fake_node(server, 1), _fake_node(server, 2)
    storage_transfer.start_pull(server["ctx"], tmp_path / "data", a, "img_c0ffee",
                                ("noc", "admin"), run_in_thread=False)
    storage_transfer.start_pull(server["ctx"], tmp_path / "data", b, "img_c0ffe2",
                                ("noc", "admin"), run_in_thread=False)
