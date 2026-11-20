"""#3826 (המשך #3768) — `never_woke` ו-`timeforecast` קוראים את `wol_wakes` באותו שעון שהוא נכתב בו.

`sent_epoch` נכתב ב-`steadyclock`. קורא שמעביר ל-`stats` / מחסיר ממנו את שעון הקיר שלו מערבב שני
שעונים: אחרי קפיצה הערה פתוחה נראית ישנה בגודל הקפיצה ("לא הגיעה" מזויף), או צעירה ממנה
(הערה שבאמת תקועה נעלמת מהמסך). השעון מוזרק כמו ב-`test_learn_wol_clock_3768.py`, והקריאות רצות
בלי `now` מפורש — הנתיב האמיתי.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import timeforecast
from server.learn import never_woke, wol as learn_wol
from server.system import steadyclock

MAC = "78:ac:c0:9b:11:c2"
HOUR = 3600.0


class Clock:
    def __init__(self) -> None:
        self.wall, self.mono = 1_760_000_000.0, 5_000.0

    def tick(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds

    def jump(self, seconds: float) -> None:
        self.wall += seconds          # הקיר בלבד — ה-monotonic אינו זז


@pytest.fixture()
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(steadyclock, "_wall", lambda: c.wall)
    monkeypatch.setattr(steadyclock, "_mono", lambda: c.mono)
    monkeypatch.setattr(steadyclock, "_anchor", (c.wall, c.mono))
    monkeypatch.setattr(learn_wol.time, "time", lambda: c.wall)   # גם הקיר של הקוראים הישנים
    return c


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02',"
              " 'grp_CLONERS', 'x')", (MAC,))
    c.commit()
    return c


def _typical(conn, clock, seconds: float = 40) -> None:
    for _ in range(learn_wol.MIN_SAMPLES):
        learn_wol.sent(conn, MAC)
        clock.tick(seconds)
        learn_wol.answered(conn, MAC)
        clock.tick(learn_wol.ANSWER_S + 10)


@pytest.mark.parametrize("jump", [3 * HOUR, -3 * HOUR])
def test_never_woke_waited_s_ignores_a_wall_jump_after_the_send(conn, clock, jump):
    _typical(conn, clock)
    learn_wol.sent(conn, MAC)
    clock.tick(100)
    clock.jump(jump)
    clock.tick(100)
    got = never_woke.waiting(conn)
    assert got is not None and MAC in got            # קפיצה אחורה: לא נעלמת; קדימה: לא "שעות"
    assert got[MAC]["waited_s"] == 200
    assert got[MAC]["typical_s"] == 40


@pytest.mark.parametrize("jump", [3 * HOUR, -3 * HOUR])
def test_a_jump_before_the_threshold_does_not_fake_a_miss_or_hide_one(conn, clock, jump):
    _typical(conn, clock)
    learn_wol.sent(conn, MAC)
    clock.tick(60)
    clock.jump(jump)
    assert never_woke.waiting(conn) == {}            # 60ש' < הסף, בכל שעון


def test_timeforecast_wake_s_is_read_on_the_clock_the_wakes_were_written_in(conn, clock, monkeypatch):
    """הערה פתוחה בת 60ש' אינה "הוכרעה" — גם כשהקיר קפץ קדימה. ב-`WINDOW=3` הערה שהוכרעה בטעות
    דוחקת הצלחה מהחלון ו-`wake_s` נעלם."""
    monkeypatch.setattr(learn_wol, "WINDOW", learn_wol.MIN_SAMPLES)
    _typical(conn, clock)
    learn_wol.sent(conn, MAC)
    clock.tick(60)
    clock.jump(3 * HOUR)
    timeforecast.started(conn, "ses_new", [{"mac": MAC, "serial": "s0"}], 10**9)
    row = conn.execute("SELECT evidence_json FROM brain_decisions WHERE rule = 'K16'").fetchone()
    assert row is not None
    assert any(f"{MAC}: wake_s=40 " in line for line in json.loads(row["evidence_json"]))
