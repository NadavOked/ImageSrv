"""#3663 (אפיק #3653, הכרעת נדב 18/11): בדיקת כבל חיה (A10) ובאנר "קישור איטי" (G2). המוח מחליט; הסוכן והגואי מבצעים ומציגים.

עד כאן שתי ההחלטות ישבו אצל מי שמבצע. `cable_live_check` (הסוכן, `cablelive.sh`) השווה את מהירות הכרטיס ל-1000 וספר נפילות קישור,
ו-`sb_nic_banner` (הגואי, `screens_standby.c`) השווה אותה ל-100 ולמקסימום של הכרטיס: סף בכל מקום, ושלישי ב-`nic_live.is_low`.
עכשיו הסוכן **מדווח עובדות** ב-hello (`nic_link`: המהירות, המקסימום, `carrier_changes` עכשיו ובקריאה הראשונה באתחול, והאם בדיקת הכבל
כבר רצה), והשרת עונה **`link_verdict`**: `{"banner": SHOW | NONE | NOT_MEASURED, "cable_test": RUN | NOT_NOW | TESTED | HEALTHY |
NOT_MEASURED, ו-`cable_why` ליד RUN}`. הסוכן מריץ `ethtool --cable-test` רק על `RUN`; הגואי מצייר את הבאנר רק על `SHOW`. אין בהם עוד סף.

| נקודה (מיפוי 18/11) | כלל | מה מוחלט | מי מבצע |
|---|---|---|---|
| A10 `cablelive.sh` | ``K-CABLE`` | לבדוק את הכבל של קישור חי: איטי (מתחת ל-`CABLE_TEST_MIN_MBPS`) או שנפל מאז האתחול, **ולעולם לא** בזמן סבב או משימה | הסוכן, ב-`wait_poll` בלבד |
| G2 `sb_nic_banner` | ``K-NICBANNER`` | "קישור איטי": `SLOW_MBPS` ומטה (`linkwarn.link_low`), או מתחת למקסימום של הכרטיס | הגואי: מצייר |

**"בזמן סבב או משימה"** (נדב 18/11: "לא להריץ cable-test בזמן סבב או משימה"): התשובה נושאת משימה, או סבב; **או שיש בשרת סבב חדר פעיל**,
גם כשהמשכפל הזה אינו בו. הסוכן ידע עד כה רק על עצמו, והשרת יודע יותר. זה הצד הבטוח היחיד שהוסף על ההתנהגות הקודמת.

**מצב.** כמו שאר כללי "המוח מחליט, אחרים מבצעים" (`silence.py`): `brain_veto_mode` shadow (ברירת המחדל) ו-enforce רושמים ב-
``brain_decisions`` והתשובה מבוצעת; ``off`` מחליט ואינו רושם. **הספים לא השתנו** (1000 לבדיקת הכבל, 100 או מתחת למקסימום לבאנר), ולכן
ההתנהגות כמו לפני #3663.

**אין החלטה = לא עושים כלום.** כלל שנפל מחסיר את המפתח שלו מ-`link_verdict`; סוכן שאינו מוצא אותו לא מריץ בדיקה ולא מצייר באנר
(בדיקת כבל מפילה את הקישור לשניות, ובאנר שלא נגזר מראיה אסור). עובדה שלא נקראה היא ``NOT_MEASURED`` ולעולם לא "תקין" (עיקרון 5).

**נרשם רק מה שזז.** hello חוזר כל כמה שניות, ולכן שורה ב-``brain_decisions`` נכתבת כש"פסק" המכונה **משתנה**, ורק לפסק שיש בו פעולה
(``RUN``, ``SHOW``) או חוסר מדידה (``NOT_MEASURED``); המעבר ממנו נרשם כתוצאה של אותה שורה.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from server.brain import linkwarn, memory
from server.rounds import room_state

log = logging.getLogger("imagesrv.brain")

RULE_CABLE, RULE_BANNER = "K-CABLE", "K-NICBANNER"
POINT_CABLE, POINT_BANNER = "A10", "G2"
ACTION_CABLE, ACTION_BANNER = "CABLE_TEST", "LINK_BANNER"
OFF = "off"

#: A10, T-CABLE (נדב 22/10, #1968): קישור חי שמהירותו **מתחת** לזה נבדק, ו-100 על כרטיס של ג'יגה הוא הזוג השבור הקלאסי. היה
#: `CABLE_TEST_MIN_MBPS` ב-`cablelive.sh` (הסוכן).
CABLE_TEST_MIN_MBPS = 1000

RUN, NOT_NOW, TESTED, HEALTHY, NOT_MEASURED = "RUN", "NOT_NOW", "TESTED", "HEALTHY", "NOT_MEASURED"
SHOW, NONE = "SHOW", "NONE"
UNDECIDED = "UNDECIDED"
#: פסק שנרשם לו שורה (פעולה, או "לא נמדד"). השאר נסגרים בלבד: הם "הכול בסדר", ולא החלטה שמישהו צריך לראות.
RECORDED = frozenset({RUN, SHOW, NOT_MEASURED})
MAX_KEPT = 1024


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()


def _n(value: object) -> int | None:
    """מספר שלם אי-שלילי, או ``None``. ``bool`` אינו מספר (עיקרון 5: דיווח פגום אינו אפס)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def judge_cable(*, busy: bool, speed: int | None, changes: int | None, first: int | None, tested: bool) -> Verdict:
    """טהור. ``speed`` = Mb/s שנקרא עכשיו (``None`` = לא נקרא, והקישור למטה אינו "איטי"); ``changes`` = `carrier_changes` עכשיו;
    ``first`` = הערך הראשון שנקרא באתחול הזה (עלייה הראשונה אינה נפילה: ספירה של 1-2 היא האתחול עצמו)."""
    ev = (f"speed_mbps={speed}", f"floor_mbps={CABLE_TEST_MIN_MBPS}", f"carrier_changes={changes}",
          f"carrier_first={first}", f"tested={str(tested).lower()}", f"busy={str(busy).lower()}")
    if busy:
        return Verdict(NOT_NOW, "סבב או משימה פעילים: הבדיקה מפילה את הקישור לשניות", ev)
    if tested:
        return Verdict(TESTED, "הבדיקה כבר רצה באתחול הזה", ev)
    why = []
    if speed is not None and 0 < speed < CABLE_TEST_MIN_MBPS:
        why.append(f"איטי: {speed}Mb/s")
    if changes is not None and first is not None and changes > first:
        why.append(f"הקישור נפל ({first} -> {changes})")
    if why:
        return Verdict(RUN, ", ".join(why), ev)
    if speed is None or changes is None or first is None:       # מה שלא נקרא אינו "תקין": נפילה שלא נבדקה, או קצב שלא נקרא
        return Verdict(NOT_MEASURED, "לא נקרא קצב הקישור או מונה הנפילות: לא ידוע אם הכבל תקין", ev)
    return Verdict(HEALTHY, "הקישור מלא ולא נפל מאז האתחול", ev)


