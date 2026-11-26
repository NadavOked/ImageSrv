"""סיווג כשל כתיבה — כבל/חריץ או הדיסק — משורות ה-ATA של הקרנל (#874).

הסוכן **אינו מסווג**: הוא אוסף מ-`dmesg` את שורות `ata<N>` / `I/O error`
של הפורט של היעד מאז תחילת הכתיבה ושולח אותן כמות שהן (`ata_log`).
הסיווג נעשה כאן, במקום אחד, ונבדק שורה-שורה על שני הדפוסים שנמדדו.

נמדד 16/09 על דיסק 1 במחשב 2 (`/root/dmesg-sda-20260916-001507.txt`):
``SError: { UnrecovData HostInt 10B8B Handshk }``, ``ATA bus error``,
``limiting SATA link speed to 1.5 Gbps`` — שגיאות קידוד **על הקו**, הדיסק
ענה ``DRDY``. זה כבל/פורט. ובראיה השנייה (07:03) דיסק **אחר** על אותו
פורט נכשל באותה חתימה — שני דיסקים, פורט אחד.

``removed`` (נדב 17/10): המגירה נשלפה — הכונן נעלם באמצע הכתיבה ולא החזיר
שגיאה משלו. זו אינה אשמת הדיסק, והיא אינה צובעת אותו (`disk_failures`), כמו
זרם שנקטע. רק על ראיה חיובית, ורק כשאין סימן קו או דיסק.

``readonly`` (#1742, נדב 18/10): הכתיבה נדחתה כי ההתקן לקריאה בלבד —
EPERM/EROFS מ-`dd` עצמו (``Operation not permitted`` / ``Read-only file
system``), לפני שפקודה הגיעה לדיסק. נמדד ב-D6 (משכפל 2, 18/10): `blockdev
--setro` על מגירה 3 נתן ``dd: error writing '/dev/sda': Operation not
permitted`` ונרשם ``unclassified``. אדום, כמו SMART שנכשל (smart.sh).

**שליפה גוברת על קו חלש (#3008, ברזל 12/11):** `10B8B`/`Handshk`/`ATA bus error`/
`interface fatal error` מופיעים **גם** בשליפה — הקו נקטע באמצע פקודה. לכן כשיש
ראיה חיובית לשליפה (`DevExch`, או הסוכן שראה את `/sys/block/<d>` נעלם), הם
לבדם אינם "כבל". מה שכן נשאר כבל גם אז הוא ראיה **מלפני** הנפילה: `ICRC` (CRC
בהעברה) ו-`limiting SATA link speed` (הקרנל מוריד מהירות רק אחרי ניסיונות חוזרים).

עיקרון 5: מה שאינו מתאים לאף דפוס הוא ``unclassified`` — לא ניחוש.
"""

from __future__ import annotations

import re

#: סימני קו — ביטויים (תת-מחרוזת): שגיאת אפיק, הורדת מהירות הקישור, כשל
#: ממשק. מספיק אחד מהם — הקו אשם.
CABLE_PHRASES = ("ATA bus error", "limiting SATA link speed",
                 "interface fatal error")

#: סימני קו — דגלים בסוגריים המסולסלים של הקרנל (`SError: { ... }`,
#: `error: { ... }`): שגיאות קידוד 8b/10b, לחיצת-יד, CRC בהעברה.
CABLE_FLAGS = frozenset({"10B8B", "Handshk", "ICRC"})

#: סימני מדיה/דיסק — דגלים: סקטור לא-ניתן-לתיקון, פקודה שהדיסק דחה.
#: נספרים **רק בלי** סימן קו — קו רעוע מייצר גם ABRT, ואז הכבל הוא ההסבר
#: הפשוט יותר.
DISK_FLAGS = frozenset({"UNC", "ABRT"})

#: סימני דיסק — ביטויים: שגיאת מדיה. **לא** `Read log ... failed` — הקרנל
#: מדפיס אותו אחרי **כל** איפוס קו (נמדד על ברזל, #890: הכבל הידוע על פורט
#: 1 במחשב 2 סווג "דיסק" בגללו), ולבדו הוא ``unclassified``.
DISK_PHRASES = ("media error",)

#: סימני שליפה — מה שהסוכן כותב ב-`error` כשראה ש-`/sys/block/<d>` נעלם
#: (`drawer_dead_reason` ב-verdict.sh). הכונן יצא מהמערכת; זו הראיה.
REMOVED_AGENT_PHRASES = ("הכונן נעלם מהמערכת",)

#: סימני שליפה — דגל SError: ה-AHCI דיווח שנוכחות ההתקן השתנתה (hot-unplug).
#: `PHYRdyChg` **אינו** סימן: קו רעוע מייצר אותו גם בלי שליפה.
REMOVED_FLAGS = frozenset({"DevExch"})

#: סימני offline — הקרנל דחה כתיבה להתקן שכבר אינו זמין (`BLK_STS_OFFLINE`),
#: או שהסוכן ראה `device/state` = offline. ראיה לשליפה **רק בלי** `exception
#: Emask`: דיסק שגווע עובר קודם טיפול שגיאות של ATA ורק אחריו הקרנל מוציא
#: אותו offline — וזה אינו "נשלף" (נמדד 17/10: `echo 1 > .../device/delete`
#: נותן `device offline error` בלי אף שורת ata).
OFFLINE_PHRASES = ("device offline error", "הקרנל סימן את הכונן offline")

