"""#3594: standing bytes are not yet "the network is lost" -- a drawer waiting for its own disk is not the network.

J1 (17/11, cloner 2): the Samsung drawer (a 3.0 link) fell behind, its ring filled, and fanout held the stream for every
drawer -- 75 s without a byte. `stream_beep_tick` read the age of the last byte (>= NETLOST_AFTER_S) and sounded
"network lost", then "back" (`round_beeps: played: netlost, netback`) on a round that finished; the cable was checked
instead of the drawer. stall.sh had already named it: `waiting_disk` (the stream kept coming after that drawer stopped).

`stall_disk_holds` (stall.sh): a writing drawer standing for its own disk, and no interface that has lost its
carrier = the standstill is the disk's. `stream_beep_tick` then says nothing. Unchanged: NETLOST_AFTER_S, the tunes, an
outage with no drawer waiting for a disk, `waiting_net`, and a pulled cable (carrier 0), which the disk does not hide.
"""

from __future__ import annotations

from test_beeps_2067 import expected, frequencies, log_lines, prelude, run_ash

STREAMING = """
    . "$LIB_DIR/stall.sh"
    command mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/class/net/eth0" "$GUI_DIR"
    SYSFS_ROOT="$RUN_DIR"; echo 1 > "$RUN_DIR/class/net/eth0/carrier"   # the cable is in (stall.sh reads $SYSFS_ROOT/class/net)
    echo writing > "$RUN_DIR/targets/sda/state"; echo writing > "$RUN_DIR/state"; echo 1 > "$RUN_DIR/bytes.raw"
    t=1000; date() { echo "$t"; }
    moved() { echo "$t 1" > "$GUI_DIR/moved.sda"; }
    stall() { echo "$1 1000 150" > "$RUN_DIR/targets/sda/stall"; }     # what stall.sh writes: "<kind> <epoch of its last byte> <limit>"
    tick() { stream_beep_tick; wait; }
    moved; tick                                          # 1000: the stream flows
"""


def test_a_drawer_waiting_for_its_disk_is_not_a_network_outage_and_nothing_is_heard(tmp_path):
    run_ash(prelude(tmp_path) + STREAMING + """
        stall waiting_disk
        t=1040; tick                                     # 40 s without a byte, J1's shape
        t=1075; tick
        t=1076; moved; tick                              # it moves again
    """)
    assert frequencies(tmp_path) == [], log_lines(tmp_path)
    assert not (tmp_path / "beep.net").exists() or (tmp_path / "beep.net").read_text().strip() != "lost"


def test_the_same_standstill_with_no_drawer_waiting_for_a_disk_is_the_outage_it_was(tmp_path):
    """Guard (both states): no stall record -- the network's tune, then "back" once the server answers and a drawer moves (#3106)."""
    run_ash(prelude(tmp_path) + STREAMING + """
        t=1040; tick
        t=1043; echo 1043 > "$RUN_DIR/net.heard"; moved; tick
    """)
    assert frequencies(tmp_path) == expected("netlost") + expected("netback"), log_lines(tmp_path)


def test_a_drawer_waiting_for_the_network_is_the_outage(tmp_path):
    """Guard: stall.sh's own `waiting_net` is positive evidence of the network, not of a disk."""
    run_ash(prelude(tmp_path) + STREAMING + """
        stall waiting_net
        t=1040; tick
    """)
    assert frequencies(tmp_path) == expected("netlost"), log_lines(tmp_path)


def test_a_pulled_cable_is_the_network_even_with_a_drawer_waiting_for_its_disk(tmp_path):
    """The disk does not hide a cable: carrier 0 on every interface and the tune is heard."""
    run_ash(prelude(tmp_path) + STREAMING + """
        stall waiting_disk; echo 0 > "$RUN_DIR/class/net/eth0/carrier"
        t=1040; tick
    """)
    assert frequencies(tmp_path) == expected("netlost"), log_lines(tmp_path)


def test_when_the_disk_stops_holding_and_the_bytes_still_stand_the_network_tune_follows(tmp_path):
    run_ash(prelude(tmp_path) + STREAMING + """
        stall waiting_disk
        t=1040; tick
        rm -f "$RUN_DIR/targets/sda/stall"               # the drawer's record ended; the stream still stands
        t=1050; tick
    """)
    assert frequencies(tmp_path) == expected("netlost"), log_lines(tmp_path)
