"""#2847: ‏/machines מחזיר את מהירות הקישור **שנמדדה** (‏`nic_link_mbps`), נפרדת מהידנית ומההנחה.

הקונסולה צובעת בכתום קישור מתחת ל-1000Mb/s. הגדרה ידנית של כרטיס ההפצה (‏`nic_speed`) קובעת את
תקרת הגל ואסור לה להסתיר קישור איטי שנמדד; ו"לא נמדד" הוא `null`, לא 1000.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from server.rounds import nic_speed

MAC = "aa:bb:cc:dd:ee:01"


def _nic(speed, conflict=None):
    return {"name": "enp2s0", "speed_mbps": speed, "duplex": "full",
            "stats": {"rx_crc_errors": 0, "rx_dropped": 0, "tx_errors": 0, "collisions": 0},
            "ip_conflict": conflict}


def _machine(server, probe):
    r = server["admin"].post("/api/console/machines",
                             json={"mac": MAC, "name": "Cloner", "group_id": "grp_CLONERS"})
    assert r.status_code == 200, r.text
    body = hello_body(MAC)
    if probe is not None:
        body["probe"] = probe
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    rows = server["admin"].get("/api/console/machines").json()
    return next(m for m in rows if m["mac"] == MAC)


def test_a_100mbps_link_is_reported_as_measured(server):
    row = _machine(server, {"nic": [_nic(100)]})
    assert row["nic_link_mbps"] == 100


def test_a_manual_speed_does_not_hide_the_measured_slow_link(server):
    nic_speed.set_manual(server["ctx"].conn, MAC, 1000)
    row = _machine(server, {"nic": [_nic(100)]})
    assert row["nic_speed"]["speed_mbps"] == 1000 and row["nic_speed"]["source"] == "manual"
    assert row["nic_link_mbps"] == 100


@pytest.mark.parametrize("probe", [None, {"nic": []}, {"nic": [_nic(None)]}, {"nic": [_nic(-1)]},
                                   {"nic": [_nic(1000), _nic(100)]}])
def test_unmeasured_or_ambiguous_is_null_not_1000(server, probe):
    row = _machine(server, probe)
    assert row["nic_link_mbps"] is None
