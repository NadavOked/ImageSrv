"""‏"בטל דיסק זה" באמצע גל — מגירה אחת יוצאת מהשידור (#1386, נדב 17/10).

נדב 13/10: לעצור רק דיסק איטי או בעייתי כדי שהשאר ימשיכו מהר. שלושה
מקומות מבקשים את זה: הקונסולה, מסך ההפצה במחשב הבנייה (שניהם עוברים
כאן, בשם המשתמש המחובר), ומסך מחשב השיכפול עצמו (מקומי, בלי כניסה —
כמו הכרעת SMART). הבקשה נמסרת לסוכן **בתשובת דיווח ההתקדמות** (ממשק 4,
‏`cancel`), שהוא ממילא שולח כל ~2 שניות; הסוכן הורג את ה-zstd של
המגירה, ‏fanout מקבל EPIPE ומפיל רק אותה (#1603), טבלת המחיצות נמחקת,
והמגירה מדווחת `cancelled`.

**בוטל אינו נכשל:** אין `ata_log`, ולכן אין `disk_failures` (#874) — בסבב
הבא הדיסק ירוק, ונכתב רגיל. הוא גם אינו `done`, ולכן אינו נספר כנכתב; אבל
‏(#1936, ‏FI O2) הביטול **סופי לסבב הזה** — `_tally` סוגר את המגירה
(`cancelled_serials`), והיא אינה פותחת גל נוסף ואינה נכתבת שוב בו.

עיקרון 5: "בוטל" נרשם ביומן רק כשהסוכן **דיווח** `cancelled` (ראיה
חיובית). הבקשה עצמה נרשמת בנפרד — "ביטול התבקש".
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from server.system import timezone
from server.db import _write_lock, journal, now_iso, writing

#: מאיפה הגיע הביטול. ‏`cloner` — מסך מחשב השיכפול עצמו (דווח בדיווח).
SOURCES = ("console", "build", "cloner")


def request(conn: sqlite3.Connection, session_id: str, mac: str, dev: str,
            user: str, source: str) -> bool:
    """רושם בקשת ביטול. ‏False = כבר יש בקשה למגירה הזו בגל הזה."""
    with _write_lock, writing(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO drawer_cancels (session_id, mac, dev, requested_by,"
            " source, requested_at) VALUES (?,?,?,?,?,?)",
            (session_id, mac, dev, user, source, now_iso()),
        )
    return cur.rowcount == 1


def existing(conn: sqlite3.Connection, session_id: str, mac: str,
             dev: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM drawer_cancels WHERE session_id = ? AND mac = ? AND dev = ?",
        (session_id, mac, dev)).fetchone()


def pending(conn: sqlite3.Connection, session_id: str, mac: str) -> list[dict]:
    """מה נמסר לסוכן בתשובת הדיווח: כל בקשה מהשרת שטרם אושרה.

    ‏`who_he` הוא מה שמסך מחשב השיכפול יכתוב ("בוטל <who_he>")."""
    rows = conn.execute(
        "SELECT dev, requested_by, source FROM drawer_cancels"
        " WHERE session_id = ? AND mac = ? AND source != 'cloner' AND done_at IS NULL"
        " ORDER BY dev", (session_id, mac)).fetchall()
    return [{"dev": r["dev"], "who_he": _who_he(r["source"], r["requested_by"])} for r in rows]


def _who_he(source: str, user: str | None) -> str:
    if source == "cloner":
        return "ממסך מחשב השיכפול"
    if source == "build":
        return f"ממחשב הבנייה ({user})" if user else "ממחשב הבנייה"
    return f"על ידי {user}" if user else "מהקונסולה"


def _hhmm(conn: sqlite3.Connection, iso: str | None) -> str:
    if not iso:
        return ""
    try:
        at = datetime.fromisoformat(iso)
        try:
            at = at.astimezone(ZoneInfo(timezone.zone_name(conn)))
        except Exception:                      # noqa: BLE001 — אזור לא נקרא: UTC, לא שעה מומצאת
            pass
        return at.strftime("%H:%M")
    except ValueError:
        return ""


def by_dev(conn: sqlite3.Connection, session_id: str, mac: str) -> dict[str, dict]:
    """הבקשות של מכונה בגל, לפי dev — מה ש-`drawer_list` מצרף לכל מגירה.

    ‏`text_he` — השורה שהמסכים מציגים: "בוטל על ידי nadav · 16:06".
    ‏`done` — הסוכן דיווח `cancelled` (עד אז המגירה "מבטלת…")."""
    out = {}
    for r in conn.execute(
            "SELECT * FROM drawer_cancels WHERE session_id = ? AND mac = ?",
            (session_id, mac)).fetchall():
        at = r["done_at"] or r["requested_at"]
        who = _who_he(r["source"], r["requested_by"])
        hhmm = _hhmm(conn, at)
        out[r["dev"]] = {
            "by": r["requested_by"] or None, "source": r["source"],
            "requested_at": r["requested_at"], "done_at": r["done_at"],
            "done": r["done_at"] is not None,
            "text_he": f"בוטל {who}" + (f" · {hhmm}" if hhmm else ""),
        }
    return out


def confirmed(conn: sqlite3.Connection, session_id: str, mac: str, dev: str,
              where: str) -> None:
    """הסוכן דיווח `cancelled` על המגירה — הראיה. נקרא מ-`reports.ingest`
    פעם אחת, במעבר. ביטול ממסך מחשב השיכפול לא עבר כאן קודם, ולכן נרשם
    עכשיו (`cloner`). ‏`where` — "מחשב 2 sdb" ליומן."""
    stamp = now_iso()
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT OR IGNORE INTO drawer_cancels (session_id, mac, dev, requested_by,"
            " source, requested_at) VALUES (?,?,?,?,?,?)",
            (session_id, mac, dev, None, "cloner", stamp))
        conn.execute(
            "UPDATE drawer_cancels SET done_at = ? WHERE session_id = ? AND mac = ?"
            " AND dev = ? AND done_at IS NULL", (stamp, session_id, mac, dev))
    row = existing(conn, session_id, mac, dev)
    who = _who_he(row["source"], row["requested_by"]) if row else ""
    journal(conn, "room_drawer_cancelled", f"{where} in {session_id} — בוטל {who}",
            (row["requested_by"] or "") if row else "")
