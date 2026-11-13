"""#715 — טבלאות מקבילות ב-native-gui חייבות להיות באותו אורך.

הלקח מהמעבדה (16/09): `"direct"` נוסף ל-`PNG_CARDS` ב-`main.c`, אבל
טבלת הצפי `want[]` של בדיקת הניתוב ב-`--png` לא הורחבה — `want[c]`
קרא מעבר לסוף המערך, ותשעה טסטי C נפלו על "routed to screen 5, not
788554541" (זבל מהזיכרון). בקוד יש עכשיו `_Static_assert` שתופס את זה
בקומפילציה; הטסט הזה תופס את זה **גם בלי מהדר** (תחנת ווינדוס), על
המקור, כדי שהמידה הבאה שתתווסף לא תגיע למעבדה כשהיא כבר אדומה.
"""

from __future__ import annotations

import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / "native-gui" / "src" / "main.c"
SCREENS = MAIN.with_name("screens.c")


def test_capture_first_menu_matches_number_key_order():
    expected = ["HIT_CAPTURE", "HIT_ROOM", "HIT_DIRECT", "HIT_RESTORE", "HIT_CLASSES"]
    menu = re.search(r"MENU\[MENU_N\]\s*=\s*\{(.*?)\n\};",
                     SCREENS.read_text(encoding="utf-8"), re.S)[1]
    assert re.findall(r"\},(HIT_\w+),", menu) == expected
    handler = re.search(r"static int visible_card_ids.*?\{(.*?)\n\}",
                        MAIN.read_text(encoding="utf-8"), re.S)[1]
    assert re.findall(r"ids\[n\+\+\] = (HIT_\w+)", handler) == expected


def _c_array_items(text: str, name: str) -> list[str]:
    """הפריטים של `... name[] = { ... };` — בלי הערות, מפוצלים בפסיקים."""
    m = re.search(r"\b" + re.escape(name) + r"\[\]\s*=\s*\{(.*?)\};", text, flags=re.S)
    assert m, f"{name}[] לא נמצא"
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    return [item.strip() for item in body.split(",") if item.strip()]


def test_png_cards_and_the_routing_expectation_have_the_same_length():
    text = MAIN.read_text(encoding="utf-8")
    cards = _c_array_items(text, "PNG_CARDS")
    want = _c_array_items(text, "want")
    assert len(cards) == len(want), (
        f"PNG_CARDS ({len(cards)}) ו-want[] ({len(want)}) אינם באותו אורך: "
        f"{cards} מול {want}")
    # והכרטיס הישיר עצמו מנותב למסך החדר (MODE_DIRECT = מסך החדר בלי בורר).
    assert cards.index('"direct"') == want.index("SCREEN_ROOM", want.index("SCREEN_ROOM") + 1) + 0 \
        or want[cards.index('"direct"')] == "SCREEN_ROOM"
    assert "_Static_assert(sizeof want" in text


def test_the_menu_table_and_its_visibility_row_match_menu_n():
    text = SCREENS.read_text(encoding="utf-8")
    n = int(re.search(r"#define MENU_N (\d+)", text).group(1))
    menu = re.search(r"MENU\[MENU_N\]\s*=\s*\{(.*?)\n\};", text, flags=re.S).group(1)
    rows = re.findall(r"^\s*\{ \"", menu, flags=re.M)
    assert len(rows) == n, f"MENU_N={n} אבל בטבלה {len(rows)} כרטיסים"
    # `{...};` או `{...}, count = 0;` — הענף המעוצב מכריז את המונה באותה שורה.
    show = re.search(r"int show\[MENU_N\] = \{(.*?)\}", text).group(1)
    assert len([x for x in show.split(",") if x.strip()]) == n
    ids = re.search(r"visible_card_ids\(const App \*a, int ids\[(\d+)\]\)",
                    MAIN.read_text(encoding="utf-8")).group(1)
    assert int(ids) == n, f"visible_card_ids מקבל ids[{ids}] אבל MENU_N={n}"
