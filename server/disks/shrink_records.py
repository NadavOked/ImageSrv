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
import sqlite3

from boot.grub_menu import normalize_mac as lenient_mac

from server.db import _write_lock, journal, now_iso, writing

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


def open_record(conn: sqlite3.Connection, body: dict) -> dict:
    """רושם את הפריסה המקורית לפני הכיווץ. מחזיר את הרשומה (עם `id`
    ו-`partitions`).

    זורק `BadRecord` (‏`bad_mac` / `no_serial` / `bad_layout`) על גוף שאין
    לזכור ממנו, ו-`AlreadyOpen` כשיש רשומה פתוחה על אותו סידורי.
    """
    mac = lenient_mac(body.get("mac")) if isinstance(body, dict) else None
    if mac is None:
        raise BadRecord("bad_mac", "missing or malformed mac")
    serial = _text(body.get("serial"))
    if serial is None:
        raise BadRecord("no_serial", "the disk has no serial -- nothing to remember it by")
    parts = _partitions(body)
    row = {
        "mac": mac, "serial": serial, "port": _int(body.get("port")),
        "opened_at": now_iso(), **parts[0], "partitions": parts,
        **{k: _text(body.get(k)) for k in _OPTIONAL_TEXT},
    }
    with _write_lock, writing(conn):
        prior = conn.execute(
            "SELECT id, opened_at FROM shrink_records WHERE serial = ? AND closed_at IS NULL",
            (serial,)).fetchone()
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


def close_record(conn: sqlite3.Connection, serial: str, record_id: int | None = None,
                 by: str = "agent", event: str = "shrink_close") -> bool:
    """סוגר את הרשומה הפתוחה של הסידורי. אמת רק אם רשומה פתוחה נסגרה
    עכשיו — רשומה שאינה קיימת או שכבר נסגרה אינה "נסגרה" (עיקרון 5).
    ‏`record_id`, אם ניתן, חייב להיות אותה רשומה."""
    if not isinstance(serial, str) or not serial:
        return False
    sql = "UPDATE shrink_records SET closed_at = ?, closed_by = ? WHERE serial = ? AND closed_at IS NULL"
    params: list = [now_iso(), by, serial]
    if record_id is not None:
        sql += " AND id = ?"
        params.append(record_id)
    with _write_lock, writing(conn):
        done = conn.execute(sql, params).rowcount == 1
    if done:
        journal(conn, event, f"serial={serial}" + (f" #{record_id}" if record_id is not None else ""),
                user="" if by == "agent" else by)
    return done


def note_record(conn: sqlite3.Connection, serial: str, record_id: int | None,
                note: str) -> bool:
    """למה הרשומה עדיין פתוחה — "הטבלה הוחזרה, מערכת הקבצים לא נמתחה
    (ntfsresize …)" — כדי שהקונסולה תראה סיבה ולא רק "מכווץ". אמת רק אם
    רשומה פתוחה עודכנה; ‏`note` ריק אינו הערה (הקורא מסרב לפני)."""
    if not isinstance(serial, str) or not serial or not isinstance(note, str) or not note.strip():
        return False
    sql = "UPDATE shrink_records SET note = ? WHERE serial = ? AND closed_at IS NULL"
    params: list = [note.strip()[:NOTE_MAX_CHARS], serial]
    if record_id is not None:
        sql += " AND id = ?"
        params.append(record_id)
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


def open_for(conn: sqlite3.Connection, disks: list | None) -> list[dict]:
    """מה ה-hello עונה (ממשק 3): הרשומות הפתוחות שהסידורי שלהן הוא אחד
    מהדיסקים שהמכונה דיווחה — מכל מכונה שהיא, כי הדיסק נודד."""
    serials = []
    for d in disks or []:
        s = d.get("serial") if isinstance(d, dict) else None
        if isinstance(s, str) and s and s not in serials:
            serials.append(s)
    if not serials:
        return []
    marks = ",".join("?" * len(serials))
    rows = conn.execute(
        f"SELECT * FROM shrink_records WHERE closed_at IS NULL AND serial IN ({marks})"
        " ORDER BY opened_at, id", serials).fetchall()
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
