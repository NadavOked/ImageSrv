"""חישובי המצב הטהורים של סבב חדר השיכפולים (#2594, שלב 1 בפיצול `room.py`).

כל פונקציה כאן **קוראת** — שורת `room_rounds`, מלאי ה-hello (`net_devices.disks_json`),
חברי הגל — ומחזירה ערך. אין כאן כתיבה ל-DB, אין `_write_lock`, אין שעון ואין FastAPI.
הגופים הועברו מ-`room.py` כפי שהם, בלי שינוי התנהגות.

`room.py` מייבא את כל השמות האלה בחזרה, ולכן `room.<שם>` ממשיך לעבוד לכל קורא קיים.

⚠️ `monkeypatch.setattr(room, "<שם>", ...)` מחליף את השם **ב-`room`** בלבד: קריאה מתוך
`room.py` תראה את ההחלפה, קריאה מתוך המודול הזה (`has_fresh_drawers` → `active_round`,
`wave_drives` → `_fresh_disks`) — לא. מי שצריך להחליף פונקציה כאן מחליף אותה ב-`room_state`.
מה שתלוי בשעון (`_stalled_s`, `_pace`, `_fresh`) נשאר ב-`room.py` בדיוק בגלל זה:
`test_clock_jump_1947` מחליף את `room.datetime`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # annotations only -- no import cycle, no FastAPI
    from server.rounds.sessions import SessionStore

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


def _expected(round_row: sqlite3.Row, machines: int) -> int:
    """17/10: כמה מחשבים בגל — מחשבי הסבב, לא כל הקבוצה ("1/3 מחשבים" בקונסולה על
    סבב של מחשב אחד). לגל של חדר השיכפולים המספר אינו תנאי התחלה (`maybe_start`)."""
    roster = _roster(round_row)
    return len(roster) if roster else max(1, machines)


def _roster(round_row: sqlite3.Row) -> list[str] | None:
    """נדב 17/10: גל של סבב עם בחירה מעיר ומצרף רק את מחשבי השיכפול שבה —
    `wol_sent grp_CLONERS count=3` העיר את כל החדר, כולל מכונה שאסור להעיר.
    None = סבב ישן בלי בחירה: כל הקבוצה, כמו קודם (`store.open`)."""
    raw = round_row["target_slots_json"]
    return None if raw is None else sorted({item["mac"] for item in json.loads(raw)})


def _written(round_row: sqlite3.Row) -> set[str]:
    return set(json.loads(round_row["written_serials"]))


def _skipped(round_row: sqlite3.Row) -> set[str]:
    """#1760 (ברזל D6b, 18/10): המגירות שהסוכן דילג עליהן כאדומות בסבב הזה —
    SMART נכשל או קריאה בלבד. אדום **לעולם אינו יעד** (#1732), ולכן מגירה כזו
    סגורה: לא נכתבה, אבל גם אינה "טרייה" — היא אינה פותחת גל ואינה מעירה."""
    return set(json.loads(round_row["skipped_serials"] or "[]"))


def _cancelled(round_row: sqlite3.Row) -> set[str]:
    """#1936 (FI O2, 21/10): המגירות שמפעיל ביטל בסבב הזה ("בטל דיסק זה", #1386).
    ביטול של מפעיל **סופי לסבב**: המגירה סגורה, לא נכתבה ואינה נכשלה — היא אינה
    פותחת גל, אינה מעירה ואינה נכתבת שוב בגל הבא. בסבב הבא היא ירוקה ונכתבת רגיל."""
    return set(json.loads(round_row["cancelled_serials"] or "[]"))


def _closed(round_row: sqlite3.Row) -> set[str]:
    """מה שהסבב כבר אינו ממתין לו: נכתב, דולג כאדום (#1760), או בוטל בידי מפעיל (#1936)."""
    return _written(round_row) | _skipped(round_row) | _cancelled(round_row)


def _no_disk_count(round_row: sqlite3.Row) -> int:
    """#2201: כמה חריצים נסגרו בסבב כי לא היה בהם דיסק (`target_slots[].no_disk`)."""
    return sum(len(item.get("no_disk") or [])
               for item in json.loads(round_row["target_slots_json"] or "[]"))


def _remaining(round_row: sqlite3.Row) -> int:
    """כמה מגירות הסבב עוד צריך לכתוב: היעד פחות מה שנכתב, פחות מה שדולג כאדום
    (#1760), פחות מה שמפעיל ביטל (#1936), ופחות חריץ שנבחר ולא היה בו דיסק (#2201) —
    שלושתן סוגרות את המקום שלהן ביעד, הן אינן ממתינות להחלפה."""
    return (round_row["target_drives"] - round_row["written_drives"]
            - len(_skipped(round_row)) - len(_cancelled(round_row))
            - _no_disk_count(round_row))


def _red(disk: dict) -> bool:
    """#1732: ה-hello האחרון אמר `smart: "fail"` (SMART נכשל או קריאה בלבד, #1742)."""
    return disk.get("smart") == "fail"


def _selected_ports(round_row: sqlite3.Row | None, mac: str) -> set[int] | None:
    """הפורטים שנבחרו לכתיבה עבור המכונה הזאת בסבב. None = סבב ישן (כל
    הדיסקים). קבוצה ריקה = המכונה לא נבחרה כלל."""
    if round_row is None or round_row["target_slots_json"] is None:
        return None
    for item in json.loads(round_row["target_slots_json"]):
        if item["mac"] == mac:
            return set(item["ports"])
    return set()


def _unconfirmed(round_row: sqlite3.Row, mac: str) -> set[int]:
    """#1954: חריצים שנבחרו כשהמשכפל היה כבוי ועוד לא אומת שהדיסק בהם הוא זה
    שנבחר (`expect` שלא נפתר — hello בלי מלאי). אינם נכתבים: "לא בדקנו" אינו
    "אותו דיסק" (עיקרון 5)."""
    for item in json.loads(round_row["target_slots_json"] or "[]"):
        if item.get("mac") == mac and isinstance(item.get("expect"), dict):
            return {int(port) for port in item["expect"]}
    return set()


def target_ports(round_row: sqlite3.Row | None, mac: str,
                 disks: list | None) -> list[int] | None:
    """`session.target_ports` של מחשב שיכפול בגל: מה שהמכונה כותבת **בגל הזה**.

    #1790 (ברזל D2, 19/10, room_41d7feed): גל 2 קיבל את כל החריצים שנבחרו, והמשכפל
    כתב מחדש גם את מגירה 3 שכבר הצליחה בגל 1 (~6 דקות וכתיבה מלאה על דיסק תקין).
    חריץ יוצא מהרשימה רק על **ראיה חיובית** (עיקרון 5): המלאי של ה-hello הזה עצמו
    (`disks`) אומר שבחריץ יושב דיסק שהסידורי שלו כבר סגור **בסבב הזה** — נכתב
    (`written_serials`) או דולג כאדום (`skipped_serials`, #1760). דיסק שהוחלף (סידורי
    אחר), דיסק בלי סידורי, או hello בלי מלאי — החריץ נשאר, והמגירה נכתבת.
    None = סבב ישן בלי בחירה (`_selected_ports`)."""
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


def target_disks(ports: list[int] | None, disks: list | None,
                 shown: list | None = None) -> list[dict] | None:
    """#2448: `session.target_disks` — לכל חריץ ב-`target_ports`, ה-serial והגודל שהקונסולה
    מציגה בו. הסוכן משווה אליהם את מה שנקשר ב-`target_init` (`drawer_shown`, drawerselect.sh),
    ודיסק שאינו זה נכשל ("המגירה הוחלפה אחרי שנבחרה") במקום להיכתב.

    #2458: `shown` (`shown_disks`) — מה שהקונסולה הציגה **ברגע הבחירה**, שנשמר בפתיחת הסבב.
    כשהוא נתון, הוא המקור, ולא המלאי של ה-hello הזה: מגירה שהוחלפה בין הבחירה ל-hello, במכונה
    שנשארה דלוקה, אינה "הדיסק שהוצג". בלעדיו (גל שאחרי הראשון) — המלאי של **ה-hello הזה עצמו**
    (`disks`), כמו ב-#2448.

    חריץ שאין בו בדיוק דיסק אחד (ריק, או ששניים טוענים לו), או בלי serial/גודל — `null` בשדה:
    הסוכן אינו מוצא בו "אותו דיסק" ואינו כותב (עיקרון 5). None = אין מה לשלוח (סבב ישן בלי
    בחירה, או hello בלי מלאי ובלי `shown`) — הסוכן ממשיך לפי החריץ בלבד, כמו קודם."""
    if ports is None:
        return None
    source = shown if shown is not None else disks
    if not isinstance(source, list):
        return None
    out = []
    for port in ports:
        here = [d for d in source if isinstance(d, dict) and _port(d.get("port")) == port]
        one = here[0] if len(here) == 1 else {}
        serial, size = one.get("serial"), _port(one.get("size_bytes"))
        out.append({"port": port,
                    "serial": serial if isinstance(serial, str) and serial else None,
                    "size_bytes": size if size and size > 0 else None})
    return out


def shown_disks(round_row: sqlite3.Row | None, mac: str) -> list | None:
    """#2458: מה שהקונסולה הציגה למפעיל בחריצים של המכונה הזו ברגע הבחירה (`shown` בפריט
    שלה ב-`target_slots_json`, `_validate_target_slots`) — רק **בגל שהבחירה פתחה** (גל 1).

    מהגל השני והלאה המגירות מוחלפות **בכוונה** ("מכבים, מחליפים מגירות, מדליקים"), ואין
    בחירה חדשה להשוות אליה: None, והמקור הוא המלאי של ה-hello (#2448).

    פריט בגל 1 בלי `shown` (סבב שנפתח לפני #2458) — `[]`: לא נשמר מה הוצג, ולכן כל חריץ
    מקבל `null` ואינו נכתב בגל הזה (עיקרון 5: "לא שמרנו" אינו "אותו דיסק"). המגירות נשארות
    טריות ונכתבות בגל הבא, לפי המלאי. None גם לסבב ישן בלי בחירה."""
    if (round_row is None or round_row["target_slots_json"] is None
            or round_row["wave_number"] != 1):
        return None
    for item in json.loads(round_row["target_slots_json"]):
        if item.get("mac") == mac:
            shown = item.get("shown")
            return shown if isinstance(shown, list) else []
    return []


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
    """המגירות של המכונה שעוד לא נכתבו בסבב הנוכחי. selected_ports מסנן
    לפורטים שנבחרו בלבד (None = כל הדיסקים, סבב ישן).

    #105: כונן בלי serial נשאר מחוץ לרשימה **בכוונה** — `_tally` סופר
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
    """#447: הכוננים שהגל הזה יכתוב — לפיהם נבחרת תקרת הקצב שלו.

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
    """#1760: כמה מגירות שהגל הפתוח היה ממתין להן ה-hello האחרון אומר שהן
    אדומות (ולא נכתבו או נסגרו כבר) — הסוכן ידלג עליהן, והגל אינו ממתין להן.
    המכונות: מי שנבחר לסבב (roster) — גם מי שלא הצטרף, כי משכפל שכל מגירותיו
    אדומות אינו מצטרף לעולם (has_fresh_drawers); סבב ישן בלי בחירה — מי שהצטרף."""
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


def _port(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
