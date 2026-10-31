"""‏#1860 — ביטול ה-endpoint בזמן `wait_closed` אינו משאיר את המכונה "כבר פתוח".

ה-`finally` של ה-WebSocket סגר את ה-TCP למכונה (`await writer.wait_closed()`)
ורק **אחריו** שחרר את המנעול (`release`). ה-runner של ASGI מבטל את משימת
ה-endpoint בפירוק החיבור (#725), וביטול שנפל בתוך `wait_closed` דילג על
השחרור: כל חיבור הבא למכונה נסגר 4409 "כבר פתוח מוניטור למכונה הזאת" — עד
אתחול השרת. מול `FakeMachine` (‏`wait_closed` שחוזר מיד) זה לא נראה; מול
המוניטור האמיתי זה נמדד 5 מתוך 15 ריצות (Testrunner, ‏20/10).

כאן `wait_closed` נתקע עד ביטול — הצורה הדטרמיניסטית של אותו מרוץ.
בקרה שלילית: על הקוד שלפני התיקון החיבור השני נסגר 4409.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")

from starlette.websockets import WebSocketDisconnect

from server.console import auth, users
from test_monitor import (MAC, SERVER_INIT, FakeMachine, browser_handshake,
                          monitor_client, prepare_secret_machine)


def _another_admin(server, client):
    """‏#1852: אותו מנהל שפותח שוב **מעביר** את הצפייה — ולכן החיבור השני כאן
    הוא של מנהל אחר: רק מנעול ששוחרר מכניס אותו."""
    users.create(server["ctx"].conn, "ada", "admin-pass-456", "admin", by="test",
                 check_policy=False)
    client.cookies.set(auth.COOKIE_NAME, auth.issue(server["ctx"].conn, "ada", "admin"))


class SlowToCloseMachine(FakeMachine):
    """מכונה שה-TCP אליה נסגר לאט: `wait_closed` ממתין עד שמבטלים אותו."""

    async def wait_closed(self) -> None:
        await asyncio.Event().wait()


def test_a_cancel_while_closing_the_machine_socket_still_releases_it(server):
    prepare_secret_machine(server)
    machines: list[FakeMachine] = []

    async def connector(_host, _port):
        machine = SlowToCloseMachine() if not machines else FakeMachine()
        machines.append(machine)
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
    assert machines[0].closed is True

    _another_admin(server, client)
    try:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
            browser_handshake(websocket)
            assert websocket.receive_bytes() == SERVER_INIT
    except WebSocketDisconnect as exc:
        raise AssertionError(f"second connection closed {exc.code}: {exc.reason}") from None
    assert len(machines) == 2


class ResetOnCloseMachine(FakeMachine):
    """המכונה נעלמה (#1850): סגירת ה-socket אליה נכשלת ב-ConnectionResetError."""

    async def wait_closed(self) -> None:
        raise ConnectionResetError(104, "Connection reset by peer")


def test_a_failed_close_of_a_vanished_machine_still_releases_it(server):
    """סקירת אבטחה, 20/10: ‏`wait_closed` שזורק (בדיוק כשהמכונה נעלמה) דילג
    על `release` — וגם הפיל את ה-endpoint בחריגה. אחריו: חיבור חדש לאותה
    מכונה מתקבל, בלי 4409, ובלי שהחריגה יוצאת מה-endpoint."""
    prepare_secret_machine(server)
    machines: list[FakeMachine] = []

    async def connector(_host, _port):
        machine = ResetOnCloseMachine() if not machines else FakeMachine()
        machines.append(machine)
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
    assert machines[0].closed is True

    _another_admin(server, client)
    try:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
            browser_handshake(websocket)
            assert websocket.receive_bytes() == SERVER_INIT
    except WebSocketDisconnect as exc:
        raise AssertionError(f"second connection closed {exc.code}: {exc.reason}") from None
    assert len(machines) == 2
