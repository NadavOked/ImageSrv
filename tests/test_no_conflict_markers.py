"""שום קובץ מנוהל אינו נושא סמני merge conflict שלא נפתרו.

CHANGELOG.md הגיע ל-main עם `<<<<<<< HEAD`/`=======`/`>>>>>>>` אחרי
מיזוג רב-ענפי, ואף בדיקה לא תפסה זאת — כי הבדיקה הזאת לא הייתה קיימת.
עיקרון 5: מיזוג שהשאיר סמנים הוא כשל גלוי, ועכשיו הוא נכשל בקול."""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MARKERS = ("<<<<<<< ", ">>>>>>> ")
# `=======` לבדו הוא גם קו setext של markdown; לכן נחשב סמן רק כשהוא
# בדיוק שבעה תווים בשורה משלו, לצד לפחות אחד מהשניים האחרים בקובץ.
_MID = "======="


def _tracked_text_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True,
        check=True, stdin=subprocess.DEVNULL,
    ).stdout
    skip = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".pdf",
            ".woff", ".woff2", ".zst", ".gz")
    return [p for p in out.splitlines() if not p.lower().endswith(skip)]


def test_no_unresolved_conflict_markers() -> None:
    offenders = []
    for rel in _tracked_text_files():
        fp = _ROOT / rel
        try:
            text = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # דלג על הקובץ הזה עצמו — הוא מזכיר את הסמנים כמחרוזות.
        if rel.endswith("test_no_conflict_markers.py"):
            continue
        lines = text.splitlines()
        has_side = any(ln.startswith(_MARKERS) for ln in lines)
        for i, ln in enumerate(lines, 1):
            if ln.startswith(_MARKERS) or (ln == _MID and has_side):
                offenders.append(f"{rel}:{i}")
    assert not offenders, f"סמני merge conflict שלא נפתרו: {offenders}"
