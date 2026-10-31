"""ה-API של הקונסולה — מה שהדפדפן מדבר איתו.

RBAC לפי סעיף 11: משתמש deploy יכול לראות אימג'ים ולנהל סבב הפצה,
ותו לא. ניהול — קבוצות, טבלאות MAC, משתמשים, יומן, הגדרות — admin בלבד.
כל פעולה כותבת נרשמת ביומן עם שם המשתמש.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import shutil
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from server.console import auth, console_login, console_mfa, console_presence, users
from server.core import capabilities, hostname_rule
from server.library import minfree
from server.rounds import drive_rates, nic_speed, room
from server.agent import identity, inventory, probe, readbackhash, registry
from server.system import backup_target, timezone
from server.net import dhcp, ports
from server.disks import (crcwarn, disk_failures, drawer_slots, shrink_records, slot_names,
                          smart_history)
from server.storage import disk_reserve, storage_locations
from server.interserver import storage_nodes, transfer_cap
from server.agent.api import ServerContext
from server.db import _write_lock, get_setting, journal, now_iso, set_settings, update_one, writing
from server.library.images import restore_refusal, used_bytes_total
from server.library.imagefit import validate_expand_choice
from server.console.journal_he import EVENTS_HE, JournalTranslator, severity
from server.rounds.session_view import label as session_label
from server.rounds.sessions import SessionError
from server.rounds.station import ROUND_OPENER_ROLES
from server.agent import agent_timing
from server import auto_reboot
from server.net import igmp_querier
from server.console import console_disk_sets   # #1857
from server.agent import firmware_image   # #2080
from server.disks import model_slots   # #2001
from server.agent import held   # #2031

#: נדב 18/10: ניתוק אוטומטי בחוסר פעילות. השדה נשאר בשניות (‏#1243, ברירת מחדל 600);
#: ‏0 = כבוי (אין ניתוק — העוגייה עדיין פגה ב-auth.TTL_SECONDS, ו-logout עובד), אחרת
#: דקות שלמות 1–1440. הקונסולה מציגה ושולחת דקות × 60 — בשום מקום בממשק לא שניות.
IDLE_SETTING = "console_idle_seconds"
IDLE_MAX_MINUTES = 1440
IDLE_REFUSAL_HE = (f"ניתוק בחוסר פעילות: 0 (כבוי) או מספר שלם של דקות 1–{IDLE_MAX_MINUTES}"
                   f" (בשניות: 60–{IDLE_MAX_MINUTES * 60}, כפולה של 60)")


def _idle_seconds(raw) -> int | None:
    """הערך שנשמר, או None לסירוב: 0, או כפולה של 60 בין 60 ל-1440×60."""
    if isinstance(raw, bool):
        return None
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None
    if n == 0 or (60 <= n <= IDLE_MAX_MINUTES * 60 and n % 60 == 0):
        return n
    return None


WRITE_SETTINGS = {"recovery_require_login", "session_wait_seconds",
                  # נדב 18/10: חיוב MFA למנהלים — כבוי כברירת מחדל.
                  console_mfa.SETTING,
                  "console_idle_seconds", "class_deploy_enabled", "cloner_beep",
                  # ‏#748: כפתור "עדכן" — כבוי כברירת מחדל (נדב, 16/09).
                  "update_enabled",
                  # ‏#855: שומר הזהות (MAC + חכירת DHCP). חסר = דלוק; "false"
                  # מפורש בלבד מכבה, ו-`setting_change` רושם מי.
                  identity.SETTING,
                  # ‏#447: תקרת הקצב הנלמדת. ‏"false" — התקרה הקבועה בלבד.
                  drive_rates.SETTING,
                  # ‏#1366: סף CRC מוחלט — מעליו המגירה כתומה (0 = כבוי).
                  crcwarn.SETTING,
                  # ‏#1354: מינימום GB פנויים במחיצת המערכת אחרי שחזור (0–500).
                  minfree.SETTING,
                  # ‏#1473: תקרת קצב להעברות בין אתרים, Mb/s (0 = ללא הגבלה).
                  transfer_cap.SETTING,
                  # ‏#1864 (נדב 20/10): אימות מלא אחרי הכתיבה; כבוי = מדגם.
                  readbackhash.SETTING,
                  # ‏#1972 (נדב 22/10): השרת כ-IGMP querier ברשת ההפצה. חסר = דלוק.
                  igmp_querier.SETTING,
                  # ‏#2056/#2059 (מוקאפ 05, נדב 24/10): זמני ההמתנה והכיבוי האוטומטי.
                  *agent_timing.KEYS,
                  auto_reboot.MODE, auto_reboot.TIME, auto_reboot.WEEKDAY}

BOOLEAN_SETTINGS = {
    "recovery_require_login": "שחזור מהשרת בלי סבב דורש כניסה",
    console_mfa.SETTING: "חיוב אימות דו-שלבי למנהלים",
    "class_deploy_enabled": "הפצה לכיתה ממסך התחנה",
    "cloner_beep": "צליל בסיום סבב שיכפול",
    "update_enabled": "עדכון גרסה מהקונסולה",
    identity.SETTING: "אימות מחשבים לפי כתובת ה-DHCP",
    drive_rates.SETTING: "קצב שידור אוטומטי לפי הכוננים בחדר",
}


def _setting_integer(raw: object, label: str, minimum: int,
                     maximum: int | None = None) -> str:
    """Canonical decimal integer accepted by the console's number controls."""
    if isinstance(raw, bool):
        value = None
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and re.fullmatch(r"0|-?[1-9][0-9]*", raw):
        value = int(raw)
    else:
        value = None
    if value is None or value < minimum or (maximum is not None and value > maximum):
        limit = f"{minimum}–{maximum}" if maximum is not None else f"{minimum} לפחות"
        raise HTTPException(400, f"{label}: מספר שלם, {limit}")
    return str(value)


def _validate_write_settings(body: dict) -> dict[str, str]:
    """Validate and normalize every generic writable setting before any write."""
    normalized: dict[str, str] = {}
    for key, value in body.items():
        if key in BOOLEAN_SETTINGS:
            if isinstance(value, bool):     # JSON true/false מלקוח API -- כמו "true"/"false" של הקונסולה
                value = "true" if value else "false"
            if value not in ("true", "false"):
                raise HTTPException(400, f"{BOOLEAN_SETTINGS[key]}: נדרש true או false")
            normalized[key] = value
        elif key == "session_wait_seconds":
            normalized[key] = _setting_integer(value, "המתנה מהמצטרף האחרון", 30)
        elif key == IDLE_SETTING:
            idle = _idle_seconds(value)
            if idle is None:
                raise HTTPException(400, IDLE_REFUSAL_HE)
            normalized[key] = str(idle)
        elif key == crcwarn.SETTING:
            limit = crcwarn.parse(value)
            if limit is None:
                raise HTTPException(400, f"סף CRC: מספר שלם 0–{crcwarn.MAXIMUM}")
            normalized[key] = str(limit)
        elif key == minfree.SETTING:
            gb = minfree.parse_gb(value)
            if gb is None:
                raise HTTPException(
                    400, f"מינימום מקום פנוי: מספר שלם 0–{minfree.MIN_FREE_GB_MAX}")
            normalized[key] = str(gb)
        elif key == transfer_cap.SETTING:
            mbps = transfer_cap.parse(value)
            if mbps is None:
                raise HTTPException(
                    400, f"הגבלת קצב בין אתרים: מספר שלם 0–{transfer_cap.MAXIMUM} (Mb/s)")
            normalized[key] = str(mbps)
        elif key == readbackhash.SETTING:
            full = readbackhash.parse_setting(value)
            if full is None:
                raise HTTPException(400, "אימות מלא אחרי הכתיבה: true או false")
            normalized[key] = full
        elif key == igmp_querier.SETTING:
            querier = igmp_querier.parse_setting(value)
            if querier is None:
                raise HTTPException(400, "IGMP querier: true או false")
            normalized[key] = querier
        elif key in agent_timing.KEYS:
            seconds = agent_timing.parse(key, value)
            if seconds is None:
                _default, low, high = agent_timing.RANGES[key]
                raise HTTPException(400, f"{agent_timing.LABELS[key]}: מספר שלם של שניות, {low}–{high}")
            normalized[key] = str(seconds)
        elif key == auto_reboot.MODE:
            if not isinstance(value, str) or value not in auto_reboot.MODES:
                raise HTTPException(400, "הפעלה מחדש אוטומטית: מצב לא מוכר")
            normalized[key] = value
        elif key == auto_reboot.TIME:
            if auto_reboot.parse_time(value) is None:
                raise HTTPException(400, "שעת הפעלה מחדש: נדרש HH:MM")
            normalized[key] = value
        elif key == auto_reboot.WEEKDAY:
            day = auto_reboot.parse_weekday(value)
            if day is None:
                raise HTTPException(400, "יום הפעלה מחדש: מספר שלם 0–6")
            normalized[key] = str(day)
        else:  # WRITE_SETTINGS and this validator must stay exhaustive together.
            raise RuntimeError(f"missing settings validator: {key}")
    return normalized