def judge_banner(speed: int | None, top: int | None) -> Verdict:
    """טהור. ``speed`` = Mb/s שנקרא עכשיו, ``top`` = המקסימום שהכרטיס אומר (``None`` = לא ידוע: רק הרצפה)."""
    ev = (f"speed_mbps={speed}", f"max_mbps={top}", f"threshold_mbps={linkwarn.SLOW_MBPS}")
    if speed is None or speed <= 0:
        return Verdict(NOT_MEASURED, "מהירות הקישור לא נקראה: אין באנר על קריאה חצי-שלמה", ev)
    if linkwarn.link_low(speed, top):
        return Verdict(SHOW, f"קישור של {speed}Mb/s" + (f" (היכולת של הכרטיס {top}Mb/s)" if top and top > speed else ""), ev)
    return Verdict(NONE, "הקישור מלא", ev)


# --- נרשם: רק מה שזז --------------------------------------------------------------------------------------------

_lock = threading.Lock()
_last: dict[tuple[str, str], str] = {}          # (כלל, MAC) -> הפסק האחרון שנאמר בתהליך הזה
_rows: dict[tuple[str, str], int] = {}          # (כלל, MAC) -> השורה הפתוחה שלו ב-brain_decisions
_failed: set[tuple[str, str]] = set()


def forget() -> None:
    """לבדיקות: הזיכרון של התהליך."""
    with _lock:
        _last.clear(); _rows.clear(); _failed.clear()


def _mode(conn) -> str:
    from server.brain import veto               # noqa: PLC0415 — veto כבד, ומי שקורא לכאן כבר טען אותו
    return veto.mode(conn)


