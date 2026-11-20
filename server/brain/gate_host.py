"""#3064, K3, קבוצה ד' (#3475): שערי השרת עצמו לפני פתיחת סבב -- RAID (B49), מקום פנוי (B60), בריאות המערכת (B51).

הכללים (פסקים, צל בלבד, סיבה מהקוד הקיים) ב-`server/brain/gates.py`. כל פונקציה: ``(conn, macs, round_id) -> list[Reading]``,
ושלושתן מחזירות **קריאה אחת** לשער (``mac=None``): הפסק החמור מבין מה שנקרא, והסיבה היא שורות הבריאות שהכריעו, מילה במילה
(``label: detail``). סדר החומרה: ``BLOCK`` > ``WARN`` > ``UNDECIDED`` > ``OPEN``; ``OPEN`` רק כשכל מה שנקרא תקין **וגם** נקרא משהו.

מה השערים **לא** קוראים: אין כאן מדידה משלהם, אלא אותן פונקציות שהמפעיל רואה בעמוד הבריאות (`raid_status.health_checks`,
`health.db_space_check`, `storage_history.health_checks`, `gate_core.health_rows`). ייבואים בתוך הפונקציות: `gates` נטען מ-`room`,
ו-`health` מייבא חצי שרת.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path

from server.brain import gate_core
from server.brain.gate_core import BLOCK, OPEN, UNDECIDED, WARN, Reading, undecided

log = logging.getLogger("imagesrv.brain")

#: סדר החומרה: הראשון שיש לו חלק בקריאה הוא הפסק שלה.
SEVERITY = (BLOCK, WARN, UNDECIDED, OPEN)
#: מצבי שורת בריאות שאינם בעיה וגם אינם ראיה. ``off`` אינו כאן: הוא "אי אפשר לבדוק כאן", כלומר לא נבדק.
NEUTRAL_STATES = ("info", "not_applicable")


def fold(parts: list[tuple[str, str, str]], mac: str | None = None) -> Reading:
    """``(פסק, טקסט, ראיה)`` לכל מה שנקרא -> קריאה אחת: הפסק החמור, והטקסט של **מי שהכריע** (אלה שבאותו פסק)."""
    worst = next(v for v in SEVERITY if any(p[0] == v for p in parts))
    decided = [p for p in parts if p[0] == worst]
    return Reading(worst, " | ".join(p[1] for p in decided), tuple(p[2] for p in parts), mac)


def row_text(row: dict) -> str:
    """שורת בריאות כמו שהמפעיל קורא אותה: ``label: detail``."""
    return f"{row.get('label') or row.get('id')}: {row.get('detail')}"


# --- B49: RAID ----------------------------------------------------------------------------------------------


def raid(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B49: מערכי ה-RAID של השרת, מאותן שלוש קריאות של עמוד הבריאות (`/proc/mdstat`, `/etc/imagesrv/raid`, `/proc/mounts`).

    - מערך **לא פעיל** (inactive), או מערך שהמתקין יצר ואינו ב-mdstat בכלל = ``BLOCK``: הנתונים אינם נגישים.
    - מערך פעיל שפגוע (כונן נפל או חסר), בבנייה מחדש, או ESP שלא עוגן = ``WARN``: הסבב עדיין אפשרי, והמפעיל צריך לדעת.
    - קובץ שלא נקרא, מצב כוננים שלא נקרא, או `/proc/mdstat` שלא נקרא כשמערך מוצהר = ``UNDECIDED`` (לא נקרא אינו "נעלם").
    - **שרת בלי RAID**: אין שורה בבריאות בכלל, ולכן ``OPEN`` רק על ראיה חיובית -- שני הקבצים נקראו בלי שגיאה, `/etc/imagesrv/raid`
      אינו קיים או אומר ``level=none``, ואין מערך ב-mdstat. קובץ הצהרה שאין בו ``level`` אינו ראיה.
    """
    from server.system import raid_status            # noqa: PLC0415

    read: dict[str, str | None] = {}

    def reader(key: str, path_name: str):
        def go() -> str | None:
            try:
                text = raid_status.read_optional(getattr(raid_status, path_name))
            except OSError:
                read[key] = "error"
                raise
            read[key] = "absent" if text is None else "text"
            read[f"{key}_text"] = text
            return text
        return go

    rows = raid_status.health_checks(reader("mdstat", "MDSTAT_PATH"), reader("declared", "RAID_FACT_PATH"),
                                     reader("mounts", "MOUNTS_PATH"))
    unread = "error" in (read.get("mdstat"), read.get("declared"))
    seen = f"mdstat={read.get('mdstat')}", f"declared={read.get('declared')}"
    if not rows:
        fact = raid_status.parse_fact(read.get("declared_text") or "")
        if read.get("declared") == "text" and "level" not in fact:
            return [undecided("הקובץ /etc/imagesrv/raid נקרא ואין בו level -- לא ידוע אם הותקן RAID", seen)]
        return [Reading(OPEN, "אין RAID מוצהר (/etc/imagesrv/raid) ואין מערך ב-/proc/mdstat",
                        (*seen, f"level={fact.get('level', 'absent')}", "arrays=0"))]
    arrays = {a.name: a for a in raid_status.parse_mdstat(read.get("mdstat_text") or "")}
    parts = []
    for row in rows:
        state, array = row["state"], arrays.get(row["id"].removeprefix("raid_"))
        if state == "bad":
            # רק מה שהוכח: מערך שמופיע ואינו פעיל, או מוצהר ואינו ב-mdstat (כשהקבצים נקראו). אחרת -- פגוע אבל קיים, או לא נקרא.
            dead = (array is not None and not array.active) or (row["id"] == "raid" and not unread)
            verdict = BLOCK if dead else UNDECIDED if row["id"] == "raid" and unread else WARN
        else:
            verdict = {"ok": OPEN, "warn": WARN}.get(state, UNDECIDED)
        parts.append((verdict, row_text(row), f"{row['id']}={state}"))
    got = fold(parts)
    return [Reading(got.verdict, got.reason, (*got.evidence, *seen))]


