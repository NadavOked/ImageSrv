"""#2905 — משימה `running` שהמכונה שלה הפסיקה לדווח נסגרת `failed`, בשמה.

עד כאן היציאות מ-`running` היו דיווח של הסוכן, המניפסט, ביטול ידני, UPS ועליית
השרת (`reconcile`). **שום דבר לא מדד שקט.** שני מקרים נשארו `running` לנצח:

* **קליטה שמחשב הבנייה עזב באמצעה** (כובה, עלה ל-Windows). `busy_db.reason`
  אמר "קליטת אימג' רצה" — ולכן הגיבוי היומי, האתחול המתוזמן ו"החל עדכון" חיכו
  בשקט, ובלי האתחול גם `reconcile_on_start` לא הגיע. `order_capture` ענה 409.
* **משימת המקור של סבב ישיר שהגל שלו נגמר.** `room._finish_wave` סוגר את הסבב
  ומשאיר את המשימה, **במכוון**, לדיווח הסופי של המקור (גל שנגמר אינו ראיה
  שהמקור סיים — `test_direct_server`). מקור שלא ידווח עוד השאיר אותה `running`,
  וכל משימה חדשה לאותו מחשב בנייה סורבה.

**הראיה לחיים:** `tasks.updated_at` — זז בכל דיווח של הסוכן (`progress_loop`, כל
~2ש' לכל אורך המשימה, גם בזמן שאלת הכיווץ) ובסוף כל העלאה — ובקליטה גם ה-mtime של
קבצי אזור הביניים, שזז עם כל בייט שמגיע (העלאה של מחיצה גדולה נמשכת דקות). אף
קורא אינו כותב `updated_at` של משימה. חותמת שאינה נקראת אינה "שקט": לא נמדד, ולא
מכריזים (אותה הכרעה כמו `pulls._failure` ו-`room._mark_lost`).

**מה לא נסגר כאן:** `pending` (מפעיל רשאי להזמין קליטה ממחשב כבוי — #1576),
`verifying` (האימות של השרת עצמו), ומשימה שמכונה אותחלה באמצעה וחזרה בתוך
`SILENT_SECONDS` — `active_task` מחזיר לה אותה כרגיל. וקפיצת שעון קיר אינה שקט:
`clockstep.rebase` מזיז גם את `updated_at` של משימות `running` (#1947).

**ושקט של השרת אינו שקט של המכונה.** שרת שהיה למטה עשר דקות (עדכון, אתחול) לא
שמע איש — וקליטה או מקור חיים ידווחו שוב שניות אחרי שהוא עונה. לכן השקט נמדד
מ-`max(updated_at, עליית השרת)`: עד `SILENT_SECONDS` אחרי העלייה שום משימה אינה
נסגרת כאן. העלייה נמדדת בשעון שאינו קופץ (`clockstep._mono`), ולכן גם קפיצת שעון
אינה מקצרת אותה.

רץ משעון החדר (`app._room_clock`), כמו `pulls.background_sweep`.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from server.core.tasks import staging_dir
from server.db import _write_lock, journal, now_iso, update_one, writing
from server.system import clockstep

log = logging.getLogger("imagesrv.silent_tasks")

#: מעל כמה שניות בלי דיווח ובלי בייט חדש משימה `running` נסגרת `failed`. אותו
#: נימוק כמו `pulls.SILENT_SECONDS` (#1573): הסוכן מדווח כל ~2ש', דיווח בודד עד
#: ~40ש' (curl --max-time 10 --retry 3), ואתחול של מכונה שחוזרת ל-PXE באמצע
#: משימה לוקח דקות — והיא אמורה לקבל את המשימה בחזרה. נגזר, לא נמדד.
SILENT_SECONDS = 600


def _parse(stamp: object) -> datetime | None:
    try:
        value = datetime.fromisoformat(stamp) if isinstance(stamp, str) and stamp else None
    except ValueError:
        return None
    return value if value is not None and value.tzinfo is not None else None


def _last_sign_of_life(row: sqlite3.Row, library_root: Path) -> datetime | None:
    """הרגע האחרון שבו המכונה הראתה שהיא חיה, או `None` כשאי-אפשר לדעת."""
    last = _parse(row["updated_at"])
    if last is None or row["type"] != "capture":
        return last
    try:
        folder = staging_dir(library_root, row["id"])
        newest = folder.stat().st_mtime
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    newest = max(newest, entry.stat().st_mtime)
                except FileNotFoundError:          # removed between the listing and the stat
                    continue
    except FileNotFoundError:                      # nothing uploaded yet (shrink, first partition)
        return last
    except (OSError, ValueError):                  # cannot read it: not measured, not silence
        return None
    return max(last, datetime.fromtimestamp(newest, timezone.utc))


def sweep(conn: sqlite3.Connection, store, library_root: str | Path, up_since: float,
          now: datetime | None = None) -> list[str]:
    """סוגר כל משימה `running` ששקטה מעל `SILENT_SECONDS`. מחזיר את המזהים שנסגרו.

    `up_since` — מתי השרת התחיל לענות, בשעון של `clockstep._mono`."""
    now = now or datetime.now(timezone.utc)
    clockstep.guard(conn, store, now)              # #1947: before any stamp is read
    if clockstep._mono() - up_since <= SILENT_SECONDS:
        return []                                  # the server was the silent one
    root = Path(library_root)
    closed: list[str] = []
    for row in conn.execute(
            "SELECT id, mac, type, updated_at FROM tasks WHERE state = 'running'").fetchall():
        last = _last_sign_of_life(row, root)
        if last is None or (now - last).total_seconds() <= SILENT_SECONDS:
            continue
        reason = (f"המכונה הפסיקה לדווח — אין דיווח ואין בייטים חדשים מאז"
                  f" {last.astimezone().strftime('%H:%M')} (מעל {SILENT_SECONDS // 60} דקות)")
        # מותנה בחותמת שנקראה: דיווח שהגיע בינתיים מנצח (#54: update_one משחרר נעילה).
        with _write_lock, writing(conn):
            changed = update_one(
                conn,
                "UPDATE tasks SET state = 'failed', error = ?, updated_at = ?"
                " WHERE id = ? AND state = 'running' AND updated_at = ?",
                (reason, now_iso(), row["id"], row["updated_at"]),
            )
        if not changed:
            continue
        capture = row["type"] == "capture"
        if capture:                                # #1915: like `capture._fail`
            shutil.rmtree(staging_dir(root, row["id"]), ignore_errors=True)
        journal(conn, "capture_failed" if capture else "direct_failed",
                f'{row["id"]} {reason}', machine=row["mac"])
        closed.append(row["id"])
    return closed


def background_sweep(conn: sqlite3.Connection, store, library_root: str | Path,
                     up_since: float) -> bool:
    """`sweep` משעון הרקע: לעולם אינו זורק, אבל כשל נרשם ביומן ולא נבלע."""
    try:
        sweep(conn, store, library_root, up_since)
        return True
    except Exception as exc:      # noqa: BLE001 — לא מפיל את לולאת הרקע
        log.exception("silent task sweep failed")
        try:
            journal(conn, "task_sweep_failed",
                    " ".join(f"{type(exc).__name__}: {exc}".split())[:200])
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל את הלולאה
            log.exception("silent task sweep failure was not journaled")
        return False
