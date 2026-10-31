"""‏#984 — WoL למחשב בנייה ולמשכפל **בודד** מהקונסולה.

עד כאן WoL מהקונסולה היה מסלול אחד: ‏`POST /api/console/room/wake`
(‏`room.py`), שמעיר את כל ‏`grp_CLONERS`. כאן שני המסלולים שנדב הכריע
עליהם (17/09): מכונה אחת לפי MAC, וקבוצת הבנייה כולה.

**תחנות כיתה הן v2.** המסלול מסרב להן ב-403 במקום "לפתוח כי זה אותו
קוד" — ההכרעה היא על מי מתעורר מהקונסולה, לא על מה המודול יודע לשלוח.

התשובה זהה ל-`/room/wake`: ‏`{sent, failed, reasons}` — ‏`sent` ולא
‏`woken`, כי חבילת WoL היא UDP בלי ACK ואין ראיה שהמכונה קמה (#528).
השולח מוזרק מ-`app.py` (‏`wol_send`) כמו בחדר, כדי שהטסטים לא ישדרו.

‏#977 §4: גם "אתחול מרחוק" למכונה אחת יושב כאן — אותה בדיקת תפקיד (כיתה = v2)
וערוץ הבקשות של כיבוי החדר (‏`room_console.request_power`).
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from server.console import auth
from server.agent import held
from server.rounds import room_console
from server.net import wol
from server.agent.api import ServerContext
from server.db import journal
from server.agent.registry import normalize_mac
from server.rounds.room import ROOM_OPERATOR_ROLES, visible_round

#: מי מתעורר מהקונסולה ב-v1 — מחשבי בנייה ושיכפול. ‏classroom = v2.
WAKEABLE_ROLES = ("build", "cloner")
CLASSROOM_V2 = "WoL לתחנות כיתה — v2"
CLASSROOM_REBOOT_V2 = "אתחול מרחוק לתחנות כיתה — v2"


def _result(sent: wol.WakeResult) -> dict:
    # ‏getattr — שולח מוזרק בטסטים עשוי להחזיר int רגיל (כמו ב-room.py).
    return {"sent": int(sent),
            "failed": len(getattr(sent, "failed", ())),
            "reasons": list(getattr(sent, "reasons", ()))}


def create_wake_router(
    ctx: ServerContext, send: Callable[[bytes], None] | None = None,
) -> APIRouter:
    """‏`send` מוזרק מ-app.py (‏`rt.wol_send`) — אותו שולח כמו בחדר.
    ‏None = ברירת המחדל של המודול (שידור לפי טבלת הניתוב)."""
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)
    send_kw = {"send": send} if send else {}

    def room_operator(user=Depends(current_user)) -> tuple[str, str]:
        """אותה רשימת-היתר כמו `/room/wake` (‏`room.py`): תפקיד שלישי
        אינו מקבל את הכפתור ביום היוולדו."""
        if user[1] not in ROOM_OPERATOR_ROLES:
            journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.post("/machines/{mac}/wake")
    def wake_machine(mac: str, user=Depends(room_operator)):
        canonical = normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        row = ctx.conn.execute(
            "SELECT g.role FROM machines m JOIN groups g ON g.id = m.group_id"
            " WHERE m.mac = ?", (canonical,)).fetchone()
        if row is None:
            raise HTTPException(404, "מכונה לא רשומה")
        if row["role"] not in WAKEABLE_ROLES:
            raise HTTPException(403, CLASSROOM_V2)
        sent = wol.wake_machine(ctx.conn, canonical, **send_kw)
        if sent:
            journal(ctx.conn, "wol_sent", f"{canonical} count={int(sent)}", user[0])
        return _result(sent)

    @router.post("/machines/{mac}/reboot")
    def reboot_machine(mac: str, body: dict, user=Depends(admin_only)):
        """‏#977 §4: "אתחול מרחוק" ממגירת המחשב — **בקשה** שנוסעת ב-hello הבא
        של המכונה (‏`power_action: "reboot"`), באותו ערוץ כמו כיבוי החדר
        (‏`room_console.py`). הסוכן מאתחל רק כשהוא ממתין (‏`poll_sleep`), ולכן
        מכונה שעובדת מסורבת כאן — "תאותחל כשתסיים" אינו מה שהמפעיל ביקש.
        ‏`confirm_name` = שם המחשב: הקונסולה שולחת אותו מחלון האישור (#1270),
        והוא שומר ה-API מפני קריאה עיוורת — כמו `POST /monitor/{mac}/power`."""
        canonical = normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        row = ctx.conn.execute(
            "SELECT m.suffix, m.group_id, g.role FROM machines m"
            " JOIN groups g ON g.id = m.group_id WHERE m.mac = ?", (canonical,)).fetchone()
        if row is None:
            raise HTTPException(404, "מכונה לא רשומה")
        if row["role"] not in WAKEABLE_ROLES:
            raise HTTPException(403, CLASSROOM_REBOOT_V2)
        if body.get("confirm_name") != row["suffix"]:
            raise HTTPException(400, "השם שנשלח אינו שם המחשב")
        if ctx.conn.execute(
                "SELECT 1 FROM tasks WHERE mac = ? AND state IN ('pending', 'running')",
                (canonical,)).fetchone():
            raise HTTPException(409, "למחשב יש משימה פתוחה — בטלו אותה לפני אתחול")
        shown = visible_round(ctx.conn)
        if (row["role"] == "cloner" and shown is not None   # #1554: כל קבוצת משכפלים
                and shown["state"] in ("active", "closing")):
            raise HTTPException(409, "יש סבב חדר פעיל — עצרו אותו לפני אתחול משכפל")
        # ‏#1367 (נדב 14/10): אושר מעל אזהרת "באמצע שיכפול מקומי" — רק `true` ממש
        confirmed = body.get("confirm_local_copy") is True
        count = room_console.request_power(ctx.conn, [canonical], room_console.REBOOT, user[0],
                                           confirmed=confirmed)
        journal(ctx.conn, "machine_reboot",
                canonical + (" local copy confirmed" if confirmed else ""), user[0])
        return {"requested": count, "ttl_s": room_console.POWEROFF_TTL_SECONDS}

    @router.post("/machines/{mac}/poweroff")
    def poweroff_held(mac: str, body: dict, user=Depends(room_operator)):
        """‏#2031: "כבה" למשכפל שמחזיק את התוצאות עד לחיצה (#2024) — **רק** כשהוא
        מוחזק עכשיו (דיווח held טרי); לכיבוי כללי יש את כיבוי החדר. בקשה בערוץ של
        ‏#980 — נמסרת בתשובת הדיווח held, והסוכן מכבה דרך `safe_poweroff`
        (‏`arm_wol`). אין כתיבה פעילה במוחזק, ולכן אין סירוב בזמן סבב. ‏`confirm_name`
        = שם המחשב, מחלון האישור (#1270)."""
        canonical = normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        row = ctx.conn.execute(
            "SELECT m.suffix, g.role FROM machines m"
            " JOIN groups g ON g.id = m.group_id WHERE m.mac = ?", (canonical,)).fetchone()
        if row is None:
            raise HTTPException(404, "מכונה לא רשומה")
        if body.get("confirm_name") != row["suffix"]:
            raise HTTPException(400, "השם שנשלח אינו שם המחשב")
        if not held.is_held(ctx.conn, canonical):
            raise HTTPException(409, "המחשב אינו מחכה ללחיצה על 'כבה' — כיבוי מכאן רק"
                                     " למשכפל שמחזיק תוצאות של דיסק עם בעיה")
        count = room_console.request_power(ctx.conn, [canonical], room_console.POWEROFF, user[0])
        journal(ctx.conn, "held_poweroff", canonical, user[0])
        return {"requested": count, "ttl_s": room_console.POWEROFF_TTL_SECONDS}

    @router.post("/groups/{gid}/wake")
    def wake_group(gid: str, user=Depends(room_operator)):
        row = ctx.conn.execute(
            "SELECT role FROM groups WHERE id = ?", (gid,)).fetchone()
        if row is None:
            raise HTTPException(404, "קבוצה לא קיימת")
        if row["role"] not in WAKEABLE_ROLES:
            raise HTTPException(403, CLASSROOM_V2)
        sent = wol.wake_group(ctx.conn, gid, **send_kw)
        if sent:
            journal(ctx.conn, "wol_sent", f"{gid} count={int(sent)}", user[0])
        return _result(sent)

    return router