#: #406: השדות שעריכת מכונה מכירה. שדה מחוץ לרשימה = טעות של הקורא,
#: והוא נדחה ב-400 במקום להיבלע ולהחזיר ``{"ok": True}`` שלא שינה כלום.
EDIT_MACHINE_FIELDS = {"name", "group_id", "drawer_count", "drawer_slots", "mac",   # mac: #977 §5
                       "nic_speed_mbps"}   # 15/10: מהירות כרטיס ידנית, null = אוטומטי
#: ‏#1127: שדה טקסט חופשי (שם קבוצה/מכונה) — תקרת אורך, כי הוא נכנס ליומן,
#: לתפריט GRUB ולמסך; בלי תקרה שורה של מגה-בייט היא DoS שקט על הקונסולה.
TEXT_FIELD_MAX = 100


def _text_field_ok(value: str, what: str) -> None:
    from fastapi import HTTPException  # noqa: PLC0415 — כמו שאר הקובץ
    if len(value) > TEXT_FIELD_MAX:
        raise HTTPException(400, f"{what} ארוך מדי ({len(value)} תווים, המקסימום {TEXT_FIELD_MAX})")


#: הכרעת נדב 11/10: כיתות כבויות במהדורה = השרת מסרב, לא רק הממשק מסתיר.
CLASSROOMS_OFF = "כיתות אינן במהדורה זו (v1.2) — השרת אינו יוצר כיתה ואינו פותח סבב כיתה"


def refuse_if_classrooms_off() -> None:
    """‏409 בשם כש-`capabilities.classrooms()` כבוי. נקרא **אחרי** ההזדהות
    (ה-Depends של הנתיב) — כך deploy בקונסולה עדיין מקבל את ה-403 שלו."""
    from fastapi import HTTPException  # noqa: PLC0415 — כמו שאר הקובץ
    if not capabilities.classrooms():
        raise HTTPException(409, CLASSROOMS_OFF)


