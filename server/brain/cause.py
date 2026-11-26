"""#3064 (K9), #3275: סיווג סיבת הכשל של דיסק **לפי ראיה**, לפני שהוא מסומן "איטי", "לא עמד בקצב" או "נכשל".

**הסיבות** (`classify`, פונקציה טהורה), בסדר הזה:

- ``LINK`` -- ניתוק בקרנל: שורות ATA של כבל (`10B8B`, `ICRC`, הורדת מהירות הקישור) או של מגירה שנשלפה (`DevExch`,
  ``device offline``, הסוכן ראה את הכונן נעלם). כבל או מגירה, **לא הדיסק**. הסיווג של השורות עצמן הוא של
  `server.disks.ata_cause.classify`, במקום אחד.
- ``DRIVE`` -- שגיאת כונן אמיתית מהקרנל: ``UNC``/``ABRT``/``media error``, כשל כתיבה (`write_error`), או התקן לקריאה
  בלבד (`readonly`: הכתיבה עצמה נדחתה). **רק זה נגד הדיסק.**
- ``NETWORK`` -- המקלט אמר ``skipped=0`` לדיסק (הוא לא פיגר), והשרת איבד את המחשב באמצע המעבר (client_lost) או
  שחלקים לא הגיעו אליו (`lost_in_net`, #3141).
- ``SOFTWARE`` -- כשל תוכנה או זרם, לפני הכתיבה או בלי קשר אליה: הזרם ריק/קצר/ארוך (`fanout:`, #1728), הזרם לא הגיע,
  נקטע או שהסבב נסגר (#1580), או שהכתיבה לא התחילה כלל (#1429). לא נגד הדיסק.
- ``UNCLASSIFIED`` -- **מצב בשם משלו** (עיקרון 5): אין ראיה שמכריעה. אינו מתקפל לאף סיבה. דיסק שהמקלט אמר שפיגר
  (``skipped>0``) בלי שום ראיה מהקרנל הוא כאן, ולא ``DRIVE``: אי אפשר לשלול כבל (#420: "drive too slow" על כבל).

**היעדר ראיה אינו ראיה.** ``None`` בכל שדה = "לא נקרא", לא "נקי". אין שורות קרנל = אין ראיה מהקרנל, לא "הקרנל נקי":
`dmesg` שנכשל אצל הסוכן מחזיר גם הוא שורות ריקות.

**מצבים** (המתג של המוח, ``brain_veto_mode``): ``off`` -- דבר לא מחושב ולא נרשם. ``shadow`` (ברירת המחדל) ו-``enforce``
-- הסיבה מחושבת ונרשמת ב-``brain_decisions`` (כלל ``K9``), עם הראיות ועם מה שהקוד הקיים עשה. **K9 אינו משנה דבר
גם ב-enforce:** לא צבע, לא סימון ולא כשל (#3275: "הצבעים לא משתנים"). השימוש בסיבה הוא של כלל אחר.

**הנקודות** (`judge`): מנוע החתיכות כשהוא מכשיל דיסק בשמו (D11 "לא עמד בקצב", D14 הורדה איטית, D15 שתיקה), ורישום
``disk_failure`` מדיווח הסוכן (`disk_failures.record`). אף נקודה לא רואה הכול: המנוע יודע ``skipped`` וניתוק; את הקרנל הוא
מקבל מהמשכפל עצמו בדיווח הגל (``wave_senses.kernel``, `wavesense.kernel_lines`; בלעדיו -- לא נקרא), ``disk_failure`` רואה את הקרנל
ואינו רואה ``skipped``. לכן "כונן איטי שהקרנל שלו נקרא ונקי" אינו
מסווג ``DRIVE`` באף נקודה היום, וזה בכוונה: הוא ``UNCLASSIFIED``, ולא ניחוש.

**אחרי כל שורה של דיסק** `judge` קורא ל-`repeatfail.observe` (B61): אותו דיסק, אותה סיבה, ``Config.fail_repeats`` גלים ברצף =
המלצה, בצל.

**`judge` לעולם אינו זורק** לתוך הנקודה: מוח שנפל הוא שורת לוג, והדיסק מסומן כמו לפני K9.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from server.brain import memory, repeatfail, veto
from server.db import now_iso
from server.disks import ata_cause, disk_failures

log = logging.getLogger("imagesrv.brain")

RULE = "K9"
ACTION = "FAIL_CAUSE"
LINK, DRIVE, NETWORK, SOFTWARE, UNCLASSIFIED = "LINK", "DRIVE", "NETWORK", "SOFTWARE", "UNCLASSIFIED"
CAUSES = (LINK, DRIVE, NETWORK, SOFTWARE, UNCLASSIFIED)
#: מה ש-`ata_cause.classify` אומר, לפי הסיבה של K9. ``unclassified`` של השורות אינו כאן: הוא "אין ראיה מהקרנל".
_KERNEL_LINK = ("cable", "removed")
_KERNEL_DRIVE = ("disk", "write_error", "readonly")

SHADOW_HE = "צל: K9 רק רושם"


@dataclass(frozen=True)
class Evidence:
    """מה שהנקודה יודעת על הדיסק. ``None`` = לא נקרא או לא דווח -- לעולם לא "תקין"."""
    kernel: tuple[str, ...] | None = None   # שורות ה-ATA/dmesg של היעד (ata_log); None = לא נקראו
    error: str | None = None                # מה שהסוכן או השרת כתבו על היעד
    skipped: int | None = None              # המקלט: חלקים שהדיסק לא הספיק לכתוב כי פיגר (מעבר ראשון)
    lost_in_net: int | None = None          # חלקים שלא הגיעו למחשב, ש-`skipped` אינו מסביר (#3141)
    client_lost: bool | None = None         # השרת איבד את המחשב באמצע המעבר הראשון
    wrote: bool | None = None               # הכתיבה לדיסק התחילה (False = כשל לפני הכתיבה)


@dataclass(frozen=True)
class Verdict:
    cause: str
    reason: str
    evidence: tuple[str, ...] = ()

    @property
    def against_disk(self) -> bool:
        return self.cause == DRIVE


def _seen(ev: Evidence, kernel: str | None) -> tuple[str, ...]:
    out = []
    if ev.kernel is not None:
        out.append(f"kernel={kernel} ({len(ev.kernel)} lines)")
    for name in ("skipped", "lost_in_net", "client_lost", "wrote"):
        value = getattr(ev, name)
        if value is not None:
            out.append(f"{name}={value}")
    if ev.error:
        out.append(f"error={ev.error[:200]}")
    return tuple(out)


def _software(error: str | None) -> bool:
    """הזרם או התוכנה, לפי המילים שהסוכן כותב -- אותן מילים ש-`disk_failures` קורא (#1580, #1728), במקום אחד."""
    return disk_failures.interrupted(None, error) or disk_failures.stream_fault(None, error)


def classify(ev: Evidence) -> Verdict:
    """הסיבה, לפי ראיה חיובית בלבד. דטרמיניסטית, בלי DB ובלי שעון."""
    kernel = ata_cause.classify(list(ev.kernel), ev.error or "") if ev.kernel is not None else None
    if kernel is None and ev.error:
        # בלי שורות: מה שהסוכן עצמו כתב על הכונן ("הכונן נעלם מהמערכת", "Operation not permitted") הוא עדיין ראיה
        said = ata_cause.classify([], ev.error)
        kernel = said if said != "unclassified" else None
    seen = _seen(ev, kernel)
    if kernel in _KERNEL_LINK:
        return Verdict(LINK, f"ניתוק בקרנל ({kernel}): כבל או מגירה, לא הדיסק", seen)
    if kernel in _KERNEL_DRIVE:
        return Verdict(DRIVE, f"שגיאת כונן מהקרנל ({kernel})", seen)
    if kernel == "network":
        if ev.kernel is None:   # עיקרון 5: "לא נקרא" נאמר בשמו, לא נקרא כ"בדקנו ואין"
            return Verdict(NETWORK, "רשת: הסוכן דיווח שהשרת לא ענה (תקרת ההמתנה שלו); שורות הקרנל לא נקראו, ולכן אי אפשר לשלול כבל או כונן",
                           (*seen, "kernel=unread"))
        return Verdict(NETWORK, "רשת: הסוכן דיווח שהשרת לא ענה (תקרת ההמתנה שלו), והקרנל נקרא ואין בו שורה של כבל או כונן", seen)
    if ev.skipped == 0 and (ev.client_lost or (ev.lost_in_net or 0) > 0):
        how = "השרת איבד את המחשב באמצע המעבר" if ev.client_lost else f"{ev.lost_in_net} חלקים לא הגיעו למחשב"
        return Verdict(NETWORK, f"רשת: הדיסק לא פיגר (skipped=0), ו{how}", seen)
    if ev.wrote is False or _software(ev.error):
        what = "הכתיבה לא התחילה" if ev.wrote is False else "הזרם או התוכנה נכשלו"
        return Verdict(SOFTWARE, f"כשל תוכנה או זרם ({what}), לא הדיסק", seen)
    missing = [name for name, value in (("שורות הקרנל", ev.kernel), ("skipped", ev.skipped)) if value is None]
    why = "אין ראיה שמכריעה"
    if ev.skipped and ev.kernel is not None and not ev.kernel:
        # #3064: הקרנל נקרא ונקי -- ראיה חיובית שאין שגיאת קו או כונן, ולא "לא נקרא". הסיווג נשאר UNCLASSIFIED בכוונה (ראש המודול)
        why = (f"המקלט אמר שהדיסק פיגר (skipped={ev.skipped}), והקרנל נקרא ואין בו שורות של הפורט: אין ראיה לכבל ואין ראיה "
               "לשגיאת כונן -- הדיסק איטי בלבד")
    elif ev.skipped:
        why = f"המקלט אמר שהדיסק פיגר (skipped={ev.skipped}), אבל אין ראיה מהקרנל: אי אפשר לשלול כבל"
    elif ev.kernel:
        why = "שורות הקרנל אינן מתאימות לאף דפוס"
    return Verdict(UNCLASSIFIED, why + (f" (לא נקראו: {', '.join(missing)})" if missing else ""), seen)


def judge(conn, *, point: str, round_id: str | None, mac: str, serial: str | None, existing: str,
          mode: str | None = None, **evidence) -> Verdict | None:
    """נקודה שמסמנת דיסק נכשל: מסווגת, רושמת ב-``brain_decisions`` ובלוג, ומחזירה את הסיבה -- או ``None`` כשהמוח
    כבוי או שנפל. ``existing`` = מה שהקוד הקיים עשה (התוצאה של ההחלטה: היא כבר ידועה, K9 אינו משנה אותה)."""
    try:
        mode = veto.mode(conn) if mode is None else mode
        if mode == veto.OFF:
            return None
        v = classify(Evidence(**evidence))
        ev = (f"mac={mac}", *v.evidence)
        memory.record(conn, mode=mode, rule=RULE, point=point, action=ACTION, verdict=v.cause, reason=v.reason,
                      evidence=ev, round_id=round_id,
                      entity=("disk", serial) if serial else ("machine", mac), outcome=f"{SHADOW_HE}. {existing}")
        repeatfail.observe(conn, serial, mac, round_id)   # B61: אותה סיבה כמה גלים ברצף; בצל, לעולם אינו זורק
        (log.warning if v.cause == UNCLASSIFIED else log.info)("brain %s", json.dumps({
            "at": now_iso(), "mode": mode, "rule": RULE, "point": point, "round": round_id, "action": ACTION,
            "target": f"{mac}/{serial or '?'}", "verdict": v.cause, "against_disk": v.against_disk,
            "say": "K9 רושם בלבד: הצבע והסימון של הדיסק לא משתנים", "existing": existing,
            "reason": v.reason, "evidence": list(ev)}, ensure_ascii=False))
        return v
    except Exception:                           # noqa: BLE001 — המוח אינו מפיל את הנקודה
        log.exception("brain: K9 failed at %s for %s/%s", point, mac, serial)
        return None
