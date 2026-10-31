"""השומר של הטסטים עצמם: אף טסט אינו נותן לסוכן את ‏`/dev` של המארח.

ב-15/09 שלוש-עשרה קריאות ‏`DEVROOT=/dev` בטסטים הריצו את ‏`run_restore`/
‏`run_restore_drawers` **האמיתיים** על יעד בשם ‏`sda` והכשילו אותו בכוונה.
עד #845 הכישלון רק כתב ‏`state=failed`; מאז #845 הוא קורא ל-‏`mark_failed_disk`
→ ‏`sgdisk -c 1:IMAGESRV-FAILED /dev/sda`. בווינדוס אין ‏sgdisk ואין ‏`/dev/sda`,
ובמעבדה הטסטים רצים כ-root עם ‏sgdisk אמיתי — **דיסק המערכת של שרת
המעבדה** יצא מכל ריצת שער עם שלוש מחיצות בשם ‏IMAGESRV-FAILED וה-ESP
מומר ל-8300. ‏`apply_gpt` באותו נתיב מוחק את הטבלה כולה על ‏`"$DEVROOT/$1"`.

הכלל: ‏`DEVROOT` בטסט הוא תמיד תיקייה של הטסט. מי שצריך "אין דיסק כזה"
מקבל אותו מתיקייה ריקה, לא מ-‏`/dev`."""
from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: `DEVROOT=/dev` בכל צורת ציטוט, וגם `DEVROOT=/dev/...` — כל דבר שמתחיל
#: ב-`/dev` של המארח. ‏`DEVROOT={posix(run)!r}/dev` תקין: הוא מתחיל בציטוט.
HOST_DEV = re.compile(r"""DEVROOT=["']?/dev\b""")


def test_no_test_hands_the_agent_the_host_dev():
    offenders = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if HOST_DEV.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_the_guard_catches_every_spelling():
    for bad in ("DEVROOT=/dev; ", 'DEVROOT="/dev" ', "DEVROOT='/dev'", "DEVROOT=/dev/"):
        assert HOST_DEV.search(bad), bad
    for good in ("DEVROOT={posix(run)!r}/dev", "DEVROOT={posix(box)!r}", 'DEVROOT="$BOX/dev"'):
        assert not HOST_DEV.search(good), good


def test_the_run_itself_detaches_the_host_dev():
    """הריצה מייצאת DEVROOT לתיקייה שאינה קיימת (conftest → hostguard):
    טסט ש**שכח** DEVROOT מקבל "אין דיסק כזה", לא את /dev של המארח."""
    import os
    import hostguard
    assert os.environ.get("DEVROOT") == hostguard.NO_HOST_DEV
    assert not Path(hostguard.NO_HOST_DEV).exists()

