"""#2528 (שלב 1) — יומן הכתיבה, ברמת דיסק.

השרת רושם, לכל דיסק יעד שמשכפל קיבל לכתוב בגל של סבב חדר, **מי הדיסק** ו**באיזה
אתחול** המכונה כתבה אותו. מכאן השרת יודע שתי עובדות שעד היום הסיק בעקיפין:

- hello מצטרף של חבר שכבר בתוך הזרם, עם **מזהה אתחול אחר** מזה שנרשם, הוא מכונה
  שכבתה או אותחלה — לא סוכן שנתקע ולא ניתוק רשת (`interrupt.on_rejoin`).
- אילו דיסקים לא הושלמו, ואיפה הם יושבים **עכשיו** — לפי הזהות, לא לפי המגירה.

**הזהות** (הכרעת נדב 12/11): serial + גודל, ו-wwid כשהסוכן דיווח אותו בשני הצדדים.
החריץ אינו חלק ממנה: דיסק שהוחזר למגירה אחרת הוא אותו דיסק. חריץ שאין בו בדיוק דיסק
אחד, או דיסק בלי serial או בלי גודל — **אין לו שורה** (אין יומן = אין שאלה עליו).

**אין כאן חתיכות.** "מה נכתב ואומת" ברמת חתיכה הוא שלב 2; כאן דיסק הושלם או לא,
ו"הושלם" נקרא מהמקורות שכבר קיימים: `room_rounds.written_serials` והיעדים שהחבר דיווח
`done` (`session_members.targets_json`). היומן אינו מחזיק עותק שלישי שיכול לסטות.

**מחיקה** (הכרעת נדב 12/11): מכונה שלא חזרה בתוך `lost_wait_seconds` — השורות שלה
נמחקות (`expire`), ואין עליה שאלה. שורות של סבב שכבר אינו פעיל נמחקות בפתיחה הבאה.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from server.db import _settle, _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.ledger")

#: מה שהקרנל נותן ב-`/proc/sys/kernel/random/boot_id` — UUID באותיות קטנות.
_BOOT_ID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def clean_boot_id(value: object) -> str | None:
    """מזהה האתחול מגוף ה-hello, או ``None``. ערך שאינו בצורה של הקרנל אינו "אתחול
    אחר" — הוא "לא ידוע" (עיקרון 5): סוכן ישן, או גוף שמישהו אחר כתב."""
    return value if isinstance(value, str) and _BOOT_ID.fullmatch(value) else None


def note_boot(conn: sqlite3.Connection, mac: str, value: object) -> None:
    """רושם את מזהה האתחול של ה-hello הזה. נכתב רק כשהשתנה (כמו `note_monitor_on`),
    ורק למכונה שכבר נראתה. hello בלי השדה אינו מוחק את מה שנרשם."""
    boot_id = clean_boot_id(value)
    if boot_id is None:
        return
    # סקירה 1-ז: מסלול ה-hello — **לעולם אינו זורק** (#177). מזהה שלא נרשם נשאר הישן, ולכן
    # אתחול שבא אחריו ייראה "אותו אתחול" (בלי שאלה, כמו לפני #2528); השורה ביומן אומרת זאת.
    try:
        row = conn.execute("SELECT boot_id FROM net_devices WHERE mac = ?", (mac,)).fetchone()
        if row is None or row["boot_id"] == boot_id:
            return
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute("UPDATE net_devices SET boot_id = ? WHERE mac = ?", (boot_id, mac))
    except sqlite3.Error as exc:
        log.exception("boot id of %s was not recorded", mac)
        try:
            conn.rollback()
            journal(conn, "boot_id_not_recorded", f'{mac} — {" ".join(str(exc).split())[:160]}',
                    machine=mac)
        except Exception:         # noqa: BLE001 -- neither does the journal line
            log.exception("boot id failure of %s was not journaled", mac)


