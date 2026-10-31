"""‏#1886 — מקום פנוי בשרת לאורך זמן, ובדיקה לפני קליטה — לא 45 דקות שנכשלות בסוף.

**ההיסטוריה** (‏`storage_usage`): דגימה של ‏`total`/`free` לכל מיקום אחסון **מחובר** — פעם ביום
(‏`housekeeping`), ובכל הזמנת קליטה ופתיחת סבב (‏`source`). מיקום שאינו מחובר אינו נדגם (אין מה
לקרוא, וזה לא "0 פנוי"). ‏`trend`: הקצב שבו המקום הפנוי יורד, מהדגימה הישנה ביותר ב-`TREND_DAYS` הימים
האחרונים ועד החדשה — ו"מתמלא בעוד ~N ימים". פחות משתי דגימות ביום שונה — "לא נבדק".

**לפני קליטה** (‏`capture_check`, מ-`capture.order_capture`, לפני שהמשימה נוצרת): הגרסה הקודמת של אותו
אימג' — אימג' בספרייה עם אותו שם ותיקייה (החדש ביותר); ובלעדיו — הקליטה האחרונה שהושלמה מאותה מכונה.
הצורך = ‏`total_compressed_bytes` שלה × (1 + `MARGIN`) + הרזרבה למערכת (‏#1737). פנוי פחות מזה —
**סירוב לפני שמתחילים**, עם המספרים. בלי גרסה קודמת — **אזהרה, לא סירוב** (יומן `capture_space_unchecked`).
לא נמדד המקום הפנוי — אזהרה, והקליטה ממשיכה לבדיקה שכבר קיימת (‏`preflight` של הסוכן, #1737).

**מחיקה רק מוצעת** (עיקרון 7): הסירוב והשורה בבריאות מציעים אימג'ים שלא שימשו בסבב `STALE_DAYS` יום,
הגדולים קודם — אדם מחליט. שום דבר כאן אינו מוחק.

תקרות: ‏`KEEP_PER_LOC` דגימות למיקום (בכל הכנסה) ו-`housekeeping` — ‏`KEEP_DAYS`. לעולם אינו זורק.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable

from server.storage import disk_reserve, storage_locations
from server.db import _settle, _write_lock, now_iso, writing

log = logging.getLogger("imagesrv.storage_history")

#: מרווח על גודל הגרסה הקודמת — אימג' גדל בין גרסאות (עדכונים). ⚠️ ניחוש מנומק.
MARGIN = 0.10
TREND_DAYS = 30
KEEP_DAYS = 400
KEEP_PER_LOC = 1000
#: אימג' שלא שימש בסבב כל כך הרבה ימים — מוצע (לא נמחק) כשהמקום חסר.
STALE_DAYS = 90
#: "מתמלא בעוד" פחות מזה — שורת בריאות כתומה.
WARN_DAYS = 30


def sample(conn: sqlite3.Connection, loc_id: str, path, source: str,
           usage: Callable = shutil.disk_usage) -> dict | None:
    """דגימה אחת. ‏None — לא נקרא (נרשם ב-log). לעולם אינו זורק."""
    try:
        got = usage(path)
        total, free = int(got.total), int(got.free)
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute("INSERT INTO storage_usage (location_id, at, total_bytes, free_bytes, source)"
                         " VALUES (?,?,?,?,?)", (loc_id, now_iso(), total, free, source))
            conn.execute("DELETE FROM storage_usage WHERE location_id = ? AND rowid NOT IN"
                         " (SELECT rowid FROM storage_usage WHERE location_id = ?"
                         " ORDER BY rowid DESC LIMIT ?)", (loc_id, loc_id, KEEP_PER_LOC))
        return {"location_id": loc_id, "total_bytes": total, "free_bytes": free}
    except Exception:             # noqa: BLE001 — מדידה אינה מפילה דבר
        log.exception("storage usage of %s (%s) unread", loc_id, path)
        return None


def sample_local(conn: sqlite3.Connection, library, source: str) -> dict | None:
    """הספרייה המקומית (‏`loc_local`) — בהזמנת קליטה ובפתיחת סבב. ‏library חסרה — כלום."""
    root = getattr(library, "root", None)
    return None if root is None else sample(conn, storage_locations.LOCAL_ID, root, source)


def sample_all(conn: sqlite3.Connection, source: str, usage: Callable = shutil.disk_usage) -> int:
    """כל מיקום **מחובר**. מחזיר כמה נדגמו."""
    try:
        roots = storage_locations.library_roots(conn, "")
    except Exception:             # noqa: BLE001
        log.exception("storage locations unreadable")
        return 0
    return sum(1 for loc in roots if loc.get("available")
               and sample(conn, loc["id"], loc["path"], source, usage) is not None)


def _when(stamp: str) -> datetime | None:
    try:
        got = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return got if got.tzinfo else None


def trend(conn: sqlite3.Connection, loc_id: str, now: datetime | None = None) -> dict:
    """‏`{"free_bytes", "total_bytes", "at", "per_day", "days_left", "samples"}`. ‏`per_day` — כמה
    בייטים פנויים נעלמים ביום (שלילי = מתפנה); ‏None — פחות משתי דגימות ביום שונה. ‏`days_left` —
    None כשהמקום אינו מתמלא או כשאין קצב."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=TREND_DAYS)).isoformat(timespec="seconds")
    rows = [r for r in conn.execute(
        "SELECT at, total_bytes, free_bytes FROM storage_usage WHERE location_id = ?"
        " AND julianday(at) >= julianday(?) ORDER BY at, rowid", (loc_id, since))
        if _when(r["at"]) is not None]
    out = {"free_bytes": None, "total_bytes": None, "at": None, "per_day": None,
           "days_left": None, "samples": len(rows)}
    if not rows:
        return out
    last, first = rows[-1], rows[0]
    out.update(free_bytes=last["free_bytes"], total_bytes=last["total_bytes"], at=last["at"])
    days = (_when(last["at"]) - _when(first["at"])).total_seconds() / 86400
    if days < 1:
        return out
    per_day = (first["free_bytes"] - last["free_bytes"]) / days
    out["per_day"] = int(per_day)
    if per_day > 0:
        out["days_left"] = round(last["free_bytes"] / per_day, 1)
    return out


