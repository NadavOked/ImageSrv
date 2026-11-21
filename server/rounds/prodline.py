"""#3181 (נדב 14/11): פס ייצור -- סבב שמבקשים בו יותר דיסקים ממספר המגירות במשכפלים.

**אין מתג ואין חלון נפרד.** פותחים סבב רגיל בחלון של #2786, והסבב עובר לפס ייצור לבד כש-
``target_drives`` גדול ממספר המגירות של המשכפלים שנבחרו (`is_line`). 9 מגירות ו-9 דיסקים = סבב רגיל;
9 מגירות ו-10 דיסקים = פס ייצור.

**רק בפס ייצור** (שאר ההתנהגות -- שליפה והחזרה, השלמה, המוח, קריאה חוזרת -- משותפת לכל סבב):

- ``נכתבו X מתוך Y`` -- `counter`. X = מה שנספר בסבב עד הגל הקודם (`written_drives`) ועוד כל דיסק
  שהגיע כאן ל-done / removable / removed ועוד לא נספר.
- ``אפשר להוציא`` -- רק אחרי שהסוכן דיווח ``detached: true`` (ניתוק בטוח). done לבדו אינו "אפשר להוציא".
- ``הכנס דיסק`` -- מגירה שהדיסק שנכתב בה הוצא (``removed: true``) שומרת לעצמה את השם הבא בתור
  (``reserved``), כל עוד יש שמות. מה שהמפעיל רואה על המגירה הוא מה שייכתב לדיסק שייכנס אליה (#2786).
- ``דיסק חדש באמצע הסבב`` -- serial שהשרת לא הכיר בסבב, בחריץ של משכפל שבסבב, מקבל את השם השמור
  למגירה הזו, ואם אין -- את הראשון שפנוי בתור. ודיסק שדווח ``failed``/``skipped``/``cancelled`` לעולם אינו
  לוקח שם.
- ``דיסק שנכשל שומר את השם שלו`` (B22, נדב 15/11: "מה שאתה מעדיף"; זאוס: כמו במוקאפ המאושר של #2786, "דיסק
  שנכשל שומר את המספר שלו ... כדי שהשם יתאים למדבקה"). השם נקשר לדיסק **ברגע שהוא נכנס**, ונכתב ל-Windows
  בסוף. השם אינו חוזר לתור: אותו דיסק שחוזר לכתיבה ממשיך עם אותו שם, ודיסק אחר לוקח את הבא בתור. אין שם
  פנוי, ודיסק שנכשל השאיר מקום ביעד -- הדיסק החדש נכתב **בלי שם** (`_extra`), עם שורת ``prodline_unnamed``
  כתומה, ולא נדחה: אחרת סבב שנכשל בו דיסק לא היה מגיע ליעד לעולם.

**השמות** (`validate_names`): בפס ייצור שם לכל דיסק שביקשו -- ``names`` לדיסקים שסומנו (כמו #2786),
ו-``queue`` לשאר, לפי הסדר. ``len(names) + len(queue) == target_drives``. בלי שמות (``null``) הסבב
עדיין סופר ומנהל את המגירות, והשורות בלי שם.

**משכפל שממתין לדיסק** (`line_wait`): בפס ייצור משכפל שכל הדיסקים שלו סיימו אינו בסוף הסבב כל עוד נשארו
שמות. הסוכן נשאר ``writing`` (לא מצב סופי, ולכן הגל והסבב אינם נסגרים; מדווח כל 2 שניות, ולכן אינו ``lost``)
ושואל בדיווח: ``line_wait: {"s": N}``. התשובה שלו היא ``queue_left`` ו-``insert`` ב-`reply`. יש לו למה לחכות =
הקונסולה מציגה "ממתין לדיסק" עם הזמן שעבר (`view`, ``waiting``). **השרת אינו סוגר לבד**: שיכפול נעשה בנוכחות
אדם, והסבב נסגר כשהשמות נגמרו או כשהמפעיל סוגר אותו מהקונסולה.

הטבלאות: ``prodline_rounds`` ו-``prodline_disks`` (`server/db.py`). המודול הזה הוא הכותב והקורא היחיד.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from server.core import hostname_rule
from server.db import _write_lock, journal, now_iso, writing
from server.disks import drawer_slots, model_slots
from server.rounds import room_names
from server.rounds.room_state import _disks, _written

log = logging.getLogger("imagesrv.prodline")

QUEUED, RESERVED, WRITING, DONE, REMOVABLE, REMOVED, FAILED = (
    "queued", "reserved", "writing", "done", "removable", "removed", "failed")
#: מצבים שבהם הדיסק נכתב ונספר ב"נכתבו X מתוך Y".
WRITTEN = (DONE, REMOVABLE, REMOVED)
#: מצבים שבהם השם פנוי לדיסק הבא: רק שם שעוד לא ניתן. דיסק שנכשל שומר את שלו (B22).
FREE = (QUEUED,)
#: מצב יעד בדיווח שלעולם אינו לוקח שם: הדיסק לא ייכתב בסבב הזה.
NO_NAME_STATES = ("failed", "skipped", "cancelled")

#: (סבב, serial) שכבר נרשמו ביומן כ"מעבר למה שביקשו" -- בזיכרון בלבד; אחרי אתחול השרת נרשם שוב פעם אחת.
_TOLD_FULL: set[tuple[str, str]] = set()

#: (סבב, mac) -> (כמה שניות הסוכן אמר שהוא ממתין לדיסק, השעון של השרת כשאמר). בזיכרון בלבד, במכוון: כל דיווח
#: נושא את העובדה כולה, ושרת שאותחל יודע אותה שוב מהדיווח הבא (2 שניות).
_WAITING: dict[tuple[str, str], tuple[int, float]] = {}
#: דיווח ישן מזה אינו "ממתין" אלא "לא ידוע" (עיקרון 5): הסוכן מדווח כל 2 שניות, ומשכפל ששתק אינו מוצג כממתין.
WAIT_FRESH_S = 30

QUEUE_NOT_LINE_HE = ("רשימת שמות בתור ניתנת רק לפס ייצור: מספר הדיסקים בסך הכול אינו גדול ממספר "
                     "המגירות במשכפלים שנבחרו")


# --- המעבר האוטומטי -----------------------------------------------------------------------------------------


def machine_drawers(conn: sqlite3.Connection, mac: str) -> int:
    """כמה מגירות SATA יש למשכפל -- אותו מיפוי שפתיחת הסבב מאמתת מולו (`_validate_target_slots`)."""
    row = conn.execute("SELECT drawer_count, drawer_slots FROM machines WHERE mac = ?", (mac,)).fetchone()
    if row is None:
        return 0
    model_map = model_slots.for_mac(conn, mac)
    return len(drawer_slots.sata_ports(drawer_slots.effective(
        row["drawer_count"], row["drawer_slots"], model_map["slots"] if model_map else None)))


def drawers(conn: sqlite3.Connection, selected: list[dict]) -> int:
    """מספר המגירות במשכפלים שנבחרו לסבב. משכפל שהמיפוי שלו קטן ממה שנבחר בו (לא אמור לקרות: הבחירה
    מאומתת מול המיפוי) נספר לפי מה שנבחר -- לעולם לא פחות מהמגירות שהסבב באמת כותב בהן."""
    return sum(max(machine_drawers(conn, item["mac"]), len(item["ports"])) for item in selected)


def is_line(target_drives: int, drawer_total: int) -> bool:
    """פס ייצור = יותר דיסקים ממגירות. שווה (9 ו-9) הוא סבב רגיל."""
    return target_drives > drawer_total


# --- השמות --------------------------------------------------------------------------------------------------


def validate_names(raw, selected: list[dict], target_drives: int, manifest: dict, line: bool) -> dict | None:
    """הבחירה בצורתה הקנונית. בסבב רגיל -- בדיוק `room_names.validate`. בפס ייצור -- גם ``queue``."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("בחירת שמות המחשב אינה תקינה")
    queue = raw.get("queue")
    if not line:
        if queue:
            raise ValueError(QUEUE_NOT_LINE_HE)
        return room_names.validate(raw, selected, target_drives, manifest)
    slots = sum(len(item["ports"]) for item in selected)
    if not isinstance(queue, list):
        raise ValueError(f"בפס ייצור צריך שם לכל דיסק שביקשו: {target_drives} שמות, "
                         f"ונשלחו {slots} בלבד")
    if slots + len(queue) != target_drives:
        raise ValueError(f"בפס ייצור צריך שם לכל דיסק שביקשו: {target_drives} שמות, "
                         f"ונשלחו {slots + len(queue)}")
    for name in queue:
        refused = hostname_rule.computer_name_refusal(name)
        if refused is not None:
            raise ValueError(refused)
    chosen = room_names.validate({k: v for k, v in raw.items() if k != "queue"}, selected, slots, manifest)
    seen: set[str] = set()
    for name in [item["name"] for item in chosen["names"]] + list(queue):
        if name.upper() in seen:
            raise ValueError(f"השם {name} מופיע פעמיים בשמות הסבב")
        seen.add(name.upper())
    chosen["queue"] = list(queue)
    return chosen


