"""#3834 (המשך #3826, #3768) -- "הגיעה / לא הגיעה" של `never_woke` אינו משתנה כששעון הקיר קופץ.

`wol_wakes.sent_epoch` נכתב ב-`steadyclock`, ו-`net_devices.last_seen` הוא שעון קיר (שמכויל ב-
`clockstep.rebase` לבסיס הקיר הנוכחי אחרי קפיצה). ההשוואה בין שניהם בלי תיקון הפכה את התשובה בגודל
הקפיצה. התיקון מתרגם את `last_seen` לסקאלה של `sent_epoch` (`steadyclock.skew`) ואינו נוגע ב-`last_seen`
עצמו -- הוא החותמת שמוצגת למפעיל. השעון מוזרק כמו ב-`test_wol_callers_clock_3826.py`, והקריאות רצות בלי
`now` מפורש (הנתיב האמיתי).
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from server import db
from server.learn import never_woke, wol as learn_wol
from server.system import clockstep, steadyclock

MAC = "78:ac:c0:9b:11:c2"
HOUR = 3600.0
JUMPS = pytest.mark.parametrize("jump", [3 * HOUR, -3 * HOUR], ids=["forward", "backward"])


class Clock:
    def __init__(self) -> None:
        self.wall, self.mono = 1_760_000_000.0, 5_000.0

    def tick(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds

    def jump(self, seconds: float) -> None:
        self.wall += seconds          # הקיר בלבד -- ה-monotonic אינו זז


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


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _seen(conn, clock, ago: float = 0.0) -> str:
    """`net_devices.last_seen` = הקיר עכשיו (פחות `ago`) -- כמו `net_seen`."""
    stamp = _stamp(clock.wall - ago)
    conn.execute("INSERT OR REPLACE INTO net_devices (mac, last_seen) VALUES (?, ?)", (MAC, stamp))
    conn.commit()
    return stamp


def _last_seen(conn) -> str:
    return conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?", (MAC,)).fetchone()["last_seen"]


def _rebase(conn, clock, step: float) -> None:
    """מה ש-`clockstep.guard` עושה בדופק הבא אחרי הקפיצה (הדופק רץ כל הזמן בשרת)."""
    store = SimpleNamespace(now=steadyclock.now)
    clockstep.rebase(conn, store, step, datetime.fromtimestamp(clock.wall, timezone.utc))


@JUMPS
def test_a_machine_that_spoke_after_the_send_is_arrived_whatever_the_wall_does(conn, clock, jump):
    learn_wol.sent(conn, MAC)
    clock.tick(100)
    clock.jump(jump)
    clock.tick(100)
    _seen(conn, clock)                         # אחרי הקפיצה, על הקיר החדש
    clock.tick(100)
    assert never_woke.waiting(conn) == {}


@JUMPS
def test_a_machine_that_spoke_before_the_jump_stays_arrived_after_the_rebase(conn, clock, jump):
    learn_wol.sent(conn, MAC)
    clock.tick(100)
    _seen(conn, clock)                         # דיברה, ואחרי זה השעון קפץ
    clock.tick(100)
    clock.jump(jump)
    _rebase(conn, clock, jump)
    clock.tick(200)
    assert never_woke.waiting(conn) == {}


@JUMPS
def test_a_machine_silent_before_the_send_is_still_a_miss_after_a_jump(conn, clock, jump):
    """אחריות הפוכה: "לא הגיעה" אמיתי אינו נעלם. הדיווח האחרון היה שעה לפני השליחה."""
    _seen(conn, clock, ago=HOUR)
    learn_wol.sent(conn, MAC)
    clock.tick(100)
    clock.jump(jump)
    _rebase(conn, clock, jump)
    clock.tick(200)
    assert MAC in (never_woke.waiting(conn) or {})


@JUMPS
def test_last_seen_itself_is_not_converted(conn, clock, jump):
    """מה שהמפעיל רואה: `last_seen` נשאר חותמת קיר, ו-`waiting` אינו כותב אותו."""
    learn_wol.sent(conn, MAC)
    clock.tick(100)
    clock.jump(jump)
    stamp = _seen(conn, clock)
    clock.tick(300)
    never_woke.waiting(conn)
    assert _last_seen(conn) == stamp


def test_without_a_jump_the_comparison_is_as_before(conn, clock):
    learn_wol.sent(conn, MAC)
    clock.tick(100)
    _seen(conn, clock, ago=200)                # לפני השליחה
    clock.tick(200)
    assert MAC in never_woke.waiting(conn)
    _seen(conn, clock)                         # אחרי השליחה
    assert never_woke.waiting(conn) == {}
