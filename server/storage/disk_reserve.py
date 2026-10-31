"""רזרבה למערכת: אף כתיבה של אימג' אינה משאירה פחות מ-`SYSTEM_RESERVE_BYTES` פנויים (‏#1737).

נמדד 18/10 על שרת המעבדה TA (‏rc6 ‏a490f0b2): האימג'ים (‏/srv/imagesrv), ה-DB,
היומנים ומערכת ההפעלה יושבים על **אותה מחיצה** (‏/dev/sda2, ‏786G). עד כאן
הקליטה סירבה רק כש"תפוס > פנוי" — קליטה אחת גדולה יכלה למלא את המחיצה עד 0
בייט, ואז נכשלים ה-DB, ‏journald, ‏apt, ועדכון הגרסה של השרת עצמו.

**הערך: ‏10 GiB** (‏10,737,418,240 בייט; הקונסולה מציגה "10.0 GB", הודעות
הסירוב — שסופרות בעשרוני כמו שאר מספרי הקליטה — "10.7 GB"). סכום מה שהשרת
צריך כדי להמשיך לעבוד כשהספרייה מלאה, כל רכיב במקורו:

* **‏journald — ‏4 GiB.** ‏`SystemKeepFree=4G` (‏deploy/journald-imagesrv.conf,
  וזו גם ברירת המחדל של systemd: ‏15% מהמחיצה, עד 4G). מתחת לזה journald
  **מפסיק לכתוב** — ובלי יומן אין שום ראיה למה השרת נכשל.
* **‏journald — עוד 1 GiB:** ‏`SystemMaxUse=1G` באותו קובץ; היומן גדל עד שם.
* **עדכוני המערכת — 2 GiB:** ‏deploy/security-upgrade.sh מוריד את מאגר
  trixie-security ופורס אותו ב-$DATA_DIR/security-pool, ו-apt פורס חבילות
  (קרנל + initramfs לכל קרנל) לפני שהישנות יוצאות.
* **עדכון הגרסה — 1 GiB:** ‏git fetch של התג, ובניית מטען האתחול
  (‏tools/boot-payload-build.sh), שכותב `<initrd>.new` **ליד** הקיים ורק אז
  מחליף.
* **ה-DB ו-/var/tmp — 1 GiB:** ‏sqlite ב-WAL, קובצי הביניים של בניית ה-initrd
  (‏IMAGESRV_BUILD_TMPDIR=/var/tmp), וטבלאות שהתקרה שלהן היא כבר
  `housekeeping` (‏#1737) — כלומר שוליים, לא צמיחה.

יחד 9 GiB, ועיגול למעלה ל-10 GiB. ‏עדכון ב-VM (‏vmupdate) כבר מחזיק
‏`GROWTH_RESERVE_BYTES` משלו לגדילת דיסק העבודה — הרזרבה הזו **נוספת** עליו.

הרזרבה חלה על **כל** יעד, גם מיקום אחסון ברשת שאין עליו DB: מערכת קבצים
מלאה מכשילה גם את הכתיבה הבאה, ו-10 GiB על NAS זולים מכלל שני. היא קבוע בקוד
ולא הגדרה בקונסולה: היא מגינה על השרת מפני המפעיל, לא העדפה שלו.

הפונקציות כאן **אינן קוראות את הדיסק** — כל קורא מודד בעצמו (נקודת ההזרקה
של הבדיקות שלו נשארת במקומה), ומעביר את המספר. "לא נקרא" נשאר אצל הקורא
כשל בשם (עיקרון 5).
"""

from __future__ import annotations

from typing import Callable

#: ‏10 GiB — הפירוט בראש הקובץ. ‏טסט משווה את ההנמקה ל-journald לקובץ ההתקנה.
SYSTEM_RESERVE_BYTES = 10 * 1024 ** 3

#: כל כמה בייטים זרם בלי אורך מוצהר בודק שוב את המקום הפנוי. ‏64MB: ב-200MB/s
#: זה ‏3 בדיקות בשנייה (‏statvfs — מיקרו-שניות), והחריגה המרבית מתחת לרזרבה
#: היא 64MB מתוך 10GB.
STREAM_CHECK_EVERY = 64 * 1024 * 1024


def reserve() -> int:
    """הערך בזמן הקריאה — לא נלכד בייבוא, כדי שבדיקה אחת תוכל לכבות אותו."""
    return SYSTEM_RESERVE_BYTES


def gb(n: int | float) -> str:
    return f"{n / 1e9:.1f} GB"


def refusal(free: int, need: int, what: str) -> str | None:
    """הסירוב בעברית ובמספרים, או `None` כשנשאר מספיק.

    ‏`need` הוא מה **שהכתיבה** תתפוס (בלי הרזרבה); ‏`what` — מה נכתב, בצורה
    שנקראת אחרי "ל" ("אחסון הקליטה", "העלאת האימג'"). ‏"אין מקום" נאמר כמו הסירובים
    הקיימים: כמה נדרש, כמה פנוי, ומה נשמר למערכת."""
    keep = reserve()
    if free - need >= keep:
        return None
    # הבייטים המדויקים בסוגריים: ‏GB בספרה אחת מעגל עד 50MB, והמספר הוא הראיה (עיקרון 5ב).
    return (f"אין מקום בשרת ל{what}: נדרשים {gb(need)} ({need} בייט), "
            f"פנויים {gb(free)} ({free} בייט), ו-{gb(keep)} נשמרים למערכת "
            f"(ה-DB, היומנים ועדכוני השרת) — חסרים {gb(need + keep - free)}")


class ReserveReached(Exception):
    """זרם בלי אורך מוצהר הגיע לרזרבה. ההודעה היא הסירוב, מוכן למפעיל."""


class StreamGuard:
    """בדיקת הרזרבה **תוך כדי** זרם שאורכו אינו ידוע מראש (קליטה ב-chunked,
    העלאת tar מהדפדפן). ‏`free_of()` מודד; ‏`wrote(n)` אחרי כל אצווה. מדידה
    שנכשלה (‏`OSError`) עוצרת את הזרם בשם — "לא נמדד" אינו "יש מקום"."""

    def __init__(self, free_of: Callable[[], int], what: str, every: int | None = None):
        self._free_of = free_of
        self._what = what
        self._every = STREAM_CHECK_EVERY if every is None else every
        self._since = 0

    def check(self) -> None:
        try:
            free = self._free_of()
        except OSError as exc:
            raise ReserveReached(f"{self._what} נעצרה: לא ניתן לקרוא כמה מקום פנוי "
                                 f"בשרת: {exc}") from exc
        if free < reserve():
            raise ReserveReached(
                f"{self._what} נעצרה: נשארו בשרת {gb(free)} פנויים, ו-{gb(reserve())} "
                f"נשמרים למערכת (ה-DB, היומנים ועדכוני השרת)")

    def wrote(self, n: int) -> None:
        self._since += n
        if self._since >= self._every:
            self._since = 0
            self.check()
