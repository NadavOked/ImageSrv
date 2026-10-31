"""Storage Nodes — אינטגרציית ה-mTLS הבין-שרתי על loopback (#740, tracer 2.1).

**שני קצוות TLS אמיתיים**, לא TestClient: ‏``InterserverTLSServer`` (המשני)
מסיים mTLS 1.3 עם pyOpenSSL, ו-``PinnedMTLSClient`` (הראשי) מציג תעודת-לקוח
ומצמיד את ה-SPKI של המשני. כאן נבדקים ה-handshake, ה-channel-binding
(exporter) וקליטת תעודת-הלקוח החתומה-עצמית — הדברים שאי אפשר לזייף ב-
TestClient.

הערת סביבה: הבדיקות רצות מול ``127.0.0.1`` בפורטים אקראיים (ולא מול
כינויי loopback כמו 127.0.0.2/3 שאינם קיימים כברירת מחדל בווינדוס). ה-pin
הוא על ה-SPKI, לא על ה-SAN, ולכן ההפרדה בין הקצוות היא בפורט — התכונה
הביטחונית זהה. ‏**ה-channel-binding רץ כאן גם על 3.12** (pyOpenSSL), ומדולג
רק אם pyOpenSSL באמת חסר (עיקרון 5).
"""

from __future__ import annotations

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server import db
from server.interserver import interserver_api, interserver_auth, storage_client, storage_nodes


pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)


class _Ctx:
    def __init__(self, conn, data_dir):
        self.conn = conn
        self.data_dir = data_dir


@pytest.fixture()
def secondary(tmp_path):
    """שרת משני חי עם מאזין mTLS על 127.0.0.1, וזהות TLS משלו."""
    conn = db.connect(tmp_path / "sec.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    ident = storage_nodes.ensure_identity(conn, tmp_path / "sec",
                                          host_sans=["127.0.0.1"])
    server = interserver_api.InterserverTLSServer(
        _Ctx(conn, str(tmp_path / "sec")), "127.0.0.1", 0,
        ident["cert_pem"], ident["key_pem"], data_dir=str(tmp_path / "sec")).start()
    yield {"conn": conn, "ident": ident, "server": server, "port": server.port}
    server.stop()


@pytest.fixture()
def primary_certs():
    return interserver_auth.generate_self_signed("primary", ip_sans=["127.0.0.1"])


def _client(secondary, primary_certs):
    cert, key = primary_certs
    return storage_client.PinnedMTLSClient(
        "127.0.0.1", secondary["port"],
        expected_secondary_spki=secondary["ident"]["server_spki"],
        cert_pem=cert, key_pem=key)


def test_live_pair_then_ping_happy(secondary, primary_certs):
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        result = client.pair(code=code, primary_id="primary-live")
    token = result["token"]
    assert len(bytes.fromhex(token)) == 32
    assert result["secondary_spki"] == secondary["ident"]["server_spki"]
    # ‏ping על session חדשה (אותה תעודת-לקוח) — sender-constraint מסתדר.
    with _client(secondary, primary_certs) as client:
        pong = client.ping(token)
    assert pong["ok"] is True and pong["role"] == "secondary"
    # המשני קלט את תעודת-הלקוח וכרך אליה את הטוקן.
    cred = secondary["conn"].execute(
        "SELECT bound_cert_ref, pinned_parent_spki FROM parent_credentials"
        " WHERE singleton = 1").fetchone()
    assert cred["bound_cert_ref"] == interserver_auth.certificate_ref(primary_certs[0])
    assert cred["pinned_parent_spki"] == interserver_auth.spki_sha256(primary_certs[0])


def test_live_spki_mismatch_hard_fails_handshake(secondary, primary_certs):
    cert, key = primary_certs
    bad = storage_client.PinnedMTLSClient(
        "127.0.0.1", secondary["port"],
        expected_secondary_spki="00" * 32,          # SPKI שגוי → כשל handshake
        cert_pem=cert, key_pem=key)
    with pytest.raises(storage_client.InterserverClientError):
        bad.open()


