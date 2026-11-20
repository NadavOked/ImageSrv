"""#3330 (KT5, I15): חלון חסד אחרי עליית השרת.

שרת שעלה עכשיו לא ראה אף משכפל: "אין ראיה" אצלו הוא "לא הסתכלתי", לא "המשכפל שתק" (עיקרון 5). לכן ב-`GRACE_S`
הראשונות אחרי העלייה K2 (`decide._judge`) מחזיר HOLD לכל פעולה הרסנית שנשענת על שתיקה.

**הערך הוא נקודת התחלה בקוד, לא הגדרה** (הכרעת נדב 14/11 בערב: ספים טכניים של המוח אינם במסך ההגדרות). הוא הערכה
מטיוטת התכנון ולא מדידה (KT5 בקטלוג), והמוח יכייל אותו.

**מה החלון אינו חוסם:** כשל עם ראיה משלו (readback, SMART אדום, D14, D12) -- הווטו לא נוגע בו גם כאן -- ופעולה
שכבר מוחזקת עד התקרה (כל החזקה נגמרת).

רגע העלייה נישא על החיבור, כמו `veto.attach`: כל נקודה הרסנית שמקבלת את ה-`conn` רואה אותו. בלי `attach` (בדיקות,
חיבור גולמי) אין רגע עלייה מדוד, והצילום נושא ``None`` = "לא נמדד": אין חלון, וההחלטה היא של היום.
"""

from __future__ import annotations

import logging

log = logging.getLogger("imagesrv.brain")

#: KT5: 300 ש'. הערכה, לא נמדד.
GRACE_S = 300.0


def attach(conn, up_at: float) -> None:
    """מחבר ל-`conn` את רגע העלייה של השרת (שניות epoch). `conn` שאינו מקבל תכונות = אין חלון חסד, ורשומה בלוג."""
    try:
        conn.brain_up_at = up_at
    except AttributeError:
        log.warning("brain: %s cannot carry the server start time; no KT5 grace window", type(conn).__name__)


def up_at(conn) -> float | None:
    """רגע העלייה שחובר ל-`conn`, או ``None`` (לא נמדד)."""
    value = getattr(conn, "brain_up_at", None)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
