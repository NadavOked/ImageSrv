"""#3677 (אפיק #3653): 40 הנקודות של קבוצה (ג), 'לא מעבירים' למוח, רשומות ב-`server/brain/catalog.toml`.

הכרעת נדב 18/11: כל ההחלטות במוח, חוץ מבטיחות קשיחה ומדיניות קבועה. מה שלא כתוב בקטלוג יעבור 'על הדרך' ב-PR
כלשהו, ולכן כל אחת מ-40 הנקודות רשומה כפריט `CG-<נקודה>` עם קובץ, סימבול, שורה, העיקרון שהיא שומרת, ו-`why` (למה לא
מועבר).

הרשימה למטה מוקלדת כאן בכוונה, ולא נגזרת מהקטלוג: טסט שקורא את הרשימה מהקובץ שהוא בודק לא נופל כשמוחקים ממנו שורה.
מחיקת נקודה מהקטלוג, שינוי שם של פונקציה, או העברת קובץ בלי לעדכן את הקטלוג, נכשלים כאן בשם הנקודה.

`line` הוא רמז ולא נבדק מול התוכן (כמו ב-`test_brain_catalog.py`): שורה מתיישנת בקומיט הבא. נבדק שהקובץ קיים, שהשורה בתוכו,
ושהסימבול (וה-`anchor`, אם יש) מופיעים בו.

**בקרה שלילית:** מוחקים פריט אחד מהקטלוג (למשל `CG-S48`) והטסט הראשון והשני נופלים בשמו; משנים `symbol` לשם שאינו בקובץ
והשלישי נופל.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "server" / "brain" / "catalog.toml"

# 40 הנקודות של קבוצה (ג): 39 כפי שהן במיפוי `lab/decisions-map-1118.md` ובגוף #3653, ו-S30 שנוספה בהכרעת נדב ב-#3695.
GROUP_C = (
    "A1 A2 A3 A4 A8 A12 A19 A23 A24 A28 A31 A36 A41 A43 L1 L4 "
    "B1 B2 B3 B4 B5 B10 S2 S30 S32 S33 S42 S43 S45 S48 S54 "
    "G1 G4 G5 G7 G8 G9 T1 T3 T4"
).split()

KINDS = {"invariant", "policy"}


def _items() -> list[dict]:
    with CATALOG.open("rb") as f:
        return tomllib.load(f)["item"]


ITEMS = _items()
BY_ID = {it["id"]: it for it in ITEMS}


@cache
def _lines(rel: str) -> list[str]:
    return (REPO / rel).read_text(encoding="utf-8", errors="replace").split("\n")


def _locations() -> list[tuple[str, dict]]:
    """כל מקום שפריט של קבוצה (ג) מצביע עליו: הראשי וכל אחד מ-`also`."""
    out = []
    for point in GROUP_C:
        it = BY_ID.get(f"CG-{point}")
        if it is None:
            continue
        out.append((f"CG-{point}", it))
        for n, extra in enumerate(it.get("also", []), start=1):
            out.append((f"CG-{point}.also{n}", extra))
    return out


LOCATIONS = _locations()


def test_the_list_is_the_40_points():
    # רשימה שהתקצרה בשקט הייתה מעבירה כל בדיקה למטה בלי לבדוק את מה שנמחק (עיקרון 5)
    assert len(GROUP_C) == 40 and len(set(GROUP_C)) == 40


def test_every_group_c_point_is_in_the_catalog():
    missing = [p for p in GROUP_C if f"CG-{p}" not in BY_ID]
    assert missing == [], f"נקודות של קבוצה (ג) שנמחקו מ-catalog.toml: {missing}"


def test_the_catalog_has_no_group_c_item_outside_the_list():
    # פריט שנוסף לקבוצה בלי להגיע לרשימה הזו הוא נקודה שמישהו הכריע עליה בלי שהטסט יודע
    extra = sorted(it["id"] for it in ITEMS if it.get("group") == "ג" and it.get("point") not in GROUP_C)
    assert extra == [], f"פריטים בקבוצה (ג) שאינם ב-40: {extra}"
    wrong = sorted(it["id"] for it in ITEMS if it["id"].startswith("CG-") and it.get("group") != "ג")
    assert wrong == [], f"פריט CG- בלי group = 'ג': {wrong}"


@pytest.mark.parametrize("point", GROUP_C)
def test_each_point_says_what_it_guards_and_why_it_is_not_moved(point):
    it = BY_ID.get(f"CG-{point}")
    assert it is not None, f"CG-{point} אינו בקטלוג"
    assert it["point"] == point and it["group"] == "ג"
    assert it["layer"] == "invariants" and it["status"] == "exists", it["id"]
    assert it["kind"] in KINDS, f"{it['id']}: kind = {it['kind']!r}, מותר {sorted(KINDS)}"
    assert it["source"] == "policy" and it["recommendation"] == "keep", it["id"]
    assert it["title"].strip(), f"{it['id']}: אין title"
    assert it["principle"].strip(), f"{it['id']}: אין העיקרון שהנקודה שומרת"
    assert re.fullmatch(r"[0-9a-f]{7}", it["line_base"]), f"{it['id']}: line_base אינו קומיט"
    # למה לא מעבירים: הציטוט של ההכרעה ואחריו נימוק בפועל (לא רק הציטוט)
    head, sep, reason = it["why"].partition("לא עובר למוח.")
    assert sep and len(reason.strip()) >= 20, f"{it['id']}: why בלי נימוק אחרי 'לא עובר למוח.'"


@pytest.mark.parametrize("where", [w for _, w in LOCATIONS], ids=[i for i, _ in LOCATIONS])
def test_the_file_and_line_still_exist(where):
    rel = where["file"]
    assert (REPO / rel).is_file(), f"{rel} אינו קיים בריפו"
    line = where["line"]
    assert isinstance(line, int) and 1 <= line <= len(_lines(rel)), \
        f"{rel}:{line} מחוץ לקובץ ({len(_lines(rel))} שורות): הקובץ התקצר, וצריך לעדכן את catalog.toml"


@pytest.mark.parametrize("where", [w for _, w in LOCATIONS], ids=[i for i, _ in LOCATIONS])
def test_the_symbol_is_in_its_file(where):
    text = "\n".join(_lines(where["file"]))
    symbol = where["symbol"] if "symbol" in where else None
    assert symbol, f"{where['file']}: אין symbol"
    assert re.search(rf"(?<!\w){re.escape(symbol)}(?!\w)", text), \
        f"`{symbol}` אינו מופיע ב-{where['file']}: הנקודה זזה או שינתה שם, וצריך לעדכן את catalog.toml"
    if "anchor" in where:
        assert where["anchor"] in text, f"העוגן «{where['anchor']}» אינו ב-{where['file']}"