#: סימני קריאה-בלבד (#1742) — strerror של EPERM ושל EROFS כפי ש-`dd`/busybox
#: מדפיסים אותו על כתיבה להתקן שהקרנל מחזיק לקריאה בלבד (`/sys/block/<d>/ro`).
READONLY_PHRASES = ("Operation not permitted", "Read-only file system")
WRITE_ERROR_PHRASES = ("periodic fsync failed", "write_error")

#: ראיה לכבל שקדמה לנפילה, ושליפה אינה מייצרת (#3008): CRC בהעברה, והורדת
#: מהירות הקישור. עם ראיה כזו, גם סימן שליפה אינו מבטל את "כבל".
CABLE_BEFORE_DROP_PHRASES = ("limiting SATA link speed",)
CABLE_BEFORE_DROP_FLAGS = frozenset({"ICRC"})

#: הדגלים שבתוך `{ ... }` — מה שהקרנל מדפיס כקבוצה, מופרד ברווחים.
_BRACED = re.compile(r"\{([^}]*)\}")

#: סימני רשת (#3844) -- מה שהסוכן כותב ב-`error` כשהשרת שתק מעבר לתקרת ההמתנה (`chunkrecv.sh`: "השרת לא ענה במשך N שניות").
#: זה אינו שגיאת קרנל ואינו אשמת הדיסק; נקרא **רק** בלי סימן קרנל, ואין כאן מספר שניות -- הסף שייך לסוכן ולמוח.
NETWORK_PHRASES = ("השרת לא ענה במשך",)

#: #4162: שורת הקרנל ש**כתיבה** שנדחתה משאירה (`I/O error, dev sda, sector 4096 op 0x1:(WRITE)`). היא ראיה של הקרנל על הכתיבה עצמה,
#: ולכן דברי הסוכן על השרת השתוק אינם מכריעים מולה, והסיבה היא `disk`. קריאה (`op 0x0:(READ)`) אינה כתיבה, ושורה בלי `op` (קרנל ישן) אינה אומרת מה נדחה.
_WRITE_IO_ERROR = re.compile(r"I/O error, dev \S+, sector \d+ op 0x[0-9a-f]+:\((?:WRITE|FLUSH)\)")

CAUSES = ("readonly", "write_error", "cable", "disk", "removed", "network", "unclassified")

#: תקרות הקלט: הסוכן שולח עד 40 שורות; מעבר לזה — קלט לא מהסוכן שלנו.
MAX_LINES = 40
MAX_LINE_CHARS = 300


def clean_log(value: object) -> list[str]:
    """`ata_log` כפי שהגיע → רשימת מחרוזות בגבולות. מה שאינו רשימה או
    שורה שאינה מחרוזת נזרק; אורך נחתך. ריק הוא קלט תקין ("לא נאספו שורות")."""
    if not isinstance(value, list):
        return []
    out = [line[:MAX_LINE_CHARS] for line in value if isinstance(line, str)]
    return out[:MAX_LINES]


def _flags(text: str) -> set[str]:
    found: set[str] = set()
    for group in _BRACED.findall(text):
        found.update(group.split())
    return found


def classify(lines: list[str], error: str = "") -> str:
    """readonly / cable / disk / removed / network / unclassified — לפי הסימנים, בסדר הזה.
    readonly ראשון: EPERM/EROFS הוא תשובת קריאת-המערכת של הכתיבה עצמה, וכשהוא
    שם הכתיבה לא הגיעה לקו ולא לדיסק — שורות ATA באותו חלון אינן הסיבה שלה.
    `error` הוא מה שהסוכן כתב על היעד: סוכן לפני #1602 לא שמר את שורת
    ה-offline ב-`ata_log`, והראיה לשליפה נמצאת רק בו."""
    text = "\n".join([*lines, error])
    flags = _flags(text)
    if any(p in text for p in READONLY_PHRASES):
        return "readonly"
    if any(p in text for p in WRITE_ERROR_PHRASES):
        return "write_error"
    disk_signs = any(p in text for p in DISK_PHRASES) or bool(flags & DISK_FLAGS)
    pulled = any(p in text for p in REMOVED_AGENT_PHRASES) or bool(flags & REMOVED_FLAGS)
    before_drop = (any(p in text for p in CABLE_BEFORE_DROP_PHRASES)
                   or bool(flags & CABLE_BEFORE_DROP_FLAGS))
    if pulled and not before_drop and not disk_signs:
        return "removed"          # #3008: קו חלש בשליפה אינו כבל
    if any(p in text for p in CABLE_PHRASES) or flags & CABLE_FLAGS:
        return "cable"
    if disk_signs:
        return "disk"
    if pulled or (any(p in text for p in OFFLINE_PHRASES) and "exception Emask" not in text):
        return "removed"
    if any(p in text for p in NETWORK_PHRASES):
        # #3844: הרשת נפלה, לא הדיסק; ראיה חיובית מהסוכן, בלי ראיה מהקרנל. #4162: אבל כשהקרנל עצמו דחה כתיבה, זו ראיה על הדיסק --
        # והיא `disk` ולא `unclassified`, כי `disk_failures.interrupted` משתיק גם `unclassified` שנושא את מילות ההמתנה
        return "disk" if any(_WRITE_IO_ERROR.search(line) for line in lines) else "network"
    return "unclassified"
