"""`hashFiles()` על נתיב מחוץ ל-`GITHUB_WORKSPACE` היא "ריק = דלג".

`hashFiles()` של GitHub Actions מסננת כל קובץ שאינו מתחת ל-
`GITHUB_WORKSPACE` ("Ignore '...' since it is not under
GITHUB_WORKSPACE"), ואם לא נותר אף קובץ היא מחזירה **מחרוזת ריקה** —
לא שגיאה. `if: hashFiles('/tmp/x') != ''` הוא לכן `false` **תמיד**,
גם כשהקובץ קיים ומלא.

זה הכשל הגרוע ביותר בעיקרון 5: השלב מדולג, שום דבר אינו אדום, וה-job
יוצא ירוק בלי לעשות דבר. ב-#538 זה היה `idea-refine.yml` — השלב
המדולג הוא זה שכותב את `READY`, ולכן גם שני השלבים שאחריו דולגו, וכל
תפקיד ה-workflow לא רץ מעולם.

בדיקת קיום נכתבת כ-`test -s <path>` בתוך ה-`run:` — היא **נכשלת
בקול** כשהקובץ חסר, וזה ההפך מדילוג שקט.
"""
import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent /
                    ".github" / "workflows").glob("*.yml"))

# hashFiles('...') או hashFiles("..."), עם רווחים כרצונך.
HASHFILES = re.compile(r"""hashFiles\(\s*(['"])(.*?)\1""", re.S)


def _text(p):
    return p.read_text(encoding="utf-8")


def _outside_workspace(pattern):
    """האם הדפוס הזה לעולם אינו יכול להימצא מתחת ל-GITHUB_WORKSPACE."""
    # hashFiles מקבלת כמה דפוסים מופרדים בשורות חדשות.
    for line in pattern.splitlines():
        p = line.strip()
        if not p:
            continue
        p = p.lstrip("!")               # שלילה בגלוב
        if p.startswith(("/", "\\")):   # נתיב מוחלט פוסיקסי
            return p
        if re.match(r"^[A-Za-z]:[\\/]", p):   # נתיב מוחלט בווינדוס
            return p
        if p.split("/")[0] == ".." or p.split("\\")[0] == "..":
            return p
    return None


def test_there_are_workflows_to_check():
    """רשימה ריקה אינה 'הכול תקין' — היא בדיקה שלא רצה."""
    assert len(WORKFLOWS) >= 5, f"נמצאו רק {len(WORKFLOWS)} workflows"


def test_the_detector_catches_the_broken_pattern():
    """בקרה חיובית לגלאי עצמו — אחרת regex שבור היה 'ירוק' על הכול."""
    hits = HASHFILES.findall("if: ${{ hashFiles('/tmp/thread.json') != '' }}")
    assert hits and hits[0][1] == "/tmp/thread.json"
    assert _outside_workspace("/tmp/thread.json") == "/tmp/thread.json"
    assert _outside_workspace("server/requirements.txt") is None


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_hashfiles_is_never_called_on_a_path_outside_the_workspace(path):
    src = _text(path)
    for _quote, pattern in HASHFILES.findall(src):
        bad = _outside_workspace(pattern)
        assert bad is None, (
            f"{path.name}: \u200fhashFiles('{bad}') — נתיב מחוץ ל-"
            f"GITHUB_WORKSPACE. \u200fhashFiles מסננת אותו ומחזירה מחרוזת "
            f"ריקה **תמיד**, ולכן `if` שנשען עליה מדלג את השלב בשקט "
            f"וה-job יוצא ירוק בלי לעשות דבר. לבדיקת קיום: "
            f"`test -s {bad}` בתוך ה-run. ר' #538."
        )
