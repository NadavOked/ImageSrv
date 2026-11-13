"""#2419 (3) — זמן חכירה בספרות ASCII בלבד, ו-MAC קנוני בלי "\\n" בסוף.

``str.isdigit`` מחזיר אמת על ספרות ערביות ('١٢'): ההגדרה נשמרה ב-DB, ומאז
כל החלה של dnsmasq נכשלה. ``^…$`` של Python מקבל MAC עם ``\\n`` בסוף —
שורה שנכתבת בלי בריחה לקובץ ש-dnsmasq קורא כ-root.
"""

from __future__ import annotations

import pytest

from server.net import dhcp
from server.net.dhcp import InterfaceConfig, validate

GOOD = dict(
    enabled=True, range_start="10.44.9.50", range_end="10.44.9.200",
    netmask="255.255.255.0", gateway="10.44.9.1", dns=["10.44.0.5"],
    lease="12h", server_ip="10.44.9.10",
)


@pytest.mark.parametrize("lease", ["١٢h", "１２h", "²h", "12h\n7", "h", "12"])
def test_a_lease_that_is_not_ascii_digits_and_a_unit_is_refused(lease):
    with pytest.raises(ValueError, match="חכירה"):
        validate(InterfaceConfig("eth0", **{**GOOD, "lease": lease}))


@pytest.mark.parametrize("lease", ["12h", "30m", "1d", "12H", " 12h "])
def test_the_leases_that_worked_before_still_work(lease):
    validate(InterfaceConfig("eth0", **{**GOOD, "lease": lease}))


def test_a_mac_with_a_trailing_newline_is_not_canonical():
    with pytest.raises(ValueError, match="MAC לא קנוני"):
        dhcp.render_known_macs(["aa:bb:cc:dd:ee:01\n"])
