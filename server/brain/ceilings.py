"""#3817 (F2, F3; אפיק #3653): שתי תקרות זמן בשרת הסבבים -- הכנת האינדקס של אימג', וההמתנה למחשב הבנייה בהפצה ישירה. **בצל.**

שתיהן היו קבועים בקוד בלי שורה בקטלוג ובלי החלטה במוח; חריגה מהן מכשילה סבב או בקשה בשם, ולכן זו החלטה. עכשיו כל אחת נרשמת
ב-``brain_decisions`` (``mode=shadow``, ``outcome`` = "צל") עם הראיה והמקור שלה. ``brain_veto_mode=off`` = לא נקרא ולא נרשם.

**הערכים נשארים בקוד, כנקודת התחלה** (`indexprep.CEILING_BASE_S` / `CEILING_FLOOR_BPS`, `directchunk.RELAY_TIMEOUT_S`): המוח אינו מחזיק
עותק מקביל שיכול להיסחף, והקוד הקיים מעביר אליו את מה שהוא עצמו מחיל. ההתנהגות זהה להיום. מעבר ל-enforce, כלומר שהקוד יקבל את התקרה
מהמוח, הוא אחרי הוכחה על ברזל ובשער #3563.

| תקרה | ערך | מה היא קובעת | מקור הערך |
|---|---|---|---|
| הכנת אימג' (``B3817a``) | 300 ש' + בייטים דחוסים / 10MB/s | כמה זמן גל או משיכה ממתינים לגזירת האינדקס לפני שהם נכשלים בשם | הערכה: img_4f8a23 (1.7GB) נגזר ב-43 ש' בשרת מעבדת ה-VM, 14/11 (#3137), כ-40MB/s; התקרה מניחה קצב איטי פי 4, ועוד 5 דקות |
| העברת חתיכות (``B3817b``) | 30 ש' | כמה השרת ממתין למחשב הבנייה בבקשת חתיכות בודדות: חיבור, ושתיקה בין שני חלקים | הערכה מ-#3209; לא נמדד. לא אומת אם #3161 משנה אותה |

**מה שאינו כאן:** ``MAX_UPLOADS`` (2) הוא הגנה על משאבים (בלעדיו 32 העלאות של 63MiB מחזיקות 2GiB): מנגנון, קבוצה ג', ולא החלטה.
**לעולם אינו זורק** אל הכנת האימג' או אל פתיחת הסבב: מוח שנפל הוא שורת לוג.
"""

from __future__ import annotations

import logging

from server.brain import memory, veto

log = logging.getLogger("imagesrv.brain")

RULE_PREP, RULE_RELAY = "B3817a", "B3817b"
ACTION_PREP, ACTION_RELAY = "IMAGE_PREP_CEILING", "DIRECT_RELAY_TIMEOUT"
WAIT = "WAIT"
SHADOW_HE = "צל: רק נרשם, הקוד הקיים החליט"
#: המקור של כל ערך (עיקרון 5ב): הערכה ולא מדידה, ומאיפה.
PREP_SOURCE = "estimate:img_4f8a23-1.7GB-43s-vmlab-1114-#3137,4x-slower+300s"
RELAY_SOURCE = "estimate:#3209,unmeasured"


def image_prep_s(compressed_bytes: int, base_s: float, floor_bps: int) -> float:
    """התקרה להכנת אימג' בגודל הזה: ``base_s`` ועוד שנייה לכל ``floor_bps`` בייטים דחוסים."""
    return base_s + max(0, compressed_bytes) / floor_bps


def prep_shadow(conn, wave_id: str, image_id: str, compressed_bytes: int, applied_s: float, *, base_s: float,
                floor_bps: int) -> None:
    """שורה אחת להכנת אימג' (גל או משיכה) בפתיחתה: התקרה שהמוח היה קובע מול התקרה שהקוד הקיים החיל. לא משנה דבר, ולעולם אינו זורק."""
    try:
        if veto.mode(conn) == veto.OFF:
            return
        brain_s = image_prep_s(compressed_bytes, base_s, floor_bps)
        memory.record(conn, mode="shadow", rule=RULE_PREP, point="index_prep", action=ACTION_PREP, verdict=WAIT,
                      reason=f"הכנת האימג' ממתינה עד {brain_s:.0f} ש' ({compressed_bytes / 1e9:.1f}GB דחוסים) ואז נכשלת בשם",
                      evidence=[f"key={wave_id}", f"image={image_id}", f"bytes={compressed_bytes}", f"base_s={base_s:g}",
                                f"floor_bps={floor_bps}", f"ceiling_s={brain_s:.0f}", f"applied_s={applied_s:.0f}",
                                f"agree={int(round(brain_s) == round(applied_s))}", f"source={PREP_SOURCE}"],
                      entity=("image", image_id), outcome=SHADOW_HE)
    except Exception:                                   # noqa: BLE001 -- מוח שנפל אינו מפיל הכנת אימג'
        log.exception("brain: the index preparation ceiling of %s was not recorded", image_id)


def relay_shadow(conn, round_id: str, task_id: str, timeout_s: float) -> None:
    """שורה אחת לסבב ישיר, בפתיחתו: כמה ממתינים למחשב הבנייה בבקשת חתיכות בודדות. הקוד הקיים מחיל את הערך שלו עד #3563."""
    try:
        if veto.mode(conn) == veto.OFF:
            return
        memory.record(conn, mode="shadow", rule=RULE_RELAY, point="direct_open", action=ACTION_RELAY, verdict=WAIT,
                      reason=f"בקשת חתיכות שמועברת למחשב הבנייה נכשלת (502) אחרי {timeout_s:g} ש' בלי תשובה",
                      evidence=[f"task={task_id}", f"timeout_s={timeout_s:g}", f"source={RELAY_SOURCE}"],
                      round_id=round_id, entity=("round", round_id), outcome=SHADOW_HE)
    except Exception:                                   # noqa: BLE001 -- מוח שנפל אינו מפיל פתיחת סבב
        log.exception("brain: the direct relay timeout of %s was not recorded", round_id)
