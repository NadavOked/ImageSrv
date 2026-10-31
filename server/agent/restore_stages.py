"""‏#1883 — ציר זמן לשלבי השחזור, לכל מכונה ודיסק: איפה הזמן הולך.

**המקור.** הסוכן (‏`agent/lib/core/datareport.sh: stage_at`) רושם לכל מגירה "השלב הזה מתחיל עכשיו" לפי
‏`/proc/uptime` של המכונה — ‏`gpt` (טבלת המחיצות), ‏`expand` (ההרחבה), ‏`p<N>` (הזרם של מחיצה N, כולל
ההמתנה לבייט הראשון שלה), ‏`readback` (הקריאה החוזרת, #1805), ‏`grow` (מתיחת מערכת הקבצים) ו-`end`.
ברגע שהיעד סופי (‏`done`/`failed`/`cancelled`) הדיווח נושא אותם **פעם אחת**: ‏`"stages": [[שם, uptime], …]`.

**מה נשמר.** ‏`clean` הופך את הסימנים למשכים — ‏`[{"stage", "seconds"}]` (כל שלב עד הבא אחריו; ‏`end`
סוגר) — וזה מה שנשמר ב-`targets_json` ומוצג. סימנים פגומים (שם לא מוכר, זמן שאינו מספר, זמן שחוזר
אחורה, יותר מ-`MAX_MARKS`) — **השדה נזנח כולו**: "לא נמדד", לא ציר זמן חלקי שנראה שלם. בלי `end` —
אין משך לשלב האחרון, ולכן גם הוא נזנח.

‏`record` — שורה ב-**`restore_stages`** לכל שלב של יעד סופי: ‏`session_id`, ‏`mac`, ‏`dev`, ‏`serial`
(מה-hello, ‏NULL = לא דווח), ‏`stage`, ‏`seconds`, ‏`at`. ממנה `learn_eta` (‏#1843) לומד את השלבים הקבועים.
השעון הוא של המכונה, ולכן אין כאן חותמת קיר — רק משכים (‏`at` הוא שעון השרת בזמן הקליטה).
חומרה ומדידות בלבד (#1924). ‏`housekeeping` — 90 יום. לעולם אינו זורק.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import statistics

from server.db import _settle, _write_lock, now_iso, writing

log = logging.getLogger("imagesrv.restore_stages")

#: שמות השלבים שהסוכן כותב. ‏`p<N>` = מחיצה N.
_NAME = re.compile(r"^(gpt|expand|p\d{1,3}|readback|grow|end)$")
#: תקרה — 128 מחיצות + חמישה שלבים קבועים.
MAX_MARKS = 140
TERMINAL = frozenset({"done", "failed", "cancelled"})


def clean(raw: object) -> list[dict] | None:
    """הסימנים מהסוכן → משכים, או None (פגום / בלי `end`). ראו את ראש המודול."""
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_MARKS:
        return None
    marks = []
    for item in raw:
        if (not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str)
                or not _NAME.match(item[0]) or isinstance(item[1], bool)
                or not isinstance(item[1], (int, float)) or not math.isfinite(item[1])
                or item[1] < 0):
            return None
        if marks and item[1] < marks[-1][1]:
            return None                         # שעון שחזר אחורה אינו מדידה
        marks.append((item[0], float(item[1])))
    if marks[-1][0] != "end" or any(name == "end" for name, _ in marks[:-1]):
        return None
    return [{"stage": name, "seconds": round(marks[i + 1][1] - t, 1)}
            for i, (name, t) in enumerate(marks[:-1])]


def clean_targets(targets: list) -> None:
    """במקום: ‏`stages` של כל יעד → משכים, או נזנח (‏`reports.ingest`, לפני השמירה)."""
    for t in targets:
        if isinstance(t, dict) and "stages" in t:
            got = clean(t.get("stages"))
            if got is None:
                t.pop("stages", None)
            else:
                t["stages"] = got


def record(conn: sqlite3.Connection, session_id: str, mac: str, t: dict,
           serial: str | None) -> int:
    """שורה לכל שלב של יעד סופי שנשא משכים. חוזר על עצמו בבטחה. לעולם אינו זורק."""
    try:
        stages, dev = t.get("stages"), t.get("dev")
        if t.get("state") not in TERMINAL or not isinstance(stages, list) or not isinstance(dev, str):
            return 0
        at = now_iso()
        _settle(conn)
        with _write_lock, writing(conn):
            return sum(conn.execute(
                "INSERT OR IGNORE INTO restore_stages (session_id, mac, dev, serial, stage,"
                " seconds, at) VALUES (?,?,?,?,?,?,?)",
                (session_id, mac, dev, serial, s["stage"], s["seconds"], at)).rowcount
                for s in stages)
    except Exception:             # noqa: BLE001 — איסוף אינו מפיל דיווח
        log.exception("recording restore stages of %s %s failed", mac, session_id)
        return 0


def _kind(stage: str) -> str:
    return "partitions" if stage.startswith("p") else stage


def medians(conn: sqlite3.Connection, mac: str, sessions: list[str]) -> dict[str, float] | None:
    """‏#1843: לכל שלב קבוע (וסכום המחיצות כ-`partitions`) — החציון על הגלים `sessions` של המכונה,
    כשבכל גל נלקחת המגירה **האיטית** (הגל נגמר כשהיא נגמרת). ‏None — אף גל לא נשא שלבים."""
    try:
        per: dict[str, list[float]] = {}
        for sid in sessions:
            worst: dict[str, float] = {}
            by_dev: dict[str, dict[str, float]] = {}
            for row in conn.execute("SELECT dev, stage, seconds FROM restore_stages"
                                    " WHERE session_id = ? AND mac = ?", (sid, mac)):
                kind = _kind(row["stage"])
                d = by_dev.setdefault(row["dev"], {})
                d[kind] = d.get(kind, 0.0) + row["seconds"]
            for d in by_dev.values():
                for kind, secs in d.items():
                    worst[kind] = max(worst.get(kind, 0.0), secs)
            for kind, secs in worst.items():
                per.setdefault(kind, []).append(secs)
        return {k: round(statistics.median(v), 1) for k, v in per.items()} if per else None
    except Exception:             # noqa: BLE001
        log.exception("stage medians of %s failed", mac)
        return None
