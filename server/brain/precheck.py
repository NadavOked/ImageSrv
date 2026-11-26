"""#3662 (S28, B72): אזהרות לפני "התחל" -- המוח מכריע מה מצדיק אזהרה, ושהיא **אזהרה בלבד**.

**הכרעת נדב 18/11 (#3653):** המוח מחליט, והשרת מבצע ומציג. מה שהיה ב-`room_precheck.warnings` -- התנאי "המגירה הזו מצדיקה אזהרה" -- נמצא כאן
(`causes`), ו-`room_precheck` רק אוסף את הראיות ומנסח את הטקסט (המסך). **ההתנהגות זהה:** אותו תנאי בדיוק, אותו פלט.

**ההכרעה המתועדת (`SEVERITY`, ההחלטה שננעלה ב-#3662): אזהרה כתומה, לעולם לא חסימה.** שיכפול הוא תמיד עם אדם ליד המכונות, וכתום שואל (כמו SMART,
#872). זה נשאר כך **עד שהמוח מכריע אחרת, ובהכרעה מתועדת**: לא נוסף כאן שום מסלול חסימה, ו-`judge` מחזיר ``WARN`` ולעולם לא ``BLOCK``.

**מה מצדיק אזהרה** (`causes`; ראיות, אין סף חדש):

- ``crc_rise`` -- מונה ה-CRC **עלה** מאז הסבב הקודם של הדיסק (``crc_rise > 0``). מונה מוחלט הוא היסטוריה ולא מצב (#2846), ולכן אינו מזהיר;
  ``crc_rise`` של ``None`` (לא נמדד) אינו עלייה.
- ``port_drop`` -- הקישור ירד מהיכולת שהיציאה הוכיחה (#3591 ב'1, `learn/portcap`). מהירות קישור לבדה היא מידע ולא אזהרה.
- ``smart_learned`` -- ה-SMART השתנה מאז הסבב הקודם (#1859, ``smart_history.LEARNED_HE``).
- ``port_learned`` -- מה שהשרת למד על החיבור או הדיסק (#1844, ``learn/ports``: "כנראה כבל/מחבר" או "הדיסק עצמו").
- ``model_slow`` -- הדגם איטי מהרגיל שלו (#1845, ``learn/models``).
- ``nic_slow`` -- כרטיס ההפצה של המשכפל איטי (#3277, `linkwarn`).

הספים שמאחורי הראיות האלה (``learn/models``, ``learn/ports``, ``learn/portcap``) אינם כאן ולא שונו: הם ספקי ראיה. הכרעה על ספים שלהם היא של #3666.

**הרישום (`record`):** כשגל יוצא לדרך **למרות אזהרה** (`room.journal_precheck`, ביד או לבד) נרשמת שורה אחת ב-``brain_decisions``: ``B72``/``PRECHECK``,
הישות ``round``, ``WARN``, ובתוצאה "צל: הגל יצא לדרך כמו קודם". ``brain_veto_mode=off`` = לא נרשם. **לעולם אינו זורק** אל הגל.
"""

from __future__ import annotations

import logging
from server.db import SqlConnection
from typing import Sequence

from server.brain.gate_core import WARN

log = logging.getLogger("imagesrv.brain")

RULE, ACTION, POINT = "B72", "PRECHECK", "start"
#: ההכרעה: אזהרה בלבד. ``BLOCK`` אינו אפשרות כאן (`judge` לעולם אינו מחזיר אותו), ו-`tests/test_brain_precheck_3662.py` נועל זאת.
SEVERITY = WARN
SHOWN = 3
STARTED_HE = "צל: הגל יצא לדרך כמו קודם"


def causes(d: dict, *, nic: dict | None, learned: str | None) -> tuple[str, ...]:
    """מה מצדיק אזהרה על מגירה אחת: ``d`` = שורת ה-`drawer_list` שלה, ``nic`` = ``nic_warn`` של המשכפל (`linkwarn`), ``learned`` = הטקסט של
    ``smart_history.LEARNED_HE`` כשה-SMART השתנה. ריק = אין אזהרה. טהור; שדה שלא נמדד (``None``) אינו ראיה."""
    model_speed = d.get("model_speed")
    speed = model_speed if isinstance(model_speed, dict) else {}
    found = (("port_drop", d.get("port_drop") is True),
             ("crc_rise", (d.get("crc_rise") or 0) > 0),
             ("smart_learned", bool(learned)),
             ("port_learned", bool(d.get("learned_he"))),
             ("model_slow", speed.get("slow") is True),
             ("nic_slow", nic is not None))
    return tuple(name for name, hit in found if hit)


def judge(warned: Sequence[dict]) -> tuple[str, str, tuple[str, ...]]:
    """``(פסק, סיבה, ראיות)`` לגל שיוצא לדרך עם ``warned`` (פריטי `room_precheck.warnings`). תמיד ``SEVERITY``, וריק = לא נקרא (לא "אין אזהרות").
    הסיבה: הטקסטים של המסך, מילה במילה."""
    shown = " · ".join(str(w.get("text_he") or "?") for w in warned[:SHOWN])
    more = f" · ועוד {len(warned) - SHOWN}" if len(warned) > SHOWN else ""
    items = tuple(f"item={w.get('mac')}:{w['port'] if w.get('port') is not None else w.get('serial')}" for w in warned)
    return SEVERITY, f"{len(warned)} אזהרות לפני התחלת הגל, והוא יוצא בכל זאת: {shown}{more}", (*items, f"warnings={len(warned)}")


def record(conn: SqlConnection, round_id: str, warned: Sequence[dict], *, manual: bool) -> int | None:
    """שורה אחת לגל שיצא עם אזהרה. ``manual`` = המפעיל לחץ "התחל" (וגם: הגל יצא לבד כי המגירות כיסו את היתרה). לא נרשם כש-``warned`` ריק או
    כשהמוח כבוי. אינו זורק; ``None`` = לא נרשם."""
    try:
        from server.brain import memory, veto                               # noqa: PLC0415 -- `room_precheck` מייבא את המודול הזה, ו-`veto` מייבא רחב
        if not warned or veto.mode(conn) == veto.OFF:
            return None
        verdict, reason, evidence = judge(warned)
        return memory.record(conn, mode=veto.SHADOW, rule=RULE, action=ACTION, point=POINT, round_id=round_id,
                             entity=("round", round_id), verdict=verdict, reason=reason,
                             evidence=(*evidence, f"started_by={'operator' if manual else 'auto'}", "legacy=started"),
                             outcome=STARTED_HE)
    except Exception:                                                       # noqa: BLE001 -- הגל יוצא גם כשהמוח נכשל
        log.exception("brain: %s of %s was not recorded", RULE, round_id)
        return None
