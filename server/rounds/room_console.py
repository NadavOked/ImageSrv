"""‏#980 — שתי פעולות של אובייקט "מחשבי שיכפול" בקונסולה שלא היה להן מקור.

1. **כיבוי כל המשכפלים** (‏`POST /api/console/room/poweroff`). **דרך הסוכן,
   לא `poweroff -f` מהשרת** (הכרעת נדב; ‏#587): מכונה שכובתה בלי
   ‏`arm_wol` אינה מתעוררת ב-WoL. השרת רק **רושם בקשה** לכל משכפל; הבקשה
   יוצאת בתשובת ה-hello הבא של המכונה (‏`power_action`, סעיף 3) — אותו
   hello באותו קצב, בלי polling חדש — והסוכן מכבה באותו מסלול כמו כיבוי
   ההמתנה (‏`idle.sh`: ‏`arm_wol` נקרא בחזרה, ורק אז `poweroff -f`).
   בקשה נמסרת **פעם אחת** ופגה אחרי `POWEROFF_TTL_SECONDS`: מכונה שהייתה
   כבויה בזמן הלחיצה ועולה מחר אינה נכבית ברגע שהיא עולה.
2. **היסטוריית סבבי החדר** (‏`GET /api/console/room/history`) — מטבלת
   ‏`room_rounds`, שכבר שומרת כל סבב.

**לא בראוטר של `room.py`**: הוא נכנס בשלמותו לקיוסק (`kiosk.py`), וכיבוי
החדר הוא פעולת ניהול שאין לה מקום על וילן ההפצה.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from server.console import auth
from server.learn import learn_waits
from server.disks import smart_ask
from server.db import _write_lock, journal, now_iso, writing
from server.agent.registry import ROOM_GROUPS
from server.rounds.room import CLONERS_GROUP, ROOM_OPERATOR_ROLES, round_label, visible_round

#: מה שהשרת מבקש מסוכן דרך ה-hello. ‏reboot — "אתחול מרחוק" ממגירת המחשב
#: (‏#977 §4, ‏`wake_api.py`): הצרכן שחסר כש-#980 פתח רק את הכיבוי.
POWEROFF = "poweroff"
REBOOT = "reboot"
#: כמה זמן בקשת כיבוי ממתינה ל-hello של המכונה. ה-hello של משכפל ממתין
#: מגיע לכל המאוחר כל `POLL_MAX` (15 ש', ‏`agent/lib/core/poll.sh`); ‏120 ש' הם
#: שמונה פעימות — ומעבר לזה מכונה שלא ענתה אינה "תכבה אחר כך".
POWEROFF_TTL_SECONDS = 120
HISTORY_DEFAULT = 10
HISTORY_MAX = 50


def _cloners_label(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT label FROM groups WHERE id = ?", (CLONERS_GROUP,)).fetchone()
    return row["label"] if row else None


def request_power(conn: sqlite3.Connection, macs: list[str], action: str,
                  user: str, confirmed: bool = False) -> int:
    """בקשה אחת לכל MAC (בקשה קודמת שעוד ממתינה — נדרסת). מחזיר כמה נרשמו.

    ‏#1367 (נדב 14/10): ``confirmed`` = המפעיל אישר מעל האזהרה "המחשב באמצע
    שיכפול מקומי". רק בקשה כזו מכבה מכונה שכותבת; בלעדיו הסוכן זורק אותה."""
    at = now_iso()
    with _write_lock, writing(conn):
        for mac in macs:
            conn.execute(
                "INSERT INTO power_requests (mac, action, requested_at, requested_by, confirmed)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT (mac) DO UPDATE SET"
                " action = excluded.action, requested_at = excluded.requested_at,"
                " requested_by = excluded.requested_by, confirmed = excluded.confirmed",
                (mac, action, at, user, 1 if confirmed else 0))
    return len(macs)


def take_power_action(conn: sqlite3.Connection, mac: str,
                      now: datetime | None = None) -> str | None:
    """הפעולה בלבד (‏`take_power`), לקוראים שלא צריכים את האישור."""
    return take_power(conn, mac, now)[0]


def take_power(conn: sqlite3.Connection, mac: str,
               now: datetime | None = None) -> tuple[str | None, bool]:
    """מה שה-hello של `mac` נושא ב-`power_action`: ‏`"poweroff"`, ‏`"reboot"` או None.

    הבקשה נמחקת בקריאה — נמסרת פעם אחת. סוכן שלא הצליח לדרוך WoL נשאר
    דולק ואומר זאת, ואינו מקבל את הבקשה שוב בכל פעימה. בקשה שפגה נמחקת
    ואינה נמסרת (ראו `POWEROFF_TTL_SECONDS`)."""
    row = conn.execute(
        "SELECT action, requested_at, confirmed FROM power_requests WHERE mac = ?",
        (mac,)).fetchone()
    if row is None:
        return None, False
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM power_requests WHERE mac = ?", (mac,))
    try:
        at = datetime.fromisoformat(row["requested_at"])
    except (TypeError, ValueError):
        return None, False                # חותמת שאינה נקראת — לא מכבים
    age = (now or datetime.now(timezone.utc)) - at
    if age > timedelta(seconds=POWEROFF_TTL_SECONDS) or age < timedelta(0):
        journal(conn, "power_expired", f"{mac} {row['action']}")
        return None, False
    confirmed = bool(row["confirmed"])
    journal(conn, "power_delivered",
            f"{mac} {row['action']}" + (" (confirmed over a local copy)" if confirmed else ""))
    if row["action"] == POWEROFF:
        _leave_open_wave(conn, mac)
    return row["action"], confirmed


