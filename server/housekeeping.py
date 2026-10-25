"""תקרה לכל מה שאינו אימג' (‏#1737): מחיקה אוטומטית פעם ביום.

נדב (18/10): "איך דואגים שהשרת לא יתמלא בדברים לא חיוניים, ושעוד חצי שנה
הוא לא יפסיק לעבוד?" — והכרעתו: מחיקה **אוטומטית** של ישן, לא פעולה ידנית.
עד כאן אף טבלה לא נמחקה מעצמה (חוץ מ-`console_sessions` שפגו), והטבלאות
שגדלות עם כל hello, כל אתחול וכל גל — גדלו בלי גבול.

**איפה זה רץ:** בשעון החדר הקיים (‏`app._room_clock`, ‏#456) — לא cron ולא
תהליכון חדש. הפעם הראשונה `FIRST_RUN_S` אחרי העלייה (שרת שמתעדכן כל יום
אחרת לא היה מגיע לסיבוב), ואחריה כל `EVERY_S`.

**מה לעולם לא נמחק:** אימג'ים (עיקרון 3 — הסחיפה ב-`work_areas` נוגעת רק
בשמות שמתחילים בנקודה), והשורה **האחרונה** של כל מה שהקוד קורא כ"מצב
נוכחי". לכל טבלה למטה נבדק מי קורא אותה, וכלל השמירה נגזר מהקורא — מחיקה
שמשנה התנהגות אסורה:

* ‏`journal` — **שנה** (‏`JOURNAL_DAYS`). יומן ביקורת. הקוראים הפנימיים
  (‏`room.py`: ‏`room_rewake`/‏`room_rejoin` לגל מסוים) מחפשים אירועים של
  הגל הנוכחי — דקות, לא חודשים.
* ‏`machine_probe`, ‏`machine_inventory` — **גרסאות** (שורה חדשה רק כשהתוכן
  השתנה). ‏`latest`/‏`latest_all` קוראים את השורה האחרונה לכל MAC — היא
  **לעולם** אינה נמחקת. גרסה ישנה נמחקת כשהגרסה שאחריה כבר נראתה לפני יותר
  מ-90 יום (כלומר היא הפסיקה להיות נוכחית לפני 90 יום) — ‏`history` שומר
  שלושה חודשים.
* ‏`boot_steps`, ‏`boot_attempts`, ‏`agent_loops`, ‏`off_vlan_contacts` — שורה
  למכונה, "התמונה החיה בלבד" (ההגדרה ב-db.py). שורה ששקטה 90 יום אינה
  מתארת שום אתחול חי; ‏`boot_attempts` מתאפס ממילא בכל הקשר חדש.
* ‏`disk_events` — 90 יום, **חוץ** מהשורה ש-`room_precheck.crc_baseline`
  קורא לכל סידורי (מונה ה-CRC בסבב האחרון שבו נקרא) — היא הבסיס ל"עלה מאז".
* ‏`disk_failures` — רק רשומה **שנוקתה** (‏`cleared_at`) לפני 90 יום. רשומה
  פתוחה צובעת דיסק (כתום, #1580) ולא נמחקת לעולם.
* ‏`drive_rates` — 90 יום, **חוץ** מ-`WINDOW` הדגימות האחרונות לכל כונן
  (‏`_limit_of` — צבע/האטה לכונן איטי) ומכל דגימה של גל שעוד בזיכרון הקצב.
* ‏`wave_rates` — 90 יום, **חוץ** מ-`MEMORY_SCAN` הגלים האחרונים — בסיס הקצב
  האוטומטי (‏`choose`) לא מסתכל רחוק מזה.
* ‏`login_attempts` — מפתח ששקט 90 יום **ושההשהיה/הנעילה שלו כבר פגו**. זה
  השינוי היחיד שאינו אפס: תוקף ששתק 90 יום מתחיל שוב מ-5 ניסיונות בלי השהיה.
  בלעדיו הטבלה גדלה לכל צירוף שם-משתמש+IP שנוסה אי פעם.
* ‏`mfa_challenges` — אתגר שפג (‏`expires_at`). אינו שמיש ממילא.
* ‏`port_history` (‏#1844) — 90 יום, **חוץ** מ-`learn_ports.WINDOW` התצפיות האחרונות
  לכל חיבור ולכל דיסק — מהן `learn_ports.verdict` מכריע "כבל" או "דיסק".
* ‏`model_speeds` (‏#1845) — 90 יום, **חוץ** מ-`learn_models.WINDOW` המדידות האחרונות
  לכל דגם (הבסיס) והאחרונה של כל דיסק (מה שהוא נמדד בפעם הקודמת).
* ‏`wol_wakes` (‏#1847) — 90 יום, **חוץ** מ-`learn_wol.WINDOW` ההערות האחרונות לכל מכונה.
* ‏`boot_step_times` (‏#1848) — 90 יום, **חוץ** מ-`learn_boot.WINDOW`+1 המדידות האחרונות לכל
  (מכונה, שלב) — הבסיס והאחרונה.
* ‏`agent_logs` (‏#1882) — 90 יום. התקרה במספר שורות (למכונה ובסך הכול) — בכל הכנסה.
* ‏`restore_stages` (‏#1883) — 90 יום.
* ‏`server_io` (‏#1884) — 90 יום.
* ‏`storage_usage` (‏#1886) — ‏`storage_history.KEEP_DAYS` (400) יום; ‏1000 דגימות למיקום בכל הכנסה.
  **וגם** הדגימה היומית עצמה: ‏`run` דוגם כל מיקום מחובר (‏`storage_history.sample_all`).
* ‏`pxe_events` (‏#1887) — ‏`pxe_log.KEEP_DAYS` (14) יום; ‏20000 שורות בכל הכנסה.
* ‏`ip_history` (‏#1888) — 365 יום, 50 למכונה; ‏`nic_history` — 90 יום, 500 למכונה (בכל הכנסה).

**מה במכוון לא כאן:** טבלאות הפעולות (‏`tasks`, ‏`sessions`,
‏`session_members`, ‏`room_rounds`, ‏`storage_transfers`, ‏`shrink_records`,
‏`drawer_cancels`, ‏`smart_asks`, ‏`power_requests`) — הן גדלות בשורה לכל
**פעולה של אדם** (קליטה, גל, העברה), לא לכל hello; ‏`work_areas` נשען על
שורת המשימה כראיה; ורשומת כיווץ פתוחה היא הדרך היחידה לדעת שדיסק בנייה
נשאר מכווץ (#926). ושורה אחת לכל מכונה (‏`net_devices`, ‏`machines`) תחומה
במספר המכונות.

כל מחיקה נרשמת ביומן במספרים (‏`retention_purged`), וכשל בטבלה אחת נרשם
בשמו (‏`retention_failed`) ואינו עוצר את השאר — ולא נבלע.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import drive_rates, learn_boot, learn_models, learn_ports, learn_wol, login_guard, room_precheck, work_areas
from . import pxe_log, storage_history   # #1886, #1887
from .db import _write_lock, journal, writing

log = logging.getLogger("imagectl.housekeeping")

#: פעם ביום — ברירת המחדל שנדב קבע.
EVERY_S = 24 * 3600
#: ההרצה הראשונה אחרי העלייה: אחרי שהשרת התייצב, לא בתוך העלייה עצמה.
FIRST_RUN_S = 10 * 60
#: טבלאות המדידה והמצב החי — ‏90 יום (נדב, 18/10).
RETENTION_DAYS = 90
#: יומן האירועים — שנה. יומן ביקורת (נדב, 18/10).
JOURNAL_DAYS = 365

_CRC_READ = (*room_precheck._CRC_READ_VERDICTS, *room_precheck._CRC_READ_FAIL_REASONS)   # #2083


def _iso(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).isoformat(timespec="seconds")


def _purge_login_attempts(conn, now_ts: float) -> int:
    """מפתח ששקט `RETENTION_DAYS` **וגם** אין עליו נעילה או השהיה בתוקף.
    החישוב ב-Python: ‏2 בחזקת מספר הכשלונות אינו בטוח ב-SQL."""
    cutoff = now_ts - RETENTION_DAYS * 86400
    doomed = []
    for row in conn.execute(
            "SELECT key, failures, locked_until, last_at FROM login_attempts"
            " WHERE last_at < ?", (cutoff,)):
        if row["locked_until"] is not None and row["locked_until"] > now_ts:
            continue
        failures = row["failures"]
        if failures >= login_guard.DELAY_AFTER and \
                row["last_at"] + 2 ** (failures - login_guard.DELAY_AFTER) > now_ts:
            continue
        doomed.append((row["key"],))
    if not doomed:
        return 0
    with _write_lock, writing(conn):
        conn.executemany("DELETE FROM login_attempts WHERE key = ?", doomed)
    return len(doomed)


def _rules(now: datetime) -> list[tuple[str, str, tuple]]:
    """(שם, SQL, פרמטרים) לכל טבלה. ‏`julianday` ולא השוואת מחרוזות: חותמת
    שאינה ISO נקראת NULL — וההשוואה נכשלת לכיוון **השמירה**."""
    old = _iso(now, RETENTION_DAYS)
    qual = ("{t}.serial IS NOT NULL AND ({t}.verdict IN (?, ?)"
            " OR ({t}.verdict = 'fail' AND COALESCE({t}.reason, '') IN (?, ?))"   # #2083
            " OR ({t}.verdict = 'unchecked' AND COALESCE({t}.reason, '') = 'no_health'))")
    return [
        ("journal", "DELETE FROM journal WHERE julianday(ts) < julianday(?)",
         (_iso(now, JOURNAL_DAYS),)),
        *[(table,
           f"DELETE FROM {table} WHERE id IN (SELECT id FROM (SELECT id, LEAD(seen_at)"
           f" OVER (PARTITION BY mac ORDER BY id) AS next_seen FROM {table})"
           " WHERE next_seen IS NOT NULL AND julianday(next_seen) < julianday(?))", (old,))
          for table in ("machine_probe", "machine_inventory")],
        ("boot_steps", "DELETE FROM boot_steps WHERE julianday(at) < julianday(?)", (old,)),
        *[(table, f"DELETE FROM {table} WHERE julianday(last_at) < julianday(?)", (old,))
          for table in ("boot_attempts", "agent_loops", "off_vlan_contacts")],
        ("disk_events",
         "DELETE FROM disk_events WHERE julianday(updated_at) < julianday(?) AND NOT ("
         + qual.format(t="disk_events")
         + " AND disk_events.updated_at = (SELECT MAX(e.updated_at) FROM disk_events e"
           " WHERE e.serial = disk_events.serial AND " + qual.format(t="e") + "))",
         (old, *_CRC_READ, *_CRC_READ)),
        # ‏#1859: הסבבים הישנים של כל סידורי — חוץ מהאחרון, שממנו נלמד "עלה מאז הסבב הקודם".
        ("smart_history",
         "DELETE FROM smart_history WHERE julianday(at) < julianday(?)"
         " AND at < (SELECT MAX(h.at) FROM smart_history h WHERE h.serial = smart_history.serial)", (old,)),
        ("disk_failures",
         "DELETE FROM disk_failures WHERE cleared_at IS NOT NULL"
         " AND julianday(cleared_at) < julianday(?)", (old,)),
        ("drive_rates",
         "DELETE FROM drive_rates WHERE julianday(at) < julianday(?)"
         " AND id NOT IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER"
         " (PARTITION BY serial, model ORDER BY id DESC) AS rn FROM drive_rates) WHERE rn <= ?)"
         " AND session_id NOT IN (SELECT session_id FROM wave_rates ORDER BY rowid DESC LIMIT ?)",
         (old, drive_rates.WINDOW, drive_rates.MEMORY_SCAN)),
        ("wave_rates",
         "DELETE FROM wave_rates WHERE julianday(at) < julianday(?)"
         " AND rowid NOT IN (SELECT rowid FROM wave_rates ORDER BY rowid DESC LIMIT ?)",
         (old, drive_rates.MEMORY_SCAN)),
        # ‏#1844: ‏`learn_ports.verdict` קורא את WINDOW התצפיות האחרונות לכל חיבור ולכל דיסק.
        ("port_history",
         "DELETE FROM port_history WHERE julianday(at) < julianday(?)"
         " AND rowid NOT IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER"
         " (PARTITION BY mac, port ORDER BY at DESC) AS rn FROM port_history) WHERE rn <= ?)"
         " AND rowid NOT IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER"
         " (PARTITION BY serial ORDER BY at DESC) AS rn FROM port_history) WHERE rn <= ?)",
         (old, learn_ports.WINDOW, learn_ports.WINDOW)),
        # ‏#1845: ‏`learn_models` קורא את WINDOW המדידות האחרונות לכל דגם, ואת האחרונה לכל דיסק.
        ("model_speeds",
         "DELETE FROM model_speeds WHERE julianday(at) < julianday(?)"
         " AND rowid NOT IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER"
         " (PARTITION BY model ORDER BY at DESC) AS rn FROM model_speeds) WHERE rn <= ?)"
         " AND rowid NOT IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER"
         " (PARTITION BY serial ORDER BY at DESC) AS rn FROM model_speeds) WHERE rn = 1)",
         (old, learn_models.WINDOW)),
        # ‏#1847: ‏`learn_wol.stats` קורא את WINDOW ההערות האחרונות לכל מכונה.
        ("wol_wakes",
         "DELETE FROM wol_wakes WHERE julianday(sent_at) < julianday(?)"
         " AND id NOT IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER"
         " (PARTITION BY mac ORDER BY sent_epoch DESC) AS rn FROM wol_wakes) WHERE rn <= ?)",
         (old, learn_wol.WINDOW)),
        # ‏#1848: ‏`learn_boot.regression` קורא את WINDOW+1 האחרונות לכל (מכונה, שלב).
        ("boot_step_times",
         "DELETE FROM boot_step_times WHERE julianday(at) < julianday(?)"
         " AND rowid NOT IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER"
         " (PARTITION BY mac, step ORDER BY rowid DESC) AS rn FROM boot_step_times) WHERE rn <= ?)",
         (old, learn_boot.WINDOW + 1)),
        # ‏#1882: התקרה במספר שורות נאכפת בכל הכנסה (`agent_logs._trim`); כאן — הזמן.
        ("agent_logs", "DELETE FROM agent_logs WHERE julianday(at) < julianday(?)", (old,)),
        # ‏#1883: ‏`learn_eta` קורא רק את הגלים בחלון שלו — 90 יום מספיקים.
        ("restore_stages", "DELETE FROM restore_stages WHERE julianday(at) < julianday(?)", (old,)),
        # ‏#1884: גל/קליטה — הצילום והתוצאה; ‏`bound` נקרא רק לגלים בחלון של הלמידה.
        ("server_io", "DELETE FROM server_io WHERE julianday(started_at) < julianday(?)", (old,)),
        # ‏#1886: ההיסטוריה של המקום הפנוי — שנה ומעט (מגמה עונתית); התקרה בשורות בכל הכנסה.
        ("storage_usage", "DELETE FROM storage_usage WHERE julianday(at) < julianday(?)",
         (_iso(now, storage_history.KEEP_DAYS),)),
        # ‏#1887: לוג ה-DHCP/PXE — 14 יום (ההכרעה היא על העלייה האחרונה); התקרה בשורות בכל הכנסה.
        ("pxe_events", "DELETE FROM pxe_events WHERE julianday(at) < julianday(?)",
         (_iso(now, pxe_log.KEEP_DAYS),)),
        # ‏#1888: היסטוריית IP — שנה; מוני הכרטיס — 90 יום. התקרה בשורות בכל הכנסה.
        ("ip_history", "DELETE FROM ip_history WHERE julianday(at) < julianday(?)", (_iso(now, 365),)),
        ("nic_history", "DELETE FROM nic_history WHERE julianday(at) < julianday(?)", (old,)),
        ("mfa_challenges", "DELETE FROM mfa_challenges WHERE expires_at < ?",
         (now.timestamp(),)),
    ]


def purge_tables(conn, now: datetime | None = None) -> dict:
    """מוחק לפי `_rules`, טבלה-טבלה (טרנזאקציה לכל אחת). ‏{"deleted": {טבלה:
    שורות}, "failed": {טבלה: סיבה}}. ‏0 שורות אינו נרשם ב-deleted."""
    now = now or datetime.now(timezone.utc)
    deleted: dict[str, int] = {}
    failed: dict[str, str] = {}
    for table, sql, params in _rules(now):
        try:
            with _write_lock, writing(conn):
                count = conn.execute(sql, params).rowcount
        except Exception as exc:                             # noqa: BLE001
            failed[table] = f"{type(exc).__name__}: {exc}"
            continue
        if count:
            deleted[table] = count
    try:
        count = _purge_login_attempts(conn, now.timestamp())
    except Exception as exc:                                 # noqa: BLE001
        failed["login_attempts"] = f"{type(exc).__name__}: {exc}"
    else:
        if count:
            deleted["login_attempts"] = count
    if deleted:
        journal(conn, "retention_purged",
                " ".join(f"{t}={n}" for t, n in deleted.items())
                + f" (מדידה/מצב: {RETENTION_DAYS} יום · יומן: {JOURNAL_DAYS} יום)")
    if failed:
        journal(conn, "retention_failed",
                "; ".join(f"{t}: {why}" for t, why in failed.items()))
    return {"deleted": deleted, "failed": failed}


def run(conn, roots: Callable[[], list[Path]], now: datetime | None = None) -> dict:
    """הסיבוב היומי: הטבלאות, ואזורי העבודה בכל שורש ספרייה זמין. לעולם אינו
    זורק — כשל נרשם ביומן, והסיבוב הבא ינסה שוב."""
    result: dict = {"tables": None, "work_areas": {}}
    try:
        result["tables"] = purge_tables(conn, now)
    except Exception:                                        # noqa: BLE001
        log.exception("retention purge failed")
    result["storage_samples"] = storage_history.sample_all(conn, "daily")   # ‏#1886; אינו זורק
    try:
        paths = roots()
    except Exception as exc:                                 # noqa: BLE001
        journal(conn, "retention_failed", f"שורשי הספרייה לא נקראו: {exc}")
        return result
    seen: set[str] = set()
    for path in paths:
        key = str(Path(path).resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            result["work_areas"][str(path)] = work_areas.sweep(
                conn, path, now.timestamp() if now else None)
        except Exception as exc:                             # noqa: BLE001
            journal(conn, "retention_failed", f"אזורי העבודה ב-{path}: {exc}")
    return result


class Schedule:
    """מתי הסיבוב הבא, מתוך הדקות של שעון החדר — בלי שעון קיר: שינוי שעון
    המערכת (‏#1088, אזור זמן) אינו מקפיץ ואינו מדלג סיבוב."""

    def __init__(self, first: float = FIRST_RUN_S, every: float = EVERY_S):
        self.every = every
        self._left = first

    def due(self, elapsed: float) -> bool:
        self._left -= elapsed
        if self._left > 0:
            return False
        self._left = self.every
        return True
