r"""#2717: a non-ASCII SMTP host or sender address is refused.

The only guard is the ``_HOST`` / ``_ADDR`` patterns in
``server/system/email_config.py`` (explicit ``[A-Za-z0-9]`` ranges, no
``re.IGNORECASE``). Nothing pinned it: widening a class to ``\w`` or adding
IGNORECASE (which makes U+212A KELVIN SIGN match ``k``) would pass silently.
Tests go through the public ``merge_account``.
"""

from __future__ import annotations

import pytest

from server.system import email_config as ec

KELVIN = "\u212a"  # looks like K, folds to ascii k under IGNORECASE


@pytest.mark.parametrize("host", [
    "b\u00fccher.de",              # bücher.de
    "smtp.\u05d3\u05d5\u05d0\u05e8.il",  # smtp.דואר.il
    f"{KELVIN}elvin.com",          # Kelvin.com with U+212A
])
def test_non_ascii_host_is_refused(host):
    with pytest.raises(ec.ConfigError):
        ec.merge_account(None, {"host": host}, False)


def test_non_ascii_sender_address_is_refused():
    with pytest.raises(ec.ConfigError):
        ec.merge_account(None, {"from_addr": "a@b\u00fccher.de"}, False)


def test_ascii_host_and_address_are_accepted():
    acc, *_ = ec.merge_account(
        None, {"host": "smtp.example.com", "from_addr": "a@example.com"}, False)
    assert acc["host"] == "smtp.example.com"
    assert acc["from_addr"] == "a@example.com"
