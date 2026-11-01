"""‏#1847 — מכונה שלא מתעוררת מ-WoL: זיהוי, שליחה חוזרת מוקדמת, התראה.

הכרעת נדב 19/10: השרת לומד **ממדידה שלו**, ושום דבר אינו נחסם בגלל הלמידה.

‏`sendto` של חבילת הקסם מצליח גם כשהמכונה לעולם לא תתעורר (#74, #1721) — "נשלח"
אינו "התעורר". הראיה היחידה שהמכונה התעוררה היא שהיא **דיברה עם השרת**: הפירור
הראשון של האתחול (‏`boottrace.record` — הקושחה ביקשה את התפריט) או hello.

**מה נרשם** (‏`wol_wakes`, שורה לכל הערה):

    ‏`sent`     — WoL שנשלח בהצלחה (‏`wol.wake_machine` / ‏`wol.wake_group`) למכונה
                 **שלא נראתה** ב-`AWAKE_S` האחרונות. מכונה ערה מתעלמת מהחבילה, ו-"ענתה"
                 שלה אינו מדידה. שליחה נוספת בזמן שהערה פתוחה (‏`room_rewake` כל 60ש')
                 היא **אותה הערה** — ‏`resent` עולה, השעון אינו מתאפס.
    ‏`answered` — הראיה הראשונה מהמכונה תוך `ANSWER_S`: ‏`seconds` = מהשליחה הראשונה.
    הערה שלא נענתה תוך `ANSWER_S` — **לא התעוררה**.

**מה נלמד לכל MAC** (‏`stats`, על `WINDOW` ההערות האחרונות שהוכרעו): שיעור
ההצלחה וזמן ההתעוררות הטיפוסי (חציון, רק עם `MIN_SAMPLES` הצלחות לפחות).

**מה נעשה** (‏`sweep`, משעון החדר — ‏`app._room_clock`):

    הערה פתוחה שעברה 2 × הזמן הטיפוסי של המכונה → שליחה חוזרת **אחת**, מוקדמת
        (‏`wol_resent`) — לפני ש-`room_rewake` (60ש') או אדם היו שולחים
    ‏`FAIL_STREAK` הערות אחרונות בלי תשובה → התראה **אחת** לרצף (‏`wol_no_wake`)
        ושורה כתומה במסך הבריאות: "לא מתעורר מ-WoL — BIOS/כבל"

ו-**`wol_arm_failed`** (הסוכן דיווח שלא הצליח לדרוך WoL לפני כיבוי) נרשם **פעם
אחת לכל אתחול** (‏`arm_failure_is_new`) — עד כאן כל hello, כל 5 דקות, הוסיף שורה.

⚠️ מכונה שהתעוררה למערכת המקומית ואינה עולה ב-PXE (מחשב בנייה ב-Windows) אינה
משאירה ראיה — היא תיספר "לא התעוררה". ההתראה אומרת זאת במפורש.

מה שנשמר: MAC וזמנים בלבד (#1924). לעולם אינו זורק.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics
import time
from datetime import datetime, timezone

from server.db import _write_lock, journal, writing

log = logging.getLogger("imagesrv.learn_wol")

#: מכונה שנראתה בחלון הזה ערה — ההערה שלה אינה מדידה. אותו חלון כמו
#: ‏`room.AWAKE_SECONDS` ("ערה" במסך החדר).
AWAKE_S = 30
#: אחרי כמה שניות בלי ראיה הערה "לא התעוררה" — הזמן שבו החדר עצמו מוותר
#: (‏`room.REWAKE_MAX × REWAKE_SECONDS + REWAKE_BOOT_SECONDS` = 6×60+180).
ANSWER_S = 540
#: כמה הערות אחרונות שהוכרעו נחשבות לכל מכונה.
WINDOW = 10
#: כמה הצלחות נדרשות לפני שיש "זמן טיפוסי" — פחות מזה אין שליחה מוקדמת.
MIN_SAMPLES = 3
#: כמה הערות אחרונות בלי תשובה ברצף = התראה. "N כשלים" של ה-Issue. ⚠️ ניחוש
#: מנומק: אחת יכולה להיות חבילה שנבלעה (#1721), שתיים — מכונה שכובתה מהחשמל.
FAIL_STREAK = 3
#: פי כמה מהזמן הטיפוסי שולחים שוב — ההגדרה ב-Issue.
EARLY_FACTOR = 2

NO_WAKE_HE = ("לא מתעורר מ-WoL — BIOS/כבל ({n} הערות אחרונות בלי תשובה;"
              " או שהמחשב כבר היה דלוק במערכת המקומית)")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _awake(conn: sqlite3.Connection, mac: str, now: float) -> bool:
    row = conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    try:
        seen = datetime.fromisoformat(row["last_seen"]).timestamp() if row else None
    except (TypeError, ValueError):
        seen = None
    return seen is not None and 0 <= now - seen <= AWAKE_S


def sent(conn: sqlite3.Connection, mac: str, now: float | None = None) -> None:
    """WoL נשלח בהצלחה ל-`mac`. לעולם אינו זורק."""
    now = time.time() if now is None else now
    try:
        if _awake(conn, mac, now):
            return
        with _write_lock, writing(conn):
            if not conn.execute(
                    "UPDATE wol_wakes SET resent = resent + 1 WHERE mac = ?"
                    " AND answered_at IS NULL AND sent_epoch > ?",
                    (mac, now - ANSWER_S)).rowcount:
                conn.execute("INSERT INTO wol_wakes (mac, sent_at, sent_epoch) VALUES (?,?,?)",
                             (mac, _iso(now), now))
    except Exception:             # noqa: BLE001 — הלמידה אינה מפילה הערה
        log.exception("recording a wake of %s failed", mac)


def answered(conn: sqlite3.Connection, mac: str, now: float | None = None) -> None:
    """המכונה דיברה עם השרת (פירור אתחול / hello) — סוגר הערה פתוחה. לעולם אינו זורק."""
    now = time.time() if now is None else now
    try:
        if not conn.execute("SELECT 1 FROM wol_wakes WHERE mac = ? AND answered_at IS NULL"
                            " AND sent_epoch > ?", (mac, now - ANSWER_S)).fetchone():
            return
        with _write_lock, writing(conn):
            conn.execute("UPDATE wol_wakes SET answered_at = ?, seconds = ? - sent_epoch"
                         " WHERE mac = ? AND answered_at IS NULL AND sent_epoch > ?",
                         (_iso(now), now, mac, now - ANSWER_S))
    except Exception:             # noqa: BLE001
        log.exception("recording the answer of %s failed", mac)


def stats(conn: sqlite3.Connection, mac: str, now: float | None = None) -> dict:
    """‏`{"decided", "answered", "rate", "typical_s", "streak"}` — ‏`rate`/‏`typical_s`
    ‏None כשאין על מה (אין הערות שהוכרעו / פחות מ-MIN_SAMPLES הצלחות). ‏`streak` —
    כמה מההערות האחרונות שהוכרעו ברצף לא נענו."""
    now = time.time() if now is None else now
    rows = conn.execute(
        "SELECT answered_at, seconds FROM wol_wakes WHERE mac = ?"
        " AND (answered_at IS NOT NULL OR sent_epoch <= ?) ORDER BY sent_epoch DESC LIMIT ?",
        (mac, now - ANSWER_S, WINDOW)).fetchall()
    good = [r["seconds"] for r in rows if r["answered_at"] is not None and r["seconds"] is not None]
    streak = 0
    for r in rows:
        if r["answered_at"] is not None:
            break
        streak += 1
    return {"decided": len(rows), "answered": len(good),
            "rate": len(good) / len(rows) if rows else None,
            "typical_s": statistics.median(good) if len(good) >= MIN_SAMPLES else None,
            "streak": streak}


def sweep(conn: sqlite3.Connection, send=None, now: float | None = None) -> None:
    """משעון החדר: שליחה חוזרת מוקדמת, והתראה על רצף. לעולם אינו זורק."""
    from server.net import wol             # noqa: PLC0415 — wol מייבא אותנו
    now = time.time() if now is None else now
    send = send or wol._send_broadcast
    try:
        for row in conn.execute(
                "SELECT id, mac, sent_epoch FROM wol_wakes WHERE answered_at IS NULL"
                " AND early = 0 AND sent_epoch > ?", (now - ANSWER_S,)).fetchall():
            typical = stats(conn, row["mac"], now)["typical_s"]
            if typical is None or now - row["sent_epoch"] < EARLY_FACTOR * typical:
                continue
            reason = wol._try_send(row["mac"], send)
            with _write_lock, writing(conn):
                conn.execute("UPDATE wol_wakes SET early = 1 WHERE id = ?", (row["id"],))
            journal(conn, "wol_resent",
                    f"{row['mac']} after={int(now - row['sent_epoch'])}s typical={int(typical)}s"
                    + (f" failed: {reason}" if reason else ""))
        for (mac,) in conn.execute("SELECT DISTINCT mac FROM wol_wakes").fetchall():
            got = stats(conn, mac, now)
            if got["streak"] < FAIL_STREAK:
                continue
            newest = conn.execute(
                "SELECT id, alerted FROM wol_wakes WHERE mac = ? AND answered_at IS NULL"
                " AND sent_epoch <= ? ORDER BY sent_epoch DESC LIMIT 1",
                (mac, now - ANSWER_S)).fetchone()
            if newest is None or newest["alerted"]:
                continue          # פעם אחת לרצף — ההערה האחרונה בו נושאת את הסימן
            with _write_lock, writing(conn):
                conn.execute("UPDATE wol_wakes SET alerted = 1 WHERE id = ?", (newest["id"],))
            journal(conn, "wol_no_wake", f"{mac} {NO_WAKE_HE.format(n=got['streak'])}")
    except Exception:             # noqa: BLE001 — שעון החדר ממשיך
        log.exception("wol sweep failed")


def health_checks(conn: sqlite3.Connection, now: float | None = None) -> list[dict]:
    """שורה כתומה לכל מכונה עם רצף של `FAIL_STREAK` הערות בלי תשובה. בלי הערות
    שנמדדו — אין שורה; עם הערות ובלי רצף — שורה ירוקה אחת."""
    from server.system.health import check     # noqa: PLC0415 — health מייבא אותנו
    now = time.time() if now is None else now
    try:
        macs = [r["mac"] for r in conn.execute("SELECT DISTINCT mac FROM wol_wakes ORDER BY mac")]
        if not macs:
            return []
        names = {r["mac"]: r["suffix"] for r in conn.execute("SELECT mac, suffix FROM machines")}
        bad = []
        for mac in macs:
            got = stats(conn, mac, now)
            if got["streak"] >= FAIL_STREAK:
                bad.append(check(f"wol:{mac}", names.get(mac) or mac, "warn",
                                 NO_WAKE_HE.format(n=got["streak"])))
        if bad:
            return bad
        return [check("wol", "Wake-on-LAN", "ok",
                      f"{len(macs)} מחשבים הוערו — אף אחד אינו ברצף של {FAIL_STREAK} הערות בלי תשובה")]
    except Exception:             # noqa: BLE001 — "לא בדקנו" אינו "תקין"
        log.exception("wol health failed")
        return [check("wol", "Wake-on-LAN", "off", "לא נבדק — הקריאה נכשלה")]


def arm_failure_is_new(conn: sqlite3.Connection, mac: str, now: float | None = None) -> bool:
    """‏`wol_arm_failed` פעם אחת לכל אתחול: True כשאין עוד שורה כזו למכונה מאז
    תחילת האתחול הנוכחי (‏`boot_steps.first_at`). בלי שביל אתחול (מחוץ לוילן,
    בדיקות) — פעם ב-24 שעות. כשל בקריאה — True: עדיף שורה כפולה משורה שנבלעה."""
    now = time.time() if now is None else now
    try:
        row = conn.execute("SELECT first_at FROM boot_steps WHERE mac = ?", (mac,)).fetchone()
        since = row["first_at"] if row and row["first_at"] else _iso(now - 86400)
        return conn.execute(
            "SELECT 1 FROM journal WHERE event = 'wol_arm_failed' AND detail = ?"
            " AND julianday(ts) >= julianday(?) LIMIT 1", (mac, since)).fetchone() is None
    except Exception:             # noqa: BLE001
        log.exception("wol_arm_failed dedupe for %s failed", mac)
        return True
