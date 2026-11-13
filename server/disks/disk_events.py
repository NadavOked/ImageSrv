"""קליטת disk_event מהסוכן — בריאות SMART וההכרעה על דיסק יעד (#652).

הסוכן מכריע מקומית, ליד המכונה; השרת שומר את התמונה החיה לצפייה
בקונסולה (#380) ולריבוט-החלפה. **השרת אינו מכריע מכאן** — `verdict`
כולל `unchecked`, שאיננו כשל אלא "לא נבדק" (עיקרון 5), והחלטת הכתיבה
נשארת בסוכן.

מבנה השורה נדרס בכל אירוע: התמונה החיה של הבחירות, לא ארכיון. הארכיון
הוא היומן, שאינו נמחק.
"""

from __future__ import annotations

import logging
import sqlite3

from boot.grub_menu import normalize_mac as lenient_mac

from server.disks import disk_failures, slot_names, smart_history
from server.db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.disk_events")

# `failed_last` (#872): הסימון של #845 על הדיסק — נכשל בשיכפול הקודם, גובר
# על SMART. `warn` אינו נשלח עוד מהסוכן (הכרעת #872) ונשאר לסוכן ישן.
VERDICTS = ("ok", "warn", "fail", "unchecked", "failed_last")
DECISIONS = (None, "replace", "rescue", "skip")


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _was_red(conn: sqlite3.Connection, serial: str | None, session_id: str, mac: str,
             disk: str) -> bool:
    """האם המדידה האחרונה של הדיסק הזה כבר הייתה אדומה (#1751: התראה אחת לאירוע).

    עם סידורי — הדיסק עצמו, בכל סבב ובכל מכונה (הוא נודד בין מגירות). בלי סידורי
    אין זהות בין סבבים — רק השורה של (סבב, מכונה, דיסק), כלומר שליחה חוזרת.
    ``unchecked`` אינו מדידה (עיקרון 5): הוא לא "כבר לא אדום", ולכן מדלגים עליו."""
    if serial:
        row = conn.execute(
            "SELECT verdict FROM disk_events WHERE serial = ? AND verdict != 'unchecked'"
            " ORDER BY updated_at DESC, rowid DESC LIMIT 1", (serial,)).fetchone()
    else:
        row = conn.execute(
            "SELECT verdict FROM disk_events WHERE session_id = ? AND mac = ? AND disk = ?"
            " AND verdict != 'unchecked'", (session_id, mac, disk)).fetchone()
    return row is not None and row["verdict"] == "fail"


def ingest(conn: sqlite3.Connection, payload: dict) -> dict:
    """שומר disk_event אחד. מחזיר {"ok": True} או שגיאה בתבנית המוסכמת."""
    session_id = payload.get("session_id")
    raw_mac = payload.get("mac")
    disk = payload.get("disk")
    if not session_id or not raw_mac or not disk:
        return {"ok": False, "error": "missing session_id, mac or disk",
                "code": "bad_event"}

    mac = lenient_mac(raw_mac)
    if mac is None:
        # codeql[py/log-injection] false positive: %r מבריח שורה חדשה ותווי בקרה (#291)
        log.warning("disk_event with a malformed mac: %r", str(raw_mac)[:32])
        return {"ok": False, "error": "missing or malformed mac", "code": "bad_mac"}

    smart = payload.get("smart") if isinstance(payload.get("smart"), dict) else {}
    verdict = smart.get("verdict")
    if verdict not in VERDICTS:
        # verdict לא-מוכר אינו "נכשל" ואינו נבלע: הוא קלט פגום, ומקבל
        # קוד משלו. עיקרון 5 — לא לקפל "לא הבנו" לתוך מצב אחר.
        return {"ok": False, "error": "unknown smart verdict", "code": "bad_verdict"}

    decision = payload.get("decision")
    if decision not in DECISIONS:
        decision = None
    port = payload.get("port")
    port = port if isinstance(port, int) else None
    serial = payload.get("serial") if isinstance(payload.get("serial"), str) else None
    # #2492: הסידורי נשמר רק כשהוא מה שה-MAC הזה דיווח ל-`disk` הזה ב-hello
    # האחרון — אותו שער כמו `smart_history.record`. אחרת כל מכונה הייתה מסמנת
    # דיסק של אחרת כ-readonly (hello שלה מחזיר את הסימון, והסוכן מדלג עליו), או
    # משנה את בסיס ה-CRC שלו. האירוע עצמו נשמר, בלי שם: best-effort של הסוכן.
    if serial and smart_history.serial_of(conn, mac, disk if isinstance(disk, str) else "") != serial:
        # codeql[py/log-injection] false positive: %r מבריח שורה חדשה ותווי בקרה (#291)
        log.warning("disk_event from %s on %r: serial %r is not what it reported -- kept without it",
                    mac, str(disk)[:32], serial[:64])
        serial = None

    # UPSERT: אירוע חדש על אותו (סבב, מכונה, דיסק) דורס את הקודם — התמונה
    # החיה, לא ארכיון. קודם לכן זו הייתה קריאה-ואז-כתיבה עם מרוץ; כאן
    # ה-UPSERT מכריע ברמת ה-DB.
    #
    # `_write_lock, writing(conn)` כמו כל כותב במסלול בקשה (#54): כתיבה
    # שנכשלת בלי rollback נועלת את השרת עד אתחול. הנעילה עוטפת את הכתיבה
    # **בלבד** — `journal` שאחריה נוטל את אותה נעילה בעצמו, והיא `Lock`
    # ולא `RLock`, ולכן הוא יושב מחוצה לה (אותו כלל כמו ב-agent_loops).
    # #1751: נקרא **לפני** הכתיבה — המדידה הקודמת של הדיסק, לפני שזו דורסת אותה.
    newly_red = verdict == "fail" and not _was_red(conn, serial, session_id, mac, disk)

    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO disk_events (session_id, mac, disk, port, serial, verdict,"
            " reason, realloc, pending, uncorrectable, crc, write_state, decision,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(session_id, mac, disk) DO UPDATE SET"
            " port=excluded.port, serial=excluded.serial, verdict=excluded.verdict,"
            " reason=excluded.reason, realloc=excluded.realloc,"
            " pending=excluded.pending, uncorrectable=excluded.uncorrectable,"
            " crc=excluded.crc, write_state=excluded.write_state,"
            " decision=excluded.decision, updated_at=excluded.updated_at",
            (session_id, mac, disk, port, serial, verdict,
             smart.get("reason"), _int(smart.get("realloc")), _int(smart.get("pending")),
             _int(smart.get("uncorrectable")), _int(smart.get("crc")),
             payload.get("write_state"), decision, now_iso()),
        )

    # #1859: כל המאפיינים של הסבב הזה, לפי סידורי — ההיסטוריה שממנה השרת לומד.
    smart_history.record(conn, serial, session_id, mac, smart.get("attrs"), disk)

    # #1760: דילוג אדום על דיסק לקריאה בלבד נזכר בשרת עם הסיבה שלו — אחרת אחרי
    # אתחול (setro נעלם) הדיסק חוזר בלי סימן, ורשומה ישנה עליו צובעת אותו כתום.
    readonly_skip = verdict == "fail" and smart.get("reason") == "readonly" and decision == "skip"
    if readonly_skip:
        disk_failures.record_readonly(conn, session_id, mac, disk, serial)

    # #1751: SMART אדום — "דיסק אדום" במייל, פעם אחת לכל דיסק שהאדים (``_was_red``).
    # לקריאה בלבד עם סידורי כבר נרשם כ-``disk_failure`` (גם הוא "דיסק אדום") — לא פעמיים.
    if newly_red and not (readonly_skip and serial):
        journal(conn, "smart_red",
                f"{mac} {disk} serial={serial or '?'} port={port if port is not None else '?'}"
                f" reason={smart.get('reason') or '?'}"
                + slot_names.journal_suffix(slot_names.label(conn, mac, port=port, serial=serial)),
                machine=mac, round_id=session_id)

    # רק הכרעה מפורשת נרשמת ביומן — לא כל probe. "החלף"/"דלג" על דיסק
    # פגום הוא מה שהמפעיל צריך לראות בהיסטוריה.
    if decision is not None:
        journal(conn, "disk_decision",
                f"{mac} {disk} ({verdict}): {decision}"   # #704: + השם, כשיש
                + slot_names.journal_suffix(slot_names.label(conn, mac, port=port, serial=serial)),
                machine=mac, round_id=session_id)
    return {"ok": True}


