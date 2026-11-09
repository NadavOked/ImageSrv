"""‏#1845 — מהירות לכל דגם דיסק: "איטי מהרגיל לדגם שלו".

הכרעת נדב 19/10: השרת לומד **ממדידה שלו**, ולעולם אינו חוסם בגלל הלמידה.

עד כאן הסף לבדיקת הכתיבה שלפני הזרם (‏`write_preflight`, ‏#1366) היה **קבוע** —
‏50MB/s לכל דיסק (‏`WRITECHECK_MIN_MBPS` בסוכן; VCLONE-3 נפסל ב-29/35). דיסק
שכותב 60MB/s עובר אותו גם כשכל הדיסקים מאותו דגם כותבים 400 — וזה בדיוק
הדיסק שעומד להיכשל. **האדום המוחלט לא משתנה** (הכרעת נדב); זו **תוספת**:

    בסיס הדגם = חציון `mbps` של עד `WINDOW` המדידות האחרונות של אותו `model`,
                **מדיסקים אחרים** (סידורי אחר) — דיסק אינו הבסיס של עצמו
    כתום       = ה-`mbps` של הדיסק < `SLOW_RATIO` × בסיס הדגם

בלי בסיס — פחות מ-`MIN_SAMPLES` מדידות, או מפחות מ-`MIN_SERIALS` דיסקים אחרים
— ‏`slow: None` ("אין בסיס לדגם"), ורק הסף המוחלט חל. "לא נמדד" אינו "תקין".

**מה נרשם** (‏`model_speeds`, שורה לכל (גל, סידורי)): ה-`mbps` של
‏`targets[].write_preflight` שהסוכן דיווח (‏`state` ‏`ok` או `slow` — מדידה
שהושלמה; ‏`failed`/`unchecked` אינם קצב), ו-`model` מה-hello של המכונה.
נכתב בסוף כל גל של החדר (‏`room._finish_wave`).

מה שנשמר: דגם, סידורי ומספרים בלבד (#1924). לעולם אינו זורק.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics

from server.db import _write_lock, journal, now_iso, writing
from server.disks import writefloor

log = logging.getLogger("imagesrv.learn_models")

#: כמה מדידות אחרונות של הדגם נחשבות לבסיס.
WINDOW = 10
#: מתחת לאיזה חלק מבסיס הדגם הדיסק כתום. ⚠️ ניחוש מנומק, לא מדידה — לכייל
#: על ברזל: הסוכן כבר פוסל (אדום) מגירה מתחת ל**חצי** מחציון שאר המגירות
#: באותה מכונה (‏#1366), ולכן הכתום מתחיל לפני האדום — ב-70%.
SLOW_RATIO = 0.7
#: בסיס דורש לפחות כמה מדידות, ומלפחות כמה דיסקים אחרים — מדידה אחת, או דיסק
#: אחד שנמדד שלוש פעמים, הם תכונה של דיסק ולא של דגם.
MIN_SAMPLES = 3
MIN_SERIALS = 2

SLOW_HE = "איטי מהרגיל לדגם — {mbps} MB/s מול חציון {median} MB/s ל-{model} ({n} מדידות)"

#: ‏#447: מול הסף שבתוקף — `writefloor.mbps`, אותה פונקציה שממלאת את המניפסט שהסוכן אוכף
#: (‏#2338, ההגדרה `writecheck_min_mbps`).
LAST_SLOW_HE = "בבדיקת הכתיבה הקודמת נמדד איטי — {mbps} MB/s, מתחת לסף {floor} MB/s"


def _mbps(pre: object) -> int | None:
    if not isinstance(pre, dict) or pre.get("state") not in ("ok", "slow"):
        return None
    mbps = pre.get("mbps")
    return mbps if isinstance(mbps, int) and not isinstance(mbps, bool) and mbps > 0 else None


def record_wave(conn: sqlite3.Connection, session_id: str, members) -> int:
    """נקרא כשגל החדר הסתיים. מחזיר כמה נרשמו; לעולם אינו זורק.

    מדידה שהושלמה לדיסק בלי `model` או בלי `serial` ב-hello אינה נשמרת (#2532), ונרשמת
    ביומן `model_speed_dropped` — לא נרשמת עם דגם ריק: שורה כזו הייתה "דגם" אחד לכל הדיסקים
    האלמוניים ומזהמת את הבסיס של `judge` ושל הלמידה (`tuning._model_base`)."""
    try:
        rows, dropped = [], []
        for member in members:
            got = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?",
                               (member["mac"],)).fetchone()
            disks = json.loads(got["disks_json"] or "[]") if got else []
            by_dev = {d.get("dev"): d for d in disks if isinstance(d, dict)}
            for t in json.loads(member["targets_json"] or "[]"):
                if not isinstance(t, dict):
                    continue
                mbps = _mbps(t.get("write_preflight"))
                disk = by_dev.get(t.get("dev")) or {}
                serial, model = disk.get("serial"), disk.get("model")
                if mbps is None:
                    continue
                if not serial or not model:
                    # ‏#2532: מדידה שהושלמה ואין לאן לשמור אותה (`model` ו-`serial` NOT NULL) —
                    # נזרקת **בגלוי**, כדי שהבדיקה המקדימה ("לא נמדד") לא תסתיר מדידה שהייתה.
                    dropped.append((member["mac"], t.get("dev"), serial, mbps))
                    continue
                if t.get("serial") and t["serial"] != serial:
                    continue          # הדיסק הוחלף מאז — אין ייחוס
                rows.append((session_id, serial, model, member["mac"], mbps, now_iso()))
        written = 0
        if rows:
            with _write_lock, writing(conn):
                written = sum(conn.execute(
                    "INSERT OR REPLACE INTO model_speeds (session_id, serial, model, mac, mbps, at)"
                    " VALUES (?,?,?,?,?,?)", row).rowcount for row in rows)
    except Exception:             # noqa: BLE001 — הלמידה אינה עוצרת סיום גל
        log.exception("recording model speeds of %s failed", session_id)
        return 0
    for mac, dev, serial, mbps in dropped:
        try:
            journal(conn, "model_speed_dropped",
                    f"{dev} serial={serial or '-'} {mbps} MB/s: {'no serial' if not serial else 'no model'}",
                    machine=mac, round_id=session_id)
        except Exception:         # noqa: BLE001 — שורת יומן שנכשלה אינה מבטלת את מה שנשמר
            log.exception("journaling a dropped model speed of %s failed", session_id)
    return written


def last_mbps(conn: sqlite3.Connection, serial: str | None) -> int | None:
    """המדידה האחרונה של הדיסק הזה, או None — לא נמדד מעולם."""
    if not serial:
        return None
    row = conn.execute("SELECT mbps FROM model_speeds WHERE serial = ?"
                       " ORDER BY at DESC, rowid DESC LIMIT 1", (serial,)).fetchone()
    return row["mbps"] if row else None


def last_check(conn: sqlite3.Connection, serial: str | None) -> dict:
    """‏#447: ‏`{"mbps", "slow", "text_he"}` — המדידה האחרונה של הדיסק מול הסף שבתוקף.

    ברזל 03/11: דיסק שנפסל "איטי — 33 MB/s" לא הוזהר בסבב הבא — ‏`judge` שופט רק מול
    הדגם, ובלי בסיס לדגם שתק. הסף — ‏`writefloor.mbps`, מה שהמניפסט של הסבב הבא ימסור
    לסוכן (‏#2338). ‏`slow`: ‏True — מתחתיו (הסוכן: ‏`-lt`); ‏False — נמדד ואינו; ‏None —
    לא נמדד מעולם, או שהשאילתה נכשלה ("לא נבדק", לא "תקין").
    ⚠️ הכלל היחסי של הסוכן (חצי מחציון המגירות מאותו סוג במכונה) אינו נשמר כאן."""
    out = {"mbps": None, "slow": None, "text_he": None}
    try:
        mbps = last_mbps(conn, serial)
        floor = writefloor.mbps(conn) if mbps is not None else None
    except Exception:             # noqa: BLE001 — מסך החדר אינו נופל בגלל הלמידה
        log.exception("last write check of %s failed", serial)
        return out
    if mbps is None:
        return out
    slow = mbps < floor
    return {"mbps": mbps, "slow": slow,
            "text_he": LAST_SLOW_HE.format(mbps=mbps, floor=floor) if slow else None}


def judge(conn: sqlite3.Connection, model: str | None, serial: str | None,
          mbps: int | None) -> dict:
    """‏`{"mbps", "median", "samples", "slow", "text_he"}` לדיסק אחד.

    ‏`mbps` — המדידה של הגל הזה, ובלעדיה האחרונה של הסידורי (‏`last_mbps`).
    ‏`slow`: ‏True — מתחת ל-`SLOW_RATIO` מהבסיס; ‏False — נבדק ואינו; ‏None — אין
    מדידה לדיסק, אין בסיס לדגם, או שהשאילתה נכשלה ("לא נבדק", לא "תקין")."""
    out = {"mbps": None, "median": None, "samples": 0, "slow": None, "text_he": None}
    try:
        if mbps is None:
            mbps = last_mbps(conn, serial)
        out["mbps"] = mbps
        if not model:
            return out
        rows = conn.execute(
            "SELECT serial, mbps FROM model_speeds WHERE model = ? AND serial IS NOT ?"
            " ORDER BY at DESC, rowid DESC LIMIT ?", (model, serial, WINDOW)).fetchall()
        out["samples"] = len(rows)
        if len(rows) < MIN_SAMPLES or len({r["serial"] for r in rows}) < MIN_SERIALS:
            return out
        median = statistics.median(r["mbps"] for r in rows)
        out["median"] = int(median)
        if mbps is None:
            return out
        out["slow"] = mbps < SLOW_RATIO * median
        if out["slow"]:
            out["text_he"] = SLOW_HE.format(mbps=mbps, median=int(median), model=model,
                                            n=len(rows))
        return out
    except Exception:             # noqa: BLE001
        log.exception("model speed of %s failed", serial)
        return {**out, "slow": None, "text_he": None}
