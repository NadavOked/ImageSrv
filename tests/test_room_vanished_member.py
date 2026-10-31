"""‏#450 — מכונה שנעלמה אינה תוקעת את הגל לנצח.

‏`tick` סיים גל רק כשלכל חבר רשום היה מצב סופי, ולא הפקיע חברים שקטים.
מכונה אחת שכובתה, איבדה רשת או קרסה באמצע — בלי דיווח סופי — מנעה
פתיחת גל נוסף, גם אחרי שכל השורדים סיימו. זה תקוע כבר ב-N=1.

התיקון: ‏`tick` מפקיע חבר שלא דיווח **progress** מעל `LOST_SECONDS`
ומסמן אותו `lost` — מצב נבדל מ-`failed` (לא דיווח כישלון) ומ-`done`
(אין ראיה שכתב). הדופק הנמדד הוא progress ולא hello, אחרת מכונה בריאה
שעסוקה בכתיבה (שמפסיקה לשלוח hello, #449) הייתה מופקעת בטעות — ולכן
שני טסטים: אחד שהגל מתקדם על מכונה שנעלמה, ואחד שהוא **אינו** מתקדם על
מכונה שעדיין מדווחת.

הערה על #456: הטסטים מריצים את `tick` דרך hello של מכונה שורדת
(‏`heartbeat`). מי שמריץ את `tick` כשהחדר **שקט לגמרי** הוא #456
(‏tick מרקע / overview), ואינו נגוע כאן.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.rounds import room as room_mod

from test_server_room import (           # noqa: F401 — ‏room_server הוא fixture
    CLONER1, CLONER2, cloner_hello, room_body, heartbeat, report, room, room_server,
)

pytest.importorskip("fastapi")

#: הזקנה מעל הספָּף. ‏getattr עם ברירת מחדל, כדי שהבקרה השלילית (קוד ללא
#: ‏`LOST_SECONDS`) תריץ את ההתנהגות ותיכשל על ה-assert, ולא על מחסור סמל.
_AGE = getattr(room_mod, "LOST_SECONDS", 180) + 60


# --- עזרים -------------------------------------------------------------------


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


def _age_started(ctx, wave: str, seconds: int) -> str:
    """מזקין את `started_at` של הגל — מדמה גל שרץ כבר `seconds` שניות."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(
        timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (old, wave))
    ctx.conn.commit()
    return old


def _member_state(ctx, wave: str, mac: str) -> tuple[str, int]:
    row = ctx.conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (wave, mac),
    ).fetchone()
    return row["state"], row["done"]


def _events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]


# --- הבקרה השלילית: מכונה שנעלמה → הגל מתקדם ---------------------------------


def test_a_vanished_member_is_marked_lost_and_the_wave_advances(room_server):
    """‏#450: אחד מסיים, השני נהרג ב-SIGKILL בלי לדווח.

    היום הגל נשאר `running` לנצח ואין גל שני. אחרי: השני מסומן `lost`,
    הגל מסתיים, וגל 2 נפתח (היעד לא הושג). הטסט נופל אם החדר נשאר תקוע.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    # יעד 4 = שתי מכונות × שתי מגירות. מכונה שנעלמה משאירה 2 < 4 כתובות,
    # ולכן הגל הבא **חייב** להיפתח — וזה מה שהיום לא קורה.
    _open(room_server, target=4)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"

    # CLONER1 מסיים; CLONER2 מתחיל לכתוב ואז נעלם — לא מדווח שוב.
    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done"})
    _progress_writing(anon, wave1, CLONER2, ["sda", "sdb"])

    # הגל רץ מעל הספָּף ו-CLONER2 שקט כל אותו זמן: מזקינים את שתי החותמות
    # שההפקעה נמדדת מהן (‏`started_at` של הגל ו-`updated_at` של החבר).
    old = _age_started(ctx, wave1, _AGE)
    ctx.conn.execute(
        "UPDATE session_members SET updated_at = ? WHERE session_id = ? AND mac = ?",
        (old, wave1, CLONER2))
    ctx.conn.commit()

    heartbeat(room_server)          # הדופק של CLONER1 השורד מריץ את tick

    state, done = _member_state(ctx, wave1, CLONER2)
    assert state == "lost", "המכונה שנעלמה לא הוכרזה אבודה — הגל תקוע"
    assert done == 0, "`lost` אינו הצלחה — אין ראיה שכתב"

    view = room(deploy)["round"]
    assert view is not None, "הסבב נסגר, אף שהיעד (4) לא הושג"
    assert view["written_drives"] == 2, "נספרו רק המגירות שהושלמו בפועל"
    assert view["wave_number"] == 2, "הגל לא התקדם — החדר נשאר תקוע"
    assert view["wave_state"] == "open"
    assert "client_lost" in _events(ctx), "האובדן לא נרשם ביומן (עיקרון 5)"


# --- שומר תיקון-היתר: מכונה שעדיין כותבת אינה אבודה --------------------------


def test_a_member_still_reporting_progress_is_not_lost(room_server):
    """‏#449 בשכבת ההפקעה: מכונה שכותבת מפסיקה לשלוח hello אך מדווחת
    progress. סף שנמדד לפי hello (או לפי גיל הגל בלבד) היה מפקיע מכונה
    בריאה. גל ישן + progress טרי חייבים להשאיר את החבר `writing`.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    _open(room_server, target=4)
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"

    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done"})

    # הגל רץ הרבה זמן (‏`started_at` ישן), אבל CLONER2 מדווח התקדמות עכשיו.
    _age_started(ctx, wave1, _AGE)
    _progress_writing(anon, wave1, CLONER2, ["sda", "sdb"])   # updated_at = עכשיו

    heartbeat(room_server)

    state, _ = _member_state(ctx, wave1, CLONER2)
    assert state == "writing", "מכונה שמדווחת התקדמות הוכרזה אבודה בטעות"

    view = room(deploy)["round"]
    assert view["wave_number"] == 1, "הגל התקדם למרות שחבר עדיין כותב"
    assert view["wave_state"] == "running"
    assert "client_lost" not in _events(ctx)
