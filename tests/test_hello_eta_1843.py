"""‏#1843 — מתי הגל צפוי להסתיים, בתשובת ה-hello של מחשב השיכפול (`session.forecast`).

מוקאפ 07/11 (נדב אישר): הגואי של המשכפל מציג "צפוי להסתיים ב-HH:MM" מעל "כ-N דק' נותרו".
כאן הצד של השרת. שלושה מצבים נפרדים, ואף אחד מהם אינו מספר מומצא:

* יש היסטוריה והשעון מסונכרן — ‏`ends_at` (epoch) = ‏`started_at` של הגל + ההערכה, ‏`clock: true`;
* אין היסטוריה — ‏`why: "no_history"`, בלי שעה;
* שעון השרת לא הוכח מסונכרן (#2144) — ‏`clock: false`, ו-`ends_at: null` גם כשיש הערכה.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from conftest import Clock, MANIFEST_256, room_slots, write_image

from server.agent import hello_eta
from test_learn_eta_1843 import GB, _wave
from test_server_room import CLONER1, cloner_hello

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


@pytest.fixture()
def clocked(tmp_path, monkeypatch):
    """חדר עם משכפל אחד, ו-`status` של שעון השרת שהבדיקה שולטת בו
    (`status["now"]` — מה ש-`timesync.status` היה מחזיר; ‏None = לא הוזרק)."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                       # noqa: PLC0415
    from server.console import users
    from server.rounds import sender as sender_module
    from server.app import create_app

    monkeypatch.setattr(sender_module, "port_holders", lambda _port: [])
    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    status = {"now": {"synchronized": True, "offset_ms": 0.0}, "calls": 0}

    def timesync_status():
        status["calls"] += 1
        got = status["now"]
        if isinstance(got, Exception):
            raise got
        return got

    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080", now_fn=Clock(),
                     sender_runner=Recorder(), wol_send=lambda _p: None,
                     health_hooks={"timesync_status": timesync_status})
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin, anon = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert admin.post("/api/console/machines", json={
        "mac": CLONER1, "name": "shich-1", "group_id": "grp_CLONERS"}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "anon": anon, "status": status}
    ctx.sender.stop()


def _open(env, target: int) -> dict:
    """פותח סבב על שתי המגירות של CLONER1 ומחזיר את ה-hello של המשכפל. עם `target=2` המגירות
    מכסות את היעד והגל יוצא עם ההצטרפות; עם יעד גדול מהן הוא נשאר `open`."""
    body = {"image_id": "img_7f3a91", "target_drives": target,
            "target_slots": room_slots(env["ctx"].conn, {CLONER1: ["S1", "S2"]})}
    assert env["admin"].post("/api/console/room", json=body).status_code == 200
    return cloner_hello(env["anon"], CLONER1, ["S1", "S2"])


def _running(env) -> dict:
    """הגל של CLONER1 (2 מגירות) יצא לדרך; מה שה-hello של אותו משכפל מחזיר."""
    answer = _open(env, 2)
    assert answer["session"]["state"] == "running", answer["session"]
    return answer


def _history(env) -> None:
    """גל קודם של אותו משכפל עם 2 מגירות: 50MB/s ו-100ש' קבועים → לאימג' של המניפסט
    (57,982,058,496 בייטים דחוסים) ‏100 + 1159.6 = 1259ש'."""
    _wave(env["ctx"].conn, {CLONER1: {"drawers": 2, "done": 2, "written_bytes": 2 * 20 * GB}},
          wall=400, total=500)


def _started(env) -> int:
    row = env["ctx"].conn.execute(
        "SELECT started_at FROM sessions WHERE state = 'running'").fetchone()
    return int(datetime.fromisoformat(row["started_at"]).timestamp())


# --- השרת: שלושת המצבים -------------------------------------------------------


def test_history_and_a_synced_clock_give_the_end_as_epoch(clocked):
    _history(clocked)
    answer = _running(clocked)
    assert answer["session"]["state"] == "running"
    forecast = answer["session"]["forecast"]
    assert forecast["why"] is None and forecast["clock"] is True, forecast
    # הגל יצא עכשיו: started_at + 1259ש' (חותמות השרת בשניות שלמות)
    assert abs(forecast["ends_at"] - (_started(clocked) + 1259)) <= 1, forecast
    assert isinstance(forecast["ends_at"], int)


def test_no_history_is_said_and_carries_no_time(clocked):
    answer = _running(clocked)
    assert answer["session"]["forecast"] == {"ends_at": None, "why": "no_history", "clock": True}


