"""#3554: `MACHINE_CAPABILITY` says CLEAN ("the cloner received at least X") only for a wave that reached X, with the disks measured and not behind.

Metal 16/11 (`lab/metal-1116-r16`, RS-1): no loss, CPU 46%, `disks_behind=True` (a drawer peaked at 96% busy), 419.6Mb/s reached of 669m asked -- and the brain
wrote CLEAN, "the cloner received at least 669Mb/s". The udpcast wave of the same day (`O0-1`): no loss, the CPU and the disks not read, 508.7Mb/s reached of 669m --
CLEAN too. In shadow this is a row; in `enforce` it is a learned capacity nobody proved. `judge` now asks for what "at least X" means: the wave reached
`KEEP_UP` x X (`drive_rates.KEEP_UP`, 0.8; `Evidence.achieved_bps` -- a chunk wave's `achieved_bps`, a udpcast wave's `stream_bps` x 8) **and** the disks were measured
and were not behind. Otherwise a verdict with a name, and nothing is learned: `DISK_BOUND` (the disks were behind), `NOT_PUSHED` (the wave did not reach the
rate -- the sender, the disk or the net held it, and the cloner was never asked for X), `UNDECIDED` (the reached rate, or whether the disks were behind, was not read).

**Negative control:** `server/brain/capability.py` back at the base -- the RS-1 wave and the udpcast wave are CLEAN and write `clean_bps`. The pure tests that build
`Evidence(achieved_bps=...)` fail there on the keyword, which is NOT a negative control; the (neg) ones go through `reports.ingest` and fail on behaviour.
"""

from __future__ import annotations

import json

import pytest

from server.brain import capability as C
from server.rounds import drive_rates

from test_brain_capability_3041 import MAC, _cap, _rows, _wave, conn   # noqa: F401 -- conn is a fixture

#: the evidence line of the brain's CLEAN row for RS-1 (`brain_decisions` id 3232, `lab/metal-1116-r16/runs/RS-1-chunk/db-extra.json`)
#: and the wave's `wave_rates.stream.chunk`
ASKED = 669_000_000
RS1 = dict(stream_bps=ASKED, rcvbuf=0, abandoned=0, rx_missed=0, rx_no_buffer=0, cpu_floor=46.0, disks_behind=True, achieved_bps=419_592_605)
#: the udpcast wave `ses_b84c248d` (`runs/O0-1-udpcast`): `stream_bps` 63,584,090 BYTES a second, the CPU and the disks not read
UDPCAST = dict(stream_bps=ASKED, rcvbuf=0, abandoned=None, rx_missed=None, rx_no_buffer=None, cpu_floor=None, disks_behind=None,
               achieved_bps=63_584_090 * 8)


def test_the_real_rs1_wave_is_disk_bound_not_clean():
    v = C.judge(C.Evidence(**RS1))
    assert v.verdict == C.DISK_BOUND and "לא הוכח שהמשכפל קיבל 669Mb/s" in v.reason and not v.lost


def test_the_real_rs1_wave_with_the_disks_not_behind_is_not_pushed_and_the_reason_says_what_it_reached():
    v = C.judge(C.Evidence(**{**RS1, "disks_behind": False}))
    assert v.verdict == C.NOT_PUSHED and "419.6Mb/s" in v.reason and "669Mb/s" in v.reason and "80%" in v.reason


def test_the_real_udpcast_wave_reached_508_of_669_and_is_not_clean():
    assert 63_584_090 * 8 / ASKED < drive_rates.KEEP_UP
    v = C.judge(C.Evidence(**UDPCAST))
    assert v.verdict == C.NOT_PUSHED and "508.7Mb/s" in v.reason


def test_a_wave_that_reached_the_rate_with_the_disks_measured_and_not_behind_is_clean():
    """Guard, both states: the proof is still a proof."""
    v = C.judge(C.Evidence(**{**RS1, "disks_behind": False, "achieved_bps": 660_000_000}))
    assert v.verdict == C.CLEAN and "660Mb/s" in v.reason and "669Mb/s" in v.reason


def test_the_share_is_keep_up_and_exactly_at_it_is_clean():
    assert drive_rates.KEEP_UP == 0.8
    gig = 1_000_000_000                                       # 0.8 x 10^9 is exact in a float; 0.8 x 669m is not
    ok = {**RS1, "disks_behind": False, "stream_bps": gig}
    assert C.judge(C.Evidence(**{**ok, "achieved_bps": 800_000_000})).verdict == C.CLEAN
    assert C.judge(C.Evidence(**{**ok, "achieved_bps": 799_999_999})).verdict == C.NOT_PUSHED


@pytest.mark.parametrize("what, change, verdict", [
    ("the reached rate was not read", {"disks_behind": False, "achieved_bps": None}, C.UNDECIDED),
    ("whether the disks were behind was not read, the rate was reached", {"disks_behind": None, "achieved_bps": ASKED}, C.UNDECIDED),
    ("the disks were behind, the rate was reached", {"disks_behind": True, "achieved_bps": ASKED}, C.DISK_BOUND),
    ("the reached rate was not read, and the disks were behind", {"disks_behind": True, "achieved_bps": None}, C.DISK_BOUND),
])
def test_what_was_not_proved_is_never_clean_and_never_learned(what, change, verdict):
    assert C.judge(C.Evidence(**{**RS1, **change})).verdict == verdict, what


