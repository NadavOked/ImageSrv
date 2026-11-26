"""#3666 (F5): the two SMART short self-test time constants are registered in the brain catalog.

`SMART_SHORT_WAIT_S` and `SMART_SELFTEST_STOP_S` (agent/lib/smart/smartselftest.sh) decide how long
the cloner waits on a disk, and neither was in `server/brain/catalog.toml`. This is registration only:
the catalog row must point at the shell constant, carry the value the shell actually defaults to,
and say where the number came from. The value is read from the shell, so changing the default
without touching the row fails here instead of drifting silently.

They have no brain rule, so they carry no `mode` (and so nothing for `brain_state` to report).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = "agent/lib/smart/smartselftest.sh"
ROWS = {"SMART_SHORT_WAIT_S": "T-SMART-SHORT-WAIT", "SMART_SELFTEST_STOP_S": "T-SMART-SELFTEST-STOP"}


def _shell_default(name: str) -> str:
    text = (REPO / SCRIPT).read_text(encoding="utf-8")
    m = re.search(rf'^{name}="\${{{name}:-(\d+)}}"', text, re.M)
    assert m, f"{SCRIPT}: no `{name}=\"${{{name}:-N}}\"` line"
    return m.group(1)


def _items() -> dict[str, dict]:
    data = tomllib.loads((REPO / "server" / "brain" / "catalog.toml").read_text(encoding="utf-8"))
    return {it["id"]: it for it in data["item"]}


@pytest.mark.parametrize("symbol", sorted(ROWS))
def test_constant_is_a_catalog_row_pointing_at_the_shell(symbol: str) -> None:
    item = _items().get(ROWS[symbol])
    assert item is not None, f"{symbol}: no catalog row {ROWS[symbol]}"
    assert (item["file"], item["symbol"]) == (SCRIPT, symbol)
    assert item["layer"] == "thresholds"
    assert item["status"] == "exists"


@pytest.mark.parametrize("symbol", sorted(ROWS))
def test_row_value_is_the_shell_default(symbol: str) -> None:
    assert _items()[ROWS[symbol]]["value"] == _shell_default(symbol)


@pytest.mark.parametrize("symbol", sorted(ROWS))
def test_row_says_where_the_number_came_from_and_is_not_a_rule(symbol: str) -> None:
    item = _items()[ROWS[symbol]]
    assert item["source"] in {"derived", "unknown", "estimate", "measured", "decision"}
    assert item["why"].strip()
    assert "mode" not in item, "no brain rule yet: registration only"
