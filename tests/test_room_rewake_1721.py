"""#1721 — גל שנפתח בזמן שהמשכפל עוד בספירת הכיבוי אינו נשאר תקוע.

מה קרה על ברזל (rc5, ‏18/10, מחשב שיכפול 2): גל 1 כתב מגירה אחת מתוך שתיים
(sdb נשלפה באמצע), המשכפל ספר 20 שניות וכבה (נדב: "משכפל נכבה אחרי 20 ש'
בכל מצב"). השרת פתח את גל 2 **באותה שנייה** ושלח WoL — למכונה שעוד הייתה
דלוקה, והחבילה נבלעה. אף אחד לא העיר אותה שוב, והסבב נשאר `active`
(‏written 1/2) בלי שום פעולה.

התיקון: כל עוד הגל פתוח ומחשב שיכפול **שנבחר ושיש לו מה לכתוב** לא נראה
מאז שהגל נפתח — WoL חוזר כל `REWAKE_SECONDS`, עד `REWAKE_MAX` פעמים (לא
לולאה בלי תקרה — הרשת, memory: server-must-not-congest-network). מכונה
שלא חזרה גם אחרי התקרה והמתנה לאתחול — הסבב נכשל **בגלוי**, בשמה.

השעון מזויף: ‏`room._utcnow` מוחלף, והדופק הוא `room.sweep` (שעון הרקע) —
לא hello, כי hello של המשכפל הוא בדיוק הראיה שהוא חזר.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from server import room as room_mod

try:
    from server import clockstep          # #1947
except ImportError:                       # pragma: no cover — negative control
    clockstep = None

from test_server_room import (           # noqa: F401 — ‏room_server הוא fixture
    CLONER1, CLONER2, cloner_hello, report, room, room_body, room_server,
)

pytest.importorskip("fastapi")

STEP = getattr(room_mod, "REWAKE_SECONDS", 60)
CAP = getattr(room_mod, "REWAKE_MAX", 6)


def _packets_to(woken: list[bytes], mac: str) -> int:
    target = bytes.fromhex(mac.replace(":", "")) * 16
    return sum(1 for packet in woken if packet.endswith(target))


def _events(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY rowid", (event,))]


def _round(ctx) -> dict:
    return dict(ctx.conn.execute(
        "SELECT state, failed_reason, written_drives, wave_session_id FROM room_rounds"
    ).fetchone())


class _Clock:
    """השעון של `room._utcnow`: מתחיל ברגע שגל 2 נפתח, ומתקדם ביד.

    ‏#1947: התקדמות ביד היא זמן שעובר, ולא קפיצת שעון — ולכן גם השעון
    המונוטוני של גלאי הקפיצות (`clockstep._mono`) זז איתו."""

    def __init__(self, start: datetime):
        self.now = start
        self.skew = time.time() - time.monotonic()

    def __call__(self) -> datetime:
        return self.now

    def mono(self) -> float:
        return self.now.timestamp() - self.skew


def _wave1_partial(room_server, monkeypatch, drawers=None, target=2, partial_mac=CLONER1):
    """גל 1 כותב חלק, המשכפל "בספירת הכיבוי" — וגל 2 נפתח מהשעון של הרקע.

    ‏`last_seen` מוחזר לפני תחילת הגל: ה-hello האחרון של משכפל שכתב הוא
    מלפני הכתיבה (בזמן הכתיבה הוא מדווח progress ולא hello, #449)."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    drawers = drawers or {CLONER1: ["S1", "S2"]}
    opened = deploy.post("/api/console/room", json=room_body(room_server, target, drawers))
    assert opened.status_code == 200, opened.text
    wave1 = None
    for mac, serials in drawers.items():
        wave1 = cloner_hello(anon, mac, serials)["session"]["id"]
    room_mod.sweep(ctx.conn, ctx.store)
    assert room(deploy)["round"]["wave_state"] == "running"

    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE net_devices SET last_seen = ?", (long_ago,))
    ctx.conn.commit()

    for mac in drawers:
        if mac == partial_mac:
            report(anon, wave1, mac, {"sda": "done", "sdb": "failed"}, top="partial")
        else:
            report(anon, wave1, mac, {f"sd{chr(ord('a') + i)}": "done"
                                      for i in range(len(drawers[mac]))})
    room_mod.sweep(ctx.conn, ctx.store)
    row = _round(ctx)
    assert row["state"] == "active" and row["wave_session_id"] != wave1, row
    created = ctx.conn.execute("SELECT created_at FROM sessions WHERE id = ?",
                               (row["wave_session_id"],)).fetchone()["created_at"]
    clock = _Clock(datetime.fromisoformat(created))
    monkeypatch.setattr(room_mod, "_utcnow", clock, raising=False)
    if clockstep is not None:
        monkeypatch.setattr(clockstep, "_mono", clock.mono)
    return clock, row["wave_session_id"]


def test_a_wol_swallowed_during_the_countdown_is_sent_again(room_server, monkeypatch):
    """המקרה מהברזל: ה-WoL של גל 2 נבלע — דקה אחר כך הוא נשלח שוב, רק לנבחר."""
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, _ = _wave1_partial(room_server, monkeypatch)
    before1, before2 = _packets_to(woken, CLONER1), _packets_to(woken, CLONER2)

    clock.now += timedelta(seconds=STEP - 5)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before1, "WoL חוזר לפני שעבר המרווח"

    clock.now += timedelta(seconds=10)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before1 + 1, (
        "המשכפל כבה אחרי שה-WoL של גל 2 נבלע, ואיש לא העיר אותו שוב — הסבב תקוע (#1721)")
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before1 + 1, "שני דופקים באותו חלון שלחו פעמיים"
    # ‏#1580: רק מי שנבחר — CLONER2 רשום בחדר ולא נבחר לסבב.
    assert _packets_to(woken, CLONER2) == before2
    assert _events(ctx, "room_rewake"), "WoL חוזר בלי שורה ביומן (עיקרון 5)"
    assert _round(ctx)["state"] == "active"


def test_a_future_wave_timestamp_is_due_for_rewake(room_server, monkeypatch):
    """#1771: a bad persisted future created_at cannot freeze the WoL timer."""
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, wave = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)
    future = (clock.now + timedelta(hours=3)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (future, wave))
    ctx.conn.commit()

    room_mod.sweep(ctx.conn, ctx.store)

    assert _packets_to(woken, CLONER1) == before + 1
    assert _events(ctx, "room_rewake")


