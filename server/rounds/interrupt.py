"""#2528 (שלב 1) — מכונה שכבתה או אותחלה באמצע סבב: השרת שואל, ואינו כותב בלי תשובה.

עד כאן משכפל שאותחל באמצע הזרם וחזר קיבל גל נוסף מעצמו (#1728, #1790). ניתוק חשמל
יכול להיות טעות ויכול להיות עצירה מכוונת, ולכן (אפיון נדב 12/11) **אין המשך אוטומטי
בלי שאלה**.

**מתי שואלים** — רק על ראיה חיובית לאתחול: hello מצטרף של חבר שכבר בתוך הזרם, עם
מזהה אתחול **אחר** מזה שהיומן רשם כשהוא קיבל את הגל (`ledger`). כל השאר ממשיך כמו
היום, בלי שאלה: דיסק שנשלף וחזר, ניתוק רשת (אותו אתחול), כשל כתיבה רגיל, וסוכן שאינו
שולח מזהה אתחול ("לא ידוע" אינו "אותחל", עיקרון 5).

**תוקף** — רק מכונה שחזרה בתוך סף האובדן בפועל מהדיווח האחרון שלה. אחר כך אין
שאלה, והיומן שלה נמחק (`ledger.expire`, מ-`room._mark_lost`).

**בזמן שהשאלה פתוחה** הגל הרץ מסתיים כרגיל למכונות האחרות, ואז הסבב נשאר `active`
**בלי גל**: לא נפתח גל נוסף, השולח אינו רץ, וחריץ המולטיקאסט פנוי. שום דיסק לא נכתב.

**התשובות** (נוסח שלב 1; "המשך מאותה נקודה" מגיע רק בשלב 4):

- `rewrite` — "כתוב מחדש את הדיסקים שלא הושלמו": נפתח הגל הבא של הסבב, אותו גל חוזר
  של #1790. דיסק שהושלם סגור בסבב לפי ה-serial שלו ואינו נכתב שוב, בכל חריץ שהוא יושב בו.
- `close` — "סגור את הסבב": הסבב נסגר, ושום דבר נוסף לא נכתב.
- בלי תשובה כמה דקות שבהגדרה, מהמאוחר מבין רגע השאלה לסוף הגל — הסבב נכשל בשמו, ולא נכתב דבר. אין שאלה שמחכה בלי
  הגבלה (נדב 12/11); התקרה היא הגדרה בקונסולה (`interrupt_ask_minutes`, ברירת מחדל 35 דקות).

**רק סבב מהספרייה** (מהשרת למשכפל). הפצה ישירה ממחשב הבנייה ושיכפול מקומי אינם כאן:
שם המקור הוא מכונה שצריכה לשדר או לקרוא שוב, וזה שלב נפרד. בסבב כזה אין יומן, ולכן
אין שאלה — הוא מתנהג בדיוק כמו קודם.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from server.agent import timing as agent_timing
from server.brain import resume as brain_resume
from server.db import _settle, _write_lock, get_setting, journal, now_iso, SqlConnection, update_one, writing
from server.rounds import ledger

log = logging.getLogger("imagesrv.interrupt")

REWRITE = "rewrite"
CLOSE = "close"
ANSWERS = (REWRITE, CLOSE)

#: כמה דקות שאלה פתוחה מחכה לתשובה — הגדרה בקונסולה (הכרעת נדב 12/11). ברירת המחדל היא
#: התקרה של שאר השאלות למפעיל (`prompt_hold.ASK_HOLD_MAX_SECONDS`, 35 דקות). התאום של
#: שורת `interrupt_ask_minutes` ב-`console.js` — `tests/test_interrupt_setting_2528.py` משווה.
SETTING = "interrupt_ask_minutes"
DEFAULT = 35
MINIMUM = 5
MAXIMUM = 240
LABEL_HE = "המתנה להכרעה על משכפל שכבה באמצע סבב"


def parse(raw: object) -> int | None:
    """מספר שלם של דקות בטווח — או ``None``. ``bool`` אינו מספר, ואפס מוביל נדחה
    (אותו כלל של `writefloor.parse`)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif (isinstance(raw, str) and raw.isascii() and raw.isdigit()
            and not raw.startswith("0") and len(raw) <= 3):
        value = int(raw)
    else:
        return None
    return value if MINIMUM <= value <= MAXIMUM else None


