"""X16 (נדב 14/11): מדיניות HDD -- מתג בהגדרות, לא סף.

המתג `hdd_support` הוא של #3268/#3271 (ברירת המחדל כבוי, נדב 14/11); כאן רק המדיניות שלו:
  כבוי  -- HDD מותר כמקור בלבד. כיעד הוא נפסל, באדום, עם הסיבה `REFUSED`.
  דלוק  -- ל-HDD ציפייה משלו, שנלמדת מהגלים שנמדדו לאותו serial ולאותו דגם
           (`drive_rates`), במקום המהירות המינימלית לכונן (`writefloor`).
דיסק שאינו HDD (`rotational` 0) -- בדיוק כמו קודם. `rotational` שלא נקרא
(`null`, חסר, סוכן ישן) = לא נמדד: המדיניות של דיסק מוצק, **עם שורת יומן**
(עיקרון 5) -- לעולם לא false בשקט.

אין כאן ספים להגדרות. **#3655 (S34, נדב 18/11): ההחלטה היא של המוח** (`brain/floorrule.py`, K-FLOOR): `INITIAL_MBPS`, `EXPECT_MARGIN` ו-`WINDOW`
(הערכה ולא מדידה: ההיסטוריה של הדיסק מחליפה את הראשון) וההשוואות שלהם הם שם. כאן נאספות העובדות (המתג, `rotational`, שורות `drive_rates`)
ומבוצעת התוצאה: סירוב, או הרצפה בתוך התוכנית.

הסוכן אוכף לפני כל כתיבה (`hdd_target_gate` ב-`agent/lib/restore/writecheck.sh`): את המתג הוא קורא
מתשובת ה-hello (`WC_HDD`, כמו בדיקת ההמתנה), ואת הציפיות מהמניפסט (`manifest`). בשרת, ההשלמה במנוע החתיכות (`repair.plan`) מקבלת את אותה
הכרעה לכל דיסק דרך `RepairFloor`, בלי לגעת ב-`chunksend.py`.
"""

from __future__ import annotations

import json
import logging

from server.brain import floorrule
from server.db import get_setting, SqlConnection
from server.disks import writefloor

log = logging.getLogger("imagesrv.hdd_policy")

SETTING = "hdd_support"
REFUSED = "HDD — תמיכה ב-HDD כבויה בהגדרות"


def enabled(conn: SqlConnection) -> bool:
    """רק "true" מדליק. חסר (שרת לפני X16) או כל ערך אחר = כבוי, ברירת המחדל."""
    return get_setting(conn, SETTING) == "true"


def expectation(conn: SqlConnection, serial: str | None, model: str | None) -> int | None:
    """הציפייה שנלמדה לדיסק הזה, MB/s, או `None` = אין גל שנמדד (לא 0). השורות נאספות כאן; החישוב במוח (`floorrule.expect`)."""
    if not serial:
        return None
    return floorrule.expect(_rates(conn, serial, model))


def _rates(conn: SqlConnection, serial: str, model: str | None) -> list[tuple]:
    """(בייטים, שניות) של הגלים האחרונים של הדיסק, החדשים ראשונים: עובדות למוח."""
    return [tuple(r) for r in conn.execute(
        "SELECT bytes, seconds FROM drive_rates WHERE serial = ? AND COALESCE(model, '') = ?"
        " AND bytes > 0 AND seconds > 0 ORDER BY id DESC LIMIT ?",
        (serial, model or "", floorrule.WINDOW)).fetchall()]


def _rotational(disk: dict, where: str) -> int:
    value = disk.get("rotational")
    if value in (0, 1) and not isinstance(value, bool):
        return value
    log.warning("%s: rotational %r not read -- not measured; the non-rotational policy", where, value)
    return 0


