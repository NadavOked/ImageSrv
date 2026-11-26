"""`server/brain/catalog.toml` — הקטלוג של המוח של פס הייצור (#3064, #3114) — צמוד לקוד.

הקטלוג מצביע על כל כלל לפי **קובץ וסימבול** (פונקציה או קבוע), ולא רק לפי מספר שורה:
מספר שורה מתיישן בקומיט הבא, ובשקט. הטסט הזה הופך את ההתיישנות לכשל בקול. מי שמזיז
כלל, או משנה שם של פונקציה, ולא מעדכן את הקטלוג, מקבל כשל בשם הפריט.

מה נבדק:
1. כל `file` (וכל `file` בתוך `also`) קיים בריפו.
2. כל `symbol` מופיע בקובץ שלו כמילה שלמה, וכל `anchor` מופיע בו כמחרוזת.
3. אין id כפול.
4. כל פריט עם `source = "measured"` נושא `date` בצורה YYYY-MM-DD (עיקרון 5ב: מספר
   בלי מקור ותאריך הוא ציטוט, לא ראיה).
5. כל פריט שקיים היום (`used`, `reported`, `exists`, `partial`) מצביע על קובץ וסימבול.

`line` אינו נבדק בכוונה: הוא רמז, נכון לקומיט שבו הקטלוג נכתב.
"""

from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "server" / "brain" / "catalog.toml"
LAYERS = {"senses", "decisions", "thresholds", "learning", "log", "invariants"}
EXISTING = {"used", "reported", "exists", "partial"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _items() -> list[dict]:
    with CATALOG.open("rb") as f:
        return tomllib.load(f)["item"]


ITEMS = _items()


def _locations() -> list[tuple[str, dict]]:
    """כל מקום שהקטלוג מצביע עליו: הראשי (file/symbol/anchor) וכל אחד מ-`also`."""
    out = []
    for it in ITEMS:
        if "file" in it:
            out.append((it["id"], {k: it[k] for k in ("file", "symbol", "anchor") if k in it}))
        for n, extra in enumerate(it.get("also", []), start=1):
            out.append((f"{it['id']}.also{n}", extra))
    return out


LOCATIONS = _locations()


@cache
def _text(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8", errors="replace")


def test_the_catalog_is_not_empty():
    # קטלוג ריק, או כזה שאיבד שכבה, היה עובר את כל הבדיקות שלמטה בלי לבדוק כלום (עיקרון 5)
    assert {it["layer"] for it in ITEMS} == LAYERS
    assert len(LOCATIONS) > 200


def test_the_catalog_calls_the_reported_beeps_reported():
    # #3125: כאן ולא בטסט של הצפצופים — הקטלוג פרטי, וטסט ציבורי אינו נוקב בו (test_publish_to_public)
    [s62] = [it for it in ITEMS if it["id"] == "S62"]
    assert s62["status"] == "reported" and s62["file"] == "server/agent/beeps.py", s62


@pytest.mark.parametrize("where", [w for _, w in LOCATIONS], ids=[i for i, _ in LOCATIONS])
def test_every_file_exists(where):
    assert (REPO / where["file"]).is_file(), f"{where['file']} אינו קיים בריפו"


@pytest.mark.parametrize("where", [w for _, w in LOCATIONS], ids=[i for i, _ in LOCATIONS])
def test_every_symbol_is_in_its_file(where):
    text = _text(where["file"])
    symbol = where["symbol"]
    assert re.search(rf"(?<!\w){re.escape(symbol)}(?!\w)", text), \
        f"`{symbol}` אינו מופיע ב-{where['file']}: הכלל זז או שינה שם, וצריך לעדכן את catalog.toml"
    if "anchor" in where:
        assert where["anchor"] in text, \
            f"העוגן «{where['anchor']}» אינו מופיע ב-{where['file']}: הכלל זז, וצריך לעדכן את catalog.toml"


def test_no_duplicate_ids():
    ids = [it["id"] for it in ITEMS]
    assert sorted({i for i in ids if ids.count(i) > 1}) == []


def test_every_measurement_carries_a_date():
    bad = [it["id"] for it in ITEMS
           if it["source"] == "measured" and not DATE.fullmatch(str(it.get("date", "")))]
    assert bad == [], f"source = measured בלי date בצורה YYYY-MM-DD: {bad}"


def test_every_existing_item_points_at_code():
    bad = [it["id"] for it in ITEMS if it["status"] in EXISTING and not (it.get("file") and it.get("symbol"))]
    assert bad == [], f"פריט שקיים היום בלי file/symbol: {bad}"


def test_the_catalog_says_the_wave_senses_are_reported_where_they_are():
    # #3041: כאן ולא בטסט של החושים — הקטלוג פרטי, וטסט ציבורי אינו נוקב בו (test_publish_to_public)
    items = {it["id"]: it for it in ITEMS}
    assert {i: items[i]["status"] for i in ("S46", "S47", "S48", "S49", "S50")} == {
        "S46": "partial", "S47": "reported", "S48": "reported", "S49": "reported", "S50": "reported"}
    for i in ("S46", "S47", "S48", "S49", "S50"):
        assert items[i]["file"] == "agent/lib/restore/wavesense.sh"
        assert any(a["file"] == "server/brain/wavesense.py" for a in items[i]["also"]), i


def test_the_catalog_points_k_fill_at_the_limits_of_a_completion():
    # #3659: כאן ולא בטסט של ההשלמה -- הקטלוג פרטי, וטסט ציבורי אינו נוקב בו (test_publish_to_public)
    item = {it["id"]: it for it in ITEMS}["K-FILL"]
    assert (item["layer"], item["mode"], item["file"], item["symbol"]) == ("decisions", "shadow", "server/brain/fill.py", "limits")
