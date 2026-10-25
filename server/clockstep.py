"""‏#1947: קפיצת שעון קיר בתהליך השרת — זיהוי, וכיול מחדש של חותמות הסבבים.

נמדד במעבדה (‏22/10, ‏QA-G16): השעון של השרת זז 3 שעות קדימה באמצע גל,
וכל מכונה בגל "אבדה" מיד — ‏`client_lost`, ואחריו ‏`room_failed 0/2`. המכונות
דיווחו כל הזמן; מה שהשתנה הוא רק השעון. כל טיימר בנתיב הסבב משווה את
שעון הקיר לחותמת **שמורה** (‏`updated_at`, ‏`started_at`, ‏`created_at`,
‏`last_seen`), וקפיצה מזיזה את "עכשיו" בלי להזיז את החותמות.

**הגלאי:** ההפרש בין שעון הקיר ל-``time.monotonic()`` קבוע כל עוד איש אינו
מזיז את השעון (‏NTP slew איטי זז בשברירי שנייה, וההשוואה היא מול הדגימה
הקודמת ולכן אינו מצטבר). שינוי שלו מעל `JUMP_SECONDS` בין שתי דגימות הוא
קפיצה, וגודלו הוא גודל הקפיצה. השהיית VM נראית כאן כקפיצה קדימה — ובצדק:
השרת לא שמע איש בזמן הזה, ואין לזה משמעות של "המכונות שתקו".

**הכיול:** החותמות של הסבבים הפעילים מוזזות לבסיס הזמן החדש (`rebase`),
וכך כל הטיימרים שקוראים אותן — אובדן, ‏rewake, גיל הגל, ההמתנה לשאלת
SMART, ‏`last_join_at` — נשארים נכונים בלי שכל אחד מהם יכיר את הקפיצה.

הגלאי שייך ל-`SessionStore` (תהליך = runtime אחד = store אחד); בבדיקות כל
שרת מקבל גלאי משלו, ולכן קפיצה מזויפת בטסט אחד אינה זולגת לבא אחריו.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import weakref
from datetime import datetime, timedelta

from .db import journal, writing

#: מעל כמה שניות שינוי בהפרש (קיר − מונוטוני) הוא קפיצה ולא סחיפה.
JUMP_SECONDS = 60


def _mono() -> float:
    """השעון שאינו קופץ — פונקציה, כדי שהבדיקה תזייף אותו יחד עם הקיר."""
    return time.monotonic()


class Detector:
    """הדגימה האחרונה של (קיר − מונוטוני), והנעילה שמסדרת זיהוי + כיול.

    הנעילה מוחזקת לאורך `observe` **וגם** הכיול שאחריו: תהליכון אחר
    (hello, ‏overview, שעון הרקע) שנכנס ל-`tick` באותו רגע ממתין עד שהחותמות
    כבר בבסיס החדש, ולא מפקיע מכונה מול חותמת ישנה."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._offset: float | None = None

    def observe(self, now: datetime) -> float | None:
        """גודל הקפיצה בשניות (חיובי = קדימה) מאז הדגימה הקודמת, או `None`."""
        offset = now.timestamp() - _mono()
        previous, self._offset = self._offset, offset
        if previous is None:
            return None
        step = offset - previous
        return step if abs(step) > JUMP_SECONDS else None


_detectors: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_detectors_lock = threading.Lock()


def detector_for(store) -> Detector:
    with _detectors_lock:
        found = _detectors.get(store)
        if found is None:
            found = _detectors[store] = Detector()
        return found


def _shift(stamp: str | None, step: float, now: datetime) -> str | None:
    """החותמת בבסיס הזמן החדש, או `None` כשאין מה להזיז.

    קדימה: כל חותמת זזה, ונחסמת ב-`now` — חותמת שנכתבה כבר אחרי הקפיצה
    (לפני שהגלאי ראה אותה) זזה לכל היותר עד "עכשיו", לא אל העתיד.
    אחורה: רק חותמת **בעתיד** היא בוודאות מהבסיס הישן. חותמת שאינה בעתיד
    עשויה להיות דיווח שהגיע אחרי הקפיצה, והזזתה אחורה הייתה מזקינה מכונה
    חיה בגודל הקפיצה — בדיוק הבאג. היא נשארת, והטעות חסומה: היא נראית
    צעירה מכפי שהיא לכל היותר בגודל הקפיצה, ורק אם כבר הייתה ישנה ממנו."""
    try:
        old = datetime.fromisoformat(stamp) if stamp else None
    except (TypeError, ValueError):
        return None
    if old is None or old.tzinfo is None or (step < 0 and old <= now):
        return None
    new = min(old + timedelta(seconds=step), now)
    return new.isoformat(timespec="seconds") if new != old else None


