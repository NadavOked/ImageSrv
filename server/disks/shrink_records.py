"""זיכרון הכיווץ של דיסק המקור — הצד של השרת ב-#926.

‏#87 מכווץ את מחיצת ה-NTFS של דיסק הבנייה לפני הקליטה ומחזיר אותה
אחריה, והפריסה המקורית ישבה ב-`targets/<disk>/shrunk` — ‏tmpfs. אובדן
חשמל או אתחול בין `ntfsresize -s` לבין ההחזרה השאיר את הדיסק מכווץ
בלי שאיש יודע. אותה משפחה כמו #874, ואותו פתרון: השרת זוכר.

הסוכן שולח `shrink-open` **לפני** הכתיבה הראשונה למקור, עם הכניסה
המלאה (idx, start, size, GUIDs, דגלים, שם) של **כל** מחיצה שתכווץ —
`partitions`, רשומה **אחת לדיסק** (‏#929) — כל מה ש-`shrink_restore_source`
צריך כדי להחזיר — ובלי 2xx הוא לא מכווץ. אחרי החזרה מוצלחת של כולן הוא
שולח `shrink-close`. ‏hello (ממשק 3, `shrink_open`) עונה למכונה שדיווחה את
הסידורי של דיסק עם רשומה פתוחה, והסוכן — במחשב הבנייה — מציע להחזיר.

**הסידורי הוא הזהות.** ‏`mac`/`port`/`dev` הם המקום שבו זה קרה, לתצוגה
בקונסולה; דיסק אחר באותו חריץ אינו תואם, ודיסק שנדד לחריץ אחר כן. סידורי
ריק = אין למה להצמיד זיכרון = סירוב לפני שנכתב בייט.

העמודות `idx`/`start_sector`/… בטבלה הן של המחיצה **הראשונה** — לקורא
שקדם ל-#929; `partitions` (JSON) נושאת את כולן, וזה מה שהסוכן קורא.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from boot.grub_menu import normalize_mac as lenient_mac

from server.db import _write_lock, journal, now_iso, writing
from server.disks import smart_history

log = logging.getLogger("imagesrv.shrink_records")


class BadRecord(ValueError):
    """גוף שאי-אפשר לזכור ממנו: ‏`code` הוא מה שהתשובה נושאת."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AlreadyOpen(Exception):
    """כבר יש רשומה פתוחה על הסידורי הזה — הדיסק מכווץ לפי הזיכרון."""

    def __init__(self, record_id: int, opened_at: str) -> None:
        super().__init__(f"open record #{record_id} since {opened_at}")
        self.record_id = record_id
        self.opened_at = opened_at


#: השדות שהפריסה חייבת, לכל מחיצה: בלעדיהם אין מה להחזיר.
_INTS = ("idx", "start_sector", "size_sectors")
_PART_TEXT = ("unique_guid", "attrs", "name")
_OPTIONAL_TEXT = ("dev", "model", "image_name", "task_id")

#: מה שכל מחיצה ברשומה נושאת — הסדר שבו `partitions` נכתבת ונקראת.
PART_FIELDS = ("idx", "start_sector", "size_sectors", "type_guid", "unique_guid",
               "attrs", "name", "ntfs_bytes")

FIELDS = ("id", "mac", "dev", "serial", "port", "model", "image_name", "task_id",
          "idx", "start_sector", "size_sectors", "type_guid", "unique_guid",
          "attrs", "name", "ntfs_bytes", "opened_at", "note")

#: כמה מהסיבה נשמר (סקירת Fable): שורת קונסולה, לא יומן שלם.
NOTE_MAX_CHARS = 400

