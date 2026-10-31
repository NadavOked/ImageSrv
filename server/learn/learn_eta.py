"""‏#1843 — זמן סיום צפוי לגל, למשיכה (שחזור) ולקליטה, **לפי היסטוריה**.

הכרעת נדב 19/10: "זאת הולכת להיות רשת של הפצה ואני רוצה שילמד את המחשבי
שיכפול ובניה". הכלל: השרת לומד **רק ממדידה שלו**, והלמידה **לעולם אינה
חוסמת** — כאן היא רק מספר על המסך. בלי היסטוריה — `seconds: None` ו-`why`
אומר למה ("אין עדיין הערכה"), לעולם לא מספר מומצא (עיקרון 5ב).

שלוש הערכות, ולכל אחת המקור שלה בשם:

**גל** (‏`wave`) — לכל מחשב שיכפול בגל, עם מספר המגירות שלו בגל:

    goodput     = הבייטים שהמגירות כתבו / מגירות / משך הזרם      (‏`wave_rates`,
                  אותו מונה של #1758 — `delivered` ו-`stream.wall_s`)
    שלבים קבועים = (‏`closed_at` − ‏`started_at` של הגל) − משך הזרם   (‏`sessions`:
                  ההמתנה לבייט הראשון, האימות וההרחבה אחרי הזרם — מה שנמדד, לא מנוחש)
    המכונה      = חציון השלבים + גודל האימג' / חציון ה-goodput
    הגל         = המכונה האיטית (הגל נגמר כשהאחרונה נגמרת)

  נלמד רק מגל שהסתיים כולו: ‏`send_done`, ‏כל מגירה של המכונה `done`, ונסגר
  (`closed`). מכונה אחת בגל בלי גל כזה **עם אותו מספר מגירות** — אין הערכה
  לגל כולו (‏`why: "no_history"`, ו-`missing` אומר מי). קצב של 2 מגירות אינו
  זמן של 4 (#1842) — ולכן אין גזירה ממספר מגירות אחר.

**משיכה** (‏`pull`, שחזור תחנה) — משיכות קודמות של **אותה מכונה** שהסתיימו
(`closed`, וכל חבר `done`): קצב = ‏`bytes_total` / ‏(`closed_at` − ‏`started_at`).
הזמן הצפוי = גודל האימג' / חציון הקצב.

**קליטה** (‏`capture`) — קליטות קודמות של **אותה מכונה** (`done`):
קצב = ‏`used_bytes_total` של האימג' שנקלט / ‏(`updated_at` − ‏`started_at` של
המשימה). הזמן הצפוי = הבייטים בשימוש שהסוכן דיווח בקליטה הזו
(‏`capture_detail.total_used_bytes`) / חציון הקצב. לפני שהסוכן דיווח את
המחיצות — ‏`why: "size_unknown"`.

מה שהמסך עושה עם זה: גל פתוח — "לפי היסטוריה: כ-X"; גל שרץ — ‏`ends_at`
(‏`started_at` + ההערכה), **וכשהקצב בפועל נמדד** (‏`eta_s` של #410) הוא
גובר, כך שההערכה מתעדכנת לפי מה שקורה עכשיו.

מה שנשמר: מספרים ותוצאות בלבד (#1924) — אין כאן שום דבר מתוכן הדיסקים.
לעולם אינו זורק: כשל בחישוב הוא `why: "error"`, והמסך מציג "אין עדיין הערכה".
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from datetime import datetime, timedelta

from server.agent import restore_stages

log = logging.getLogger("imagesrv.learn_eta")

#: כמה מדידות אחרונות נחשבות לכל (מכונה, מגירות) / מכונה. אותו חלון כמו
#: הזיכרון של הכוננים (‏`drive_rates.WINDOW`): מספיק כדי שחציון יבלע גל
#: חריג אחד, וקצר מספיק כדי שאימג'ים ומגירות שהתחלפו ייכנסו באותו שבוע.
WINDOW = 5
#: כמה גלים אחרונים סורקים כדי למצוא את החלון של כל מכונה — כמו
#: ‏`drive_rates.MEMORY_SCAN`; ‏`housekeeping` אינו מוחק את אלה.
SCAN = 50
#: מדידה קצרה או קטנה מדי היא רעש של התחלה וסוף — אותם ספים כמו
#: ‏`drive_rates.MIN_SAMPLE_S` / ‏`MIN_SAMPLE_BYTES` (‏#447).
MIN_SECONDS = 60
MIN_BYTES = 10 ** 9


def _when(stamp: object) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        got = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return got if got.tzinfo is not None else None


def _span(since: object, until: object) -> float | None:
    start, end = _when(since), _when(until)
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds > 0 else None


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _none(why: str, **extra) -> dict:
    return {"seconds": None, "ends_at": None, "why": why, **extra}


def _ends(started_at: object, seconds: int) -> str | None:
    start = _when(started_at)
    return (start + timedelta(seconds=seconds)).isoformat(timespec="seconds") if start else None


# --- גל -----------------------------------------------------------------------


def _wave_samples(conn: sqlite3.Connection, want: dict[str, int],
                  exclude: str | None) -> dict[str, list[dict]]:
    """לכל MAC ב-`want`: עד WINDOW מדידות מהגלים האחרונים שבהם היו לה בדיוק
    `want[mac]` מגירות, והגל הסתיים כולו (ראו את ראש המודול)."""
    found: dict[str, list[dict]] = {mac: [] for mac in want}
    rows = conn.execute(
        "SELECT w.session_id, w.decision, w.stream, w.delivered, s.started_at, s.closed_at,"
        " s.state FROM wave_rates w JOIN sessions s ON s.id = w.session_id"
        " WHERE w.session_id IS NOT ? ORDER BY w.rowid DESC LIMIT ?", (exclude, SCAN))
    for row in rows:
        try:
            past = json.loads(row["decision"])
            stream = json.loads(row["stream"]) if row["stream"] else {}
            delivered = json.loads(row["delivered"]) if row["delivered"] else {}
        except (TypeError, ValueError):
            continue
        if (past.get("direct") or row["state"] != "closed" or not isinstance(stream, dict)
                or stream.get("ended") != "send_done" or not isinstance(delivered, dict)):
            continue
        wall = stream.get("wall_s")
        total = _span(row["started_at"], row["closed_at"])
        if (not isinstance(wall, (int, float)) or isinstance(wall, bool) or wall < MIN_SECONDS
                or total is None):
            continue
        for mac, drawers in want.items():
            got = delivered.get(mac)
            if len(found[mac]) >= WINDOW or not isinstance(got, dict):
                continue
            n, done, written = got.get("drawers"), got.get("done"), got.get("written_bytes")
            if not (_count(n) and n == drawers and done == n and _count(written)
                    and written // n >= MIN_BYTES):
                continue
            found[mac].append({"session_id": row["session_id"],
                               "goodput_bps": written / n / wall,
                               "fixed_s": max(0.0, total - wall)})
    return found


def wave(conn: sqlite3.Connection, drives: list[dict], image_bytes: object,
         started_at: str | None = None, session_id: str | None = None) -> dict:
    """ההערכה לגל של `drives` (‏`mac` לכל מגירה) על אימג' של `image_bytes`
    בייטים דחוסים (‏`total_compressed_bytes` — אותו ציר כמו `bytes_written`).

    ‏`{"seconds", "ends_at", "why", "machines": [...], "missing": [...]}`:
    ‏`seconds` — הזמן הצפוי לגל כולו; ‏`ends_at` — ‏`started_at` + ‏`seconds`
    (‏None לגל שעוד לא יצא); ‏`machines` — לכל מכונה: ‏`drawers`, ‏`waves`
    (כמה גלים נמדדו), ‏`sessions`, ‏`goodput_bps`, ‏`fixed_s`, ‏`seconds`.
    ‏`why`: ‏None (יש הערכה) · ‏`no_drives` · ‏`no_image_size` · ‏`no_history`
    (‏`missing` — המכונות בלי גל קודם עם אותו מספר מגירות) · ‏`error`."""
    try:
        want: dict[str, int] = {}
        for d in drives or []:
            mac = d.get("mac") if isinstance(d, dict) else None
            if not mac:
                return _none("no_drives")
            want[mac] = want.get(mac, 0) + 1
        if not want:
            return _none("no_drives")
        if not _count(image_bytes) or image_bytes <= 0:
            return _none("no_image_size")
        samples = _wave_samples(conn, want, session_id)
        missing = sorted(mac for mac, got in samples.items() if not got)
        if missing:
            return _none("no_history", missing=missing)
        machines = []
        for mac, got in sorted(samples.items()):
            goodput = statistics.median(s["goodput_bps"] for s in got)
            fixed = statistics.median(s["fixed_s"] for s in got)
            sessions = [s["session_id"] for s in got]
            machines.append({"mac": mac, "drawers": want[mac], "waves": len(got),
                             "sessions": sessions,
                             # ‏#1883: השלבים שנמדדו (חציון; ‏None = הגלים לא נשאו ציר זמן)
                             "stages_s": restore_stages.medians(conn, mac, sessions),
                             "goodput_bps": int(goodput), "fixed_s": int(fixed),
                             "seconds": int(fixed + image_bytes / goodput)})
        seconds = max(m["seconds"] for m in machines)
        return {"seconds": seconds, "ends_at": _ends(started_at, seconds), "why": None,
                "machines": machines, "missing": []}
    except Exception:             # noqa: BLE001 — הערכה אינה מפילה את מסך החדר
        log.exception("wave forecast failed")
        return _none("error")


# --- משיכה --------------------------------------------------------------------


def pull(conn: sqlite3.Connection, mac: str, image_bytes: object,
         started_at: str | None = None, session_id: str | None = None) -> dict:
    """ההערכה למשיכה (שחזור) של `mac`: ‏`{"seconds", "ends_at", "why", "pulls",
    "sessions", "rate_bps"}`. ‏`why`: ‏None · ‏`no_image_size` · ‏`no_history` · ‏`error`."""
    try:
        if not _count(image_bytes) or image_bytes <= 0:
            return _none("no_image_size")
        rates, sessions = [], []
        for row in conn.execute(
                "SELECT s.id, s.started_at, s.closed_at,"
                " MIN(m.done) AS all_done, SUM(m.bytes_total) AS total"
                " FROM sessions s JOIN session_members m ON m.session_id = s.id"
                " WHERE s.kind = 'unicast' AND s.state = 'closed' AND s.id IS NOT ?"
                " AND m.mac = ? GROUP BY s.id ORDER BY s.closed_at DESC LIMIT ?",
                (session_id, mac, SCAN)):
            seconds = _span(row["started_at"], row["closed_at"])
            if (row["all_done"] != 1 or seconds is None or seconds < MIN_SECONDS
                    or not _count(row["total"]) or row["total"] < MIN_BYTES):
                continue
            rates.append(row["total"] / seconds)
            sessions.append(row["id"])
            if len(rates) >= WINDOW:
                break
        if not rates:
            return _none("no_history")
        rate = statistics.median(rates)
        seconds = int(image_bytes / rate)
        return {"seconds": seconds, "ends_at": _ends(started_at, seconds), "why": None,
                "pulls": len(rates), "sessions": sessions, "rate_bps": int(rate)}
    except Exception:             # noqa: BLE001
        log.exception("pull forecast failed")
        return _none("error")


# --- קליטה --------------------------------------------------------------------


def capture(conn: sqlite3.Connection, library, mac: str, used_bytes: object,
            started_at: str | None = None, task_id: str | None = None) -> dict:
    """ההערכה לקליטה של `mac` כשהסוכן כבר דיווח כמה בשימוש (`used_bytes`):
    ‏`{"seconds", "ends_at", "why", "captures", "tasks", "rate_bps"}`. ‏`why`:
    ‏None · ‏`size_unknown` (המחיצות טרם נמדדו) · ‏`no_history` · ‏`error`.
    ‏`library` — הספרייה (‏`get(image_id)` → מניפסט), ממנה ה-`used_bytes` של
    כל קליטה קודמת; אימג' שנמחק או מחיצה שלא נמדדה — אינה מדידה."""
    try:
        if not _count(used_bytes) or used_bytes <= 0:
            return _none("size_unknown")
        rates, tasks = [], []
        for row in conn.execute(
                "SELECT id, image_id, started_at, updated_at FROM tasks"
                " WHERE mac = ? AND type = 'capture' AND state = 'done' AND id IS NOT ?"
                " ORDER BY updated_at DESC LIMIT ?", (mac, task_id, SCAN)):
            seconds = _span(row["started_at"], row["updated_at"])
            manifest = library.get(row["image_id"]) if library is not None else None
            used = _used_total(manifest)
            if seconds is None or seconds < MIN_SECONDS or used is None or used < MIN_BYTES:
                continue
            rates.append(used / seconds)
            tasks.append(row["id"])
            if len(rates) >= WINDOW:
                break
        if not rates:
            return _none("no_history")
        rate = statistics.median(rates)
        seconds = int(used_bytes / rate)
        return {"seconds": seconds, "ends_at": _ends(started_at, seconds), "why": None,
                "captures": len(rates), "tasks": tasks, "rate_bps": int(rate)}
    except Exception:             # noqa: BLE001
        log.exception("capture forecast failed")
        return _none("error")


def _used_total(manifest: object) -> int | None:
    """סכום `used_bytes` של כל המחיצות, או None — מחיצה אחת שלא נמדדה הופכת
    את הסכום ל"לא נמדד" (אותו כלל כמו `GET /api/console/images`)."""
    parts = manifest.get("partitions") if isinstance(manifest, dict) else None
    if not isinstance(parts, list) or not parts:
        return None
    total = 0
    for part in parts:
        used = part.get("used_bytes") if isinstance(part, dict) else None
        if not _count(used):
            return None
        total += used
    return total
