"""סבב חדר השיכפולים — הפצה מצטברת על פני גלים (אפיון סעיף 29).

הסבב חי בשרת ומצהיר יעד סך-כוננים. כל גל הוא session רגיל על קבוצת
השיכפול — אותו מנוע שידור, אותם דיווחי התקדמות. מה שמייחד את החדר:

- המוכנות נמדדת בכוננים (מגירות), לא במחשבים. גל יוצא כשהמגירות
  הטריות מכסות את היתרה, או בלחיצת "התחל עכשיו". בלי טיימר —
  בחדר עומד אדם ליד המכונות.
- מגירה מזוהה ב-serial של הכונן: כונן שכבר נכתב בסבב לא נספר שוב,
  ומכונה שהמגירות שלה לא הוחלפו לא מצטרפת לגל הבא.
- כשגל מסתיים והיעד לא הושג, הגל הבא נפתח אוטומטית — "כיבוי,
  החלפת מגירות, הדלקה — סבב שני מתחיל אוטומטית".

הערה: אין כאן `from .api import ServerContext` — זה היה סוגר מעגל
(api → hello → room). ctx מגיע כפרמטר ומשמש כפי שהוא.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from . import (auth, disk_failures, drawer_cancel, drawer_slots, drive_rates, drivers, minfree, registry, reports,
               room_drivers,
               room_precheck, slot_names, smart_ask, storage_locations)
from . import model_slots   # #2001
from .db import _write_lock, journal, now_iso, update_one, writing
from . import clockstep, learn_eta, learn_models, learn_ports, pxe_log, readbackhash, storage_history
from . import held   # #2031
from .images import required_bytes, restore_refusal
from .imagefit import validate_expand_choice
from .sessions import SessionError, SessionStore, SessionSuperseded
from .tasks import request_origin

log = logging.getLogger("imagectl.room")

CLONERS_GROUP = registry.CLONERS_GROUP
#: ‏#1554: חברי החדר — כל קבוצה בתפקיד cloner (ראו registry.ROOM_GROUPS).
_IN_ROOM = f"group_id IN {registry.ROOM_GROUPS}"
#: מי רשאי להפעיל את חדר השיכפולים. רשימת-היתר מפורשת, ולא "מחובר".
#:
#: אותה הכרעה כמו ``station.ROUND_OPENER_ROLES`` (#94), ובאותו נימוק:
#: היום זה כל התפקידים שקיימים ולכן זה לא משנה התנהגות, אבל הקוד שלף
#: את ``admin_only`` ומעולם לא בדק דבר — ותפקיד שלישי (צופה, מבקר,
#: חשבון ניטור) היה מקבל ביום היוולדו את ``/wake`` ו-``/start``, שהם
#: הפעולה שדורסת כל מגירה מחוברת בחדר. "לא ידענו מה התפקיד" הוא סירוב.
#:
#: ``GET`` נשאר פתוח לכל מחובר — קריאת מצב אינה הרסנית.
ROOM_OPERATOR_ROLES = ("admin", "deploy")
#: מכונה שדיברה עם השרת בטווח הזה נחשבת ערה — הסוכן דוגם כל ~2 שניות.
AWAKE_SECONDS = 30
#: מעל כמה שניות בלי **דיווח התקדמות** חבר גל מוכרז אבוד (#450). הדופק
#: הנמדד הוא progress ולא hello: מכונה שכותבת מפסיקה לשלוח hello אך
#: מדווחת התקדמות כל ~2 שניות (#449), ולכן הפקעה לפי hello הייתה קוטלת
#: מכונה בריאה עסוקה בכתיבה. ‏`updated_at` זז על כל דיווח (וגם על מגירה
#: קפואה — הוא זמן הדיווח, לא זמן התנועה), ומכונה שנעלמה מפסיקה להזיז
#: אותו. הסף נגזר מקצב הדיווח (~2 ש') עם שוליים גסים; אינו מדידת שדה.
LOST_SECONDS = 180
#: ‏#1721: WoL חוזר לגל פתוח, כל `REWAKE_SECONDS`, עד `REWAKE_MAX` פעמים, למחשב
#: שיכפול שנבחר, שיש לו עוד מה לכתוב ושלא נראה מאז שהגל נפתח. ה-WoL של הפתיחה
#: נשלח כשהמשכפל עוד בספירת הכיבוי (‏`END_POWEROFF_S`=20, ועד `END_HOLD_S`=300
#: כשעצרו אותה), ומכונה דלוקה בולעת אותו. ‏6×60 מכסה את 320 השניות של ספירה
#: שנעצרה; תקרה — כי WoL בלולאה פתוחה מעמיס על הרשת. אחרי התקרה,
#: `REWAKE_BOOT_SECONDS` לאתחול, ומכונה שעדיין לא נראתה מפילה את הסבב בשמה.
REWAKE_SECONDS = 60
REWAKE_MAX = 6
REWAKE_BOOT_SECONDS = 180

#: ‏17/10: חלון קצב שלא התחדש כך וכך שניות — הדיווחים פסקו, והקצב שבו ישן.
#: שלושה חלונות של 10 ש' (‏`reports.RATE_WINDOW_S`), עם דיווח כל ~2 ש'.
RATE_STALE_S = 30


# --- שאילתות טהורות ----------------------------------------------------------


def active_round(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM room_rounds WHERE state = 'active' LIMIT 1"
    ).fetchone()


def visible_round(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """מצב החדר החי או הכושל שעדיין דורש את תשומת לב המפעיל."""
    # החי קודם: חדר שנפתח מחדש באותה שנייה שבה הקודם נכשל חולק איתו
    # `created_at`, והסדר לבדו החזיר את הכושל למסך (rowid שובר את השוויון).
    row = conn.execute(
        "SELECT * FROM room_rounds WHERE state IN ('active', 'closing') "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM room_rounds ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    return (row if row is not None
            and row["state"] in ("active", "closing", "failed") else None)


def fail_wave(conn: sqlite3.Connection, session_id: str, reason: str,
              *, commit: bool = True) -> bool:
    """הופך כשל שולח לסופי בחדר; רק מפעיל יכול לפתוח אותו מחדש.

    ‏`commit=False` — הקורא כבר בתוך טרנזאקציה (‏`sessions.fail` מריץ אותה
    כ-`related` באותו מעבר אטומי); אחרת הכתיבה עוברת ב-`update_one`, שגם
    משחרר נעילה כשאף שורה לא תאמה (#54), ומסתיימת ב-`writing` (#525).
    """
    sql = ("UPDATE room_rounds SET state = 'failed', failed_reason = ?, closed_at = ?"
           " WHERE wave_session_id = ? AND state = 'active'")
    args = (reason, now_iso(), session_id)
    if not commit:
        return conn.execute(sql, args).rowcount == 1
    with writing(conn):
        return update_one(conn, sql, args)


def _expected(round_row: sqlite3.Row, machines: int) -> int:
    """‏17/10: כמה מחשבים בגל — מחשבי הסבב, לא כל הקבוצה ("1/3 מחשבים" בקונסולה על
    סבב של מחשב אחד). לגל של חדר השיכפולים המספר אינו תנאי התחלה (`maybe_start`)."""
    roster = _roster(round_row)
    return len(roster) if roster else max(1, machines)


def _roster(round_row: sqlite3.Row) -> list[str] | None:
    """‏נדב 17/10: גל של סבב עם בחירה מעיר ומצרף רק את מחשבי השיכפול שבה —
    ‏`wol_sent grp_CLONERS count=3` העיר את כל החדר, כולל מכונה שאסור להעיר.
    ‏None = סבב ישן בלי בחירה: כל הקבוצה, כמו קודם (‏`store.open`)."""
    raw = round_row["target_slots_json"]
    return None if raw is None else sorted({item["mac"] for item in json.loads(raw)})


def _written(round_row: sqlite3.Row) -> set[str]:
    return set(json.loads(round_row["written_serials"]))


def _skipped(round_row: sqlite3.Row) -> set[str]:
    """‏#1760 (ברזל D6b, 18/10): המגירות שהסוכן דילג עליהן כאדומות בסבב הזה —
    ‏SMART נכשל או קריאה בלבד. אדום **לעולם אינו יעד** (‏#1732), ולכן מגירה כזו
    סגורה: לא נכתבה, אבל גם אינה "טרייה" — היא אינה פותחת גל ואינה מעירה."""
    return set(json.loads(round_row["skipped_serials"] or "[]"))


def _cancelled(round_row: sqlite3.Row) -> set[str]:
    """‏#1936 (FI O2, 21/10): המגירות שמפעיל ביטל בסבב הזה ("בטל דיסק זה", #1386).
    ביטול של מפעיל **סופי לסבב**: המגירה סגורה, לא נכתבה ואינה נכשלה — היא אינה
    פותחת גל, אינה מעירה ואינה נכתבת שוב בגל הבא. בסבב הבא היא ירוקה ונכתבת רגיל."""
    return set(json.loads(round_row["cancelled_serials"] or "[]"))


def _closed(round_row: sqlite3.Row) -> set[str]:
    """מה שהסבב כבר אינו ממתין לו: נכתב, דולג כאדום (#1760), או בוטל בידי מפעיל (#1936)."""
    return _written(round_row) | _skipped(round_row) | _cancelled(round_row)


def _remaining(round_row: sqlite3.Row) -> int:
    """כמה מגירות הסבב עוד צריך לכתוב: היעד פחות מה שנכתב, פחות מה שדולג כאדום
    (#1760) ופחות מה שמפעיל ביטל (#1936) — שתיהן סוגרות את המקום שלהן ביעד, הן אינן
    ממתינות להחלפה."""
    return (round_row["target_drives"] - round_row["written_drives"]
            - len(_skipped(round_row)) - len(_cancelled(round_row)))


def _red(disk: dict) -> bool:
    """‏#1732: ה-hello האחרון אמר `smart: "fail"` (‏SMART נכשל או קריאה בלבד, #1742)."""
    return disk.get("smart") == "fail"


def _selected_ports(round_row: sqlite3.Row | None, mac: str) -> set[int] | None:
    """הפורטים שנבחרו לכתיבה עבור המכונה הזאת בסבב. ‏None = סבב ישן (כל
    הדיסקים). קבוצה ריקה = המכונה לא נבחרה כלל."""
    if round_row is None or round_row["target_slots_json"] is None:
        return None
    for item in json.loads(round_row["target_slots_json"]):
        if item["mac"] == mac:
            return set(item["ports"])
    return set()


def _unconfirmed(round_row: sqlite3.Row, mac: str) -> set[int]:
    """‏#1954: חריצים שנבחרו כשהמשכפל היה כבוי ועוד לא אומת שהדיסק בהם הוא זה
    שנבחר (‏`expect` שלא נפתר — hello בלי מלאי). אינם נכתבים: "לא בדקנו" אינו
    "אותו דיסק" (עיקרון 5)."""
    for item in json.loads(round_row["target_slots_json"] or "[]"):
        if item.get("mac") == mac and isinstance(item.get("expect"), dict):
            return {int(port) for port in item["expect"]}
    return set()


def wake_check(conn: sqlite3.Connection, mac: str, disks) -> None:
    """‏#1954 (נדב 22/10): משכפל שנבחרו לו דיסקים כשהיה כבוי עלה — ה-hello שלו
    (`disks`, המלאי של עכשיו) מכריע פעם אחת לכל חריץ שנקשר ל-serial: אותו דיסק —
    הקשירה יורדת והחריץ נכתב כרגיל (גם בגלים הבאים, עם מגירות שהוחלפו); דיסק אחר
    או חריץ ריק — **החריץ יוצא מהבחירה של הסבב ולא נכתב**, היעד יורד באחד,
    ‏`room_drawer_wake_mismatch` ביומן, והקונסולה אומרת אותו (`wake_skipped`).
    אסור לכתוב לדיסק שלא נבחר. hello בלי מלאי, או חריץ שנראה ריק כשבמלאי יש
    דיסק בלי חריץ (סוכן ישן, כונן לא ממופה) — אין הכרעה: החריץ נשאר קשור ואינו
    נכתב (`_unconfirmed`)."""
    round_row = active_round(conn)
    if (round_row is None or round_row["target_slots_json"] is None
            or not isinstance(disks, list)):
        return
    raw = round_row["target_slots_json"]
    slots = json.loads(raw)
    item = next((x for x in slots if x.get("mac") == mac and isinstance(x.get("expect"), dict)), None)
    if item is None:
        return
    disks = [d for d in disks if isinstance(d, dict)]
    found = {d["port"]: d.get("serial") for d in disks
             if isinstance(d.get("port"), int) and not isinstance(d.get("port"), bool)}
    # דיסק בלי חריץ (סוכן ישן, כונן לא ממופה): חריץ שנראה ריק אינו ראיה שהוא ריק — לא מכריעים עליו.
    unported = len(found) < len(disks)
    expect = item["expect"]
    keep = {p: s for p, s in expect.items() if found.get(int(p)) is None and unported}
    gone = [(int(p), s, found.get(int(p))) for p, s in sorted(expect.items(), key=lambda kv: int(kv[0]))
            if p not in keep and found.get(int(p)) != s]
    if keep == expect:
        return                                   # nothing decided: no slot of it is known
    if keep:
        item["expect"] = keep
    else:
        item.pop("expect")
    if gone:
        item["ports"] = [p for p in item["ports"] if p not in {g[0] for g in gone}]
        item["wake_skipped"] = [{"port": p, "serial": s, "found": f} for p, s, f in gone]
    # התביעה: רק מי שראה את אותה בחירה מעדכן (שני hello במקביל — אחד מכריע).
    # ‏#54: הכתיבה בתוך `_write_lock` + `writing` — כתיבה שנכשלה אינה משאירה נעילה יתומה.
    with _write_lock, writing(conn):
        claimed = update_one(
            conn,
            "UPDATE room_rounds SET target_slots_json = ?, target_drives = MAX(0, target_drives - ?)"
            " WHERE id = ? AND state = 'active' AND target_slots_json = ?",
            (json.dumps(slots), len(gone), round_row["id"], raw),
        )
    if not claimed:
        return
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    for port, serial, now in gone:
        journal(conn, "room_drawer_wake_mismatch",
                f'{round_row["id"]} {row["suffix"] if row else mac} port={port}'
                f" chosen={serial} found={now or 'missing'}")


def target_ports(round_row: sqlite3.Row | None, mac: str,
                 disks: list | None) -> list[int] | None:
    """‏`session.target_ports` של מחשב שיכפול בגל: מה שהמכונה כותבת **בגל הזה**.

    ‏#1790 (ברזל D2, 19/10, ‏room_41d7feed): גל 2 קיבל את כל החריצים שנבחרו, והמשכפל
    כתב מחדש גם את מגירה 3 שכבר הצליחה בגל 1 (~6 דקות וכתיבה מלאה על דיסק תקין).
    חריץ יוצא מהרשימה רק על **ראיה חיובית** (עיקרון 5): המלאי של ה-hello הזה עצמו
    (`disks`) אומר שבחריץ יושב דיסק שהסידורי שלו כבר סגור **בסבב הזה** — נכתב
    (`written_serials`) או דולג כאדום (`skipped_serials`, ‏#1760). דיסק שהוחלף (סידורי
    אחר), דיסק בלי סידורי, או hello בלי מלאי — החריץ נשאר, והמגירה נכתבת.
    ‏None = סבב ישן בלי בחירה (‏`_selected_ports`)."""
    ports = _selected_ports(round_row, mac)
    if ports is None:
        return None
    ports -= _unconfirmed(round_row, mac)     # #1954: chosen asleep, not yet seen to be the same disk
    if round_row is None or not isinstance(disks, list):
        return sorted(ports)
    closed = _closed(round_row)
    done, open_ = set(), set()
    for d in disks:
        if not isinstance(d, dict):
            continue
        serial = d.get("serial")
        (done if isinstance(serial, str) and serial and serial in closed else open_).add(d.get("port"))
    return sorted(p for p in ports if p not in done or p in open_)


def _disks(conn: sqlite3.Connection, mac: str) -> list[dict]:
    row = conn.execute(
        "SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()
    if row is None or not row["disks_json"]:
        return []
    disks = json.loads(row["disks_json"])
    return disks if isinstance(disks, list) else []


def fresh_serials(conn: sqlite3.Connection, mac: str, written: set[str],
                  selected_ports: set[int] | None = None) -> list[str]:
    """המגירות של המכונה שעוד לא נכתבו בסבב הנוכחי. ‏selected_ports מסנן
    לפורטים שנבחרו בלבד (‏None = כל הדיסקים, סבב ישן).

    ‏#105: כונן בלי serial נשאר מחוץ לרשימה **בכוונה** — `_tally` סופר
    לפי serial, וכונן שנספר כאן כ"טרי" היה מצרף את המכונה לכל גל וכותב
    אותו שוב ושוב בלי שייספר לעולם. הוא אינו נעלם: `drawer_list` מחזיר
    לו `fresh: None`, והמסך אומר "לא ניתן לזהות את הכונן" בחריץ שלו."""
    return [d["serial"] for d in _fresh_disks(conn, mac, written, selected_ports)]


def _fresh_disks(conn: sqlite3.Connection, mac: str, written: set[str],
                 selected_ports: set[int] | None) -> list[dict]:
    return [
        d for d in _disks(conn, mac)
        if isinstance(d, dict) and d.get("serial") and d["serial"] not in written
        and (selected_ports is None or d.get("port") in selected_ports)
        and not _red(d)                     # #1760: red is never a target -- not fresh
    ]


def wave_drives(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """‏#447: הכוננים שהגל הזה יכתוב — לפיהם נבחרת תקרת הקצב שלו.

    אותן מגירות ש-`ready_drives` סופר: טריות, ובפורטים שנבחרו, של המכונות
    שהצטרפו. גל שאינו גל של סבב חדר פעיל — רשימה ריקה, והגל יוצא בקצב
    הפתיחה (`drive_rates.choose`)."""
    round_row = conn.execute(
        "SELECT * FROM room_rounds WHERE wave_session_id = ? AND state = 'active'",
        (session_id,),
    ).fetchone()
    if round_row is None:
        return []
    written = _closed(round_row)
    return [
        {"serial": d["serial"], "model": d.get("model") or None,
         "mac": member["mac"], "port": d.get("port")}
        for member in conn.execute(
            "SELECT mac FROM session_members WHERE session_id = ? ORDER BY mac",
            (session_id,))
        for d in _fresh_disks(conn, member["mac"], written,
                              _selected_ports(round_row, member["mac"]))
    ]


def has_fresh_drawers(conn: sqlite3.Connection, mac: str) -> bool:
    """שער ההצטרפות לגל: מכונה שהמגירות שלה כבר נכתבו נשארת בהמתנה.

    בלי סבב חדר פעיל אין מה לסנן — מצטרפים כרגיל.
    """
    round_row = active_round(conn)
    if round_row is None:
        return True
    return bool(fresh_serials(conn, mac, _closed(round_row),
                              _selected_ports(round_row, mac)))


def drawer_list(conn: sqlite3.Connection, mac: str, written: set[str],
                member: sqlite3.Row | None = None,
                selected_ports: set[int] | None = None,
                started_at: str | None = None,
                skipped: set[str] | None = None,
                cancelled: set[str] | None = None) -> list[dict]:
    """המגירות של המכונה כפי שהטכנאי רואה אותן: חריץ, התקן ומצב (#27).

    ‏`port` הוא ה-ataN שהסוכן דיווח בממשק 2 — החריץ הפיזי, לא סדר הגילוי
    של הקרנל. סוכן ישן, VM או בקר לא-ATA לא מדווחים אותו, והוא יוצא `null`:
    הקונסולה נופלת חזרה לתצוגה לפי שם ההתקן, בלי להיכשל (עיקרון 1).

    ‏`started_at` (‏#410) — מתי הגל יצא לדרך; ממנו נגזרים `rate_bps` ו-`eta_s`
    לכל מגירה. בלעדיו שניהם `None` — "לא נמדד", לא 0.

    ‏`skipped` (‏#1760) — ה-`skipped_serials` של הסבב: מגירה שדולגה כאדומה סגורה,
    כמו בחדר עצמו (`_closed`). ‏`fresh: false` עם `skipped_red: true` — "דולגה, לא
    נכתבה"; ‏`fresh: false` בלעדיו נשאר "נכתבה".

    ‏`cancelled` (‏#1936) — ה-`cancelled_serials` של הסבב: מגירה שמפעיל ביטל סגורה
    באותו אופן — ‏`fresh: false` עם `cancelled_round: true` = "בוטלה, לא נכתבה".
    """
    skipped = skipped or set()
    cancelled = cancelled or set()
    targets = {}
    cancels: dict[str, dict] = {}
    if member is not None:
        targets = {t.get("dev"): t
                   for t in json.loads(member["targets_json"] or "[]")
                   if isinstance(t, dict)}
        if "session_id" in member.keys():               # #1386 (a bare member: no wave to ask)
            cancels = drawer_cancel.by_dev(conn, member["session_id"], mac)
    drawers = []
    for disk in _disks(conn, mac):
        if not isinstance(disk, dict):
            continue
        port = disk.get("port")
        target = targets.get(disk.get("dev")) or {}
        drawers.append({
            "dev": disk.get("dev"),
            "port": port if isinstance(port, int) and not isinstance(port, bool)
            else None,
            # ‏#1495: כתובת ה-PCI של בקר NVMe (‏hello, ‏#1485) — בלעדיה `drawer` של
            # כונן NVMe היה תמיד None, גם כשהוא ממופה כדיסק N.
            "pci": disk.get("pci") if isinstance(disk.get("pci"), str) else None,
            # ‏#553: הסריאל והדגם הם מה שמזהה כונן **ביד**. ‏`port` אומר
            # לאיזו מגירה ללכת; הסריאל אומר איזה כונן זה כשמחזיקים
            # אותו — וכשאין `port` (‏NVMe, ‏VM, בקר לא-ATA) הוא כל מה
            # שנשאר. שורת כשל בלי אחד מהם שולחת אדם לחפש.
            "serial": disk.get("serial") or None,
            "model": disk.get("model") or None,
            # ‏#105: שלושה מצבים, לא שניים. ‏`None` = הכונן לא דיווח serial,
            # ולכן הסבב (שסופר לפי serial) אינו יכול לדעת אם הוא נכתב —
            # "לא ניתן לזהות את הכונן", לא `False` ("נכתבה") ולא `True`.
            "fresh": (disk["serial"] not in written and disk["serial"] not in skipped
                      and disk["serial"] not in cancelled)
            if disk.get("serial") else None,
            "skipped_red": bool(disk.get("serial")) and disk["serial"] in skipped,   # #1760
            "cancelled_round": bool(disk.get("serial")) and disk["serial"] in cancelled,   # #1936
            # ‏#695: האם הפורט הזה נבחר כיעד בסבב (לצביעת הגריד/בחירה).
            "selected": (selected_ports is not None
                         and isinstance(port, int) and not isinstance(port, bool)
                         and port in selected_ports),
            "state": target.get("state"),
            "error": target.get("error"),
            # ‏#1386: "בטל דיסק זה" — מי ביקש ומתי (`text_he`), ו-`done` = הסוכן דיווח
            # `cancelled`. ‏None = לא בוטל. בקשה בלי `done` על מגירה שכותבת = "מבטל…".
            "cancel": cancels.get(disk.get("dev")),
            # ‏#1269: תוצאת הנחת הדרייברים על המגירה הזו (מנורמלת ב-ingest).
            # ‏None = השלב לא רץ עליה — לא `no_match`.
            "drivers": target.get("drivers"),
            # ‏#1883: כמה זמן ארך כל שלב (‏`[{stage, seconds}]`, ‏`restore_stages.clean`). ‏None = לא נמדד.
            "stages": target.get("stages"),
            # ‏#1805: הקריאה החוזרת מהדיסק אחרי הכתיבה — `verified` / `sampled` (‏#1864:
            # "נבדק במדגם", לא "אומת") / `unverified`
            # (לאימג' אין גיבוב: "לא אומת", לעולם לא "אומת"). ‏None = לא דווח
            # (סוכן ישן, או שהשלב עוד לא רץ). כשל של הקריאה הוא `failed` + `error`.
            "readback": target.get("readback")
            if target.get("readback") in ("verified", "sampled", "unverified") else None,
            "readback_reason": target.get("readback_reason")
            if isinstance(target.get("readback_reason"), str) else None,
            # ‏#1907 (נדב 22/10): `unverified` מסיבה משלו — רשימת הבדיקה לא הורדה מהשרת
            # אחרי כל הניסיונות. ‏None = כל סיבה אחרת (או סוכן ישן).
            "readback_why": "list_not_fetched"
            if target.get("readback") == "unverified"
            and target.get("readback_why") == "list_not_fetched" else None,
            # ‏#1366: MB/s של בדיקת הכתיבה לפני הזרם (מדוח הסוכן). ‏None = לא נמדד —
            # לא 0 (עיקרון 5ב). דיסק איטי כבר נכשל בשמו (`error`): זה המספר עצמו.
            "write_mbps": _write_mbps(target),
            # ‏#1845: מול הדגם — המדידה של הגל הזה, ובלעדיה האחרונה של הדיסק, מול חציון
            # הדיסקים האחרים מאותו דגם. ‏`slow: null` = אין מדידה או אין בסיס — רק הסף המוחלט.
            "model_speed": learn_models.judge(conn, disk.get("model") or None,
                                              disk.get("serial") or None, _write_mbps(target)),
            # ‏#552: המפעיל ראה ‏"0 of 5 written" במשך 75 דקות. המונה
            # הזה סופר מגירות ש**סיימו**, ולכן הוא 0 גם כשהכול רץ מצוין
            # וגם כשהכול תקוע — ואי-אפשר להבחין. הבייטים הם ההבחנה.
            "bytes_written": _int(target.get("bytes_written")),
            "bytes_total": _int(target.get("bytes_total")),
            "stalled_s": _stalled_s(target.get("moved_at")),
            # ‏#410: קצב ו-ETA — מהבייטים שדווחו, לא מהערכה. ‏None = לא נמדד.
            **_pace(target, started_at),
            # ‏#872: בכמה עלה מונה ה-CRC (199) בסבב הזה, מדוח הסוכן. ‏None =
            # לא נמדד — לא 0 (עיקרון 5). >0 מוצג "CRC +N · לבדוק כבל", ואינו
            # צובע את הדיסק.
            "crc_delta": target["crc_delta"]
            if isinstance(target.get("crc_delta"), int)
            and not isinstance(target.get("crc_delta"), bool) else None,
            # ‏#652: בריאות SMART כפי שדווחה ב-hello (ממטמון הסוכן), כדי
            # שהמפעיל יראה "מגירה N: SMART אזהרה" **לפני** שהוא משגר סבב.
            # ‏`unchecked` = לא-נבדק, לא כשל (עיקרון 5).
            "smart": disk.get("smart") or "unchecked",
            # ‏#1366: למה `fail` (‏health_failed / pending_sectors / uncorrectable) —
            # "ה-BIOS יעצור ב-F1". ‏None = סוכן ישן או דיסק ירוק.
            "smart_reason": disk.get("smart_reason") or None,
            # ‏#1798: סימן "כתיבה לא הושלמה" שעל הדיסק עצמו (hello, ‏writemark.sh) —
            # ‏`incomplete` / `clean` / `unchecked`. ‏None = סוכן ישן — לא "תקין".
            "write_mark": disk.get("write_mark")
            if disk.get("write_mark") in ("incomplete", "clean", "unchecked") else None,
            # ‏#710: הגודל שהסוכן דיווח (sysinfo.sh), כדי שהגריד יראה קיבולת
            # אמיתית לכל דיסק ולא 0. חסר => 0, לא נחסם (עיקרון 5).
            "size_bytes": _int(disk.get("size_bytes")),
            # ‏#432: קישור ה-SATA (מהירות/מקסימום/מצב) ועליית מונה ה-CRC מאז
            # הסבב הקודם של הדיסק. ‏None/`unknown` = לא נקרא — לא "תקין".
            **room_precheck.drawer_fields(conn, disk, mac),
        })
    return drawers


def _write_mbps(target: dict) -> int | None:
    pre = target.get("write_preflight")
    mbps = pre.get("mbps") if isinstance(pre, dict) else None
    return mbps if isinstance(mbps, int) and not isinstance(mbps, bool) and mbps > 0 else None


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pace(target: dict, started_at: str | None) -> dict:
    """‏#410: ‏`rate_bps` ו-`eta_s` של מגירה, מהבייטים שדווחו.

    ‏17/10 (ברזל): הקצב הוא **הקצב העדכני** — ‏`recent_bps`, הבייטים בחלון
    האחרון של 10 שניות לפחות (‏`reports._stamp_window`). עד כאן הוא היה ממוצע
    מאז שהגל יצא, והמכנה כלל את ההמתנה לפני הבייט הראשון (38 ש' ב-
    ‏`ses_fbd8f9ca`, דקות כששאלת SMART פתוחה) ובין המחיצות — מספר שאינו
    "עכשיו". ‏ETA = מה שנשאר חלקי הקצב, רק כשיש סך ורק כשהמגירה כותבת.

    ⚠️ ‏`None` ≠ `0` (עיקרון 5): גל שאינו רץ, חלון שטרם נסגר, חלון שלא זז
    (העצירה נראית ב-`stalled_s`), או חלון שהתיישן — הדיווחים פסקו יותר מ-
    ‏`RATE_STALE_S` — כולם "לא נמדד", ומסך שמצייר אותם כאפס אומר "עצר".
    """
    none = {"rate_bps": None, "eta_s": None}
    if target.get("state") != "writing" or _iso_or_none(started_at) is None:
        return none
    rate, age = target.get("recent_bps"), _stalled_s(target.get("rate_at"))
    if (not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0
            or age is None or age > RATE_STALE_S):
        return none
    written = _int(target.get("bytes_written"))
    total = _int(target.get("bytes_total"))
    eta = int((total - written) / rate) if total > written else None
    return {"rate_bps": int(rate), "eta_s": eta}


def _stalled_s(moved_at: str | None) -> int | None:
    """כמה שניות עברו מאז שהבייטים של המגירה הזאת גדלו — ‏`None` כשלא ידוע.

    ⚠️ **‏`None` ו-`0` הם שני מצבים שונים ואסור לקפל אותם.** ‏`0` הוא
    "נמדד, והמגירה זזה עכשיו"; ‏`None` הוא "אין חותמת" — מגירה שטרם
    דיווחה, או שרת שהתחיל לפני שהשדה הזה קיים. מסך שמצייר `None`
    כאפס מציג "הכול זז" על מגירה שלא נמדדה כלל (עיקרון 5).
    """
    if not moved_at:
        return None
    try:
        moved = datetime.fromisoformat(moved_at)
    except (TypeError, ValueError):
        return None
    elapsed = (datetime.now(timezone.utc) - moved).total_seconds()
    # A future persisted timestamp is a clock step, not evidence of recent
    # progress.  Make it immediately due for the stall sweep instead of
    # freezing the drawer until wall time catches up.
    return STALL_SECONDS if elapsed < 0 else int(elapsed)


def ready_drives(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row) -> int:
    """כמה כוננים טריים מחוברים למכונות שכבר הצטרפו לגל הפתוח."""
    written = _closed(round_row)
    return sum(
        len(fresh_serials(conn, member["mac"], written,
                          _selected_ports(round_row, member["mac"])))
        for member in store.members(round_row["wave_session_id"])
    )


def red_drives(conn: sqlite3.Connection, store: SessionStore,
               round_row: sqlite3.Row) -> int:
    """‏#1760: כמה מגירות שהגל הפתוח היה ממתין להן ה-hello האחרון אומר שהן
    אדומות (ולא נכתבו או נסגרו כבר) — הסוכן ידלג עליהן, והגל אינו ממתין להן.
    המכונות: מי שנבחר לסבב (‏roster) — גם מי שלא הצטרף, כי משכפל שכל מגירותיו
    אדומות אינו מצטרף לעולם (‏has_fresh_drawers); סבב ישן בלי בחירה — מי שהצטרף."""
    closed = _closed(round_row)
    roster = _roster(round_row)
    macs = roster if roster is not None else [
        m["mac"] for m in store.members(round_row["wave_session_id"])]
    count = 0
    for mac in macs:
        ports = _selected_ports(round_row, mac)
        count += sum(
            1 for d in _disks(conn, mac)
            if isinstance(d, dict) and d.get("serial") and d["serial"] not in closed
            and (ports is None or d.get("port") in ports) and _red(d))
    return count


def precheck_now(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row) -> list[dict]:
    """‏#432: אותן אזהרות ש-`status_view` מציג, על חברי הגל הפתוח — ברגע
    שהוא יוצא לדרך, ביד או לבד."""
    written = _closed(round_row)          # #1760
    names = slot_names.all_by_mac(conn)
    machines = []
    for member in store.members(round_row["wave_session_id"]):
        mac = member["mac"]
        row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
        drawers = drawer_list(conn, mac, written, None, _selected_ports(round_row, mac))
        for d in drawers:
            d["slot_name"] = slot_names.resolve(names.get(mac, {}), d["port"], d["serial"],
                                                d.get("pci"))
        machines.append({"mac": mac, "name": row["suffix"] if row else None, "joined": True,
                         "drawer_list": drawers, "slot_names": names.get(mac, {})})
    return room_precheck.warnings(machines, round_row["target_slots_json"] is not None)


def journal_precheck(conn: sqlite3.Connection, round_row: sqlite3.Row,
                     warned: list[dict], user: str) -> None:
    """‏#432: "סבב שהתחיל בכל זאת חייב לרשום שהאזהרה הייתה שם". שורה אחת
    לגל, רק כשהייתה אזהרה; ‏`user` ריק = הגל יצא לבד (המגירות כיסו את היתרה)."""
    if not warned:
        return
    items = []
    for w in warned:
        item = f"{w['mac']}:{w['port'] if w['port'] is not None else w['serial']}"
        if room_precheck.link_state(w["link_gbps"], w["link_max_gbps"]) == "slow":
            item += f" link={w['link_gbps']:.1f}/{w['link_max_gbps']:.1f}"
        if w["crc_rise"]:
            item += f" crc+{w['crc_rise']}"
        if w.get("crc_suspect"):
            item += f" crc={w['crc']}"   # #1366: the absolute count over the threshold
        if w.get("port_suspect"):
            item += " learned=port"      # #1844: the same connection with different disks
        if w.get("disk_suspect"):
            item += " learned=disk"      # #1844: the same disk in different connections
        if w.get("model_slow"):
            item += " learned=slow_model"   # #1845: slower than the usual for its model
        items.append(item)
    # שורה אחת, בלי תקרת `_one_line`: תשע מגירות לא ייחתכו באמצע (סידורי מה-hello
    # הוא קלט חיצוני — רק שבירות השורה מוחלפות).
    detail = f"{round_row['id']} wave={round_row['wave_number']} " + ", ".join(items)
    journal(conn, "room_precheck_warned", " ".join(detail.splitlines()), user)


#: ‏#953: "‏256GB" על מדבקת כונן הוא 256×10⁹ — הקונסולה מדברת באותן יחידות.
GB = 10 ** 9


def disk_floor(conn: sqlite3.Connection, selected: list[dict] | None = None
               ) -> dict | None:
    """המגירה **הקטנה ביותר** שמחשבי השיכפול דיווחו ב-hello האחרון — הרצפה
    שאימג' חייב להיכנס אליה כדי שסבב ייכתב על כל המגירות (‏#953).

    ‏`selected` (‏#695) מצמצם לפורטים שנבחרו: מגירה שלא תיכתב אינה רצפה.
    בלי בחירה — כל המגירות של כל מחשבי החדר, כפי שהסבב אכן יכתוב אליהן.

    ‏`None` = **אין דיווח** (מכונות שטרם דיברו, או סוכן ישן בלי
    ‏`size_bytes`) — לא "הכול נכנס". הקורא מציג הערה, ובדיקה 2.7 במכונה
    היא הקו האחרון (עיקרון 5). גודל 0/חסר הוא "לא נמדד" ומדולג, לא רצפה.
    """
    chosen = ({item["mac"]: set(item["ports"]) for item in selected}
              if selected is not None else None)
    floor = None
    for row in conn.execute(
        f"SELECT mac, suffix FROM machines WHERE {_IN_ROOM} ORDER BY suffix",
    ):
        if chosen is not None and row["mac"] not in chosen:
            continue
        for disk in _disks(conn, row["mac"]):
            if not isinstance(disk, dict):
                continue
            port = disk.get("port")
            port = port if isinstance(port, int) and not isinstance(port, bool) else None
            if chosen is not None and port not in chosen[row["mac"]]:
                continue
            size = _int(disk.get("size_bytes"))
            if size <= 0:
                continue
            if floor is None or size < floor["size_bytes"]:
                floor = {"mac": row["mac"], "name": row["suffix"], "port": port,
                         "dev": disk.get("dev"), "size_bytes": size}
    return floor


class FitRefusal(SessionError):
    """‏`open_round` סירב כי האימג' אינו נכנס (`fit_refusal`). ‏SessionError
    כמו קודם — הקונסולה עונה ‏409 כרגיל; מסך המשכפל מבחין בו (קוד `fit`, ‏#1863)."""


def fit_refusal(manifest: dict, floor: dict | None, min_free_gb: int = 0) -> str | None:
    """הסיבה שהאימג' אינו נכנס לרצפה — או `None` כשהוא נכנס, או כשאין
    רצפה ידועה. **אותה מחרוזת בדיוק** שמסך החדר מציג על האימג' המושבת
    (‏`imageFitReason` ב-`console.js`), כדי שהמפעיל יראה סיבה אחת
    משני הצדדים: "דיסק 2 במחשב 2 הוא 256GB, האימג' צריך 300GB".

    ‏#1354: "צריך" כולל את המינימום הפנוי במחיצת המערכת (‏`min_disk_bytes`),
    וכשזה מה שהוסיף — המשפט אומר זאת.

    ‏#1494: ‏`used_bytes` של מחיצת המערכת שלא נמדד **הוא** סירוב, בלי קשר
    לרצפה: הסוכן מסרב לו בכל מגירה ("לא נבדק"), וסבב שנפתח עליו נכשל כולו
    (ממצא ברזל 16/10). עד #1494 זה החזיר `None` כאן, והסבב נפתח.
    """
    blocked = minfree.unchecked(manifest, min_free_gb)
    if blocked is not None:
        return blocked
    need = minfree.min_disk_bytes(manifest, min_free_gb)
    if need is None:
        # ‏#1863: דרישה שאינה ניתנת לקביעה אינה "נכנס" (עיקרון 5) — אותה הכרעה
        # שמסתירה אותו מרשימת המכונה (`allowed_for_disks`). ‏`None` כאן אחרי
        # ‏`unchecked`: פריסה חסרה, או מחיצת מערכת שלא תגדל לאף גודל דיסק.
        layout = required_bytes(manifest)
        return ((minfree.refusal(manifest, layout, min_free_gb) if layout is not None else None)
                or "לא ניתן לקבוע כמה מקום האימג' צריך, ולכן הוא אינו נכתב")
    if floor is None or need <= floor["size_bytes"]:
        return None
    slot = (f"דיסק {floor['port']}" if floor["port"] is not None
            else f"דיסק {floor['dev'] or '?'}")
    layout = required_bytes(manifest)
    extra = (f" (כולל {min_free_gb}GB פנויים במחיצת המערכת)"
             if layout is not None and need > layout else "")
    return (f"{slot} במחשב {floor['name']} הוא {round(floor['size_bytes'] / GB)}GB, "
            f"האימג' צריך {-(-need // GB)}GB{extra}")


# --- מחזור החיים -------------------------------------------------------------


def _validate_target_slots(conn: sqlite3.Connection, raw) -> list[dict]:
    """‏#695: מאמת את בחירת דיסקי היעד ומחזיר צורה קנונית. דוחה בגלוי כל
    קלט פגום — MAC זר, פורט מחוץ ל-drawer_count, פורט כפול, או פורט שאינו
    מחובר כרגע — כדי שלעולם לא ייכתב דיסק לא-נכון (עיקרון 4/5/7). אין
    fallback ל"כל הדיסקים": בחירה ריקה נדחית."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("יש לבחור לפחות דיסק יעד אחד")
    selected = []
    seen_macs = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("בחירת דיסקי היעד אינה תקינה")
        mac = registry.normalize_mac(item.get("mac", ""))
        ports = item.get("ports")
        if mac is None or mac in seen_macs or not isinstance(ports, list):
            raise ValueError("בחירת דיסקי היעד אינה תקינה")
        row = conn.execute(
            f"SELECT drawer_count, drawer_slots FROM machines WHERE mac = ? AND {_IN_ROOM}",
            (mac,),
        ).fetchone()
        if row is None:
            raise ValueError("נבחר מחשב שאינו שייך לחדר השכפול")
        model_map = model_slots.for_mac(conn, mac)   # #2001
        allowed_ports = drawer_slots.sata_ports(drawer_slots.effective(
            row["drawer_count"], row["drawer_slots"], model_map["slots"] if model_map else None))
        if (not ports or len(set(ports)) != len(ports) or any(
                isinstance(p, bool) or not isinstance(p, int)
                or p not in allowed_ports for p in ports)):
            raise ValueError(f"בחירת המגירות של {mac} אינה תקינה")
        disks = [d for d in _disks(conn, mac) if isinstance(d, dict)]
        live = {d.get("port") for d in disks if d.get("serial")}
        # ‏#105: כונן מחובר בלי serial אינו "אינה מחוברת" — זה שולח את
        # הטכנאי לבדוק כבל תקין. הוא נדחה, ובשם הנכון.
        unidentified = sorted(
            {d.get("port") for d in disks if not d.get("serial")}
            & set(ports) - live)
        if unidentified:
            raise ValueError(
                f"אי אפשר לפתוח סבב: לא ניתן לזהות את הכונן בחריץ "
                f"{unidentified[0]} ב־{mac} (הכונן לא דיווח serial)")
        if not set(ports) <= live:
            raise ValueError(
                f"אי אפשר לפתוח סבב: אחת המגירות שנבחרו ב־{mac} אינה מחוברת")
        # ‏#1732 (נדב 18/10): דיסק אדום (SMART נכשל, כפי שדווח ב-hello) לעולם
        # אינו יעד — בכל מסלול שעובר כאן (סבב חדר, סבב ממסך משכפל, הפצה
        # ישירה), גם כשהמסך לא סינן אותו. הסוכן מדלג עליו גם הוא (smartgate.sh).
        red = sorted(d.get("port") for d in disks
                     if d.get("smart") == "fail" and d.get("port") in ports)
        if red:
            raise ValueError(
                f"אי אפשר לפתוח סבב: דיסק {red[0]} ב־{mac} — {smart_ask.SMART_RED_HE}")
        item = {"mac": mac, "ports": sorted(ports)}
        # ‏#1954 (נדב 22/10): משכפל כבוי — הבחירה היא לפי הדיווח האחרון שלו, ולכן היא
        # נקשרת ל-serial של כל דיסק שנבחר. כשהוא עולה (`wake_check`) — אותו דיסק נכתב,
        # דיסק אחר או חריץ ריק מדולג בגלוי. משכפל ער: כמו קודם, לפי פורט.
        seen = conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?", (mac,)).fetchone()
        if not _is_awake(seen["last_seen"] if seen else None):
            item["expect"] = {str(d["port"]): d["serial"] for d in disks
                              if d.get("serial") and d.get("port") in ports}
        selected.append(item)
        seen_macs.add(mac)
    return sorted(selected, key=lambda item: item["mac"])


def library_packages(ctx) -> list[dict]:
    """החבילות שבספרייה עכשיו (‏#1269). ‏`ctx.drivers` חסר בבדיקות ישנות."""
    return list(ctx.drivers.scan().values()) if ctx.drivers is not None else []


def open_round(ctx, image_id: str, target_drives: int, user: str,
               target_slots=None, expand_partition=None, driver_models=None) -> dict:
    manifest = ctx.library.get(image_id)
    if manifest is None:
        raise ValueError("אימג' לא קיים בספרייה")
    unavailable = storage_locations.unavailable_message(manifest)
    if unavailable:
        raise SessionError(unavailable)
    # ‏#381: אימג' הקשור למכונה אחת אינו נשפך על מגירות. אין כאן רשימת
    # יעדים בכלל — הכוננים יותקנו במכונות שאיש עוד אינו יודע מי הן —
    # ולכן `restore_refusal` מסרב אותו, וזה הכיוון הנכון (עיקרון 5).
    refusal = restore_refusal(manifest, None)
    if refusal is not None:
        raise ValueError(refusal)
    if target_drives < 1:
        raise ValueError("יעד הכוננים חייב להיות חיובי")
    # ‏#59: בחירת ההרחבה חלה על **כל** גלי הסבב, ולכן היא נשמרת ברמת
    # ה-round ולא ב-session של גל בודד — כל גל חדש קורא אותה מחדש.
    expand_choice = validate_expand_choice(manifest, expand_partition)
    machines = ctx.conn.execute(
        f"SELECT COUNT(*) AS n FROM machines WHERE {_IN_ROOM}"
    ).fetchone()["n"]
    if machines == 0:
        raise ValueError("אין מחשבי שיכפול רשומים — רשמו אותם בקונסולה קודם")
    if active_round(ctx.conn) is not None:
        raise SessionError("כבר יש סבב חדר פעיל")

    # ‏#1410: בחירת דיסקי היעד מפורשת וחובה. מאמתים אותה לפני שנוגעים
    # בשום דבר; אין fallback לכתיבה לכל המגירות.
    selected = _validate_target_slots(ctx.conn, target_slots)
    selected_count = sum(len(item["ports"]) for item in selected)
    if target_drives < selected_count:
        raise ValueError(
            "מספר היעדים בסבב קטן ממספר הדיסקים שנבחרו לגל הראשון")
    # ‏#1269: דגם דרייברים לכל דיסק יעד — אופציונלי; מאומת לפני כל כתיבה.
    chosen_drivers = room_drivers.validate(driver_models, selected, library_packages(ctx))
    # ‏#953: אימג' שאינו נכנס למגירה הקטנה ביותר שדווחה אינו נפתח — לא רק
    # מושבת במסך. הסירוב נוקב במגירה ובמספרים; ‏409 כמו כל התנגשות עם מצב
    # החדר. בלי דיווח (`None`) אין סירוב — בדיקה 2.7 במכונה תסרב לה לבדה.
    refusal = fit_refusal(manifest, disk_floor(ctx.conn, selected),
                          minfree.min_free_gb(ctx.conn))   # #1354
    if refusal is not None:
        raise FitRefusal(refusal)

    # פתיחת הגל תופסת את חריץ הסבב היחיד במערכת ומעירה את החדר (WoL).
    wave_id = ctx.store.open(
        CLONERS_GROUP, image_id, prefix="ROOM",
        expected_clients=len(selected), opened_by=user, expand_partition=expand_choice,
        roster=[item["mac"] for item in selected],   # נדב 17/10: מעירים רק את מי שנבחר
    )
    round_id = "room_" + secrets.token_hex(4)
    ctx.conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json,"
        " expand_partition, driver_models_json, state, wave_session_id, opened_by,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (round_id, image_id, target_drives,
         json.dumps(selected, separators=(",", ":")),
         expand_choice,
         json.dumps(chosen_drivers, ensure_ascii=False) if chosen_drivers else None,
         wave_id, user, now_iso()),
    )
    ctx.conn.commit()
    readbackhash.kick(image_id)   # #1864: digest the round's image first, while its machines boot
    storage_history.sample_local(ctx.conn, getattr(ctx, "library", None), "round")   # #1886
    journal(ctx.conn, "room_open",
            f"{round_id} {image_id} target={target_drives}"
            # ‏#59: אותו כלל כמו session_open — "auto" אינו נרשם, כדי
            # שסבב שלא נגע בהרחבה לא ייראה כמו החלטה שמישהו קיבל.
            + (f" expand={expand_choice}" if expand_choice != "auto" else "")
            + (f" drivers={len(chosen_drivers)}" if chosen_drivers else ""),
            user)
    return {"id": round_id, "wave_session_id": wave_id}


def round_label(ctx, round_row: sqlite3.Row) -> str:
    """שם הסבב כפי שהמפעיל רואה אותו — שם האימג' שהוא משדר.

    לסבב אין שם משלו: יש `id` אקראי שאינו מופיע על שום מסך, ומספר גל
    שהוא ספרה. מה שהמפעיל **קורא** בכותרת הוא שם האימג', וזה מה שהוא
    מקליד כדי לעצור (עיקרון 7). הנפילה חזרה ל-`image_id` היא אותה
    נפילה בדיוק כמו ב-`status_view` — **מקום אחד**, אחרת סבב שהאימג'
    שלו נמחק באמצע היה בלתי-ניתן לעצירה.
    """
    # ‏#715: לסבב מדיסק חי אין אימג' בספרייה — השם הוא `<מכונה>:<דיסק>`,
    # וזה מה שהמפעיל מקליד כדי לעצור אותו.
    from . import direct                 # noqa: PLC0415 — direct מייבא את room
    if direct.is_build_disk(round_row):
        return direct.source_label(ctx.conn, round_row)
    manifest = ctx.library.get(round_row["image_id"])
    return manifest["name"] if manifest else round_row["image_id"]


def close_round(ctx, user: str, confirm_name: str) -> None:
    round_row = visible_round(ctx.conn)
    if round_row is None:
        raise SessionError("אין סבב חדר פעיל")
    # פעולה הרסנית מאחורי הקלדת שם — אותו דפוס כמו מחיקת אימג'
    # (`console_library.delete_image`), ובאותה שורה באפיון. לפני #533
    # ‏POST ריק עם עוגייה תקפה הרג משדר חי, וההקלדה נאכפה במסך בלבד.
    if confirm_name != round_label(ctx, round_row):
        raise ValueError("השם שהוקלד אינו זהה לשם האימג' שהסבב משדר")
    original_state = round_row["state"]
    if not update_one(
        ctx.conn,
        "UPDATE room_rounds SET state = 'closing'"
        " WHERE id = ? AND state IN ('active', 'failed')",
        (round_row["id"],),
    ):
        raise SessionError("סבב החדר כבר נסגר או נמצא בתהליך סגירה")
    ctx.conn.commit()
    try:
        wave = ctx.conn.execute(
            "SELECT id, state FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
        if wave is not None and wave["state"] in ("open", "running"):
            ctx.store.close(wave["id"], user)
        # store.close קורא on_closed → sender.stop רק כשהגל עוד open/running.
        # גל שכבר סגור (או חסר) היה מחזיר ok והמשדר נשאר חי (#439).
        # מזהה הגל מונע מסגירה מאוחרת של כשל ישן לעצור שידור חדש שכבר
        # נפתח בחריץ שהתפנה (#773).
        left = ctx.sender.stop(round_row["wave_session_id"])
        if left is not None:
            who = f"PID {left}" if left > 0 else "לא הצלחנו לוודא שהוא מת"
            raise SessionError(
                f"udp-sender עדיין רץ ({who}) אחרי ניסיון העצירה — הסבב לא נסגר"
            )
        ctx.conn.execute(
            "UPDATE room_rounds SET state = 'closed', closed_at = ?"
            " WHERE id = ? AND state = 'closing'",
            (now_iso(), round_row["id"]),
        )
        ctx.conn.commit()
    except Exception:
        ctx.conn.execute(
            "UPDATE room_rounds SET state = ? WHERE id = ? AND state = 'closing'",
            (original_state, round_row["id"]),
        )
        ctx.conn.commit()
        raise
    journal(ctx.conn, "room_close",
            f'{round_row["id"]} written={round_row["written_drives"]}'
            f'/{round_row["target_drives"]}', user)
    # ‏#715: המקור מפסיק עם הסבב — המשימה נסגרת, ומחשב הבנייה רואה זאת
    # ב-hello הבא ואינו משדר.
    from . import direct                 # noqa: PLC0415
    direct.cancel_source_task(ctx.conn, round_row, user)


def _iso_or_none(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _mark_lost(conn: sqlite3.Connection, store: SessionStore, wave: sqlite3.Row,
               members: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """מסמן `lost` חבר גל שלא דיווח התקדמות מעל `LOST_SECONDS` (#450).

    הגרייס נמדד מ-`max(updated_at, started_at)`: גל שרק התחיל לרוץ נותן
    לכל חבר `LOST_SECONDS` להשמיע קול ראשון לפני שהוא מוכרז אבוד — אחרת
    חבר שהצטרף בשלב הפתיחה (‏`updated_at` בן דקות, כי בפתיחה אין
    progress) היה מופקע ברגע שהגל מתחיל. שני מקורות `None` (אין חותמת)
    אינם "אבוד" אלא "לא נמדד", והכיוון הבטוח הוא לא להכריז (עיקרון 5).

    ה-UPDATE מותנה במצב המדויק שנקרא: דיווח שהגיע בין הקריאה לכתיבה כבר
    שינה אותו, והתביעה הזו נותנת לו לנצח (‏`update_one` גם משחרר את
    הנעילה כשלא תאם — #54). מחזיר את החברים המעודכנים כשמשהו השתנה.

    ‏#1947: חותמת **בעתיד** אינה "אבוד". היא קפיצת שעון שהגלאי של התהליך
    (`clockstep`) לא ראה — למשל קפיצה לפני הדגימה הראשונה אחרי עלייה. עד
    ‏#1947 היא סומנה אבודה מיד (#1771 רצה רק שלא תקפיא את הגל), וזה בדיוק
    מה שהפיל גל שלם כשהשעון זז אחורה. עכשיו היא מכוילת ל"עכשיו": המכונה
    מקבלת `LOST_SECONDS` מלאות מעכשיו — חסום, לא קפוא, ולא אובדן מיידי.
    """
    now = _utcnow()
    ahead = now + timedelta(seconds=clockstep.JUMP_SECONDS)
    floor = _iso_or_none(wave["started_at"])
    lost, future = [], []
    for m in members:
        if m["state"] in reports.FINAL:
            continue
        refs = [t for t in (_iso_or_none(m["updated_at"]), floor) if t is not None]
        if not refs:
            continue
        if max(refs) > ahead:
            future.append(m)
        elif (now - max(refs)).total_seconds() > LOST_SECONDS:
            lost.append(m)
    if future:
        _rebase_future(conn, wave, future, now)
    if not lost:
        return store.members(wave["id"]) if future else members
    marked = [
        m for m in lost
        if update_one(
            conn,
            "UPDATE session_members SET state = ?, updated_at = ?"
            " WHERE session_id = ? AND mac = ? AND state = ?",
            (reports.LOST, now_iso(), wave["id"], m["mac"], m["state"]),
        )
    ]
    if not marked:
        return members
    # ‏`update_one` כבר עשה commit לכל שינוי מצב, ו-`journal` נועל וכותב
    # בעצמו (#379) — ולכן אין כאן `conn.commit()` חשוף (#450, #54).
    for m in marked:
        journal(conn, "client_lost", f'{m["mac"]} in {wave["id"]}')
    return store.members(wave["id"])


def _rebase_future(conn: sqlite3.Connection, wave: sqlite3.Row,
                   future: list[sqlite3.Row], now: datetime) -> None:
    """‏#1947: חותמות הגל שבעתיד (קפיצה שלא נצפתה) נקבעות ל-`now` — מותנה
    בערך שנקרא, כך שדיווח שהגיע בינתיים מנצח; ושורת `clock_jump` ביומן."""
    stamp = now.isoformat(timespec="seconds")
    ahead = now + timedelta(seconds=clockstep.JUMP_SECONDS)
    started = _iso_or_none(wave["started_at"])
    with writing(conn):
        if started is not None and started > ahead:
            conn.execute("UPDATE sessions SET started_at = ? WHERE id = ? AND started_at = ?",
                         (stamp, wave["id"], wave["started_at"]))
        for m in future:
            updated = _iso_or_none(m["updated_at"])
            if updated is not None and updated > now:
                conn.execute(
                    "UPDATE session_members SET updated_at = ?"
                    " WHERE session_id = ? AND mac = ? AND updated_at = ? AND state = ?",
                    (stamp, wave["id"], m["mac"], m["updated_at"], m["state"]))
    journal(conn, "clock_jump", f'? {wave["id"]}')


def _utcnow() -> datetime:
    """השעון של הסבב: ה-WoL החוזר (#1721), ההפקעה וגלאי הקפיצות (#1947) —
    פונקציה, כדי שהבדיקה תזייף אותו."""
    return datetime.now(timezone.utc)


def _unseen(conn: sqlite3.Connection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row) -> list[str]:
    """‏#1721: מחשבי השיכפול שהגל הפתוח עוד ממתין להם ושלא נראו מאז שנפתח.

    רק מי ש**נבחר** לסבב (‏#1580; ‏None = סבב ישן בלי בחירה — לא נוגעים),
    שנשארה לו לפי הדיווח האחרון מגירה נבחרת שלא נכתבה (מי שסיים את כולן
    אינו נדרש לגל הזה), שלא הצטרף לגל, וש-hello שלו אינו מאז הפתיחה.
    משכפל בספירת הכיבוי אינו שולח hello, ולכן "נראה" = עלה מחדש."""
    roster = _roster(round_row)
    opened = _iso_or_none(wave["created_at"])
    if roster is None or opened is None:
        return []
    joined = {m["mac"] for m in store.members(wave["id"])}
    written = _closed(round_row)          # #1760: a red-skipped drawer does not need the cloner
    unseen = []
    for mac in roster:
        if mac in joined or not fresh_serials(conn, mac, written,
                                              _selected_ports(round_row, mac)):
            continue
        row = conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?",
                           (mac,)).fetchone()
        seen = _iso_or_none(row["last_seen"]) if row else None
        if seen is None or seen < opened:
            unseen.append(mac)
    return unseen


def _rewake(conn: sqlite3.Connection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row) -> None:
    """‏#1721: WoL חוזר — עם תקרה — למי שהגל הפתוח ממתין לו ולא חזר.

    ברזל rc5: גל 2 נפתח והעיר את המשכפל באותה שנייה שבה הוא נכנס לספירת
    הכיבוי; ה-WoL נבלע, המשכפל כבה, והסבב נשאר `active` בלי איש שיעיר אותו.
    הספירה היא שורות `room_rewake` של הגל ביומן — כך היא שורדת אתחול שרת
    ונראית למפעיל. אחרי `REWAKE_MAX` ועוד `REWAKE_BOOT_SECONDS` — הסבב נכשל
    בגלוי ובשם המכונה (עיקרון 5), והחריץ משתחרר."""
    unseen = _unseen(conn, store, round_row, wave)
    if not unseen:
        return
    age = (_utcnow() - _iso_or_none(wave["created_at"])).total_seconds()
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'room_rewake' AND detail LIKE ?",
        (wave["id"] + " %",),
    ).fetchone()["n"]
    # The lab incident left created_at three hours in the future.  Waiting
    # for that wall clock would suppress every re-wake; a negative age is a
    # clock discontinuity and is therefore due immediately.
    if age < 0:
        # #1771: a clock step left created_at in the future. Measure from the
        # last re-wake this wave sent (written after the step, so real time):
        # the spacing between WoL stays REWAKE_SECONDS and the cap still ends
        # the round -- a negative age must neither freeze it nor burn all six
        # re-wakes in one breath.
        last = conn.execute(
            "SELECT MAX(ts) AS ts FROM journal WHERE event = 'room_rewake' AND detail LIKE ?",
            (wave["id"] + " %",),
        ).fetchone()["ts"]
        last_at = _iso_or_none(last)
        since = (_utcnow() - last_at).total_seconds() if last_at else REWAKE_SECONDS
        age = sent * REWAKE_SECONDS + max(0.0, since)
    names = ", ".join(_machine_name(conn, mac) for mac in unseen)
    if sent < REWAKE_MAX:
        if age >= (sent + 1) * REWAKE_SECONDS:
            store.on_opened(CLONERS_GROUP, None, unseen)
            journal(conn, "room_rewake",
                    f'{wave["id"]} {round_row["id"]} n={sent + 1}/{REWAKE_MAX} {names}')
        return
    if age >= REWAKE_MAX * REWAKE_SECONDS + REWAKE_BOOT_SECONDS:
        reason = (f"מחשב השיכפול {names} לא חזר לגל — {REWAKE_MAX + 1} הערות WoL"
                  " בלי תשובה. לבדוק חשמל, כבל רשת ו-Wake-on-LAN ב-BIOS")
        # ‏#2031: דלוק ומחזיק תוצאות (#2024) — WoL לא יעיר אותו; הסיבה האמיתית, בשמו
        waiting = [mac for mac in unseen if held.is_held(conn, mac)]
        if waiting:
            reason = (f"מחשב השיכפול {', '.join(_machine_name(conn, mac) for mac in waiting)}"
                      " דלוק ומחכה ללחיצה על 'כבה' — יש בו דיסק עם בעיה מהסבב הקודם,"
                      " ולכן לא הצטרף לגל. לחצו 'כבה' (במחשב או בקונסולה) ופתחו סבב חדש")
        if _fail_round(conn, store, round_row, reason):
            journal(conn, "room_wake_failed",
                    f'{round_row["id"]} {wave["id"]} {names}'
                    f' written={round_row["written_drives"]}/{round_row["target_drives"]}')
            from . import direct         # noqa: PLC0415 — כמו ב-`_resume`
            direct.cancel_source_task(conn, round_row, "")


def _machine_name(conn: sqlite3.Connection, mac: str) -> str:
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    return row["suffix"] if row else mac


def tick(conn: sqlite3.Connection, store: SessionStore) -> None:
    """Advance on cloner hellos, never on screen reads.

    With no hello, an idle room waits. No timer or additional SQLite writer.

    #1947: every pulse first checks the server clock for a step and rebases
    the active rounds' stamps -- before any timer below reads them.
    """
    clockstep.guard(conn, store, _utcnow())
    round_row = active_round(conn)
    if round_row is None:
        return
    wave = None
    if round_row["wave_session_id"]:
        wave = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (round_row["wave_session_id"],)
        ).fetchone()

    if wave is None or wave["state"] == "closed":
        # הסבב פעיל והגל שלו כבר אינו. בלי הענף הזה זה מצב **יציב**:
        # כל דופק עתידי נוסג כאן, החדר נראה פתוח ואף גל אינו נפתח (#217).
        _resume(conn, store, round_row, wave)
    elif wave["state"] == "failed":
        # גיבוי למעבר האטומי של callback המשדר: גם אם ה-session כבר
        # סומן מכיוון אחר, כשל מפורש לעולם אינו נתיב לגל אוטומטי נוסף.
        fail_wave(conn, wave["id"],
                  wave["failed_reason"] or "גל השידור נכשל")
    elif wave["state"] == "open":
        # ‏#1760 (ברזל D6c): מגירה שה-hello כבר אומר שהיא אדומה, אצל מי שהצטרף,
        # אינה טרייה — ולכן גם אינה מגירה שהגל ממתין לה. בלי זה גל של "אחת אדומה
        # ואחת תקינה" חיכה ל-2 טריות ולא יצא לעולם (44 דקות, ‏room_3afb859d).
        remaining = _remaining(round_row) - red_drives(conn, store, round_row)
        ready = ready_drives(conn, store, round_row)
        # ‏#715: מדיסק חי הגל ממתין גם למניפסט — מקבל שיוצא לדרך לפניו אין
        # לו מול מה לאמת, ו-`GET …/manifest` שלו היה נענה 404.
        from . import direct             # noqa: PLC0415
        if 0 < ready and remaining <= ready and direct.source_ready(round_row):
            warned = precheck_now(conn, store, round_row)     # #432
            store.start_auto(wave["id"])
            journal_precheck(conn, round_row, warned, "")
        elif ready == 0 and remaining <= 0:
            # כל מה שהגל ממתין לו אדום: אין מה לכתוב — נסגר בגלוי, לא `open` לנצח.
            if _fail_round(conn, store, round_row, NOTHING_TO_WRITE_REASON):
                journal(conn, "room_failed",
                        f'{round_row["id"]} {wave["id"]} אין מגירות לכתוב — כל המגירות אדומות'
                        f' written={round_row["written_drives"]}/{round_row["target_drives"]}')
                direct.cancel_source_task(conn, round_row, "")
        else:
            _rewake(conn, store, round_row, wave)         # #1721
    elif wave["state"] == "running":
        members = store.members(wave["id"])
        # ‏#450: מכונה שנעלמה (כובתה, איבדה רשת, קרסה) לעולם אינה מדווחת
        # מצב סופי, ובלעדי הפקעה הגל ממתין לה לנצח וגל נוסף אינו נפתח.
        # מפקיעים אותה **לפני** בדיקת הסיום, ובודקים מול `reports.FINAL`
        # (הטרמינליים + `lost`).
        members = _mark_lost(conn, store, wave, members)
        # ‏`partial` הוא סיום לכל דבר (#67): מכונה שכתבה שתי מגירות מתוך
        # שלוש אמרה את דברה, והגל אינו ממתין לה עוד. הספירה שאחריו היא
        # ממילא לפי מגירה — המגירה שנכשלה פשוט לא נספרת ונשארת טרייה.
        if members and all(m["state"] in reports.FINAL for m in members):
            _finish_wave(conn, store, round_row, members)


#: תקרה לאורך פרט ביומן. הודעת חריגה יכולה לגרור traceback שלם, ושורת
#: יומן שאי אפשר לקרוא על המסך אינה מוסיפה למפעיל דבר.
_DETAIL_MAX = 200


def _one_line(value: object) -> str:
    """ערך כפי שמותר לכתוב אותו לשורת יומן או לוג: **שורה אחת**.

    **לא** מפני שהוא מזייף רשומה בטבלת היומן — הוא אינו יכול:
    ‏`journal()` מעביר את `detail` כפרמטר קשור לעמודת TEXT, ושורה חדשה
    בתוכו נשארת שדה אחד. בקרה שלילית שהייתה נשענת על "רשומה שנייה
    בטבלה" **הייתה נכשלת להיכשל**, וזה בדיוק סוג השומר שנראה עובד.

    שתי הסיבות האמיתיות:

    1. ‏`log.exception` הולך ליומן ה-systemd, שהוא **טקסטואלי**. שורה
       חדשה בהודעת חריגה מייצרת שם שורה שנראית עצמאית — כלומר רשומה
       מזויפת ביומן שהמפעיל קורא. זה אותו יומן שכבר נכווינו בו ב-#179.
    2. תקרת אורך: הודעת חריגה יכולה לגרור traceback שלם, ושדה שאי-אפשר
       לקרוא על המסך אינו מוסיף למפעיל דבר.

    ‏`normalize_mac` מחזיר hex קנוני או `None`, ולכן מסלול ה-MAC אינו
    ניתן לניצול ממילא. **הווקטור החי היחיד הוא טקסט החריגה** — והשמירה
    יושבת בנקודת הכתיבה, לא בהנחה על מי שקורא.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:_DETAIL_MAX]


def pulse(conn: sqlite3.Connection, store: SessionStore, mac: str) -> bool:
    """הדופק של הסבב מתוך מסלול ה-hello — לעולם אינו מפיל אותו.

    אותה מוסכמה כמו `agent_loops.note`: הפונקציה לעולם אינה זורקת
    ולעולם אינה נוגעת בתשובה שנשלחת למכונה, ועיקרון 1 נשאר בדיוק כפי
    שהוא. ‏hello שמחזיר 500 אומר למחשב השיכפול שהשרת מת, וזה שקר —
    השרת חי, רק קידום הסבב נכשל (#177).

    **אבל לא בשקט (עיקרון 5).** גל שלא נפתח הוא בדיוק מה שהמפעיל חייב
    לראות: הסבב נראה תקוע והחדר ממתין לאדם שיבחין. לכן הכישלון נרשם
    ביומן — שם הוא מגיע למסך — וגם ב-`log.exception` עם ה-traceback.
    ‏`except: pass` היה הופך את הבאג לשקוף, וזה גרוע מ-500.

    מוחזר האם הדופק עבר, למי שכן רוצה לדעת.
    """
    who = _one_line(mac)
    try:
        tick(conn, store)
        return True
    except Exception as exc:      # noqa: BLE001 — קידום הסבב לא מפיל hello
        log.exception("room tick from %s failed", who)
        try:
            journal(conn, "room_tick_failed", f"{who} — {_one_line(exc)}")
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל hello
            log.exception("room tick failure from %s was not journaled", who)
        return False


def sweep(conn: sqlite3.Connection, store: SessionStore) -> bool:
    """הדופק של הסבב מהרקע — **גל שאיש אינו צופה בו חייב להתקדם** (#456).

    ‏`tick` מקודם היום משני מקורות בלבד: ‏hello של משכפל (`pulse`) ו-GET
    ‏`/overview`. שניהם תלויים במשהו חיצוני — מכונה ששולחת hello, או אדם
    שמסתכל על מבט-העל. כשכל המכונות נעלמו (כובו, נותקו, קרסו) **אין יותר
    hello**, ומסך החדר מושך `/api/console/room` שהוא read-only (#446,
    ‏#453) — ולכן `_mark_lost` לא נקרא, אף חבר לא מוכרז אבוד, והגל תקוע
    לנצח (#456, וזה גם המקרה שנשאר פתוח ב-#659 כשהמכונה **האחרונה**
    נעלמת). לולאת הרקע (`app._room_clock`) קוראת לכאן בקצב קבוע, ולכן
    ההפקעה אינה תלויה עוד בצופה או ב-hello של שורד.

    אותה מוסכמה כמו `pulse`: לעולם אינה זורקת. הרקע אינו יכול להחזיר 500
    לאיש, אבל כישלון בקידום הסבב עדיין חייב להיראות — לכן `log.exception`
    עם ה-traceback ושורת יומן, ולא `except: pass` שמעלים את הבאג (עיקרון
    5). מוחזר האם הדופק עבר, למי שכן רוצה לדעת (הבדיקה).
    """
    try:
        tick(conn, store)
        return True
    except Exception as exc:      # noqa: BLE001 — קידום הסבב לא מפיל את לולאת הרקע
        log.exception("room background tick failed")
        try:
            journal(conn, "room_tick_failed", f"background — {_one_line(exc)}")
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל את הלולאה
            log.exception("room background tick failure was not journaled")
        return False


def rejoined_mid_stream(conn: sqlite3.Connection, session: sqlite3.Row, mac: str) -> bool:
    """‏#1728: hello **מצטרף** של חבר בגל `running` שכבר נכנס לזרם — אתחול.

    ברזל rc5 (18/10): ‏`reboot -f` במשכפל באמצע מחיצה 3; הוא חזר תוך 27
    שניות, קיבל את אותו גל רץ, ניסה לכתוב את מחיצה 1 מזרם שכבר במחיצה 3
    (`fanout: empty stream`), ושני דיסקים תקינים נרשמו `disk_failure`.

    הראיה היא מצב החבר: הסוכן מתחיל לשחזר רק כשה-hello אמר `running`, ומאז
    הוא מדווח progress ואינו שולח hello מצטרף עד שהעבודה נגמרה (הדופק של
    הגואי הוא `joining: false`). חבר שעוד `waiting` הוא ה-hello הראשון אחרי
    שהגל יצא — כך הסוכן לומד זאת — והוא מקבל את הגל כמו קודם.

    חבר שכבר אמר את דברו (`failed`/`partial`) פשוט אינו מקבל את הגל שוב.
    חבר שהיה באמצע מסומן `lost` (‏UPDATE מותנה, כמו `_mark_lost`), והיומן
    אומר `room_rejoin` פעם אחת — זו הראיה ש-`_revived` קורא בסוף הגל.
    מוחזר האם ה-hello הזה **אינו** מקבל את הגל."""
    row = conn.execute(
        "SELECT state FROM session_members WHERE session_id = ? AND mac = ?",
        (session["id"], mac),
    ).fetchone()
    if row is None or row["state"] == "waiting":
        return False
    if row["state"] in reports.TERMINAL:
        return True
    if row["state"] != reports.LOST:
        update_one(
            conn,
            "UPDATE session_members SET state = ?, updated_at = ?"
            " WHERE session_id = ? AND mac = ? AND state = ?",
            (reports.LOST, now_iso(), session["id"], mac, row["state"]),
        )
    detail = f'{mac} in {session["id"]}'
    if conn.execute(
        "SELECT 1 FROM journal WHERE event = 'room_rejoin' AND detail = ?", (detail,),
    ).fetchone() is None:
        journal(conn, "room_rejoin", detail)
    return True


def _revived(conn: sqlite3.Connection, wave_id: str, members: list[sqlite3.Row]) -> bool:
    """‏#1728: האם חבר `lost` בגל הזה חזר וחי — אתחול באמצע הזרם (`room_rejoin`)
    ו-hello בחלון `AWAKE_SECONDS`. גל כזה שלא כתב דבר אינו "גל מת" (#1672):
    המשכפל מוכן לגל הבא. חבר שנעלם ולא חזר — אין ראיה, והסבב נכשל כמו קודם."""
    for m in members:
        if m["state"] != reports.LOST or conn.execute(
            "SELECT 1 FROM journal WHERE event = 'room_rejoin' AND detail = ?",
            (f'{m["mac"]} in {wave_id}',),
        ).fetchone() is None:
            continue
        seen = conn.execute(
            "SELECT last_seen FROM net_devices WHERE mac = ?", (m["mac"],)
        ).fetchone()
        if seen is not None and _is_awake(seen["last_seen"]):
            return True
    return False


def _journal_drawer_changed(conn: sqlite3.Connection, round_id: str,
                            member: sqlite3.Row, dev: str | None,
                            recorded: str | None, current: str | None,
                            event: str = "room_drawer_changed") -> None:
    row = conn.execute(
        "SELECT suffix FROM machines WHERE mac = ?", (member["mac"],)
    ).fetchone()
    name = row["suffix"] if row else member["mac"]
    detail = (
        f"{round_id} {name} {dev or '?'} serial="
        f"{recorded or 'missing'} current={current or 'missing'}"
    )
    if conn.execute(
        "SELECT 1 FROM journal WHERE event = ? AND detail = ?",
        (event, detail),
    ).fetchone() is None:
        journal(conn, event, detail)


def _tally(conn: sqlite3.Connection, round_row: sqlite3.Row,
           members: list[sqlite3.Row]) -> tuple[int, set[str], set[str], set[str]]:
    """כמה מגירות נכתבו בסבב עד כה, ואילו — לפי serial; ואילו נסגרו בלי שנכתבו:
    דולגו כאדומות (#1760) או בוטלו בידי מפעיל (#1936).

    **הספירה חוזרת על עצמה בבטחה:** מגירה שכבר בקבוצה אינה נספרת שוב,
    ולכן ספירה חוזרת של אותו גל (במסלול השחזור של `_resume`) מחזירה
    בדיוק את אותו מספר. זה מה שמאפשר לשחזר בלי לדעת אם הספירה הקודמת
    הספיקה להיכתב.
    """
    written = _written(round_row)
    skipped = _skipped(round_row)
    cancelled = _cancelled(round_row)
    new_drives = 0
    for member in members:
        disks = [d for d in _disks(conn, member["mac"]) if isinstance(d, dict)]
        serial_of = {d.get("dev"): d.get("serial") for d in disks}
        for target in json.loads(member["targets_json"] or "[]"):
            if not isinstance(target, dict):
                continue
            if target.get("state") == "skipped":
                # ‏#1760: דולגה כאדומה — סגורה בסבב, לא נכתבה. דילוג כתום (המפעיל
                # בחר "דלג") אינו נסגר כאן: הוא נשאר טרי לגל הבא, כמו קודם.
                serial = target.get("serial") or serial_of.get(target.get("dev"))
                if serial and serial not in written and _red_skip(
                        conn, member, target.get("dev"), serial, disks):
                    skipped.add(serial)
                continue
            if target.get("state") == "cancelled":
                # ‏#1936: הסוכן דיווח שהמגירה בוטלה (‏#1386 — הראיה, `drawer_cancel`).
                # ביטול של מפעיל סופי לסבב: סגורה, לא נכתבה, ואינה פותחת גל נוסף.
                serial = target.get("serial") or serial_of.get(target.get("dev"))
                if serial and serial not in written:
                    cancelled.add(serial)
                continue
            if target.get("state") != "done":
                continue
            dev = target.get("dev")
            recorded = target.get("serial")
            current = serial_of.get(dev)
            # #1126 item 4 binds a completed target to the serial observed at
            # completion when that binding is available.  Rows written by an
            # older server have no such field; absence is not evidence that a
            # drawer was swapped, so retain their historical behaviour and
            # count the serial currently observed in that device.  A present
            # binding remains authoritative and must match exactly.
            if recorded and recorded != current:
                _journal_drawer_changed(
                    conn, round_row["id"], member, dev, recorded, current,
                )
                continue
            serial = recorded or current
            if not serial:
                # ‏#105: לא הוחלפה — מעולם לא הייתה לה זהות לספור. לא נספרת
                # (ערך מומצא ב-`written_serials` היה מדלג על כונן ריק).
                _journal_drawer_changed(
                    conn, round_row["id"], member, dev, recorded, current,
                    event="room_drawer_unidentified",
                )
                continue
            if serial not in written:
                written.add(serial)
                new_drives += 1
    return (round_row["written_drives"] + new_drives, written, skipped - written,
            cancelled - written)


def _clear_written_failures(conn: sqlite3.Connection, round_row: sqlite3.Row,
                            written: set[str]) -> None:
    """‏#1792: מה שהגל הזה הוסיף ל-`written_serials` סוגר את רשומת הכשל הכתומה שלו.
    לפני התביעה (סגירת הגל / עדכון הסבב) ולא אחריה: הניקוי חוזר על עצמו בבטחה,
    ואם הוא נכשל — הדופק נרשם `room_tick_failed` והגל לא נסגר, והדופק הבא מנסה
    שוב עם אותה ספירה. אחרי התביעה כשל היה נבלע: הסבב כבר סופר את הדיסק."""
    disk_failures.clear_written(conn, written - _written(round_row), round_row["id"])


def _skipped_note(skipped: set[str], cancelled: set[str]) -> str:
    """‏#1760: שורת היומן אומרת כמה דולגו כאדומות — "1/2" לבדו נקרא כמו כשל.
    ‏#1936: וכמה בוטלו בידי מפעיל — `written=1/2 cancelled=1`."""
    return ((f" skipped_red={len(skipped)}" if skipped else "")
            + (f" cancelled={len(cancelled)}" if cancelled else ""))


def _red_skip(conn: sqlite3.Connection, member: sqlite3.Row, dev: str | None,
              serial: str, disks: list[dict]) -> bool:
    """‏#1760: היעד דולג **כאדום** — ראיה חיובית אחת משתיים: ה-disk_event של הגל
    על הדיסק הזה אומר `verdict: fail` עם `decision: skip` (‏smart_preflight), או
    שה-hello האחרון אומר שהדיסק בעל הסידורי הזה אדום. דילוג בלי אחת מהן (המפעיל
    דילג על כתום) אינו אדום."""
    row = conn.execute(
        "SELECT verdict, decision FROM disk_events WHERE session_id = ? AND mac = ?"
        " AND disk = ?", (member["session_id"], member["mac"], dev),
    ).fetchone() if dev else None
    if row is not None and row["verdict"] == "fail" and row["decision"] == "skip":
        return True
    return any(d.get("serial") == serial and _red(d) for d in disks)


def _resume(conn: sqlite3.Connection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row | None) -> None:
    """הסבב פעיל, והגל שהוא מצביע עליו סגור (או שאין כזה) — ‏#217.

    שלוש דרכים נמדדות להגיע לכאן: סגירת הגל מ-endpoint הסבבים הכללי של
    הקונסולה (בלי שום תקלה — ‏`close_session` אינו יודע שזה גל של חדר),
    וכשל בכתיבת `room_rounds` אחרי ש-`store.close` או `store.open` כבר
    בוצע להם commit — שני מסלולי הנסיגה של `_finish_wave`.

    **התביעה כאן היא איפוס המצביע**, בדיוק כמו שהסגירה המותנית היא
    התביעה ב-#177: ‏`UPDATE` מותנה שרק תהליכון אחד מנצח בו, ומי שהפסיד
    רואה `NULL` וכבר אינו במצב הזה. איפוס לפני הפתיחה ולא אחריה, כדי
    שכשל **בפתיחה** ישאיר מצב שהדופק הבא יודע לתקן — ולא סבב שמצביע על
    גל שאינו קיים.
    """
    total, written = round_row["written_drives"], _written(round_row)
    skipped, cancelled = _skipped(round_row), _cancelled(round_row)
    if wave is not None:
        members = store.members(wave["id"])
        drive_rates.record_wave(conn, wave["id"], members)      # ‏#447
        total, written, skipped, cancelled = _tally(conn, round_row, members)
        _clear_written_failures(conn, round_row, written)          # #1792
        if not update_one(
            conn,
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " skipped_serials = ?, cancelled_serials = ?, wave_session_id = NULL"
            " WHERE id = ? AND state = 'active' AND wave_session_id = ?",
            (total, json.dumps(sorted(written)), json.dumps(sorted(skipped)),
             json.dumps(sorted(cancelled)), round_row["id"], wave["id"]),
        ):
            return
        conn.commit()

    target = round_row["target_drives"]
    # ‏#715: סבב מדיסק חי הוא סבב **יחיד** — הגל שנגמר הוא הסבב, גם כשמגירה
    # נכשלה; אין גל נוסף לפתוח בלי שמחשב הבנייה ישדר שוב, וזו החלטה של אדם.
    from . import direct                 # noqa: PLC0415
    if total + len(skipped) + len(cancelled) >= target or direct.is_build_disk(round_row):
        conn.execute(
            "UPDATE room_rounds SET state = 'closed', closed_at = ? WHERE id = ?",
            (now_iso(), round_row["id"]),
        )
        conn.commit()
        journal(conn, "room_done", f'{round_row["id"]} written={total}/{target}'
                + _skipped_note(skipped, cancelled))
        direct.cancel_source_task(conn, round_row, "")
        return
    if wave is not None:
        # לא בשקט (עיקרון 5): גל שנעלם מתחת לסבב הוא בדיוק מה שהמפעיל
        # צריך לראות, גם כשהחדר מתאושש לבדו. שורה אחת, כי התביעה למעלה
        # מצליחה פעם אחת.
        journal(conn, "room_wave_lost",
                f'{round_row["id"]} {wave["id"]} written={total}/{target}')
    _attach_wave(conn, store, round_row, total)


def _attach_wave(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row, total: int) -> None:
    """מצמיד לסבב גל — קיים או חדש.

    **קודם מאמצים.** גל שנפתח ואיש לא הספיק להצביע עליו כבר מחזיק את
    חריץ המולטיקאסט היחיד, ופתיחת גל נוסף הייתה נכשלת ב-`TAKEN` בכל
    דופק מכאן והלאה. הזיהוי הוא קבוצה + קידומת + אימג' — חתימה שסבב
    כיתה אינו נושא.
    """
    existing = store.active_for_group(CLONERS_GROUP)
    opened_here = False
    if (existing is not None and existing["prefix"] == "ROOM"
            and existing["image_id"] == round_row["image_id"]):
        wave_id = existing["id"]
    else:
        machines = conn.execute(
            f"SELECT COUNT(*) AS n FROM machines WHERE {_IN_ROOM}"
        ).fetchone()["n"]
        try:
            wave_id = store.open(
                CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
                expected_clients=_expected(round_row, machines), opened_by="",
                expand_partition=round_row["expand_partition"],
                roster=_roster(round_row),
            )
        except SessionError:
            # החריץ תפוס בידי סבב אחר. **לא נבלע**: המצב כבר ביומן
            # (`room_wave_lost`) וגם על המסך (`wave_state: closed`),
            # והדופק הבא ינסה שוב. חריגה כאן הייתה מייצרת
            # `room_tick_failed` בכל hello — רעש שמכסה על השורה שכן
            # אומרת משהו.
            return
        opened_here = True
    if not update_one(
        conn,
        "UPDATE room_rounds SET wave_session_id = ?, wave_number = wave_number + 1"
        " WHERE id = ? AND state = 'active' AND wave_session_id IS NULL",
        (wave_id, round_row["id"]),
    ):
        # תהליכון אחר הקדים אותנו. גל שאיש אינו מצביע עליו יחזיק את החריץ
        # לנצח, ולכן סוגרים את מה ש**אנחנו** פתחנו — אבל רק אחרי שנבדק
        # שהסבב אינו מצביע עליו: המנצח יכול היה לאמץ בדיוק את הגל הזה
        # (זה הענף הראשון כאן), וסגירתו הייתה מחזירה את החדר למצב שממנו
        # באנו. שאילתה, ולא הנחה — הראיה החיובית היא שהמצביע אינו שלנו.
        if opened_here and conn.execute(
            "SELECT 1 FROM room_rounds WHERE id = ? AND wave_session_id = ?",
            (round_row["id"], wave_id),
        ).fetchone() is None:
            store.close(wave_id, "")
        return
    conn.commit()
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}')


#: ‏#1760 — הסיבה כשכל המגירות שהגל ממתין להן אדומות (‏SMART נכשל / קריאה בלבד).
NOTHING_TO_WRITE_REASON = ("אין מגירות לכתוב — כל המגירות שנבחרו אדומות (SMART נכשל"
                           " או קריאה בלבד), ועל אדום לא כותבים")

#: ‏נדב 17/10 — הסיבה על שורת הסבב כשגל הסתיים בלי שאף מגירה נכתבה בו.
DEAD_WAVE_REASON = "אף מגירה לא נכתבה בגל — הזרם נעצר, או שכל המחשבים נכשלו או נותקו"


def _fail_dead_wave(conn: sqlite3.Connection, store: SessionStore,
                    round_row: sqlite3.Row, total: int) -> None:
    """גל שכל יעדיו נכשלו או נותקו מסיים את הסבב כ-`failed` (נדב, ברזל 17/10).

    עד כאן נפתח גל נוסף "שממתין למגירות מוחלפות" — אבל מגירה שנכשלה נשארת
    טרייה, ומשכפל שנותק (או נכבה אחרי הכשל, #1560) לא יחזור לבדו: הסבב
    נשאר `active` וחריץ המולטיקאסט תפוס, בלי שאיש יבחין (`room_72308383`).
    גל שכתב **משהו** ממשיך כמו קודם. הסגירה היא התביעה, כמו ב-`room_done`."""
    if not _fail_round(conn, store, round_row, DEAD_WAVE_REASON):
        return
    journal(conn, "room_failed",
            f'{round_row["id"]} {round_row["wave_session_id"]}'
            f' written={total}/{round_row["target_drives"]}')


def _fail_round(conn: sqlite3.Connection, store: SessionStore,
                round_row: sqlite3.Row, reason: str) -> bool:
    """סוגר את הגל ומסמן את הסבב `failed` עם `reason`; מחזיר האם הקריאה הזו
    היא שהפילה אותו (הסגירה היא התביעה, #177) — ורק היא כותבת ליומן."""
    if not store.close(round_row["wave_session_id"], ""):
        return False
    with writing(conn):                  # #54: a failed commit leaves no orphan lock
        return update_one(
            conn,
            "UPDATE room_rounds SET state = 'failed', failed_reason = ?, closed_at = ?"
            " WHERE id = ? AND state = 'active' AND wave_session_id = ?",
            (reason, now_iso(), round_row["id"], round_row["wave_session_id"]),
        )


def _finish_wave(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row, members: list[sqlite3.Row]) -> None:
    """הגל הסתיים: סופרים לפי serial אילו מגירות נכתבו, וממשיכים."""
    current = conn.execute(
        "SELECT state, wave_session_id FROM room_rounds WHERE id = ?",
        (round_row["id"],),
    ).fetchone()
    if (current is None or current["state"] != "active"
            or current["wave_session_id"] != round_row["wave_session_id"]):
        return
    # ‏#447: מה שכל מגירה כתבה, לפני הספירה. חוזר על עצמו בבטחה ולעולם
    # אינו זורק — זיכרון שנכשל אינו עוצר את סיום הגל.
    drive_rates.record_wave(conn, round_row["wave_session_id"], members)
    # ‏#1844: מה שנמדד בכל חיבור SATA — "כנראה כבל" או "הדיסק". לעולם אינו זורק.
    learn_ports.record_wave(conn, round_row["wave_session_id"], members)
    # ‏#1845: ה-MB/s של בדיקת הכתיבה לכל דיסק — הבסיס של "איטי מהרגיל לדגם".
    learn_models.record_wave(conn, round_row["wave_session_id"], members)
    total, written, skipped, cancelled = _tally(conn, round_row, members)
    _clear_written_failures(conn, round_row, written)              # #1792

    # ‏#715: מדיסק חי הגל היחיד הוא הסבב כולו (ראו `_resume`).
    # ‏#1760: מגירה שדולגה כאדומה סוגרת את המקום שלה — 1/2 עם אחת אדומה הוא הסוף.
    # ‏#1936 (FI O2): וכך גם מגירה שמפעיל ביטל — 1/2 עם אחת שבוטלה הוא `room_done`,
    # לא גל 2 שמעיר את המשכפל ולא `room_failed`.
    from . import direct                 # noqa: PLC0415
    if (total + len(skipped) + len(cancelled) >= round_row["target_drives"]
            or direct.is_build_disk(round_row)):
        # הסגירה היא התביעה: שני תהליכונים שהגיעו לכאן עם אותו גל —
        # רק זה שסגר אותו בפועל כותב את השורה התחתונה של הסבב (#177).
        if not store.close(round_row["wave_session_id"], ""):
            return
        if not update_one(
            conn,
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " skipped_serials = ?, cancelled_serials = ?, state = 'closed', closed_at = ?"
            " WHERE id = ? AND state = 'active' AND wave_session_id = ?",
            (total, json.dumps(sorted(written)), json.dumps(sorted(skipped)),
             json.dumps(sorted(cancelled)), now_iso(),
             round_row["id"], round_row["wave_session_id"]),
        ):
            return
        conn.commit()
        journal(conn, "room_done",
                f'{round_row["id"]} written={total}/{round_row["target_drives"]}'
                + _skipped_note(skipped, cancelled))
        return

    # ‏#1728: משכפל שאותחל באמצע הזרם וחזר — הגל הבא, לא סבב שנכשל.
    # ‏#1936: ביטול של מפעיל אינו כשל — גל שבו מגירה בוטלה אינו "גל מת".
    if (total == round_row["written_drives"] and skipped == _skipped(round_row)
            and cancelled == _cancelled(round_row)
            and not _revived(conn, round_row["wave_session_id"], members)):
        _fail_dead_wave(conn, store, round_row, total)
        return

    # היעד לא הושג — הגל הבא נפתח מעצמו וממתין למגירות מוחלפות.
    machines = conn.execute(
        f"SELECT COUNT(*) AS n FROM machines WHERE {_IN_ROOM}"
    ).fetchone()["n"]
    current = conn.execute(
        "SELECT state, wave_session_id FROM room_rounds WHERE id = ?",
        (round_row["id"],),
    ).fetchone()
    if (current is None or current["state"] != "active"
            or current["wave_session_id"] != round_row["wave_session_id"]):
        return
    # סגירת הגל הגמור ופתיחת הבא הן טרנזאקציה אחת: בין השתיים החריץ
    # היה פנוי, ושני דופקים בו-זמנית הפכו את השני ל-`TAKEN` — ה-hello
    # שהריץ אותו החזיר 500 (#177), ופותח שלישי (סבב כיתה מהקונסולה)
    # שהיה נכנס לאותו חלון היה משאיר את החדר בלי גל בכלל.
    try:
        wave_id = store.open(
            CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
            expected_clients=_expected(round_row, machines), opened_by="",
            replaces=round_row["wave_session_id"],
            expand_partition=round_row["expand_partition"],
            roster=_roster(round_row),
        )
    except SessionSuperseded:
        # תהליכון אחר סגר את הגל הזה ופתח את הבא. זה אינו כישלון אלא
        # בדיוק מה שהאטומיות נועדה לייצר: פותח אחד, לא שניים.
        return
    if not update_one(
        conn,
        "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
        " skipped_serials = ?, cancelled_serials = ?, wave_session_id = ?,"
        " wave_number = wave_number + 1"
        " WHERE id = ? AND state = 'active' AND wave_session_id = ?",
        (total, json.dumps(sorted(written)), json.dumps(sorted(skipped)),
         json.dumps(sorted(cancelled)), wave_id,
         round_row["id"], round_row["wave_session_id"]),
    ):
        if conn.execute(
            "SELECT 1 FROM room_rounds WHERE id = ? AND wave_session_id = ?",
            (round_row["id"], wave_id),
        ).fetchone() is None:
            store.close(wave_id, "")
        return
    conn.commit()
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}')


# --- התצוגה ------------------------------------------------------------------


def _fresh(stamp: str | None) -> bool:
    if not stamp:
        return False
    try:
        seen = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    # ‏#774: חסם תחתון. חותמת בעתיד (סטיית שעון, DB מיובא) נותנת גיל
    # שלילי שמקיים `<= AWAKE_SECONDS` — והמכונה נראתה "ערה" לכל משך
    # הסטייה. חותמת עתידית אינה ראיה שהמכונה חיה עכשיו.
    age = datetime.now(timezone.utc) - seen
    return timedelta(0) <= age <= timedelta(seconds=AWAKE_SECONDS)


def local_copy_now(local_clone: str | None, last_seen: str | None) -> bool:
    """‏#1367 (נדב 14/10): המכונה באמצע שיכפול מקומי **עכשיו** — ה-hello האחרון
    אמר `local_clone: copying`, והוא טרי (`AWAKE_SECONDS`; ה-beat של הסוכן כל
    10 ש'). מכונה ששתקה אינה "כותבת": אין ראיה לכך — ואין למי לשלוח אזהרה."""
    return local_clone == "copying" and _fresh(last_seen)


def _is_awake(*stamps: str | None) -> bool:
    """ערה אם דיברה איתנו בחלון האחרון — ב-hello **או** בדיווח progress.

    ‏#449: ``last_seen`` מתעדכן מ-hello בלבד, אבל בזמן שחזור הכתיבה
    סינכרונית והסוכן חדל לפעום — לולאת הרקע מדווחת **progress** ולא
    hello. מכונה שכותבת בפועל דיווחה התקדמות לפני שניות, וזו ראיה
    חיובית שהיא חיה; סימונה כ"ישנה" קיפל "עסוקה בכתיבה" ו"נפלה/איבדה
    רשת" לאותו סימן (עיקרון 5). ``updated_at`` של חבר הסבב הוא הראיה
    השנייה — מספיקה אחת מהשתיים בחלון."""
    return any(_fresh(stamp) for stamp in stamps)


#: מעל כמה שניות בלי תנועה מגירה נחשבת עצורה. נגזר מ-1,896 דגימות
#: של סבב 08/09: מגירה בריאה עצרה עד 120 שניות. היה זהה ל-`ROOM_STALL_S`
#: של מסך הטקסט בסוכן, שהוסר ב-11/10 (GUI בלבד); כאן הוא משמש להכרעה שלמטה.
STALL_SECONDS = 180


def _stream_stalled(machines: list[dict]) -> bool:
    """האם **כל** המגירות הכותבות עצרו יחד — כלומר הזרם, לא הכונן.

    ⚠️ **זו ההבחנה ש-`fanout` אינו יכול לעשות, והשרת כן.** ‏`fanout`
    רואה מגירה אחת, ולכן כשהיא נעצרת הוא מסיק `drive too slow`
    ומאשים אותה. בסבב 08/09 זה היה שגוי: נמדד ש**ארבע המגירות
    הבריאות קפאו באותן דגימות בדיוק** — ‏81%–100% חפיפה — ‏**כולל
    שתי מגירות במכונה פיזית אחרת.** ‏26 דגימות שבהן כל החמש עצרו.

    ‏Garbage collection של כונן אינו מסתנכרן עם כונן במארז אחר.
    מקור משותף פירושו סיבה משותפת, ותווית "כונן תקוע" על עצירת זרם
    שולחת אדם להחליף חומרה תקינה — בדיוק הבאג שהמסך הזה בא לתקן.

    פחות משתי מגירות כותבות = אין רוב, ואין מה להסיק (עיקרון 5):
    מגירה בודדת שעצרה היא **המגירה**, ואין ראיה לזרם.
    """
    writing = [d for m in machines for d in m.get("drawer_list", [])
               if d.get("state") == "writing"]
    if len(writing) < 2:
        return False
    # ‏`None` אינו "לא עצורה" — הוא "לא נמדד". מגירה שלא נמדדה אינה
    # יכולה להשתתף בהכרעה שכולן עצרו.
    if any(d.get("stalled_s") is None for d in writing):
        return False
    return all(d["stalled_s"] >= STALL_SECONDS for d in writing)


def status_view(ctx) -> dict:
    """מה שמסך החדר מציג: המכונות, המגירות, והסבב אם יש."""
    round_row = visible_round(ctx.conn)
    written = _written(round_row) if round_row else set()
    member_of = {}
    wave = None
    if round_row is not None:
        member_of = {
            m["mac"]: m for m in ctx.store.members(round_row["wave_session_id"])
        }
        wave = ctx.conn.execute(
            "SELECT state, started_at FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
    started_at = wave["started_at"] if wave and wave["state"] == "running" else None

    machines = []
    all_names = slot_names.all_by_mac(ctx.conn)   # #704
    model_maps = model_slots.by_mac(ctx.conn)      # #2001
    for row in ctx.conn.execute(
        "SELECT m.mac, m.suffix, m.drawer_count, m.drawer_slots,"
        " d.last_seen, d.disk_probe, d.local_clone, d.held_at, d.held_json"
        " FROM machines m"
        " LEFT JOIN net_devices d ON d.mac = m.mac"
        f" WHERE m.{_IN_ROOM} ORDER BY m.suffix"
    ):
        member = member_of.get(row["mac"])
        drawers = drawer_list(ctx.conn, row["mac"], written, member,
                              _selected_ports(round_row, row["mac"]), started_at,
                              _skipped(round_row) if round_row else None,   # #1760
                              _cancelled(round_row) if round_row else None)  # #1936
        # ‏#704: השם של כל מגירה (לפי החריץ; NVMe לפי סידורי) — תווית בלבד,
        # ‏`selected`/`port` לא זזים. ‏None = אין שם, המסך מציג "דיסק N".
        names = all_names.get(row["mac"], {})
        model_map = model_maps.get(row["mac"])
        slots = drawer_slots.effective(row["drawer_count"], row["drawer_slots"],
                                       model_map["slots"] if model_map else None)
        for d in drawers:
            d["slot_name"] = slot_names.resolve(names, d["port"], d["serial"], d.get("pci"))
            d["drawer"] = drawer_slots.drawer_of(slots, d)
        # ‏#710: כל עוד #704 (הגדרת מגירות במסוף) לא נבנה, העמודה NULL —
        # ואז ברירת המחדל היא מספר הדיסקים שהמכונה מדווחת בפועל (הפורט
        # הגבוה ביותר, או הספירה כשאין פורטים), כדי שהגריד לא יסתיר דיסק.
        _ports = [d["port"] for d in drawers if isinstance(d["port"], int)]
        _count = row["drawer_count"] or (max(_ports + [len(drawers)]) if drawers else 1)
        machines.append({
            "mac": row["mac"],
            "name": row["suffix"],
            "drawer_count": _count,   # #695 הוגדר במסוף / #710 ברירת מחדל לפי הדיסקים
            "drawer_slots": slots,
            "awake": _is_awake(row["last_seen"],
                               member["updated_at"] if member else None),
            "drawers": len(drawers),
            # ‏#402: כש-`drawers` הוא 0 — למה: `no_disks` (חברו כונן) /
            # `no_ports` (הבקר מנוטרל בקושחה) / `unchecked` (לא נספר) /
            # ‏null (סוכן ישן). המספר לבדו קיפל את שלושתם.
            "disk_probe": row["disk_probe"],
            "fresh_drawers": sum(1 for d in drawers if d["fresh"]),
            "drawer_list": drawers,
            "ask": smart_ask.view(ctx.conn, row["mac"]),   # ‏#989 ס׳ 2: שאלת SMART פתוחה/שנענתה
            # ‏#704: כל השמות של המכונה, גם של חריץ ריק (‏sata:3 בלי דיסק).
            "slot_names": names,
            "joined": member is not None,
            # ‏#1367: שיכפול מקומי כותב עכשיו — "כבה אותו" שואל לפני
            "local_copy": local_copy_now(row["local_clone"], row["last_seen"]),
            # ‏#2031: דלוק ומחכה ל"כבה" (#2024) — ‏{at, problems} או None
            "held": held.view(row["held_at"], row["held_json"]),
            "state": member["state"] if member else None,
            "bytes_written": member["bytes_written"] if member else 0,
            "bytes_total": member["bytes_total"] if member else 0,
            "error": member["error"] if member else None,
        })

    label = ctx.conn.execute("SELECT label FROM groups WHERE id = ?", (CLONERS_GROUP,)).fetchone()
    view = {"round": None, "machines": machines, "group_label": label["label"] if label else "",
            "stream_stalled": _stream_stalled(machines),
            # ‏#953: הרצפה שהמסך משווה אליה כל אימג' לפני פתיחת סבב — אותה
            # רצפה בדיוק שהשרת מסרב לפיה, ממקום אחד. ‏null = אין דיווח.
            "disk_floor": disk_floor(ctx.conn)}
    if round_row is not None:
        slots = (json.loads(round_row["target_slots_json"])
                 if round_row["target_slots_json"] else None)
        ready = (ready_drives(ctx.conn, ctx.store, round_row)
                 if round_row["state"] == "active" else 0)
        open_wave = (round_row["state"] == "active" and wave is not None
                     and wave["state"] == "open")
        view["round"] = {
            "id": round_row["id"],
            # ‏17/10: הגל הנוכחי — הקונסולה מזהה בו את `/overview.session` של אותו סבב
            # ומציגה שורה אחת, לא שתיים.
            "wave_session_id": round_row["wave_session_id"],
            "state": round_row["state"],
            "failed_reason": round_row["failed_reason"],
            "image_id": round_row["image_id"],
            "image_name": round_label(ctx, round_row),
            "target_drives": round_row["target_drives"],
            "target_slots": slots,
            "expand_partition": round_row["expand_partition"] or "auto",
            # ‏#1269: דגם הדרייברים לכל דיסק יעד; ‏null = הסבב בלי דרייברים.
            "driver_models": room_drivers.view(round_row),
            "written_drives": round_row["written_drives"],
            # ‏#1760: מגירות שדולגו כאדומות — סגורות, לא נכתבו, ואינן "נותרו".
            "skipped_drives": len(_skipped(round_row)),
            "remaining_drives": _remaining(round_row),
            "wave_number": round_row["wave_number"],
            "wave_state": wave["state"] if wave else "closed",
            "ready_drives": ready,
            # ‏#432: האזהרות הכתומות לפני "התחל" — רק לגל פתוח; אזהרה, לא חסימה.
            "precheck": (room_precheck.warnings(machines, slots is not None)
                         if open_wave else []),
            # ‏#65: מי חסר בגל הפתוח ולמה — כבה אותו / התחל בכל זאת / בטל.
            "missing": (_with_pxe(ctx.conn, room_precheck.missing(
                machines, _remaining(round_row),
                slots, ready)) if open_wave else []),
            "opened_by": round_row["opened_by"],
            # ‏#715: מאיפה הבייטים — ספרייה (השרת משדר) או מחשב בנייה.
            "source": _direct().source_view(ctx.conn, round_row),
            # ‏#410: זמן, קצב והערכת סיום של הגל — null כשאין מה למדוד.
            **_wave_pace(started_at, machines),
            # ‏#989: מה ש-udp-sender של השרת מדפיס — קצב הזרם ובלוקים ששודרו
            # שוב. ‏null כשהשרת אינו המשדר (מחשב בנייה, #715) או לא נמדד.
            **(ctx.sender.stream_stats(round_row["wave_session_id"])
               if ctx.sender is not None and round_row["wave_session_id"]
               else {"throughput_bps": None, "loss_blocks": None}),
            # ‏#447: באיזו תקרה הגל יוצא (או יצא), ולפי איזה כונן.
            "wave_rate": _wave_rate(ctx, round_row, wave),
            # ‏#1843: כמה זמן הגל צפוי לקחת לפי הגלים הקודמים של אותם מחשבי שיכפול
            # עם אותו מספר מגירות. ‏`seconds: null` + ‏`why` = "אין עדיין הערכה".
            "forecast": _wave_forecast(ctx, round_row, wave, started_at),
        }
    return view


def _with_pxe(conn: sqlite3.Connection, missing: list[dict]) -> list[dict]:
    """‏#1887: למכונה שחסרה כי היא כבויה/לא עונה — מה ש-dnsmasq ראה מאז ההערה האחרונה
    (‏`{state, he}`). מידע בלבד; ‏`unchecked` = הלוג לא נקרא."""
    for item in missing:
        if item.get("reason") == "off":
            got = pxe_log.verdict(conn, item["mac"], pxe_log.since_last_wake(conn, item["mac"]))
            item["pxe"] = {"state": got["state"], "he": got["he"]}
    return missing


def _wave_forecast(ctx, round_row: sqlite3.Row, wave: sqlite3.Row | None,
                   started_at: str | None) -> dict | None:
    """‏#1843: ‏`learn_eta.wave` לגל הנוכחי — לגל שרץ לפי הכוננים שיצא אליהם
    (‏`wave_rates.roster`), לגל פתוח לפי מי שהצטרף עד עכשיו. ‏None = אין גל."""
    session_id = round_row["wave_session_id"]
    if not session_id or wave is None or wave["state"] not in ("open", "running"):
        return None
    stored = drive_rates.decision(ctx.conn, session_id)
    drives = ((stored or {}).get("roster") if wave["state"] == "running" else None)         or wave_drives(ctx.conn, session_id)
    library = getattr(ctx, "library", None)
    manifest = library.get(round_row["image_id"]) if library is not None else None
    size = manifest.get("total_compressed_bytes") if isinstance(manifest, dict) else None
    return learn_eta.wave(ctx.conn, drives, size, started_at, session_id)


def _wave_rate(ctx, round_row: sqlite3.Row, wave: sqlite3.Row | None) -> dict | None:
    """‏#447: מה שנבחר לגל כשיצא; לגל `open` — מה שייבחר לפי מי שהצטרף עד
    עכשיו (‏`projected`). גם כשהמשדר הוא מחשב בנייה (#715, ‏`direct: true`)
    — הקצב שלו נקבע כאן. ‏None: גל רץ/סגור שלא נשמרה לו בחירה — "לא ידוע",
    לא התקרה."""
    session_id = round_row["wave_session_id"]
    if not session_id:
        return None
    direct = _direct().is_build_disk(round_row)
    stored = drive_rates.decision(ctx.conn, session_id)
    if stored is not None:
        # ‏`roster` (הכוננים כשהגל יצא) הוא לזיכרון בשרת, לא למסך.
        return {**{k: v for k, v in stored.items() if k != "roster"},
                "projected": False}
    if wave is None or wave["state"] != "open" or round_row["state"] != "active":
        return None
    ceiling = ctx.sender.max_bitrate if ctx.sender is not None else None
    # כרטיס ההפצה (15/10) — רק כשהשרת הוא המשדר; גל ישיר יוצא מהכרטיס של
    # מחשב הבנייה.
    link = ctx.sender.link() if ctx.sender is not None and not direct else None
    return {**drive_rates.choose(ctx.conn, wave_drives(ctx.conn, session_id), ceiling,
                                 session_id, direct=direct, link=link),
            "projected": True}


def _wave_pace(started_at: str | None, machines: list[dict]) -> dict:
    """‏#410: מה שהמפעיל רואה בראש המסך — כמה זמן הגל רץ, באיזה קצב,
    ומתי הוא צפוי להסתיים.

    ‏`elapsed_s` מ-`started_at` של הגל (‏`running` בלבד). ‏`rate_bps` הוא
    **האיטית** מבין המגירות הכותבות שנמדדו, ו-`eta_s` הוא **הארוך** —
    הגל נגמר כשהאחרונה נגמרת, וקצב ממוצע-על-הכול היה מסתיר מגירה
    שמפגרת. ‏None כשאין מגירה כותבת שנמדדה (עיקרון 5: לא 0).
    """
    since = _iso_or_none(started_at)
    elapsed = (max(0, int((datetime.now(timezone.utc) - since).total_seconds()))
               if since is not None else None)
    writing = [d for m in machines for d in m.get("drawer_list", [])
               if d.get("state") == "writing" and d.get("rate_bps") is not None]
    rate = min(d["rate_bps"] for d in writing) if writing else None
    etas = [d["eta_s"] for d in writing if d.get("eta_s") is not None]
    return {"started_at": started_at, "elapsed_s": elapsed,
            "rate_bps": rate, "eta_s": max(etas) if etas else None}


def _wake_targets(conn: sqlite3.Connection, macs) -> set[str]:
    """‏נדב 17/10: "הער" מעיר רק את מה שנבחר. רשימה לא ריקה של מחשבי שיכפול
    רשומים, אחרת 400 — בקשה שלא נקראה אינה "כל החדר" (עיקרון 5): בחדר יש
    מכונה שאסור להעיר מרחוק, ו-`wol_sent grp_CLONERS count=3` העיר גם אותה."""
    if not isinstance(macs, list) or not macs:
        raise HTTPException(400, "לא נבחר מחשב שיכפול להערה")
    chosen = set()
    for raw in macs:
        mac = registry.normalize_mac(raw) if isinstance(raw, str) else None
        # לפי תפקיד הקבוצה ולא לפי `grp_CLONERS` — כך גם אחרי #1555 (חדר שכולל כל
        # קבוצת שיכפול), בכל סדר מיזוג.
        if mac is None or conn.execute(
                "SELECT 1 FROM machines m JOIN groups g ON g.id = m.group_id"
                " WHERE m.mac = ? AND g.role = 'cloner'", (mac,)).fetchone() is None:
            raise HTTPException(400, f"אינו מחשב שיכפול רשום: {str(raw)[:32]}")
        chosen.add(mac)
    return chosen


def _direct():
    """‏`direct` מייבא את `room` (ולידציית היעדים, הסבב הפעיל) — ולכן כאן
    הייבוא מאוחר, באותה צורה כמו `session_view` ב-`sessions.py`."""
    from . import direct                 # noqa: PLC0415
    return direct


# --- ה-API -------------------------------------------------------------------


def create_room_router(ctx, wake=None, origin: str = "console") -> APIRouter:
    """`wake` מוזרק מ-app.py כדי ששליחת ה-WoL תהיה אותה פונקציה בכל
    המערכת (וניתנת לזיוף בבדיקות). ‏`origin` — ‏`build` כשהראוטר יושב על
    פורט הסוכן (הקיוסק של מחשב הבנייה); נרשם ב"בטל דיסק זה" (#1386)."""
    router = APIRouter(prefix="/api/console/room")
    current_user, _admin_only = auth.dependencies(ctx.conn)

    def room_operator(user=Depends(current_user)) -> tuple[str, str]:
        """מחובר **וגם** בתפקיד שמותר לו להפעיל את החדר."""
        if user[1] not in ROOM_OPERATOR_ROLES:
            journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.get("")
    def status(user=Depends(current_user)):
        # Observation is never a room state-machine event (#446).
        return status_view(ctx)

    @router.get("/driver-models")
    def list_driver_models(user=Depends(current_user)):
        """‏#1269: הדגמים שיש להם חבילת דרייברים בספרייה — הרשימה שממנה
        המפעיל בוחר. רק מכללי הדגם של המניפסטים (‏`drivers.models`)."""
        return drivers.models(library_packages(ctx))

    @router.post("")
    async def open_(request: Request, user=Depends(room_operator)):
        body = await request.json()
        source = body.get("source")
        try:
            # ‏#715: `source: {kind: build_disk, mac, disk}` — המקור הוא דיסק
            # במחשב הבנייה, אין `image_id`, והיעדים הם בחירה מפורשת.
            if isinstance(source, dict) and source.get("kind") == _direct().BUILD_DISK:
                return _direct().open_direct_round(
                    ctx, source, body.get("target_slots"),
                    body.get("target_drives"), user[0],
                    body.get("driver_models"),     # #1269
                    origin=request_origin(request))   # #1576
            return open_round(
                ctx, body.get("image_id", ""),
                int(body.get("target_drives", 0)), user[0],
                body.get("target_slots"),          # #1410: explicit, non-empty selection is required
                body.get("expand_partition"),      # #59: בחירת ההרחבה (אופציונלי)
                body.get("driver_models"),         # #1269: דרייברים לכל דיסק (אופציונלי)
            )
        except SessionError as exc:       # לפני ValueError — הוא יורש ממנו
            raise HTTPException(409, str(exc))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc) or "בקשה לא תקינה")

    @router.post("/start")
    def start_now(user=Depends(room_operator)):
        round_row = active_round(ctx.conn)
        if round_row is None:
            raise HTTPException(409, "אין סבב חדר פעיל")
        if not _direct().source_ready(round_row):
            raise HTTPException(
                409, "מחשב הבנייה עדיין קורא את הדיסק — המניפסט טרם הגיע")
        warned = precheck_now(ctx.conn, ctx.store, round_row)   # #432
        try:
            ctx.store.start_now(round_row["wave_session_id"], user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        journal_precheck(ctx.conn, round_row, warned, user[0])
        return {"ok": True, "warned": len(warned)}

    @router.post("/missing/{mac}/poweroff")
    async def poweroff_missing(mac: str, request: Request, user=Depends(room_operator)):
        """‏#65 "כבה אותו": כיבוי **מכונה אחת** שהגל הפתוח מראה כחסרה — כדי
        להחליף בה מגירות. רק לגל `open` (כיבוי באמצע כתיבה מפיל אותה), רק
        למכונה שב-`missing` ושעונה ב-hello (הכיבוי נמסר בתשובתו, דרך הסוכן
        ו-`arm_wol`, #587). ‏`confirm_name` = שם המחשב, מחלון האישור (#1270).
        על פורט הסוכן כי זו הכרעת נדב (05/09): הפלט והבחירה — במחשב הבנייה."""
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        canon = registry.normalize_mac(mac)
        if canon is None:
            raise HTTPException(400, "MAC לא תקין")
        view = status_view(ctx)["round"]
        if view is None or view["state"] != "active":
            raise HTTPException(409, "אין סבב חדר פעיל")
        if view["wave_state"] != "open":
            raise HTTPException(409, "הגל כבר יצא לדרך — כיבוי עכשיו מפיל כתיבה חיה")
        entry = next((m for m in view["missing"] if m["mac"] == canon), None)
        if entry is None:
            raise HTTPException(409, "המחשב אינו חסר בגל הזה")
        if not entry["can_poweroff"]:
            raise HTTPException(409, "המחשב אינו מחובר — אין למי לשלוח כיבוי")
        name = entry["name"] or canon
        if typed != name:
            raise HTTPException(400, "השם שנשלח אינו שם המחשב")
        # ‏#1367 (נדב 14/10): אישור מעל אזהרת "באמצע שיכפול מקומי" — מהחלון
        # של הקונסולה. רק `true` ממש; בלעדיו הסוכן זורק בקשה שמגיעה בזמן העתקה.
        confirmed = isinstance(body, dict) and body.get("confirm_local_copy") is True
        from . import room_console           # noqa: PLC0415 — room_console מייבא את room
        room_console.request_power(ctx.conn, [canon], room_console.POWEROFF, user[0], confirmed=confirmed)
        journal(ctx.conn, "room_missing_poweroff",
                f"{canon} ({name})" + (" local copy confirmed" if confirmed else ""), user[0])
        return {"requested": 1, "ttl_s": room_console.POWEROFF_TTL_SECONDS}

    @router.post("/wake")
    async def wake_room(request: Request, user=Depends(room_operator)):
        if wake is None:
            raise HTTPException(503, "WoL אינו מחובר בשרת הזה")
        # ‏נדב 17/10: `macs` — מחשבי השיכפול שנבחרו במסך; רק הם מתעוררים. **בלי גוף
        # בכלל** (הקונסולה) — כל החדר, כמו קודם. גוף שלא נקרא, שאינו אובייקט, או
        # אובייקט בלי `macs` ("mac" בשגיאת כתיב) — 400: בקשה שלא הובנה אינה "כל החדר"
        # (עיקרון 5), ובחדר יש מכונה שאסור להעיר מרחוק (סקירת אבטחה, #1577).
        raw = await request.body()
        only = None
        if raw.strip():
            try:
                body = json.loads(raw)
            except ValueError:
                raise HTTPException(400, "גוף הבקשה אינו JSON — אף מחשב לא הוער") from None
            if not isinstance(body, dict) or "macs" not in body:
                raise HTTPException(400, "לא נבחר מחשב שיכפול להערה (macs)")
            only = _wake_targets(ctx.conn, body["macs"])
        sent = wake() if only is None else wake(only=only)
        journal(ctx.conn, "wol_sent", f"{CLONERS_GROUP} count={sent}"
                + (f" macs={','.join(sorted(only))}" if only is not None else ""), user[0])
        # ‏`sent` ולא `woken`: חבילת WoL היא UDP broadcast בלי ACK —
        # ‏`sendto` שהצליח פירושו שהקרנל קיבל 102 בייט, ותו לא. אין
        # ראיה שהמכונה קמה, והשם `woken` טען טענה שלא נמדדה (#528;
        # המודול עצמו מונה `sent`). הראיה החיובית להתעוררות היא hello
        # שמגיע בחלון — וזו הכרעה נפרדת.
        # הסיבה עולה למסך ולא רק ליומן: "0 מחשבים" בלי הסבר שולח את
        # הטכנאי לחפש WoL ב-BIOS של 12 מכונות, כשהסיבה היא כבל אחד
        # בשרת (#74). ‏getattr — שולח מוזרק בטסטים עשוי להחזיר int רגיל.
        return {"sent": int(sent),
                "failed": len(getattr(sent, "failed", ())),
                "reasons": list(getattr(sent, "reasons", ()))}

    @router.post("/drawer/cancel")
    async def cancel_drawer(request: Request, user=Depends(room_operator)):
        """‏#1386 "בטל דיסק זה": מגירה אחת יוצאת מגל שכותב; השאר ממשיכות.

        ‏`{mac, dev}` — המגירה כפי שהיא ב-`drawer_list`. רק בגל שיצא לדרך, רק
        למחשב שחבר בו, ורק למגירה שהדיווח האחרון שלה `writing` (המסכים מציגים
        את הכפתור רק אז). הבקשה נמסרת לסוכן בתשובת הדיווח הבא (~2 ש');
        "בוטל" נרשם ביומן כשהסוכן מדווח `cancelled` (`drawer_cancel.confirmed`).
        בלי הקלדת שם (#1270) — חלון האישור הוא האישור."""
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, "גוף הבקשה אינו אובייקט JSON")
        mac = registry.normalize_mac(body.get("mac")) if isinstance(body.get("mac"), str) else None
        dev = body.get("dev")
        if mac is None or not isinstance(dev, str) or not dev.isalnum() or len(dev) > 32:
            raise HTTPException(400, "חסר מחשב (mac) או דיסק (dev) תקין")
        round_row = active_round(ctx.conn)
        wave = (ctx.conn.execute("SELECT * FROM sessions WHERE id = ?",
                                 (round_row["wave_session_id"],)).fetchone()
                if round_row is not None and round_row["wave_session_id"] else None)
        if wave is None:
            raise HTTPException(409, "אין סבב חדר פעיל")
        if wave["state"] != "running":
            raise HTTPException(409, "הגל עוד לא יצא לדרך — אין כתיבה לבטל")
        member = ctx.conn.execute(
            "SELECT * FROM session_members WHERE session_id = ? AND mac = ?",
            (wave["id"], mac)).fetchone()
        if member is None:
            raise HTTPException(409, "המחשב אינו בגל הזה")
        target = next((t for t in json.loads(member["targets_json"] or "[]")
                       if isinstance(t, dict) and t.get("dev") == dev), None)
        prior = drawer_cancel.existing(ctx.conn, wave["id"], mac, dev)
        if prior is not None:
            raise HTTPException(409, "הדיסק כבר בוטל — " + drawer_cancel.by_dev(
                ctx.conn, wave["id"], mac)[dev]["text_he"])
        if target is None or target.get("state") != "writing":
            raise HTTPException(409, "הדיסק אינו כותב עכשיו — אין מה לבטל")
        drawer_cancel.request(ctx.conn, wave["id"], mac, dev, user[0], origin)
        named = slot_names.label(ctx.conn, mac, dev=dev) or dev
        journal(ctx.conn, "room_drawer_cancel_requested",
                f"{round_row['id']} {mac} {named} in {wave['id']} ({origin})", user[0])
        return {"ok": True}

    @router.post("/close")
    async def close_(request: Request, user=Depends(room_operator)):
        # גוף ריק או לא-JSON הוא בדיוק המקרה שההקלדה נועדה לתפוס, ולכן
        # הוא נופל לאישור ריק — 400 עם ההסבר, ולא 500 שנראה כתקלת שרת.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        try:
            close_round(ctx, user[0], typed)
        except SessionError as exc:       # לפני ValueError — הוא יורש ממנו
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    return router