#: ‏#2436: הדגלים חוזרים ל-sgdisk כ-`-A idx:=:0x<attrs>` -- 16 ספרות הקס, כפי
#: ש-sgdisk -i מדפיס אותם. ערך אחר (רווח, אות) היה ארגומנט נוסף לפקודה.
_ATTRS = re.compile(r"[0-9A-Fa-f]{16}")
_SEPARATORS = re.compile(r"[|\r\n]")


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _partition(entry: object) -> dict:
    """מחיצה אחת מהגוף — הכניסה המלאה שההחזרה צריכה, או `bad_layout`."""
    if not isinstance(entry, dict):
        raise BadRecord("bad_layout", "each partition must be an object")
    ints = {k: _int(entry.get(k)) for k in _INTS}
    type_guid = _text(entry.get("type_guid"))
    if any(v is None for v in ints.values()) or type_guid is None:
        raise BadRecord("bad_layout", "idx, start_sector, size_sectors and type_guid are required")
    # ‏GPT מתחיל מכניסה 1, ומחיצה בת 0 סקטורים אינה מחיצה (סקירת Fable).
    if ints["idx"] < 1 or ints["size_sectors"] < 1:
        raise BadRecord("bad_layout", "idx and size_sectors must be at least 1")
    attrs = entry.get("attrs")
    if attrs not in (None, "") and not (isinstance(attrs, str) and _ATTRS.fullmatch(attrs)):
        raise BadRecord("bad_layout", "attrs must be 16 hex digits")
    # ‏#2436: הסוכן קורא את הרשומה כשורה מופרדת ב-`|` -- שם `X|8000…` היה attrs
    # אחר, ושורה חדשה בשם הייתה שורה שלמה משלה.
    for key in ("type_guid", "unique_guid", "name"):
        if isinstance(entry.get(key), str) and _SEPARATORS.search(entry[key]):
            raise BadRecord("bad_layout", f"{key} must not carry '|', CR or LF")
    return {**ints, "type_guid": type_guid, "ntfs_bytes": _int(entry.get("ntfs_bytes")),
            **{k: _text(entry.get(k)) for k in _PART_TEXT}}


def _partitions(body: dict) -> list[dict]:
    """‏#929: `partitions` — רשימה, מחיצה אחת לפחות, אינדקסים שונים. גוף
    שקדם ל-#929 (מחיצה אחת בשדות העליונים) נקרא כרשימה של אחת. רשומה
    שאי-אפשר להחזיר ממנה את **כל** המחיצות היא רשומה שאין לפתוח."""
    raw = body.get("partitions")
    if raw is None:
        return [_partition(body)]
    if not isinstance(raw, list) or not raw:
        raise BadRecord("bad_layout", "partitions must be a non-empty list")
    parts = [_partition(p) for p in raw]
    if len({p["idx"] for p in parts}) != len(parts):
        raise BadRecord("bad_layout", "partitions must have distinct idx")
    return parts