# --- הפתיחה -------------------------------------------------------------------------------------------------


def open_line(conn: sqlite3.Connection, round_id: str, selected: list[dict], target_drives: int,
              drawer_total: int, names: dict | None) -> None:
    """שורה לכל דיסק שהסבב ביקש. בלי commit: חלק מהטרנזאקציה של פתיחת הסבב (`room.open_round`)."""
    by_slot = {(item["mac"], item["port"]): item["name"] for item in (names or {}).get("names", [])}
    queue = list((names or {}).get("queue") or [])
    stamp = now_iso()
    conn.execute("INSERT INTO prodline_rounds (round_id, drawers, target, created_at) VALUES (?, ?, ?, ?)",
                 (round_id, drawer_total, target_drives, stamp))
    seq = 0
    for item in selected:
        shown = {d.get("port"): d.get("serial") for d in item.get("shown") or [] if isinstance(d, dict)}
        for port in item["ports"]:
            conn.execute("INSERT INTO prodline_disks (round_id, seq, name, mac, port, serial, state, changed_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (round_id, seq, by_slot.get((item["mac"], port)), item["mac"], port,
                          shown.get(port), WRITING, stamp))
            seq += 1
    for i in range(target_drives - seq):
        conn.execute("INSERT INTO prodline_disks (round_id, seq, name, mac, port, serial, state, changed_at)"
                     " VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?)",
                     (round_id, seq + i, queue[i] if i < len(queue) else None, QUEUED, stamp))


