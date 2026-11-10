"""#2796 -- the health "שעון" row after a VM resume: 352 s behind, shown as "סטייה -0.019 ms".

Measured on ESXi, 10/11 (`nt1109-srv`, v0.91.7, run-fix-v0.91.7.txt in lab/release-tests/nt1109/1814):
for 5.5 minutes chrony had no selectable source and the row read "לא מסונכרן · סטייה -0.019 ms".
That number is tracking's "System time" -- chrony's estimate from its last clock update, not a
measurement. Unsynchronised, the row now says "סטייה לא ידועה", and the freshest real sample
from `chronyc -n sources` (any source, chosen or not) is shown with its source and age, and
decides `bad` from 30 s like an offset does.

The chronyc text below follows the real `chronyc -n sources` layout (lab/evening-1015/rc4-1024/
hyperv/clock-boot1.txt) and chronyc's documented sign: positive = the local clock is ahead.
The sources output DURING the 10/11 window was not captured -- see the PR.

Negative control: `server/system/health.py` and `server/system/timesync.py` from origin/main.
"""
from __future__ import annotations

import pytest

from server.system import health, timesync

TRACKING_UNSYNC = """Reference ID    : 00000000 ()
Stratum         : 0
Ref time (UTC)  : Thu Jan 01 00:00:00 1970
System time     : 0.000019000 seconds slow of NTP time
Last offset     : +0.000000000 seconds
RMS offset      : 0.000000000 seconds
Leap status     : Not synchronised
"""

TRACKING_SYNC = TRACKING_UNSYNC.replace("Not synchronised", "Normal").replace(
    "0.000019000 seconds slow", "0.000109000 seconds fast")

HEADER = ("MS Name/IP address         Stratum Poll Reach LastRx Last sample               \n"
          "===============================================================================\n")

# 352 s behind: two sources answered after the resume, one still holds a sample from before
# it, and one was never heard from (LastRx "-").
SOURCES_352_BEHIND = HEADER + (
    "^? 109.237.76.250                3   6   377    21   -352s[  -352s] +/-   18ms\n"
    "^? 64.176.168.216                2   6   377    40   -352s[  -352s] +/-   25ms\n"
    "^? 195.189.140.250               2   6   177    5m    -19us[  -19us] +/- 8012us\n"
    "^? 10.0.0.9                      0   6     0     -     +0ns[   +0ns] +/-    0ns\n")

SOURCES_NO_SAMPLE = HEADER + (
    "^? 10.0.0.9                      0   6     0     -     +0ns[   +0ns] +/-    0ns\n")


def _status(tracking: str, sources: str) -> dict:
    return timesync.status({"tracking": lambda: tracking, "sources": lambda: sources,
                            "now": lambda: "2026-11-09T23:17:33+00:00"})


def test_unsynchronised_352s_behind_is_said_and_is_bad():
    row = health.time_sync_check(_status(TRACKING_UNSYNC, SOURCES_352_BEHIND))
    assert row["state"] == "bad", row
    assert "סטייה לא ידועה (אין מקור זמן נבחר)" in row["detail"], row
    assert "דגימה אחרונה מ-109.237.76.250 לפני 21 ש': השעון מאחר ב-352 ש'" in row["detail"], row
    assert "קודי MFA לא יתקבלו" in row["detail"], row
    assert "0.019" not in row["detail"], row


def test_unsynchronised_without_a_sample_shows_no_number():
    row = health.time_sync_check(_status(TRACKING_UNSYNC, SOURCES_NO_SAMPLE))
    assert row["state"] == "warn", row
    assert row["detail"] == "מקור לא ידוע · לא מסונכרן · סטייה לא ידועה (אין מקור זמן נבחר)", row


def test_the_freshest_sample_is_read_with_its_unit_age_and_sign():
    status = _status(TRACKING_UNSYNC, SOURCES_352_BEHIND)
    assert status.get("last_sample") == {"server": "109.237.76.250", "age_s": 21,
                                         "offset_ms": -352000.0}
    only_old = HEADER + "^x 195.189.140.250   2   6   177    5m   +415us[ +939us] +/- 8012us\n"
    assert _status(TRACKING_UNSYNC, only_old).get("last_sample") == {
        "server": "195.189.140.250", "age_s": 300, "offset_ms": pytest.approx(0.415)}
    assert _status(TRACKING_UNSYNC, SOURCES_NO_SAMPLE).get("last_sample") is None


def test_a_small_old_sample_is_named_as_a_sample_not_as_the_offset():
    sources = HEADER + "^? 195.189.140.250   2   6   177    5m    -19us[  -19us] +/- 8012us\n"
    row = health.time_sync_check(_status(TRACKING_UNSYNC, sources))
    assert row["state"] == "warn", row
    assert row["detail"] == ("מקור לא ידוע · לא מסונכרן · סטייה לא ידועה (אין מקור זמן נבחר) · "
                             "דגימה אחרונה מ-195.189.140.250 לפני 5 דק': סטייה -0.019 ms"), row


def test_ahead_sample_says_ahead():
    sources = HEADER + "^? 10.0.0.5   2   6   377    3   +95s[  +95s] +/- 1ms\n"
    row = health.time_sync_check(_status(TRACKING_UNSYNC, sources))
    assert row["state"] == "bad" and "השעון מקדים ב-95 ש'" in row["detail"], row


def test_synchronised_row_still_shows_chrony_system_time():
    sources = HEADER + "^* 109.237.76.250   3   6   377    12   +80us[ +120us] +/-   18ms\n"
    row = health.time_sync_check(_status(TRACKING_SYNC, sources))
    assert row["state"] == "ok", row
    assert row["detail"] == "מקור 109.237.76.250 · מסונכרן · סטייה +0.109 ms", row
