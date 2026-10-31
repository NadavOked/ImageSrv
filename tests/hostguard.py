"""מה שהריצה קוראת מהמכונה שהיא רצה עליה (‏#113).

‏`hygiene.py` חוסם *כתיבה* לעולם — תהליך אמיתי ששורד את הריצה. כאן
נחסמת ה*קריאה*: רשימת כרטיסי הרשת של המכונה.

הסיפור: ‏`console_dhcp.default_hooks()` נותן ל-hook בשם `interfaces`
ברירת מחדל שקוראת את `/sys/class/net` **האמיתי**. בדיקה ששכחה להזריק
`dhcp_hooks` לא נכשלת — היא מקבלת את הכרטיסים של מי שמריץ אותה. במעבדה
יש `eth1` ולכן הכל ירוק; ל-runner של GitHub אין, ושם אותה בדיקה מקבלת
‏404. וגרוע מזה: על תחנת הפיתוח נמצאה תיקייה `C:\\sys\\class\\net` עם
‏`eth0` ו-`eth1` מזויפים — כלומר גם "עבר אצלי" נשען על מכונה שסודרה
כדי שהבדיקה תעבור. שלוש סביבות, שלוש תשובות שונות, ואף אחת מהן אינה
מה שהבדיקה חושבת שהיא בודקת.

הדפוס הנכון כבר קיים בפרויקט — ‏`dhcp_hooks` מוזרק ב-`create_app` —
ולכן החוסר כאן אינו מנגנון אלא **אכיפה** שלו:

1. **חסימה** — ‏`block_real_host_reads()` סורק את **כל** חבילת `server`
   ומחליף כל קישור ל-`dhcp_host.list_interfaces` בשומר שזורק. קריאה עם
   שורש מפורש (‏`list_interfaces(tmp_path)`) עוברת כרגיל, כי זו בדיוק
   הזרקה; קריאה בברירת המחדל נכשלת בשם הבדיקה שגרמה לה.
2. **ראיה שהשומר בכלל הותקן** — הסריקה מחזירה את רשימת האתרים שתוקנו
   ואת המודולים שלא ניתן היה לייבא, ו-`test_no_real_host.py` מאמת
   אותן. שומר שלא תפס כלום נראה בדיוק כמו שומר שאין מה לתפוס
   (עיקרון 5), ולכן הוא נמדד ולא מונח.

החסימה מותקנת פעם אחת לריצה ואינה מוחזרת: אין בדיקה שצריכה את הכרטיסים
של המכונה האמיתית, ולכן אין למי להחזיר אותם.
"""

from __future__ import annotations

import os
import importlib
import pkgutil

#: כל ניסיון לקרוא את המכונה האמיתית, לפי הסדר. שם הבדיקה מגיע
#: מהחריגה עצמה; זה כאן כדי שאפשר יהיה לספור בסוף ריצה.
blocked_host_reads: list[str] = []

#: אתרי הקישור שהוחלפו בפועל (‏`server.net.dhcp.list_interfaces` וכו').
patched_sites: list[str] = []

#: מודולים בחבילת `server` שהייבוא שלהם נכשל — ולכן **לא נסרקו**.
#: ריק אינו מובן מאליו: מודול שלא נסרק הוא חור בשומר, לא "אין בעיה".
unimportable_modules: list[str] = []

#: הפונקציה המקורית, מוחזקת כאן ולא על `dhcp_host` — קישור שנשמר
#: *בתוך* החבילה הנסרקת נתפס בסריקה עצמה ומוחלף בשומר, והשומר קורא
#: לעצמו. (זה קרה, וזו הייתה RecursionError.)
_original = None

MESSAGE = (
    "בדיקה קראה את רשימת כרטיסי הרשת של המכונה האמיתית (#113). "
    "הזריקו hook בשם `interfaces` — ‏`create_app(..., dhcp_hooks=...)`, "
    "‏`netcfg_hooks=...` או `health_hooks=...` — כמו בשאר הבדיקות. "
    "בלי זה הבדיקה בודקת את הכרטיסים של מי שמריץ אותה."
)