def _leave_open_wave(conn: sqlite3.Connection, mac: str) -> None:
    """‏#65: מכונה שנאמר לה לכבות יוצאת מהגל **הפתוח** — באותו hello שמוסר
    את הכיבוי (ה-hello הזה עצמו כבר צירף אותה מחדש). אחרת המגירות שדיווחה
    לפני שכבתה נספרות ב-`ready_drives`, ו"התחל בכל זאת" היה משגר גל עם
    מכונה כבויה. כשתעלה אחרי החלפת המגירות — ה-hello שלה מצרף אותה שוב.
    גל שכבר רץ אינו נוגע כאן: לא מכבים מכונה באמצע כתיבה (‏`/room/poweroff`
    ו-`/missing/{mac}/poweroff` מסרבים לזה)."""
    with _write_lock, writing(conn):
        conn.execute(
            "DELETE FROM session_members WHERE mac = ? AND session_id IN"
            " (SELECT id FROM sessions WHERE state = 'open')", (mac,))


def round_history(ctx, limit: int) -> list[dict]:
    """סבבי חדר שהסתיימו (‏`closed`/`failed`), מהחדש לישן. הסבב שמוצג
    עכשיו ב-`GET /room` (פעיל, או כושל שעוד לא נסגר) אינו כאן."""
    shown = visible_round(ctx.conn)
    rows = ctx.conn.execute(
        "SELECT * FROM room_rounds WHERE state IN ('closed', 'failed') AND id != ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (shown["id"] if shown is not None else "", limit)).fetchall()
    return [{
        "id": r["id"],
        "state": r["state"],
        "image_id": r["image_id"],
        "image_name": round_label(ctx, r),
        "source_kind": r["source_kind"],
        "target_drives": r["target_drives"],
        "written_drives": r["written_drives"],
        "opened_by": r["opened_by"],
        "created_at": r["created_at"],
        "closed_at": r["closed_at"],
        "failed_reason": r["failed_reason"],
        # ‏#1889: כמה זמן הסבב חיכה לאדם, ועל מה (‏`learn_waits.round_waits`; ‏null = לא נמדד).
        "waits": learn_waits.round_waits(ctx.conn, r["created_at"], r["closed_at"]),
    } for r in rows]


def create_room_console_router(ctx) -> APIRouter:
    router = APIRouter(prefix="/api/console/room")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/history")
    def history(limit: int = HISTORY_DEFAULT, user=Depends(current_user)):
        # קריאה בלבד — פתוחה לכל מחובר, כמו `GET /room`.
        if limit < 1:
            raise HTTPException(400, "limit חייב להיות לפחות 1")
        return round_history(ctx, min(limit, HISTORY_MAX))

    @router.post("/answer")
    async def answer_question(request: Request, user=Depends(current_user)):
        """‏#989 ס׳ 2: תשובת מפעיל לשאלת ה-SMART של משכפל — מהקונסולה.
        התשובה הראשונה גוברת (ליד המכונה או כאן); השנייה מקבלת 409
        "כבר נענה ע"י …". מפעיל סבבים בלבד, כמו שאר פעולות החדר."""
        if user[1] not in ROOM_OPERATOR_ROLES:
            journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        if not isinstance(body, dict):
            body = {}
        mac = body.get("mac")
        known = isinstance(mac, str) and ctx.conn.execute(
            f"SELECT 1 FROM machines WHERE mac = ? AND group_id IN {ROOM_GROUPS}", (mac,)).fetchone()
        if not known:
            raise HTTPException(404, "מחשב שיכפול לא מוכר")
        try:
            return smart_ask.answer_from_console(
                ctx.conn, mac, body.get("nonce"), body.get("answer"), user[0])   # #1732: red is never written
        except smart_ask.AskError as exc:
            raise HTTPException(exc.status, exc.detail)

    @router.post("/poweroff")
    async def poweroff(request: Request, user=Depends(admin_only)):
        # גוף ריק או לא-JSON נופל לאישור ריק — 400, כמו `/room/close`.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        label = _cloners_label(ctx.conn)
        if label is None or typed != label:
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם קבוצת מחשבי השיכפול")
        shown = visible_round(ctx.conn)
        if shown is not None and shown["state"] in ("active", "closing"):
            # כיבוי באמצע גל מפיל כתיבה חיה — הסבב נעצר קודם, בהקלדה משלו.
            raise HTTPException(409, "יש סבב חדר פעיל — עצרו אותו לפני כיבוי החדר")
        confirmed = isinstance(body, dict) and body.get("confirm_local_copy") is True
        macs = [r["mac"] for r in ctx.conn.execute(
            f"SELECT mac FROM machines WHERE group_id IN {ROOM_GROUPS}")]   # #1554
        count = request_power(ctx.conn, macs, POWEROFF, user[0], confirmed=confirmed)
        journal(ctx.conn, "room_poweroff", f"requested={count}" +
                (" local copy confirmed" if confirmed else ""), user[0])
        return {"requested": count, "ttl_s": POWEROFF_TTL_SECONDS}

    return router
