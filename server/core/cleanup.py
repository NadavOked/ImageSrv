"""מחיקת אזור עבודה, ושורת יומן כשהוא נשאר (עיקרון 5; מפת המגע A-DSK-17/18/22/23).

``shutil.rmtree(..., ignore_errors=True)`` לבדו הופך "לא הצלחנו למחוק" ל"נמחק":
מה שנשאר (staging של קליטה, ``disk.raw`` של עדכון VM) כבר אינו אימג', אבל
הוא תופס מקום בדיסק הספרייה, שמשותף לסבבים, עד שמישהו יידע עליו. זו
אותה צורה של ``archive._clear``, במקום אחד לכל האתרים.

"נמחק" נקבע בקריאה חוזרת (``os.path.lexists``), לא בהיעדר חריגה.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("imagesrv.cleanup")


def remove_tree(path: str | Path, what: str) -> bool:
    """מוחק את ``path`` ואינו זורק. True = הנתיב איננו (נקרא בחזרה). False =
    משהו נשאר, ושורת warning אומרת מה, איפה ולמה."""
    errors: list[str] = []

    def _note(_func, failed, exc: BaseException) -> None:
        if not isinstance(exc, FileNotFoundError):   # נמחק בינתיים = מה שרצינו
            errors.append(f"{failed}: {exc}")

    shutil.rmtree(path, onexc=_note)
    if not os.path.lexists(path):
        return True
    log.warning("%s left behind at %s: %s", what, path,
                "; ".join(errors[:3]) or "no error was reported")
    return False