def test_live_sender_constraint_stolen_token_other_cert_rejected(secondary, primary_certs):
    """טוקן אמיתי (מ-pairing חוקי) + תעודת-לקוח אחרת על session אמיתית → 401.

    זו ה-sender-constraint (RFC 8705) מעל TLS חי: מי שגנב את הטוקן אך אין לו
    המפתח הפרטי של הראשי — נדחה, כי ה-thumbprint של תעודתו אינו הכרוך."""
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        token = client.pair(code=code, primary_id="primary-live")["token"]
    thief_cert, thief_key = interserver_auth.generate_self_signed(
        "thief", ip_sans=["127.0.0.1"])
    thief = storage_client.PinnedMTLSClient(
        "127.0.0.1", secondary["port"],
        expected_secondary_spki=secondary["ident"]["server_spki"],
        cert_pem=thief_cert, key_pem=thief_key)
    with thief:
        with pytest.raises(storage_client.InterserverClientError) as exc:
            thief.ping(token)
    assert "401" in str(exc.value)


def test_live_channel_binding_begin_a_complete_b_rejected(secondary, primary_certs):
    """‏pair-begin על חיבור A, ואז pair-complete עם אותו handle על חיבור B
    (session אחרת → exporter אחר) → נדחה. זו הכריכה לערוץ (RFC 9266) על TLS חי."""
    code = interserver_auth.open_pairing_window(secondary["conn"])
    a = _client(secondary, primary_certs)
    b = _client(secondary, primary_certs)
    a.open(); b.open()
    try:
        status_a, _, begin = a._request("POST", "/pair-begin", body={
            "primary_id": "p", "protocol_version": interserver_auth.PROTOCOL_VERSION,
            "expected_secondary_spki": secondary["ident"]["server_spki"],
            "client_nonce": "n"})
        assert status_a == 200
        # complete על B עם ה-handle של A → exporter שונה → 400.
        status_b, _, body = b._request("POST", "/pair-complete", body={
            "handle": begin["handle"], "code": code,
            "protocol_version": interserver_auth.PROTOCOL_VERSION})
        assert status_b == 400
    finally:
        a.close(); b.close()
    # לא נרשם אב — ה-complete נדחה.
    assert storage_nodes.has_parent(secondary["conn"]) is False


def test_live_downgrade_below_tls13_rejected(secondary, primary_certs):
    """לקוח שמנסה TLS 1.2 בלבד אינו מצליח handshake מול מאזין TLS-1.3-בלבד."""
    import socket
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from OpenSSL import SSL
    cert, key = primary_certs
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_max_proto_version(SSL.TLS1_2_VERSION)            # תקרה 1.2 → downgrade
    ctx.use_certificate(x509.load_pem_x509_certificate(cert))
    ctx.use_privatekey(load_pem_private_key(key, password=None))
    raw = socket.create_connection(("127.0.0.1", secondary["port"]), timeout=10)
    conn = SSL.Connection(ctx, raw)
    conn.set_connect_state()
    with pytest.raises(SSL.Error):
        conn.do_handshake()
    raw.close()