def _shift_epoch(value, step: float, now_epoch: float) -> float | None:
    if not isinstance(value, (int, float)) or (step < 0 and value <= now_epoch):
        return None
    new = min(value + step, now_epoch)
    return new if new != value else None


def rebase(conn: sqlite3.Connection, store, step: float, now: datetime) -> list[str]:
    """מזיז ב-`step` שניות את החותמות שהטיימרים של הסבבים הפעילים קוראים.

    כל עדכון מותנה בערך שנקרא: דיווח שהגיע בינתיים כבר כתב חותמת בבסיס
    החדש, והוא מנצח. הכול ברצף אחד בתוך `writing` (‏#54, ‏#525).
    מחזיר את מזהי הסבבים שכוילו."""
    sessions = conn.execute(
        "SELECT id, created_at, started_at, last_join_at FROM sessions"
        " WHERE state IN ('open', 'running') ORDER BY id").fetchall()
    ids = [s["id"] for s in sessions]
    marks = ",".join("?" * len(ids))
    members = conn.execute(
        f"SELECT session_id, mac, updated_at FROM session_members WHERE session_id IN ({marks})"
        " AND state NOT IN ('done', 'failed', 'partial', 'lost')", ids).fetchall() if ids else []
    devices = conn.execute("SELECT mac, last_seen FROM net_devices").fetchall()
    asks = conn.execute(
        "SELECT mac, nonce, asked_at FROM smart_asks WHERE answer IS NULL").fetchall()
    now_epoch = store.now()
    with writing(conn):
        for s in sessions:
            for column in ("created_at", "started_at"):
                new = _shift(s[column], step, now)
                if new is not None:
                    conn.execute(f"UPDATE sessions SET {column} = ? WHERE id = ? AND {column} = ?",
                                 (new, s["id"], s[column]))
            epoch = _shift_epoch(s["last_join_at"], step, now_epoch)
            if epoch is not None:
                conn.execute("UPDATE sessions SET last_join_at = ? WHERE id = ? AND last_join_at = ?",
                             (epoch, s["id"], s["last_join_at"]))
        for m in members:
            new = _shift(m["updated_at"], step, now)
            if new is not None:
                conn.execute("UPDATE session_members SET updated_at = ?"
                             " WHERE session_id = ? AND mac = ? AND updated_at = ?",
                             (new, m["session_id"], m["mac"], m["updated_at"]))
        for d in devices:
            new = _shift(d["last_seen"], step, now)
            if new is not None:
                conn.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ? AND last_seen = ?",
                             (new, d["mac"], d["last_seen"]))
        for a in asks:
            new = _shift(a["asked_at"], step, now)
            if new is not None:
                conn.execute("UPDATE smart_asks SET asked_at = ?"
                             " WHERE mac = ? AND nonce = ? AND asked_at = ? AND answer IS NULL",
                             (new, a["mac"], a["nonce"], a["asked_at"]))
    return ids


def guard(conn: sqlite3.Connection, store, now: datetime) -> float | None:
    """נקרא בראש כל דופק שמחשב זמן מול חותמת שמורה. מחזיר את הקפיצה, אם הייתה.

    קפיצה נרשמת ביומן (`clock_jump`) **תמיד** — גם בלי סבב פעיל זו ראיה
    שהמפעיל צריך לראות (סוללת BIOS, ‏snapshot, סנכרון מהמארח)."""
    found = detector_for(store)
    with found.lock:
        step = found.observe(now)
        if step is None:
            return None
        ids = rebase(conn, store, step, now)
        journal(conn, "clock_jump", f"{step:+.0f}s {','.join(ids)}".strip())
    return step
