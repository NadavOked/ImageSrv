"""‏#1848 — זמן אתחול לכל מכונה ולכל שלב: זיהוי רגרסיה, ולפני/אחרי עדכון.

הכרעת נדב 19/10: השרת לומד **ממדידה שלו**, ושום דבר אינו נחסם בגלל הלמידה.

הראיה ב-Issue: ‏10.7 שניות של ACPI ‏`_PSL` ב-HP G6 נמצאו **ביד** (‏`thermal.off`).
שביל הפירורים (#400) כבר רושם כל שלב, אבל רק את **האחרון** לכל מכונה
(‏`boot_steps`) — השלב שהתארך לא השאיר עקבה.

**מה נמדד** (‏`boot_step_times`, שורה לכל מעבר): כש-`boottrace.record` רושם שלב
מהשרשרת (‏`boot.trace.STEPS`) **שבא מיד אחרי** השלב הקודם של אותה מכונה, באותו
אתחול — ‏`seconds` = מהפירור הקודם לזה, **בשעון השרת** (‏`boot_steps.at_epoch`,
שבר שנייה). השלב נקרא בשם השלב **שהגיע**: "‏`pre-initrd` ארך 12ש'" = טעינת הקרנל.

**לא נמדד:** ‏`entry` — המעבר מ-`menu` כולל את ההמתנה לאדם בתפריט (מחשב בנייה /
תלמיד, ‏#140); שלב שדילג על קודמו (פירור שאבד — אין ממה למדוד); והענף המקומי (#416).
סמני `up=` של `agent/init` (‏#1452) הם שעון של המכונה ואינם נשמרים — רק נרשמים ב-log.

**רגרסיה** (‏`regression`): המעבר האחרון ארך ≥ `FACTOR` × הבסיס **וגם** לפחות
`MIN_DELTA_S` יותר ממנו. הבסיס = חציון עד `WINDOW` המדידות **הקודמות** של אותו
(MAC, שלב); בלי `MIN_SAMPLES` — אין בסיס ("לא נבדק"). נרשם ביומן `boot_step_slow`
ומוצג במסך הבריאות עם שם השלב.

**לפני/אחרי עדכון** (‏`version_compare`): לכל שלב — חציון כל המכונות בגרסת השרת
הנוכחית מול הגרסה שלפניה (‏`agent_version.server_version` בזמן המדידה — השרת מגיש
את ה-GRUB, הקרנל וה-initramfs). שלב שהתארך פי `FACTOR` ויותר מ-`MIN_DELTA_S` — כתום.

מה שנשמר: MAC, שם שלב, שניות וגרסה (#1924). לעולם אינו זורק.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics

from boot.trace import STEP_INDEX, STEPS

from server.db import journal, now_iso

log = logging.getLogger("imagesrv.learn_boot")

#: פי כמה מהבסיס — ההגדרה ב-Issue.
FACTOR = 2
#: תוספת מוחלטת מינימלית. ⚠️ ניחוש מנומק: רוב המעברים הם שבר שנייה (משיכת מודול,
#: ‏`insmod`), ושם "פי 2" הוא רעש של הרשת; ‏3ש' הם פחות משליש מ-10.7ש' של `_PSL`.
MIN_DELTA_S = 3.0
#: כמה מדידות קודמות לכל (MAC, שלב) בבסיס, וכמה נדרשות לפחות.
WINDOW = 10
MIN_SAMPLES = 3
#: המעבר אל `entry` כולל את ההמתנה לאדם בתפריט — אינו זמן אתחול.
SKIPPED = frozenset({"entry"})


def version() -> str | None:
    """גרסת השרת (נקראת פעם אחת לתהליך, ‏`lru_cache`). נקרא **לפני** הנעילה."""
    try:
        from server.core.agent_version import server_version   # noqa: PLC0415
        return server_version()
    except Exception:             # noqa: BLE001 — גרסה שלא נקראה = None, לא "אותה גרסה"
        log.exception("server version unreadable")
        return None


def measure(conn: sqlite3.Connection, mac: str, step: str, prev: sqlite3.Row | None,
            now_epoch: float, server_version: str | None = None) -> float | None:
    """נקרא מתוך הכתיבה של `boottrace.record` (כבר תחת `_write_lock`), **לפני**
    שהשורה החיה נדרסת. ‏`prev` — השורה הקודמת של המכונה; ‏`server_version` — מ-`version()`.
    מחזיר את השניות שנרשמו, או None (אין מעבר שנמדד)."""
    if (prev is None or step not in STEPS or step in SKIPPED or prev["step"] not in STEPS
            or STEP_INDEX[step] != prev["idx"] + 1 or prev["at_epoch"] is None):
        return None
    seconds = now_epoch - prev["at_epoch"]
    if seconds < 0:
        return None               # שעון שקפץ אחורה — אינו מדידה
    conn.execute("INSERT INTO boot_step_times (mac, step, seconds, version, at)"
                 " VALUES (?,?,?,?,?)", (mac, step, seconds, server_version, now_iso()))
    return seconds


def regression(conn: sqlite3.Connection, mac: str, step: str) -> dict:
    """המדידה האחרונה של (MAC, שלב) מול הבסיס שלה: ‏`{"seconds", "baseline",
    "samples", "slow"}` — ‏`slow` ‏None = אין מדידה או אין בסיס."""
    rows = conn.execute(
        "SELECT seconds FROM boot_step_times WHERE mac = ? AND step = ?"
        " ORDER BY rowid DESC LIMIT ?", (mac, step, WINDOW + 1)).fetchall()
    out = {"seconds": rows[0]["seconds"] if rows else None, "baseline": None,
           "samples": max(0, len(rows) - 1), "slow": None}
    if len(rows) - 1 < MIN_SAMPLES:
        return out
    base = statistics.median(r["seconds"] for r in rows[1:])
    out["baseline"] = base
    out["slow"] = out["seconds"] >= FACTOR * base and out["seconds"] - base >= MIN_DELTA_S
    return out


def after_record(conn: sqlite3.Connection, mac: str, step: str, seconds: float | None) -> None:
    """אחרי שהמעבר נרשם (מחוץ לנעילה): רגרסיה → `boot_step_slow`. לעולם אינו זורק."""
    if seconds is None:
        return
    try:
        got = regression(conn, mac, step)
        if got["slow"]:
            journal(conn, "boot_step_slow",
                    f"{mac} {step} {got['seconds']:.1f}s baseline={got['baseline']:.1f}s"
                    f" ({got['samples']} boots)")
    except Exception:             # noqa: BLE001
        log.exception("boot regression for %s %s failed", mac, step)


def version_compare(conn: sqlite3.Connection) -> dict | None:
    """‏`{"now", "before", "steps": [{"step", "before_s", "now_s", "slow"}]}` — לכל שלב
    עם `MIN_SAMPLES` מדידות בשתי הגרסאות. ‏None — אין שתי גרסאות שנמדדו."""
    versions = [r["version"] for r in conn.execute(
        "SELECT version, MAX(rowid) AS last FROM boot_step_times WHERE version IS NOT NULL"
        " GROUP BY version ORDER BY last DESC LIMIT 2")]
    if len(versions) < 2:
        return None
    now_v, before_v = versions
    steps = []
    for step in STEPS:
        sides = []
        for version in (before_v, now_v):
            got = [r["seconds"] for r in conn.execute(
                "SELECT seconds FROM boot_step_times WHERE version = ? AND step = ?"
                " ORDER BY rowid DESC LIMIT ?", (version, step, WINDOW * 5))]
            sides.append(statistics.median(got) if len(got) >= MIN_SAMPLES else None)
        if None in sides:
            continue
        before_s, now_s = sides
        steps.append({"step": step, "before_s": before_s, "now_s": now_s,
                      "slow": now_s >= FACTOR * before_s and now_s - before_s >= MIN_DELTA_S})
    return {"now": now_v, "before": before_v, "steps": steps}


def health_checks(conn: sqlite3.Connection) -> list[dict]:
    """שורה כתומה לכל מכונה שהמעבר האחרון באחד השלבים שלה ארך פי 2 מהבסיס,
    ושורת לפני/אחרי עדכון. בלי מדידות — אין שורה."""
    from server.agent.boottrace import STEP_LABELS          # noqa: PLC0415 — boottrace מייבא אותנו
    from server.system.health import check                   # noqa: PLC0415
    try:
        pairs = conn.execute("SELECT DISTINCT mac, step FROM boot_step_times"
                             " ORDER BY mac, step").fetchall()
        if not pairs:
            return []
        names = {r["mac"]: r["suffix"] for r in conn.execute("SELECT mac, suffix FROM machines")}
        out, slow_by_mac = [], {}
        for p in pairs:
            got = regression(conn, p["mac"], p["step"])
            if got["slow"]:
                slow_by_mac.setdefault(p["mac"], []).append(
                    f"'{STEP_LABELS.get(p['step'], p['step'])}' ארך {got['seconds']:.1f}ש'"
                    f" מול בסיס {got['baseline']:.1f}ש' ({got['samples']} אתחולים)")
        for mac, parts in slow_by_mac.items():
            out.append(check(f"boot_slow:{mac}", names.get(mac) or mac, "warn",
                             "האתחול התארך — " + " · ".join(parts)))
        if not out:
            out.append(check("boot_slow", "זמן אתחול", "ok",
                             "אף שלב אתחול לא התארך פי 2 מהבסיס של המכונה"))
        compared = version_compare(conn)
        if compared is not None:
            label = f"זמן אתחול לפני/אחרי {compared['now'] or '?'}"
            slow = [s for s in compared["steps"] if s["slow"]]
            if not compared["steps"]:
                out.append(check("boot_version", label, "off",
                                 f"לא נבדק — אין שלב עם {MIN_SAMPLES} מדידות בשתי הגרסאות"))
            elif slow:
                out.append(check("boot_version", label, "warn", " · ".join(
                    f"'{STEP_LABELS.get(s['step'], s['step'])}' {s['before_s']:.1f}ש' →"
                    f" {s['now_s']:.1f}ש'" for s in slow) + f" (מול {compared['before']})"))
            else:
                out.append(check("boot_version", label, "ok",
                                 f"{len(compared['steps'])} שלבים — אף אחד לא התארך מול"
                                 f" {compared['before']}"))
        return out
    except Exception:             # noqa: BLE001 — "לא בדקנו" אינו "תקין"
        log.exception("boot timing health failed")
        return [check("boot_slow", "זמן אתחול", "off", "לא נבדק — הקריאה נכשלה")]