def test_live_fetch_server_spki_survives_a_slow_server_hello(secondary, monkeypatch):
    """‏#902: ה-preview של ה-SPKI נפל במעבדה (דרך חומת האש של חיפה) עם
    ‏``WantReadError`` — ה-socket נוצר עם timeout ולכן non-blocking ל-pyOpenSSL,
    ו-``do_handshake`` נזרק ברגע שה-ServerHello לא מוכן מיד. ב-``open`` זה כבר
    טופל; ‏``fetch_server_spki`` שכח. כאן מכריחים את המצב: ה-socket מוחזר
    במצב timeout **ומאולץ להיראות "לא מוכן"** בקריאה הראשונה, כמו ברשת
    אמיתית — בלי התיקון נופל ב-``WantReadError``, איתו מחזיר את ה-SPKI."""
    import socket as _socket
    real_create = _socket.create_connection

    class _Slow:
        """עוטף socket אמיתי: הקריאה הראשונה מחזירה EAGAIN כאילו הרשת איטית."""
        def __init__(self, sock):
            self._s = sock
            self._first = True

        def recv(self, *a, **k):
            if self._first and self._s.gettimeout() is not None:
                self._first = False
                raise BlockingIOError()
            return self._s.recv(*a, **k)

        def __getattr__(self, name):
            return getattr(self._s, name)

    def slow_create(addr, timeout=None):
        return _Slow(real_create(addr, timeout=timeout))

    monkeypatch.setattr(storage_client.socket, "create_connection", slow_create)
    spki = storage_client.fetch_server_spki("127.0.0.1", secondary["port"])
    assert spki == secondary["ident"]["server_spki"]


# --- #1122: הערוץ בין-שרתי בפועל — handshake, חידוש תעודה, גוף לא-תואם ------

from test_interserver_enrollment import (   # noqa: E402
    PEER_CERT_CHANGED_DETAIL, renew_cert)


def _handler_threads() -> int:
    import threading
    return sum(1 for t in threading.enumerate() if t.name == "interserver-conn")


def test_live_silent_tcp_connection_is_closed_after_the_handshake_timeout(
        secondary, monkeypatch):
    """‏#1122 (1): חיבור TCP שלא שולח בייט. בלי תקרה, ‏``do_handshake`` חוסם
    לנצח ותהליכון נשאר תקוע לכל סורק — כאן המשני סוגר תוך התקרה, והתהליכון
    משתחרר. הראיה החיובית: ‏recv מחזיר EOF (לא timeout) ומונה התהליכונים
    חוזר לבסיס."""
    import socket
    import time
    monkeypatch.setattr(interserver_api, "HANDSHAKE_TIMEOUT_SECONDS", 0.5, raising=False)
    base = _handler_threads()
    raw = socket.create_connection(("127.0.0.1", secondary["port"]), timeout=10)
    try:
        raw.settimeout(5)                         # כישלון = timeout כאן, לא EOF
        started = time.monotonic()
        try:
            data = raw.recv(1)
        except ConnectionResetError:
            data = b""                            # גם RST הוא "השרת סגר"
        except socket.timeout:
            pytest.fail("המשני לא סגר חיבור שקט תוך 5 שניות — do_handshake חוסם")
        elapsed = time.monotonic() - started
        assert data == b"", "המשני לא סגר חיבור שקט"
        assert elapsed < 4, f"נסגר רק אחרי {elapsed:.1f}s"
    finally:
        raw.close()
    deadline = time.monotonic() + 3
    while _handler_threads() > base and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _handler_threads() == base, "תהליכון ה-handshake לא השתחרר"


def test_live_normal_handshake_is_not_hurt_by_the_timeout(secondary, primary_certs):
    """הצד החיובי של (1): ‏handshake רגיל עדיין עובר, והזרם שאחריו חוסם רגיל
    (ה-timeout הוסר אחרי ה-handshake — ‏ping אחרי המתנה עדיין עונה)."""
    import time
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        token = client.pair(code=code, primary_id="primary-live")["token"]
        time.sleep(0.3)
        assert client.ping(token)["ok"] is True


