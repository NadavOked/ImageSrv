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

from server.db import _write_lock, now_iso, writing

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


def _mbps(pre: object) -> int | None:
    if not isinstance(pre, dict) or pre.get("state") not in ("ok", "slow"):
        return None
    mbps = pre.get("mbps")
    return mbps if isinstance(mbps, int) and not isinstance(mbps, bool) and mbps > 0 else None


def record_wave(conn: sqlite3.Connection, session_id: str, members) -> int:
    """נקרא כשגל החדר הסתיים. מחזיר כמה נרשמו; לעולם אינו זורק."""
    try:
        rows = []
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
                if mbps is None or not serial or not model:
                    continue
                if t.get("serial") and t["serial"] != serial:
                    continue          # הדיסק הוחלף מאז — אין ייחוס
                rows.append((session_id, serial, model, member["mac"], mbps, now_iso()))
        if not rows:
            return 0
        with _write_lock, writing(conn):
            return sum(conn.execute(
                "INSERT OR REPLACE INTO model_speeds (session_id, serial, model, mac, mbps, at)"
                " VALUES (?,?,?,?,?,?)", row).rowcount for row in rows)
    except Exception:             # noqa: BLE001 — הלמידה אינה עוצרת סיום גל
        log.exception("recording model speeds of %s failed", session_id)
        return 0


def last_mbps(conn: sqlite3.Connection, serial: str | None) -> int | None:
    """המדידה האחרונה של הדיסק הזה, או None — לא נמדד מעולם."""
    if not serial:
        return None
    row = conn.execute("SELECT mbps FROM model_speeds WHERE serial = ?"
                       " ORDER BY at DESC, rowid DESC LIMIT 1", (serial,)).fetchone()
    return row["mbps"] if row else None


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