# --- B60: מקום פנוי -----------------------------------------------------------------------------------------


def _db_dir(conn: sqlite3.Connection) -> Path | None:
    """תיקיית קובץ ה-DB של החיבור (`PRAGMA database_list`), או ``None`` כשהוא בזיכרון."""
    for _seq, name, file in conn.execute("PRAGMA database_list"):
        if name == "main" and file:
            return Path(file).parent
    return None


def _trend_rows(conn: sqlite3.Connection, connected: list[dict]) -> list[tuple[str, str, str]]:
    """מיקום מחובר אחד = שורה אחת (`storage_history.health_checks`, אותו טקסט), ועוד הרזרבה למערכת מול הפנוי האחרון."""
    from server.storage import disk_reserve, history        # noqa: PLC0415

    by_id = {r["id"]: r for r in history.health_checks(conn)}
    parts = []
    for loc in connected:
        row, t = by_id.get(f"storage_trend:{loc['id']}"), history.trend(conn, loc["id"])
        label = f"{loc['name']} ({loc['id']})"
        if row is None or t["free_bytes"] is None:
            parts.append((UNDECIDED, f"מקום פנוי לאורך זמן ({label}): לא נדגם", f"{loc['id']}=unsampled"))
            continue
        free, reserve = t["free_bytes"], disk_reserve.reserve()
        if free < reserve:
            # אותו משפט של `db_space_check`, על מיקום האחסון
            parts.append((WARN, f"מקום פנוי ב{label}: נותרו {disk_reserve.gb(free)}, פחות מרזרבת המערכת ({disk_reserve.gb(reserve)})",
                          f"{loc['id']}=below_reserve free={free}"))
        elif row["state"] == "warn":
            parts.append((WARN, row_text(row), f"{loc['id']}=filling days_left={t['days_left']}"))
        elif row["state"] == "ok":
            parts.append((OPEN, row_text(row), f"{loc['id']}=ok free={free}"))
        else:
            parts.append((UNDECIDED, row_text(row), f"{loc['id']}=no_rate free={free}"))   # `off`: הקצב עוד לא נמדד
    return parts


