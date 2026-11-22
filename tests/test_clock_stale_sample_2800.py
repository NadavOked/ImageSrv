"""#2800 -- "synchronised" with a sample that is not fresh is not a green "שעון" row.

Right after a resume chrony still reports "Normal" and a selected source from its old estimate,
so the row was green for ~30 s while the clock was hundreds of seconds behind (#2796). Nadav
(12/11): look at when chrony last really heard a source; older than the poll interval allows
= orange. The freshest sample's age comes from `chronyc -n sources` (LastRx), its poll interval
from the same row. Whether LastRx also shows a VM resume is for the VM run (the Issue says so).
"""
from __future__ import annotations

from test_clock_health_2796 import HEADER, TRACKING_SYNC, _status

from server.system import health


def _row(last_rx: str, poll: int = 6):
    sources = HEADER + f"^* 109.237.76.250   3   {poll}   377   {last_rx}   +80us[ +120us] +/-   18ms\n"
    return health.time_sync_check(_status(TRACKING_SYNC, sources))


def test_a_fresh_sample_keeps_the_row_green():
    row = _row("12")
    assert row["state"] == "ok", row
    assert "אין דגימה טרייה" not in row["detail"], row


def test_a_sample_older_than_two_poll_intervals_is_orange_and_says_so():
    row = _row("5m")                                   # 300 s, poll 6 = 64 s: limit 138 s
    assert row["state"] == "warn", row
    assert "אין דגימה טרייה מ-109.237.76.250: האחרונה לפני 5 דק'" in row["detail"], row


def test_the_limit_follows_the_poll_interval_of_the_source():
    assert _row("5m", poll=8)["state"] == "ok"        # poll 8 = 256 s: limit 522 s, 300 s is normal
    assert _row("20m", poll=8)["state"] == "warn"


def test_an_unknown_poll_interval_is_not_called_stale():
    from server.system import timesync
    assert timesync._poll_s("^* host 3 x 377 5m +1us[ +1us] +/- 1ms") is None
    assert timesync._poll_s("^* host 3 40 377 5m +1us[ +1us] +/- 1ms") is None
    assert timesync._poll_s("^* host 3 6 377 5m +1us[ +1us] +/- 1ms") == 64
