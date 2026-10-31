"""‏#1839 — משכפל שכותב נשאר "מחובר" ופתוח למוניטור.

אותו ערב (19/10, משכפל 2): ‏`last_seen` התעדכן רק מ-hello, ובזמן כתיבה
הסוכן שולח רק progress כל 2ש'. אחרי 90ש' המשכפל הכותב הוצג "לא מחובר"
והמוניטור סירב לו (4409). התיקון: דיווח שהתקבל נרשם ב-`progress_seen`,
ו"מחובר" של המוניטור קורא את שתי החותמות. עמודה נפרדת ולא `last_seen`:
‏`room` (#1721) קורא `last_seen` אחרי פתיחת הגל כ"המשכפל עלה מחדש", ושלושה
טסטים ב-`test_room_rewake_1721` נפלו כשהדיווח האחרון של גל 1 נכתב שם.

בקרה שלילית: על הקוד שלפני התיקון `online` הוא False והחיבור נסגר 4409.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.console import auth, monitor
from server import db as db_module
from test_monitor import (MAC, SERVER_INIT, browser_handshake,
                          prepare_secret_machine)


class _Clock:
    """שעון של `server.db` — ‏`datetime.now` שזז רק כשהטסט מזיז אותו."""

    def __init__(self, start: datetime):
        self.now = start

    def install(self, monkeypatch) -> None:
        clock = self

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.now if tz else clock.now.replace(tzinfo=None)

        monkeypatch.setattr(db_module, "datetime", Frozen)


def _writing_member(server, session_id: str = "ses_1839") -> None:
    """סבב פתוח שהמכונה חברה בו וכותבת — כמו גל של משכפל."""
    conn = server["ctx"].conn
    image_id = server["admin"].get("/api/console/images").json()[0]["id"]
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES (?, 'grp_BUILD', ?, 'ROOM', 1, 300, 'running', 'nadav', ?, 0.0,"
        " 'multicast')",
        (session_id, image_id, "2026-10-19T19:29:30+00:00"))
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, done,"
        " bytes_written, bytes_total, updated_at)"
        " VALUES (?, ?, 'writing', 0, 0, 0, ?)",
        (session_id, MAC, "2026-10-19T19:29:30+00:00"))
    conn.commit()


def _progress(server, session_id: str, written: int) -> None:
    body = {"session_id": session_id, "mac": MAC, "state": "writing",
            "targets": [{"dev": "sda", "state": "writing",
                         "bytes_written": written, "bytes_total": 1 << 40}]}
    response = server["anon"].post("/api/v1/agent/progress", json=body)
    assert response.status_code == 200, response.text
    assert response.json().get("ok") is True, response.json()


def test_progress_alone_for_120_seconds_keeps_the_machine_online(server, monkeypatch):
    """**הבאג עצמו.** ‏hello אחד בתחילת הגל, ואז רק progress כל 2ש' במשך
    120ש' — כמו משכפל שכותב. בסוף: ‏`online: true` ברשימת המוניטור,
    והחיבור למוניטור מתקבל (ServerInit, לא 4409 "המכונה אינה מחוברת")."""
    start = datetime(2026, 10, 19, 19, 29, 30, tzinfo=timezone.utc)
    clock = _Clock(start)
    clock.install(monkeypatch)
    machine = prepare_secret_machine(server)              # ה-hello היחיד, ב-start
    _writing_member(server)

    for second in range(2, 121, 2):
        clock.now = start + timedelta(seconds=second)
        _progress(server, "ses_1839", second << 20)

    now = start + timedelta(seconds=120)
    rows = monitor.machine_rows(server["ctx"].conn, now)
    assert [r["online"] for r in rows if r["mac"] == MAC] == [True]

    async def connector(_host, _port):
        return machine, machine

    app = FastAPI()
    app.include_router(monitor.create_monitor_router(
        server["ctx"], open_connection=connector, now_fn=lambda: now))
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, auth.issue(server["ctx"].conn, "noc", "admin"))
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT


def test_progress_writes_last_seen_at_most_every_15_seconds(server):
    """לא מעמיס: progress כל 2ש' כותב שורה לכל היותר פעם ב-15ש' (חניקת
    #136), ומכונה שמעולם לא שלחה hello אינה נוצרת מ-progress."""
    prepare_secret_machine(server)
    conn = server["ctx"].conn
    start = datetime.now(timezone.utc) + timedelta(seconds=20)   # ה-hello ישן מ-15ש'
    assert db_module.agent_seen(conn, MAC, start) is True
    assert db_module.agent_seen(conn, MAC, start + timedelta(seconds=14)) is False
    assert db_module.agent_seen(conn, MAC, start + timedelta(seconds=15)) is True
    assert db_module.agent_seen(conn, "02:00:00:00:00:99", start) is False
    assert conn.execute("SELECT 1 FROM net_devices WHERE mac = ?",
                        ("02:00:00:00:00:99",)).fetchone() is None


def test_a_refused_progress_is_not_a_sign_of_life(server, monkeypatch):
    """רק דיווח שהתקבל מעדכן — סבב שנסגר / מי שאינו חבר אינם ראיה."""
    start = datetime(2026, 10, 19, 19, 29, 30, tzinfo=timezone.utc)
    clock = _Clock(start)
    clock.install(monkeypatch)
    prepare_secret_machine(server)
    clock.now = start + timedelta(seconds=120)
    body = {"session_id": "ses_none", "mac": MAC, "state": "writing",
            "targets": [{"dev": "sda", "state": "writing",
                         "bytes_written": 1, "bytes_total": 2}]}
    response = server["anon"].post("/api/v1/agent/progress", json=body)
    assert response.json().get("ok") is False
    rows = monitor.machine_rows(server["ctx"].conn, clock.now)
    assert [r["online"] for r in rows if r["mac"] == MAC] == [False]
