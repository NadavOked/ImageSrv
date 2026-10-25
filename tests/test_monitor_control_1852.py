"""‏#1852 — מכונה אחת, צופה אחד; מנהל שני **מבקש** שליטה (הכרעת נדב, 19/10).

עד כאן: מנהל שני (או "פתח בחלון נפרד" של אותו מנהל, כשה-iframe עוד פתוח) קיבל
‏4409 "כבר פתוח מוניטור" בלי לדעת מי מחזיק, ונוסה שוב 9 דקות — וחטף את המכונה
ברגע שהראשון התנתק לרגע. ההכרעה: הודעה "המכונה בצפייה של <שם>" ואפשרות לבקש
שליטה, כמו ב-VM (#1334): המחזיק מאשר; 60 ש' בלי תשובה = סירוב; מחזיק שלא
נראה 2 דק' — אישור אוטומטי. ואותו מנהל שפותח במקום אחר — הצפייה עוברת.

בקרה שלילית: על הקוד שלפני #1852 אין `…/control` (404), הסיבה אינה נוקבת
בשם, ו"פתח בחלון נפרד" של אותו מנהל נסגר 4409.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import auth, monitor, users
from test_monitor import (MAC, SERVER_INIT, FakeMachine, browser_handshake,
                          prepare_secret_machine)

WS = f"/api/console/monitor/{MAC}"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def admins(server):
    """שני מנהלים (noc, ‏ada) מול אותו נתב, ושעון שהטסט מזיז."""
    prepare_secret_machine(server)
    users.create(server["ctx"].conn, "ada", "admin-pass-456", "admin", by="test",
                 check_policy=False)
    clock = Clock()

    async def connector(_host, _port):
        machine = FakeMachine()
        return machine, machine

    app = FastAPI()
    try:
        router = monitor.create_monitor_router(
            server["ctx"], open_connection=connector, clock=clock)
    except TypeError:
        # the base before #1852 has no clock: the control must fail on behaviour, not on a kwarg
        router = monitor.create_monitor_router(server["ctx"], open_connection=connector)
    app.include_router(router)

    def client(name: str) -> TestClient:
        c = TestClient(app)
        c.cookies.set(auth.COOKIE_NAME, auth.issue(server["ctx"].conn, name, "admin"))
        return c

    return {"noc": client("noc"), "ada": client("ada"), "clock": clock, "server": server}


def _watch(ws) -> None:
    browser_handshake(ws)
    assert ws.receive_bytes() == SERVER_INIT


def _closed(ws) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as caught:
        ws.receive_bytes()
    return caught.value


def test_a_second_admin_is_told_who_holds_the_machine(admins):
    """לא "כבר פתוח" עלום: 4409 עם שם המחזיק, ו-`…/control` אומר את אותו דבר."""
    with admins["noc"].websocket_connect(WS) as held:
        _watch(held)
        with admins["ada"].websocket_connect(WS) as second:
            refused = _closed(second)
        assert refused.code == 4409
        assert refused.reason == "המכונה בצפייה של noc"
        state = admins["ada"].get(f"{WS}/control").json()
        assert state["holder"] == "noc" and state["you_hold"] is False


def test_open_in_a_separate_window_moves_the_view_instead_of_4409(admins):
    """אותו מנהל פותח את המכונה במקום אחר (החלון הנפרד, כשה-iframe עוד פתוח):
    החלון מקבל את המסך, וה-iframe נסגר 4410 — קוד שאינו מנוסה שוב, אחרת
    השניים היו מעבירים את המכונה הלוך ושוב."""
    with admins["noc"].websocket_connect(WS) as frame:
        _watch(frame)
        with admins["noc"].websocket_connect(WS) as window:
            _watch(window)
            moved = _closed(frame)
            assert moved.code == monitor.WS_MOVED
            assert moved.reason == "המוניטור נפתח בחלון אחר"


def test_a_request_is_approved_by_the_holder_and_the_machine_waits_for_the_requester(admins):
    noc, ada = admins["noc"], admins["ada"]
    with noc.websocket_connect(WS) as held:
        _watch(held)
        asked = ada.post(f"{WS}/control/request").json()
        assert asked == {"outcome": "pending", "seconds_left": 60}
        seen = noc.get(f"{WS}/control").json()
        assert seen["you_hold"] is True and seen["request"]["by"] == "ada"
        assert noc.post(f"{WS}/control/answer", json={"approve": True}).json() == {
            "outcome": "approved"}
        handed = _closed(held)
    assert handed.code == monitor.WS_HANDED_OVER
    assert handed.reason == "השליטה במכונה הועברה ל-ada"
    assert ada.get(f"{WS}/control").json()["granted_to_you"] is True
    # השמירה: המחזיק הקודם אינו חוטף בחזרה לפני שהמבקש הגיע.
    with noc.websocket_connect(WS) as again:
        assert _closed(again).code == 4409
    with ada.websocket_connect(WS) as mine:
        _watch(mine)


def test_sixty_seconds_without_an_answer_is_a_refusal(admins):
    noc, ada, clock = admins["noc"], admins["ada"], admins["clock"]
    with noc.websocket_connect(WS) as held:
        _watch(held)
        ada.post(f"{WS}/control/request")
        clock.now += 59
        noc.get(f"{WS}/control")                       # המחזיק נוכח — לא אוטומטי
        assert ada.get(f"{WS}/control").json()["request"]["seconds_left"] == 1
        clock.now += 1
        state = ada.get(f"{WS}/control").json()
        assert state["request"] is None and state["holder"] == "noc"
        assert state["last"] == {"by": "ada", "outcome": "timeout"}
        assert noc.post(f"{WS}/control/answer", json={"approve": True}).status_code == 409


def test_a_holder_not_seen_for_two_minutes_hands_over_automatically(admins):
    noc, ada, clock = admins["noc"], admins["ada"], admins["clock"]
    with noc.websocket_connect(WS) as held:
        _watch(held)
        clock.now += 119
        assert ada.post(f"{WS}/control/request").json()["outcome"] == "pending"
        ada.post(f"{WS}/control/answer", json={"approve": False})   # לא שלו — 403
        clock.now += 60                                 # הבקשה פגה; המחזיק עדיין לא נראה
        assert ada.post(f"{WS}/control/request").json() == {"outcome": "auto"}
        assert _closed(held).code == monitor.WS_HANDED_OVER
    with ada.websocket_connect(WS) as mine:
        _watch(mine)


def test_a_refusal_keeps_the_holder_and_says_so(admins):
    noc, ada = admins["noc"], admins["ada"]
    with noc.websocket_connect(WS) as held:
        _watch(held)
        ada.post(f"{WS}/control/request")
        assert noc.post(f"{WS}/control/answer", json={"approve": False}).json() == {
            "outcome": "denied"}
        assert ada.get(f"{WS}/control").json()["last"] == {"by": "ada", "outcome": "denied"}
        held.send_bytes(b"\x03\x01\x00\x00\x00\x00\x04\x00\x03\x00")  # הגשר עדיין חי


def test_only_the_holder_sends_a_power_command(admins):
    noc, ada = admins["noc"], admins["ada"]
    with noc.websocket_connect(WS) as held:
        _watch(held)
        refused = ada.post(f"{WS}/power", json={"action": "reboot", "confirm": "Monitor"})
        assert refused.status_code == 409 and "בצפייה של noc" in refused.json()["detail"]
        assert noc.post(f"{WS}/power", json={"action": "reboot", "confirm": "Monitor"}
                        ).status_code == 200
