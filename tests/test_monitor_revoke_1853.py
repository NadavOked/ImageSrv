"""#1853 — גשר מוניטור פתוח נסגר כשההתחברות לקונסולה בוטלה.

#1394 חיווט את צופה ה-VM ל-`viewer_sessions`; המוניטור — מקומי ודרך שרת משנה —
לא נרשם, ולכן גשר שנושא מקלדת ועכבר למכונה נשאר חי אחרי logout, אחרי מחיקת
המשתמש ואחרי הורדתו ל-deploy: עד שהדפדפן סגר, המכונה נעלמה, או 10 דק' בלי
תעבורה. ומחיקה/הורדה לא קראו ל-`revoke_user` כלל — גם לא לצופה ה-VM.

בקרה שלילית: על הבסיס (round/26) הגשר נשאר פתוח — היומן אינו רושם סגירה תוך
5 ש' וה-socket למכונה אינו נסגר.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.console import auth, users, viewer_sessions
from test_monitor import (MAC, SERVER_INIT, FakeMachine, browser_handshake,
                          monitor_client, prepare_secret_machine)

WS = f"/api/console/monitor/{MAC}"
SESSION_ENDED = "נותקת מהקונסולה"


def _closed_details(server) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'monitor_closed' ORDER BY id")]


def _wait_closed(server, timeout: float = 5.0) -> list[str]:
    """מחכים ליומן ולא ל-receive: גשר שלא נסגר היה חוסם את ה-receive לנצח."""
    deadline = time.monotonic() + timeout
    while not _closed_details(server) and time.monotonic() < deadline:
        time.sleep(0.05)
    return _closed_details(server)


def _close_within(client, seconds: float = 10.0):
    """קוד וסיבת הסגירה, או כשל אם לא נסגר תוך `seconds` — בלי להיתקע: גשר שלא נסגר
    (או endpoint שיצא בלי close) היה חוסם את ה-receive לנצח."""
    got = {}

    def run():
        try:
            with client.websocket_connect(WS) as ws:
                ws.receive_bytes()
            got["close"] = "a bridge byte arrived"
        except WebSocketDisconnect as exc:
            got["close"] = (exc.code, exc.reason)
        except Exception as exc:                     # noqa: BLE001 — נמסר כערך, לא נבלע
            got["close"] = repr(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"no close within {seconds:.0f} s — the page sees nothing and retries"
    return got["close"]


def _watching(server, username: str):
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    client.cookies.set(auth.COOKIE_NAME, auth.issue(server["ctx"].conn, username, "admin"))
    return client, machine


def _assert_revoked(server, ws, machine) -> None:
    assert _wait_closed(server) == [f"{MAC} session_revoked"], \
        "the monitor bridge stayed open after the console session ended"
    with pytest.raises(WebSocketDisconnect) as gone:
        ws.receive_bytes()
    assert gone.value.code == 4401
    assert gone.value.reason == SESSION_ENDED
    assert machine.closed, "the socket to the machine (keyboard and mouse) stayed open"


def test_console_logout_closes_the_open_monitor_and_its_machine_socket(server):
    """הגדרת "גמור" 3, דרך ה-HTTP: צופה פתוח → `POST /logout` באותה עוגייה."""
    client, machine = _watching(server, "noc")
    console = TestClient(server["app"])
    console.cookies.set(auth.COOKIE_NAME, client.cookies.get(auth.COOKIE_NAME))
    with client.websocket_connect(WS) as ws:
        browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT                 # הגשר פתוח
        assert console.post("/api/console/logout").status_code == 200
        _assert_revoked(server, ws, machine)


@pytest.mark.parametrize("how", ["delete", "demote", "disable", "password"])
def test_deleting_demoting_disabling_or_repassing_the_admin_closes_the_monitor(server, how):
    """"password" ו-"disable" — ממצאי סקירת האבטחה על #1853: עריכת סיסמה בעמוד
    המשתמשים (`users.update`) והשבתה (`set_disabled`) לא ביטלו צופים."""
    conn = server["ctx"].conn
    users.create(conn, "ada", "admin-pass-456", "admin", by="test", check_policy=False)
    client, machine = _watching(server, "ada")
    with client.websocket_connect(WS) as ws:
        browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT
        if how == "delete":
            users.delete(conn, "ada", by="noc")
        elif how == "demote":
            users.update(conn, "ada", by="noc", role="deploy")
        elif how == "disable":
            users.set_disabled(conn, "ada", True, by="noc")
        else:
            users.update(conn, "ada", by="noc", password="new-pass-789", check_policy=False)
        _assert_revoked(server, ws, machine)


class _GateThatRevokes:
    """שער ה-#705 רץ ב-`to_thread` **בין** `auth.check` לרישום ב-`viewer_sessions` —
    בדיוק החלון שבו logout/הורדה ל-deploy לא מצאו את הגשר ברישום (סקירת אבטחה)."""

    def __init__(self, action):
        self.action = action

    def refused(self, _service, _local):
        self.action()
        return None


@pytest.mark.parametrize("how", ["logout", "demote"])
def test_a_revocation_between_the_check_and_the_registration_still_closes(server, monkeypatch,
                                                                          how):
    conn = server["ctx"].conn
    users.create(conn, "ada", "admin-pass-456", "admin", by="test", check_policy=False)
    machine = prepare_secret_machine(server)
    reached = []

    async def connector(_host, _port):
        reached.append(1)
        return machine, machine

    client = monitor_client(server, "admin", connector)
    client.cookies.set(auth.COOKIE_NAME, auth.issue(conn, "ada", "admin"))
    console = TestClient(server["app"])
    console.cookies.set(auth.COOKIE_NAME, client.cookies.get(auth.COOKIE_NAME))
    if how == "logout":
        action = lambda: console.post("/api/console/logout").raise_for_status()   # noqa: E731
    else:
        action = lambda: users.update(conn, "ada", by="noc", role="deploy")      # noqa: E731
    monkeypatch.setattr(server["ctx"], "nic_services", _GateThatRevokes(action), raising=False)
    assert _close_within(client) == (4401, SESSION_ENDED), \
        "a session revoked in the gate window still got a bridge"
    assert reached == [], "no TCP to the machine after the session ended"


def test_a_revocation_while_connecting_to_the_machine_closes_4401(server):
    """ביטול **אחרי** הרישום ולפני הגשר (החיבור למכונה, אימותה): המשימה מבוטלת שם,
    והדפדפן מקבל 4401 בשמו — לא סגירה עלומה שהדף מנסה שוב."""
    from server.console import viewer_sessions as sessions
    conn = server["ctx"].conn
    prepare_secret_machine(server)
    holder = {}

    async def connector(_host, _port):
        auth.revoke(conn, holder["token"])           # מה ש-POST /logout עושה, בסדר שלו
        sessions.revoke(holder["token"])
        await asyncio.sleep(0)                       # הביטול נוחת כאן
        raise AssertionError("the cancel did not land while connecting")

    client = monitor_client(server, "admin", connector)
    holder["token"] = client.cookies.get(auth.COOKIE_NAME)
    assert _close_within(client) == (4401, SESSION_ENDED)
    assert conn.execute("SELECT COUNT(*) AS n FROM journal WHERE event = 'monitor_connected'"
                        ).fetchone()["n"] == 0


def test_a_browser_that_closes_is_still_journaled_as_browser(server):
    """השומר מפני תיקון-יתר: ביטול שאינו מ-`viewer_sessions` (הדפדפן הלך) אינו
    "ההתחברות בוטלה", והמנעול משתחרר — אותה מכונה נפתחת שוב מיד."""
    client, _machine = _watching(server, "noc")
    with client.websocket_connect(WS) as ws:
        browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT
    assert _wait_closed(server) == [f"{MAC} browser"]

    async def fresh(_host, _port):            # כל WebSocket ב-TestClient רץ ב-loop משלו
        machine = FakeMachine()
        return machine, machine

    client = monitor_client(server, "admin", fresh)
    with client.websocket_connect(WS) as again:
        browser_handshake(again)
        assert again.receive_bytes() == SERVER_INIT


def test_one_cookie_holds_several_viewers_and_revoke_closes_all_of_them():
    """מוניטור לכמה מכונות (ו-VM) מאותה עוגייה: הרישום הישן החזיק משימה אחת
    לטוקן, והשנייה דרסה את הראשונה — logout היה סוגר רק את האחרונה."""
    async def scenario():
        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(3)]
        for task in tasks:
            viewer_sessions.register("cookie-1", "Noc", task)
        viewer_sessions.forget("cookie-1", tasks[2])             # הצופה השלישי נסגר לבד
        assert viewer_sessions.revoke("cookie-1") is True
        await asyncio.gather(*tasks[:2], return_exceptions=True)
        assert all(task.cancelled() for task in tasks[:2])
        assert all(viewer_sessions.was_revoked(task) for task in tasks[:2])
        assert not tasks[2].cancelled() and not viewer_sessions.was_revoked(tasks[2])
        assert viewer_sessions.revoke("cookie-1") is False
        tasks[2].cancel()
        await asyncio.gather(tasks[2], return_exceptions=True)

    asyncio.run(scenario())


def test_revoke_user_closes_every_viewer_of_that_user_only():
    async def scenario():
        mine = [asyncio.create_task(asyncio.Event().wait()) for _ in range(3)]
        other = asyncio.create_task(asyncio.Event().wait())
        viewer_sessions.register("cookie-a", "ada", mine[0])
        viewer_sessions.register("cookie-a", "ada", mine[1])
        viewer_sessions.register("cookie-b", "ADA", mine[2])
        viewer_sessions.register("cookie-c", "noc", other)
        assert viewer_sessions.revoke_user("Ada") == 3
        await asyncio.gather(*mine, return_exceptions=True)
        assert all(task.cancelled() for task in mine)
        assert not other.cancelled()
        assert viewer_sessions.revoke("cookie-c") is True
        await asyncio.gather(other, return_exceptions=True)

    asyncio.run(scenario())

