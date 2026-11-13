"""אף workflow אינו דוחק ריצה ממתינה על `main`.

GitHub מרשה **ריצה ממתינה אחת** בכל קבוצת concurrency. קבוצה שנקבעת
לפי `github.ref` בלבד היא קבוצה אחת לכל `main` — ולכן דחיפה חדשה
מבטלת את הממתינה שלפניה, גם כש-`cancel-in-progress` הוא false.

ריצה שבוטלה **אינה אדומה**, ולכן הקומיט נראה תקין. זה נמדד פעמיים:
#236 (13 מיזוגים בלי ריצת טסטים) ו-#258 (47 ריצות של סורקי אבטחה).
בפעם הראשונה התיקון הוחל על קובץ אחד, ואיש לא בדק את השאר — הטסט
הזה הוא מה שהיה תופס את זה.
"""
import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent /
                    ".github" / "workflows").glob("*.yml"))

# הדפוס הפגום: הקבוצה נקבעת לפי הענף בלבד.
BY_REF_ONLY = re.compile(r"group:\s*\$\{\{\s*github\.workflow\s*\}\}"
                         r"-\$\{\{\s*github\.ref\s*\}\}\s*$", re.M)


def _text(p):
    return p.read_text(encoding="utf-8")


def test_there_are_workflows_to_check():
    """רשימה ריקה אינה 'הכול תקין' — היא בדיקה שלא רצה."""
    assert len(WORKFLOWS) >= 5, f"נמצאו רק {len(WORKFLOWS)} workflows"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_groups_by_branch_alone(path):
    src = _text(path)
    assert not BY_REF_ONLY.search(src), (
        f"{path.name}: הקבוצה נקבעת לפי הענף בלבד. על push ל-main כל "
        f"דחיפה תדחוק את הריצה הממתינה שלפניה, והקומיט ייראה תקין בלי "
        f"שנבדק. ר' #236 ו-#258."
    )


@pytest.mark.parametrize(
    "path",
    [p for p in WORKFLOWS if "push:" in _text(p) and "concurrency:" in _text(p)],
    ids=lambda p: p.name,
)
def test_a_workflow_that_runs_on_push_keys_its_group_on_the_sha(path):
    """workflow שרץ על push חייב ש-github.sha ייכנס לשם הקבוצה."""
    src = _text(path)
    block = src.split("concurrency:", 1)[1].split("\njobs:", 1)[0]
    assert "github.sha" in block, (
        f"{path.name}: רץ על push אך הקבוצה שלו אינה כוללת את ה-SHA. "
        f"כל קומיט צריך קבוצה משלו, אחרת ריצות נדחקות."
    )
