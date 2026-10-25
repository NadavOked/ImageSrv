"""זיכרון כשלי הכתיבה — הצד של השרת ב-#874.

הכרעת נדב 16/09: הסימון על הדיסק (#845) **נפסל** — הסוכן אינו משנה דיסק
מחוץ לשיכפול. במקומו השרת זוכר, מדיווח ההתקדמות (ממשק 4) של יעד שנכשל
בכתיבה, **מי** נכשל בשתי צורות: הסידורי של הדיסק, והחריץ (מכונה+פורט).
שתיהן נדרשות: בראיה השנייה (07:03) דיסק אחר על אותו פורט נכשל באותה
חתימה — הפורט אשם והדיסקים חפים; ואותיות ‏sd* מתחלפות בין אתחולים,
ולכן אף דבר כאן אינו מזוהה לפי אות.

באתחול, hello (ממשק 3) עונה לסוכן `disk_failures` — הרשומות הפתוחות
שתואמות לסידוריים שהמכונה שלחה **או** לחריץ באותה מכונה — והסוכן מציב
את הדיסק אדום (`failed_last`) לפני הסבב, בלי לכתוב עליו דבר.

הזיכרון אינו גובר על המפעיל: "נקה" (`cleared_at`) תמיד זמין — הדיסק
הוחלף, הכבל תוקן — ורשומה מנוקה אינה צובעת עוד.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from . import ata_cause, slot_names
from .db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagectl.disk_failures")


#: ‏#1580 (נדב 17/10): כשל שהוא **עצירה באמצע** — הסבב נעצר או נסגר, המכונה
#: כובתה, הזרם לא הגיע — אינו שגיאת דיסק, ו"סיבה כזו לא צובעת את הדיסק".
#: הסיווג לפי מה שנרשם: ‏`cause` מה-ATA (כבל/דיסק גוברים — אם הקרנל ראה
#: שגיאה, היא ההסבר) ו-`error` של הסוכן. הביטויים הם המילים שהסוכן כותב
#: (‏drawers.sh, ‏verdict.sh, ‏localclone) — מקום אחד, נבדק בטסט מולם.
INTERRUPTION_PHRASES = (
    "הזרם לא הגיע",                 # drawers.sh: פג הזמן -- הזרם לא הגיע
    "הזרם נקטע לפני סופו",          # drawers.sh: המקור (‏udp-receiver/curl) יצא באמצע
    "השרת סגר את הסבב",             # verdict.sh: ROUND_CLOSED_WHY (ברזל 17/10)
    "כובה מהקונסולה",                # localclone: המחשב כובה באמצע
    "השיכפול נעצר לפני מחיצה",       # localclone: נעצר
)


def interrupted(cause: str | None, error: str | None) -> bool:
    """האם הרשומה היא עצירה באמצע ולא שגיאת דיסק (#1580). ‏`cable`/`disk`
    לעולם אינם עצירה; בלי `error` אין ראיה — הרשומה נשארת כמות שהיא."""
    if cause in ("cable", "disk") or not isinstance(error, str):
        return False
    return any(p in error for p in INTERRUPTION_PHRASES)


#: ‏#1728 (ברזל rc5, 18/10): פסק האורך של fanout על הזרם עצמו — כל המגירות
#: קיבלו אותם בייטים, ולכן הוא של הזרם ולא של אף דיסק. ‏`_write_fail` של
#: verdict.sh בסוכן לפני #1728 כתב אותו כ"הכתיבה לדיסק נכשלה (fanout: …)"
#: ושלח `ata_log` — ושני דיסקים תקינים נצבעו כתום.
STREAM_PHRASES = ("fanout: empty stream", "fanout: short stream", "fanout: long stream")


def stream_fault(cause: str | None, error: str | None) -> bool:
    """האם הכשל הוא של הזרם ואין ראיה מהדיסק עצמו (#1728). ‏`cable`/`disk`
    הם ראיה מהקרנל וגוברים; בלי `error` אין ממה לקבוע — לא."""
    if cause in ("cable", "disk") or not isinstance(error, str):
        return False
    return any(p in error for p in STREAM_PHRASES)


def _quiet(row: sqlite3.Row) -> bool:
    """האם הרשומה אינה צובעת את הדיסק: עצירה באמצע (#1580), מגירה שנשלפה
    (‏`removed`, נדב 17/10), או כשל של הזרם (#1728). רשומה שנרשמה `unclassified`
    לפני הסיווג הזה (‏id=1241 במעבדה) נקראת שוב לפי הראיה שבה — אחרת היא
    נשארת כתומה עד "נקה"."""
    cause, error = row["cause"], row["error"]
    if cause == "removed" or interrupted(cause, error) or stream_fault(cause, error):
        return True
    if cause != "unclassified":
        return False
    return ata_cause.classify(_lines(row["ata_log"]), error if isinstance(error, str) else "") == "removed"


def _cause(row: sqlite3.Row) -> str:
    """הסיבה כפי שהיא נקראת היום (#1760). רשומה `unclassified` שנרשמה לפני ש-#1742
    הכיר את `readonly` (‏D6, ‏18/10: ‏`dd: … Operation not permitted`) נקראת שוב לפי
    הראיה שבה — אחרת הדיסק הלקריאה-בלבד חוזר **כתום** ("נכשל בשיכפול הקודם") ולא
    אדום, ו-send_held_smart עוצר את הזרם על שאלה שאסור לשאול."""
    cause = row["cause"]
    if cause == "unclassified" and ata_cause.classify(
            _lines(row["ata_log"]), row["error"] if isinstance(row["error"], str) else "") == "readonly":
        return "readonly"
    return cause


def _lines(raw: str | None) -> list[str]:
    try:
        return ata_cause.clean_log(json.loads(raw or "[]"))
    except ValueError:
        return []


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record(conn: sqlite3.Connection, session_id: str, mac: str, target: dict) -> dict | None:
    """רושם כשל כתיבה של יעד אחד, פעם אחת לכל (סבב, מכונה, יעד).

    נקרא מ-`reports.ingest` על יעד `failed` שנושא `ata_log` (סוכן חדש;
    סוכן ישן שאינו שולח אותו אינו נרשם — אין לו סידורי ולא פורט, ואין
    למה להצמיד את הזיכרון). מחזיר את הרשומה שנכתבה, או `None` כשכבר
    הייתה — או כשהכשל הוא של הזרם בלי ראיה מהקרנל (#1728: אין רשומה בלי
    ראיה מהדיסק עצמו; הכשל עצמו גלוי ב-`targets_json` של הדיווח).
    ‏`mac` כבר קנוני (הקורא נרמל).
    """
    dev = target.get("dev")
    if not isinstance(dev, str) or not dev or "ata_log" not in target:
        return None
    lines = ata_cause.clean_log(target.get("ata_log"))
    serial = target.get("serial") if isinstance(target.get("serial"), str) else None
    error = target.get("error") if isinstance(target.get("error"), str) else None
    cause = ata_cause.classify(lines, error or "")
    if stream_fault(cause, error):
        return None
    row = {
        "session_id": session_id, "mac": mac, "dev": dev, "serial": serial or None,
        "port": _int(target.get("port")), "ata_port": _int(target.get("ata_port")),
        "at": now_iso(), "cause": cause, "error": error,
    }
    with _write_lock, writing(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO disk_failures (session_id, mac, dev, serial, port,"
            " ata_port, at, cause, error, ata_log) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (session_id, mac, dev, row["serial"], row["port"], row["ata_port"],
             row["at"], cause, error, json.dumps(lines)),
        )
        inserted = cur.rowcount == 1
        row["id"] = cur.lastrowid if inserted else None
    if not inserted:
        return None
    journal(conn, "disk_failure",
            f"{mac} {dev} serial={serial or '?'} port={row['port'] if row['port'] is not None else '?'}"
            f" cause={cause}"
            # ‏#704: השם שהמפעיל נתן לחריץ; בלי שם — השורה כמו היום.
            + slot_names.journal_suffix(slot_names.label(conn, mac, port=row["port"], serial=serial)))
    return row


#: ‏#1760: מה שהקונסולה קוראת ברשומה של דילוג אדום על דיסק לקריאה בלבד.
READONLY_SKIP_ERROR = "הדיסק במצב קריאה בלבד — דולג, לא נכתב (#1732)"


def record_readonly(conn: sqlite3.Connection, session_id: str, mac: str, dev: str,
                    serial: str | None) -> bool:
    """‏#1760 (ברזל D6b): הסוכן דילג על דיסק **אדום כי הוא לקריאה בלבד** (‏disk_event
    ‏`fail`/`readonly`/`skip`) — נרשם כרשומה פתוחה `cause=readonly`, כדי שבגל הבא
    ובאתחול הבא (כש-`blockdev --setro` כבר נעלם) אותו דיסק יהיה **אדום** ולא ירוק
    או כתום (‏smart.sh: ‏disk_failure_readonly). לפי **סידורי בלבד** — ‏`port` ריק:
    קריאה-בלבד היא של הדיסק, ודיסק אחר באותו חריץ אינו הוא (`open_for` מתאים חריץ
    רק לרשומה שיש לה פורט). בלי סידורי אין זהות — אין רשומה. רשומה פתוחה אחת לדיסק:
    כבר קיימת = לא נרשם שוב. מחזיר אם נרשמה עכשיו."""
    if not serial:
        return False
    with _write_lock, writing(conn):
        if conn.execute(
            "SELECT 1 FROM disk_failures WHERE serial = ? AND cause = 'readonly'"
            " AND cleared_at IS NULL", (serial,),
        ).fetchone() is not None:
            return False
        cur = conn.execute(
            "INSERT OR IGNORE INTO disk_failures (session_id, mac, dev, serial, port,"
            " ata_port, at, cause, error, ata_log) VALUES (?,?,?,?,NULL,NULL,?,'readonly',?,'[]')",
            (session_id, mac, dev, serial, now_iso(), READONLY_SKIP_ERROR),
        )
        inserted = cur.rowcount == 1
    if inserted:
        journal(conn, "disk_failure", f"{mac} {dev} serial={serial} port=? cause=readonly")
    return inserted


def open_for(conn: sqlite3.Connection, mac: str, disks: list | None) -> list[dict]:
    """מה ה-hello עונה: הרשומות הפתוחות שתואמות למכונה הזאת (ממשק 3).

    ‏**סידורי** — כל רשומה פתוחה על אחד הסידוריים שהמכונה דיווחה, מכל
    מכונה שהיא (הדיסק נדד). ‏**חריץ** — כל רשומה פתוחה של אותה מכונה
    שיש לה פורט. ‏`port` בתשובה הוא **החריץ במכונה הזאת**: ברשומה של
    מכונה אחרת שתאמה לפי סידורי הוא `null` — פורט 2 של מכונה אחרת אינו
    פורט 2 כאן, והסוכן משווה פורט רק מול המכונה שלו.
    """
    serials = []
    for d in disks or []:
        s = d.get("serial") if isinstance(d, dict) else None
        if isinstance(s, str) and s and s not in serials:
            serials.append(s)
    marks = ",".join("?" * len(serials))
    sql = ("SELECT serial, mac, port, cause, error, ata_log, at FROM disk_failures"
           " WHERE cleared_at IS NULL AND ((mac = ? AND port IS NOT NULL)")
    params: list = [mac]
    if serials:
        sql += f" OR serial IN ({marks})"
        params += serials
    sql += ") ORDER BY at"
    out = []
    for r in conn.execute(sql, params).fetchall():
        if _quiet(r):
            continue                      # #1580 / שליפה: אינם צובעים
        out.append({
            "serial": r["serial"],
            "port": r["port"] if r["mac"] == mac else None,
            "cause": _cause(r),
            "at": r["at"],
        })
    return out


def list_open(conn: sqlite3.Connection) -> list[dict]:
    """הרשימה לקונסולה: כל הרשומות הפתוחות, החדשה ראשונה."""
    rows = conn.execute(
        "SELECT id, session_id, mac, dev, serial, port, ata_port, at, cause, error,"
        " ata_log FROM disk_failures WHERE cleared_at IS NULL ORDER BY at DESC, id DESC"
    ).fetchall()
    out = []
    for r in rows:
        if _quiet(r):
            continue                      # #1580 / שליפה: אינם צובעים
        try:
            lines = json.loads(r["ata_log"] or "[]")
        except ValueError:
            lines = []
        out.append({
            "id": r["id"], "session_id": r["session_id"], "mac": r["mac"],
            "dev": r["dev"], "serial": r["serial"], "port": r["port"],
            "disk_number": r["port"], "ata_port": r["ata_port"], "at": r["at"],
            "cause": _cause(r), "error": r["error"], "ata_log": lines,
        })
    return out


#: ‏#1792: שורת היומן של ניקוי אוטומטי — מה שהמפעיל קורא ליד "רשומת כשל דיסק נוקתה".
AUTO_CLEARED_HE = "נוקה אוטומטית: נכתב בהצלחה בסבב {round_id}"


def clear_written(conn: sqlite3.Connection, serials, round_id: str) -> list[int]:
    """‏#1792 (ברזל D2/D5, 19/10): דיסק שנכשל פעם אחת ונכתב אחר כך **במלואו** נשאר
    כתום ("נכשל בשיכפול הקודם"), והמשכפל עצר לשאלה בכל סבב עד "נקה" ידני.

    ‏`serials` — הסידוריים שהסבב ספר עכשיו ככתובים (‏`room._tally`: יעד `done`
    שהסידורי שהוטבע בו תואם לדיסק שבאותו התקן — ראיה חיובית שהדיסק **הזה** נכתב
    ואומת). הרשומות הפתוחות על הסידוריים האלה נסגרות, כל אחת עם שורת יומן.
    ‏**אדום אינו מתנקה לבד** (#1742, ‏#1580): רשומה שהסיבה שלה — כפי שהיא נקראת היום
    (`_cause`) — היא `readonly` נשארת עד "נקה"; ‏SMART נכשל אינו רשומה כאן כלל.
    לפי סידורי בלבד: רשומה בלי סידורי (תואמת רק לחריץ) אינה נסגרת — הצלחה של דיסק
    אחד אינה ראיה על חריץ. חוזר על עצמו בבטחה: רשומה שכבר נוקתה אינה נספרת שוב.
    מחזיר את המזהים שנסגרו עכשיו."""
    wanted = sorted({s for s in serials if isinstance(s, str) and s})
    if not wanted:
        return []
    rows = conn.execute(
        "SELECT id, serial, cause, error, ata_log FROM disk_failures WHERE cleared_at IS NULL"
        f" AND serial IN ({','.join('?' * len(wanted))}) ORDER BY id", wanted).fetchall()
    cleared = []
    with _write_lock, writing(conn):
        for r in rows:
            if _cause(r) == "readonly":
                continue                      # אדום: רק "נקה" של המפעיל
            if conn.execute(
                "UPDATE disk_failures SET cleared_at = ? WHERE id = ? AND cleared_at IS NULL",
                (now_iso(), r["id"]),
            ).rowcount == 1:
                cleared.append((r["id"], r["serial"]))
    for failure_id, serial in cleared:
        journal(conn, "disk_failure_cleared",
                f"#{failure_id} serial={serial} " + AUTO_CLEARED_HE.format(round_id=round_id))
    return [failure_id for failure_id, _ in cleared]


def clear(conn: sqlite3.Connection, failure_id: int, by: str = "") -> bool:
    """"נקה": הרשומה מקבלת `cleared_at`. אמת רק אם רשומה פתוחה נסגרה
    עכשיו — רשומה שאינה קיימת או שכבר נוקתה אינה "נוקתה" (עיקרון 5)."""
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE disk_failures SET cleared_at = ? WHERE id = ? AND cleared_at IS NULL",
            (now_iso(), failure_id),
        )
        done = cur.rowcount == 1
    if done:
        journal(conn, "disk_failure_cleared", f"#{failure_id}", user=by)
    return done