@pytest.mark.parametrize("state", [
    {"synchronized": False, "offset_ms": 4000.0},      # chrony ענה: לא מסונכרן
    {"synchronized": None, "offset_ms": None},         # chrony לא נקרא — "לא הצלחנו לבדוק" אינו "מסונכרן"
    RuntimeError("chronyc is not installed"),          # והמדידה עצמה נכשלה
])
def test_a_clock_that_is_not_proven_synced_hides_the_time(clocked, state):
    _history(clocked)
    clocked["status"]["now"] = state
    forecast = _running(clocked)["session"]["forecast"]
    assert forecast == {"ends_at": None, "why": "clock", "clock": False}, forecast


def test_no_history_is_not_hidden_by_the_clock(clocked):
    """אין שעה להסתיר: "אין היסטוריה" נשאר "אין היסטוריה" — וה-`clock` אומר שהשעה, אם תחושב מהקצב, אסורה."""
    clocked["status"]["now"] = {"synchronized": False}
    assert _running(clocked)["session"]["forecast"] == {"ends_at": None, "why": "no_history", "clock": False}


def test_an_open_wave_carries_no_forecast(clocked):
    """הגל עוד לא יצא — אין `started_at`, ואין מה להגיד (הקונסולה אומרת "לפי היסטוריה: כ-X")."""
    _history(clocked)
    answer = _open(clocked, 4)
    assert answer["session"]["state"] == "open" and "forecast" not in answer["session"]


def test_a_forecast_that_breaks_never_breaks_the_hello(clocked, monkeypatch):
    """ההערכה היא מספר על המסך. מכונה שעומדת לכתוב מקבלת את הסבב שלה גם כשהיא נופלת."""
    from server.rounds import room
    monkeypatch.setattr(room, "wave_forecast", lambda *a, **k: 1 / 0)
    answer = _running(clocked)
    assert answer["session"]["state"] == "running" and answer["session"]["id"]
    assert answer["session"]["forecast"] == {"ends_at": None, "why": "error", "clock": True}


def test_the_clock_is_asked_once_a_minute_not_on_every_hello(clocked):
    _history(clocked)
    _running(clocked)
    seen = clocked["status"]["calls"]
    for _ in range(5):
        cloner_hello(clocked["anon"], CLONER1, ["S1", "S2"], joining=False)
    assert clocked["status"]["calls"] == seen >= 1


def test_the_console_forecast_is_unchanged(clocked):
    """`round.forecast` נשאר כפי שהיה (הפונקציה עברה ל-`room.wave_forecast`, אותה גוף)."""
    _history(clocked)
    _running(clocked)
    fc = clocked["admin"].get("/api/console/room").json()["round"]["forecast"]
    assert fc["why"] is None and fc["seconds"] == 1259 and fc["ends_at"] is not None


# --- ClockTrust: ראיה חיובית, ולא יותר ----------------------------------------


class Stopwatch:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _trust(results, watch=None):
    watch = watch or Stopwatch()
    seen = []

    def status():
        seen.append(watch.t)
        got = results.pop(0) if len(results) > 1 else results[0]
        if isinstance(got, Exception):
            raise got
        return got

    return hello_eta.ClockTrust(status, watch), watch, seen


def test_a_clock_never_measured_is_not_trusted():
    assert hello_eta.ClockTrust(None).ok() is False


def test_one_reading_serves_the_ttl_and_then_it_is_asked_again():
    trust, watch, seen = _trust([{"synchronized": True}])
    assert trust.ok() is True and trust.ok() is True and len(seen) == 1
    watch.t += hello_eta.CLOCK_TTL_S
    assert trust.ok() is True and len(seen) == 2


def test_a_clock_that_stopped_being_synced_is_noticed_at_the_next_reading():
    trust, watch, _ = _trust([{"synchronized": True}, {"synchronized": False}])
    assert trust.ok() is True
    watch.t += hello_eta.CLOCK_TTL_S
    assert trust.ok() is False


def test_a_failing_reading_is_not_trust_and_does_not_raise():
    trust, _, _ = _trust([RuntimeError("chronyc hung")])
    assert trust.ok() is False


def test_an_old_reading_is_not_believed_when_the_refresh_cannot_run():
    """תהליכון אחר מחזיק את הרענון (הנעילה תפוסה); קריאה בת יותר מ-CLOCK_MAX_AGE_S אינה ראיה."""
    trust, watch, _ = _trust([{"synchronized": True}])
    assert trust.ok() is True
    watch.t += hello_eta.CLOCK_MAX_AGE_S + 1
    trust._lock.acquire()
    try:
        assert trust.ok() is False
    finally:
        trust._lock.release()


def test_a_fresh_enough_reading_is_still_believed_while_another_thread_refreshes():
    trust, watch, _ = _trust([{"synchronized": True}])
    assert trust.ok() is True
    watch.t += hello_eta.CLOCK_TTL_S + 1
    trust._lock.acquire()
    try:
        assert trust.ok() is True
    finally:
        trust._lock.release()
