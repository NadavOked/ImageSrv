"""‏#1889 — כמה זמן סבב חיכה לאדם, ועל מה. ניתוח בלבד — הנתונים כבר נאספים.

בלילה של 19/10 גל חיכה 16 דק' לתשובת SMART, ואיש לא ידע לספור את זה אחר כך. שני מקורות קיימים, בלי
טבלה חדשה:

* **ההמתנה לתשובת SMART** — השולח רושם ביומן **`send_held_smart`** (‏`<ses> <macs…>`) כשהוא עוצר לפני המחיצה
  הראשונה, ו-**`send_hold_released`** (‏`<ses> <N>s`) כשהשאלה האחרונה נענתה (‏`sender._hold_for_asks`).
  ‏`smart_hold_s` = סכום ה-N של גלי הסבב. עצירה שלא שוחררה (הסבב נסגר בזמן ההמתנה) נספרת ב-`smart_unreleased`
  — **בלי** שניות: לא ידוע כמה ארכה (עיקרון 5ב).
* **הגל הפתוח** — ‏`sessions.started_at − created_at` של כל גל (‏multicast של קבוצת השיכפול, בחלון הזמן של
  הסבב): מכונות שמצטרפות, החלפת מגירות, "התחל גל". ‏`open_wait_s` — סכום; גל שלא יצא — אינו נספר.

‏`None` = לא נמדד (אין גל שיצא לדרך), לא 0. ההמלצה ("שקלו לענות מראש…") מוצגת לאדם — שום דבר אינו
משתנה אוטומטית. לעולם אינו זורק.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from server.agent.registry import ROOM_GROUPS

log = logging.getLogger("imagesrv.learn_waits")

#: המתנה לתשובת SMART מעל זה (בשבוע) — שורת בריאות כתומה עם ההמלצה. ⚠️ ניחוש מנומק: 5 דק'.
SMART_WARN_S = 300
WEEK_DAYS = 7
_RELEASED = re.compile(r"^(\S+)\s+(\d+)s\b")
RECOMMEND_HE = "שקלו לענות מראש לדיסקים שנכשלו בעבר, או להיות ליד המחשבים בתחילת הגל"


def _span(a: str | None, b: str | None) -> float | None:
    try:
        x, y = datetime.fromisoformat(a), datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return None
    s = (y - x).total_seconds()
    return s if s >= 0 else None


def round_waits(conn: sqlite3.Connection, created_at: str, closed_at: str | None) -> dict:
    """‏`{"waves", "open_wait_s", "smart_hold_s", "smart_holds", "smart_unreleased"}` לסבב בחלון."""
    try:
        until = closed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        waves = conn.execute(
            f"SELECT id, created_at, started_at FROM sessions WHERE group_id IN {ROOM_GROUPS}"
            " AND kind = 'multicast' AND julianday(created_at) >= julianday(?)"
            " AND julianday(created_at) <= julianday(?)", (created_at, until)).fetchall()
        started = [w for w in waves if w["started_at"]]
        opens = [s for w in started if (s := _span(w["created_at"], w["started_at"])) is not None]
        ids = {w["id"] for w in waves}
        held, released, secs = set(), set(), 0
        for row in conn.execute(
                "SELECT event, detail FROM journal WHERE event IN ('send_held_smart', 'send_hold_released')"
                " AND julianday(ts) >= julianday(?) AND julianday(ts) <= julianday(?) ORDER BY id",
                (created_at, until)):
            sid = (row["detail"] or "").split(" ", 1)[0]
            if sid not in ids:
                continue
            if row["event"] == "send_held_smart":
                held.add(sid)
            elif m := _RELEASED.match(row["detail"] or ""):
                released.add(sid)
                secs += int(m.group(2))
        return {"waves": len(waves),
                "open_wait_s": int(sum(opens)) if opens else None,
                "smart_hold_s": secs if started else None,
                "smart_holds": len(released),
                "smart_unreleased": len(held - released)}
    except Exception:             # noqa: BLE001
        log.exception("round waits failed")
        return {"waves": None, "open_wait_s": None, "smart_hold_s": None, "smart_holds": None,
                "smart_unreleased": None}


def week(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """הסבבים שנסגרו ב-`WEEK_DAYS` הימים האחרונים: ‏`{"rounds", "smart_hold_s", "longest_s",
    "open_wait_s"}` (‏None — לא נמדד באף סבב)."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=WEEK_DAYS)).isoformat(timespec="seconds")
    rows = conn.execute("SELECT created_at, closed_at FROM room_rounds WHERE state IN ('closed','failed')"
                        " AND julianday(closed_at) >= julianday(?)", (since,)).fetchall()
    got = [round_waits(conn, r["created_at"], r["closed_at"]) for r in rows]
    holds = [g["smart_hold_s"] for g in got if g["smart_hold_s"] is not None]
    opens = [g["open_wait_s"] for g in got if g["open_wait_s"] is not None]
    return {"rounds": len(rows), "smart_hold_s": sum(holds) if holds else None,
            "longest_s": max(holds) if holds else None, "open_wait_s": sum(opens) if opens else None}


def _min(s: int) -> str:
    return "פחות מדקה" if s < 60 else f"{round(s / 60)} דק'"


def health_checks(conn: sqlite3.Connection) -> list[dict]:
    """סיכום שבועי. בלי סבב שנסגר בשבוע — אין שורה."""
    from server.system.health import check                # noqa: PLC0415 — health מייבא אותנו
    try:
        w = week(conn)
        if not w["rounds"]:
            return []
        label = "המתנה למפעיל (7 ימים)"
        hold = "לא נמדד" if w["smart_hold_s"] is None else _min(w["smart_hold_s"])
        opened = "לא נמדד" if w["open_wait_s"] is None else _min(w["open_wait_s"])
        text = (f"{w['rounds']} סבבים: חיכו לתשובות SMART {hold}"
                + (f" (בסבב אחד עד {_min(w['longest_s'])})" if w["longest_s"] else "")
                + f"; גלים פתוחים עד היציאה {opened}")
        if w["longest_s"] is not None and w["longest_s"] >= SMART_WARN_S:
            return [check("operator_waits", label, "warn", f"{text} — {RECOMMEND_HE}")]
        return [check("operator_waits", label, "ok", text)]
    except Exception:             # noqa: BLE001
        log.exception("operator waits health failed")
        return [check("operator_waits", "המתנה למפעיל (7 ימים)", "off", "לא נבדק — הקריאה נכשלה")]
