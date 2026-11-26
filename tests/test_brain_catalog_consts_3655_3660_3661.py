"""#3655 (F14), #3660 (F6), #3661 (A33): קבועים שמחליטים בפועל ולא היו בקטלוג -- K8 (`chunk_rate.py`) וארבעה בסוכן.

הקטלוג חייב לשמור **את הערך שבקוד**, לא ערך שהיה נכון ביום שנכתב: כל פריט כאן נבדק מול הקבוע עצמו. ו"מקור" אינו `measured`
(אין תאריך מדידה לאף אחד מהם; `test_brain_catalog` ממילא נופל על measured בלי date).

**בקרה שלילית:** עם `server/brain/catalog.toml` של הבסיס אין אף אחד מהפריטים, וכל הטסטים כאן נופלים.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from server.rounds import chunk_rate

REPO = Path(__file__).resolve().parent.parent
CATALOG = {i["id"]: i for i in tomllib.loads((REPO / "server/brain/catalog.toml").read_text(encoding="utf-8"))["item"]}

K8 = {"T-K8-LOSS": "LOSS_PERMILLE", "T-K8-STEP": "STEP_MBPS", "T-K8-CUT": "CUT_PERCENT", "T-K8-STALE": "STALE_HOURS",
      "T-K8-PROBE-DIFF": "PROBE_DIFF_PERCENT", "T-K8-RELEASE": "RELEASE_WAVES", "T-K8-WRITE-STEP": "WRITE_STEP_PERCENT",
      "T-K8-WRITE-HELD": "WRITE_HELD_PERCENT", "T-K8-BEHIND": "BEHIND_PERMILLE"}
AGENT = {"T-DRAWER-PLUG-SETTLE": "DRAWER_PLUG_SETTLE_S", "T-DRAWER-OUT-GONE": "DRAWER_OUT_GONE_S",
         "T-LINE-PL-WORD": "PL_WORD_S", "T-LINE-WAIT-ASK": "LINE_WAIT_ASK_S"}


@pytest.mark.parametrize("item_id,name", sorted(K8.items()))
def test_a_k8_constant_is_in_the_catalog_with_the_value_the_code_has(item_id, name):
    item = CATALOG.get(item_id)
    assert item is not None, f"{item_id} ({name}) אינו בקטלוג"
    assert item["symbol"] == name and item["file"] == "server/rounds/chunk_rate.py"
    assert item["value"] == str(getattr(chunk_rate, name)), f"{name}: הקטלוג אומר {item['value']}, הקוד {getattr(chunk_rate, name)}"


@pytest.mark.parametrize("item_id,name", sorted(AGENT.items()))
def test_an_agent_constant_is_in_the_catalog_with_the_default_the_script_has(item_id, name):
    item = CATALOG.get(item_id)
    assert item is not None, f"{item_id} ({name}) אינו בקטלוג"
    text = (REPO / item["file"]).read_text(encoding="utf-8")
    got = re.search(rf'^{name}="\$\{{{name}:-(\d+)\}}"', text, re.M)
    assert got is not None, f"{name} לא נמצא ב-{item['file']} בצורה NAME=\"${{NAME:-N}}\""
    assert item["symbol"] == name and item["value"] == got.group(1)


@pytest.mark.parametrize("item_id", sorted([*K8, *AGENT]))
def test_none_of_them_claims_to_be_measured(item_id):
    """לאף אחד מהם אין תאריך מדידה: `measured` היה טענה שאין לה ראיה (עיקרון 5ב)."""
    assert CATALOG[item_id]["source"] in ("estimate", "derived", "unknown")
