"""#3815 (part): the card's pause frames and the bytes it took in reach the brain's senses.

A switch that sends the card a pause frame looks exactly like loss (`rx_no_buffer` rises), and without the counter nothing
tells it from a slow card or a slow bus. And without `rx_bytes` the brain compares what the sender sent only to packets, not
to what each card took in. The agent (`agent/lib/restore/wavesense.sh`) now reads, since the wave began, `rx_bytes` (sysfs)
and the `{tx,rx}_flow_control_{xon,xoff}` counters (`ethtool -S`), and the pause setting (`ethtool -a`); the server keeps
them beside the S50 fields.

Principle 5: a driver that has no such counter, or a machine without ethtool, says null -- never "no pause".
"""

from __future__ import annotations

import json

from native import requires_native
from server.brain import wavesense
from test_agent import BASH
from test_wave_senses_3041 import _unit

needs_bash = requires_native(("bash", BASH), why="the agent's shell")

#: `ethtool -S` of a card that counts pause frames, as `e1000e`/`igb` print them
STATS = r'''
stats() { printf 'NIC statistics:\n     rx_packets: 100\n     rx_no_buffer_count: 0\n     tx_flow_control_xon: %s\n     tx_flow_control_xoff: %s\n     rx_flow_control_xon: %s\n     rx_flow_control_xoff: %s\n' "$1" "$2" "$3" "$4" > "$RUN_DIR/eth-S"; }
pausecfg() { printf 'Pause parameters for eth0:\nAutonegotiate:\toff\nRX:\t\t%s\nTX:\t\t%s\n' "$1" "$2" > "$RUN_DIR/eth-a"; }
rxbytes() { echo "$1" > "$SYSROOT/sys/class/net/eth0/statistics/rx_bytes"; }
'''


@needs_bash
def test_the_pause_frames_and_the_bytes_taken_in_are_counted_since_the_wave_began(tmp_path):
    got = _unit(tmp_path, STATS + r'''
up 100; nodisks; ring 256 4096; stats 1 5 0 2; pausecfg on off; rxbytes 1000000
pass
up 106; stats 4 55 0 9; rxbytes 8000000; tick; show w1
''')
    nic = got["w1"]["nic"]
    assert nic["rx_bytes"] == 7000000
    assert (nic["tx_xon"], nic["tx_xoff"], nic["rx_xon"], nic["rx_xoff"]) == (3, 50, 0, 7)
    assert nic["pause_rx"] is True and nic["pause_tx"] is False


@needs_bash
def test_a_driver_without_the_counters_says_null_and_not_no_pause(tmp_path):
    got = _unit(tmp_path, STATS + r'''
up 100; nodisks; ring 256 4096; rxbytes 10
printf 'NIC statistics:\n     rx_packets: 100\n' > "$RUN_DIR/eth-S"
pass
up 106; rxbytes 30; tick; show w1
''')
    nic = got["w1"]["nic"]
    assert nic["rx_bytes"] == 20
    assert nic["ethtool"] is True
    assert (nic["tx_xon"], nic["tx_xoff"], nic["rx_xon"], nic["rx_xoff"]) == (None, None, None, None)
    assert nic["pause_rx"] is None and nic["pause_tx"] is None, "an `ethtool -a` that said nothing became a setting"


@needs_bash
def test_a_counter_that_went_down_in_the_wave_is_not_read(tmp_path):
    got = _unit(tmp_path, STATS + r'''
up 100; nodisks; ring 256 4096; stats 5 9 0 4; rxbytes 900
pass
up 106; stats 6 2 0 4; rxbytes 100; tick; show w1
''')
    nic = got["w1"]["nic"]
    assert nic["tx_xon"] == 1 and nic["tx_xoff"] is None and nic["rx_bytes"] is None


@needs_bash
def test_no_ethtool_leaves_the_bytes_from_sysfs_and_the_pause_not_available(tmp_path):
    got = _unit(tmp_path, STATS + r'''
up 100; nodisks; rxbytes 100
pass
up 106; rxbytes 400; tick; show w1
''', env={"NO_ETHTOOL": "1"})
    nic = got["w1"]["nic"]
    assert nic["ethtool"] is False and nic["rx_bytes"] == 300
    assert nic["tx_xoff"] is None and nic["pause_rx"] is None


def test_the_server_keeps_the_pause_fields_and_nulls_what_only_ethtool_reads():
    sent = {"iface": "eth0", "ethtool": True, "rx_bytes": 7, "tx_xoff": 50, "rx_xoff": 0, "pause_rx": True,
            "pause_tx": "yes", "rx_xon": -1}
    kept = wavesense.well_formed({"nic": sent})["nic"]
    assert kept["rx_bytes"] == 7 and kept["tx_xoff"] == 50 and kept["rx_xoff"] == 0
    assert kept["pause_rx"] is True
    assert kept["pause_tx"] is None, "a pause setting that is not a boolean was kept"
    assert kept["rx_xon"] is None, "a negative counter was kept"
    assert kept["tx_xon"] is None, "a counter the agent did not send became a 0"
    # the machine has no ethtool, and still sends numbers beside it: they cannot be measurements
    none = wavesense.well_formed({"nic": {**sent, "ethtool": False}})["nic"]
    assert none["tx_xoff"] is None and none["pause_rx"] is None and none["rx_bytes"] == 7


def test_the_bytes_taken_in_reach_the_long_term_memory_and_a_missing_one_stays_absent():
    row = json.dumps({"nic": {"iface": "eth0", "ethtool": True, "rx_bytes": 123456}})
    assert wavesense.numbers(row)["rx_bytes"] == 123456
    assert "rx_bytes" not in wavesense.numbers(json.dumps({"nic": {"iface": "eth0", "rx_bytes": None}}))