def test_a_loss_or_a_hot_cpu_is_judged_as_before():
    """Guard, both states (#3041): the new conditions are only for the clean path; a lost packet or a hot CPU with idle disks is still the machine's."""
    idle = {**RS1, "disks_behind": False, "achieved_bps": 100}
    assert C.judge(C.Evidence(**{**idle, "rcvbuf": 5})).verdict == C.LIMITED
    assert C.judge(C.Evidence(**{**idle, "cpu_floor": 90.0})).verdict == C.LIMITED


@pytest.mark.parametrize("stream, want", [
    (json.dumps({"chunk": {"asked_bps": ASKED, "achieved_bps": 419_592_605}}), 419_592_605),
    (json.dumps({"chunk": {"asked_bps": ASKED, "achieved_bps": None}}), None),             # the loss was not measured: nothing reached
    (json.dumps({"chunk": {"asked_bps": ASKED}}), None),
    (json.dumps({"chunk": {"achieved_bps": -1}}), None),
    (json.dumps({"chunk": {"achieved_bps": True}}), None),
    (json.dumps({"sent_bytes": 18545476482, "loss_blocks": 0, "stream_bps": 63_584_090, "stream_s": 290.1}), 508_672_720),   # bytes a second
    (json.dumps({"sent_bytes": 1, "stream_bps": None}), None),
    (json.dumps({"stream_bps": "63584090"}), None),
    (None, None), ("", None), ("not json", None), (json.dumps([1]), None),
])
def test_the_reached_rate_is_read_from_the_stream_of_the_wave(stream, want):
    assert C._achieved(stream) == want


# --- a wave, as the server sees it (the cases the negative control fails on) ------------------------------------------------------


def _rs1(conn, session):
    """The RS-1 wave: 669m asked, no loss, one drawer at 96% peak busy (the metal's `util_max`), 419.6Mb/s reached."""
    _wave(conn, session, bps=ASKED, rcvbuf=0, cpu=(46, 52, 54, 57), util=96, achieved=419_592_605)
    [row] = _rows(conn, session)
    return row


def test_the_rs1_wave_through_the_server_is_disk_bound_and_nothing_is_learned(conn):
    """(neg) The row the metal wrote CLEAN for. It says what it saw, and the memory has no `clean_bps`."""
    row = _rs1(conn, "ses_rs1")
    assert row["verdict"] == "DISK_BOUND" and "לא נלמד דבר" in row["outcome"], row
    assert "achieved_bps=419592605" in row["evidence"] and "disks_behind=True" in row["evidence"] and "stream_bps=669000000" in row["evidence"]
    assert _cap(conn) is None and C.learned(conn, MAC) is None


def test_a_wave_that_did_not_reach_the_rate_through_the_server_is_not_pushed_and_nothing_is_learned(conn):
    """(neg) The disks measured and idle, 419.6Mb/s of 669m: not CLEAN. The memory stays empty."""
    _wave(conn, "ses_slow", bps=ASKED, util=30, achieved=419_592_605)
    [row] = _rows(conn, "ses_slow")
    assert row["verdict"] == "NOT_PUSHED" and "לא נלמד דבר" in row["outcome"], row
    assert _cap(conn) is None


def test_a_udpcast_stream_through_the_server_is_read_in_bytes_a_second(conn):
    """(neg) The wave of `O0-1`: `stream_bps` of a udpcast wave is BYTES a second -- 63,584,090 is 508.7Mb/s, under 80% of 669m. No senses, as on the metal."""
    stream = json.dumps({"sent_bytes": 18545476482, "loss_blocks": 0, "stream_bps": 63_584_090, "stream_s": 290.1, "wall_s": 374.6})
    _wave(conn, "ses_udp", bps=ASKED, senses=False, stream=stream)
    [row] = _rows(conn, "ses_udp")
    assert row["verdict"] == "NOT_PUSHED" and "achieved_bps=508672720" in row["evidence"] and "disks_behind=unmeasured" in row["evidence"], row
    assert _cap(conn) is None


def test_a_wave_with_no_stream_recorded_yet_is_undecided_not_clean(conn):
    """(neg) The cloner reported before the engine recorded the wave's stream: nothing says what it reached."""
    _wave(conn, "ses_early", bps=ASKED, util=30, achieved=None)
    [row] = _rows(conn, "ses_early")
    assert row["verdict"] == "UNDECIDED" and "achieved_bps=unmeasured" in row["evidence"], row
    assert _cap(conn) is None


def test_a_wave_that_reached_the_rate_through_the_server_is_clean_and_learned(conn):
    """Guard, both states: 660Mb/s of 669m, the disks measured and idle."""
    _wave(conn, "ses_ok", bps=ASKED, util=30, achieved=660_000_000)
    [row] = _rows(conn, "ses_ok")
    assert row["verdict"] == "CLEAN" and "achieved_bps=660000000" in row["evidence"], row
    assert _cap(conn)["num"]["clean_bps"]["max"] == ASKED
