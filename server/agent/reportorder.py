"""#3331 (I14, #3064): סדר דיווחי ההתקדמות -- דיווח ישן אינו מחזיר מצב ו-ledger אחורה.

דיווח שהתעכב ברשת (או נשלח שוב אחרי אתחול של המשכפל) היה מגיע אחרי דיווח חדש ממנו, מחזיר את המצב והיומן אחורה,
ומרענן את `session_members.updated_at` כאילו המשכפל חי עכשיו -- ו-K1 של המוח קורא את זה כראיית חיים.

**החוזה** (interfaces.md סעיף 4, "סדר הדיווחים"): הסוכן שולח בכל דיווח התקדמות `boot_id` (מזהה האתחול של הקרנל)
ו-`seq` (המספר שעולה עם כל דיווח באותו אתחול). השרת מחזיק לכל מכונה את האחרון שקיבל:

- אותו `boot_id` ו-`seq` קטן ממה שנקלט ← **ישן**: נרשם ונזרק. הדיווח לא נכנס ל-`reports.ingest`, ולכן לא מרענן
  דבר ולא נוגע במצב וביומן. התשובה `ok: true, stale: true` (הדיווח שהתעכב הוחלף בחדש ממנו, ואין מה לשלוח שוב).
- `boot_id` שהוצא משימוש (אתחול קודם של אותה מכונה) ← **ישן**, גם אם ה-`seq` שלו גדול: דיווח מהאתחול הקודם שהגיע
  אחרי הראשון של החדש. השרת זוכר כמה אתחולים אחרונים (`RETIRED`).
- `boot_id` שלא נראה ← אתחול חדש: מתקבל, והקודם יוצא משימוש.
- **בלי השדות** (סוכן ישן), או שהם לא תקינים ← מתקבל כמו קודם ונרשם "בלי seq", פעם אחת לכל מכונה. דיווח שאי אפשר
  לסדר אינו "חדש" (עיקרון 5): הוא אינו מעדכן את מה שהשרת זוכר, ואינו נזרק -- כי מכונה שדיווחיה נזרקים בגלל באג
  בשדה נראית מתה.

**הבסיס מתעדכן רק אחרי שהדיווח התקבל** (`note`, אחרי `ok: true`): דיווח שנדחה (אסימון שגוי, סבב סגור) לא יכול
להזיז אותו, אחרת מי ששולח `seq` ענק היה גורם לדיווחים אמיתיים להיזרק. **מקבל גם `seq` שווה** (שליחה חוזרת של
אותו דיווח אחרי תשובה שאבדה): היא לא מחזירה דבר אחורה, והיומן הוא איחוד.

**בזיכרון בלבד, במכוון** (כמו `DurableSeq`; בלי שינוי סכמה): שרת שאותחל אינו יודע מה קיבל, והדיווח הראשון אחרי
העלייה הוא הבסיס. את הסיכון הזה ("דיווח ישן שמגיע מיד אחרי העלייה") סוגר חלון החסד של K2 (`brain/grace.py`, KT5).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from server.rounds.ledger import clean_boot_id

log = logging.getLogger("imagesrv.reportorder")

#: כמה אתחולים קודמים של מכונה נזכרים כ"ישנים". מכונה שהתאתחלה יותר מכך מאז דיווח שמתעכב ברשת אינה תרחיש.
RETIRED = 8
#: תקרה לשפיות: `seq` מעל זה אינו מספר שהסוכן שלנו מייצר (שניות-מאית מאז האתחול, ~3 שנים).
MAX_SEQ = 10**10

ORDERED, STALE, UNORDERED, INVALID = "ordered", "stale", "unordered", "invalid"
#: מה שהתשובה אומרת על דיווח ישן. `ok: true`: הדיווח הוחלף בחדש ממנו, ואין מה לשלוח שוב (`report_final`).
STALE_REPLY = {"ok": True, "stale": True}


@dataclass
class _Boot:
    current: str
    last: int
    retired: deque = field(default_factory=lambda: deque(maxlen=RETIRED))


def fields_of(body: dict) -> tuple[str, int] | Literal[False] | None:
    """``(boot_id, seq)`` מהגוף; ``None`` = השדות חסרים כולם (סוכן ישן); ``False`` = יש שדה, ואינו תקין."""
    boot, seq = body.get("boot_id"), body.get("seq")
    if boot is None and seq is None:
        return None
    clean = clean_boot_id(boot)
    if clean is None or type(seq) is not int or not 0 < seq <= MAX_SEQ:
        return False
    return clean, seq


class ReportOrder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_mac: dict[str, _Boot] = {}
        self._said: set[tuple[str, str]] = set()
        #: כמה דיווחים ישנים נזרקו, לכל מכונה (לבדיקות ולאבחון).
        self.dropped: dict[str, int] = {}

    def check(self, mac: str, body: dict) -> str:
        """האם הדיווח הזה ישן ממה שכבר נקלט. **לא משנה מה שהשרת זוכר** (`note` עושה את זה, אחרי שהדיווח התקבל)."""
        got = fields_of(body)
        if got is None or got is False:
            kind = UNORDERED if got is None else INVALID
            with self._lock:
                first = (mac, kind) not in self._said
                self._said.add((mac, kind))
            if first:
                log.info("progress report of %s %s", mac,
                         "has no (boot_id, seq): an agent from before #3331, accepted as before" if got is None
                         else "carries a boot_id/seq that cannot be read: accepted unordered, not trusted as new")
            return kind
        boot, seq = got
        with self._lock:
            known = self._by_mac.get(mac)
            if known is None or (boot != known.current and boot not in known.retired):
                return ORDERED
            if boot in known.retired:
                where = f"the boot {boot} that ended before {known.current}"
            elif seq < known.last:
                where = f"seq {seq} < {known.last} of boot {boot}"
            else:
                return ORDERED
            self.dropped[mac] = self.dropped.get(mac, 0) + 1
        log.warning("progress report of %s dropped as stale: %s", mac, where)
        return STALE

    def note(self, mac: str, body: dict) -> None:
        """הדיווח התקבל: הוא הבסיס החדש. דיווח בלי שדות תקינים אינו נוגע בבסיס."""
        got = fields_of(body)
        if not isinstance(got, tuple):
            return
        boot, seq = got
        with self._lock:
            known = self._by_mac.get(mac)
            if known is None:
                self._by_mac[mac] = _Boot(boot, seq)
            elif boot == known.current:
                known.last = max(known.last, seq)
            elif boot not in known.retired:
                known.retired.append(known.current)
                known.current, known.last = boot, seq