def boot_of(conn: sqlite3.Connection, mac: str) -> str | None:
    row = conn.execute("SELECT boot_id FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    return row["boot_id"] if row is not None else None


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def identities(ports: list[int] | None, disks: list | None) -> list[dict]:
    """הדיסקים שיושבים ב-`ports` לפי המלאי של ה-hello הזה — רק מי שיש לו זהות.

    אותו כלל כמו `room_state.target_disks` (#2448): חריץ שאין בו **בדיוק** דיסק אחד
    במלאי, או דיסק בלי serial או בלי גודל, אינו מזוהה — ואינו נכנס ליומן."""
    if not ports or not isinstance(disks, list):
        return []
    # סקירה 1-ד: שני דיסקים במכונה עם אותו serial ואותו גודל (#2448) אינם ניתנים להבחנה —
    # לאף אחד מהם אין שורה (המפתח הוא ה-serial, ושורה אחת לשניהם הייתה מטעה).
    seen: dict[tuple, int] = {}
    for d in disks:
        if isinstance(d, dict):
            key = (_text(d.get("serial")), _whole(d.get("size_bytes")))
            seen[key] = seen.get(key, 0) + 1
    out = []
    for port in ports:
        here = [d for d in disks if isinstance(d, dict) and d.get("port") == port]
        if len(here) != 1:
            continue
        serial, size = _text(here[0].get("serial")), _whole(here[0].get("size_bytes"))
        if serial is None or size is None or seen[(serial, size)] != 1:
            continue
        out.append({"port": port, "serial": serial, "size_bytes": size,
                    "wwid": _text(here[0].get("wwid")), "model": _text(here[0].get("model"))})
    return out


def opened(conn: sqlite3.Connection, round_id: str, wave_id: str, mac: str,
           ports: list[int] | None, disks: list | None) -> int:
    """המשכפל קיבל עכשיו את הגל הרץ: שורה לכל דיסק יעד מזוהה, עם מזהה האתחול שלו.

    בלי מזהה אתחול (סוכן ישן) אין שורות: בלי "מי כתב" אין מול מה להשוות אתחול.
    חוזר על עצמו בבטחה — אותו hello פעמיים כותב את אותן שורות. מחזיר כמה נרשמו."""
    boot_id = boot_of(conn, mac)
    found = identities(ports, disks)
    if boot_id is None or not found:
        return 0
    now = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        # שורות (ושאלות) של סבבים שכבר אינם פעילים, ושל גל קודם של המכונה הזו בסבב הזה.
        for table in ("write_ledger", "round_interrupts"):
            conn.execute(f"DELETE FROM {table} WHERE round_id NOT IN"
                         " (SELECT id FROM room_rounds WHERE state = 'active')")
        conn.execute("DELETE FROM write_ledger WHERE round_id = ? AND mac = ? AND wave_id != ?",
                     (round_id, mac, wave_id))
        for d in found:
            conn.execute(
                "INSERT OR REPLACE INTO write_ledger (round_id, wave_id, mac, serial,"
                " size_bytes, wwid, port, model, boot_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (round_id, wave_id, mac, d["serial"], d["size_bytes"], d["wwid"],
                 d["port"], d["model"], boot_id, now))
    return len(found)


def rows(conn: sqlite3.Connection, round_id: str, mac: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM write_ledger WHERE round_id = ? AND mac = ? ORDER BY port, serial",
        (round_id, mac)).fetchall()


def wave_boot(conn: sqlite3.Connection, wave_id: str, mac: str) -> str | None:
    """מזהה האתחול של הריצה שקיבלה את הגל הזה, או ``None`` כשאין יומן."""
    row = conn.execute(
        "SELECT boot_id FROM write_ledger WHERE wave_id = ? AND mac = ? LIMIT 1",
        (wave_id, mac)).fetchone()
    return row["boot_id"] if row is not None else None


def forget(conn: sqlite3.Connection, round_id: str, mac: str | None = None) -> int:
    """מוחק את יומן הסבב (או של מכונה אחת בו). מחזיר כמה שורות נמחקו."""
    sql, args = "DELETE FROM write_ledger WHERE round_id = ?", [round_id]
    if mac is not None:
        sql, args = sql + " AND mac = ?", [round_id, mac]
    _settle(conn)
    with _write_lock, writing(conn):
        return conn.execute(sql, args).rowcount


def expire(conn: sqlite3.Connection, wave_id: str, mac: str) -> None:
    """המכונה לא חזרה בתוך `lost_wait_seconds`: אין המשך, והמידע נמחק (נדב 12/11).
    שורת יומן אחת, רק כשהיה מה למחוק."""
    _settle(conn)
    with _write_lock, writing(conn):
        gone = conn.execute("DELETE FROM write_ledger WHERE wave_id = ? AND mac = ?",
                            (wave_id, mac)).rowcount
    if gone:
        journal(conn, "write_ledger_expired", f"{mac} in {wave_id} disks={gone}",
                machine=mac, round_id=wave_id)


