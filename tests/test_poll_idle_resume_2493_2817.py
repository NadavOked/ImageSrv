"""#2493 (הכרעת נדב 12/11) ו-#2817: שני תיקונים קטנים.

1. חלון מוניטור פתוח **אינו** פעילות. ה-poll של `GET /api/console/monitor/{mac}/control`
   נשלח כל כמה שניות, ובקשה כזו אינה `touch` ואינה חידוש עוגייה: הקונסולה מתנתקת
   בחוסר פעילות גם כשהחלון פתוח. בקשה אחרת באותה עוגייה כן נספרת, וה-poll עצמו נבדק
   (עוגייה שפג חוסר הפעילות שלה נדחית ב-401, כמו כל בקשה).
2. `_resume` (גל שנסגר מבחוץ) קורא למתעדי הסטטיסטיקה דרך אותה עטיפה כמו `_finish_wave`:
   מתעד שזרק אינו משאיר את הסבב תקוע על `_resume` שנכשל בכל דופק. `learn_ports` לא נרשם
   ב-`_resume` (אותה התנהגות כמו קודם).
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("fastapi")

from server.console import auth
from test_room_wave_recovery import (CONFIRM, _actives, _events, _open_round,  # noqa: F401
                                     _round)
from test_server_room import CLONER1, cloner_hello, room_server  # noqa: F401
from test_user_hardening_2414 import _idle_limit, idle_clock  # noqa: F401

MAC = "aa:bb:cc:dd:ee:01"
POLL = f"/api/console/monitor/{MAC}/control"


# --- 1: ה-poll של המוניטור אינו פעילות ------------------------------------------------

def test_a_monitor_poll_alone_does_not_keep_the_console_alive(server, idle_clock):
    admin = server["admin"]
    assert admin.get("/api/console/me").status_code == 200          # הפעילות האחרונה
    waited = 0
    while waited + 120 <= _idle_limit(server) - 1:
        idle_clock["now"] += 120
        waited += 120
        assert admin.get(POLL).status_code == 200, "the poll was refused before the idle limit"
    idle_clock["now"] += 120
    refused = admin.get(POLL)
    assert refused.status_code == 401, "an open monitor window kept the console alive"
    assert admin.get("/api/console/me").status_code == 401            # מבוטלת, לא רק נדחית


def test_control_a_real_request_between_polls_does_keep_it_alive(server, idle_clock):
    admin = server["admin"]
    for _ in range(4):
        idle_clock["now"] += 400
        assert admin.get(POLL).status_code == 200
        assert admin.get("/api/console/me").status_code == 200       # אדם ליד המקלדת
    assert admin.get(POLL).status_code == 200


def test_the_poll_is_recognised_by_method_and_exact_path():
    class Conn:
        def __init__(self, method, path):
            self.scope = {"method": method, "path": path}
    assert auth._is_poll(Conn("GET", POLL))
    assert auth._is_poll(Conn("GET", POLL + "/"))
    assert not auth._is_poll(Conn("POST", POLL))
    assert not auth._is_poll(Conn("POST", POLL + "/request"))      # בקשת שליטה = פעולה של אדם
    assert not auth._is_poll(Conn("GET", "/api/console/monitor/settings"))
    assert not auth._is_poll(Conn("GET", "/api/console/machines"))


# --- 2: `_resume` עם מתעד שזורק ---------------------------------------------------------

def test_a_wave_closed_from_outside_resumes_even_when_a_recorder_raises(room_server, monkeypatch,
                                                                        caplog):
    from server.learn import ports as learn_ports
    from server.rounds import drive_rates
    anon, ctx = room_server["anon"], room_server["ctx"]
    ports_called = []

    def boom(*_a, **_k):
        raise RuntimeError("injected #2817 (resume)")
    real_ports = learn_ports.record_wave
    monkeypatch.setattr(drive_rates, "record_wave", boom)
    monkeypatch.setattr(learn_ports, "record_wave",
                        lambda *a, **k: ports_called.append(1) or real_ports(*a, **k))
    _open_round(room_server, target=6)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    closed = room_server["deploy"].post(f"/api/console/sessions/{wave1}/close", json=CONFIRM)
    assert closed.status_code == 200, closed.text
    with caplog.at_level(logging.ERROR, logger="imagesrv.room"):
        cloner_hello(anon, CLONER1, ["S1", "S2"])                      # דופק אחד
    round_row = _round(ctx)
    assert round_row["state"] == "active" and round_row["wave_number"] == 2, \
        "the round stayed stuck on the wave that was closed from outside"
    assert len(_actives(ctx)) == 1
    assert _events(ctx, "room_tick_failed") == 0
    assert any("drive_rates" in r.getMessage() and r.exc_info for r in caplog.records), \
        [r.getMessage() for r in caplog.records]                       # הכישלון גלוי ביומן השרת
    assert ports_called == [], "learn_ports is not recorded on this path (as before)"