def library_space(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B60: מקום פנוי במחיצת ה-DB ובכל מיקום אחסון **מחובר**.

    ``SERVER_DISK_FULL`` אינו קיים בקוד (אומת): המקורות הם `db.last_disk_full`, `health.db_space_check`, `storage/history.trend`
    ו-`disk_reserve.reserve`, ואין כאן סף חדש.

    - כתיבה ל-DB שכבר נכשלה ב-SQLITE_FULL (`db.last_disk_full`) = ``BLOCK``. הערה: `room.open_round` רושם `room_open` ביומן
      **לפני** השערים, וכתיבה מוצלחת מנקה את הסימן -- כלומר הוא מגיע לכאן רק כשזו עצמה נכשלה.
    - פנוי ב-DB מתחת ל-1GiB או מתחת לרזרבה, מיקום שפנוי בו פחות מהרזרבה, או שמתמלא תוך `WARN_DAYS` = ``WARN``.
    - לא נמדד (מחיצה שלא נקראה, מיקום בלי דגימה, קצב שעוד אין לו שתי דגימות ביום שונה) = ``UNDECIDED``. שורת "אינו מתמלא"
      דורשת קצב: בלעדיו `OPEN` היה טוען מה שלא נמדד.
    """
    from server.db import last_disk_full             # noqa: PLC0415
    from server.storage import locations             # noqa: PLC0415
    from server.system import health                 # noqa: PLC0415

    where = _db_dir(conn)
    try:
        free = shutil.disk_usage(where).free if where is not None else None
    except OSError:
        free = None
    full = last_disk_full()
    db_row = health.db_space_check(free, full)
    state = db_row["state"]
    verdict = BLOCK if full is not None else {"ok": OPEN, "warn": WARN, "bad": WARN}.get(state, UNDECIDED)
    parts = [(verdict, row_text(db_row), f"db_space={state} free={free if free is not None else 'unread'}")]
    connected = [loc for loc in locations.library_roots(conn, "") if loc["available"]]
    if not connected:
        parts.append((UNDECIDED, "אין מיקום אחסון מחובר שאפשר למדוד", "locations=0"))
    parts += _trend_rows(conn, connected)
    return [fold(parts)]


# --- B51: בריאות המערכת -------------------------------------------------------------------------------------


def system_health(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B51 (הכרעת זאוס 15/11): **כל** שורות `GET /health`, כולל SMART של דיסקי השרת (`server_disks`, `health.collect`), דרך
    `gate_core.health_rows` -- אותו `collect` ואותם hooks, ואין הפעלה שנייה.

    ``bad`` ו-``warn`` = ``WARN``: שורה אדומה אינה מוכיחה לבדה שהסבב חסר תוחלת (dnsmasq שנפל אינו מזיז משכפל שכבר דלוק), ו-
    ``BLOCK`` שמור למה שהוכח (B49, B60). ``unknown`` ו-``off`` ("אי אפשר לבדוק כאן") = ``UNDECIDED``. ``info``/``not_applicable``
    אינן בעיה וגם אינן ראיה. ``OPEN`` רק כשיש לפחות שורה ``ok`` ואף שורה אחרת אינה בעיה או לא נבדקה. מקור שלא חובר
    (``None``) או שזרק = ``UNDECIDED``. הקריאה מריצה `ss`/`systemctl` ובדיקות HTTP לשרת עצמו: ``health_ms`` בראיות.
    """
    began = time.monotonic()
    try:
        rows = gate_core.health_rows(conn)
    except Exception as exc:                                                    # noqa: BLE001 -- נרשם כ-UNDECIDED, לא נבלע
        log.exception("brain: the health rows could not be read for the health gate")
        return [undecided(f"שורות הבריאות לא נקראו: {type(exc).__name__}", (f"error={type(exc).__name__}",))]
    took = f"health_ms={int((time.monotonic() - began) * 1000)}"
    if rows is None:
        return [undecided("שורות הבריאות לא חוברו למוח -- לא נקראו", ("health=unwired",))]
    if not rows:
        return [undecided("עמוד הבריאות לא החזיר אף שורה", ("rows=0", took))]
    parts, neutral = [], 0
    for row in rows:
        state = row.get("state")
        if state in NEUTRAL_STATES:
            neutral += 1
            continue
        verdict = {"ok": OPEN, "warn": WARN, "bad": WARN}.get(state, UNDECIDED)
        parts.append((verdict, row_text(row), f"{row.get('id')}={state}"))
    if not parts:
        return [undecided("כל שורות הבריאות לא רלוונטיות או מידע בלבד -- אין ראיה שהשרת תקין", (f"rows={len(rows)}", took))]
    got = fold(parts)
    if got.verdict == OPEN:
        got = Reading(OPEN, f"נבדקו {len(parts)} שורות בריאות, כולן תקינות", (f"ok={len(parts)}", f"neutral={neutral}"))
    return [Reading(got.verdict, got.reason, (*got.evidence, took))]