def _done_serials(conn: sqlite3.Connection, round_row: sqlite3.Row, wave_id: str,
                  mac: str) -> set[str]:
    """מה שכבר הושלם: נספר בסבב, או שהחבר דיווח עליו `done` עם ה-serial שנקשר לו."""
    done = set(json.loads(round_row["written_serials"] or "[]"))
    member = conn.execute(
        "SELECT targets_json FROM session_members WHERE session_id = ? AND mac = ?",
        (wave_id, mac)).fetchone()
    try:
        targets = json.loads(member["targets_json"] or "[]") if member is not None else []
    except (TypeError, ValueError):
        targets = []
    for t in targets if isinstance(targets, list) else []:
        if isinstance(t, dict) and t.get("state") == "done" and _text(t.get("serial")):
            done.add(t["serial"])
    return done


def _same(row: sqlite3.Row, disk: dict) -> bool:
    """אותו דיסק: serial וגודל שווים, ו-wwid שווה כששני הצדדים יודעים אותו."""
    if disk.get("serial") != row["serial"] or disk.get("size_bytes") != row["size_bytes"]:
        return False
    wwid = _text(disk.get("wwid"))
    return row["wwid"] is None or wwid is None or wwid == row["wwid"]


def _inventory(conn: sqlite3.Connection, mac: str) -> list[dict]:
    """המלאי של ה-hello האחרון של המכונה. מלאי שלא נקרא הוא רשימה ריקה: שום דיסק "אינו כאן"."""
    seen = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    try:
        disks = json.loads(seen["disks_json"] or "[]") if seen is not None else []
    except (TypeError, ValueError):
        return []
    return [d for d in disks if isinstance(d, dict)] if isinstance(disks, list) else []


def _port_now(row: sqlite3.Row, disks: list[dict]) -> int | None:
    """החריץ שבו הדיסק של השורה יושב עכשיו — רק כשבדיוק דיסק אחד עונה לזהות שלו (#2448)."""
    here = [d for d in disks if _same(row, d)]
    port = here[0].get("port") if len(here) == 1 else None
    return port if isinstance(port, int) and not isinstance(port, bool) else None


def incomplete(conn: sqlite3.Connection, round_row: sqlite3.Row, mac: str) -> list[dict]:
    """הדיסקים של המכונה שהיומן אומר שהתחילו להיכתב ולא הושלמו, ואיפה כל אחד עכשיו.

    `port` = החריץ שבו הדיסק יושב לפי ה-hello **האחרון** (לפי הזהות, לא לפי החריץ שבו
    נכתב); ``None`` = הדיסק אינו במלאי עכשיו, או ששניים עונים לזהות שלו (#2448)."""
    mine = rows(conn, round_row["id"], mac)
    if not mine:
        return []
    done = _done_serials(conn, round_row, mine[0]["wave_id"], mac)
    disks = _inventory(conn, mac)
    return [{"serial": row["serial"], "size_bytes": row["size_bytes"], "model": row["model"],
             "was_port": row["port"], "port": _port_now(row, disks)}
            for row in mine if row["serial"] not in done]


def completed_here(conn: sqlite3.Connection, round_row: sqlite3.Row, mac: str) -> set[str]:
    """הדיסקים של המכונה שהושלמו בגל שנקטע ו**נמצאים בה עכשיו** — בכל חריץ.

    `room._tally` סופר יעד `done` רק כשבאותו שם התקן עדיין יושב אותו serial; דיסק שהושלם
    והוחזר למגירה אחרת נרשם אצלו `room_drawer_changed` ולא נספר, ולכן היה נכתב שוב. כאן
    הראיה היא הזהות: היומן רשם את הדיסק, החבר דיווח עליו `done` עם ה-serial שנקשר לו,
    ובדיוק דיסק אחד במלאי של עכשיו עונה ל-serial + גודל (+ wwid). חסר אחד מאלה — לא נספר."""
    mine = rows(conn, round_row["id"], mac)
    if not mine:
        return set()
    done = _done_serials(conn, round_row, mine[0]["wave_id"], mac)
    disks = _inventory(conn, mac)
    return {row["serial"] for row in mine
            if row["serial"] in done and _port_now(row, disks) is not None}
