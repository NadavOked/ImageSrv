"""#3895 -- K3 (`DRAWER_HOLD`): the completion rate is the DURABLE rate of this drive, and a slow drive is not failed on an ETA.

Hardware, 19/11 (#3189, E3, `room_63714d82`): K3 row 13028 (`brain_decisions`, wave `ses_0b72db2b`, the APS-SL3N-512 of cloner 2)
estimated 22.74 chunks/s, while the HTTP windows measured 5.6 (E1) and 9.5 (E3). The 22.74 was `done_chunks=44 / elapsed_s=1.93`:
`received`, the counter of the whole machine and the pass, not of that drive's ledger. The inputs below are that row's evidence.

What is checked: (1) the rate is `grown / seconds` of the drive -- a big shared `received` does not raise it; with no durable growth there
is no rate (UNDECIDED, the ETA missing), and `received` is only written down; (2) an ETA-only DROP (`DROP` twice) never fails a drive that
runs above the floor in the settings -- in `on` and in `slow` it is HELD and completes; (3) a drive whose durable rate was MEASURED below the
floor is failed by name in `on`; (4) a floor that cannot be read fails nobody.

The floor values (20 and 50 MB/s) are test values: the floor that was in force on 19/11 was not looked up.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import drawerhold as H, memory

# row 13028's evidence (E3): the image, the wave's rate, the overhead of the wave, what was left
IMG = dict(chunks_total=18441, zbytes_total=18545444861, bits=892_000_000, parts=4)
E3 = dict(remaining=8734, later_chunks=173, elapsed_s=1.93, **IMG)
OVERHEAD = 0.9


def _judge(**over):
    return H.judge(**{**E3, "grown": None, "received": None, "overhead_s": OVERHEAD, **over})


def test_replay_e3_the_shared_received_counter_is_not_the_rate_of_the_drive():
    """Row 13028 as the engine fed it: 44 chunks `received` by the machine, nothing known of this drive's ledger. It said 22.74 chunks/s
    and DROP. The drive has no durable rate here: UNDECIDED, the ETA missing -- and `received` stays in the row as work evidence."""
    j = _judge(received=44)
    assert j.verdict == H.UNDECIDED and j.rate_chunks_s is None and j.t_continue_s is None, j
    assert "rate_chunks_s=unmeasured" in j.evidence and "received_chunks=44" in j.evidence and "rate_source=durable_grown" in j.evidence


def test_replay_e3_with_the_durable_growth_the_rate_is_the_measured_one():
    """The drive's own ledger grew 11 chunks in the 1.93 s: 5.7 chunks/s (the E3 HTTP window measured 5.6-9.5), not 22.7. The rate does
    not move when the machine's counter is 44 or 4,400."""
    for received in (44, 4_400, None):
        j = _judge(grown=11, received=received)
        assert j.rate_chunks_s == pytest.approx(11 / 1.93), (received, j)
        assert j.t_continue_s == pytest.approx((8734 + 173) / (11 / 1.93)) and "rate_chunks_s=5.70" in j.evidence, (received, j)


def test_the_shared_counter_does_not_raise_the_rate_of_a_slow_ledger():
    """(neg for `max(grown, received)`) A fast machine counter and a slow, advancing ledger: the rate is the ledger's."""
    j = _judge(grown=2, received=500)
    assert j.rate_chunks_s == pytest.approx(2 / 1.93) and j.verdict == H.DROP, j     # slow: the ETA proposes DROP; it is only a proposal


def test_the_judgment_says_the_rate_in_mbps_and_against_the_floor():
    """Rate, reason, proposal are separate fields: 5.7 chunks/s of 4 MiB = 23.9 MB/s; above a floor of 20, below a floor of 50, and
    `unmeasured` when no floor was read."""
    assert _judge(grown=11, floor_mbps=20).below_floor is False
    assert _judge(grown=11, floor_mbps=50).below_floor is True
    j = _judge(grown=11, floor_mbps=None)
    assert j.below_floor is None and "below_floor=unmeasured" in j.evidence and j.rate_mbps == pytest.approx(11 / 1.93 * 4194304 / 1e6)
    above = _judge(grown=11, floor_mbps=20)
    assert "below_floor=no" in above.evidence and "floor_mbps=20" in above.evidence


# --- what is handed to the engine ---------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def _tell(conn, setting, floor, *, grown=11, elapsed=1.93, drops=2, serial="APS", remaining=8734):
    """`drops` evaluations of one drawer, the last one's answer."""
    hold = H.DrawerHold(conn, mode_fn=lambda: "shadow", hold=lambda: setting, floor=floor)
    hold.gap("W-1", OVERHEAD)
    got = {}
    for n in range(drops):
        got = hold.evaluate(session_id="W-1", part=3, mac="c", received=44, elapsed_s=elapsed + n * 0.001, later_chunks=173,
                            drawers=[{"serial": serial, "remaining": remaining, "grown": grown}], **IMG)
    return got


@pytest.mark.parametrize("setting", ["on", "slow"])
def test_a_drop_on_an_eta_alone_is_a_hold_when_the_rate_is_above_the_floor(conn, setting):
    """(neg: bringing back the execution of DROP on the ETA alone fails this) 5.7 chunks/s = 23.9 MB/s against a floor of 20: the
    proposal is DROP (twice), the action is a HOLD -- the drive is not failed, it completes in the background."""
    got = _tell(conn, setting, lambda: 20)
    assert list(got) == ["APS"] and got["APS"][0] == "CONTINUE" and H.SLOW_HE in got["APS"][1], got
    (row,) = [r for r in memory.decisions(conn, rule="K3", limit=20) if r["action"] == H.ACTION]
    assert row["verdict"] == "DROP" and "proposal=DROP" in row["evidence"] and "below_floor=no" in row["evidence"], row


def test_a_drop_once_is_still_only_counted(conn):
    assert _tell(conn, "on", lambda: 20, drops=1) == {}


def test_a_rate_measured_below_the_floor_fails_the_drive_by_name_in_on(conn):
    """The one case that fails: `on`, DROP twice, and the durable rate (23.9 MB/s) measured BELOW the floor (50)."""
    got = _tell(conn, "on", lambda: 50)
    assert list(got) == ["APS"] and got["APS"][0] == "DROP" and "ייכתב בגל הבא" in got["APS"][1], got


def test_in_slow_the_drive_below_the_floor_is_still_only_held(conn):
    """The default (`slow`, #3605): below the floor too it is HELD, never failed -- failing is `on`'s alone."""
    got = _tell(conn, "slow", lambda: 50)
    assert got["APS"][0] == "CONTINUE"


def test_a_floor_that_cannot_be_read_fails_nobody(conn):
    """Principle 5: not reading the floor is not "below it". `on`, DROP twice: a hold."""
    def broken():
        raise OSError("settings unreadable")
    assert _tell(conn, "on", broken)["APS"][0] == "CONTINUE"
    assert _tell(conn, "on", lambda: None)["APS"][0] == "CONTINUE"


def test_no_durable_growth_hands_nothing_over_even_with_a_big_received(conn):
    """(neg for `max`) the drive's ledger did not grow, the machine took 44 chunks: no rate, UNDECIDED, nothing executed."""
    assert _tell(conn, "on", lambda: 50, grown=0) == {} and _tell(conn, "on", lambda: 50, grown=None) == {}