def test_live_renewed_primary_cert_on_the_same_key_still_pings(secondary, primary_certs):
    """‏#1122 (2ב): הראשי חידש תעודה על אותו מפתח — מעל TLS אמיתי ה-ping
    מצליח, והמשני עדכן את הכריכה לתעודה החדשה עם שורת יומן."""
    cert, key = primary_certs
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        token = client.pair(code=code, primary_id="primary-live")["token"]
    renewed = renew_cert(cert, key)
    with storage_client.PinnedMTLSClient(
            "127.0.0.1", secondary["port"],
            expected_secondary_spki=secondary["ident"]["server_spki"],
            cert_pem=renewed, key_pem=key) as client:
        assert client.ping(token)["ok"] is True
    cred = secondary["conn"].execute(
        "SELECT bound_cert_ref FROM parent_credentials WHERE singleton = 1").fetchone()
    assert cred["bound_cert_ref"] == interserver_auth.certificate_ref(renewed)
    assert secondary["conn"].execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'storage_parent_cert_renewed'"
    ).fetchone()["n"] == 1


def test_live_changed_primary_key_is_named_in_the_response_body(secondary, primary_certs):
    """‏#1122 (2א): מפתח אחר → 401 עם ``code: peer_cert_changed`` והודעה
    בעברית — מה שהקונסולה בראשי מציגה. הסמל עובר את ה-terminator, לא רק
    את שכבת ה-service."""
    code = interserver_auth.open_pairing_window(secondary["conn"])
    with _client(secondary, primary_certs) as client:
        token = client.pair(code=code, primary_id="primary-live")["token"]
    other_cert, other_key = interserver_auth.generate_self_signed(
        "primary-new-key", ip_sans=["127.0.0.1"])
    other = storage_client.PinnedMTLSClient(
        "127.0.0.1", secondary["port"],
        expected_secondary_spki=secondary["ident"]["server_spki"],
        cert_pem=other_cert, key_pem=other_key)
    with other:
        status, _, body = other._request("GET", "/ping", headers={
            "Authorization": f"Bearer {token}"})
        with pytest.raises(storage_client.InterserverClientError) as exc:
            other.ping(token)
    assert status == 401
    assert body == {"detail": PEER_CERT_CHANGED_DETAIL, "code": "peer_cert_changed"}
    assert PEER_CERT_CHANGED_DETAIL in str(exc.value)


class _ClosedConn:
    """חיבור שכבר נסגר בצד השני: ‏recv מחזיר EOF, ‏sendall אוסף."""
    def __init__(self):
        self.sent = b""

    def recv(self, _n):
        return b""

    def sendall(self, data):
        self.sent += data


def _small(secondary, primary_certs, *, length: int, rest: bytes):
    peer = interserver_api.TlsPeer(
        "TLSv1.3", interserver_auth._to_der(primary_certs[0]), b"E" * 32)
    conn = _ClosedConn()
    keep = secondary["server"]._small_request(
        conn, peer, "GET", "/api/interserver/v1/ping",
        {"content-length": str(length)}, rest)
    return keep, conn.sent


def test_small_request_body_shorter_than_declared_is_400_and_closes(secondary, primary_certs):
    """‏#1122 (4): ‏Content-Length: 10, הגיעו 5 והחיבור נסגר → 400, ‏keep=False —
    לא dispatch של חצי גוף."""
    keep, sent = _small(secondary, primary_certs, length=10, rest=b"12345")
    assert keep is False
    assert sent.startswith(b"HTTP/1.1 400 ") and b"5" in sent and b"10" in sent


def test_small_request_bytes_beyond_declared_length_close_the_connection(secondary, primary_certs):
    """שאריות מעבר ל-Content-Length אינן נשארות לבקשה הבאה (desync) —
    הבקשה נענית, אבל החיבור נסגר."""
    keep, sent = _small(secondary, primary_certs, length=2, rest=b"{}trailing")
    assert keep is False
    assert sent.startswith(b"HTTP/1.1 ")


def test_small_request_exact_body_keeps_alive(secondary, primary_certs):
    """הצד החיובי: גוף באורך המוצהר בדיוק — ‏keep-alive נשאר."""
    keep, sent = _small(secondary, primary_certs, length=2, rest=b"{}")
    assert keep is True
    assert sent.startswith(b"HTTP/1.1 ")