def _gb(n: int | float) -> str:
    return f"{n / 1e9:.1f} GB"


def stale_images(conn: sqlite3.Connection, library, now: datetime | None = None,
                 limit: int = 3) -> list[dict]:
    """אימג'ים שלא שימשו בסבב `STALE_DAYS` יום (או מעולם), הגדולים קודם — **הצעה** בלבד."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_DAYS)
    used = {r["image_id"]: r["last"] for r in conn.execute(
        "SELECT image_id, MAX(created_at) AS last FROM sessions GROUP BY image_id")}
    out = []
    for image_id, m in (library.scan() if library is not None else {}).items():
        last = _when(used.get(image_id) or "")
        if last is not None and last >= cutoff:
            continue
        size = m.get("total_compressed_bytes") if isinstance(m, dict) else None
        if isinstance(size, int) and size > 0:
            out.append({"id": image_id, "name": m.get("name") or image_id,
                        "folder": m.get("folder") or "", "bytes": size,
                        "last_round": used.get(image_id)})
    return sorted(out, key=lambda x: -x["bytes"])[:limit]


def _previous(conn: sqlite3.Connection, library, name: str, folder: str, mac: str) -> dict | None:
    scanned = library.scan() if library is not None else {}
    same = [m for m in scanned.values() if isinstance(m, dict) and m.get("name") == name
            and (m.get("folder") or "") == folder
            and isinstance(m.get("total_compressed_bytes"), int)]
    if same:
        m = max(same, key=lambda m: (m.get("version") or 1, str(m.get("created", ""))))
        return {"id": m.get("id"), "bytes": m["total_compressed_bytes"], "why": "same_name"}
    for row in conn.execute("SELECT image_id FROM tasks WHERE mac = ? AND type = 'capture'"
                            " AND state = 'done' ORDER BY updated_at DESC LIMIT 5", (mac,)):
        m = scanned.get(row["image_id"])
        if isinstance(m, dict) and isinstance(m.get("total_compressed_bytes"), int):
            return {"id": row["image_id"], "bytes": m["total_compressed_bytes"], "why": "same_machine"}
    return None


def capture_check(conn: sqlite3.Connection, library, name: str, folder: str, mac: str,
                  free: int | None) -> dict:
    """‏`{"status": "ok"|"warning"|"refused", "reason", "previous", "need_bytes", "free_bytes",
    "suggest"}` — ראו את ראש המודול. לעולם אינו זורק (כשל = אזהרה)."""
    try:
        previous = _previous(conn, library, name, folder, mac)
        out = {"status": "ok", "reason": "", "previous": previous, "need_bytes": None,
               "free_bytes": free, "suggest": []}
        if free is None:
            return {**out, "status": "warning",
                    "reason": "המקום הפנוי בשרת לא נמדד — הקליטה תיבדק שוב כשהסוכן ימדוד את הדיסק"}
        if previous is None:
            return {**out, "status": "warning",
                    "reason": f"אין גרסה קודמת להשוואה — פנוי בשרת {_gb(free)}; המקום ייבדק כשהסוכן ימדוד את הדיסק"}
        need = int(previous["bytes"] * (1 + MARGIN)) + disk_reserve.reserve()
        out["need_bytes"] = need
        if free >= need:
            return {**out, "reason": f"פנוי {_gb(free)}, הגרסה הקודמת {_gb(previous['bytes'])}"}
        suggest = stale_images(conn, library)
        tail = ("; אפשר לפנות מקום — לא שימשו בסבב 90 יום: "
                + ", ".join(f"{s['name']} ({_gb(s['bytes'])})" for s in suggest)
                + " (המחיקה בידיים שלכם, בדף האימג'ים)") if suggest else ""
        return {**out, "status": "refused", "suggest": suggest,
                "reason": (f"אין מקום בשרת לקליטה: פנוי {_gb(free)}, והגרסה הקודמת של האימג' תפסה"
                           f" {_gb(previous['bytes'])} — צריך {_gb(need)} כולל מרווח {int(MARGIN * 100)}%"
                           f" והרזרבה למערכת{tail}")}
    except Exception:             # noqa: BLE001
        log.exception("capture space check failed")
        return {"status": "warning", "reason": "בדיקת המקום לפני הקליטה נכשלה — הקליטה תיבדק שוב בהמשך",
                "previous": None, "need_bytes": None, "free_bytes": free, "suggest": []}


def health_checks(conn: sqlite3.Connection) -> list[dict]:
    """שורה לכל מיקום שנדגם: ‏`warn` — מתמלא בעוד פחות מ-`WARN_DAYS` ימים; ‏`ok`; ‏`off` — אין קצב עדיין."""
    from server.system.health import check                # noqa: PLC0415 — health מייבא אותנו
    try:
        out = []
        for row in conn.execute("SELECT DISTINCT location_id FROM storage_usage ORDER BY location_id"):
            loc = row["location_id"]
            t = trend(conn, loc)
            if t["free_bytes"] is None:
                continue
            label = f"מקום פנוי לאורך זמן ({loc})"
            now = f"פנוי {_gb(t['free_bytes'])} מתוך {_gb(t['total_bytes'])}"
            if t["per_day"] is None:
                out.append(check(f"storage_trend:{loc}", label, "off",
                                 f"לא נבדק — צריך דגימות ביותר מיום אחד; {now}"))
            elif t["days_left"] is not None and t["days_left"] < WARN_DAYS:
                out.append(check(f"storage_trend:{loc}", label, "warn",
                                 f"{now}; יורד ב-{_gb(t['per_day'])} ליום — מתמלא בעוד כ-{t['days_left']:.0f} ימים"))
            else:
                grow = (f"יורד ב-{_gb(t['per_day'])} ליום, מתמלא בעוד כ-{t['days_left']:.0f} ימים"
                        if t["days_left"] is not None else "אינו מתמלא בקצב הנוכחי")
                out.append(check(f"storage_trend:{loc}", label, "ok", f"{now}; {grow}"))
        return out
    except Exception:             # noqa: BLE001
        log.exception("storage trend health failed")
        return [check("storage_trend", "מקום פנוי לאורך זמן", "off", "לא נבדק — הקריאה נכשלה")]
