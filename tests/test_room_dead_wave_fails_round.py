"""ברזל 17/10 — גל שאף מגירה לא נכתבה בו מסיים את הסבב כ-`failed`.

מה קרה: סבב `room_72308383` (מחשב שיכפול 2, שתי מגירות). ממשק הרשת של
המשכפל הורד באמצע הזרם; השרת סימן אותו `lost`, ו-`_finish_wave` פתח גל 2
(‏`room_wave wave=2 written=0/2`) ועצר את השולח (`send_stopped`). שתי
המגירות נכשלו בגלוי — אבל הסבב נשאר `active` וגל 2 נשאר פתוח וממתין, בלי
שאיש יבחין: "הגל הבא נפתח מעצמו וממתין למגירות מוחלפות" נכון לגל שכתב
**משהו**, לא לגל שכל יעדיו נכשלו או נותקו.

הכרעת נדב 17/10: סבב שכל היעדים בגל שלו נכשלו / הזרם נעצר — נסגר `failed`
ומשתחרר (חריץ המולטיקאסט פנוי, `open_round` חדש אפשרי).

שומרי תיקון-יתר: גל שכתב **חלק** ממשיך לגל הבא כמו קודם
(``test_failed_drawer_is_not_counted_and_retries_next_wave``,
``test_a_vanished_member_is_marked_lost_and_the_wave_advances``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.rounds import room as room_mod

from test_server_room import (           # noqa: F401 — ‏room_server הוא fixture
    CLONER1, cloner_hello, heartbeat, report, room, room_body, room_server,
)

pytest.importorskip("fastapi")

_AGE = getattr(room_mod, "LOST_SECONDS", 180) + 60


def _round_row(ctx) -> dict:
    return dict(ctx.conn.execute(
        "SELECT state, failed_reason, wave_session_id, written_drives FROM room_rounds"
    ).fetchone())


def _events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]


def _start(room_server) -> str:
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"
    return wave


def _assert_failed_and_released(room_server, wave: str) -> None:
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    row = _round_row(ctx)
    assert row["state"] == "failed", f"הסבב נשאר {row['state']} אחרי גל שלא כתב דבר: {row}"
    assert row["failed_reason"], "סבב שנכשל בלי סיבה — המפעיל לא יודע למה"
    assert "room_failed" in _events(ctx), "הכשל לא נרשם ביומן (עיקרון 5)"
    # משוחרר: אין גל פתוח שמחזיק את חריץ המולטיקאסט של הקבוצה
    open_waves = ctx.conn.execute(
        "SELECT id, state FROM sessions WHERE state IN ('open', 'running')").fetchall()
    assert not open_waves, f"גל נשאר פתוח אחרי שהסבב נכשל: {[tuple(w) for w in open_waves]}"
    assert room(deploy)["round"]["state"] == "failed"
    # ונפתח מחדש בלי 409
    again = deploy.post("/api/console/room",
                        json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert again.status_code == 200, again.text


def test_a_wave_whose_only_cloner_was_cut_off_fails_the_round(room_server):
    """המקרה מהברזל: המשכפל היחיד נעלם באמצע (רשת למטה) ומוכרז `lost`."""
    ctx = room_server["ctx"]
    wave = _start(room_server)
    old = (datetime.now(timezone.utc) - timedelta(seconds=_AGE)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (old, wave))
    ctx.conn.execute("UPDATE session_members SET updated_at = ? WHERE session_id = ?", (old, wave))
    ctx.conn.commit()

    heartbeat(room_server)

    _assert_failed_and_released(room_server, wave)


def test_a_wave_where_every_drawer_failed_fails_the_round(room_server):
    """אותו כלל כשהמשכפל כן דיווח: שתי המגירות `failed` (הזרם נקטע)."""
    wave = _start(room_server)
    report(room_server["anon"], wave, CLONER1, {"sda": "failed", "sdb": "failed"})

    heartbeat(room_server)

    _assert_failed_and_released(room_server, wave)
