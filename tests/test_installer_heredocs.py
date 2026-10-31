"""‏#1149 — heredoc לא-מצוטט במתקין מרחיב כל `$NAME`, גם בתוך הערה.

‏`setup-boot-server.sh` רץ תחת `set -u`, ולכן `$IMAGESRV_STORAGE_ARGS` בשורת
הערה בתוך `write_file … <<EOF` הפיל **כל** התקנה (גם `--dry-run`) מאז #734 —
והטסטים היו ירוקים כי הם בדקו את הטקסט של הסקריפט, לא הרצה. הבדיקה כאן
סטטית ורצה גם בווינדוס: כל משתנה שמורחב בתוך heredoc לא-מצוטט חייב
להיות מוגדר איפשהו בסקריפט (השמה, `local`, `read`, `for`), או להיות
מוברח (`\$`)."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"

# משתני סביבה שהסקריפט קורא ואינו משים — מותרים רק אם יש להם ברירת מחדל
# `${NAME:-…}`; `$NAME` חשוף מהם היה נופל תחת set -u בדיוק כמו #1149.
#: השמה בכל מקום בשורה (‏`A=1; B=2` על שורה אחת), לא רק בתחילתה.
_ASSIGN = re.compile(r"(?<![\w$-])([A-Za-z_]\w*)=")
_READ = re.compile(r"\bread\b[^\n]*?\s(-r\s+)?([A-Za-z_]\w*)\s*$", re.M)
_FOR = re.compile(r"^\s*for\s+([A-Za-z_]\w*)\s+in\b", re.M)
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)^\s*\2\s*$", re.S | re.M)
_REF = re.compile(r"(?<!\\)\$(?:\{([A-Za-z_]\w*)(?::?[-+=?]|[^}]*)?\}|([A-Za-z_]\w*))")


def _defined_names(script: str) -> set[str]:
    # ההשמות נספרות רק בקוד: לא בתוך גוף heredoc (שם `Environment=X=…` אינו
    # השמה של bash) ולא בשורות הערה — אחרת ההערה שהפילה את #1149 הייתה
    # "מגדירה" את המשתנה של עצמה, והבקרה השלילית הייתה ירוקה.
    code = _HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0] + "\n", script)
    code = "\n".join(line for line in code.split("\n")
                     if not line.lstrip().startswith("#"))
    names = set(_ASSIGN.findall(code))
    names |= {m.group(2) for m in _READ.finditer(script)}
    names |= set(_FOR.findall(script))
    return names


def _unquoted_heredocs(script: str) -> list[tuple[str, str]]:
    return [(m.group(2), m.group(3)) for m in _HEREDOC.finditer(script)
            if m.group(1) == ""]


def test_every_variable_expanded_inside_an_unquoted_heredoc_is_defined():
    script = INSTALLER.read_text(encoding="utf-8")
    defined = _defined_names(script) | {"1", "2", "@", "#", "?", "$", "!", "0"}
    blocks = _unquoted_heredocs(script)
    assert blocks, "לא נמצאו heredocs לא-מצוטטים — הבדיקה לא בודקת כלום"
    loose: list[str] = []
    for tag, body in blocks:
        for m in _REF.finditer(body):
            name = m.group(1) or m.group(2)
            has_default = m.group(0).startswith("${") and re.match(r"\$\{\w+:?[-+=?]", m.group(0))
            if name not in defined and not has_default:
                line = body[: m.start()].count("\n") + 1
                loose.append(f"<<{tag} שורה {line}: {m.group(0)}")
    assert loose == [], (
        "משתנים שמורחבים בתוך heredoc לא-מצוטט ואינם מוגדרים בסקריפט — "
        f"תחת set -u זה מפיל את ההתקנה (#1149): {loose}")
