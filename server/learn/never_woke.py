"""#1858 (מוקאפ 10) — "המכונה לא הגיעה אחרי WoL", ו-F1 רק כשיש דיסק שידוע שעוצר.

F1 של ה-BIOS עוצר את ההדלקה לפני PXE: הסוכן לא עולה ולא יכול לדווח, והשרת רואה רק "WoL נשלח, ואין
תשובה". הכרעת נדב 24/10: "מאושר רק אם אנחנו יודעים שיש F1 לדיסק" — השרת **לא רואה** את מסך ה-BIOS
(עיקרון 5א), ולכן מכונה שלא הגיעה בלי ראיה כזו מקבלת הודעה כללית, בלי שום אזכור של F1.

**מתי "לא הגיעה"** — ההערה האחרונה של המכונה (`wol_wakes`, #1847) עדיין **לא נענתה**, המכונה **לא נראתה
מאז השליחה** (`net_devices.last_seen`), ועבר `max(MIN_WAIT_S, פי 2 מהזמן הטיפוסי שלה)` — אותו פי 2 ששולח את
ה-WoL החוזר המוקדם (`learn_wol.EARLY_FACTOR`); בלי זמן טיפוסי נלמד (פחות מ-`MIN_SAMPLES` הצלחות) —
`MIN_WAIT_S` לבדו. ההערה אינה פגה אחרי `ANSWER_S`: מכונה שעדיין שותקת היא עדיין "לא הגיעה", וכל ראיה
מאוחרת (hello, פירור אתחול, `last_seen`) מנקה אותה.

**ראיה ל-F1** — דיסק שהמכונה דיווחה עליו ב-hello האחרון, `smart == "fail"` כשסיבת ה-SMART היא **בדיקת
הבריאות נכשלה** או **לא נקרא** (#2078), או שאין סיבה בכלל (סוכן ישן) — בדיוק הכלל של חלון הכיבוי בסוכן
(`native-gui/src/screens.c`, `app_f1_disk`; טסט צמד בודק שהשניים לא נסחפו). דיסק אדום משום שהוא לקריאה
בלבד (`readonly`/`media_readonly`) אינו F1.

**הראיה השנייה** (מוקאפ 07/11, נדב) — דיסק שנרשם לו "נצפה F1" לפי serial (`server/disks/f1_marks.py`).
`f1.basis` נושא את המקור: `observed` (גובר — אדם או מכונה ראו F1) או `smart_red`. דיסק מסומן שגם ה-SMART
שלו אדום מקבל `smart_red: true`, והקונסולה אומרת את שניהם.

**סימון אוטומטי** (`arrived`) — מכונה שהקונסולה הציגה "לא הגיע · ייתכן F1" ודיברה עם השרת: הדיסק שהוצג
נרשם `auto`. נקרא **לפני** ש-`net_seen` מרענן את `last_seen` — אחרת ההגעה עצמה מבטלת את התנאי "לא נראתה
מאז השליחה" ולא היה מה לסמן.

**קריאה בלבד.** שום דבר אינו נכתב ושום דבר אינו נחסם. קריאה שנכשלה היא `{"state": "unchecked"}` — לא
"הגיעה" ולא "לא הגיעה" (עיקרון 5); הקונסולה אינה טוענת דבר במצב הזה.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime

from server.disks import drawer_slots, f1_marks, model_slots
from server.learn import wol as learn_wol

log = logging.getLogger("imagesrv.never_woke")

#: ⚠️ נדב 24/10: "לא פחות מ-3 דקות" — מתחת לזה אין "לא הגיעה", גם כשהזמן הטיפוסי קצר.
MIN_WAIT_S = 180
#: סיבות ה-SMART שבהן ה-BIOS עוצר ב-F1 (או שאין סיבה — סוכן ישן). זהה ל-`app_f1_disk` בסוכן.
F1_REASONS = (None, "", "health_failed", "health_unread")
#: מכונה שהקריאה עליה נכשלה — לא "הגיעה" ולא "לא הגיעה".
UNCHECKED = {"state": "unchecked"}


def threshold_s(typical_s: float | None) -> int:
    """אחרי כמה שניות בלי ראיה מציגים "לא הגיעה"."""
    if typical_s is None:
        return MIN_WAIT_S
    return int(max(MIN_WAIT_S, learn_wol.EARLY_FACTOR * typical_s))


def _epoch(stamp) -> float | None:
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except (TypeError, ValueError):
        return None


def waiting(conn: sqlite3.Connection, now: float | None = None, mac: str | None = None) -> dict[str, dict] | None:
    """`{mac: {sent_at, waited_s, typical_s, after_s, resent}}` לכל מכונה שהערתה האחרונה לא נענתה, שלא
    נראתה מאז, ושעבר הסף (או רק `mac`). `None` = הקריאה נכשלה (לא "אין אף אחת")."""
    now = time.time() if now is None else now
    try:
        out: dict[str, dict] = {}
        for row in conn.execute(
                "SELECT w.mac, w.sent_at, w.sent_epoch, w.resent, d.last_seen FROM wol_wakes w"
                " LEFT JOIN net_devices d ON d.mac = w.mac WHERE w.answered_at IS NULL"
                " AND w.id = (SELECT MAX(id) FROM wol_wakes WHERE mac = w.mac)"
                + (" AND w.mac = ?" if mac is not None else ""), (mac,) if mac is not None else ()).fetchall():
            seen = _epoch(row["last_seen"])
            if seen is not None and seen >= row["sent_epoch"]:
                continue              # דיברה עם השרת אחרי השליחה — הגיעה
            typical = learn_wol.stats(conn, row["mac"], now)["typical_s"]
            waited = now - row["sent_epoch"]
            if waited < threshold_s(typical):
                continue
            out[row["mac"]] = {"sent_at": row["sent_at"], "waited_s": int(waited),
                               "typical_s": None if typical is None else int(typical),
                               "after_s": threshold_s(typical), "resent": row["resent"]}
        return out
    except Exception:                 # noqa: BLE001 — "לא בדקנו" אינו "הגיעה"
        log.exception("reading the unanswered wakes failed")
        return None


def _serial(disk: dict) -> str | None:
    value = disk.get("serial")
    return value.strip() if isinstance(value, str) and value.strip() else None


def f1_disk(disks, slots: list[str] | None, marks: dict[str, dict] | None = None) -> dict | None:
    """הדיסק שידוע שעוצר ב-F1, או `None`. שתי ראיות: דיסק שנרשם לו "נצפה F1" (`marks`, לפי serial;
    `basis: observed`, גובר), או SMART אדום (`basis: smart_red`). בכל אחת — הראשון לפי המגירה.
    `marks=None` = אין סימונים או שהקריאה נכשלה: אין ראיה שנייה, ושום טענה על F1 ממנה."""
    if not isinstance(disks, list):
        return None
    found = []
    for disk in disks:
        if not isinstance(disk, dict):
            continue
        serial = _serial(disk)
        mark = marks.get(serial) if marks and serial else None
        red = disk.get("smart") == "fail" and disk.get("smart_reason") in F1_REASONS
        if mark is None and not red:
            continue
        drawer = drawer_slots.drawer_of(slots, disk) if slots else None
        found.append(((0 if mark else 1, drawer if drawer is not None else 99), {
            "drawer": drawer, "model": disk.get("model") or None, "serial": serial,
            "reason": disk.get("smart_reason") or None, "basis": "observed" if mark else "smart_red",
            "smart_red": red,
            "observed": None if mark is None else {"at": mark["at"], "date": mark["date"], "source": mark["source"]}}))
    return min(found, key=lambda item: item[0])[1] if found else None


def arrived(conn: sqlite3.Connection, mac: str, now: float | None = None) -> None:
    """מכונה שהקונסולה הציגה "לא הגיע · ייתכן F1" דיברה עם השרת: הדיסק שהוצג (SMART אדום) נרשם `auto`.
    נקרא מ-`hello._record_seen` **לפני** `net_seen` (ראו למעלה). הדיסק נלקח מ-`net_devices.disks_json`
    כפי שנשמר בהדלקה הקודמת — בדיוק מה שהקונסולה הציגה. לעולם אינו זורק: קריאה שנכשלה אינה סימון."""
    try:
        wait = (waiting(conn, now, mac) or {}).get(mac)
        if wait is None:
            return
        row = conn.execute(
            "SELECT d.disks_json, m.drawer_count, m.drawer_slots FROM machines m"
            " LEFT JOIN net_devices d ON d.mac = m.mac WHERE m.mac = ?", (mac,)).fetchone()
        if row is None or row["disks_json"] is None:
            return                    # מכונה לא רשומה / מעולם לא דיווחה דיסקים — אין דיסק שהוצג
        disks = json.loads(row["disks_json"])
        model_map = model_slots.for_mac(conn, mac)
        slots = drawer_slots.effective(row["drawer_count"], row["drawer_slots"],
                                       model_map["slots"] if model_map else None)
        marks = f1_marks.all_marks(conn)
        if marks is None:
            return
        f1 = f1_disk(disks, slots, marks)
        if f1 is None or f1["basis"] != "smart_red":
            return                    # אין דיסק שהוצג כ-F1, או שהוא כבר מסומן
        if not f1["serial"]:
            log.warning("%s came back from an F1 candidate with no serial -- nothing to mark", mac)
            return
        f1_marks.mark(conn, f1["serial"], f1_marks.AUTO, "", mac, model=f1["model"])
    except f1_marks.MarkError:
        pass                          # סומן בינתיים (מרוץ עם סימון ידני) — הסימון הראשון נשאר
    except Exception:                 # noqa: BLE001 — הלמידה אינה מפילה hello
        log.exception("auto-marking the F1 disk of %s failed", mac)


def for_machine(waits: dict[str, dict] | None, mac: str, disks, slots: list[str] | None,
                marks: dict[str, dict] | None = None) -> dict | None:
    """מה `GET /machines` מחזיר בשדה `never_woke`: `None` — לא ממתינה; `unchecked` — הקריאה נכשלה;
    אחרת `{state: "never_woke", sent_at, waited_s, typical_s, after_s, resent, f1}` עם `f1` = הדיסק
    הידוע או `None`; `marks` = `f1_marks.all_marks` (`None` = אין / נכשל)."""
    if waits is None:
        return dict(UNCHECKED)
    wait = waits.get(mac)
    if wait is None:
        return None
    return {"state": "never_woke", **wait, "f1": f1_disk(disks, slots, marks)}