def line_of(conn: sqlite3.Connection, round_id: str | None) -> sqlite3.Row | None:
    if not round_id:
        return None
    return conn.execute("SELECT * FROM prodline_rounds WHERE round_id = ?", (round_id,)).fetchone()


def _rows(conn: sqlite3.Connection, round_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM prodline_disks WHERE round_id = ? ORDER BY seq", (round_id,)).fetchall()


# --- הדיווח -------------------------------------------------------------------------------------------------


def _port(raw) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None


def identities(conn: sqlite3.Connection, mac: str, targets) -> list[tuple[dict, str, int]]:
    """(יעד, serial, port) לכל יעד בדיווח שאפשר לזהות. בלי serial או בלי חריץ אין למה לקשור שם או ספירה,
    והיעד מדולג (עיקרון 5: "לא ידוע" אינו "דיסק חדש"). מה שחסר ביעד נלקח מהמלאי של ה-hello לפי ``dev``."""
    inventory = {d.get("dev"): d for d in _disks(conn, mac) if isinstance(d, dict)}
    out = []
    for t in targets if isinstance(targets, list) else []:
        if not isinstance(t, dict):
            continue
        known = inventory.get(t.get("dev")) or {}
        serial = t.get("serial") if isinstance(t.get("serial"), str) and t.get("serial") else known.get("serial")
        port = _port(t.get("port"))
        port = port if port is not None else _port(known.get("port"))
        if isinstance(serial, str) and serial and port is not None:
            out.append((t, serial, port))
    return out


def _next_state(row: sqlite3.Row, t: dict) -> str:
    state, now = t.get("state"), row["state"]
    if state == "failed":
        return FAILED
    if t.get("removed") is True and now in WRITTEN:
        return REMOVED
    if state == "done":
        if now == REMOVED:
            return REMOVED
        return REMOVABLE if t.get("detached") is True else (REMOVABLE if now == REMOVABLE else DONE)
    if state in ("writing", "verifying") and now in (WRITING, FAILED, RESERVED):
        return WRITING
    return now


def _free(rows, mac: str | None = None, port: int | None = None):
    """השם שהדיסק הבא יקבל: השמור למגירה הזו, ואם אין -- הראשון הפנוי בתור."""
    if mac is not None:
        for row in rows:
            if row["state"] == RESERVED and row["mac"] == mac and row["port"] == port:
                return row
    for row in rows:
        if row["state"] in FREE:
            return row
    return None


def note_report(conn: sqlite3.Connection, round_row: sqlite3.Row, mac: str, targets, wait=None) -> dict | None:
    """דיווח התקדמות של משכפל בסבב פס ייצור -> מה שהוא צריך לדעת (`reply`). ``None`` = הסבב אינו פס ייצור.
    ``wait`` = ``line_wait`` של הדיווח: המשכפל אומר שאין לו מה לכתוב, ושואל אם נשארו שמות (`_note_wait`).

    כל חריגה נרשמת ביומן השרת והתשובה היא ``None``: הדיווח עצמו לעולם אינו נכשל בגלל פס הייצור."""
    try:
        if line_of(conn, round_row["id"]) is None:
            return None
        found = identities(conn, mac, targets)
        refused: list[str] = []
        events: list[str] = []
        with _write_lock, writing(conn):
            rows = _rows(conn, round_row["id"])
            for t, serial, port in found:
                row = next((r for r in rows if r["serial"] == serial and r["state"] != QUEUED), None)
                if row is None:
                    if t.get("state") in NO_NAME_STATES:
                        continue
                    # דיסק של הגל הראשון שהקונסולה לא ידעה את ה-serial שלו: השורה של החריץ שלו, לא שם מהתור
                    row = next((r for r in rows if r["mac"] == mac and r["port"] == port and r["serial"] is None
                                and r["state"] == WRITING), None) or _free(rows, mac, port)
                    row = row or _extra(conn, round_row["id"], rows, events)
                    if row is None:
                        refused.append(serial)
                        continue
                    conn.execute("UPDATE prodline_disks SET mac = ?, port = ?, serial = ?, state = ?, changed_at = ?"
                                 " WHERE round_id = ? AND seq = ?",
                                 (mac, port, serial, WRITING, now_iso(), round_row["id"], row["seq"]))
                    events.append(f"join seq={row['seq']} name={row['name']} {mac}:{port} serial={serial}")
                    rows = _rows(conn, round_row["id"])
                    row = next(r for r in rows if r["seq"] == row["seq"])
                new = _next_state(row, t)
                if new != row["state"]:
                    conn.execute("UPDATE prodline_disks SET state = ?, changed_at = ? WHERE round_id = ? AND seq = ?",
                                 (new, now_iso(), round_row["id"], row["seq"]))
                    events.append(f"{new} seq={row['seq']} name={row['name']} {mac}:{port} serial={serial}")
                    rows = _rows(conn, round_row["id"])
            _reserve(conn, round_row["id"], events)
        _note_wait(conn, round_row["id"], mac, wait, events)
        for event in events:
            if event.startswith("unnamed "):
                journal(conn, "prodline_unnamed", f"{round_row['id']} {event}: אין שם פנוי -- דיסק שנכשל שומר את"
                        " שמו, ולכן הדיסק שנכנס במקומו נכתב בלי שם (השם שבאימג' נשאר)", round_id=round_row["id"])
                continue
            journal(conn, "prodline", f"{round_row['id']} {event}", round_id=round_row["id"])
        fresh = [s for s in refused if (round_row["id"], s) not in _TOLD_FULL]
        if fresh:                               # פעם אחת לכל דיסק: הסוכן מדווח כל 2 שניות
            _TOLD_FULL.update((round_row["id"], s) for s in fresh)
            journal(conn, "prodline_full", f"{round_row['id']} {mac} {','.join(fresh)}: הסבב כבר קיבל את כל"
                    " הדיסקים שביקשו -- הדיסק לא נכתב", round_id=round_row["id"])
        return reply(conn, round_row, mac, refused)
    except Exception:                           # noqa: BLE001 — פס הייצור אינו מפיל דיווח
        log.exception("prodline: the report of %s was not applied", mac)
        return None


def _extra(conn: sqlite3.Connection, round_id: str, rows, events: list[str]) -> sqlite3.Row | None:
    """B22: אין שם פנוי, אבל דיסק שנכשל (ושומר את שמו) השאיר מקום ביעד -- שורה חדשה בלי שם לדיסק שנכנס.
    כל עוד השורות שלא נכשלו מכסות את היעד, אין מקום, והדיסק נדחה כמו קודם (`prodline_full`)."""
    line = line_of(conn, round_id)
    if line is None or sum(1 for r in rows if r["state"] != FAILED) >= line["target"]:
        return None
    seq = max((r["seq"] for r in rows), default=-1) + 1
    conn.execute("INSERT INTO prodline_disks (round_id, seq, name, mac, port, serial, state, changed_at)"
                 " VALUES (?, ?, NULL, NULL, NULL, NULL, ?, ?)", (round_id, seq, QUEUED, now_iso()))
    events.append(f"unnamed seq={seq}")
    return conn.execute("SELECT * FROM prodline_disks WHERE round_id = ? AND seq = ?", (round_id, seq)).fetchone()


def _reserve(conn: sqlite3.Connection, round_id: str, events: list[str]) -> None:
    """מגירה שהדיסק שנכתב בה הוצא, ואין בה דיסק אחר בסבב, שומרת לעצמה את השם הבא בתור."""
    rows = _rows(conn, round_id)
    busy = {(r["mac"], r["port"]) for r in rows if r["state"] in (RESERVED, WRITING, DONE, REMOVABLE)}
    for row in rows:
        slot = (row["mac"], row["port"])
        if row["state"] != REMOVED or slot in busy:
            continue
        free = _free(rows)
        if free is None:
            return
        conn.execute("UPDATE prodline_disks SET mac = ?, port = ?, serial = NULL, state = ?, changed_at = ?"
                     " WHERE round_id = ? AND seq = ?", (row["mac"], row["port"], RESERVED, now_iso(), round_id,
                                                         free["seq"]))
        events.append(f"reserved seq={free['seq']} name={free['name']} {row['mac']}:{row['port']}")
        busy.add(slot)
        rows = _rows(conn, round_id)


def _left_for(rows, mac: str) -> tuple[int, list[int]]:
    """(שמות שאף דיסק לא מחזיק, החריצים הריקים של המשכפל הזה ששמור להם שם) -- למה משכפל שסיים מחכה."""
    return (sum(1 for r in rows if r["state"] in FREE),
            sorted(r["port"] for r in rows if r["mac"] == mac and r["state"] == RESERVED))


def _note_wait(conn: sqlite3.Connection, round_id: str, mac: str, wait, events: list[str]) -> None:
    """``line_wait`` של הדיווח. המשכפל "ממתין לדיסק" רק כשהוא אמר זאת **ו**יש לו למה לחכות; כל דיווח אחר
    (בלי השדה, שדה שאינו ``{"s": שלם אי-שלילי}``, או שלא נשאר לו דבר) מוריד אותו. המעבר נרשם ביומן פעם אחת."""
    for key in [k for k in list(_WAITING) if k[0] != round_id]:
        _WAITING.pop(key, None)                 # סבב אחד פעיל בחדר: מה שנשאר מסבב קודם אינו של אף אחד
    secs = wait.get("s") if isinstance(wait, dict) else None
    queue_left, insert = _left_for(_rows(conn, round_id), mac)
    if isinstance(secs, bool) or not isinstance(secs, int) or secs < 0 or not (queue_left or insert):
        _WAITING.pop((round_id, mac), None)
        return
    if (round_id, mac) not in _WAITING:
        events.append(f"wait {mac} queue_left={queue_left} insert={','.join(map(str, insert)) or '-'}")
    _WAITING[(round_id, mac)] = (secs, time.monotonic())


def waiting(round_id: str) -> list[dict]:
    """המשכפלים של הסבב שממתינים לדיסק עכשיו, וכמה זמן: מה שהסוכן אמר ועוד הזמן שעבר מאז שאמר."""
    now = time.monotonic()
    return [{"mac": mac, "since_s": secs + int(now - at)}
            for (rid, mac), (secs, at) in sorted(list(_WAITING.items()))
            if rid == round_id and now - at <= WAIT_FRESH_S]


# --- מה שהסוכן והקונסולה רואים ------------------------------------------------------------------------------


def counter(round_row: sqlite3.Row, rows) -> tuple[int, int]:
    """(נכתבו, מתוך). מה שנספר עד הגל הקודם, ועוד דיסק שנכתב בגל הזה ועוד לא נספר -- פעם אחת לכל serial."""
    written = _written(round_row)
    extra = {r["serial"] for r in rows if r["state"] in WRITTEN and r["serial"] and r["serial"] not in written}
    return round_row["written_drives"] + len(extra), round_row["target_drives"]


def written_serials(conn: sqlite3.Connection, round_id: str) -> set[str]:
    """ה-serials שנכתבו בפס הייצור -- גם דיסק שכבר הוצא ואינו בדיווח האחרון של הגל (`room._tally`)."""
    if line_of(conn, round_id) is None:
        return set()
    return {r["serial"] for r in _rows(conn, round_id) if r["state"] in WRITTEN and r["serial"]}


def reply(conn: sqlite3.Connection, round_row: sqlite3.Row, mac: str, refused=()) -> dict | None:
    """``production_line`` בתשובה לסוכן (progress ו-hello). ``None`` = הסבב אינו פס ייצור."""
    if line_of(conn, round_row["id"]) is None:
        return None
    rows = _rows(conn, round_row["id"])
    written, target = counter(round_row, rows)
    free = _free(rows)
    return {"written": written, "target": target, "remaining": max(target - written, 0),
            "next_name": free["name"] if free is not None else None,
            # שמות שאף דיסק לא מחזיק (בתור, או של דיסק שנכשל): משכפל שסיים מחכה כל עוד זה או `insert` אינם ריקים
            "queue_left": _left_for(rows, mac)[0],
            "names": {r["serial"]: r["name"] for r in rows
                      if r["mac"] == mac and r["serial"] and r["name"] and r["state"] != FAILED},
            "insert": {str(r["port"]): r["name"] for r in rows if r["mac"] == mac and r["state"] == RESERVED},
            "removable": sorted(r["port"] for r in rows if r["mac"] == mac and r["state"] == REMOVABLE),
            "refused": sorted(refused)}


def view(conn: sqlite3.Connection, round_row: sqlite3.Row) -> dict | None:
    """``round.production_line`` ב-``GET /api/console/room``. ``None`` = הסבב אינו פס ייצור."""
    line = line_of(conn, round_row["id"])
    if line is None:
        return None
    rows = _rows(conn, round_row["id"])
    written, target = counter(round_row, rows)
    free = _free(rows)
    from server.brain import memory          # noqa: PLC0415 -- המוח קורא את החדר, לא להפך

    return {"drawers": line["drawers"], "target": target, "written": written,
            "remaining": max(target - written, 0),
            "next_name": free["name"] if free is not None else None,
            "queue_left": sum(1 for r in rows if r["state"] in FREE),
            # משכפלים שכל הדיסקים שלהם סיימו והם ממתינים לדיסק הבא: [{"mac", "since_s"}] (`line_wait` של הסוכן)
            "waiting": waiting(round_row["id"]),
            "disks": [{"seq": r["seq"], "name": r["name"], "mac": r["mac"], "port": r["port"],
                       "serial": r["serial"], "state": r["state"], "changed_at": r["changed_at"]} for r in rows],
            "decisions": memory.decisions(conn, round_id=round_row["id"], limit=12)}
