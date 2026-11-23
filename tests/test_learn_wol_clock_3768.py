"""#3768 (המשך #3752) — קפיצת שעון קיר באמצע מדידת WoL אינה מרעילה את הלמידה.

השעון מוזרק דרך `steadyclock._wall` / `_mono` (קיר וmonotonic יחד): הקיר קופץ, ה-monotonic
ממשיך ישר. המדידה והסריקה רצות בלי `now` מפורש — הנתיב האמיתי.
"""

from __future__ import annotations

import pytest

from server import db
from server.learn import wol as learn_wol, wol_sweep
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
    monkeypatch.setattr(learn_wol.time, "time", lambda: c.wall)
    return c


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02',"
              " 'grp_CLONERS', 'x')", (MAC,))
    c.commit()
    return c


def _sample(conn, clock, seconds, jump=0.0):
    learn_wol.sent(conn, MAC)
    clock.tick(seconds / 2)
    clock.jump(jump)
    clock.tick(seconds / 2)
    learn_wol.answered(conn, MAC)
    clock.tick(learn_wol.ANSWER_S + 10)


@pytest.mark.parametrize("jump", [3 * HOUR, -3 * HOUR])
def test_a_clock_jump_mid_wake_does_not_change_the_measured_duration(conn, clock, jump):
    _sample(conn, clock, 40, jump=jump)
    got = conn.execute("SELECT seconds FROM wol_wakes").fetchone()["seconds"]
    assert got == pytest.approx(40)


def test_jumps_both_ways_leave_typical_s_unchanged(conn, clock):
    for seconds in (30, 40, 50):
        _sample(conn, clock, seconds)
    assert learn_wol.stats(conn, MAC)["typical_s"] == 40
    for seconds, jump in ((35, 3 * HOUR), (45, -3 * HOUR), (60, 3 * HOUR), (20, -3 * HOUR)):
        _sample(conn, clock, seconds, jump=jump)
    rows = [r["seconds"] for r in conn.execute("SELECT seconds FROM wol_wakes")]
    assert all(r is not None and 0 <= r <= learn_wol.ANSWER_S for r in rows)
    assert sorted(rows) == [20, 30, 35, 40, 45, 50, 60]


@pytest.mark.parametrize("jump", [3 * HOUR, -3 * HOUR])
def test_a_jump_between_send_and_sweep_raises_no_false_no_wake(conn, clock, jump):
    for seconds in (30, 40, 50):
        _sample(conn, clock, seconds)
    before = learn_wol.stats(conn, MAC)
    sends = []
    for _ in range(learn_wol.FAIL_STREAK):          # הערות פתוחות שהשעון קופץ מתחתן
        learn_wol.sent(conn, MAC)
        clock.tick(10)
        clock.jump(jump)
        wol_sweep.sweep(conn, send=lambda *a: sends.append(a))
        clock.tick(30)                              # ענתה אחרי 40ש' בסך הכול
        learn_wol.answered(conn, MAC)
        clock.tick(learn_wol.ANSWER_S + 10)
    events = [r["event"] for r in conn.execute("SELECT event FROM journal")]
    assert "wol_no_wake" not in events and "wol_resent" not in events and not sends
    after = learn_wol.stats(conn, MAC)
    assert after["streak"] == 0 and after["typical_s"] == before["typical_s"]


def test_a_cross_restart_gap_is_closed_without_a_sample(conn, clock):
    """הערה שנכתבה בבסיס של תהליך קודם, והקיר קפץ אחורה לפני שהמכונה ענתה: נסגרת, לא נמדדת."""
    learn_wol.sent(conn, MAC)
    conn.execute("UPDATE wol_wakes SET sent_epoch = sent_epoch + ?", (3 * HOUR,))
    conn.commit()
    learn_wol.answered(conn, MAC)
    row = conn.execute("SELECT answered_at, seconds FROM wol_wakes").fetchone()
    assert row["answered_at"] and row["seconds"] is None
