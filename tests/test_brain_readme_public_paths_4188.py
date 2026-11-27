"""#4188: ה-README של המוח בריפו הציבורי לא מפנה לנתיב שאין בו, ולא אומר שמנוע החתיכות "קיים רק" בענף מעבדה.

`lab/...` ו-`handoffs/...` הם נתיבים פנימיים: למבקר מבחוץ אין גישה אליהם. מקור פנימי נכתב "מסמך פנימי" או כ-Issue.
הגבול הזה נאכף כאן על `server/brain/README.md` בלבד (ה-README של תיקיות המשנה מחוץ לטווח של #4188).

**בקרה שלילית:** על ה-README של v0.101.0 (שורות 10-13: `lab/vm-1113c`, `handoffs/brain-inventory/`,
`handoffs/brain-design-draft.md` ו"קיים רק שם") שלושת הטסטים הראשונים נופלים.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "server" / "brain" / "README.md").read_text(encoding="utf-8")
# `lab/` או `handoffs/` בתחילת נתיב. `tools/lab/` הוא נתיב אחר ולכן לא נתפס (תו לפני המילה מבטל).
INTERNAL = re.compile(r"(?<![\w./-])(?:lab|handoffs)/[\w./-]*")
ENGINE = ("server/rounds/chunksend.py", "server/rounds/repair.py", "agent/chunkcast.c")


def test_the_readme_names_no_path_that_is_not_in_the_public_repo():
    found = sorted({m.group(0) for m in INTERNAL.finditer(README)})
    assert not found, f"נתיבים פנימיים ב-server/brain/README.md (כתבו 'מסמך פנימי' או מספר Issue): {found}"


def test_the_readme_does_not_say_the_chunk_engine_exists_only_in_a_lab_branch():
    assert "קיים רק שם" not in README


def test_the_readme_says_where_the_chunk_engine_is_and_it_is_there():
    for path in ENGINE:
        assert path in README, f"ה-README לא אומר איפה {path}"
        assert (ROOT / path).is_file(), f"ה-README מצביע על {path} והוא לא קיים"


def test_the_pattern_catches_the_old_paths_and_not_a_nested_one():
    """הגנה על הדפוס עצמו: שלא ייחלש לדפוס שלא תופס כלום."""
    for old in ("`lab/vm-1113c`", "(`handoffs/brain-inventory/`", "handoffs/brain-design-draft.md", "lab/decisions-map-1118.md"):
        assert INTERNAL.search(old), old
    assert not INTERNAL.search("tools/lab/example.c")
