"""#3180: כל המתנה בין-שרתית עובדת גם על socket שמספר ה-fd שלו מעל 1024.

``select.select`` זורק ``ValueError: filedescriptor out of range in select()``
על fd מעל FD_SETSIZE (1024). ב-CI הציבורי של v0.95.0 ה-workers של 3.13 (תחת
כיסוי) עברו את 1024, ו-704 טסטים בין-שרתיים נפלו על זה ב-``_TimedConn._wait``.
שרת עמוס יכול להגיע לשם באותה דרך. כאן כל אחד מחמשת אתרי ההמתנה מקבל
socket עם fd גבוה, ובודקים שהוא ממתין, מתעורר ופג בזמן — ולא זורק.

לינוקס/POSIX בלבד: ``resource`` ו-``F_DUPFD`` אינם בווינדוס, ושם ``select``
מוגבל במספר ה-sockets ולא במספר ה-fd.
"""

import socket
import threading

import pytest

resource = pytest.importorskip("resource")
fcntl = pytest.importorskip("fcntl")

from OpenSSL import SSL  # noqa: E402

from server.interserver import api, storage_client  # noqa: E402

#: מעל FD_SETSIZE של glibc (1024) — שם ``select.select`` נשבר.
HIGH = 1500


@pytest.fixture
def high_pair():
    """זוג sockets מחוברים; ``high`` יושב על fd >= 1500, ``peer`` הוא הצד השני."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    want = HIGH + 100
    # עיקרון 5: אם אי אפשר להגיע ל-fd גבוה, הטסט נכשל — לא מדלג ונראה ירוק.
    assert hard == resource.RLIM_INFINITY or hard >= want, (
        f"RLIMIT_NOFILE hard={hard} — אי אפשר לפתוח fd מעל {HIGH}, הטסט לא יבדוק כלום")
    if soft != resource.RLIM_INFINITY and soft < want:
        resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    a, peer = socket.socketpair()
    fd = fcntl.fcntl(a.fileno(), fcntl.F_DUPFD_CLOEXEC, HIGH)
    a.close()
    high = socket.socket(fileno=fd)
    try:
        assert high.fileno() >= HIGH
        yield high, peer
    finally:
        high.close()
        peer.close()
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def test_storage_client_wait_readable_on_high_fd(high_pair):
    high, peer = high_pair
    peer.sendall(b"x")
    conn = storage_client._TimedConn(None, high)
    conn.limit(5.0, total=True, waiting_for="test")
    conn._wait([high], [])      # מוכן לקריאה — חוזר בלי חריגה
    conn._wait([], [high])      # מוכן לכתיבה — חוזר בלי חריגה


def test_storage_client_wait_still_times_out_on_high_fd(high_pair):
    """התקרה נשמרת: בלי בייט מהמשני — ``InterserverTimeout``, לא ValueError."""
    high, _peer = high_pair
    conn = storage_client._TimedConn(None, high)
    conn.limit(0.2, total=True, waiting_for="test")
    with pytest.raises(storage_client.InterserverTimeout):
        conn._wait([high], [])


class _WantReadOnce:
    """חיבור pyOpenSSL מדומה: WantRead בפעם הראשונה, ואחריה הצלחה."""

    def __init__(self, result=None):
        self.calls = 0
        self.result = result

    def _step(self, *_args):
        self.calls += 1
        if self.calls == 1:
            raise SSL.WantReadError()
        return self.result

    do_handshake = _step
    recv = _step


def test_handshake_within_on_high_fd(high_pair):
    high, peer = high_pair
    peer.sendall(b"x")
    conn = _WantReadOnce()
    assert api._handshake_within(conn, high, 5.0) is True
    assert conn.calls == 2


def test_timed_recv_on_high_fd(high_pair):
    high, peer = high_pair
    peer.sendall(b"x")
    reader = api._TimedRecv(_WantReadOnce(b"data"), high, 5.0)
    assert reader.recv(4) == b"data"
    assert reader.timed_out is False


class _PlainTLS:
    """צד ה-TLS של המנהרה, בלי TLS: recv/send ישר על ה-socket."""

    def __init__(self, sock):
        self.sock = sock

    def pending(self):
        return 0

    def recv(self, n):
        return self.sock.recv(n)

    def send(self, data):
        return self.sock.send(data)


def test_relay_tunnel_on_high_fd(high_pair):
    high, peer = high_pair
    machine, machine_peer = socket.socketpair()
    result = {}
    worker = threading.Thread(target=lambda: result.update(
        reason=api.relay_tunnel(_PlainTLS(high), high, machine, idle_seconds=5.0)))
    try:
        worker.start()
        peer.sendall(b"hello")
        machine_peer.settimeout(5.0)
        assert machine_peer.recv(16) == b"hello"
        peer.shutdown(socket.SHUT_WR)
        worker.join(10.0)
        assert not worker.is_alive()
        assert result["reason"] == "primary closed"
    finally:
        machine.close()
        machine_peer.close()