def open_record(conn: sqlite3.Connection, body: dict, require_reported: bool = False) -> dict:
    """רושם את הפריסה המקורית לפני הכיווץ. מחזיר את הרשומה (עם `id`
    ו-`partitions`).

    זורק `BadRecord` (‏`bad_mac` / `no_serial` / `bad_layout`) על גוף שאין
    לזכור ממנו, ו-`AlreadyOpen` כשיש רשומה פתוחה על אותו סידורי.

    ‏#2492: ‏``require_reported`` (הסוכן, ‏`api.py`) — הסידורי חייב להיות מה שה-MAC
    הזה דיווח ל-`dev` הזה ב-hello האחרון (כמו `smart_history.record`); אחרת
    ‏`BadRecord("serial_not_reported")`. בלעדיו כל מכונה הייתה פותחת רשומה על
    דיסק של אחרת, ו-hello שלה היה מציע "להחזיר" דיסק שלא כווץ.
    """
    mac = lenient_mac(body.get("mac")) if isinstance(body, dict) else None
    if mac is None:
        raise BadRecord("bad_mac", "missing or malformed mac")
    serial = _text(body.get("serial"))
    if serial is None:
        raise BadRecord("no_serial", "the disk has no serial -- nothing to remember it by")
    parts = _partitions(body)
    dev = body.get("dev")
    if require_reported and (not isinstance(dev, str) or smart_history.serial_of(conn, mac, dev) != serial):
        raise BadRecord("serial_not_reported", "this machine did not report this serial on this disk")
    row = {
        "mac": mac, "serial": serial, "port": _int(body.get("port")),
        "opened_at": now_iso(), **parts[0], "partitions": parts,
        **{k: _text(body.get(k)) for k in _OPTIONAL_TEXT},
    }
    # ‏#2492: עם ``require_reported`` (הסוכן) — רשומה פתוחה **של המכונה הזו**. ה-hello מציע
    # למכונה רק את שלה (`open_for`), ולכן רשומה של מכונה אחרת על אותו סידורי אינה
    # "כבר מכווץ": היא הייתה חוסמת בשקט את רשומת הבעלים. שתיהן גלויות בקונסולה.
    prior_sql = "SELECT id, opened_at FROM shrink_records WHERE serial = ? AND closed_at IS NULL"
    prior_args: tuple = (serial,)
    if require_reported:
        prior_sql += " AND mac = ?"
        prior_args = (serial, mac)
    with _write_lock, writing(conn):
        prior = conn.execute(prior_sql, prior_args).fetchone()
        if prior is not None:
            raise AlreadyOpen(prior["id"], prior["opened_at"])
        cur = conn.execute(
            "INSERT INTO shrink_records (mac, dev, serial, port, model, image_name, task_id,"
            " idx, start_sector, size_sectors, type_guid, unique_guid, attrs, name,"
            " ntfs_bytes, opened_at, partitions) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["mac"], row["dev"], serial, row["port"], row["model"], row["image_name"],
             row["task_id"], row["idx"], row["start_sector"], row["size_sectors"],
             row["type_guid"], row["unique_guid"], row["attrs"], row["name"], row["ntfs_bytes"],
             row["opened_at"], json.dumps(parts, ensure_ascii=False)),
        )
        row["id"] = cur.lastrowid
    journal(conn, "shrink_open",
            f"{mac} {row['dev'] or '?'} serial={serial} port={row['port'] if row['port'] is not None else '?'}"
            " partitions " + ", ".join(f"{p['idx']}: {p['size_sectors']} sectors" for p in parts))
    return row


def open_owner(conn: sqlite3.Connection, serial: object,
               record_id: int | None = None, mac: str | None = None) -> str | None:
    """‏#2413: ה-MAC שפתח את הרשומה הפתוחה של הסידורי (ו-`record_id`, אם ניתן),
    או None כשאין כזו. ‏`api.py` משווה אותו ל-MAC של הפונה לפני close/note.
    ‏#2492: כמה מכונות יכולות להחזיק רשומה פתוחה על אותו סידורי — כשל-`mac` יש
    אחת מהן, הוא הבעלים שלה (ולא "הראשונה שנמצאה")."""
    if not isinstance(serial, str) or not serial:
        return None
    sql = "SELECT mac FROM shrink_records WHERE serial = ? AND closed_at IS NULL"
    params: list = [serial]
    if record_id is not None:
        sql += " AND id = ?"
        params.append(record_id)
    if mac is not None:
        sql += " ORDER BY mac = ? DESC"
        params.append(mac)
    row = conn.execute(sql, params).fetchone()
    return row["mac"] if row is not None else None


def close_record(conn: sqlite3.Connection, serial: str, record_id: int | None = None,
                 by: str = "agent", event: str = "shrink_close",
                 mac: str | None = None) -> bool:
    """סוגר את הרשומה הפתוחה של הסידורי. אמת רק אם רשומה פתוחה נסגרה
    עכשיו — רשומה שאינה קיימת או שכבר נסגרה אינה "נסגרה" (עיקרון 5).
    ‏`record_id`, אם ניתן, חייב להיות אותה רשומה. ‏`mac`, אם ניתן (‏#2413,
    הסוכן), חייב להיות המכונה שפתחה אותה — באותו UPDATE, בלי מרוץ."""
    if not isinstance(serial, str) or not serial:
        return False
    sql = "UPDATE shrink_records SET closed_at = ?, closed_by = ? WHERE serial = ? AND closed_at IS NULL"
    params: list = [now_iso(), by, serial]
    if record_id is not None:
        sql += " AND id = ?"
        params.append(record_id)
    if mac is not None:
        sql += " AND mac = ?"
        params.append(mac)
    with _write_lock, writing(conn):
        done = conn.execute(sql, params).rowcount == 1
    if done:
        journal(conn, event, f"serial={serial}" + (f" #{record_id}" if record_id is not None else ""),
                user="" if by == "agent" else by)
    return done