def _csv_cell(value):
    """‏#1008: תא בייצוא היומן. טקסט שמתחיל ב-``= + - @`` (או טאב/CR) מקבל
    ``'`` בראשו, כדי שגיליון אלקטרוני לא יריץ אותו כנוסחה — ‏``login_failed``
    נושא את השם שהוקלד במסך הכניסה, כלומר טקסט של מי שלא נכנס."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _drawer_count(raw):
    """#695: מאמת את מספר המגירות שהוגדר למחשב שכפול (1..8). ‏None = לא
    נשלח (משאירים את ברירת המחדל בסכמה)."""
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "מספר המגירות חייב להיות מספר")
    if not 1 <= n <= 8:
        raise HTTPException(400, "מספר המגירות חייב להיות בין 1 ל־8")
    return n


def _reported_drawer_count(conn, mac: str) -> int | None:
    """Use the last positive hello inventory as a registration default."""
    import json
    canonical = registry.normalize_mac(mac)
    if canonical is None:
        return None
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (canonical,)).fetchone()
    if row is None or row["disks_json"] is None:
        return None
    try:
        disks = json.loads(row["disks_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(disks, list):
        return None
    ports = {disk.get("port") for disk in disks
             if isinstance(disk, dict) and isinstance(disk.get("port"), int)}
    count = len(ports) if ports else len(disks)
    return count if 1 <= count <= 8 else None

#: כל כתיבה כאן עוברת ב-``with _write_lock, writing(ctx.conn)`` — שני
#: המנגנונים של `db.py`, מאותה סיבה שבגללה `net_seen` קיבל אותם (#272,
#: ‏#356, ‏#313): כתיבה שנכשלה בלי ``rollback`` משאירה את החיבור בתוך
#: טרנזאקציה, ומשם **כל** כתיבה על התהליכון הזה נכשלת מיד עד אתחול
#: השרת — קונסולה שמחזירה שגיאה בכל שמירה, גם אחרי שהעומס חלף.
#: ‏`journal` נוטל את אותה נעילה בעצמו, והיא ``Lock`` ולא ``RLock``,
#: ולכן הרישום ביומן נשאר **מחוץ** לבלוק.


def create_console_router(
    ctx: ServerContext, known_macs_hooks: dict | None = None,
    version: Callable[[], object] | None = None,
    tls=None,
    kiosk: bool = False,
    uptime: Callable[[], int | None] | None = None,
    deploy_ip: Callable[[], str | None] | None = None,
) -> APIRouter:
    """‏``tls`` (#703, tracer 5): ‏``console_tls.ConsoleTLS`` כשהקונסולה מוגשת
    ב-HTTPS — העוגייה מקבלת ``Secure`` ו-``/me`` מדווח את טביעת האצבע.
    ‏``None`` = בלי TLS (loopback/בדיקות): עוגיית ``Secure`` על http הייתה
    נזרקת על ידי הדפדפן, ואיש לא היה מצליח להיכנס בפיתוח.

    ‏``kiosk`` (#1073): ‏``True`` כשהראוטר נבנה בשביל ה-allowlist של הקיוסק
    (`kiosk.py` — פורט הסוכן, שממנו מחשב הבנייה נכנס). שם הכניסה
    מקבלת גם ``deploy``; בקונסולה (ברירת המחדל) deploy מסורב ב-403 עם
    ``deploy_no_console`` — למשתמש הפצה אין ניהול וובי (הכרעת נדב 18/09).

    ‏``uptime`` / ‏``deploy_ip`` (#968): שני קוראים ל-`/me` — זמן הפעילות
    של המכונה (‏`health.read_uptime`, מוזרק דרך `health_hooks["uptime"]`)
    וכתובת השרת כפי שהתחנות רואות אותה (מ-`server_base`; ‏``None`` כשרשת
    ההפצה טרם הוגדרה — #1088). ‏``None`` לקורא עצמו = לא הוזרק (בדיקות
    ישנות) — השדה מוחזר ``null``, לא ערך מומצא."""
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    def _sync_known_macs(user_id) -> dict | None:
        """‏#141: מי שנכנס/יוצא מטבלת המכונות משנה מי מקבל dhcp-boot בכלל —
        הקובץ נכתב מחדש **מיד**, לא ממתין לעריכת DHCP הבאה בלשונית הרשת.

        מחזיר את שדה `network` של התשובה (#857): ‏`None` כשלא הופעל
        (אין hooks), ‏`{"applied": True, "error": None}` בהצלחה,
        ‏`{"applied": False, "error": "<סיבה>"}` בכשל. עד #857 הכשל
        נרשם ביומן בלבד והתשובה הייתה 200 נקי — ה-DB עודכן, הרשת לא,
        והמכונה החדשה לא קיבלה GRUB בשקט. ה-DB **אינו** מגולגל אחורה
        (המפעיל ביקש את המכונה) — התשובה פשוט מבחינה.

        ‏`known_macs_hooks` הוא `None` כברירת מחדל בכוונה (בדיוק כמו
        `dhcp_hooks` ב-console_dhcp.py, אבל כאן ה-None הוא גם ברירת
        המחדל הבטוחה לבדיקות): הוספה/מחיקה/ייבוא של מכונה קורים כמעט
        בכל בדיקה בחבילה, ובלי hook מוזרק זה היה אומר כתיבה אמיתית
        ל-/etc/imagesrv/known-macs ו-`systemctl reload dnsmasq` על כל
        אחת מהן. הייצור מדליק אותו במפורש דרך `main.py` — לא ברירת מחדל
        שקטה, כדי שהיא לא תיעלם באותה שקט שבו הייתה נדלקת."""
        if known_macs_hooks is None:
            return None
        text = dhcp.render_known_macs(registry.all_macs(ctx.conn))
        error = known_macs_hooks["apply"](text)
        if error:
            journal(ctx.conn, "known_macs_apply_failed", error, user_id)
            return {"applied": False, "error": str(error)}
        return {"applied": True, "error": None}

    @router.post("/login")
    async def login(request: Request, response: Response):
        # הלוגיקה ב-console_login: #1073 (kiosk=, 403 ל-deploy) ו-#1085
        # (MFA / כפייה / הגבלת ניסיונות) באותו סדר — ראו finish_login.
        return await console_login.finish_login(
            ctx.conn, request, response, tls=tls, kiosk=kiosk,
        )

    @router.post("/logout")
    def logout(request: Request, response: Response, user=Depends(current_user)):
        token = request.cookies.get(auth.COOKIE_NAME)
        # ‏#1633: גם בשרת, לא רק בדפדפן — עותק של העוגייה נדחה מכאן והלאה.
        auth.revoke(ctx.conn, token)
        console_presence.forget(ctx.conn, token)
        from server.console import viewer_sessions
        viewer_sessions.revoke(token)
        response.delete_cookie(auth.COOKIE_NAME)
        return {"ok": True}

    @router.get("/sessions")
    def sessions(user=Depends(admin_only)):
        """‏#1039: מי מחובר לקונסולה — ‏``[{user, ip, since, last_seen}]``.
        ‏``last_seen`` מדויק עד דקה (``console_presence.TOUCH_SECONDS``)."""
        return console_presence.active(ctx.conn)

    @router.get("/me")
    def me(user=Depends(current_user)):
        # זמן הניתוק מוחזר לכל משתמש מחובר — גם deploy, שאין לו גישה
        # למסך ההגדרות אבל הניתוק חל גם עליו.
        #
        # ‏capabilities: דגלים נגזרי-שרת שהקונסולה חושפת מהם רכיבים
        # מותנים. ‏`interbranch_transfer` (#723) — רק ל-admin, רק על
        # standalone, ורק כשיש משני פעיל אחד לפחות. הדגל הוא הנראות;
        # האכיפה עצמה יושבת בשכבת ה-route (שלבים הבאים ב-#655).
        info = users.flags(ctx.conn, user[0])
        version_info = version() if version else None
        shown_version = getattr(version_info, "version", version_info)
        version_verified = getattr(version_info, "verified", False)
        return {
            "username": user[0],
            "role": user[1],
            "mfa_enabled": info["mfa_enabled"],
            "is_builtin": info["is_builtin"],
            "must_change_password": info["must_change_password"],
            "idle_seconds": int(get_setting(ctx.conn, "console_idle_seconds") or 600),
            # ‏#915: גרסת השרת (תג git, ‏`update.current_version`) לשורת
            # הסטטוס — לכל משתמש מחובר, לא רק admin. ‏None = אין תג על
            # העץ, והקונסולה לא מציגה מספר שאין לו מקור.
            "version": shown_version,
            "version_verified": version_verified,
            # ‏#703 (tracer 5): מצב ה-TLS של הקונסולה וטביעת האצבע (SHA-256
            # של התעודה, כמו בדפדפן) לשורת הסטטוס — כדי שהאישור החד-פעמי
            # של התעודה יהיה השוואה, לא לחיצה. ‏null = בלי TLS (loopback).
            "tls": tls.public_info() if tls is not None else None,
            # ‏#936: שם השרת הראשי לצומת העליון בעץ — שם המארח (‏ports.server_name:
            # שם אחד לתצוגה ולרשת, נדב 17/10). לכל משתמש מחובר.
            "server_name": ports.server_name(ctx.conn),
            # ‏#1997: שם מארח שאינו שם מחשב חוקי (עברית, ארוך מ-15, נקודות) —
            # ל"דורש טיפול" בסקירה, עם קישור לשינוי השם. ‏null = חוקי.
            "server_name_problem": hostname_rule.refusal(ports.server_name(ctx.conn), "שם השרת"),
            # ‏#968: שורת-המשנה בסקירה — "פעיל 6 ימים 3 שעות" ו-"10.44.12.1".
            # ‏uptime של המכונה (‏/proc/uptime); ‏null = לא נקרא → "לא נבדק".
            # ‏deploy_ip = הכתובת שב-GRUB ובקרנל של התחנות (‏server_base);
            # ‏null = רשת ההפצה לא הוגדרה (#1088) → מוצג בשם, לא 127.0.0.1.
            "uptime_seconds": uptime() if uptime else None,
            "deploy_ip": deploy_ip() if deploy_ip else None,
            # ‏#1933: האזור שהקונסולה מציגה בו כל זמן — ההגדרה של #1447, ובלעדיה
            # ‏Asia/Jerusalem (שאליו ``apply_on_start`` מיישר את המערכת). לא UTC בשקט.
            "timezone": timezone.zone_name(ctx.conn),
            "capabilities": {
                "interbranch_transfer":
                    storage_nodes.can_interbranch_transfer(ctx.conn, user[1]),
                # ‏#740: הוספת משני זמינה על ראשי גם בלי משניים קיימים (זו
                # הפעולה שיוצרת את הראשון); פאנל ה-pairing המקומי — על משני.
                "enroll_secondary":
                    storage_nodes.can_enroll_secondary(ctx.conn, user[1]),
                "open_local_pairing":
                    storage_nodes.can_open_local_pairing(ctx.conn, user[1]),
                # ‏#1081: v1 מסתיר כיתות. קבוע בקוד, לא הגדרה למפעיל. v2 מדליק.
                "classrooms": capabilities.classrooms(),
                # v1 בלי ארגז הכלים (נדב 19/09). קבוע בקוד; v1.1 מדליק.
                "tools": capabilities.tools(),
            },
            # ‏#1093: ערכת הנושא של המשתמש — auto/light/dark. ברירת מחדל
            # auto (לפי מערכת ההפעלה). לא הגדרת שרת: זה של המשתמש.
            "theme": users.get_theme(ctx.conn, user[0]),
        }

    @router.put("/me/theme")
    async def set_my_theme(request: Request, user=Depends(current_user)):
        """המשתמש על עצמו. ערך זר → 422. בלי יומן."""
        body = await request.json()
        theme = body.get("theme") if isinstance(body, dict) else None
        try:
            users.set_theme(ctx.conn, user[0], theme)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"ok": True}

    # --- מבט-על --------------------------------------------------------------

    @router.get("/overview")
    def overview(user=Depends(current_user)):
        from server.rounds import pulls as pulls_module    # noqa: PLC0415 — נמנע ממעגל ייבוא
        from server.rounds import room as room_module      # noqa: PLC0415 — נמנע ממעגל ייבוא
        room_module.tick(ctx.conn, ctx.store)
        pulls_module.sweep(ctx.store)
        from server.rounds import session_view as sv       # noqa: PLC0415 — נמנע ממעגל ייבוא

        def with_extras(row):
            # ‏#1255: מי פתח ומתי — רק כאן (קונסולה מאומתת), לא ב-`view()` המשותף לתחנה/סוכן
            return {**ctx.store.view(row, ctx.library), **sv.console_extras(ctx.store, row)}

        session = ctx.store.active()
        session_view = None
        if session is not None:
            session_view = with_extras(ctx.store.maybe_start(session))
        else:
            session = ctx.store.latest_failed_broadcast()
            if session is not None:
                session_view = with_extras(session)
        machines = ctx.conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"]
        try:
            usage = shutil.disk_usage(ctx.library.root)
            # ‏#1737: ‏reserve_bytes — מה שהשרת לא ייתן לאף כתיבה של אימג'
            # (`disk_reserve`); הקונסולה מתריעה כשהפנוי מתקרב אליו.
            storage = {"total_bytes": usage.total, "free_bytes": usage.free,
                       "reserve_bytes": disk_reserve.reserve()}
        except OSError:
            storage = None
        return {
            "session": session_view,
            # משיכות היוניקאסט שרצות עכשיו. הן אינן "הסבב" — אבל הן
            # עבודה אמיתית על השרת, ושרת שעובד לא ייראה פנוי (#60).
            # ‏#1843: לכל משיכה גם `forecast` — הזמן הצפוי לפי המשיכות הקודמות של המכונה.
            "pulls": [{**with_extras(row), "forecast": sv.pull_forecast(ctx.store, row, ctx.library)}
                      for row in ctx.store.active_pulls()],
            "room": room_module.status_view(ctx)["round"],
            "sender": ctx.sender.status() if ctx.sender else None,
            "machines": machines,
            "images": len(ctx.library.scan()),
            "storage": storage,
            "now": now_iso(),
            # ‏#1932: זמן הפעילות של המכונה גם כאן — ‏/me אינו עובר בערוץ הבין-שרתי
            # (הוא של המשתמש בראשי), ו-/overview כן. ‏null = לא נקרא → "לא נבדק".
            "uptime_seconds": uptime() if uptime else None,
        }

    # אימג'ים ותיקיות — ב-console_library.py.

    # --- קבוצות וטבלת MAC (admin) -------------------------------------------

    @router.get("/groups")
    def groups(user=Depends(current_user)):
        rows = ctx.conn.execute(
            "SELECT g.id, g.label, g.role, g.sort, COUNT(m.mac) AS machines FROM groups g"
            " LEFT JOIN machines m ON m.group_id = g.id"
            " GROUP BY g.id ORDER BY g.sort, g.id"
        ).fetchall()
        return [dict(r) for r in rows]

    def derive_group_id(label: str) -> str:
        """מזהה מהשם: שם באנגלית/ספרות משמש כפי שהוא, שם בעברית מקבל
        מזהה רץ. המזהה נכנס לכתובות URL וליומן ולכן נשאר ASCII — אבל
        המשתמש לא חייב להמציא אותו."""
        base = re.sub(r"[^A-Za-z0-9_-]", "", label.replace(" ", "_")).strip("_-")
        if not base:
            n = ctx.conn.execute(
                "SELECT COUNT(*) AS n FROM groups WHERE role = 'classroom'"
            ).fetchone()["n"]
            base = f"CLASS{n + 1}"
        gid, bump = f"grp_{base}", 1
        while ctx.conn.execute(
            "SELECT 1 FROM groups WHERE id = ?", (gid,)
        ).fetchone() is not None:
            bump += 1
            gid = f"grp_{base}-{bump}"
        return gid

    @router.post("/groups")
    async def add_group(request: Request, user=Depends(admin_only)):
        body = await request.json()
        gid, label, role = body.get("id", "").strip(), body.get("label", "").strip(), body.get("role", "")
        if not label or role not in ("build", "cloner", "classroom"):
            raise HTTPException(400, "צריך שם ותפקיד חוקי")
        if role == "classroom":
            refuse_if_classrooms_off()
        _text_field_ok(label, "שם הקבוצה")
        # המזהה נכנס לכתובות URL ולשורות היומן, ולכן חייב להיות ASCII —
        # אבל הוא רשות: בלעדיו הוא נגזר מהשם (שיכול להיות בכל שפה).
        if gid in ("", "grp_"):
            gid = derive_group_id(label)
        if not re.fullmatch(r"grp_[A-Za-z0-9][A-Za-z0-9_-]*", gid):
            raise HTTPException(400, "מזהה חייב להיות אותיות או ספרות באנגלית")
        # קבוצה חדשה נכנסת לסוף הרשימה, לא לאמצע.
        last = ctx.conn.execute("SELECT MAX(sort) AS n FROM groups").fetchone()["n"]
        try:
            # ה-``rollback`` שעמד כאן ידנית (#184) יושב עכשיו ב-`writing`,
            # והוא חל על **כל** מסלול יציאה ולא רק על `IntegrityError`:
            # ה-INSERT שנכשל כבר פתח טרנזאקציית כתיבה, וחריגה שיוצאת
            # בלי לסגור אותה משאירה **נעילה יתומה** — והכתיבה הבאה
            # נופלת ב-`database is locked` בלי קשר נראה לעין בין
            # השתיים (זה ה-gotcha של #54).
            with _write_lock, writing(ctx.conn):
                ctx.conn.execute(
                    "INSERT INTO groups (id, label, role, sort) VALUES (?, ?, ?, ?)",
                    (gid, label, role, (last or 0) + 1),
                )
        except sqlite3.IntegrityError:
            # תופסים `IntegrityError` ולא `Exception`: כשל אחר — דיסק
            # מלא, סכימה שהשתנתה — היה מתחפש כאן ל"כבר קיימת", וזו
            # בדיוק ההודעה שתשלח את המפעיל לחפש במקום הלא נכון.
            raise HTTPException(409, "קבוצה בשם הזה כבר קיימת")
        journal(ctx.conn, "group_create", f"{gid} ({role})", user[0])
        return {"ok": True}

    @router.post("/groups/order")
    async def reorder_groups(request: Request, user=Depends(admin_only)):
        """הסדר שנקבע בגרירה. מקבל את המזהים לפי הסדר הרצוי."""
        ids = (await request.json()).get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise HTTPException(400, "צריך רשימת מזהים")
        known = {r["id"] for r in ctx.conn.execute("SELECT id FROM groups")}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise HTTPException(400, f"קבוצה לא קיימת: {unknown[0]}")
        # שורה לכל קבוצה — רצף אחד. סדר שנכתב חלקית הוא רשימה שהמפעיל
        # רואה אחרת ממה שגרר, ואינו יודע שזה מה שקרה.
        with _write_lock, writing(ctx.conn):
            for index, gid in enumerate(ids):
                ctx.conn.execute("UPDATE groups SET sort = ? WHERE id = ?",
                                 (index, gid))
        journal(ctx.conn, "group_reorder", ", ".join(ids), user[0])
        return {"ok": True}

    @router.put("/groups/{gid}")
    async def rename_group(gid: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        label = (body.get("label") or "").strip()
        if not label:
            raise HTTPException(400, "שם ריק")
        _text_field_ok(label, "שם הקבוצה")
        # ‏`update_one` מכסה את השורה שלא נמצאה; ‏`writing` מכסה את
        # ה-UPDATE שנכשל בכלל, ואת ה-``commit`` שאחריו.
        with _write_lock, writing(ctx.conn):
            if not update_one(ctx.conn,
                              "UPDATE groups SET label = ? WHERE id = ?",
                              (label, gid)):
                raise HTTPException(404, "קבוצה לא קיימת")
        journal(ctx.conn, "group_edit", f"{gid} label={label}", user[0])
        return {"ok": True}

    @router.delete("/groups/{gid}")
    def del_group(gid: str, user=Depends(admin_only)):
        from server.db import FIXED_GROUPS
        if gid in {g[0] for g in FIXED_GROUPS}:
            # חדר השיכפולים ומחשב הבנייה הם יחידים במערכת — אין להם
            # ניהול קבוצות, ולכן גם אי אפשר למחוק אותם.
            raise HTTPException(400, "קבוצה קבועה — אפשר להסיר ממנה מכונות, לא למחוק אותה")
        # ‏#1016: בדיקה ומחיקה בטרנזאקציה אחת. כיתה שיש בה מחשבים (או סבבים)
        # אינה נמחקת — המפתח הזר היה מחזיר 500 גולמי. סירוב בעברית, בלי שאריות.
        with _write_lock, writing(ctx.conn):
            if ctx.conn.execute("SELECT 1 FROM groups WHERE id = ?", (gid,)).fetchone() is None:
                raise HTTPException(404, "קבוצה לא קיימת")
            n_machines = ctx.conn.execute(
                "SELECT COUNT(*) FROM machines WHERE group_id = ?", (gid,)).fetchone()[0]
            if n_machines:
                raise HTTPException(409, f"בכיתה יש {n_machines} מחשבים — העבירו או הסירו "
                                         "אותם לפני מחיקת הכיתה")
            n_sessions = ctx.conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE group_id = ?", (gid,)).fetchone()[0]
            if n_sessions:
                raise HTTPException(409, "לכיתה יש היסטוריית סבבים — אי אפשר למחוק אותה")
            try:
                deleted = update_one(ctx.conn, "DELETE FROM groups WHERE id = ?", (gid,))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "הכיתה עדיין בשימוש — אי אפשר למחוק אותה")
            if not deleted:
                raise HTTPException(404, "קבוצה לא קיימת")
        journal(ctx.conn, "group_delete", gid, user[0])
        return {"ok": True}

    @router.get("/machines/{mac}/firmware")
    def machine_firmware(mac: str, user=Depends(admin_only)):
        """‏#2080: תמונת הקושחה של מכונה, מה השתנה, החיבורים, והשוואה לדגם."""
        canonical = registry.normalize_mac(mac)
        if canonical is None or ctx.conn.execute("SELECT 1 FROM machines WHERE mac = ?", (canonical,)).fetchone() is None:
            raise HTTPException(404, "מכונה לא רשומה")
        return firmware_image.view(ctx.conn, canonical)

    @router.post("/machines/{mac}/firmware/accept")
    def accept_firmware(mac: str, user=Depends(admin_only)):
        """‏"זו ההגדרה הנכונה": התמונה הנוכחית נשמרת, ומכאן משווים אליה."""
        canonical = registry.normalize_mac(mac)
        if canonical is None or ctx.conn.execute("SELECT 1 FROM machines WHERE mac = ?", (canonical,)).fetchone() is None:
            raise HTTPException(404, "מכונה לא רשומה")
        if not firmware_image.accept(ctx.conn, canonical, user[0]):
            raise HTTPException(409, "המכונה עוד לא דיווחה על הקושחה — אין מה לשמור")
        return {"ok": True}

    @router.get("/machines")
    def machines(group: str | None = None, user=Depends(current_user)):
        # #417: המלאי האחרון שהמכונה דיווחה ב-hello, ליד מסך המכונה.
        # ‏null = מעולם לא דיווחה (או שהדיווח האחרון היה פגום); [] = דיווחה
        # בפועל אפס כוננים — שני ממצאים שונים, ואסור לקפל (עיקרון 5).
        query = (
            "SELECT m.mac, m.suffix, m.group_id, m.note, m.drawer_count, m.drawer_slots,"
            " m.added_at, d.disks_json, d.optical_json, d.last_seen AS disks_reported_at,"
            " d.prompt,"   # #906: מה המכונה ממתינה עליו לאדם (NULL = לא ממתינה)
            " d.disk_probe,"   # #402: למה אפס דיסקים (NULL = סוכן ישן)
            " d.firmware, d.secure_boot,"   # #500: כפי שהסוכן מדד (NULL = לא דווח)
            " d.agent_version,"   # #1257: גרסת ה-initrd מה-hello האחרון (NULL = לא דווח)
            " d.local_clone,"   # #1367: שיכפול מקומי כותב (נגזר ל-local_copy למטה)
            " d.held_at, d.held_json"   # #2031: מחזיק תוצאות עד "כבה" (נגזר ל-held למטה)
            " FROM machines m LEFT JOIN net_devices d ON d.mac = m.mac"
            + (" WHERE m.group_id = ?" if group else "")
            + " ORDER BY m.group_id, m.suffix"
        )
        rows = ctx.conn.execute(query, (group,) if group else ()).fetchall()
        # ‏#720: המלאי החומרתי האחרון (schema 2). null = מעולם לא דיווחה.
        inventories = inventory.latest_all(ctx.conn)
        # ‏#1049 שלב ב': הדגימה האחרונה של בדיקת המכונה + השערים שנגזרים ממנה.
        # null = מעולם לא דיווחה; verdicts ריק = לא נמצאה בעיה **במה שנמדד**.
        probes = probe.latest_all(ctx.conn)
        # ‏#980 §6: "בשימוש" מהקליטה האחרונה שהסתיימה. null = לא נקלט מהמחשב.
        last_captures = _last_captures()
        # ‏#704: השם לכל חיבור של כונן. ‏{} = אין שמות — כל המסכים מציגים "דיסק N".
        names = slot_names.all_by_mac(ctx.conn)
        # ‏#2001: מיפוי לפי דגם — מכונה ← דגם ← ברירת מחדל.
        model_maps, models = model_slots.all_maps(ctx.conn), model_slots.models_by_mac(ctx.conn)
        crc_limit = crcwarn.threshold(ctx.conn)   # #1366
        fw_counts = firmware_image.change_counts(ctx.conn)   # ‏#2080
        result = []
        for r in rows:
            row = dict(r)
            disks_json = row.pop("disks_json")
            row["disks"] = json.loads(disks_json) if disks_json is not None else None
            stored_slots = row.pop("drawer_slots")
            row["fw_changes"] = fw_counts.get(row["mac"], 0)   # ‏#2080: כמה שינויי קושחה מול ההפניה (0 = אין / לא נקרא)
            row["slot_model"] = models.get(row["mac"])
            model_map = model_maps.get(row["slot_model"])
            row["model_slot_map"] = ({"slots": model_map["slots"], "names": model_map["names"]}
                                     if model_map else None)
            row["slot_map_source"] = ("machine" if stored_slots is not None
                                      else "model" if model_map else "default")
            row["drawer_slots"] = drawer_slots.effective(
                row["drawer_count"], stored_slots, model_map["slots"] if model_map else None)
            row["drawer_slots_custom"] = stored_slots is not None
            optical_json = row.pop("optical_json")
            row["optical"] = json.loads(optical_json) if optical_json is not None else None
            # ‏#1366: מונה ה-CRC המוחלט מעל הסף — אותו דגל כמו במסך החדר.
            for d in row["disks"] if isinstance(row["disks"], list) else []:
                if isinstance(d, dict):
                    crc = d.get("crc")
                    read = isinstance(crc, int) and not isinstance(crc, bool) and crc >= 0
                    d["crc_suspect"] = crcwarn.suspect(crc if read else None, crc_limit)
            seen = inventories.get(row["mac"])
            row["inventory"] = seen["inventory"] if seen else None
            row["inventory_seen_at"] = seen["seen_at"] if seen else None
            sample = probes.get(row["mac"])
            row["probe"] = sample["probe"] if sample else None
            row["probe_seen_at"] = sample["sampled_at"] if sample else None
            row["probe_verdicts"] = probe.verdicts(row["probe"])
            # ‏15/10: מהירות כרטיס ההפצה — ידנית / מדווחת / הונחה 1Gb/s.
            row["nic_speed"] = nic_speed.view(ctx.conn, row["mac"], row["probe"], sampled=True)
            row["last_capture"] = last_captures.get(row["mac"])
            row["slot_names"] = names.get(row["mac"], {})
            # ‏#1997: שם שנרשם לפני הכלל ואינו שם מחשב — מוצג כמו שהוא, וכאן
            # הסיבה, ל"דורש טיפול" בסקירה. ‏null = חוקי. אין מיגרציה שמשנה שם.
            row["name_problem"] = hostname_rule.refusal(row["suffix"], "שם המחשב")
            # ‏#1367: באמצע שיכפול מקומי עכשיו — "אתחל" שואל לפני (room.local_copy_now)
            row["local_copy"] = room.local_copy_now(row.pop("local_clone"), row["disks_reported_at"])
            # ‏#2031: "דלוק — מחכה ללחיצה" (#2024): ‏{at, problems} מדיווח held טרי, או None
            row["held"] = held.view(row.pop("held_at"), row.pop("held_json"))
            result.append(row)
        return result

    def _last_captures() -> dict[str, dict]:
        """‏#980 §6: הקליטה האחרונה שהסתיימה (‏`done` — יש לה מניפסט) לכל MAC,
        עם ה"בשימוש" של המניפסט שלה. שלושה מצבים שאסור לקפל (עיקרון 5):
        אין קליטה = אין מפתח; ‏`in_library: false` = האימג' כבר לא בספרייה
        (‏`used_bytes: null` — לא נקרא, לא 0); ‏`used_bytes: null` עם
        ‏`in_library: true` = מחיצה שלא נמדדה (‏`used_bytes_total`)."""
        latest: dict[str, sqlite3.Row] = {}
        for r in ctx.conn.execute(
                "SELECT id, mac, image_id, name, updated_at FROM tasks"
                " WHERE type = 'capture' AND state = 'done'"
                " ORDER BY updated_at DESC, rowid DESC"):
            latest.setdefault(r["mac"], r)
        if not latest:
            return {}
        library = ctx.library.scan()            # פעם אחת לכל הבקשה, ורק כשיש מה לחפש
        out = {}
        for mac, r in latest.items():
            manifest = library.get(r["image_id"])
            out[mac] = {
                "task_id": r["id"], "image_id": r["image_id"], "name": r["name"],
                "at": r["updated_at"], "in_library": manifest is not None,
                "used_bytes": used_bytes_total(manifest) if manifest is not None else None,
            }
        return out

    @router.post("/machines/import")
    async def import_machines(request: Request, user=Depends(admin_only)):
        body = await request.json()
        group_id = body.get("group_id", "")
        role = registry.group_role(ctx.conn, group_id)
        if role is None:
            raise HTTPException(400, "קבוצה לא קיימת")
        lines = registry.parse_paste(body.get("text", ""), role)
        if body.get("dry_run"):
            return {"preview": [vars(l) for l in lines]}
        saved, rejected = registry.import_lines(ctx.conn, group_id, lines, user[0])
        network = _sync_known_macs(user[0]) if saved else None
        return {"saved": saved, "rejected": [vars(l) for l in rejected],
                "network": network}

    @router.post("/machines")
    async def add_machine(request: Request, user=Depends(admin_only)):
        body = await request.json()
        drawer_count = _drawer_count(body.get("drawer_count"))   # #695
        if drawer_count is None and body.get("group_id") == "grp_CLONERS":
            drawer_count = _reported_drawer_count(ctx.conn, body.get("mac", ""))
        try:
            mac = registry.add_machine(
                ctx.conn, body.get("mac", ""), body.get("name", ""),
                body.get("group_id", ""), user[0],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if drawer_count is not None:
            with _write_lock, writing(ctx.conn):
                ctx.conn.execute(
                    "UPDATE machines SET drawer_count = ? WHERE mac = ?",
                    (drawer_count, mac),
                )
            journal(ctx.conn, "machine_drawer_count",
                    f"{mac} count={drawer_count}", user[0])
        return {"mac": mac, "network": _sync_known_macs(user[0])}

    @router.put("/machines/{mac}")
    async def edit_machine(mac: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "גוף הבקשה חייב להיות אובייקט")
        unknown = set(body) - EDIT_MACHINE_FIELDS
        if unknown:
            raise HTTPException(400, "שדות לא מוכרים: " + ", ".join(sorted(unknown)))
        if "mac" in body:
            # ‏#977 §5: לבד — שם לא תקין אחרי שה-MAC כבר עבר היה משאיר חצי עריכה.
            if len(body) > 1:
                raise HTTPException(400, "עריכת MAC נשלחת לבד, בלי שדות אחרים")
            try:
                new, changed = registry.change_mac(ctx.conn, mac, body["mac"], user[0])
            except LookupError as exc:
                raise HTTPException(404, str(exc))
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            except registry.MacBusy as exc:
                raise HTTPException(409, str(exc))
            # ‏#141: הכתובת החדשה מקבלת GRUB והישנה כבר לא — מיד, לא בעריכה הבאה.
            return {"ok": True, "mac": new,
                    "network": _sync_known_macs(user[0]) if changed else None}
        drawer_count = _drawer_count(body.get("drawer_count"))   # #695
        canonical = registry.normalize_mac(mac)
        current = ctx.conn.execute(
            "SELECT drawer_slots FROM machines WHERE mac = ?", (canonical,)
        ).fetchone() if canonical is not None else None
        mapping_sent = "drawer_slots" in body
        mapping = None
        if mapping_sent and body["drawer_slots"] is not None:
            try:
                mapping = drawer_slots.parse(body["drawer_slots"])
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            if "drawer_count" in body and drawer_count != len(mapping):
                raise HTTPException(400, "מספר המגירות אינו תואם למספר החיבורים במיפוי")
            drawer_count = len(mapping)
        if ("drawer_count" in body and not mapping_sent and current is not None
                and current["drawer_slots"] is not None):
            raise HTTPException(400, "למכונה יש מיפוי חיבורים מותאם; יש לשלוח גם drawer_slots")
        # ‏15/10: נבדק **לפני** כל כתיבה — ערך פסול אינו משאיר חצי עריכה.
        speed = body.get("nic_speed_mbps")
        if "nic_speed_mbps" in body and speed is not None and (
                isinstance(speed, bool) or speed not in nic_speed.CHOICES):
            raise HTTPException(400, "מהירות כרטיס: אוטומטי (null), 1000, 2500 או 10000")
        try:
            registry.update_machine(
                ctx.conn, mac, body.get("name"), body.get("group_id"), user[0]
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if drawer_count is not None:
            with _write_lock, writing(ctx.conn):
                if not update_one(
                    ctx.conn,
                    "UPDATE machines SET drawer_count = ? WHERE mac = ?",
                    (drawer_count, registry.normalize_mac(mac)),
                ):
                    raise HTTPException(404, "מכונה לא קיימת")
            journal(ctx.conn, "machine_drawer_count",
                    f"{registry.normalize_mac(mac)} count={drawer_count}", user[0])
        if mapping_sent:
            with _write_lock, writing(ctx.conn):
                if not update_one(
                    ctx.conn, "UPDATE machines SET drawer_slots = ? WHERE mac = ?",
                    (json.dumps(mapping) if mapping is not None else None, canonical),
                ):
                    raise HTTPException(404, "מכונה לא קיימת")
            journal(ctx.conn, "machine_drawer_slots",
                    f"{canonical} " + (",".join(mapping) if mapping is not None else "default"),
                    user[0])
        if "nic_speed_mbps" in body:
            canonical = registry.normalize_mac(mac)
            nic_speed.set_manual(ctx.conn, canonical, speed)
            journal(ctx.conn, "machine_nic_speed",
                    f"{canonical} " + (f"{speed}Mb/s" if speed else "auto"), user[0])
        return {"ok": True}

    @router.put("/machines/{mac}/slot-names")
    async def put_slot_name(mac: str, request: Request, user=Depends(admin_only)):
        """‏#704: שם לחיבור אחד — ``{"slot": "sata:1", "name": "דיסק 1"}``;
        ‏``"name": null`` מחזיר לברירת המחדל. תווית בלבד: הבחירה לפי port."""
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"slot", "name"}:
            raise HTTPException(400, 'הגוף חייב להיות {"slot", "name"} בלבד')
        try:
            slot = slot_names.parse_key(body["slot"])
            name = None if body["name"] is None else slot_names.clean_name(body["name"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(404, "מכונה לא רשומה")
        try:
            slot_names.set_name(ctx.conn, canonical, slot, name, user[0])
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        journal(ctx.conn, "machine_slot_name",
                f"{canonical} {slot} " + (f'name="{name}"' if name else "cleared"), user[0])
        return {"ok": True, "slot": slot, "name": name}

    @router.put("/machines/{mac}/slot-map")
    async def put_slot_map(mac: str, request: Request, user=Depends(admin_only)):
        """‏#2001: חלון "מיפוי חיבורים" — שמירה אחת: ``{"scope": "machine"|"model",
        "slots": [...] | null, "names": {key: name}}``. ‏``slots: null`` = "החזר
        לברירת המחדל" באותו היקף. הכול נבדק לפני כל כתיבה."""
        body = await request.json()
        if (not isinstance(body, dict) or not {"scope", "slots"} <= set(body)
                or set(body) - {"scope", "slots", "names", "replace_machine_maps"}):
            raise HTTPException(400, 'הגוף חייב להיות {"scope", "slots", "names", "replace_machine_maps"}')
        replace = body.get("replace_machine_maps", False)
        if not isinstance(replace, bool):
            raise HTTPException(400, "replace_machine_maps: true או false")
        if body["scope"] not in ("machine", "model"):
            raise HTTPException(400, 'scope: "machine" או "model"')
        try:
            if body["slots"] is None:
                if body.get("names"):
                    raise ValueError("בחזרה לברירת המחדל אין שמות")
                slots, names = None, None
            else:
                slots, names = model_slots.clean(body["slots"], body.get("names"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(404, "מכונה לא רשומה")
        try:
            done = model_slots.save(ctx.conn, canonical, body["scope"], slots, names, user[0],
                                    replace_machine_maps=replace)
        except model_slots.MachineMaps as exc:
            # נדב 22/10: הקונסולה שואלת "ל-N מחשבים מהדגם יש מיפוי משלהם — הוא יוחלף"
            return JSONResponse(status_code=409, content={
                "code": "machine_maps", "message_he": str(exc), "detail": str(exc),
                "machines": exc.macs})
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        target = f"model={done['model']} machines={len(done['machines'])}"             if done["scope"] == "model" else canonical
        what = ("default" if slots is None else ",".join(
            k + (f'="{names[k]}"' if k in names else "") for k in slots))
        journal(ctx.conn, "machine_slot_map", f"{target} {what}", user[0])
        return {"ok": True, **done}

    @router.delete("/machines/{mac}")
    def del_machine(mac: str, user=Depends(admin_only)):
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        with _write_lock, writing(ctx.conn):
            if not update_one(ctx.conn, "DELETE FROM machines WHERE mac = ?", (canonical,)):
                raise HTTPException(404, "מכונה לא קיימת")
            # #1873: hello must never see the deletion without its timestamp.
            # journal() owns its own lock/transaction, so insert here atomically;
            # a failed record must roll back the deletion and fail visibly.
            ctx.conn.execute(
                "INSERT INTO journal (ts, user, event, detail) VALUES (?, ?, ?, ?)",
                (now_iso(), user[0], "machine_delete", canonical),
            )
        return {"ok": True, "network": _sync_known_macs(user[0])}

    @router.get("/machines.csv")
    def machines_csv(user=Depends(admin_only)):
        return PlainTextResponse(registry.export_csv(ctx.conn), media_type="text/csv")

    @router.get("/machines/{mac}/smart")
    def machine_smart(mac: str, user=Depends(current_user)):
        """‏#1859: כל מאפייני ה-SMART של דיסקי המכונה, בעברית, מול הסבב הקודם של כל סידורי.
        ‏`disks: null` = המכונה מעולם לא דיווחה דיסקים (לא "אין")."""
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        row = ctx.conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (canonical,)).fetchone()
        try:
            disks = json.loads(row["disks_json"]) if row is not None and row["disks_json"] else None
        except (TypeError, ValueError):
            disks = None
        return {"mac": canonical, "no_history_he": smart_history.NO_HISTORY_HE,
                "disks": None if not isinstance(disks, list) else
                [smart_history.describe(ctx.conn, d) for d in disks if isinstance(d, dict)]}

    # --- #874: דיסקים אדומים — זיכרון כשלי הכתיבה בשרת ---------------------------

    @router.get("/disk-failures")
    def read_disk_failures(user=Depends(current_user)):
        return disk_failures.list_open(ctx.conn)

    @router.post("/disk-failures/{failure_id}/clear")
    def clear_disk_failure(failure_id: int, user=Depends(admin_only)):
        # "נקה" — הדיסק הוחלף / הכבל תוקן. רשומה שכבר נוקתה או שאינה
        # קיימת אינה "נוקתה" (עיקרון 5): 404, לא ok.
        if not disk_failures.clear(ctx.conn, failure_id, by=user[0]):
            raise HTTPException(404, "רשומה לא קיימת או שכבר נוקתה")
        return {"ok": True}

    # --- #926: דיסקים מכווצים — המקור שלא הוחזר לגודלו אחרי קליטה -------------

    @router.get("/shrink-records")
    def read_shrink_records(user=Depends(current_user)):
        return shrink_records.list_open(ctx.conn)

    @router.post("/shrink-records/{record_id}/clear")
    def clear_shrink_record(record_id: int, user=Depends(admin_only)):
        # "נקה" — המפעיל הרחיב את המחיצה בעצמו / הדיסק הוחלף. רשומה שכבר
        # נסגרה או שאינה קיימת אינה "נוקתה" (עיקרון 5): 404, לא ok.
        if not shrink_records.clear(ctx.conn, record_id, by=user[0]):
            raise HTTPException(404, "רשומה לא קיימת או שכבר נסגרה")
        return {"ok": True}

    # --- סבבים (גם deploy) ---------------------------------------------------

    def round_operator(user=Depends(current_user)) -> tuple[str, str]:
        """מחובר **וגם** בתפקיד שרשאי להפעיל סבב כיתה.

        אותה רשימת-היתר שבה נפתח הסבב מהתחנה — ``ROUND_OPENER_ROLES``
        (#94) — ולא עותק שלה: מדובר באותו אובייקט בדיוק, וכשיתווסף
        תפקיד שלישי אסור ששתי הרשימות ייפרדו. חדר השיכפולים עשה את
        אותה הכרעה ב-#152 (``room.ROOM_OPERATOR_ROLES``).

        ‏#581 סגר את ``start``/``close`` מאחורי הבדיקה הזו, אך **פתיחת**
        הסבב מהקונסולה נשארה ``current_user`` בלבד — #592 סגר גם אותה.
        וכך כל חשבון מחובר — תפקיד שיתווסף מחר בכלל — כבר אינו יכול
        לפתוח או לעצור שידור חי לכיתה שלמה.
        """
        if user[1] not in ROUND_OPENER_ROLES:
            journal(ctx.conn, "session_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.post("/sessions")
    async def open_session(request: Request, user=Depends(round_operator)):
        """פתיחת סבב כיתה — מהקונסולה או ממסך מחשב הבנייה (אותו cookie).

        `macs` (רשות) — בחירת מחשבים: רק הם מוערים ומצטרפים. קידומת
        ומספר מחשבים הם רשות — נגזרים מהקבוצה ומהבחירה, כמו בתחנה.
        """
        refuse_if_classrooms_off()
        body = await request.json()
        image_id = body.get("image_id", "")
        group_id = body.get("group_id", "")
        manifest = ctx.library.get(image_id)
        if manifest is None:
            raise HTTPException(400, "אימג' לא קיים בספרייה")
        unavailable = storage_locations.unavailable_message(manifest)
        if unavailable:
            raise HTTPException(409, unavailable)
        # #534: יעד סבב הוא כיתה בלבד — לא מחשב הבנייה ולא חדר השיכפולים.
        # אותה בדיקה של station.py, דרך registry.group_role המשותפת.
        if registry.group_role(ctx.conn, group_id) != "classroom":
            raise HTTPException(400, "יעד הסבב חייב להיות קבוצת כיתה")

        roster = None
        if body.get("macs") is not None:
            if not isinstance(body["macs"], list):
                raise HTTPException(400, "macs חייב להיות רשימה")
            roster = sorted({registry.normalize_mac(m) for m in body["macs"]})
            if not roster or None in roster:
                raise HTTPException(400, "בחירת המחשבים ריקה או מכילה MAC פגום")

        # ‏#381, אחרי שה-roster ידוע: אימג' שנקלט ממחשב כיתה מסוים
        # מסורב לכל יעד אחר, ובהודעה שנוקבת בשני הצדדים (עיקרון 5).
        refusal = restore_refusal(manifest, roster)
        if refusal is not None:
            journal(ctx.conn, "session_image_bound", f"{image_id} — {refusal}",
                    user[0])
            raise HTTPException(400, refusal)

        # ‏#59: בחירת ההרחבה — ברירת המחדל האוטומטית, כיבוי, או מחיצה
        # שנבחרה ביד. מאומתת מול המניפסט הזה לפני שהסבב נפתח.
        try:
            expand_choice = validate_expand_choice(
                manifest, body.get("expand_partition"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        machines = ctx.conn.execute(
            "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (group_id,)
        ).fetchone()["n"]
        expected = int(body.get("expected_clients") or 0) \
            or (len(roster) if roster else machines)
        prefix = body.get("prefix") or group_id.removeprefix("grp_").upper()
        try:
            session_id = ctx.store.open(
                group_id, image_id, prefix, expected,
                opened_by=user[0], roster=roster, expand_partition=expand_choice,
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(409, str(exc))
        return {"id": session_id}

    @router.post("/sessions/{session_id}/start")
    def start_session(session_id: str, user=Depends(round_operator)):
        # תפקיד בלבד, בלי הקלדת שם — אותה הכרעה כמו ``/room/start``:
        # עיקרון 7 נוקב ב"עצירת סבב", וההתחלה רק מקדימה את מה שהטיימר
        # עומד לעשות ממילא.
        try:
            ctx.store.start_now(session_id, user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    @router.post("/sessions/{session_id}/close")
    async def close_session(session_id: str, request: Request,
                            user=Depends(round_operator)):
        # גוף ריק או לא-JSON הוא בדיוק המקרה שההקלדה נועדה לתפוס, ולכן
        # הוא נופל לאישור ריק — 400 עם ההסבר, ולא 500 שנראה כתקלת שרת.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        row = ctx.conn.execute(
            "SELECT image_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(409, "סבב לא קיים")
        # פעולה הרסנית מאחורי הקלדת שם — אותו דפוס כמו מחיקת אימג'
        # ועצירת סבב החדר (#533). מה שמוקלד הוא מה שהמסך כבר מציג,
        # כולל הנפילה חזרה ל-`image_id` כשהמניפסט נמחק באמצע הסבב.
        if typed != session_label(row, ctx.library):
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם האימג' שהסבב משדר")
        try:
            ctx.store.close(session_id, user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    # --- משתמשים, יומן, הגדרות (admin) --------------------------------------

    @router.get("/users")
    def list_users(user=Depends(admin_only)):
        return users.list_users(ctx.conn)

    @router.post("/users")
    async def add_user(request: Request, user=Depends(admin_only)):
        body = await request.json()
        try:
            users.create(
                ctx.conn, body.get("username", ""), body.get("password", ""),
                body.get("role", ""), by=user[0],
                mfa_required=body.get("mfa_required") is True,   # נדב 18/10
            )
        except users.UsernameTaken as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except sqlite3.IntegrityError:
            # רק התנגשות UNIQUE היא "שם תפוס". כל חריגה אחרת — דיסק מלא,
            # DB נעול — עולה כ-500 עם הסיבה האמיתית, ולא מתחפשת לשם תפוס
            # ששולח את המפעיל לחפש חשבון שלא נוצר (עיקרון 5).
            raise HTTPException(409, "משתמש בשם הזה כבר קיים")
        return {"ok": True}

    @router.post("/users/{username}/reset-password")
    def reset_user_password(username: str, user=Depends(admin_only)):
        if username.strip().lower() == user[0].lower():
            raise HTTPException(400, "אי אפשר לאפס את הסיסמה של עצמך כאן")
        try:
            new = users.reset_password(ctx.conn, username, by=user[0])
        except ValueError as exc:
            raise HTTPException(404 if "לא קיים" in str(exc) else 400, str(exc))
        return {"new_password": new}

    @router.post("/me/password")
    async def change_own_password(request: Request, response: Response,
                                  user=Depends(current_user)):
        body = await request.json()
        try:
            users.change_own_password(
                ctx.conn, user[0],
                body.get("current_password") or "",
                body.get("new_password") or "",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        info = users.flags(ctx.conn, user[0])
        if console_mfa.enrollment_required(ctx.conn, user[1], info):   # נדב 18/10
            auth.attach_cookie(response, ctx.conn, user[0], user[1], tls,
                               purpose=auth.PURPOSE_MFAENROLL)
            return {"ok": True, "mfa_enrollment_required": True}
        auth.attach_cookie(response, ctx.conn, user[0], user[1], tls)
        return {"ok": True}

    @router.put("/users/{username}")
    async def edit_user(username: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        role = body.get("role") or None
        if username == user[0] and role and role != user[1]:
            raise HTTPException(400, "אי אפשר לשנות את התפקיד של עצמך")
        disabled = body.get("disabled")
        if disabled is not None and username == user[0]:
            # נעילה מיידית מחוץ למסך, ובלי דרך לחזור — `auth.check` קורא
            # את החסימה בכל בקשה (#186), כולל בזו ששחררה אותה.
            raise HTTPException(400, "אי אפשר לחסום את המשתמש המחובר")
        try:
            mfa_req = body.get("mfa_required")
            if mfa_req is not None and not isinstance(mfa_req, bool):
                raise ValueError("mfa_required: true או false")
            users.update(ctx.conn, username, by=user[0],
                         password=body.get("password") or None, role=role,
                         mfa_required=mfa_req)
            if disabled is not None:
                users.set_disabled(ctx.conn, username, bool(disabled), by=user[0])
        except users.BuiltinProtected as exc:
            # ‏#1262: לא קלט שגוי אלא מצב שמסרב — כמו שם תפוס.
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    @router.delete("/users/{username}")
    def del_user(username: str, user=Depends(admin_only)):
        if username == user[0]:
            raise HTTPException(400, "אי אפשר למחוק את המשתמש המחובר")
        # השומר על "יישאר מי שינהל" ירד ל-``users.delete`` ב-#521: שם הוא
        # תנאי **בתוך** ה-DELETE ולא קריאה שלפניו, ולכן שני מנהלים שנמחקים
        # בו-זמנית אינם רואים שניהם "יש שניים". כאן נשאר רק התרגום ל-HTTP.
        try:
            users.delete(ctx.conn, username, by=user[0])
        except users.BuiltinProtected as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(404 if "לא קיים" in str(exc) else 400, str(exc))
        return {"ok": True}

    @router.get("/journal/events")
    def read_journal_events(user=Depends(admin_only)):
        # לתפריט הסינון — מציג את מה שהמפעיל רואה (השם בעברית), לא את
        # מפתח האירוע הגולמי.
        return sorted(
            ({"event": k, "label": v} for k, v in EVENTS_HE.items()),
            key=lambda e: e["label"],
        )

    #: חלון הסריקה של סינון מכונה/חיפוש (על הטקסט המתורגם), וגם התקרה של
    #: ייצוא ה-CSV. ‏`/journal` עצמו מוגבל ל-1,000 שורות לדף.
    journal_scan_cap = 20000

    def _journal_rows(response: Response, *, limit: int, max_limit: int,
                      event: str, user_filter: str, date_from: str, date_to: str,
                      machine: str, q: str, before: int | None) -> list[dict]:
        limit = max(1, min(limit, max_limit))
        conditions = []
        params: list = []
        # אירוע, משתמש וטווח זמן הם עמודות אמיתיות — מסננים בשאילתה עצמה.
        if event:
            conditions.append("event = ?")
            params.append(event)
        if user_filter:
            conditions.append("user = ?")
            params.append(user_filter)
        if date_from:
            conditions.append("ts >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("ts <= ?")
            params.append(date_to)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        if not (machine or q):
            # ‏#1008: הסך של כל השורות התואמות — בלי ``before``, שמזיז את
            # הדף ולא את הסך. עם מכונה/חיפוש הסינון הוא על הטקסט המתורגם,
            # והסך אינו ידוע בלי לתרגם את כל היומן — ואז הכותרת **חסרה**
            # ("לא ידוע"), לא 0 ולא אורך הדף (עיקרון 5).
            response.headers["X-Journal-Total"] = str(ctx.conn.execute(
                f"SELECT COUNT(*) AS n FROM journal {where}", params
            ).fetchone()["n"])
        if before is not None:
            # ‏#1008: דפדוף לפי ``id`` ולא לפי offset — שורה שנכתבת בין שני
            # דפים אינה מזיזה את הדף הבא ואינה מכפילה בו שורה.
            conditions.append("id < ?")
            params.append(before)
            where = f"WHERE {' AND '.join(conditions)}"
        # מכונה/MAC וחיפוש חופשי חבויים בתוך detail הגולמי, לא בעמודה
        # (המלכודת ב-#115: מה שהמפעיל רואה, כמו שם כיתה, אינו מה
        # שכתוב בשורה — grp_a3f1 וכו'). לכן סורקים חלון גדול של שורות,
        # מתרגמים אותן כמו למסך, ומסננים על התוצאה המתורגמת — לא על
        # המזהה הגולמי שהמפעיל לא הקליד ולא ראה.
        scan_cap = journal_scan_cap
        if machine or q:
            scanned = ctx.conn.execute(
                f"SELECT COUNT(*) AS n FROM journal {where}", params
            ).fetchone()["n"]
            # אם יש יותר שורות תואמות-לעמודות מהחלון שסרקנו, זו לא "אין
            # תוצאות" — זו חיפוש שלא כיסה את כל היומן. עיקרון 5: אסור
            # לקפל "לא בדקנו הכל" ל"בדקנו והכל תקין".
            if scanned > scan_cap:
                response.headers["X-Journal-Search-Truncated"] = "true"
        rows = ctx.conn.execute(
            f"SELECT id, ts, user, event, detail FROM journal {where} "
            "ORDER BY id DESC LIMIT ?",
            (*params, scan_cap),
        ).fetchall()
        translator = JournalTranslator(ctx.conn, ctx.library)
        machine_q = machine.strip().lower()
        text_q = q.strip().lower()
        result = []
        for r in rows:
            label, text = translator.translate(r["event"], r["detail"])
            if machine_q and machine_q not in text.lower() and machine_q not in r["detail"].lower():
                continue
            if text_q and not any(
                text_q in field.lower() for field in (label, text, r["user"])
            ):
                continue
            result.append({
                # ‏#1008: סמן הדפדוף — ``before=<id>`` של השורה האחרונה בדף.
                "id": r["id"],
                "ts": r["ts"], "user": r["user"], "event": r["event"],
                "label": label, "text": text,
                # ‏#968: החומרה נקבעת בשרת לצד התרגום (journal_he.severity),
                # לא בהיוריסטיקה על שם ה-event בקונסולה.
                "severity": severity(r["event"]),
            })
            if len(result) >= limit:
                break
        return result

    @router.get("/journal")
    def read_journal(
        response: Response,
        limit: int = 200,
        event: str = "",
        user_filter: str = Query("", alias="user"),
        date_from: str = Query("", alias="from"),
        date_to: str = Query("", alias="to"),
        machine: str = "",
        q: str = "",
        before: int | None = None,
        user=Depends(admin_only),
    ):
        return _journal_rows(
            response, limit=limit, max_limit=1000, event=event,
            user_filter=user_filter, date_from=date_from, date_to=date_to,
            machine=machine, q=q, before=before)

    @router.get("/journal.csv")
    def journal_csv(
        limit: int = journal_scan_cap,
        event: str = "",
        user_filter: str = Query("", alias="user"),
        date_from: str = Query("", alias="from"),
        date_to: str = Query("", alias="to"),
        machine: str = "",
        q: str = "",
        before: int | None = None,
        user=Depends(admin_only),
    ):
        """‏#1008: אותם פרמטרים ואותן שורות כמו ``/journal``, אחרי אותה
        הרשאה (admin). ברירת המחדל והתקרה של ``limit`` הן חלון הסריקה ולא
        1,000 — ייצוא שנחתך בשקט בדף הראשון אינו ייצוא. ‏``X-Journal-Total``
        ו-``X-Journal-Search-Truncated`` כמו במסך."""
        meta = Response()
        rows = _journal_rows(
            meta, limit=limit, max_limit=journal_scan_cap, event=event,
            user_filter=user_filter, date_from=date_from, date_to=date_to,
            machine=machine, q=q, before=before)
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        fields = ("id", "ts", "user", "event", "label", "text", "severity")
        writer.writerow(fields)
        for row in rows:
            writer.writerow([_csv_cell(row[f]) for f in fields])
        headers = {k: v for k, v in meta.headers.items()
                   if k.lower().startswith("x-journal-")}
        headers["Content-Disposition"] = 'attachment; filename="imagesrv-journal.csv"'
        # BOM: אקסל פותח UTF-8 בלעדיו כ-ANSI, והעברית (label/text) נשברת.
        return Response(("﻿" + out.getvalue()).encode("utf-8"),
                        media_type="text/csv; charset=utf-8", headers=headers)

    @router.get("/settings")
    def read_settings(user=Depends(admin_only)):
        values = {key: get_setting(ctx.conn, key) for key in sorted(WRITE_SETTINGS)}
        # ‏#1300: אותו שם כמו ‏/me — לקריאה בלבד; משנים אותו ב-POST /hostname (נדב 17/10).
        values["server_name"] = ports.server_name(ctx.conn)
        # ‏#1366: הערך שבתוקף — ברירת המחדל כשלא נקבע, לא "לא נקבע".
        values[crcwarn.SETTING] = str(crcwarn.threshold(ctx.conn))
        # ‏#1354: הערך שבתוקף — ברירת המחדל כשלא נקבע, לא "לא נקבע".
        values[minfree.SETTING] = str(minfree.min_free_gb(ctx.conn))
        # ‏#1473: הערך שבתוקף — "0" (ללא הגבלה) כשלא נקבע או כשהשמור פגום.
        values[transfer_cap.SETTING] = str(transfer_cap.cap_mbps(ctx.conn))
        # ‏#1864: הערך שבתוקף — "false" (מדגם) כשלא נקבע.
        values[readbackhash.SETTING] = "true" if readbackhash.mode(ctx.conn) == "full" else "false"
        # ‏#1956: יעד גיבוי המסד — לקריאה בלבד כאן (בלי סוד); בוחרים ב-POST /backup-target, שכותב
        # ומקריא קובץ בדיקה. ‏`backup_dest` אינו ב-WRITE_SETTINGS — אין דרך לעקוף את הבדיקה.
        values[backup_target.SETTING] = backup_target.describe(ctx.conn)
        # ‏#2056/#2059: הערך שבתוקף (ברירת המחדל כשלא נקבע) + כמה מכונות מחוברות החזירו אותו.
        for key in agent_timing.KEYS:
            values[key] = str(agent_timing.seconds(ctx.conn, key))
        values["agent_timing_applied"] = agent_timing.applied_counts(ctx.conn)
        mode, at, weekday = auto_reboot.settings(ctx.conn)
        values.update({auto_reboot.MODE: mode, auto_reboot.TIME: at,
                       auto_reboot.WEEKDAY: str(weekday)})
        return values

    @router.post("/settings")
    async def write_settings(request: Request, user=Depends(admin_only)):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "ההגדרות חייבות להיות אובייקט JSON")
        if "server_name" in body:
            # נדב 17/10: שם השרת הוא שם המארח — שם אחד, משתנה במקום אחד.
            raise HTTPException(400, "שם השרת הוא שם המארח — משנים אותו ב-POST /hostname")
        unknown = next((key for key in body if key not in WRITE_SETTINGS), None)
        if unknown is not None:
            raise HTTPException(400, f"הגדרה לא מוכרת: {unknown}")
        # ‏#1794: validate the complete request before writing any key. The
        # validator is deliberately exhaustive over WRITE_SETTINGS.
        normalized = _validate_write_settings(body)
        set_settings(ctx.conn, normalized)
        for key, value in normalized.items():
            journal(ctx.conn, "setting_change", f"{key}={value}", user[0])
        return {"ok": True}

    console_mfa.register(router, ctx, current_user, admin_only, tls)
    console_disk_sets.register(router, ctx, admin_only)   # ‏#1857
    return router
