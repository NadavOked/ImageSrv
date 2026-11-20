import json

import pytest

from test_nic_live_3277 import _hello
from test_server_room import CLONER1, room_server  # noqa: F401


def test_hello_bus_in_machine_details(room_server):
    bus = dict(bus="pci", capable_66mhz=False, bridge_capable_66mhz=True)
    for extra, expected in (({"nic_bus": bus}, bus), ({}, None)):
        _hello(room_server["anon"], ["S1"], **extra)
        rows = room_server["admin"].get("/api/console/machines").json()
        assert next(row for row in rows if row["mac"] == CLONER1)["nic_bus"] == expected


@pytest.mark.parametrize("kind,losses,verdict,cap", [
    ("pci", (390, 9), "SHOW", 417), ("pci", (390,), "SHOW", None),
    ("pci", (0,), "NONE", None), ("pci", (), "NOT_MEASURED", None),
    ("pcie", (390, 9), "NONE", None), ("unknown", (390, 9), "NOT_MEASURED", None),
])
def test_shadow_decision(room_server, kind, losses, verdict, cap):
    conn = room_server["ctx"].conn
    for i, rate in enumerate((891, 417)):
        drops = {"other-machine": {"abandoned": 999}}
        if i < len(losses):
            drops[CLONER1] = {"abandoned": losses[i]}
        stream = {"chunk": {"ended": "send_done", "teaches": True, "sent_bps": rate * 1_000_000, "chunks": 1000}}
        conn.execute("INSERT INTO wave_rates(session_id,at,decision,stream,drops) VALUES(?,?,?,?,?)",
                     (f"wave{i}", "now", "{}", json.dumps(stream), json.dumps(drops)))
    conn.commit()
    for _ in range(2):
        answer = _hello(room_server["anon"], ["S1"], nic_link={"speed_mbps": 1000}, nic_bus={"bus": kind})
        assert answer["link_verdict"]["bus_banner"] == verdict
        assert answer["link_verdict"]["ceiling_mbps"] == cap
    row, = conn.execute("SELECT * FROM brain_decisions WHERE rule='K-NICBUS'").fetchall()
    assert (row["entity_key"], row["verdict"], row["mode"], row["outcome"]) == (CLONER1, verdict, "shadow", "n/a: shadow")
    if cap:
        assert "wave1: abandoned=9" in row["evidence_json"]


def test_a_wave_that_does_not_teach_is_not_evidence_against_the_bus(room_server):
    """A lost machine (chunk_net_gap) or a held-back disk loses chunks for reasons that are not the bus."""
    conn = room_server["ctx"].conn
    for i, (rate, lost, teaches) in enumerate(((891, 390, False), (417, 9, True))):
        stream = {"chunk": {"ended": "send_done", "teaches": teaches, "skip": None if teaches else "net_gap",
                            "sent_bps": rate * 1_000_000, "chunks": 1000}}
        conn.execute("INSERT INTO wave_rates(session_id,at,decision,stream,drops) VALUES(?,?,?,?,?)",
                     (f"wave{i}", "now", "{}", json.dumps(stream), json.dumps({CLONER1: {"abandoned": lost}})))
    conn.commit()
    answer = _hello(room_server["anon"], ["S1"], nic_link={"speed_mbps": 1000}, nic_bus={"bus": "pci"})
    assert (answer["link_verdict"]["bus_banner"], answer["link_verdict"]["ceiling_mbps"]) == ("SHOW", None)
    row, = conn.execute("SELECT evidence_json FROM brain_decisions WHERE rule='K-NICBUS'").fetchall()
    assert "wave0" not in row["evidence_json"] and "wave1: abandoned=9" in row["evidence_json"]
