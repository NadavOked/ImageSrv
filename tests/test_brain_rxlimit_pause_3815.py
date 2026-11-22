"""#3815 (part 2): the card's pause frames, bytes taken in and ring size reach the evidence of B59.

A switch that sends the card pause frames looks exactly like loss (`rx_no_buffer` rises); without the counters nothing tells it
from a slow card. `wavesense.numbers` now keeps the pause counters, the pause setting, `rx_bytes` and `ring_rx_max`, and
`rxlimit.diagnose` writes them into the evidence of the `B59`/`RX_LIMIT` row. Evidence only: no threshold, the verdict does
not move. A sense that was not read is "unmeasured" in the evidence -- never 0 and never "no pause" (principle 5).
"""

from __future__ import annotations

import json

from server.brain import rxlimit, wavesense

CLEAN_DROPS = {"rcvbuf": 0, "inerrors": 0, "abandoned": 0}


def _wave(**nic):
    return wavesense.numbers(json.dumps({"nic": {"iface": "eth0", "ethtool": True, "rx_missed": 0, "rx_no_buffer": 0, **nic},
                                         "cpu": [{"busy": 40}], "disks": {"sda": {"util_max": 30}}}))


def _evidence(wave):
    return dict(e.split("=", 1) for e in rxlimit.diagnose(CLEAN_DROPS, wave, None, None)[2] if "=" in e)


def test_the_pause_frames_the_bytes_and_the_ring_are_in_the_evidence():
    ev = _evidence(_wave(rx_bytes=7000000, ring_rx=256, ring_rx_max=4096, tx_xon=3, tx_xoff=50, rx_xon=0, rx_xoff=7,
                         pause_rx=True, pause_tx=False))
    assert ev["rx_bytes"] == "7000000" and ev["ring_rx"] == "256" and ev["ring_rx_max"] == "4096"
    assert (ev["tx_xon"], ev["tx_xoff"], ev["rx_xon"], ev["rx_xoff"]) == ("3", "50", "0", "7")
    assert ev["pause_rx"] == "on" and ev["pause_tx"] == "off"


def test_what_the_driver_did_not_say_is_unmeasured_and_not_zero_or_no_pause():
    ev = _evidence(_wave(rx_bytes=100, ring_rx=None, tx_xon=None, tx_xoff=None, rx_xon=None, rx_xoff=None, pause_rx=None))
    assert ev["rx_bytes"] == "100"
    for key in ("ring_rx", "ring_rx_max", "tx_xon", "tx_xoff", "rx_xon", "rx_xoff", "pause_rx", "pause_tx"):
        assert ev[key] == "unmeasured", (key, ev[key])


def test_a_zero_pause_counter_that_was_read_differs_from_one_that_was_not():
    assert _evidence(_wave(tx_xoff=0))["tx_xoff"] == "0"
    assert _evidence(_wave())["tx_xoff"] == "unmeasured"


def test_the_pause_evidence_does_not_move_the_verdict():
    quiet = rxlimit.diagnose(CLEAN_DROPS, _wave(), None, None)[:2]
    paused = rxlimit.diagnose(CLEAN_DROPS, _wave(tx_xoff=900, rx_xoff=900, pause_rx=True), None, None)[:2]
    assert paused == quiet
