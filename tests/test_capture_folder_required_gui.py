"""#1251 (נדב, 10/10, על ה-HP בקליטה): בגואי הנייטיב אין "ללא תיקייה".

`folder_options` ב-`screens_capture.c` הוסיף "ללא תיקייה" כאפשרות
הראשונה, ו-`folder_sel=0` שלח `folder=` ריק. עכשיו הרשימה היא התיקיות
בלבד, `folder_sel` הוא 1-based ו-0 הוא "טרם נבחרה"; המסך הסגור כותב
"בחרו תיקייה", או — כשאין אף תיקייה — "אין תיקיות — צרו חדשה ב-'+ חדשה'".
`start_capture` מסרב לתיקייה ריקה, והשרת אוכף ממילא (`test_capture.py`).

**בקרה שלילית:** על הקוד הישן מצב בלי שום `folder=` ומצב עם תיקייה
אחת ששמה "ללא תיקייה" מצוירים זהים (שניהם מציגים את אותה מחרוזת) —
טסט ה-`--png` נופל; ובדיקת המקור נופלת על המחרוזת שב-`folder_options`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, GUI, _build_gui, _pkgconfig, _png

SRC = GUI / "src"


def test_the_capture_screen_offers_no_no_folder_option():
    """רץ בכל מקום (גם בווינדוס): המחרוזת אינה במסך הקליטה ואינה ב-main.c,
    ונשארת במסך השחזור — אימג' ישן בלי תיקייה עדיין מוצג בשמה."""
    assert "ללא תיקייה" not in (SRC / "screens_capture.c").read_text(encoding="utf-8")
    assert "ללא תיקייה" not in (SRC / "main.c").read_text(encoding="utf-8")
    # owner 10/10: the restore picker is folder-first; the name of the
    # no-folder group lives in one helper (folder_label), shared with the room
    assert "folder_label(" in (SRC / "screens_restore.c").read_text(encoding="utf-8")
    assert "ללא תיקייה" in (SRC / "screens_rounds.c").read_text(encoding="utf-8")


def _state(path: Path, folders: list[str]) -> Path:
    path.write_text(
        "disk=/dev/sda|Samsung SSD|500107862016|1|0\n"
        + "".join(f"folder={f}\n" for f in folders)
        # --png renders every card; "message" needs msg_title or it falls to the menu.
        + "message=x|y\n",
        encoding="utf-8", newline="\n")
    return path


@requires_native(
    ("bash", BASH),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_with_no_folders_the_select_says_to_create_one(tmp_path):
    """כרטיס `pick` פעמיים: בלי תיקיות כלל, ועם תיקייה אחת ששמה
    "ללא תיקייה" (שנבחרת ב-`--png`). על הקוד הישן שני הרינדורים זהים —
    בלי תיקיות הוצגה בדיוק אותה מחרוזת. עכשיו הם שונים."""
    binary = _build_gui(tmp_path)
    _png(binary, tmp_path / "none", _state(tmp_path / "none.txt", []))
    _png(binary, tmp_path / "named", _state(tmp_path / "named.txt", ["ללא תיקייה"]))
    for theme in ("light", "dark"):
        none = (tmp_path / f"none-pick-{theme}.png").read_bytes()
        named = (tmp_path / f"named-pick-{theme}.png").read_bytes()
        assert none != named, f"{theme}: no folders still draws a 'no folder' choice"
