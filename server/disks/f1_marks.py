""""נצפה F1 בהדלקה" לכל דיסק, לפי מספר סידורי (#1858, מוקאפ 07/11, נדב).

F1 של ה-BIOS עוצר את ההדלקה לפני PXE, והשרת **אינו רואה** את המסך (עיקרון 5א): הוא יודע על F1 רק
מראיה. עד כאן הראיה היחידה הייתה SMART אדום (`never_woke.F1_REASONS`). כאן הראיה השנייה — דיסק
שנרשם לו שה-BIOS עצר עליו:

* **ידנית** — אדם לוחץ "סמן F1 לדיסק…" בהודעת "לא הגיע" (עמוד "מחשבים") או בתפריט השורה בעמוד
  "דיסקים". בלי הקלדת מילה.
* **אוטומטית** — מכונה שהוצגה כ"לא הגיע · ייתכן F1" וחזרה לרשת (`never_woke.arrived`). נדב החליט
  שכך, אף שהגעה מאוחרת אינה מוכיחה F1: לכן המקור (`auto`) נשמר, מוצג, ונרשם ביומן.

המפתח הוא ה-**serial** ולא המגירה: דיסק חדש בחריץ שבו היה הדיסק הבעייתי נקי, ודיסק שהוחלף נשאר
מסומן. הסימון צובע את הדיסק אדום ומדלג עליו בשיכפול (כלל 24/10, `smart.sh smart_ro_check`), והתגית
אומרת "נצפה F1" ולא "SMART תקול". ניקוי = מחיקת השורה; ההיסטוריה ביומן.

**קריאה שנכשלה אינה "אין סימון"** — `all_marks` מחזיר `None`, והקורא אינו טוען דבר (עיקרון 5).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from server.db import _settle, _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.f1_marks")

MANUAL, AUTO = "manual", "auto"
SERIAL_MAX = 64


class MarkError(ValueError):
    """שגיאת קלט/התנגשות -- ההודעה בעברית, והקונסולה מציגה אותה כמות שהיא."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _serial(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > SERIAL_MAX:
        raise MarkError("מספר סידורי חסר או ארוך מדי")
    return raw.strip()


def _known(conn: sqlite3.Connection, serial: str) -> bool:
    """הדיסק מוכר — במלאי, או ב-hello האחרון של איזושהי מכונה. סימון של מחרוזת שאין לה דיסק היה צובע
    בשקט דיסק עתידי שקיבל אותה."""
    from server.disks import disk_sets   # כאן ולא למעלה: disk_sets קורא את הסימונים (ייבוא מעגלי)
    if conn.execute("SELECT 1 FROM disk_inventory WHERE serial = ?", (serial,)).fetchone() is not None:
        return True
    return serial in disk_sets._current_places(conn)


def view(row: sqlite3.Row | None) -> dict | None:
    """מה שהקונסולה והשרת מציגים: `{at, date, source, by, mac}` או `None`."""
    if row is None:
        return None
    return {"at": row["observed_at"], "date": local_date(row["observed_at"]), "source": row["source"],
            "by": row["marked_by"] or None, "mac": row["mac"]}


def local_date(stamp: object) -> str | None:
    """התאריך (YYYY-MM-DD) לפי שעון השרת — לא UTC: הסימון של 00:30 הוא של היום."""
    try:
        return datetime.fromisoformat(stamp).astimezone().date().isoformat()
    except (TypeError, ValueError):
        return None


def all_marks(conn: sqlite3.Connection) -> dict[str, dict] | None:
    """`{serial: view}` לכל הסימונים. `None` = הקריאה נכשלה (לא "אין סימונים")."""
    try:
        return {r["serial"]: view(r) for r in conn.execute("SELECT * FROM disk_f1_marks")}
    except Exception:             # noqa: BLE001 — "לא בדקנו" אינו "אין F1"
        log.exception("reading the F1 marks failed")
        return None


def for_hello(conn: sqlite3.Connection, disks: object) -> list[dict] | None:
    """לתשובת ה-hello: `[{"serial", "date", "source"}]` לדיסקים שהמכונה שלחה ושמסומנים. `[]` = אין.
    `None` = הקריאה נכשלה — השדה נשמט, והסוכן משאיר את מה שקיבל קודם (כמו `disk_failures`)."""
    marks = all_marks(conn)
    if marks is None:
        return None
    serials = [d["serial"].strip() for d in disks if isinstance(d, dict) and isinstance(d.get("serial"), str)] \
        if isinstance(disks, list) else []
    return [{"serial": s, "date": marks[s]["date"], "source": marks[s]["source"]}
            for s in dict.fromkeys(serials) if s in marks]


def mark(conn: sqlite3.Connection, serial: object, source: str, user: str, mac: str | None = None,
         *, model: str | None = None) -> dict:
    """רושם "נצפה F1" לדיסק. כבר מסומן — 409 (הסימון הראשון נשאר, עם התאריך והמקור שלו)."""
    serial = _serial(serial)
    if source not in (MANUAL, AUTO):
        raise MarkError("מקור סימון לא מוכר")
    if source == MANUAL and not _known(conn, serial):
        raise MarkError(f"הדיסק {serial} אינו מוכר לשרת", 404)
    if conn.execute("SELECT 1 FROM disk_f1_marks WHERE serial = ?", (serial,)).fetchone() is not None:
        raise MarkError(f"הדיסק {serial} כבר מסומן: נצפה F1", 409)
    ts = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("INSERT INTO disk_f1_marks (serial, observed_at, source, marked_by, mac) VALUES (?,?,?,?,?)",
                     (serial, ts, source, user, mac))
    how = "סומן ידנית" if source == MANUAL else "סומן אוטומטית: המכונה לא הגיעה אחרי WoL וחזרה"
    journal(conn, "disk_f1_marked", f"{serial}{f' · {model}' if model else ''} · {how}", user, machine=mac)
    return view(conn.execute("SELECT * FROM disk_f1_marks WHERE serial = ?", (serial,)).fetchone())


def clear(conn: sqlite3.Connection, serial: object, user: str) -> None:
    serial = _serial(serial)
    row = conn.execute("SELECT * FROM disk_f1_marks WHERE serial = ?", (serial,)).fetchone()
    if row is None:
        raise MarkError("הדיסק אינו מסומן", 404)
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM disk_f1_marks WHERE serial = ?", (serial,))
    journal(conn, "disk_f1_cleared", f"{serial} · הסימון מ-{local_date(row['observed_at'])} נוקה", user,
            machine=row["mac"])
