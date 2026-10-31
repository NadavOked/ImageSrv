"""כלי מערכת חסר: מתי דילוג הוא אמת, ומתי הוא שקר.

‏`skipif` הוא הדרך של pytest לומר "אי אפשר לבדוק כאן". על עמדת הפיתוח
בווינדוס זו אמת: אין gcc, אין hivexget, ו-fanout צריך fifo של POSIX.
על שרת המעבדה וב-CI זו לא אמת אלא תקלה — שם הכלים אמורים להיות, וחבילה
שדילגה בשלמותה נספרה ירוקה בדיוק כמו חבילה שעברה. ככה שלוש חבילות —
כל סוכן ה-POSIX, ‏`fanout`, ו-‏`hivewrite` — יכלו לא לרוץ בלי שאיש
יבחין, ו-`pytest` יצא 0 (#52).

ההבחנה נשענת על דגל מפורש ולא על ניחוש: כש-`IMAGESRV_REQUIRE_NATIVE=1`
דלוק, כלי חסר הוא **כישלון**. הדגל כבוי כברירת מחדל, כדי שהעמדה
בווינדוס תמשיך לדלג כרגיל — שם הדילוג לגיטימי.

שתי שכבות, כי אף אחת מהן לבדה אינה מספיקה:

* ‏`requires_native` — לכל חבילה שיודעת איזה כלי היא צריכה. כשהדגל דלוק
  היא מסמנת את הטסטים בסימון `missing_native`, ו-`fail_on_missing_native`
  מפיל אותם בשמם, עם שם הכלי החסר.
* ‏`SkipAudit` — רשת ביטחון על **כל** דילוג בריצה, מאיזו סיבה שלא תהיה:
  ‏`importorskip`, ‏`pytest.skip()` באמצע טסט, וכל `skipif` שייכתב מחר
  ולא ייעבור דרך כאן. כשהדגל דלוק, ריצה עם ולו דילוג אחד אינה ירוקה.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ENV_FLAG = "IMAGESRV_REQUIRE_NATIVE"
MISSING_MARK = "missing_native"

#: תחילית קבועה בסיבת הדילוג — ‏`SkipAudit` מזהה לפיה דילוג **מוצהר**
#: ומפריד אותו מדילוג סתמי. טקסט, כי זה מה ש-pytest מוסר בדוח.
DECLARED_PREFIX = "תחנת פיתוח בלבד"

#: תג ASCII בשורת הסיכום, כדי ש-`tools/verify.py` יוכל לקרוא את המספר.
#: לא עברית: בווינדוס הקונסולה היא cp1252 והפלט נופל ל-backslashreplace,
#: ואז כל עברית בשורה הופכת ל-`\uXXXX` — תג ASCII שורד את זה.
DECLARED_TAG = "declared-skips"


#: ספריות ה-sbin. הסוכן רץ כ-root ושם הן ב-PATH; משתמש רגיל בדביאן (‏`test`
#: על ה-Testrunner) מקבל PATH בלעדיהן, ואז `which("sgdisk")` ריק וכל טסט
#: של sgdisk אמיתי דילג בשקט — ורגרסיה של rc5 לא נראתה (#1594).
SBIN = ("/usr/local/sbin", "/usr/sbin", "/sbin")


def with_sbin(path: str, isdir=os.path.isdir) -> str:
    """‏PATH של POSIX ובסופו כל ספריית sbin שקיימת וחסרה בו. בסוף ולא
    בהתחלה: מה שהמשתמש כבר מצא קודם נשאר קודם."""
    parts = [p for p in path.split(":") if p]
    return ":".join(parts + [d for d in SBIN if d not in parts and isdir(d)])


def native_required() -> bool:
    """נקרא בכל פעם מחדש, ולא נקבע ביבוא — הטסטים מזיזים את הדגל."""
    return os.environ.get(ENV_FLAG, "").strip().lower() not in ("", "0", "no", "false")


def missing_requirements(
    *tools: str | tuple[str, object], paths: tuple[str, ...] = ()
) -> list[str]:
    """מה מהדרישות אינו כאן. כל דרישה היא שם כלי ל-`which`, או זוג
    ‏(שם, ערך שנמצא) לכלי שהחבילה מאתרת בעצמה — ‏bash של Git למשל."""
    missing: list[str] = []
    for tool in tools:
        label, found = tool if isinstance(tool, tuple) else (tool, shutil.which(tool))
        if not found:
            missing.append(label)
    missing += [str(path) for path in paths if not Path(path).exists()]
    return missing


def requires_native(
    *tools: str | tuple[str, object],
    paths: tuple[str, ...] = (),
    posix: bool = False,
    why: str = "",
):
    """סימון pytest: דילוג בעמדה שאין בה את הכלי, כישלון כשהדגל דלוק."""
    missing = missing_requirements(*tools, paths=paths)
    if posix and os.name == "nt":
        missing.append("מערכת POSIX")
    if not missing:
        return pytest.mark.skipif(False, reason="הכלים כאן")
    if native_required():
        return getattr(pytest.mark, MISSING_MARK)(", ".join(missing))
    tail = f" ({why})" if why else ""
    return pytest.mark.skipif(True, reason=f"חסר כאן: {', '.join(missing)}{tail}")


def requires_dev_workstation(*tools: str | tuple[str, object], why: str = ""):
    """סימון pytest לכלי שקיים **רק** על תחנת הפיתוח, ובמכוון.

    ‏`requires_native` מניח שהכלי אמור להיות בכל מקום שבו הדגל דלוק, ולכן
    היעדרו שם הוא תקלת סביבה. ‏PowerShell הוא המקרה ההפוך: הוא נעדר משרת
    המעבדה **לפי החלטה** — במכללה לא יהיה PowerShell על השרת, והמעבדה
    אמורה להישאר דומה לשטח. התקנתו שם היתה מוסיפה תלות על סביבת האימות
    עצמה כדי שהאימות ייראה ירוק (#295).

    ולכן דילוג כאן חוקי בכל סביבה — אבל **לא שקט**: הוא נושא את
    ‏`DECLARED_PREFIX`, ‏`SkipAudit` סופר אותו בנפרד, ו-`conftest` מדפיס
    את המספר ואת הסיבה בסוף כל ריצה. "תמיד אדום שם" מנרמל את עצמו עד
    שכשל אמיתי נבלע — וזה בדיוק מה שהוחלף כאן בדילוג שאומר את שמו.

    אין כאן `posix`/`paths`: הדרישה היא כלי בשם, וזה מה שנמדד.
    """
    if not tools:
        raise ValueError("דרישה בלי כלי מדלגת תמיד — זו מחיקה, לא דילוג")
    missing = missing_requirements(*tools)
    if not missing:
        return pytest.mark.skipif(False, reason="הכלים כאן")
    tail = f" — {why}" if why else ""
    return pytest.mark.skipif(
        True, reason=f"{DECLARED_PREFIX}: חסר {', '.join(missing)}{tail}"
    )


def fail_on_missing_native(item) -> None:
    """נקרא מ-`pytest_runtest_setup`: הופך את הסימון לכישלון בשם הטסט."""
    for mark in item.iter_markers(MISSING_MARK):
        pytest.fail(
            f"{ENV_FLAG}=1 והכלי חסר: {mark.args[0]}. כאן הכלים אמורים להיות,"
            " ולכן זו תקלת סביבה ולא סיבה לדלג.",
            pytrace=False,
        )


def skip_reason(report) -> str:
    """הסיבה שדווחה לדילוג. ‏longrepr של דילוג הוא (קובץ, שורה, סיבה)."""
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr or "בלי סיבה")


class SkipAudit:
    """כל דילוג בריצה, לפי nodeid — כדי שאפשר יהיה לומר *מה* לא נבדק."""

    def __init__(self) -> None:
        self.skipped: dict[str, str] = {}

    def record(self, report) -> None:
        if report.skipped and not self._ran_and_failed_as_predicted(report):
            self.skipped.setdefault(report.nodeid, skip_reason(report))

    @staticmethod
    def _ran_and_failed_as_predicted(report) -> bool:
        """pytest reports a failed ``xfail`` as outcome *skipped* with ``wasxfail``
        set. That test RAN and failed as predicted (and ``strict=True`` turns an
        unexpected pass into a failure), so it is not a test that did not run.
        Only the ``call`` phase counts: ``xfail(run=False)`` is skipped in
        ``setup`` and never ran -- that one stays on the audit."""
        return hasattr(report, "wasxfail") and getattr(report, "when", "") == "call"

    def declared(self) -> dict[str, str]:
        """הדילוגים שהוצהרו מראש — כלי תחנת-פיתוח שאינו כאן במכוון."""
        return {node: reason for node, reason in self.skipped.items()
                if DECLARED_PREFIX in reason}

    def unexplained(self) -> dict[str, str]:
        """כל השאר. אלה שהדגל אמור להפיל."""
        return {node: reason for node, reason in self.skipped.items()
                if DECLARED_PREFIX not in reason}

    def verdict(self) -> list[str]:
        """שורות לדוח — ריק כשאין מה לדווח."""
        unexplained = self.unexplained()
        if not unexplained or not native_required():
            return []
        lines = [
            f"{ENV_FLAG}=1 אבל {len(unexplained)} טסטים דולגו — כאן היעד אפס:"
        ]
        lines += [f"    {nodeid}  ←  {reason}"
                  for nodeid, reason in sorted(unexplained.items())]
        return lines

    def notes(self) -> list[str]:
        """הדילוגים המוצהרים — נספרים ומודפסים תמיד, ואינם מפילים.

        זו כל הנקודה של #295: לא להשתיק את האדום ולא להשאיר אותו. ריצה
        שדילגה חייבת לומר **כמה** ו**למה**, גם כשהיא ירוקה.
        """
        declared = self.declared()
        if not declared:
            return []
        lines = [f"{len(declared)} טסטים דולגו במוצהר, לא כישלון "
                 f"[{DECLARED_TAG}={len(declared)}]:"]
        lines += [f"    {nodeid}  ←  {reason}"
                  for nodeid, reason in sorted(declared.items())]
        return lines