def test_a_future_wave_timestamp_keeps_the_rewake_spacing_and_the_cap(room_server, monkeypatch):
    """#1771 (metal S3, 19/10): with created_at in the future the six re-wakes
    went out within 20 seconds (one per tick). The spacing is from the last
    re-wake, so a tick every few seconds sends one WoL a minute -- and the cap
    still ends the round when the machine never comes back."""
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, wave = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)
    future = (clock.now + timedelta(hours=3)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (future, wave))
    ctx.conn.commit()

    for _ in range(6):                       # 30 s of 5 s ticks
        clock.now += timedelta(seconds=5)
        room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) - before == 1, "re-wakes burned without spacing"

    clock.now += timedelta(seconds=STEP)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) - before == 2

    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) - before == CAP
    assert _round(ctx)["state"] == "failed", "a future created_at froze the round past the cap"


def test_the_rewake_is_capped_and_then_the_round_fails_by_name(room_server, monkeypatch):
    """תקרה: לא יותר מ-`REWAKE_MAX` חבילות חוזרות, ואז הסבב נכשל בגלוי."""
    ctx, deploy, woken = room_server["ctx"], room_server["deploy"], room_server["woken"]
    clock, wave2 = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)

    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        room_mod.sweep(ctx.conn, ctx.store)

    assert _packets_to(woken, CLONER1) - before == CAP, (
        f"WoL חוזר בלי תקרה או בלי שליחה: {_packets_to(woken, CLONER1) - before} במקום {CAP}")
    row = _round(ctx)
    assert row["state"] == "failed", f"הסבב נשאר {row['state']} — משכפל שלא התעורר תוקע אותו"
    assert "shich-1" in (row["failed_reason"] or ""), row["failed_reason"]
    assert _events(ctx, "room_wake_failed"), "הכשל לא נרשם ביומן"
    still_open = ctx.conn.execute(
        "SELECT id FROM sessions WHERE state IN ('open', 'running')").fetchall()
    assert not still_open, "הגל נשאר פתוח ומחזיק את חריץ המולטיקאסט"
    assert ctx.conn.execute("SELECT state FROM sessions WHERE id = ?",
                            (wave2,)).fetchone()["state"] == "closed"
    again = deploy.post("/api/console/room",
                        json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert again.status_code == 200, again.text


@pytest.mark.parametrize("serials, joining", [
    (["S1"], None),          # התעורר; sdb עוד בחוץ — אין לו מגירה טרייה להצטרף איתה
    (["S1", "S2"], False),   # התעורר ו-sdb בפנים, אבל ממתין על מסך ואינו מצטרף עדיין
], ids=["drawer-still-out", "awake-not-joining"])
def test_a_cloner_that_came_back_is_not_woken_again_and_the_round_waits(
        room_server, monkeypatch, serials, joining):
    """שומר תיקון-יתר: משכפל שהתעורר (hello אחרי פתיחת הגל) ולא הצטרף —
    דלוק ונראה למפעיל. הסבב ממתין לו כמו קודם, בלי WoL נוסף ובלי כשל."""
    ctx, anon, woken = room_server["ctx"], room_server["anon"], room_server["woken"]
    clock, _ = _wave1_partial(room_server, monkeypatch)

    clock.now += timedelta(seconds=STEP + 1)
    room_mod.sweep(ctx.conn, ctx.store)
    cloner_hello(anon, CLONER1, serials, joining=joining)
    assert not ctx.store.members(_round(ctx)["wave_session_id"]), "הצטרף — זה לא המקרה הנבדק"
    after = _packets_to(woken, CLONER1)

    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        room_mod.sweep(ctx.conn, ctx.store)

    assert _packets_to(woken, CLONER1) == after, "WoL ממשיך למכונה שכבר חזרה"
    assert _round(ctx)["state"] == "active"
    assert not _events(ctx, "room_wake_failed")


def test_only_a_cloner_with_something_left_to_write_is_woken_again(room_server, monkeypatch):
    """שני משכפלים בסבב: האחד סיים את כל מגירותיו — אין לו מה לכתוב בגל 2,
    ולכן הוא אינו מוער שוב ואינו מפיל את הסבב; רק זה שנשארה לו מגירה."""
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, _ = _wave1_partial(room_server, monkeypatch,
                              drawers={CLONER1: ["S1", "S2"], CLONER2: ["S3"]}, target=3)
    before1, before2 = _packets_to(woken, CLONER1), _packets_to(woken, CLONER2)

    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        room_mod.sweep(ctx.conn, ctx.store)

    assert _packets_to(woken, CLONER1) - before1 == CAP
    assert _packets_to(woken, CLONER2) == before2, "הוער משכפל שכל מגירותיו כבר נכתבו"
    reason = _round(ctx)["failed_reason"] or ""
    assert "shich-1" in reason and "shich-2" not in reason, reason
