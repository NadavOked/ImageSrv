"""מגבלת אורך הקובץ — ספירה אחת, קיר אחד, ונורה לפניו (#483).

עד כאן הכלל היה חמישה קבועים נפרדים בחמישה קבצים, ו**שתי שיטות
ספירה שונות**: ‏`test_agent.py` ספר ב-`splitlines()`, וארבעת האחרים
ב-`count("\n") + 1` — שהוא תמיד אחד יותר על קובץ שמסתיים בשורה
חדשה. כלומר הם אכפו 299 בזמן שכתבו 300.

והחשוב מזה: הגבול היה **קיר בלבד**. הוא התגלה רק אחרי שחצו אותו,
ופעמיים באותו לילה הוא נחצה בלי שאף PR היה אדום לבדו — ‏#456 ו-#478.
במקרה של #478 המנהל של #459 אף **הזיז שער ל-`expand.sh` דווקא כדי לא
לחצות ב-`restore.sh`**: הוא לא פתר, הוא העביר את החציה למקום אחר.

לכן יש כאן שני ספים: ב-280 נדלקת נורה שנשארת ירוקה, וב-300 נופל.
הפיצול נעשה כשעוד יש זמן לעשות אותו בשיקול דעת.

**המספר עצמו הוא הכרעה של נדב** — 300 שורות הוא גודל שמודל שפה קורא
ומבין בשלמותו. ההיוריסטיקה עומדת; מה שלא עמד היה צורת האכיפה.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from agentlib import MIN_INSTALLER_LIB_FILES, checked_files, lib_files

#: הקיר — מעבר לו הטסט נופל.
MAX_LINES = 300

#: הנורה — מכאן ומעלה נרשמת אזהרה, והטסט נשאר ירוק.
WARN_LINES = 280


def guarded_shell_files(root: Path | str) -> list[Path]:
    """POSIX initramfs code covered by the 300-line wall."""
    root = Path(root)
    agent = root / "agent"
    installer = root / "installer"
    return sorted([
        agent / "init", agent / "imagesrv-agent",
        *lib_files(agent / "lib"),
        installer / "imagesrv-install",
        *checked_files(installer / "lib", "*.sh", MIN_INSTALLER_LIB_FILES),
    ])


class FileGrowingWarning(UserWarning):
    """קובץ שעבר את סף האזהרה ועדיין לא את הקיר.

    ‏pytest מציג אותה ב-warnings summary, ולכן היא נראית בלי להכשיל
    ריצה. ‏מחלקה נפרדת ולא `UserWarning` גנרי, כדי שאפשר יהיה לסנן
    עליה (`-W error::sizelimit.FileGrowingWarning`) כשרוצים קיר קשיח.
    """


def count_lines(path: Path | str, *, encoding: str = "utf-8") -> int:
    """מספר השורות בקובץ. ‏`splitlines()` ולא `count("\n") + 1`:
    קובץ בן 300 שורות שמסתיים בשורה חדשה הוא 300, לא 301."""
    return len(Path(path).read_text(encoding=encoding).splitlines())


def assert_within_limit(path: Path | str, *, encoding: str = "utf-8",
                        name: str | None = None) -> int:
    """נופל מעל `MAX_LINES`, מזהיר מ-`WARN_LINES`, ומחזיר את הספירה.

    מחזיר את המספר כדי שהקורא יוכל לטעון עליו עוד משהו — ולא כדי
    שיסתמך על היעדר חריגה כראיה (עיקרון 5)."""
    label = name or Path(path).name
    lines = count_lines(path, encoding=encoding)
    if lines > MAX_LINES:
        raise AssertionError(
            f"{label}: {lines} שורות, המגבלה {MAX_LINES}")
    if lines >= WARN_LINES:
        warnings.warn(
            f"{label}: {lines} שורות — {MAX_LINES - lines} מהקיר. "
            f"לפצל עכשיו בשיקול דעת, לא ב-{MAX_LINES + 1} בלחץ.",
            FileGrowingWarning, stacklevel=2)
    return lines
