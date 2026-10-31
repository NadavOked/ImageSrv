"""‏#1889 — כמה זמן הסבב חיכה לאדם, ועל מה. ניתוח של מה שכבר נאסף (היומן, ‏sessions).

‏`send_held_smart`/`send_hold_released` של השולח = ההמתנה לתשובת SMART; ‏`started_at − created_at` של כל
גל = הגל הפתוח. ההמלצה מוצגת לאדם, ושום דבר אינו משתנה אוטומטית. לא נמדד = None, לא 0.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db
from server.learn import learn_waits

T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)


def iso(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _wave(conn, sid, created, started):
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at, started_at, closed_at, kind)"
                 " VALUES (?, 'grp_CLONERS', 'img', 'p', 1, 300, 'closed', 't', ?, 0, ?, ?, 'multicast')",
                 (sid, iso(created), None if started is None else iso(started), iso(created + 30)))


def _journal(conn, minutes, event, detail):
    conn.execute("INSERT INTO journal (ts, event, detail) VALUES (?,?,?)", (iso(minutes), event, detail))


def _round(conn, rid="room_1", created=0, closed=60):
    conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at,"
                 " closed_at) VALUES (?, 'img', 4, 'closed', 't', ?, ?)", (rid, iso(created), iso(closed)))


def _night(conn):
    """הלילה של 19/10 בקטן: גל 1 חיכה 16 דק' לתשובת SMART; גל 2 לא חיכה; גל 3 נסגר בזמן ההמתנה."""
    _round(conn)
    _wave(conn, "ses_w1", 0, 4)            # 4 דק' פתוח
    _wave(conn, "ses_w2", 25, 27)          # 2 דק' פתוח
    _wave(conn, "ses_w3", 50, None)        # לא יצא — אינו נספר בגל הפתוח
    _journal(conn, 4, "send_held_smart", "ses_w1 78:ac:c0:9b:11:c2")
    _journal(conn, 20, "send_hold_released", "ses_w1 960s")
    _journal(conn, 51, "send_held_smart", "ses_w3 78:ac:c0:9b:11:c2")
    _journal(conn, 70, "send_hold_released", "ses_other 999s")   # של סבב אחר
    conn.commit()


def test_a_round_is_counted_by_what_it_waited_for(conn):
    _night(conn)
    got = learn_waits.round_waits(conn, iso(0), iso(60))
    assert got == {"waves": 3, "open_wait_s": 360, "smart_hold_s": 960, "smart_holds": 1,
                   "smart_unreleased": 1}


def test_a_round_whose_waves_never_started_is_not_measured_not_zero(conn):
    _round(conn)
    _wave(conn, "ses_x", 0, None)
    conn.commit()
    got = learn_waits.round_waits(conn, iso(0), iso(60))
    assert got["open_wait_s"] is None and got["smart_hold_s"] is None and got["waves"] == 1


def test_the_week_line_recommends_but_changes_nothing(conn):
    assert learn_waits.health_checks(conn) == []
    _night(conn)
    [line] = learn_waits.health_checks(conn)
    assert line["state"] == "warn" and "חיכו לתשובות SMART 16 דק'" in line["detail"]
    assert learn_waits.RECOMMEND_HE in line["detail"]
    before = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    learn_waits.health_checks(conn)
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == before


def test_a_short_hold_is_ok(conn):
    _round(conn)
    _wave(conn, "ses_s", 0, 1)
    _journal(conn, 1, "send_held_smart", "ses_s m")
    _journal(conn, 2, "send_hold_released", "ses_s 40s")
    conn.commit()
    [line] = learn_waits.health_checks(conn)
    assert line["state"] == "ok" and "פחות מדקה" in line["detail"]


def test_the_room_history_carries_the_waits(server):
    conn = server["ctx"].conn
    _night(conn)
    [h] = server["admin"].get("/api/console/room/history").json()
    assert h["waits"]["smart_hold_s"] == 960 and h["waits"]["open_wait_s"] == 360
    js = (Path(__file__).resolve().parent.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert "roomRoundWaits(h)" in js
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "smart_hold_s" in doc
