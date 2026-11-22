"""#3897 (נדב 20/11): הצעת השרת לפתיחת סבב -- אילו משכפלים ואילו מגירות, לפי המספר ולפי הכמות שביקשו.

המפעיל בוחר אימג' וכמות ("18 דיסקים"). השרת עובר על המשכפלים לפי המספר שלהם (1, 2, 3...) וממלא מגירות עד
שיש מספיק. **זו הצעה ולא פעולה**: היא קריאה בלבד, ולא פותחת דבר ולא מעירה דבר. המפעיל מאשר אותה בלחיצה אחת,
והאישור הוא אותו `POST /room` עם `target_slots` מפורש שהיה תמיד (#1410) -- ההצעה מחליפה את הבחירה הידנית,
לא את האישור. לכן `selected` כאן הוא בדיוק הצורה של `target_slots`.

מה **אינו** מועמד (וכל אחד מהם מוחזר עם הסיבה, כדי שהמפעיל יראה למה משכפל חסר ולא ינחש):

- ``busy`` -- משכפל שכבר בסבב פעיל (בלעדיות; #3898) או באמצע שיכפול מקומי. הסבב הפעיל נקרא ב-`busy_macs` בלבד:
  זה המקום האחד שהסבבים-במקביל יחליפו.
- מגירה **אדומה** -- אסורה כיעד, בלי עקיפה (נדב 20/11; הכלל מ-22/10 נשאר). ההצעה מדלגת עליה בגלוי (``red``),
  והשלמה מגיעה ממשכפל הבא. מי שמסווג אדום הוא המוח (`rule.target`), אותו פסק שהקונסולה מציירת ושפתיחת הסבב אוכפת.
- מגירה ש**נפסלה כאיטית בסבב קודם** (``avoid``; #3897, ברזל 21/11) -- **אינה נבחרת כברירת מחדל**, אבל אינה אדומה ואינה
  חסומה: "המוח מציע ולא פוסל" (נדב 16/11), והמפעיל יכול לבחור אותה ידנית (`POST /room` עם `target_slots`). המקור: מה שהמשכפל
  עצמו מציג למגירה (B62 `SLOW_DISK`, הפסק האחרון של הסידורי: "בגל הקודם כתב 46MB/s…") ושורת היומן `disk_write_slow` של
  הסידורי. `rule.target` לבדו אינו רואה אותם (#2848: "איטי" הוא של הסבב הזה בלבד), ולכן ההצעה קוראת אותם בנפרד.
- מגירה בלי דיסק מזוהה (חריץ ריק, או כונן בלי serial) או בלי פורט SATA -- אין לה זהות על החוט.

משכפל **כבוי** נכלל (``awake: false``): פתיחת הסבב מעירה אותו (WoL) ונועלת את הבחירה ל-serial שדווח (#1954).

**פס ייצור**: חסרות מגירות (``shortfall`` > 0) ו-`prodline.is_line` אומר שהסבב ייפתח כפס ייצור -- ההצעה אומרת זאת
(``line``), וכמה גלים (``waves``). ``line`` נגזר מאותה פונקציה שפתיחת הסבב משתמשת בה, לא מחישוב שני.

**כל החלטה נושאת סיבה** (`reason_he`, בעברית, לעולם לא ``null``): לכל מגירה -- למה הוצעה, או למה לא -- ולכל משכפל.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3

from server.brain import memory, slowdisk
from server.core import machine_names
from server.disks import disk_sets, drawer_slots, model_slots, slow_write
from server.rounds import prodline, room
from server.rounds.room_state import active_rounds

FREE, BUSY, EMPTY = "free", "busy", "empty"
#: מצב מגירה בהצעה.
SELECTED, SPARE, RED, ABSENT, AVOID = "selected", "spare", "red", "absent", "avoid"
#: מה שנאמר על מגירה שנפסלה כאיטית: ההצעה לא בוחרת בה, והמפעיל יכול (המוח מציע ולא פוסל).
MANUAL_HE = "לא מוצע כברירת מחדל; אפשר לבחור אותו ידנית"


def busy_macs(ctx) -> dict[str, str]:
    """mac -> למה הוא תפוס, למשכפלים שאינם פנויים להצעה. כרגע חריץ סבב אחד (`active_round`)."""
    busy: dict[str, str] = {}
    for rnd in active_rounds(ctx.conn):   # #3898: כל הסבבים הפעילים -- משכפל בסבב אחד בלבד
        label = room.round_label(ctx, rnd)
        for item in json.loads(rnd["target_slots_json"] or "[]"):
            busy[item["mac"]] = label
    return busy


def _date(ts: object) -> str:
    """"17/11" מ-ISO 8601; ריק כשאין תאריך קריא."""
    m = re.match(r"\d{4}-(\d{2})-(\d{2})", ts) if isinstance(ts, str) else None
    return f"{m.group(2)}/{m.group(1)}" if m else ""


def slow_before(conn: sqlite3.Connection, serial: str) -> str | None:
    """הסיבה (בעברית) שהדיסק הזה אינו מוצע כברירת מחדל כי נפסל כאיטי, או None = לא נמצאה ראיה.

    שני מקורות, כי `rule.target` אינו רואה אף אחד מהם (#2848: "איטי" הוא של הסבב הזה בלבד):
    (1) מה שהמשכפל עצמו מציג -- הפסק האחרון של B62 על הסידורי (`RED`), בנוסח שהמסך מציג; (2) שורת היומן
    `disk_write_slow` של הסידורי (נפסל בבדיקת הכתיבה של סבב קודם). **קריאה שנכשלה אינה "לא נפסל"** (עיקרון 5):
    הדיסק לא מוצע, והסיבה אומרת שההיסטוריה שלו לא נקראה."""
    try:
        last = memory.decisions(conn, kind="disk", key=serial, rule=slowdisk.RULE, limit=1)
        if last and last[0]["verdict"] == slowdisk.RED:
            return f"{last[0]['reason']} -- {MANUAL_HE}"
        row = conn.execute("SELECT ts, detail FROM journal WHERE event = ? AND instr(detail, ?) > 0 ORDER BY id DESC LIMIT 1",
                           (slow_write.EVENT, f" serial={disk_sets.serial_key(serial)} ")).fetchone()
    except sqlite3.Error:
        return f"ההיסטוריה של הדיסק (איטי בסבב קודם?) לא נקראה -- {MANUAL_HE}"
    if row is None:
        return None
    mbps = re.search(r"\bmbps=(\d+)", row["detail"])
    seen = ", ".join(x for x in (_date(row["ts"]), f"{mbps.group(1)}MB/s" if mbps else "") if x)
    return f"נפסל כאיטי בבדיקת הכתיבה בסבב קודם{f' ({seen})' if seen else ''} -- {MANUAL_HE}"


def _order(row: sqlite3.Row) -> tuple:
    number = machine_names.number_of("cloner", row["suffix"])
    return (number is None, number if number is not None else 0, row["suffix"])


def _drawers(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict]:
    """המגירות של משכפל כפי שהצעה רואה אותן, לפי מספר המגירה."""
    mac = row["mac"]
    model_map = model_slots.for_mac(conn, mac)
    slots = drawer_slots.effective(row["drawer_count"], row["drawer_slots"], model_map["slots"] if model_map else None)
    allowed = drawer_slots.sata_ports(slots)
    out = []
    for d in room.drawer_list(conn, mac, set()):
        port = d["port"]
        if not d["serial"]:
            state, why = ABSENT, "אין דיסק מזוהה במגירה: חריץ ריק או כונן בלי מספר סידורי, ואין לו זהות על החוט"
        elif port is None or port not in allowed:
            state, why = ABSENT, "המגירה בלי פורט SATA מוכר, ואין לה זהות על החוט"
        elif not (d.get("rule") or {}).get("target"):
            state, why = RED, (d.get("rule") or {}).get("target_reason_he") or "המגירה אדומה: לא כותבים על הדיסק הזה"
        elif (slow := slow_before(conn, d["serial"])) is not None:
            state, why = AVOID, slow
        else:
            state, why = SPARE, "דיסק כשר לכתיבה, אך הכמות המבוקשת כבר מולאה ממגירות קודמות"
        out.append({"port": port, "drawer": drawer_slots.drawer_of(slots, d), "serial": d["serial"],
                    "state": state, "reason_he": why})
    return sorted(out, key=lambda x: (x["drawer"] is None, x["drawer"] or 0, x["port"] if x["port"] is not None else 0))


def suggest(ctx, target_drives: int) -> dict:
    """ההצעה ל-``target_drives`` דיסקים. ``selected`` ריק = אין מה להציע (אין משכפל פנוי עם מגירה כשרה)."""
    if isinstance(target_drives, bool) or not isinstance(target_drives, int) or target_drives < 1:
        raise ValueError("כמות הדיסקים חייבת להיות מספר חיובי")
    conn = ctx.conn
    busy = busy_macs(ctx)
    rows = sorted(conn.execute(
        "SELECT m.mac, m.suffix, m.drawer_count, m.drawer_slots, d.last_seen, d.local_clone"
        f" FROM machines m LEFT JOIN net_devices d ON d.mac = m.mac WHERE m.{room._IN_ROOM}").fetchall(), key=_order)
    left, selected, machines = target_drives, [], []
    for row in rows:
        mac = row["mac"]
        entry = {"mac": mac, "name": row["suffix"], "number": machine_names.number_of("cloner", row["suffix"]),
                 "display_name": machine_names.label("cloner", row["suffix"]),
                 "awake": room._is_awake(row["last_seen"]), "state": FREE, "busy_in": None, "reason_he": None, "drawers": []}
        if mac in busy or room.local_copy_now(row["local_clone"], row["last_seen"]):
            entry.update(state=BUSY, busy_in=busy.get(mac) or "local_copy")
            entry["reason_he"] = (f"תפוס בסבב {busy[mac]}" if mac in busy else "תפוס: שיכפול מקומי רץ עליו עכשיו")
            machines.append(entry)
            continue
        entry["drawers"] = _drawers(conn, row)
        if not any(x["state"] == SPARE for x in entry["drawers"]):
            entry["state"] = EMPTY
        ports = []
        for x in entry["drawers"]:
            if x["state"] == SPARE and left > 0:
                x["state"] = SELECTED
                x["reason_he"] = "מוצעת: דיסק כשר לכתיבה (לא אדום, ולא נפסל כאיטי בסבב קודם)"
                ports.append(x["port"])
                left -= 1
        if ports:
            selected.append({"mac": mac, "ports": sorted(ports)})
            entry["reason_he"] = f"מוצע: {len(ports)} מגירות כשרות"
        elif entry["state"] == EMPTY:
            entry["reason_he"] = "אין בו מגירה כשרה להצעה (הסיבה של כל מגירה כתובה לידה)"
        else:
            entry["reason_he"] = "לא נדרש: הכמות המבוקשת כבר מולאה ממשכפלים קודמים"
        machines.append(entry)
    count = sum(len(item["ports"]) for item in selected)
    line = bool(selected) and prodline.is_line(target_drives, prodline.drawers(conn, sorted(selected, key=lambda i: i["mac"])))
    return {"target_drives": target_drives, "machines": machines, "selected": selected, "selected_count": count,
            "shortfall": target_drives - count, "line": line,
            "waves": math.ceil(target_drives / count) if line and count else (1 if count else 0)}