def decision(conn: SqlConnection, disk: dict, *, role: str = "target",
             support: bool | None = None, where: str = "") -> tuple[str | None, int | None]:
    """(סיבת סירוב או None, הרצפה בMB/s לדיסק הזה). מקור אינו נפסל לעולם. **המוח מחליט** (`floorrule.decide_disk`, K-FLOOR, S34, #3655);
    כאן נאספות העובדות ומבוצעת התוצאה. אין החלטה (המוח נכשל) = הרצפה של דיסק מוצק (`floorrule.DEFAULT`)."""
    where = where or f"{disk.get('model') or '?'} {disk.get('serial') or '?'}"
    serial, model = disk.get("serial"), disk.get("model")
    call = floorrule.decide_disk(
        conn, get_setting(conn, writefloor.SETTING), rotational=0 if role == "source" else _rotational(disk, where), role=role,
        support=enabled(conn) if support is None else support,
        rates=lambda: _rates(conn, serial, model) if serial else [], serial=serial or "", where=where)
    if call is None:
        return None, floorrule.DEFAULT
    if call.verdict == floorrule.REFUSED:
        return REFUSED, None
    if call.verdict == floorrule.ESTIMATE:
        log.warning("%s: HDD with no measured wave -- the starting estimate %d MB/s", where, call.mbps)
    return None, call.mbps


def _inventory(conn: SqlConnection, mac: str | None = None) -> list[tuple[str, dict]]:
    """(mac, דיסק) מה-hello האחרון. JSON פגום = שורת יומן, והמכונה בלי דיסקים ידועים."""
    rows = conn.execute("SELECT mac, disks_json FROM net_devices WHERE disks_json IS NOT NULL"
                        + (" AND mac = ?" if mac else ""), (mac,) if mac else ()).fetchall()
    out = []
    for m, raw in rows:
        try:
            disks = json.loads(raw)
        except (ValueError, TypeError):
            log.warning("%s: disks_json unreadable -- rotational not measured", m)
            continue
        out += [(m, d) for d in (disks if isinstance(disks, list) else []) if isinstance(d, dict)]
    return out


def manifest(conn: SqlConnection) -> dict:
    """מה שהסוכן מקבל במניפסט: ערך הפתיחה, והציפיות שנלמדו (serial ← דגם ← MB/s)
    לכל HDD במלאי שיש לו גל שנמדד. רק כשהמתג דלוק: כבוי, אין לציפייה שימוש."""
    support = enabled(conn)
    floors: dict[str, dict[str, int]] = {}
    if support:
        for _, disk in _inventory(conn):
            serial, model = disk.get("serial"), disk.get("model") or ""
            if disk.get("rotational") != 1 or not serial or model in floors.get(serial, {}):
                continue
            learned = expectation(conn, serial, model)
            if learned is not None:
                floors.setdefault(serial, {})[model] = learned
    return {"hdd_floor_mbps": floorrule.INITIAL_MBPS, "hdd_floors": floors}


class RepairFloor(int):
    """המהירות המינימלית לכונן (`writefloor.mbps`) כמספר -- החוזה של `write_floor` ב-chunksend
    לא השתנה -- ועוד `for_disk`, ההכרעה לכל דיסק בהשלמה (`repair.plan`).

    עצל: נשאל רק כש-`plan` שואל על דיסק, פעם אחת לכל דיסק לכל מופע (מופע אחד למחיצה).
    השימושים המספריים (`_write_kbps`, `_fail_slow_fetches`) אינם קוראים ל-DB מעבר לקודם.
    """

    _conn: SqlConnection
    _memo: dict[tuple[str, str], tuple[str | None, int | None]]

    def __new__(cls, conn: SqlConnection) -> "RepairFloor":
        value = super().__new__(cls, writefloor.mbps(conn))
        value._conn = conn
        value._memo = {}
        return value

    def for_disk(self, mac: str, serial: str) -> tuple[str | None, int | None]:
        key = (mac, serial)
        if key not in self._memo:
            disk = next((d for _, d in _inventory(self._conn, mac) if d.get("serial") == serial), None)
            if disk is None:
                log.warning("%s/%s: not in the inventory -- rotational not measured;"
                            " the non-rotational policy", mac, serial)
                self._memo[key] = (None, int(self))
            else:
                self._memo[key] = decision(self._conn, disk, where=f"{mac}/{serial}")
        return self._memo[key]
