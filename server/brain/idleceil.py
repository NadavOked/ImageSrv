"""#3672 (אפיק #3653, נדב 18/11) A15: תקרות הזמן של בדיקת הכתיבה במסך ההמתנה (`agent/lib/restore/idlecheck.sh`). המוח מחליט; הסוכן מבצע.

בדיקת הכתיבה של המשכפל הממתין (#3268) כותבת על דיסק שאיש לא בחר בו, עם עותק בשרת, ומחזירה את התוכן. שלוש תקרות קבעו עד כה מתי
היא מוותרת, והן ישבו כקבועים בסוכן (`IDLE_WC_BUSY_WAIT_S`, `IDLE_WC_UPLOAD_S` ב-`idlecheck.sh`, `IDLE_WC_KILL_S` ב-`idlegate.sh`). עכשיו הן **פסק
של המוח**, והשרת מוסר אותן בתשובת ה-hello (`idle_ceilings`); הסוכן קורא אותן מהתשובה האחרונה ומבצע.

| תקרה | ערך | מה היא קובעת | מקור הערך |
|---|---|---|---|
| ``busy_wait_s`` | 300 | כמה זמן סבב או משימה ממתינים שבדיקה שרצה כרגע על הדיסק שלהם תסתיים, לפני שהדיסק נדחה בשמו ("לא נגענו בו") | `552e12785` (#3268, 14/11): 120; `67ca4192a` (18/11, "busy wait 300s"): 300, כשהבדיקה קיבלה חלק מתמשך |
| ``upload_s`` | 60 | `curl --max-time` של שליחת העותק לשרת ושל קריאתו חזרה (השחזור) | `552e12785` (#3268, 14/11) |
| ``kill_wait_s`` | 10 | כמה זמן ממתינים אחרי `kill -9` ש-dd שעוד כותב על הדיסק ייעלם מ-`/proc`, לפני שנאמר "הוא עוד כותב" | `52d24320a` (#3271 H4, ממצאי אגיס, 14/11) |

**לא נגעתי בדיסק עסוק, וסבב בבדיקה לא נקטע** (נדב): התקרות אומרות כמה מחכים לפני **שמוותרים בשם**, לא מתי נוגעים. מי שנדחה נדחה בגלוי
(`failed by name`), ולא "כנראה בסדר" (עיקרון 5). **SMART לא אדום, מחיצה מחוברת, BitLocker, זהות הדיסק, העותק בשרת** -- שומרי הבטיחות של הבדיקה --
נשארו בסוכן: הם מדיניות קבועה (קבוצה ג), לא החלטה.

**אין החלטה = הערכים של היום.** שרת ישן, כלל שנפל או ערך לא תקין (לא מספר שלם בין 1 ל-3600): הסוכן נשאר עם הערכים שהוא טען (300 / 60 / 10, או
מה שהסביבה קבעה). **הערכים לא השתנו**, ולכן ההתנהגות כמו לפני #3672. כרגע הכלל לא מקבל קלט משתנה מלבד התפקיד (משכפל בלבד: רק הוא מריץ את
הבדיקה); תקרה שתלויה במהירות הקישור או בגודל הדיסק תבוא אחרי שיהיה לה מספר מדוד.

**נרשם ב-`brain_decisions`** (כלל ``K-IDLE``, נקודה A15, פעולה ``IDLE_CEILINGS``, פסק ``SET``, ישות `machine`) פעם אחת למכונה בתהליך הזה, ושוב רק כשהערכים
משתנים; ``off`` מחליט ואינו רושם. hello בקריאה בלבד (`read_only`) אינו רושם דבר.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("imagesrv.brain")

RULE, POINT, ACTION = "K-IDLE", "A15", "IDLE_CEILINGS"
SET, UNDECIDED = "SET", "UNDECIDED"
OFF = "off"
MAX_KEPT = 1024

#: A15, T-IDLE-BUSY: ראו הטבלה למעלה. היו `IDLE_WC_BUSY_WAIT_S` ב-`idlecheck.sh`.
BUSY_WAIT_S = 300
#: A15, T-IDLE-UPLOAD. היה `IDLE_WC_UPLOAD_S` ב-`idlecheck.sh`.
UPLOAD_S = 60
#: A15, T-IDLE-KILL. היה `IDLE_WC_KILL_S` ב-`idlegate.sh`.
KILL_WAIT_S = 10


def judge(role: object) -> dict[str, int] | None:
    """טהור. התקרות של משכפל; ``None`` לכל תפקיד אחר (הבדיקה רצה רק במשכפל שמדבר עם שרת, `idle_wc_start`)."""
    if role != "cloner":
        return None
    return {"busy_wait_s": BUSY_WAIT_S, "upload_s": UPLOAD_S, "kill_wait_s": KILL_WAIT_S}


_lock = threading.Lock()
_last: dict[str, dict[str, int]] = {}           # MAC -> מה נרשם לו בתהליך הזה
_failed: set[str] = set()


def forget() -> None:
    """לבדיקות: הזיכרון של התהליך."""
    with _lock:
        _last.clear(); _failed.clear()


def _record(conn, mac: str, verdict: str, reason: str, evidence: tuple[str, ...]) -> None:
    from server.brain import memory, veto       # noqa: PLC0415 — כמו שאר כללי ה-hello: נטען כשצריך
    mode = veto.mode(conn)
    if mode != OFF:
        memory.record(conn, mode=mode, rule=RULE, point=POINT, action=ACTION, verdict=verdict, reason=f"{mac}: {reason}",
                      evidence=evidence, entity=("machine", mac), outcome="לא בוצע דבר" if verdict == UNDECIDED else None)


def decide(conn, mac: str, role: object, *, record: bool = True) -> dict[str, int] | None:
    """``idle_ceilings`` של התשובה, או ``None`` (לא משכפל, או שהכלל נפל: אין שדה, והסוכן נשאר עם מה שיש לו). לעולם אינו זורק."""
    try:
        ceilings = judge(role)
        if ceilings is None:
            return None
    except Exception as exc:                    # noqa: BLE001 — אין החלטה, אין שדה
        log.exception("brain: the idle-check ceilings of %s were not decided", mac)
        if record:
            _note(conn, mac, None, f"הכלל נפל ({type(exc).__name__}): אין החלטה, והסוכן נשאר עם הערכים שיש לו")
        return None
    if record:
        _note(conn, mac, ceilings, "תקרות בדיקת הכתיבה של מסך ההמתנה: כמה מחכים לפני שמוותרים בשם, לא מתי נוגעים בדיסק")
    return ceilings


def _note(conn, mac: str, ceilings: dict[str, int] | None, reason: str) -> None:
    """שורה אחת למכונה בתהליך, ושוב כשהערכים משתנים. כשל הרישום הוא שורת לוג."""
    try:
        with _lock:
            if ceilings is None:
                if mac in _failed:
                    return
                if len(_failed) >= MAX_KEPT:
                    _failed.clear()
                _failed.add(mac)
            else:
                if _last.get(mac) == ceilings:
                    return
                if len(_last) >= MAX_KEPT and mac not in _last:
                    _last.pop(next(iter(_last)), None)
                _last[mac] = dict(ceilings)
        if ceilings is None:
            _record(conn, mac, UNDECIDED, reason, ())
        else:
            _record(conn, mac, SET, reason, tuple(f"{k}={v}" for k, v in ceilings.items()))
    except Exception:                           # noqa: BLE001 — הרישום אינו מפיל hello
        log.exception("brain: the idle-check ceilings of %s were not recorded", mac)
