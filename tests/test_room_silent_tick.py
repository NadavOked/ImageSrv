"""‏#456 + #659 — גל שאיש אינו צופה בו חייב להתקדם.

‏#450 נתן ל-`tick` את היכולת להפקיע חבר שנעלם (`_mark_lost`) — **בהינתן
ש-`tick` רץ**. אבל `tick` מקודם היום רק מ-hello של משכפל (`room.pulse`)
ומ-GET `/overview`. שני אלה תלויים במשהו חיצוני: מכונה חיה ששולחת hello,
או אדם שמסתכל על מבט-העל. מסך החדר עצמו מושך `/api/console/room` שהוא
read-only (#446/#453 — ותרחיש זה מאומת ב-`test_room_observation`).

לכן, כשכל המכונות נעלמו (כובו, נותקו, קרסו), אין יותר hello, ואף אחד
אינו מריץ `tick`: אף חבר לא מוכרז אבוד, `_finish_wave` לא רץ, והגל תקוע
לנצח. זה #456; זה גם הזנב שנשאר פתוח ב-#659 כשהמכונה **האחרונה** מאבדת
חשמל (למכונה שורדת אחת, #450 כבר סוגר אותו — ראה
`test_room_vanished_member`).

ההכרעה (הערת נדב על #456, אפשרות A): שעון-רקע דרך `lifespan` של FastAPI
מריץ את הדופק בקצב קבוע, בלתי-תלוי בצופה או ב-hello. הכניסה שהלולאה
קוראת לה היא `room.sweep`; הבדיקות ההתנהגותיות מריצות אותה ישירות (דגימה
דטרמיניסטית, בלי המתנה לטיימר), ובדיקה נפרדת מאמתת שהלולאה באמת יורה.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from server.rounds import room as room_mod

from test_server_room import (           # noqa: F401 — fixtures/עזרים משותפים
    CLONER1, CLONER2, cloner_hello, room_body, heartbeat, report, room, room_server,
)

pytest.importorskip("fastapi")

#: זקנה מעל הסף. ‏getattr עם ברירת מחדל, כדי שהבקרה השלילית (מוטציה של
#: הקוד) תריץ את ההתנהגות ותיכשל על ה-assert, ולא על מחסור סמל.
_AGE = getattr(room_mod, "LOST_SECONDS", 180) + 60


def _open(room_server, target: int, drawers=None) -> None:
    """‏#1410: בחירה מפורשת — ברירת המחדל: כל מגירות שני המשכפלים."""
    opened = room_server["deploy"].post(
        "/api/console/room", json=room_body(room_server, target, drawers or {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    assert opened.status_code == 200, opened.text


def _progress_writing(client, session_id: str, mac: str, devs: list[str]) -> None:
    """דיווח `writing` לא-סופי — מזיז את `updated_at` בלי לסיים."""
    resp = client.post("/api/v1/agent/progress", json={
        "session_id": session_id, "mac": mac, "state": "writing",
        "targets": [{"dev": d, "bytes_written": 40, "bytes_total": 100,
                     "state": "writing"} for d in devs],
    })
    assert resp.status_code == 200, resp.text


def _age(ctx, wave: str, macs: list[str], seconds: int) -> None:
    """מזקין את שתי החותמות שההפקעה נמדדת מהן: `started_at` של הגל
    ו-`updated_at` של כל חבר — מדמה גל שרץ מזמן ומכונות ששקטו מזמן."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(
        timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (old, wave))
    for mac in macs:
        ctx.conn.execute(
            "UPDATE session_members SET updated_at = ? WHERE session_id = ? AND mac = ?",
            (old, wave, mac))
    ctx.conn.commit()


def _future(ctx, wave: str, macs: list[str], seconds: int) -> None:
    stamp = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (stamp, wave))
    for mac in macs:
        ctx.conn.execute(
            "UPDATE session_members SET updated_at = ? WHERE session_id = ? AND mac = ?",
            (stamp, wave, mac))
    ctx.conn.commit()


def _member_state(ctx, wave: str, mac: str) -> tuple[str, int]:
    row = ctx.conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (wave, mac),
    ).fetchone()
    return row["state"], row["done"]


def _events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]


# --- #456: חדר שקט לגמרי מתקדם משעון-הרקע -----------------------------------


def test_a_fully_silent_running_wave_advances_from_the_background_sweep(room_server):
    """שתי מכונות הצטרפו וכתבו; אחת סיימה ויצאה (אתחול), השנייה נעלמה באמצע.
    מכאן אין יותר hello משום מכונה — החדר שקט לגמרי.

    בלי שעון-הרקע אף אחד אינו מריץ `tick`: השנייה נשארת `writing`, הגל
    נשאר `running` לנצח, ואין גל שני. עם השעון (מיוצג כאן בקריאה ישירה
    ל-`room.sweep`, בלי hello ובלי GET שמניע): היא `lost`, הגל מסתיים,
    וגל 2 נפתח (2 נכתבו, היעד 4 לא הושג).

    ‏#1672 (נדב 17/10): גל שלא כתב **אף** מגירה מסיים את הסבב `failed` ואינו
    פותח גל נוסף — ולכן כאן גל שכתב חלק. הגל המת בנתיב השעון — הטסט הבא.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    _open(room_server, target=4)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)                       # ה-hello של ההצטרפות מפעיל את הגל
    assert room(deploy)["round"]["wave_state"] == "running"

    # שתיהן דיווחו כתיבה — ואז נעלמו. שום hello ושום progress מכאן והלאה.
    _progress_writing(anon, wave1, CLONER1, ["sda", "sdb"])
    _progress_writing(anon, wave1, CLONER2, ["sda", "sdb"])
    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done"})   # סיימה ויצאה
    _age(ctx, wave1, [CLONER2], _AGE)

    # שעון-הרקע יורה. **אין** heartbeat של שורד ו**אין** GET שמניע tick.
    assert room_mod.sweep(ctx.conn, ctx.store) is True

    state, done = _member_state(ctx, wave1, CLONER2)
    assert state == "lost", f"{CLONER2} לא הוכרז אבוד — החדר השקט נשאר תקוע"
    assert done == 0

    view = room(deploy)["round"]                  # קריאה = תצפית בלבד (#446)
    assert view is not None, "הסבב נסגר, אף שהיעד (4) לא הושג"
    assert view["written_drives"] == 2, "רק המגירות של המכונה שסיימה נכתבו"
    assert view["wave_number"] == 2, "הגל לא התקדם — החדר השקט נשאר תקוע"
    assert view["wave_state"] == "open"
    assert "client_lost" in _events(ctx), "האובדן לא נרשם ביומן (עיקרון 5)"


def test_future_wave_progress_is_rebased_neither_lost_nor_frozen(room_server):
    """#1771 + #1947: a stored stamp in the future -- a clock step this
    process did not see, e.g. one before its first sample after a boot --
    must not freeze the lost sweep (#1771). Until #1947 it was therefore
    "lost at once", and that is what failed a whole wave when the lab clock
    stepped back. Now it is rebased to now: a full LOST_SECONDS from now,
    and lost after that if the machine stays silent."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _open(room_server, target=4)
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)
    _progress_writing(anon, wave, CLONER1, ["sda", "sdb"])
    _progress_writing(anon, wave, CLONER2, ["sda", "sdb"])
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})
    _future(ctx, wave, [CLONER2], 3 * 60 * 60)

    assert room_mod.sweep(ctx.conn, ctx.store) is True
    assert _member_state(ctx, wave, CLONER2)[0] == "writing", "a future stamp lost a machine at once"
    stamp = ctx.conn.execute(
        "SELECT updated_at FROM session_members WHERE session_id = ? AND mac = ?",
        (wave, CLONER2)).fetchone()["updated_at"]
    assert datetime.fromisoformat(stamp) <= datetime.now(timezone.utc) + timedelta(seconds=5),         "the future stamp was left in the future -- the sweep would stay frozen"
    assert "clock_jump" in _events(ctx), "the step was not journaled"

    _age(ctx, wave, [CLONER2], _AGE)                 # then it really stays silent
    assert room_mod.sweep(ctx.conn, ctx.store) is True
    assert _member_state(ctx, wave, CLONER2)[0] == "lost"
    assert room(deploy)["round"] is not None


# --- #659: מקבל שמאבד חשמל לצמיתות אינו תוקע את הסבב -------------------------


def test_power_loss_while_writing_is_marked_terminal_so_the_wave_finishes(room_server):
    """‏#659: המכונה **האחרונה** מדווחת `writing` ואז מאבדת חשמל לצמיתות.

    זהו הזנב שנשאר פתוח אחרי #450: כשיש מכונה שורדת אחת, ה-hello שלה מריץ
    את `tick` ו-#450 מפקיע את המתה (מאומת ב-`test_room_vanished_member`).
    כאן אין שורד — המכונה היחידה מתה — ולכן ההפקעה מגיעה משעון-הרקע (#456).
    התוצאה שנדרשת ב-DoD של #659: החבר מסומן terminal אחרי הסף, `_finish_wave`
    רץ, והסבב מתקדם במקום להיתקע לנצח על מקבל שלא יחזור.

    ‏#1672 (נדב 17/10): כאן הגל לא כתב אף מגירה, ולכן "מתקדם" פירושו שהסבב
    נסגר `failed` ומשחרר את חריץ המולטיקאסט — לא גל 2 שממתין לנצח
    (`room_72308383` על ברזל). זה אותו מסלול כמו ב-`test_room_dead_wave_fails_round`,
    אבל בלי שורד: ההפקעה מגיעה רק משעון-הרקע.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    _open(room_server, target=2, drawers={CLONER1: ["S1", "S2"]})
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"

    _progress_writing(anon, wave1, CLONER1, ["sda", "sdb"])   # כותב — ואז נופל חשמל
    _age(ctx, wave1, [CLONER1], _AGE)

    assert room_mod.sweep(ctx.conn, ctx.store) is True

    state, done = _member_state(ctx, wave1, CLONER1)
    assert state == "lost", "המקבל שאיבד חשמל לא סומן terminal — הסבב תקוע"
    assert done == 0, "`lost` אינו הצלחה — אין ראיה שכתב"

    view = room(deploy)["round"]
    assert view is not None and view["state"] == "failed", (
        f"_finish_wave לא רץ — הסבב תקוע על מקבל מת: {view}")
    assert view["failed_reason"], "סבב שנכשל בלי סיבה — המפעיל לא יודע למה"
    open_waves = ctx.conn.execute(
        "SELECT id FROM sessions WHERE state IN ('open', 'running')").fetchall()
    assert not open_waves, "גל נשאר פתוח ותופס את החריץ אחרי שהסבב נכשל"
    assert "client_lost" in _events(ctx)
    assert "room_failed" in _events(ctx), "הכשל לא נרשם ביומן (עיקרון 5)"


# --- שומר-החיווט: הלולאה באמת יורה (עיקרון 5) --------------------------------


def test_the_room_clock_actually_fires_in_the_background(tmp_path, monkeypatch):
    """שעון שקיים אבל אינו מחובר נראה בדיוק כמו שעון שעובד (עיקרון 5).

    ‏`_room_clock` דרך `lifespan` חייב באמת לקרוא ל-`room.sweep` בקצב.
    ‏TestClient כמנהל-הקשר מריץ את ה-`lifespan`; מחליפים את `room.sweep`
    במונה, ומאמתים שהוא נקרא — בלי להמתין לטיימר האמיתי.
    """
    from fastapi.testclient import TestClient
    from conftest import Clock, write_image, MANIFEST_256
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)

    fired = threading.Event()
    calls = {"n": 0}
    real_sweep = room_mod.sweep

    def counting_sweep(conn, store):
        calls["n"] += 1
        fired.set()
        return real_sweep(conn, store)

    monkeypatch.setattr(room_mod, "sweep", counting_sweep)

    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080",
                     now_fn=Clock(), room_clock_interval=0.02)
    try:
        with TestClient(app):                 # ‏__enter__ מריץ את ה-lifespan
            assert fired.wait(timeout=5.0), "שעון-הרקע לא ירה — הלולאה אינה מחוברת"
    finally:
        app.state.ctx.sender.stop()

    assert calls["n"] >= 1
