"""#3600 -- `server/learn/netrate.py` as a unit: the margin's parser and the bottleneck rule.

The behaviour (a silent 1G network, the 2.5G tie, the setting through `POST /settings`) is pinned through the wiring API in
`test_wiring_network_bottleneck_3600.py`, where the old code fails on behaviour. This file is the unit of a module that did not exist
before, so on the old code it can only fail by ImportError.
"""

from __future__ import annotations

import pytest

from server.learn import netrate


@pytest.mark.parametrize("raw, want", [("1.1", 1.1), (1.1, 1.1), (" 2 ", 2.0), ("1", 1.0), ("10", 10.0), (3, 3.0), ("1.25", 1.25)])
def test_a_margin_is_a_number_in_range_with_at_most_two_decimals(raw, want):
    assert netrate.parse_margin(raw) == want


@pytest.mark.parametrize("raw", ["0.99", "10.01", "abc", "", "1.123", "nan", "inf", "-1", True, False, None, [1.1], {}])
def test_anything_else_is_refused(raw):
    assert netrate.parse_margin(raw) is None


def test_is_bottleneck_says_no_for_what_is_not_known():
    assert netrate.is_bottleneck(1125.0, 3.0, 1.1) is True
    assert netrate.is_bottleneck(None, 3.0, 1.1) is False, "an unmeasured wire is not a bottleneck"
    assert netrate.is_bottleneck(1125.0, 4.5, 1.1) is False, "a speed with no known ceiling is not a bottleneck"
    assert netrate.is_bottleneck(1125.0, None, 1.1) is False
    assert netrate.is_bottleneck(309.0, 3.0, 1.1) is True and netrate.is_bottleneck(307.0, 3.0, 1.1) is False


def test_the_default_is_1_1_and_the_stored_value_wins(tmp_path):
    from server import db
    conn = db.connect(tmp_path / "imagesrv.db")
    assert netrate.margin(conn) == 1.1 == netrate.DEFAULT_MARGIN
    db.set_setting(conn, netrate.MARGIN_SETTING, "1.5")
    assert netrate.margin(conn) == 1.5
