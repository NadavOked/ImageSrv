"""#3696 (הכרעת זאוס 18/11): ליד כל פריט בקטלוג עם `mode` כתוב באיזה משני המצבים של `brain_veto_mode` הוא:
`decides` (הועבר למוח, הקוד הישן נמחק) או `shadow` (הקוד הישן מחליט, המוח רושם). וה-README לא אומר עוד שיש מתג שמחזיר לקוד הישן.

**בקרה שלילית (bakara-shlilit):** בלי השדה `brain_state` בקטלוג ובלי הפרק ב-README, הטסטים (neg) נופלים על התנהגות.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ITEMS = tomllib.loads((REPO / "server" / "brain" / "catalog.toml").read_text(encoding="utf-8"))["item"]
STATES = {"decides", "shadow"}
# #3657: the four points whose old code was deleted (`silence.py` decides, `room.py`/`pulls.py`/`silent_tasks.py` only execute)
MIGRATED = {"K-LOST", "K-STALL", "K-REWAKE", "K-SILENT"}


def test_every_item_with_a_mode_says_which_of_the_two_states_it_is_in():
    bad = [(i["id"], i.get("brain_state")) for i in ITEMS if "mode" in i and i.get("brain_state") not in STATES]
    assert bad == [], f"פריט עם mode בלי brain_state תקין (decides | shadow): {bad}"
    assert any("mode" in i for i in ITEMS)                       # the check is not vacuous


def test_an_item_that_decides_in_the_code_is_marked_decides():
    items = {i["id"]: i for i in ITEMS}
    assert {k: items[k]["brain_state"] for k in MIGRATED} == {k: "decides" for k in MIGRATED}
    assert all(i["brain_state"] == "decides" for i in ITEMS if i.get("mode") == "enforce")     # enforce = the brain decides (K8)


def test_a_state_is_only_written_where_there_is_a_mode():
    stray = [i["id"] for i in ITEMS if "brain_state" in i and "mode" not in i]
    assert stray == [], stray


def test_the_readme_and_the_interfaces_no_longer_say_the_switch_returns_to_the_old_code():
    readme = (REPO / "server" / "brain" / "README.md").read_text(encoding="utf-8")
    assert "מתג כללי שמחזיר הכול לקוד הישן" not in readme.replace("**אין מתג כללי שמחזיר לקוד הישן:**", "")
    assert "brain_state" in readme and "`decides`" in readme and "`shadow`" in readme
    interfaces = (REPO / "docs" / "architecture" / "interfaces.md").read_text(encoding="utf-8")
    row = next(line for line in interfaces.splitlines() if line.startswith("| `brain_veto_mode` |"))
    assert "כמו לפני המוח" not in row and "decides" in row and "brain_state" in row, row
