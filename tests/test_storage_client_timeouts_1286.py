"""‏#1286 — משני שמקבל TCP ואז נתקע אינו חוסם את הראשי בלי תקרה.

‏``PinnedMTLSClient.open`` הוריד את ה-timeout אחרי ה-connect
(``settimeout(None)``), ולכן handshake שלא מגיע, או תשובה שלא מגיעה אחרי
בקשה, חסמו את ה-relay לנצח. כאן "משני" מזויף על loopback — TCP אמיתי
ו-TLS 1.3 אמיתי (pyOpenSSL) — שמתנהג בכל פעם בצורה אחרת:

* מקבל TCP ולא שולח בייט (handshake תקוע);
* משלים handshake, קורא בקשה ולא עונה (relay תקוע);
* בזרם: נעצר באמצע הגוף — מול זרם איטי שמתקדם, שאסור לחתוך;
* ב-PUT: שותק אחרי הגוף (אימות sha256) — שאסור לחתוך לפי תקרת המנה.

כל קריאת לקוח רצה בתהליכון עם join מוגבל: בקוד הישן הטסט **נכשל** ולא
נתקע, והסגירה של המשני בסוף משחררת את התהליכון.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי")

from server.interserver import interserver_auth, storage_client  # noqa: E402

#: תקרת ה-join — הרבה מעל תקרות הבדיקה (0.5ש'), הרבה מתחת ל"לנצח".
JOIN = 6.0


class _FakeSecondary:
    """מאזין על 127.0.0.1 שמריץ ``behave(ssl_conn | raw, stop)`` לחיבור אחד."""

    def __init__(self, behave, *, tls: bool = True):
        self.cert, self.key = interserver_auth.generate_self_signed(
            "fake-secondary", ip_sans=["127.0.0.1"])
        self.spki = interserver_auth.spki_sha256(self.cert)
        self.stop = threading.Event()
        self._behave = behave
        self._tls = tls
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._conns: list = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            raw, _ = self._sock.accept()
        except OSError:
            return
        self._conns.append(raw)
        if not self._tls:
            self._behave(raw, self.stop)
            return
        from OpenSSL import SSL
        conn = SSL.Connection(
            interserver_auth.build_server_tls_context(self.cert, self.key), raw)
        conn.set_accept_state()
        try:
            conn.do_handshake()
            self._behave(conn, self.stop)
        except Exception:                                    # noqa: BLE001
            pass

    def close(self) -> None:
        self.stop.set()
        for raw in self._conns:
            try:
                raw.close()
            except OSError:
                pass
        self._sock.close()


def _read_request(conn) -> bytes:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _client(sec: _FakeSecondary, **kw) -> storage_client.PinnedMTLSClient:
    cert, key = interserver_auth.generate_self_signed("primary", ip_sans=["127.0.0.1"])
    return storage_client.PinnedMTLSClient(
        "127.0.0.1", sec.port, expected_secondary_spki=sec.spki,
        cert_pem=cert, key_pem=key, **kw)


def _run_bounded(fn):
    """מריץ ``fn`` בתהליכון. מחזיר ``(ערך, חריגה, שניות)``; תקיעה = כשל הטסט."""
    out: dict = {}

    def target():
        started = time.monotonic()
        try:
            out["value"] = fn()
        except BaseException as exc:                         # noqa: BLE001
            out["error"] = exc
        out["elapsed"] = time.monotonic() - started

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(JOIN)
    if worker.is_alive():
        pytest.fail(f"הלקוח נתקע מעל {JOIN} שניות מול משני שאינו עונה — אין תקרה")
    return out.get("value"), out.get("error"), out["elapsed"]


@pytest.fixture()
def fast(monkeypatch):
    monkeypatch.setattr(storage_client, "REQUEST_TIMEOUT", 0.5, raising=False)
    monkeypatch.setattr(storage_client, "STREAM_IDLE_TIMEOUT", 0.5, raising=False)


@pytest.fixture()
def secondaries():
    made: list[_FakeSecondary] = []

    def make(behave, **kw):
        sec = _FakeSecondary(behave, **kw)
        made.append(sec)
        return sec

    yield make
    for sec in made:
        sec.close()


def _hold(_conn, stop):
    stop.wait(30)


def _read_then_hold(conn, stop):
    _read_request(conn)
    stop.wait(30)


# --- בקשות רגילות (relay/קונסולה): תקרה כוללת ---------------------------------

def test_tcp_accepted_but_no_tls_fails_open_within_the_connect_timeout(secondaries):
    sec = secondaries(_hold, tls=False)
    client = _client(sec, timeout=0.5)
    _, error, elapsed = _run_bounded(client.open)
    assert isinstance(error, storage_client.InterserverTimeout), error
    assert "handshake" in str(error)
    assert elapsed < 3


def test_secondary_that_reads_the_request_and_never_answers_fails_in_name(
        secondaries, fast):
    sec = secondaries(_read_then_hold)
    client = _client(sec)

    def ask():
        with client:
            return client.get_answer("/console/state", "t" * 64)

    _, error, elapsed = _run_bounded(ask)
    assert isinstance(error, storage_client.InterserverTimeout), error
    assert isinstance(error, storage_client.InterserverClientError)   # → 502 בקונסולה
    assert "0.5" in str(error)
    assert elapsed < 3


def test_a_prompt_answer_is_unaffected_by_the_timeout(secondaries, fast):
    def answer(conn, _stop):
        _read_request(conn)
        body = b'{"ok": true}'
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)

    sec = secondaries(answer)
    client = _client(sec)

    def ask():
        with client:
            return client.get_answer("/console/state", "t" * 64)

    value, error, _ = _run_bounded(ask)
    assert error is None, error
    assert value == (200, {"ok": True})


# --- זרם: תקרה למנה, לא תקרה כוללת --------------------------------------------

def _stream(total: int, pieces: int, delay: float, *, stall_after: int | None = None):
    def behave(conn, stop):
        _read_request(conn)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % total)
        piece = total // pieces
        for i in range(pieces):
            if stall_after is not None and i == stall_after:
                stop.wait(30)
                return
            time.sleep(delay)
            conn.sendall(b"x" * piece)
    return behave


def test_stream_that_stops_mid_body_fails_with_the_byte_count(secondaries, fast, tmp_path):
    sec = secondaries(_stream(4000, 4, 0.0, stall_after=2))
    client = _client(sec)
    dest = tmp_path / "part.zst"

    def pull():
        with client:
            return client.get_stream("/images/x/files/p1", "t" * 64, dest)

    _, error, elapsed = _run_bounded(pull)
    assert isinstance(error, storage_client.InterserverTimeout), error
    assert "2000" in str(error)
    assert elapsed < 3


def test_slow_stream_that_keeps_moving_is_not_cut(secondaries, fast, tmp_path):
    """שש מנות, כל אחת 0.3ש' — סה"כ 1.8ש', פי 3.6 מתקרת המנה (0.5ש')."""
    sec = secondaries(_stream(6000, 6, 0.3))
    client = _client(sec)
    dest = tmp_path / "part.zst"

    def pull():
        with client:
            return client.get_stream("/images/x/files/p1", "t" * 64, dest)

    value, error, elapsed = _run_bounded(pull)
    assert error is None, error
    assert value == 6000 and dest.stat().st_size == 6000
    assert elapsed > 1.5


def _put(verify_seconds: float):
    def behave(conn, stop):
        head = _read_request(conn)
        length = int(head.lower().split(b"content-length:")[1].split(b"\r\n")[0])
        conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        got = len(head.partition(b"\r\n\r\n")[2])
        while got < length:
            got += len(conn.recv(65536))
        if stop.wait(verify_seconds):
            return
        body = b'{"ok": true, "id": "img"}'
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
    return behave


def test_put_waits_through_a_verify_longer_than_the_chunk_timeout(
        secondaries, fast, monkeypatch):
    """המשני מחלץ ומאמת sha256 אחרי הגוף בלי לשלוח בייט — זה לא "תקוע"."""
    monkeypatch.setattr(storage_client, "VERIFY_MIN_RATE", 1000, raising=False)   # 3000B → 3ש'
    sec = secondaries(_put(1.2))
    client = _client(sec)

    def push():
        with client:
            return client.put_stream("/images/img", "t" * 64, [b"y" * 3000], 3000)

    value, error, _ = _run_bounded(push)
    assert error is None, error
    assert value == {"ok": True, "id": "img"}


def test_put_whose_verify_never_answers_fails_at_the_ceiling(
        secondaries, fast, monkeypatch):
    monkeypatch.setattr(storage_client, "VERIFY_MIN_RATE", 3000, raising=False)   # 3000B → 1ש'
    sec = secondaries(_put(30))
    client = _client(sec)

    def push():
        with client:
            return client.put_stream("/images/img", "t" * 64, [b"y" * 3000], 3000)

    _, error, elapsed = _run_bounded(push)
    assert isinstance(error, storage_client.InterserverTimeout), error
    assert elapsed < 4


# --- #1292: ה-preview של ה-SPKI בהוספת משני — אותו מוקש, אותה תקרה ---------------

def test_fetch_server_spki_fails_in_name_when_the_secondary_accepts_and_stays_silent(
        secondaries):
    """‏``fetch_server_spki`` הוריד את ה-timeout אחרי ה-connect כמו ``open``
    לפני #1286 — משני שמקבל TCP ולא שולח ServerHello תקע את ה-preview לנצח."""
    sec = secondaries(_hold, tls=False)
    _, error, elapsed = _run_bounded(
        lambda: storage_client.fetch_server_spki("127.0.0.1", sec.port, timeout=0.5))
    assert isinstance(error, storage_client.InterserverTimeout), error
    assert "handshake" in str(error)
    assert elapsed < 3


def test_fetch_server_spki_still_reads_a_prompt_secondary(secondaries):
    sec = secondaries(_hold)
    value, error, _ = _run_bounded(
        lambda: storage_client.fetch_server_spki("127.0.0.1", sec.port, timeout=0.5))
    assert error is None, error
    assert value == sec.spki