def note_record(conn: sqlite3.Connection, serial: str, record_id: int | None,
                note: str, mac: str | None = None) -> bool:
    """למה הרשומה עדיין פתוחה — "הטבלה הוחזרה, מערכת הקבצים לא נמתחה
    (ntfsresize …)" — כדי שהקונסולה תראה סיבה ולא רק "מכווץ". אמת רק אם
    רשומה פתוחה עודכנה; ‏`note` ריק אינו הערה (הקורא מסרב לפני). ‏`mac` — כמו
    ב-`close_record` (‏#2413)."""
    if not isinstance(serial, str) or not serial or not isinstance(note, str) or not note.strip():
        return False
    sql = "UPDATE shrink_records SET note = ? WHERE serial = ? AND closed_at IS NULL"
    params: list = [note.strip()[:NOTE_MAX_CHARS], serial]
    if record_id is not None:
        sql += " AND id = ?"
        params.append(record_id)
    if mac is not None:
        sql += " AND mac = ?"
        params.append(mac)
    with _write_lock, writing(conn):
        done = conn.execute(sql, params).rowcount == 1
    if done:
        journal(conn, "shrink_note", f"serial={serial}: {note.strip()[:NOTE_MAX_CHARS]}")
    return done


def _row_dict(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in FIELDS}
    # שורה שנפתחה לפני #929 (העמודה ריקה) היא רשומה של מחיצה אחת — העמודות.
    raw = r["partitions"]
    d["partitions"] = json.loads(raw) if raw else [{k: r[k] for k in PART_FIELDS}]
    return d


def open_for(conn: sqlite3.Connection, disks: list | None, mac: str | None = None) -> list[dict]:
    """מה ה-hello עונה (ממשק 3): הרשומות הפתוחות שהסידורי שלהן הוא אחד
    מהדיסקים שהמכונה דיווחה.

    ‏#2492 (סקירה): ‏`mac` — רק רשומות **שהמכונה הזו פתחה**. הקשירה ב-`shrink-open`
    נשענת על ה-hello של הפותח, כלומר על מה שהוא טען על עצמו; בלי הסינון כאן מכונה
    אחרת הייתה גורמת ל-hello שלי להציע "להחזיר" פריסה שהיא כתבה. רק סגירה של
    הבעלים נקבלת ממילא (#2413), ומאז #1466 הסוכן אינו פותח רשומות. דיסק שנדד —
    "נקה" בקונסולה."""
    serials = []
    for d in disks or []:
        s = d.get("serial") if isinstance(d, dict) else None
        if isinstance(s, str) and s and s not in serials:
            serials.append(s)
    if not serials:
        return []
    marks = ",".join("?" * len(serials))
    sql = f"SELECT * FROM shrink_records WHERE closed_at IS NULL AND serial IN ({marks})"
    if mac is not None:
        sql += " AND mac = ?"
        serials = [*serials, mac]
    rows = conn.execute(sql + " ORDER BY opened_at, id", serials).fetchall()
    return [_row_dict(r) for r in rows]


def list_open(conn: sqlite3.Connection) -> list[dict]:
    """הרשימה לקונסולה: כל הרשומות הפתוחות, החדשה ראשונה."""
    rows = conn.execute(
        "SELECT * FROM shrink_records WHERE closed_at IS NULL ORDER BY opened_at DESC, id DESC"
    ).fetchall()
    return [_row_dict(r) for r in rows]


def clear(conn: sqlite3.Connection, record_id: int, by: str = "") -> bool:
    """"נקה" מהקונסולה: המפעיל הרחיב בעצמו / הדיסק הוחלף. אמת רק אם
    רשומה פתוחה נסגרה עכשיו."""
    with _write_lock, writing(conn):
        done = conn.execute(
            "UPDATE shrink_records SET closed_at = ?, closed_by = ? WHERE id = ? AND closed_at IS NULL",
            (now_iso(), by or "console", record_id)).rowcount == 1
    if done:
        journal(conn, "shrink_cleared", f"#{record_id}", user=by)
    return done