def ask_minutes(conn: SqlConnection) -> int:
    """ההגדרה, או ברירת המחדל. פגומה = ברירת המחדל עם שורת יומן — לעולם לא "בלי תקרה"."""
    raw = get_setting(conn, SETTING)
    if raw is None:
        return DEFAULT
    value = parse(raw)
    if value is None:
        log.warning("setting %s=%r is not %d-%d -- using %d", SETTING, raw, MINIMUM, MAXIMUM, DEFAULT)
        return DEFAULT
    return value

#: מה שכתוב על הכפתורים — מקור אחד לקונסולה ולכל מסך אחר שיציג את השאלה.
BUTTONS_HE = {REWRITE: "כתוב מחדש את הדיסקים שלא הושלמו", CLOSE: "סגור את הסבב"}

UNANSWERED_REASON = "{what}, ולא התקבלה הכרעה תוך {minutes} דקות — הסבב נסגר בלי לכתוב. {left}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _age(stamp: object, now: datetime) -> float | None:
    if not isinstance(stamp, str):
        return None
    try:
        return (now - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return None


def _waited(conn: SqlConnection, ask: sqlite3.Row, now: datetime) -> tuple[float | None, bool]:
    """כמה שניות השאלה כבר מחכה, והאם הגל שלה עוד רץ. השעון נספר **מהמאוחר** מבין רגע
    השאלה לבין סוף הגל שבו המכונה כבתה (הכרעת נדב 13/11, סקירה 1-ג): התשובה מיושמת רק
    כשהגל מסתיים, ולכן משכפל שכבה בדקה 2 של גל ארוך מקבל את מלוא הזמן **אחרי** סוף הגל.

    כל עוד הגל רץ — 0. גל שנגמר בלי חותמת סיום קריאה, או שורת גל שאיננה — מרגע השאלה:
    חותמת שלא נקראה אינה "עוד יש זמן". ``None`` = גם רגע השאלה לא נקרא (הקורא סוגר)."""
    raised = _age(ask["raised_at"], now)
    wave = conn.execute("SELECT state, closed_at FROM sessions WHERE id = ?",
                        (ask["wave_id"],)).fetchone()
    if wave is not None and wave["state"] in ("open", "running"):
        return (0.0 if raised is not None else None), True
    ended = _age(wave["closed_at"], now) if wave is not None else None
    if raised is None or ended is None:
        return raised, False
    return max(0.0, min(raised, ended)), False


def _library_round(conn: SqlConnection, wave_id: str) -> sqlite3.Row | None:
    """הסבב הפעיל שהגל הזה שייך לו — רק כשהמקור שלו הוא הספרייה."""
    row = conn.execute(
        "SELECT * FROM room_rounds WHERE wave_session_id = ? AND state = 'active'",
        (wave_id,)).fetchone()
    if row is None or (row["source_kind"] or "library") != "library":
        return None
    return row


def handed(conn: SqlConnection, wave_id: str, mac: str,
           ports: list[int] | None, disks: list | None) -> bool:
    """המשכפל מקבל את הגל הרץ — פותחים לו יומן (`hello.build_answer`). **לעולם אינו זורק**
    (סקירה 1-ז). ``False`` = היומן לא נכתב: הקורא אינו מוסר את הגל ב-hello הזה (בלי יומן
    אי אפשר יהיה לדעת שהמכונה אותחלה באמצע), והחבר נשאר `waiting` ומקבל אותו ב-hello הבא."""
    try:
        round_row = _library_round(conn, wave_id)
        if round_row is not None:
            ledger.opened(conn, round_row["id"], wave_id, mac, ports, disks)
        return True
    except Exception as exc:      # noqa: BLE001 -- the hello never fails on the ledger
        log.exception("write ledger for %s in %s was not opened", mac, wave_id)
        try:
            conn.rollback()
            journal(conn, "write_ledger_failed",
                    f'{mac} in {wave_id} — {" ".join(str(exc).split())[:160]}',
                    machine=mac, round_id=wave_id)
        except Exception:         # noqa: BLE001
            log.exception("write ledger failure for %s was not journaled", mac)
        return False


def other_boot(conn: SqlConnection, wave_id: str, mac: str) -> bool:
    """האם היומן של הגל הזה רשום על אתחול **אחר** מזה של ה-hello הנוכחי (סקירה 1-א).
    בלי יומן או בלי מזהה אתחול — ``False`` ("לא ידוע" אינו "אותחל"). בדיקה שנכשלה —
    ``True``: הגל אינו נמסר שוב על סמך בדיקה שלא רצה, ו-`checked` רושם את הכשל."""
    try:
        was, now_boot = ledger.wave_boot(conn, wave_id, mac), ledger.boot_of(conn, mac)
    except sqlite3.Error:
        log.exception("boot comparison for %s in %s failed", mac, wave_id)
        return True
    return was is not None and now_boot is not None and was != now_boot


def checked(conn: SqlConnection, session: sqlite3.Row, mac: str,
            member: sqlite3.Row) -> bool:
    """`on_rejoin` מתוך מסלול ה-hello — **לעולם אינו זורק** (סקירה 1-ז, כמו `room.pulse`,
    #177: hello שמחזיר 500 אומר למשכפל שהשרת מת). מחזיר האם הבדיקה **רצה**; כשלא,
    שורת `round_interrupt_check_failed` ביומן, והקורא אינו מסמן את החבר `lost`."""
    try:
        on_rejoin(conn, session, mac, member)
        return True
    except Exception as exc:      # noqa: BLE001 -- the hello never fails on the round's bookkeeping
        log.exception("interrupt check for %s in %s failed", mac, session["id"])
        try:
            conn.rollback()
            journal(conn, "round_interrupt_check_failed",
                    f'{mac} in {session["id"]} — {" ".join(str(exc).split())[:160]}',
                    machine=mac, round_id=session["id"])
        except Exception:         # noqa: BLE001 -- neither does the journal line
            log.exception("interrupt check failure for %s was not journaled", mac)
        return False


def on_rejoin(conn: SqlConnection, session: sqlite3.Row, mac: str,
              member: sqlite3.Row) -> bool:
    """hello מצטרף של חבר שכבר בתוך הזרם (`room.rejoined_mid_stream`). מחזיר האם
    זו מכונה ש**אותחלה** וחזרה בזמן — ואז נרשמת שאלה למפעיל.

    שלוש ראיות, כולן נדרשות: יומן של הגל הזה עם מזהה אתחול; מזהה אתחול אחר ב-hello
    הזה; והדיווח האחרון של החבר בתוך סף האובדן בפועל. חסרה אחת — ``False``, והמסלול
    של #1728 ממשיך כמו שהיה."""
    was, now_boot = ledger.wave_boot(conn, session["id"], mac), ledger.boot_of(conn, mac)
    if was is None or now_boot is None or was == now_boot:
        return False
    round_row = _library_round(conn, session["id"])
    if round_row is None:
        return False
    now = _utcnow()
    wave = conn.execute("SELECT started_at FROM sessions WHERE id = ?", (session["id"],)).fetchone()
    ages = [a for a in (_age(member["updated_at"], now),
                        _age(wave["started_at"] if wave is not None else None, now))
            if a is not None]
    if not ages or min(ages) > agent_timing.effective_lost_seconds(conn):
        # לא חזרה בזמן (או שאי אפשר למדוד): אין המשך, והמידע נמחק.
        ledger.expire(conn, session["id"], mac)
        return False
    left = ledger.incomplete(conn, round_row, mac)
    if not left:
        return False            # כל מה שקיבלה כבר דווח `done` — אין על מה לשאול
    _settle(conn)
    with _write_lock, writing(conn):
        raised = conn.execute(
            "INSERT OR IGNORE INTO round_interrupts (round_id, mac, wave_id, raised_at)"
            " VALUES (?, ?, ?, ?)", (round_row["id"], mac, session["id"], now_iso())).rowcount
    if raised:
        journal(conn, "round_interrupted",
                f'{mac} in {round_row["id"]} wave={session["id"]} incomplete={len(left)}',
                machine=mac, round_id=round_row["id"])
        _b42_ask(conn, round_row, mac, len(left))
    return True


def _b42_ask(conn: SqlConnection, round_row: sqlite3.Row, mac: str, incomplete: int) -> None:
    """B42 (צל): בפס ייצור המוח היה עונה לבד ("כתוב מחדש"). השאלה למפעיל נשארת. לעולם אינו זורק."""
    from server.brain import line as brain_line      # noqa: PLC0415
    from server.rounds import prodline                # noqa: PLC0415
    try:
        if prodline.line_of(conn, round_row["id"]) is not None:
            brain_line.on_ask(conn, round_id=round_row["id"], mac=mac, name=_name(conn, mac), incomplete=incomplete)
    except Exception:                                 # noqa: BLE001 -- השאלה נרשמה; המוח אינו מפיל אותה
        log.exception("brain: B42 on %s failed", round_row["id"])


def asks(conn: SqlConnection, round_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM round_interrupts WHERE round_id = ? ORDER BY raised_at, mac",
        (round_id,)).fetchall()


def holds(conn: SqlConnection, round_id: str) -> bool:
    """האם יש על הסבב שאלה (פתוחה או שנענתה וטרם יושמה) — הגל שנגמר אינו מחליט לבד."""
    return bool(asks(conn, round_id))


def _clear(conn: SqlConnection, round_id: str) -> None:
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM round_interrupts WHERE round_id = ?", (round_id,))
        conn.execute("DELETE FROM write_ledger WHERE round_id = ?", (round_id,))


def _name(conn: SqlConnection, mac: str) -> str:
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?", (mac,)).fetchone()
    return row["suffix"] if row is not None and row["suffix"] else mac


def _what_he(who: list[dict]) -> str:
    """"המשכפל X כבה או אותחל באמצע הסבב" — ביחיד או ברבים."""
    names = ", ".join(m["name"] for m in who)
    return (f"המשכפל {names} כבה או אותחל באמצע הסבב" if len(who) == 1
            else f"המשכפלים {names} כבו או אותחלו באמצע הסבב")


def _left_he(count: int) -> str:
    return "דיסק אחד לא הושלם." if count == 1 else f"{count} דיסקים לא הושלמו."


def machines(conn: SqlConnection, round_row: sqlite3.Row,
             rows: list[sqlite3.Row] | None = None) -> list[dict]:
    """לכל מכונה שעליה שואלים: השם שלה, והדיסקים שלא הושלמו (לפי הזהות)."""
    return [{"mac": a["mac"], "name": _name(conn, a["mac"]),
             "disks": ledger.incomplete(conn, round_row, a["mac"])}
            for a in (asks(conn, round_row["id"]) if rows is None else rows)]


def settle(conn: SqlConnection, store, round_row: sqlite3.Row, total: int) -> bool:
    """הסבב פעיל ובלי גל (`room._resume`). מחזיר ``True`` כשההכרעה כאן — ממתינים
    לתשובה, או שהסבב נסגר, או שהגל הבא נפתח — והקורא אינו ממשיך; ``False`` כשאין שאלה."""
    rows = asks(conn, round_row["id"])
    if not rows:
        return False
    total = _credit_moved(conn, round_row, rows, total)
    now = _utcnow()
    open_ = [a for a in rows if a["answer"] is None]
    if open_:
        waited = [_waited(conn, a, now)[0] for a in open_]
        minutes = ask_minutes(conn)
        # חותמת שלא נקראת אינה "עוד יש זמן": שאלה אינה מחכה בלי הגבלה.
        if all(w is not None and w <= minutes * 60 for w in waited):
            return True                                   # ממתינים למפעיל; שום דבר לא נכתב
        who = machines(conn, round_row, rows)
        reason = UNANSWERED_REASON.format(
            what=_what_he(who), minutes=minutes,
            left=_left_he(sum(len(m["disks"]) for m in who)))
        if _end(conn, round_row, "failed", reason):
            journal(conn, "round_interrupt_unanswered",
                    f'{round_row["id"]} {reason} written={total}/{round_row["target_drives"]}',
                    round_id=round_row["id"])
        return True
    if any(a["answer"] == CLOSE for a in rows):
        by = next(a for a in rows if a["answer"] == CLOSE)
        if _end(conn, round_row, "closed", None):
            journal(conn, "room_done",
                    f'{round_row["id"]} written={total}/{round_row["target_drives"]}'
                    f" interrupted={len(rows)}", by["answered_by"] or "",
                    round_id=round_row["id"])
        return True
    # כולן `rewrite`: הגל הבא של הסבב — רק מה שלא נסגר בו נכתב (#1790).
    from server.rounds import room                    # noqa: PLC0415 -- room imports this module
    _follow_moved(conn, round_row, rows)
    brain_resume.snapshot(conn, round_row, [a["mac"] for a in rows])   # #2528 שלב 4: ה-ledger, לפני שהגל החדש מוחק אותו
    _clear(conn, round_row["id"])
    fresh = conn.execute("SELECT * FROM room_rounds WHERE id = ? AND state = 'active'",
                         (round_row["id"],)).fetchone()
    if fresh is None or room._remaining(fresh) <= 0:
        return False                                      # אין מה לכתוב: הסבב נסגר כרגיל
    room._attach_wave(conn, store, fresh, total)
    return True


def _follow_moved(conn: SqlConnection, round_row: sqlite3.Row,
                  rows: list[sqlite3.Row]) -> None:
    """"כתוב מחדש" כותב את הדיסק שלא הושלם **איפה שהוא יושב עכשיו** (הכרעת נדב 12/11: אותו
    דיסק לפי המספר הסידורי, לא לפי המגירה). דיסק שהוחזר לחריץ שלא נבחר לסבב: הבחירה של
    המכונה הולכת אחריו — החריץ שלו עכשיו נכנס, והחריץ שבו נכתב יוצא (אלא אם דיסק אחר שלא
    הושלם יושב בו עכשיו). כך מספר החריצים שנבחרו אינו גדל, ודיסק זר שהוכנס לחריץ הישן אינו
    נכתב. הסוכן עדיין משווה serial וגודל לפני הכתיבה (`session.target_disks`, #2448).
    סבב ישן בלי בחירה (כל הדיסקים) — אין מה לעדכן. התביעה היא ה-`target_slots_json` שנקרא."""
    raw = conn.execute("SELECT target_slots_json FROM room_rounds WHERE id = ? AND state = 'active'",
                       (round_row["id"],)).fetchone()
    if raw is None or raw["target_slots_json"] is None:
        return
    slots = json.loads(raw["target_slots_json"])
    moved: list[tuple[str, int, int]] = []
    for a in rows:
        item = next((x for x in slots if x.get("mac") == a["mac"]), None)
        if item is None:
            continue
        left = ledger.incomplete(conn, round_row, a["mac"])
        now = {d["port"] for d in left if d["port"] is not None}
        for d in left:
            if d["port"] is None or d["port"] in item["ports"]:
                continue
            item["ports"] = sorted(set(item["ports"]) | {d["port"]})
            if d["was_port"] in item["ports"] and d["was_port"] not in now:
                item["ports"] = [p for p in item["ports"] if p != d["was_port"]]
            moved.append((a["mac"], d["was_port"], d["port"]))
    if not moved:
        return
    _settle(conn)
    with _write_lock, writing(conn):
        claimed = update_one(
            conn, "UPDATE room_rounds SET target_slots_json = ? WHERE id = ? AND state = 'active'"
            " AND target_slots_json = ?",
            (json.dumps(slots, separators=(",", ":")), round_row["id"], raw["target_slots_json"]))
    if claimed:
        for mac, was, port in moved:
            journal(conn, "round_disk_followed",
                    f'{round_row["id"]} {_name(conn, mac)} port={was}->{port}',
                    machine=mac, round_id=round_row["id"])


def _credit_moved(conn: SqlConnection, round_row: sqlite3.Row,
                  rows: list[sqlite3.Row], total: int) -> int:
    """דיסק שהושלם לפני הכיבוי ויושב עכשיו במגירה אחרת נספר בסבב לפי הזהות שלו
    (`ledger.completed_here`) — אחרת "כתוב מחדש" היה כותב אותו שוב. מחזיר את הספירה
    אחרי התוספת. התביעה היא ה-`written_serials` שנקרא, וחוזר על עצמו בבטחה."""
    row = conn.execute("SELECT written_drives, written_serials FROM room_rounds"
                       " WHERE id = ? AND state = 'active'", (round_row["id"],)).fetchone()
    if row is None:
        return total
    written = set(json.loads(row["written_serials"]))
    moved = {(a["mac"], serial) for a in rows
             for serial in ledger.completed_here(conn, round_row, a["mac"])
             if serial not in written}
    if not moved:
        return total
    serials = {serial for _mac, serial in moved}
    _settle(conn)
    with _write_lock, writing(conn):
        counted = update_one(
            conn,
            "UPDATE room_rounds SET written_drives = written_drives + ?, written_serials = ?"
            " WHERE id = ? AND state = 'active' AND written_serials = ?",
            (len(serials), json.dumps(sorted(written | serials)), round_row["id"],
             row["written_serials"]))
    if not counted:
        return total
    for mac, serial in sorted(moved):
        journal(conn, "round_disk_counted_by_identity",
                f'{round_row["id"]} {_name(conn, mac)} serial={serial}',
                machine=mac, round_id=round_row["id"])
    return row["written_drives"] + len(serials)


def _end(conn: SqlConnection, round_row: sqlite3.Row, state: str,
         reason: str | None) -> bool:
    """סוגר את הסבב שעומד על שאלה. התביעה היא ה-UPDATE (#177): רק מי שסגר כותב ליומן."""
    _settle(conn)
    with _write_lock, writing(conn):
        ended = update_one(
            conn,
            "UPDATE room_rounds SET state = ?, failed_reason = ?, closed_at = ?"
            " WHERE id = ? AND state = 'active' AND wave_session_id IS NULL",
            (state, reason, now_iso(), round_row["id"]))
    if ended:
        _clear(conn, round_row["id"])
    return ended


def answer(conn: SqlConnection, round_row: sqlite3.Row, choice: str,
           user: str, origin: str, macs: object) -> int:
    """התשובה של המפעיל, על השאלות הפתוחות של המכונות ש**המסך הציג לו** (`macs`, סקירה
    1-ב): שאלה של משכפל שנרשמה אחרי שהוא ראה את המסך נשארת פתוחה, והסבב ממשיך לחכות
    לה. מחזיר על כמה מכונות התשובה חלה; 0 = אין להן שאלה פתוחה (מישהו אחר כבר ענה).
    היישום עצמו ב-`settle`, בדופק הבא."""
    if choice not in ANSWERS:
        raise ValueError("תשובה לא מוכרת")
    if (not isinstance(macs, list) or not macs or len(macs) > 64
            or not all(isinstance(m, str) and m for m in macs)):
        raise ValueError("חסרה רשימת המכונות שהשאלה הוצגה עליהן")
    marks = ",".join("?" * len(macs))
    _settle(conn)
    with _write_lock, writing(conn):
        count = conn.execute(
            "UPDATE round_interrupts SET answer = ?, answered_by = ?, answered_from = ?,"
            f" answered_at = ? WHERE round_id = ? AND answer IS NULL AND mac IN ({marks})",
            (choice, user, origin, now_iso(), round_row["id"], *macs)).rowcount
    if count:
        journal(conn, "round_interrupt_answer",
                f'{round_row["id"]} {choice} machines={count} ({origin})', user,
                round_id=round_row["id"])
        from server.brain import line as brain_line  # noqa: PLC0415 -- B42 (צל): מה שהמפעיל ענה בפועל
        brain_line.on_answer(conn, round_id=round_row["id"], choice=choice, origin=origin)
    return count


def for_cloner(conn: SqlConnection, mac: str) -> dict | None:
    """השדה `interrupt` בתשובת ה-hello של משכפל: השאלה הפתוחה ש**עליו** — מזהה הסבב והדיסקים
    שלא הושלמו (החריץ עכשיו, והחריץ שבו נכתבו). ``None`` כשאין שאלה פתוחה עליו. קריאה בלבד,
    ו**לעולם אינו זורק** (מסלול ה-hello, #177): שאלה שלא נקראה פשוט אינה מוצגת במסך המשכפל —
    היא נשארת בקונסולה, והשרת אינו כותב בלעדיה."""
    try:
        # #3898: כמה סבבים פעילים -- הסבב שיש עליו שאלה פתוחה על המשכפל הזה (משכפל בסבב אחד בלבד).
        round_row = conn.execute(
            "SELECT r.* FROM room_rounds r JOIN round_interrupts i ON i.round_id = r.id"
            " WHERE r.state = 'active' AND i.mac = ? AND i.answer IS NULL"
            " ORDER BY r.created_at, r.rowid LIMIT 1", (mac,)).fetchone()
        if round_row is None:
            return None
        disks = [{"port": d["port"], "was_port": d["was_port"]}
                 for d in ledger.incomplete(conn, round_row, mac) if d["was_port"] is not None]
        return {"round_id": round_row["id"], "disks": disks} if disks else None
    except Exception:             # noqa: BLE001 -- the hello never fails on a notice
        log.exception("the open question about %s could not be read", mac)
        return None


def view(conn: SqlConnection, round_row: sqlite3.Row) -> dict | None:
    """מה שהמסך מציג (`/room.round.interrupt`), או ``None`` כשאין שאלה. קריאה בלבד."""
    rows = asks(conn, round_row["id"]) if round_row["state"] == "active" else []
    if not rows:
        return None
    who = machines(conn, round_row, rows)
    left = sum(len(m["disks"]) for m in who)
    waiting = [a for a in rows if a["answer"] is None]
    clocks = [_waited(conn, a, _utcnow()) for a in waiting]
    waited = [w for w, _running in clocks]
    known = [w for w in waited if w is not None]
    return {
        "machines": who,
        "disks": left,
        "answer": None if waiting else (CLOSE if any(a["answer"] == CLOSE for a in rows)
                                        else REWRITE),
        "text_he": f"{_what_he(who)}. {_left_he(left)}",
        "buttons_he": dict(BUTTONS_HE),
        # הגל שבו המכונה כבתה עוד רץ למכונות אחרות: השעון של השאלה מתחיל כשהוא מסתיים.
        "wave_running": any(running for _w, running in clocks),
        # כמה שניות נשארו עד שהסבב נסגר בלי תשובה (מסוף הגל, אם הוא מאוחר מהשאלה);
        # None = כבר נענתה, או שלא ניתן למדוד.
        "expires_in_s": (max(0, int(ask_minutes(conn) * 60 - max(known)))
                         if waited and len(known) == len(waited) else None),
    }
