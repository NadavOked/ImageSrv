"""#3461: מלאי המוח אומר על אבדן הרשת (`server/learn/netloss.py`) מה שהקוד עושה -- לא "udpcast בלבד".

השורש: #3164 הוסיף ל-`netloss` גל חתיכות (`evaluate_chunk`, בסיס נפרד לכל מנוע), והמלאי נשאר "קיים (udpcast בלבד)".
`test_brain_catalog` בודק שהסימבול **קיים**, לא שהתיאור **נכון**, ולכן איש לא נכשל.

כאן: כל עוד הקוד מטפל בגל חתיכות, ארבעת הפריטים של netloss בקטלוג (N2, D39, LR16, L17) לא אומרים "udpcast בלבד",
והם אומרים "חתיכות"; וכל README שלהם אומר אותו דבר.

**בקרה שלילית:** החזרת הקטלוג וארבעת ה-README לבסיס מפילה את כל הטסטים כאן, חוץ מהראשון (הוא על הקוד).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRAIN = REPO / "server" / "brain"
ITEMS = ("N2", "D39", "LR16", "L17")
READMES = {"N2": "senses", "D39": "decisions", "LR16": "learning", "L17": "log"}
#: "udpcast בלבד" / "(udpcast)" כסיום משפט: הניסוחים שהיו במלאי לפני התיקון
ONLY_UDPCAST = re.compile(r"udpcast בלבד|\(udpcast\)|udpcast\)\.")


def _catalog() -> dict[str, dict]:
    with (BRAIN / "catalog.toml").open("rb") as f:
        return {it["id"]: it for it in tomllib.load(f)["item"]}


def _row(layer: str, item: str) -> str:
    text = (BRAIN / layer / "README.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith(f"| {item} |")]
    assert len(rows) == 1, f"{item}: {len(rows)} שורות ב-{layer}/README.md"
    return rows[0]


def test_netloss_still_measures_a_chunk_wave():
    # אם זה נופל, הטסטים הבאים אינם נכונים עוד: ה-README צריך לחזור להגיד udpcast בלבד
    from server.learn import netloss

    assert netloss.CHUNK == "chunk" and callable(netloss.evaluate_chunk)


def test_the_catalog_items_of_netloss_do_not_say_udpcast_only():
    bad = []
    for item, entry in _catalog().items():
        if item not in ITEMS:
            continue
        said = " ".join(str(entry.get(k, "")) for k in ("why", "note", "title"))
        if ONLY_UDPCAST.search(said):
            bad.append(f"{item}: {said}")
    assert bad == [], "\n".join(bad)


def test_the_catalog_items_of_netloss_say_chunk_waves():
    items = _catalog()
    missing = [i for i in ITEMS if "חתיכות" not in " ".join(str(items[i].get(k, "")) for k in ("why", "note"))]
    assert missing == [], f"הקטלוג לא מזכיר גל חתיכות: {missing}"


def test_every_readme_row_of_netloss_says_what_the_catalog_says():
    bad = []
    for item, layer in READMES.items():
        row = _row(layer, item)
        if ONLY_UDPCAST.search(row):
            bad.append(f"{item}: אומר udpcast בלבד")
        if "חתיכות" not in row:
            bad.append(f"{item}: לא מזכיר גל חתיכות")
        if "evaluate_chunk" not in row and item in ("D39", "L17"):
            bad.append(f"{item}: לא מצביע על evaluate_chunk")
    assert bad == [], "\n".join(bad)


def test_the_learning_row_says_it_was_not_measured_on_metal_and_that_the_metric_differs():
    row = _row("learning", "LR16")
    assert "לא נמדד על ברזל" in row, "הרצפה CHUNK_FLOOR_PER_GB מנומקת ולא מכוילת (netloss.py)"
    assert "loss_permille" in row and "abandoned" in row
