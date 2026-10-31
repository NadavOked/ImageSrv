"""‏#1850 — מכונה שנעלמה בלי FIN אינה "מחוברת" עם מסך קפוא 10 דקות.

משכפל 2, ‏18/10: כיבוי idle (‏`poweroff -f`) ב-17:52:55, והגשר נסגר `idle` רק
ב-18:03:25 — עשר וחצי דקות שבהן המסך הציג אותו מחובר. בלי keepalive, הקורא
מהמכונה ממתין ל-EOF שלא יגיע. התיקון: TCP keepalive על ה-socket אל המכונה
(10 ש' שקט, 3 בדיקות כל 5 ש'), כאן ובמנהרה מהמשני (`test_storage_remote`).

ה-peer המת עצמו אינו ניתן לשחזור בלי לשלוט ברשת (הקרנל המקומי עונה תמיד על
loopback). מה שנבדק: (א) ה-socket האמיתי אל "המכונה" נושא את ההגדרות — קריאה
בחזרה מהקרנל; (ב) כשל keepalive — ‏`TimeoutError` מהקורא — נסגר 4513, והדפדפן
מתחבר מחדש (‏`monitor_power_1850.test.cjs`). בקרה שלילית: (א) נכשל על הקוד
שלפני #1850 (SO_KEEPALIVE = 0); (ב) עובר בשני המצבים — שומר על המיפוי.
"""

from __future__ import annotations

import asyncio
import errno
import socket
import struct
import threading

import pytest

pytest.importorskip("fastapi")

from starlette.websockets import WebSocketDisconnect

from server.console import monitor
from test_monitor import (CHALLENGE, MAC, SERVER_INIT, FakeMachine, browser_handshake,
                          monitor_client, prepare_secret_machine)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        piece = sock.recv(n - len(data))
        if not piece:
            raise ConnectionError("closed")
        data += piece
    return data


class TcpMonitor(threading.Thread):
    """מוניטור מזויף על TCP אמיתי: לחיצת-יד 3.8 עם סוג 2, ServerInit, ואז שקט."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.done = threading.Event()

    def run(self) -> None:
        self.sock.settimeout(20)
        conn, _ = self.sock.accept()
        with conn:
            conn.sendall(b"RFB 003.008\n")
            _recv_exact(conn, 12)
            conn.sendall(b"\x01\x02")
            _recv_exact(conn, 1)
            conn.sendall(CHALLENGE)
            _recv_exact(conn, 16)
            conn.sendall(struct.pack(">I", 0))
            _recv_exact(conn, 1)                       # ClientInit
            conn.sendall(SERVER_INIT)
            self.done.wait(20)


def test_the_socket_to_the_machine_carries_tcp_keepalive(server):
    """(א) הראיה מהקרנל: SO_KEEPALIVE דלוק, ו-10/5/3 שניות-בדיקות היכן שהמערכת
    מאפשרת לקבוע אותם — כלומר מכונה מתה נסגרת תוך ~25 ש', לא `idle` אחרי 600."""
    prepare_secret_machine(server)
    fake = TcpMonitor()
    fake.start()
    writers: list = []

    async def connector(host, _port):
        reader, writer = await asyncio.open_connection("127.0.0.1", fake.port)
        writers.append(writer)
        return reader, writer

    client = monitor_client(server, "admin", connector)
    try:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
            browser_handshake(websocket)
            assert websocket.receive_bytes() == SERVER_INIT
            sock = writers[0].get_extra_info("socket")
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
            for name, value in (("TCP_KEEPIDLE", 10), ("TCP_KEEPINTVL", 5),
                                ("TCP_KEEPCNT", 3)):
                option = getattr(socket, name, None)
                if option is not None:
                    assert sock.getsockopt(socket.IPPROTO_TCP, option) == value, name
    finally:
        fake.done.set()


class KeepaliveFailedReader(FakeMachine):
    """אחרי לחיצת-היד, ה-keepalive נכשל: הקריאה הבאה זורקת ETIMEDOUT — מה
    שהקרנל מחזיר על peer שלא ענה לשלוש בדיקות."""

    async def read(self, n: int) -> bytes:
        if b"\x01" in self.writes[3:]:               # הדפדפן כבר שלח ClientInit
            if self._pending:
                return await super().read(n)
            raise TimeoutError(errno.ETIMEDOUT, "Connection timed out")
        return await super().read(n)


def test_a_keepalive_failure_closes_4513_not_idle(server):
    """(ב) המיפוי: כשל keepalive הוא "החיבור למחשב נותק" (4513) — קוד שה-
    monitor.js מתחבר אחריו מחדש — ולא 4408 בלי ניסיון חוזר."""
    prepare_secret_machine(server)
    machine = KeepaliveFailedReader()

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()
    assert caught.value.code == monitor.WS_MACHINE_GONE


def test_keep_alive_sets_what_it_says():
    """יחידה: ‏`keep_alive` על socket רגיל — אותה קריאה-בחזרה, בלי שרת."""
    with socket.socket() as sock:
        monitor.keep_alive(sock)
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) != 0
        idle = getattr(socket, "TCP_KEEPIDLE", None)
        if idle is not None:
            assert sock.getsockopt(socket.IPPROTO_TCP, idle) == monitor.KEEPALIVE_IDLE_SECONDS
    assert (monitor.KEEPALIVE_IDLE_SECONDS
            + monitor.KEEPALIVE_INTERVAL_SECONDS * monitor.KEEPALIVE_PROBES) <= 30
