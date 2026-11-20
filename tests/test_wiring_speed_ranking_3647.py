"""#3647 (Zeus 18/11, the sequel of #3600) -- the speed RANKING and TEXT of the wiring map follow the same rule as the "better, if it is free" note.

"חברו את N המגירות ל-SATA 0 ו-1" and the "מומלץ" chip on the fast ports are a speed recommendation. They are shown only when the measured network
is faster than a clean port by the margin (`netrate`), and a faster clean port exists. Otherwise -- a 1G network, or a network that was not measured --
only what concerns a cable is left: what to keep free because of CRC, or "no evidence of a bad cable", and "מומלץ" stays only on the target of a move
for a cable, which is chosen by port number, not by speed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_learning_2028 import CLONER1, CLONER2, _fleet, _hello, _obs, _wiring, room_server, wire_net  # noqa: E402,F401

NO_CABLE = "לא נמצאה ראיה לכבל פגום בחיבורי הדגם. מבוסס על 2 מחשבים מהדגם, 6 שיכפולים."


def _mixed_model(room_server, *, drawers=2, connected=(1, 3)):
    """SATA 0 and 1 (ports 1, 2) are 6.0 and SATA 2 (port 3) is 3.0, all clean, on two machines of the model, 3 waves each."""
    anon, conn = room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = ?", (drawers,))
    conn.commit()
    _hello(anon, CLONER1, list(connected))
    _hello(anon, CLONER2, [1, 2])
    for i in range(3):
        for mac in (CLONER1, CLONER2):
            _obs(conn, mac, 1, session=f"s{mac}{i}")
            _obs(conn, mac, 2, session=f"s{mac}{i}")
            _obs(conn, mac, 3, link=(3.0, 3.0), session=f"s{mac}{i}")


def _no_speed_recommendation(got):
    assert got["enough"] is True
    assert got["recommended"] == [] and got["text_he"] == NO_CABLE, got["text_he"]
    assert "חברו" not in got["text_he"] and "נמדדו נקיים" not in got["text_he"]
    assert all(p["advice"] is None for p in got["ports"].values()), {k: p["advice"] for k, p in got["ports"].items()}
    assert got["speed"] is False


def test_on_a_1g_network_there_is_no_speed_recommendation_only_the_cables_line(room_server, wire_net):
    _mixed_model(room_server)
    wire_net(CLONER1, 1000, 1000)
    _no_speed_recommendation(_wiring(room_server["admin"], CLONER1))


def test_a_network_that_was_not_measured_has_no_speed_recommendation_either(room_server):
    _mixed_model(room_server)
    _no_speed_recommendation(_wiring(room_server["admin"], CLONER1))


def test_the_2_5g_tie_has_no_speed_recommendation(room_server, wire_net):
    """281MB/s against 280MB/s (3.0) is below the margin (1.1): the same silence as the note's."""
    _mixed_model(room_server)
    wire_net(CLONER1, 2500, 2500)
    _no_speed_recommendation(_wiring(room_server["admin"], CLONER1))


def test_on_a_network_faster_than_a_clean_port_the_speed_recommendation_is_back(room_server, wire_net):
    _mixed_model(room_server)
    wire_net(CLONER1, 10000, 10000)
    got = _wiring(room_server["admin"], CLONER1)
    assert got["recommended"] == [1, 2], got
    assert got["text_he"].startswith("חברו את 2 המגירות ל-SATA 0 ו-1."), got["text_he"]
    assert got["ports"]["1"]["advice"] == "use" and got["ports"]["2"]["advice"] == "use" and got["ports"]["3"]["advice"] is None
    assert got["speed"] is True


def test_a_fast_network_with_no_faster_port_than_the_slow_one_has_no_speed_recommendation(room_server, wire_net):
    """Every clean port is 3.0 on a 10G network: all are the bottleneck, none is better -- nothing to rank by speed."""
    anon, conn = room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = 2")
    conn.commit()
    _hello(anon, CLONER1, [1, 2])
    _hello(anon, CLONER2, [1, 2])
    for i in range(3):
        for mac in (CLONER1, CLONER2):
            for port in (1, 2):
                _obs(conn, mac, port, link=(3.0, 3.0), session=f"s{mac}{i}")
    wire_net(CLONER1, 10000, 10000)
    got = _wiring(room_server["admin"], CLONER1)
    assert got["recommended"] == [] and got["text_he"] == NO_CABLE and got["speed"] is False


def test_a_cable_still_gets_its_line_its_move_and_its_chip_on_a_1g_network(room_server, wire_net):
    """CRC on SATA 3 (both machines): "leave it free", the move to SATA 4, and "מומלץ: במקום" on the target only -- on a network where no speed is said."""
    admin = _fleet(room_server)
    wire_net(CLONER1, 1000, 1000)
    got = _wiring(admin, CLONER1)
    assert got["recommended"] == [5] and got["avoid"] == [4] and got["speed"] is False
    assert got["text_he"] == ("SATA 3 יש עליו היסטוריית CRC, ולכן מומלץ להשאיר אותו פנוי."
                              " מבוסס על 2 מחשבים מהדגם, 6 שיכפולים.")
    assert got["moves"] == [{"from": 4, "to": 5, "drawer": 4}]
    assert got["ports"]["4"]["advice"] == "avoid" and (got["ports"]["5"]["advice"], got["ports"]["5"]["instead_of"]) == ("use", 4)
    assert all(got["ports"][k]["advice"] is None for k in ("1", "2", "3"))


def _cable_on_the_drawers_port(room_server):
    """One drawer on SATA 2 (port 3), which has CRC on both machines. Free and clean: SATA 0 (port 1, 3.0) and SATA 1 (port 2, 6.0)."""
    anon, conn = room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = 1")
    conn.commit()
    _hello(anon, CLONER1, [3])
    _hello(anon, CLONER2, [3])
    for i in range(3):
        for mac in (CLONER1, CLONER2):
            _obs(conn, mac, 1, link=(3.0, 3.0), session=f"s{mac}{i}")
            _obs(conn, mac, 2, session=f"s{mac}{i}")
            _obs(conn, mac, 3, crc=100, session=f"s{mac}{i}")


def test_the_target_of_a_cable_move_is_chosen_by_port_number_when_speed_is_not_said(room_server, wire_net):
    _cable_on_the_drawers_port(room_server)
    wire_net(CLONER1, 1000, 1000)
    got = _wiring(room_server["admin"], CLONER1)
    assert got["moves"] == [{"from": 3, "to": 1, "drawer": 1}], "SATA 0 (3.0) is the lowest clean port: speed is no reason to skip it on 1G"
    assert got["recommended"] == [1] and got["speed"] is False


def test_the_target_of_a_cable_move_is_the_fast_port_when_the_network_is_faster(room_server, wire_net):
    _cable_on_the_drawers_port(room_server)
    wire_net(CLONER1, 10000, 10000)
    got = _wiring(room_server["admin"], CLONER1)
    assert got["moves"] == [{"from": 3, "to": 2, "drawer": 1}] and got["speed"] is True