def for_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """כל אירועי הדיסקים של סבב, לצפיית #380. disk_number נגזר מ-port
    (חריץ 1 -> "דיסק 1"), ונופל אחורה לשם ההתקן כשאין חריץ."""
    rows = conn.execute(
        "SELECT mac, disk, port, serial, verdict, reason, realloc, pending,"
        " uncorrectable, crc, write_state, decision, updated_at"
        " FROM disk_events WHERE session_id = ? ORDER BY mac, port, disk",
        (session_id,),
    ).fetchall()
    names = slot_names.all_by_mac(conn) if rows else {}
    out = []
    for r in rows:
        out.append({
            "mac": r["mac"],
            "disk": r["disk"],
            "disk_number": r["port"] if r["port"] is not None else None,
            # #704: השם שהמפעיל נתן לחיבור; None = "דיסק N" כמו היום.
            "slot_name": slot_names.resolve(names.get(r["mac"], {}), r["port"], r["serial"]),
            "port": r["port"],
            "serial": r["serial"],
            "verdict": r["verdict"],
            "reason": r["reason"],
            "realloc": r["realloc"],
            "pending": r["pending"],
            "uncorrectable": r["uncorrectable"],
            "crc": r["crc"],
            "write_state": r["write_state"],
            "decision": r["decision"],
            "updated_at": r["updated_at"],
        })
    return out


def last_by_serial(conn: sqlite3.Connection, serials: list[str] | None = None) -> dict[str, dict]:
    """#2706: הפסק של הסבב האחרון שקרא כל סידורי -- {serial: {"verdict", "reason", "at"}}.

    משכפל שהוער לסבב שולח hello אחד עם `smart: "unchecked"` (המטמון בסוכן לכל אתחול, וה-hello
    לעולם אינו probe), קורא SMART בשער של הסבב ונכבה; ה-hello מחליף את `disks_json` כולו. זה מה
    שהסבב קרא, ליד המגירה, **לתצוגה בלבד**: השער האדום וה-precheck נשארים על ה-hello -- פסק
    מאתחול קודם אינו ראיה על הדיסק עכשיו (עיקרון 5). רק שורות שהסידורי שלהן נשמר: `ingest` שומר
    סידורי רק כשהוא מה שה-MAC הזה דיווח (#2492). `unchecked` של הסבב הוא גם תשובה ("לא נקרא")."""
    # `serials` -- רק אלה (מגירות של מכונה אחת, drawer_list); None = כולם (/machines).
    if serials is not None and not serials:
        return {}
    where = f" AND serial IN ({','.join('?' * len(serials))})" if serials else ""
    rows = conn.execute(
        "SELECT serial, verdict, reason, MAX(updated_at) AS at FROM disk_events"
        " WHERE serial IS NOT NULL AND serial != ''" + where + " GROUP BY serial", serials or ())
    return {r["serial"]: {"verdict": r["verdict"], "reason": r["reason"], "at": r["at"]} for r in rows}