def _guarded_list_interfaces(sys_root=None):
    """‏`list_interfaces` של הבדיקות: שורש מפורש בלבד.

    ברירת המחדל בייצור היא `/sys/class/net`; כאן היא `None`, ולכן
    ההבדל בין "הזרקנו שורש" לבין "נפלנו על המכונה" הוא מפורש ולא
    מנוחש מתוך המחרוזת.
    """
    if sys_root is None:
        blocked_host_reads.append("list_interfaces()")
        raise AssertionError(MESSAGE)
    return _original(sys_root)


def _server_modules() -> list:
    """כל מודול בחבילת `server`, מיובא — כדי שהסריקה תהיה על הכל.

    מודול שלא יובא אינו מחזיק קישור *עדיין*, אבל יחזיק ברגע שמישהו
    ייבא אותו באמצע הריצה. לכן מייבאים הכל מראש, ומי שנכשל נרשם.
    """
    import server

    def failed_package(name):
        # walk_packages otherwise suppresses ImportError in a package and
        # silently omits its descendants.
        raise AssertionError(f"hostguard could not walk package {name}")

    discovered = list(pkgutil.walk_packages(
        server.__path__, server.__name__ + ".", onerror=failed_package))
    assert len(discovered) >= 155, (
        f"hostguard found {len(discovered)} server modules; expected >= 155"
    )
    modules = [server]
    for info in discovered:
        try:
            modules.append(importlib.import_module(info.name))
        except Exception as error:             # noqa: BLE001 — נרשם, לא נבלע
            line = f"{info.name}: {error}"
            if line not in unimportable_modules:
                unimportable_modules.append(line)
    return modules


def block_real_host_reads() -> list[str]:
    """מתקין את השומר בכל חבילת `server`. מחזיר את האתרים שתוקנו.

    ‏[] פירושו שלא נמצא מה לתפוס — או שאין שרת לייבא (עמדה בלי
    fastapi), או שהשומר לא עובד. הקורא (‏`test_no_real_host.py`) הוא
    שמכריע בין השניים; כאן לא מניחים.
    """
    global _original
    try:
        from server.net import dhcp_host
    except ImportError:                        # pragma: no cover — עמדה חסרה
        return []
    if _original is not None:
        return patched_sites                   # ריצה אחת, התקנה אחת
    _original = dhcp_host.list_interfaces
    for module in _server_modules():
        for name, value in list(vars(module).items()):
            if value is _original:
                setattr(module, name, _guarded_list_interfaces)
                patched_sites.append(f"{module.__name__}.{name}")
    return patched_sites


def unguarded_sites() -> list[str]:
    """אתרים שעדיין מחזיקים את הפונקציה המקורית — חייב להיות ריק.

    זו הקריאה החוזרת, לא ההנחה: הסריקה רצה שוב **אחרי** ההתקנה.
    """
    if _original is None:
        return ["השומר לא הותקן בכלל"]
    found = []
    for module in _server_modules():
        for name, value in vars(module).items():
            if value is _original:
                found.append(f"{module.__name__}.{name}")
    return sorted(found)


#: ‏`DEVROOT` שכל תהליך-בן של הריצה יורש כשאיש לא הגדיר אותו: תיקייה
#: שאינה קיימת. הסוכן נופל ל-`/dev` (‏sysinfo.sh) — וב-15/09 טסט
#: שהריץ את נתיב הכישלון של #845 בלי DEVROOT משלו סימן את דיסק המערכת
#: של שרת המעבדה (#870). ‏`test_tests_never_touch_host_dev.py` תופס
#: הגדרה מפורשת של ‏`/dev` של המארח; זה תופס את ה**היעדר**.
NO_HOST_DEV = "/nonexistent/imagesrv-tests-never-touch-host-dev"


def detach_host_dev() -> str:
    """מייצא ‏`DEVROOT` לתיקייה שאינה קיימת לכל הריצה, ומחזיר אותה.
    טסט שצריך התקן מצביע על תיקייה משלו — לעולם לא על ‏`/dev`."""
    os.environ["DEVROOT"] = NO_HOST_DEV
    return NO_HOST_DEV
