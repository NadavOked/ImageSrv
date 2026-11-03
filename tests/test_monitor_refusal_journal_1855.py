"""‏#1855 — כשל התחברות למוניטור משאיר שורה ביומן, עם הקוד והסיבה, בצבירה.

עד כאן רק `monitor_connected` נרשם: 4404/4409/4502/4512 נסגרו בלי עקבות בשרת
(#1837 — ‏bad HMAC, "6 פעמים ברצף" — נמצא ביומן הסוכן במכונה), ו-`monitor_closed
… refused` לא אמר מה נדחה. הדפדפן מנסה שוב עד 20 פעמים, ולכן סדרה של אותו כשל
היא שורה אחת, והספירה נכתבת בשורה הבאה או כשהחיבור הצליח.

בקרה שלילית: על הבסיס אין `monitor_refused` ביומן כלל, ו-refused נרשם בלי סיבה.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.console import auth, monitor, users
from server.console.journal_he import EVENTS_HE, JournalTranslator
from server.db import net_seen
from test_monitor import (CHALLENGE, IP, KNOWN_HMAC, MAC, SECRET, SERVER_INIT, FakeMachine,
                          browser_handshake, journal_rows, prepare_secret_machine)

WS = f"/api/console/monitor/{MAC}"
REJECTED = monitor.MACHINE_REJECTED_SECRET


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _client(server, machines, clock=None, name="noc"):
    """מוניטור עם מכונה מזויפת חדשה לכל חיבור (כל WebSocket ב-TestClient ב-loop משלו);
    ‏``machines()`` מחזיר את הבאה."""
    async def connector(_host, _port):
        machine = machines()
        return machine, machine

    app = FastAPI()
    kwargs = {"clock": clock} if clock is not None else {}
    app.include_router(monitor.create_monitor_router(server["ctx"], open_connection=connector,
                                                     **kwargs))
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, auth.issue(server["ctx"].conn, name, "admin"))
    return client


def _closed(client, path=WS) -> tuple[int, str]:
    with client.websocket_connect(path) as ws:
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
    return caught.value.code, caught.value.reason


def test_a_machine_that_rejects_the_secret_leaves_a_journal_row_with_4512_and_the_reason(server):
    """הגדרת "גמור" 3: סוד שגוי → ‏4512 לדפדפן **ושורת יומן** עם הקוד והסיבה —
    בלי ה-challenge ובלי התשובה (#839)."""
    prepare_secret_machine(server)
    seen = []

    def machines():
        seen.append(FakeMachine(result=1))
        return seen[-1]

    assert _closed(_client(server, machines)) == (monitor.WS_MACHINE_AUTH, REJECTED)
    assert journal_rows(server, "monitor_refused") == [(f"{MAC} 4512 {REJECTED}", "noc")]
    detail = journal_rows(server, "monitor_refused")[0][0]
    for secret in (CHALLENGE.hex(), KNOWN_HMAC.hex(), seen[0].response.hex()):
        assert secret not in detail


def test_a_series_of_the_same_failure_is_one_row_and_the_count_comes_later(server):
    """הדפדפן מנסה שוב (2/4/8/15/30 ש'): חמישה כשלים זהים תוך החלון = שורה אחת. אחרי
    החלון — שורה חדשה שנושאת את מה שנספר; והצלחה כותבת את מה שנספר מאז."""
    prepare_secret_machine(server)
    clock = Clock()
    answer = {"result": 1}
    client = _client(server, lambda: FakeMachine(**answer), clock)
    for _ in range(5):
        assert _closed(client)[0] == monitor.WS_MACHINE_AUTH
        clock.now += 30
    assert journal_rows(server, "monitor_refused") == [(f"{MAC} 4512 {REJECTED}", "noc")]
    clock.now += monitor.REFUSAL_WINDOW_SECONDS
    assert _closed(client)[0] == monitor.WS_MACHINE_AUTH
    assert journal_rows(server, "monitor_refused")[-1] == (f"{MAC} 4512 {REJECTED} ×4 נוספים", "noc")
    for _ in range(2):
        _closed(client)                                  # עוד שניים, נספרים
    answer["result"] = 0
    with client.websocket_connect(WS) as ws:
        browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT
    rows = journal_rows(server, "monitor_refused")
    assert rows == [(f"{MAC} 4512 {REJECTED}", "noc"),
                    (f"{MAC} 4512 {REJECTED} ×4 נוספים", "noc"),
                    (f"{MAC} 4512 {REJECTED} ×2 נוספים", "noc")]
    assert journal_rows(server, "monitor_connected") == [(MAC, "noc")]


def test_each_failure_before_the_bridge_is_journaled_by_code(server):
    """‏4404 (MAC שאינו MAC), ‏4512 (סוכן ישן בלי סוד), ‏4409 "המכונה בצפייה של …"."""
    conn = server["ctx"].conn
    machine_factory = FakeMachine
    users.create(conn, "ada", "admin-pass-456", "admin", by="test", check_policy=False)
    noc = _client(server, lambda: machine_factory())
    assert _closed(noc, "/api/console/monitor/not-a-mac")[0] == 4404
    prepare_secret_machine(server, secret=None)
    assert _closed(noc)[0] == monitor.WS_MACHINE_AUTH
    net_seen(conn, MAC, IP, monitor_secret=SECRET, monitor_auth="hmac")   # הסוכן דיווח סוד
    ada = TestClient(noc.app)                            # אותו נתב — אותו מנעול "אחד לכל מכונה"
    ada.cookies.set(auth.COOKIE_NAME, auth.issue(conn, "ada", "admin"))
    with noc.websocket_connect(WS) as held:
        browser_handshake(held)
        assert held.receive_bytes() == SERVER_INIT
        assert _closed(ada) == (4409, "המכונה בצפייה של noc")
    assert journal_rows(server, "monitor_refused") == [
        ("not-a-mac 4404 מכונה לא מוכרת", "noc"),
        (f"{MAC} 4512 המכונה לא דיווחה סוד מוניטור — סוכן ישן?", "noc"),
        (f"{MAC} 4409 המכונה בצפייה של noc", "ada"),
    ]


def test_refused_inside_the_bridge_carries_what_was_refused(server):
    """הגדרת "גמור" 2: ‏`monitor_closed … refused` עם הסיבה — כאן סוג הודעה לא מוכר, ואיזה."""
    prepare_secret_machine(server)
    client = _client(server, FakeMachine)
    with client.websocket_connect(WS) as ws:
        browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT
        ws.send_bytes(b"\x07\x00\x00\x00")
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_bytes()
    assert caught.value.code == 1002
    assert journal_rows(server, "monitor_closed") == [
        (f"{MAC} refused — הודעת RFB לא מוכרת: סוג 7", "noc")]


def test_a_refusal_through_a_secondary_is_journaled_with_the_node(server):
    """`/storage-nodes/{nid}/monitor/{mac}` — אותו יומן, עם ‏`node=`."""
    code, reason = _closed(server["admin"], f"/api/console/storage-nodes/n0de/monitor/{MAC}")
    assert (code, reason) == (4404, "שרת משנה לא קיים")
    assert journal_rows(server, "monitor_refused") == [
        (f"{MAC} 4404 שרת משנה לא קיים node=n0de", "noc")]


def test_the_new_rows_are_translated_to_hebrew(server):
    from test_monitor import prepare_build_machine
    prepare_build_machine(server)
    tr = JournalTranslator(server["ctx"].conn, server["ctx"].library)
    who = tr._machines[MAC]
    assert tr.translate("monitor_refused", f"{MAC} 4512 {REJECTED}") == (
        EVENTS_HE["monitor_refused"], f"{who} · 4512 · {REJECTED}")
    assert tr.translate("monitor_closed", f"{MAC} refused — סוג אבטחה לא צפוי")[1] == (
        f"{who} · נסגר — הודעה אסורה מהדפדפן · סוג אבטחה לא צפוי")
