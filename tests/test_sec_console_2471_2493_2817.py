"""#2471, #2493, #2817 — הקשחות בצד הקונסולה והאימות.

- #2471 ס' 1: `/login/mfa` (לא מאומת) קורא את הגוף דרך `body.read_json` — תקרה ו-400.
- #2471 ס' 2: עוגייה (גם קיוסק, הפטורה מ-idle) אינה חוזרת לחיים בשחרור חסימה או
  ביצירה מחדש של משתמש באותו שם.
- #2493 ס' 1: `NaN`/`Infinity` ב-hello נדחים ב-400, ו-`GET /machines` אינו נופל ב-500.
- #2493 ס' 3: צופה ה-VM בודק `still_admin` אחרי הרישום, כמו המוניטור (#1853).
- #2817 ס' 1 (#2493 ה1): כותרות האבטחה של 8081.
- #2817 ס' 2: מתעד סטטיסטיקה שזורק אינו משאיר את הגל פתוח.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import hello_body
from server.agent import body as agent_body
from server.app import create_agent_app, create_console_app
from server.console import auth, users, viewer_sessions
from test_server_room import (CLONER1, cloner_hello, heartbeat, report, room,  # noqa: F401
                              room_body, room_server)
from test_vmupdate_api import BASE as VM_BASE, QemuReader, Writer, viewer_client, vm  # noqa: F401

STATIC = Path(__file__).resolve().parent.parent / "server" / "static"
SESSION_ENDED = "נותקת מהקונסולה"


def _console_anon(server) -> TestClient:
    """אפליקציית 8081 עצמה, בלי עוגייה; 500 חוזר כתשובה ולא כחריגה בטסט."""
    return TestClient(create_console_app(server["app"].state.runtime),
                      raise_server_exceptions=False)


# --- #2471 ס' 1: /login/mfa ---------------------------------------------------

MFA = "/api/console/login/mfa"
JSON = {"Content-Type": "application/json"}


@pytest.mark.parametrize("raw", [b"[1]", b'"challenge"', b'{"challenge": 5, "code": "1"}',
                                 b'{"challenge": "x", "code": 123456}', b"{"])
def test_login_mfa_refuses_a_malformed_body_with_400_not_500(server, raw):
    r = _console_anon(server).post(MFA, content=raw, headers=JSON)
    assert r.status_code == 400, (raw, r.status_code, r.text)


def test_login_mfa_refuses_an_oversized_body_before_reading_it(server):
    huge = b'{"challenge": "' + b"a" * (agent_body.MAX_BYTES + 1) + b'", "code": "1"}'
    r = _console_anon(server).post(MFA, content=huge, headers=JSON)
    assert r.status_code == 413, (r.status_code, r.text[:200])


def test_login_mfa_still_answers_a_wrong_challenge_with_401(server):
    """השומר מפני תיקון-יתר: גוף תקין עם אתגר שגוי — אותה תשובה כמו קודם."""
    r = _console_anon(server).post(MFA, json={"challenge": "nope", "code": "123456"})
    assert r.status_code == 401, r.text


# --- #2471 ס' 2: עוגייה שחוזרת לחיים -------------------------------------------

@pytest.mark.parametrize("purpose", [auth.PURPOSE_KIOSK, ""])
def test_a_cookie_from_before_a_block_stays_dead_after_unblocking(server, purpose):
    conn = server["ctx"].conn
    users.create(conn, "kio", "kiosk-pass-1", "deploy", by="test", check_policy=False)
    token = auth.issue(conn, "kio", "deploy", purpose=purpose)
    assert auth.read_session(conn, token) is not None
    users.set_disabled(conn, "kio", True, "noc")
    assert auth.read_session(conn, token) is None
    users.set_disabled(conn, "kio", False, "noc")
    assert auth.read_session(conn, token) is None, \
        "a cookie issued before the block came back to life when the block was lifted"
    fresh = auth.issue(conn, "kio", "deploy", purpose=purpose)
    assert auth.read_session(conn, fresh) is not None, "signing in again after unblocking works"


def test_a_cookie_of_a_deleted_user_is_dead_for_a_new_user_with_the_same_name(server):
    conn = server["ctx"].conn
    users.create(conn, "kio", "kiosk-pass-1", "deploy", by="test", check_policy=False)
    token = auth.issue(conn, "kio", "deploy", purpose=auth.PURPOSE_KIOSK)
    users.delete(conn, "kio", "noc")
    assert auth.read_session(conn, token) is None
    users.create(conn, "kio", "another-pass-2", "deploy", by="test", check_policy=False)
    assert auth.read_session(conn, token) is None, \
        "the deleted user's cookie opened a session as the re-created user"
    fresh = auth.issue(conn, "kio", "deploy", purpose=auth.PURPOSE_KIOSK)
    assert auth.read_session(conn, fresh) is not None


def test_a_deleted_users_cookie_stays_dead_when_the_clock_went_back(server, monkeypatch):
    """The creation epoch is monotonic, not the wall clock: the server clock is set back
    (no NTP, a dead RTC battery) between deleting a user and creating one by the same name,
    and the old cookie -- whose epoch was bumped past the new clock -- is still dead."""
    conn = server["ctx"].conn
    clock = [2_000_000_000_000 * 1_000_000]                  # ns
    monkeypatch.setattr(users.time, "time_ns", lambda: clock[0])
    users.create(conn, "kio", "kiosk-pass-1", "deploy", by="test", check_policy=False)
    for _ in range(3):
        users.bump_auth_epoch(conn, "kio")                    # the old row's epoch is now +3
    token = auth.issue(conn, "kio", "deploy", purpose=auth.PURPOSE_KIOSK)
    users.delete(conn, "kio", "noc")
    clock[0] -= 3_600 * 10**9                                 # an hour back
    users.create(conn, "kio", "another-pass-2", "deploy", by="test", check_policy=False)
    assert auth.read_session(conn, token) is None,         "the deleted user's cookie opened a session as the re-created user"
    assert auth.read_session(conn, auth.issue(conn, "kio", "deploy", purpose=auth.PURPOSE_KIOSK))


def test_a_corrupt_epoch_floor_refuses_the_create_by_name(server):
    conn = server["ctx"].conn
    from server.db import set_setting
    set_setting(conn, users.AUTH_EPOCH_FLOOR, "١٢")           # Unicode digits are not a floor
    with pytest.raises(RuntimeError, match="רצפת ה-epoch"):
        users.create(conn, "kio", "kiosk-pass-1", "deploy", by="test", check_policy=False)
    assert users.lookup(conn, "kio") is None


# --- #2493 ס' 1: NaN ב-hello -------------------------------------------------

@pytest.mark.parametrize("raw", [b'{"size": NaN}', b'{"size": Infinity}', b'{"size": -Infinity}',
                                 b'{"size": 1e999}', b'[{"a": [-1e400]}]'])
def test_non_finite_numbers_are_bad_json(raw):
    with pytest.raises(agent_body.BodyError) as refused:
        agent_body.parse(raw)
    assert (refused.value.status, refused.value.code) == (400, "bad_json")


def test_finite_numbers_still_parse():
    assert agent_body.parse(b'{"a": 1.5, "b": -2e10, "c": 0, "d": 1E3}') == {
        "a": 1.5, "b": -2e10, "c": 0, "d": 1000.0}


def test_a_nan_in_hello_is_400_and_the_machine_list_stays_up(room_server):
    body = hello_body(CLONER1)
    raw = json.dumps(body).replace('"size_bytes": 256060514304', '"size_bytes": NaN')
    assert "NaN" in raw
    answer = room_server["anon"].post("/api/v1/agent/hello", content=raw.encode(),
                                      headers=JSON)
    assert answer.status_code == 400, answer.text
    assert answer.json().get("code") == "bad_json", answer.text
    admin = TestClient(room_server["app"], raise_server_exceptions=False)
    admin.cookies.set(auth.COOKIE_NAME, room_server["admin"].cookies.get(auth.COOKIE_NAME))
    listed = admin.get("/api/console/machines")
    assert listed.status_code == 200, listed.text


# --- #2493 ס' 3: צופה ה-VM -----------------------------------------------------

def _vm_close_within(client, seconds: float = 10.0):
    """קוד וסיבת הסגירה — או "a bridge byte arrived" כשהגשר נפתח. בלי להיתקע."""
    got = {}

    def run():
        try:
            with client.websocket_connect(VM_BASE + "/viewer") as ws:
                ws.receive_bytes()
            got["close"] = "a bridge byte arrived"
        except WebSocketDisconnect as exc:
            got["close"] = (exc.code, exc.reason)
        except Exception as exc:                     # noqa: BLE001 — נמסר כערך, לא נבלע
            got["close"] = repr(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), f"no close within {seconds:.0f} s"
    return got["close"]


def test_a_revocation_between_the_check_and_the_registration_closes_the_vm_viewer(
        server, vm, monkeypatch):
    """ביטול בין `auth.check` ל-`viewer_sessions.register` (בסדר של logout: המסד, ואז
    viewer_sessions) — לא מוצא גשר לבטל. `still_admin` אחרי הרישום תופס אותו."""
    conn = server["ctx"].conn
    sock = vm["engine"].workdir / "vnc.sock"
    reached = []

    async def connector(path):
        reached.append(path)
        return QemuReader(), Writer()

    client = viewer_client(server, vm["engine"], "admin", connector)
    token = client.cookies.get(auth.COOKIE_NAME)

    def vnc_socket():
        auth.revoke(conn, token)
        viewer_sessions.revoke(token)
        return sock

    monkeypatch.setattr(vm["engine"], "vnc_socket", vnc_socket)
    assert _vm_close_within(client) == (4401, SESSION_ENDED), \
        "a session revoked before the registration still got the VM's keyboard and mouse"
    assert reached == [], "no socket to the VM after the session ended"


def test_a_revocation_while_connecting_to_the_vm_closes_4401(server, vm, monkeypatch):
    conn = server["ctx"].conn
    sock = vm["engine"].workdir / "vnc.sock"
    monkeypatch.setattr(vm["engine"], "vnc_socket", lambda: sock)
    holder = {}

    async def connector(_path):
        auth.revoke(conn, holder["token"])           # מה ש-POST /logout עושה, בסדר שלו
        viewer_sessions.revoke(holder["token"])
        await asyncio.sleep(0)                       # הביטול נוחת כאן
        raise AssertionError("the cancel did not land while connecting")

    client = viewer_client(server, vm["engine"], "admin", connector)
    holder["token"] = client.cookies.get(auth.COOKIE_NAME)
    assert _vm_close_within(client) == (4401, SESSION_ENDED)


def test_an_admin_still_gets_the_vm_viewer(server, vm, monkeypatch):
    """השומר מפני תיקון-יתר: בלי ביטול — הגשר נפתח."""
    sock = vm["engine"].workdir / "vnc.sock"
    monkeypatch.setattr(vm["engine"], "vnc_socket", lambda: sock)

    async def connector(_path):
        return QemuReader(), Writer()

    client = viewer_client(server, vm["engine"], "admin", connector)
    assert _vm_close_within(client) == "a bridge byte arrived"


# --- #2817 ס' 1: כותרות האבטחה של 8081 -----------------------------------------

EXPECTED_HEADERS = {
    "x-frame-options": "SAMEORIGIN",
    "content-security-policy":
        "frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'self'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
}


@pytest.mark.parametrize("path", ["/console/", "/console/monitor.html", "/console/console.js",
                                  "/api/console/me", "/api/console/no-such"])
def test_every_console_response_carries_the_security_headers_once(server, path):
    r = _console_anon(server).get(path)
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers.get_list(name) == [value], (path, r.status_code, name,
                                                     r.headers.get_list(name))


def test_a_refused_request_carries_them_too(server):
    r = _console_anon(server).post("/api/console/users", json={"username": "x"},
                                   headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers.get_list(name) == [value], name


def test_the_agent_port_gets_none_of_them(server):
    r = TestClient(create_agent_app(server["app"].state.runtime)).post(
        "/api/console/login", json={"username": "nobody", "password": "x"})
    for name in EXPECTED_HEADERS:
        assert name not in r.headers, name


@pytest.mark.parametrize("name,kind", [("console.js", "javascript"), ("monitor.js", "javascript"),
                                       ("console.css", "text/css"),
                                       ("design-tokens.css", "text/css")])
def test_nosniff_does_not_break_the_console_scripts_and_styles(server, name, kind):
    """nosniff חוסם סקריפט או גיליון שסוגו שגוי — כאן הם מוגשים בסוג שלהם."""
    r = _console_anon(server).get(f"/console/{name}")
    assert r.status_code == 200
    assert kind in r.headers["content-type"], r.headers["content-type"]


def test_the_console_html_has_nothing_the_policy_would_block():
    """base-uri/object-src/form-action: אין `<base>`, `<object>`, `<embed>` ואין טופס
    עם `action=` (הטפסים נשלחים ב-JS לאותו מקור)."""
    found = []
    for f in [*STATIC.glob("*.js"), *STATIC.glob("*.html")]:
        text = f.read_text(encoding="utf-8")
        found += [(f.name, m) for m in re.findall(
            r"<base\b|<object\b|<embed\b|<form\b[^>]*\baction=|\bformaction=", text, re.I)]
    assert found == []


# --- #2817 ס' 2: מתעד שזורק אינו תוקע את הגל -------------------------------------

RECORDERS = (("server.rounds.drive_rates", "drive_rates"), ("server.learn.ports", "learn_ports"),
             ("server.learn.models", "learn_models"), ("server.learn.tuning", "learn_tuning"))


@pytest.mark.parametrize("broken", [name for _, name in RECORDERS])
def test_a_recorder_that_raises_does_not_keep_the_wave_open(room_server, monkeypatch, caplog,
                                                           broken):
    import importlib
    called = []
    for module_name, name in RECORDERS:
        module = importlib.import_module(module_name)
        real = module.record_wave

        def fake(*args, _name=name, _real=real, **kwargs):
            called.append(_name)
            if _name == broken:
                raise RuntimeError(f"injected #2817 ({_name})")
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, "record_wave", fake)
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    if room(deploy)["round"]["wave_state"] == "open":
        assert deploy.post("/api/console/room/start").status_code == 200
    with caplog.at_level(logging.ERROR, logger="imagesrv.room"):
        report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})
        heartbeat(room_server)
    assert room(deploy)["round"] is None, "the wave stayed open — the round is stuck"
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "room_done" in events
    assert set(called) == {name for _, name in RECORDERS}, called
    assert any(broken in r.getMessage() and r.exc_info for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