def _note(conn, rule: str, point: str, action: str, mac: str, v: Verdict) -> None:
    """רושם כשהפסק של המכונה בכלל הזה השתנה, וסוגר את השורה הפתוחה הקודמת במה שבא אחריה. לעולם אינו זורק."""
    key = (rule, mac)
    try:
        with _lock:
            if _last.get(key) == v.verdict:
                return
            if len(_last) >= MAX_KEPT and key not in _last:
                _last.pop(next(iter(_last)), None)
            _last[key] = v.verdict
            before = _rows.pop(key, None)
        if before is not None:
            memory.resolve(conn, [before], f"אחרי כן: {v.verdict} ({v.reason})")
        if v.verdict not in RECORDED or _mode(conn) == OFF:
            return
        row = memory.record(conn, mode=_mode(conn), rule=rule, point=point, action=action, verdict=v.verdict,
                            reason=f"{mac}: {v.reason}", evidence=v.evidence, entity=("machine", mac),
                            outcome="לא נדרשה פעולה: לא נמדד" if v.verdict == NOT_MEASURED else None)
        if row is not None and v.verdict != NOT_MEASURED:
            with _lock:
                _rows[key] = row
    except Exception:                           # noqa: BLE001 — הרישום אינו מפיל hello
        log.exception("brain: the %s verdict of %s was not recorded", rule, mac)


def _undecided(conn, rule: str, point: str, action: str, mac: str, why: Exception) -> None:
    """הכלל נפל: שורה אחת לכל (כלל, מכונה) בתהליך. אין החלטה, ולכן אין מפתח בתשובה."""
    key = (rule, mac)
    with _lock:
        if key in _failed:
            return
        if len(_failed) >= MAX_KEPT:
            _failed.clear()
        _failed.add(key)
    try:
        if _mode(conn) != OFF:
            memory.record(conn, mode=_mode(conn), rule=rule, point=point, action=action, verdict=UNDECIDED,
                          reason=f"{mac}: הכלל נפל ({type(why).__name__}): אין החלטה, ולא מבצעים דבר", entity=("machine", mac),
                          outcome="לא בוצע דבר")
    except Exception:                           # noqa: BLE001
        log.exception("brain: the %s failure of %s was not recorded", rule, mac)


# --- השער של ה-hello ----------------------------------------------------------------------------------------------

def _busy(conn, answer: dict) -> bool:
    """סבב או משימה: בתשובה הזו, או סבב חדר פעיל בשרת (גם כשהמשכפל הזה אינו בו). נכשל = החריגה עולה, ואין החלטה."""
    return (answer.get("task") is not None or answer.get("session") is not None
            or room_state.active_round(conn) is not None)


def decide(conn, mac: str, nic_link: object, answer: dict) -> dict | None:
    """``link_verdict`` של התשובה למשכפל, או ``None`` (הסוכן לא שלח `nic_link`: אין עובדות, ואין שדה). מפתח שהכלל שלו נפל חסר.

    הקורא (`agent/api.py`) קורא כשהתשובה כבר נבנתה: כך ``task`` ו-``session`` בה הם מה שהסוכן יראה."""
    if not isinstance(nic_link, dict):
        return None
    speed, top = _n(nic_link.get("speed_mbps")), _n(nic_link.get("max_mbps"))
    out: dict[str, str] = {}
    try:
        banner = judge_banner(speed, top)
        out["banner"] = banner.verdict
        _note(conn, RULE_BANNER, POINT_BANNER, ACTION_BANNER, mac, banner)
    except Exception as exc:                    # noqa: BLE001 — אין החלטה, אין באנר
        log.exception("brain: the link banner of %s was not decided", mac)
        _undecided(conn, RULE_BANNER, POINT_BANNER, ACTION_BANNER, mac, exc)
    try:
        cable = judge_cable(busy=_busy(conn, answer), speed=speed, changes=_n(nic_link.get("carrier_changes")),
                            first=_n(nic_link.get("carrier_first")), tested=nic_link.get("cable_tested") is True)
        out["cable_test"] = cable.verdict
        if cable.verdict == RUN:
            out["cable_why"] = cable.reason     # הסוכן כותב אותו ליומן שלו: למה הקישור נופל עכשיו לשניות
        _note(conn, RULE_CABLE, POINT_CABLE, ACTION_CABLE, mac, cable)
    except Exception as exc:                    # noqa: BLE001 — אין החלטה, אין בדיקה
        log.exception("brain: the cable test of %s was not decided", mac)
        _undecided(conn, RULE_CABLE, POINT_CABLE, ACTION_CABLE, mac, exc)
    return out or None
