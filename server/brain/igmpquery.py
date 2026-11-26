"""#3663 (אפיק #3653, נדב 18/11) S49: האם השרת שולח שאילתת IGMP (querier) עכשיו. המוח מחליט; `net/igmp_querier.py` שולח.

עד כאן ההחלטה ישבה בתוך `Querier.should_send` / `other_wins` / `other_present` (השרת): הסף של "querier אחר עדיין נוכח" (`OTHER_PRESENT`), ההשוואה של
RFC 2236 (הכתובת הנמוכה מנצחת), והמרווח בין שאילתות. עכשיו `Querier` **אוסף עובדות** (הכתובת שלו, מי נשמע ומתי, מתי נשלחה השאילתה האחרונה, האם
ההגדרה או השער מסרבים) **ושולח או לא שולח** לפי הפסק; ההשוואות כאן.

| פסק | משמעות | מי שולח |
|---|---|---|
| ``SEND`` | אין querier אחר שמנצח, ועבר מרווח מהשאילתה האחרונה (או שלא נשלחה אחת) | השרת, מיד |
| ``WAIT`` | אנחנו ה-querier, ועוד לא עבר מרווח | אף אחד, והנקודה נשאלת שוב בשנייה הבאה |
| ``YIELD`` | querier אחר, עם כתובת **נמוכה** משלנו, נשמע בתוך `other_present_s` | אף אחד: ה-querier של הרשת הוא אחר (RFC 2236, סעיף 7) |
| ``REFUSED`` | ההגדרה `igmp_querier` כבויה, או שהשער של הכרטיס מסרב (הסיבה בעברית ב-`reason`) | אף אחד |
| ``UNDECIDED`` | הכלל נפל | אף אחד: אין החלטה = לא שולחים, וצריך לראות את זה (מסך הבריאות) |

**מה לא עבר.** שני קבועי הפרוטוקול, `QUERY_INTERVAL` (125, RFC 2236) ו-`OTHER_PRESENT` (2 x 125 + 5), נשארו ב-`igmp_querier.py` והם **קלט** לכלל: הם
ערכים של התקן, לא של המוח. השליחה עצמה (`sendto` על סוקט raw) ושמיעת שאילתות זרות (`observe`) הן מנגנון.

**מצב.** כמו כללי "המוח מחליט, השרת מבצע" האחרים: ``brain_veto_mode`` shadow (ברירת המחדל) ו-enforce רושמים ב-``brain_decisions``, וההחלטה מתבצעת;
``off`` מחליט ואינו רושם. **הספים לא השתנו.** המוח נשאל בכל שנייה (לולאת ההאזנה), ולכן **נרשם רק מעבר** בין מצבים (שולחים / מוותרים / מסורבים /
לא הוכרע), ושורת ``SEND`` נסגרת במה שבא אחריה.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from dataclasses import dataclass

log = logging.getLogger("imagesrv.brain")

RULE, POINT, ACTION = "K-QUERIER", "S49", "QUERIER"
SEND, WAIT, YIELD, REFUSED, UNDECIDED = "SEND", "WAIT", "YIELD", "REFUSED", "UNDECIDED"
OFF = "off"
#: המצבים שנרשמים כשהם משתנים. ``WAIT`` הוא אותו מצב כמו ``SEND`` (אנחנו ה-querier), רק לפני המרווח הבא.
STATE = {SEND: SEND, WAIT: SEND, YIELD: YIELD, REFUSED: REFUSED, UNDECIDED: UNDECIDED}
MAX_KEPT = 64


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()


def other_wins(own: str | None, other: str | None) -> bool:
    """RFC 2236, סעיף 7: ה-querier עם הכתובת **הנמוכה** מנצח. ללא כתובת משלנו או ללא querier אחר אין "ניצחון" (ואין שליחה שנחסמת בגללו)."""
    if other is None or own is None:
        return False
    theirs, mine = ipaddress.ip_address(other), ipaddress.ip_address(own)
    if isinstance(theirs, ipaddress.IPv4Address) and isinstance(mine, ipaddress.IPv4Address):
        return theirs < mine
    if isinstance(theirs, ipaddress.IPv6Address) and isinstance(mine, ipaddress.IPv6Address):
        return theirs < mine
    raise TypeError(f"'<' not supported between {type(theirs).__name__} and {type(mine).__name__}")


def present(other: str | None, age_s: float | None, present_s: float) -> str | None:
    """הכתובת של ה-querier האחר אם נשמע בתוך ``present_s`` שניות (Other Querier Present Interval), אחרת ``None``. גיל שלא נקרא = לא נוכח."""
    if other is None or age_s is None or age_s > present_s:
        return None
    return other


def judge(*, refused: str | None, own: str | None, other: str | None, other_age_s: float | None,
          last_sent_age_s: float | None, interval_s: float, other_present_s: float) -> Verdict:
    """טהור. ``refused`` = למה ההגדרה או השער מסרבים (``None`` = לא מסרבים); ``other``/``other_age_s`` = מי נשמע אחרון ולפני כמה זמן;
    ``last_sent_age_s`` = לפני כמה זמן נשלחה השאילתה האחרונה (``None`` = עוד לא)."""
    near = present(other, other_age_s, other_present_s)
    ev = (f"own={own}", f"other={other}", f"other_present={near}", f"other_age_s={other_age_s}",
          f"last_sent_age_s={last_sent_age_s}", f"interval_s={interval_s}", f"other_present_s={other_present_s}",
          f"refused={refused}")
    if refused is not None:
        return Verdict(REFUSED, refused, ev)
    if other_wins(own, near):
        return Verdict(YIELD, f"querier אחר ברשת ({near}) עם כתובת נמוכה מזו של השרת ({own}): הוא ה-querier, והשרת אינו שולח", ev)
    if last_sent_age_s is None or last_sent_age_s >= interval_s:
        return Verdict(SEND, "אין querier אחר שמנצח, ועבר מרווח מהשאילתה האחרונה", ev)
    return Verdict(WAIT, "השרת הוא ה-querier, ועוד לא עבר מרווח", ev)


# --- נרשם: רק מעבר --------------------------------------------------------------------------------------------

_lock = threading.Lock()
_last: dict[str, str] = {}              # כרטיס -> המצב האחרון שנרשם בתהליך הזה
_rows: dict[str, int] = {}              # כרטיס -> השורה הפתוחה שלו


def forget() -> None:
    """לבדיקות: הזיכרון של התהליך."""
    with _lock:
        _last.clear(); _rows.clear()


def _note(conn, key: str, v: Verdict) -> None:
    """רושם כשהמצב של הכרטיס משתנה, וסוגר את השורה הפתוחה הקודמת במה שבא אחריה. לעולם אינו זורק."""
    state = STATE[v.verdict]
    try:
        with _lock:
            if _last.get(key) == state:
                return
            if len(_last) >= MAX_KEPT and key not in _last:
                _last.pop(next(iter(_last)), None)
            _last[key] = state
            before = _rows.pop(key, None)
        from server.brain import memory, veto               # noqa: PLC0415 — ה-brain אינו נטען עם הרשת בעליית השרת
        mode = veto.mode(conn)
        if before is not None:
            memory.resolve(conn, [before], f"אחרי כן: {state} ({v.reason})")
        if mode == OFF:
            return
        row = memory.record(conn, mode=mode, rule=RULE, point=POINT, action=ACTION,
                            verdict=state, reason=f"{key}: {v.reason}", evidence=v.evidence, entity=("network", key),
                            outcome="לא בוצע דבר: הכלל נפל" if state == UNDECIDED else None)
        if row is not None and state != UNDECIDED:
            with _lock:
                _rows[key] = row
    except Exception:                               # noqa: BLE001 — הרישום אינו עוצר את ה-querier
        log.exception("brain: the querier decision on %s was not recorded", key)


def decide(conn, key: str, **facts) -> Verdict:
    """הפסק לכרטיס ``key`` (שם הכרטיס), נרשם כשהמצב השתנה. כלל שנפל = ``UNDECIDED``: לא שולחים, ואין "תקין" שנאמר בטעות."""
    try:
        v = judge(**facts)
    except Exception as exc:                        # noqa: BLE001
        log.exception("brain: the querier on %s was not decided", key)
        v = Verdict(UNDECIDED, f"הכלל נפל ({type(exc).__name__}): אין החלטה, ולא נשלחת שאילתה")
    _note(conn, key, v)
    return v
