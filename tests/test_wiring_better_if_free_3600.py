"""#3600 (Nadav 17/11) -- the wiring map says "better, if it is free", never "suspect, do not connect", for a port that is slower.

A port that is always 3.0 is the board's (Q77: SATA 0/1 are 6.0, SATA 2/3 are 3.0) -- the map used to call it `avoid`
("חשוד: לא לחבר") and "slower than usual for its model". Now `avoid` is the evidence of a cable only (CRC, a cable failure), and a
connected drawer on a slower port gets an `upgrades` entry when a FREE port the model shows faster exists.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_learning_2028 import CLONER1, CLONER2, _fleet, _hello, _obs, _wiring, room_server, wire_net  # noqa: E402,F401


def test_with_no_faster_free_port_there_is_nothing_to_suggest(room_server, wire_net):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = 2")
    conn.commit()
    _hello(anon, CLONER1, [1, 2])
    _hello(anon, CLONER2, [1, 2])
    for i in range(3):
        for mac in (CLONER1, CLONER2):
            for port in (1, 2):
                _obs(conn, mac, port, link=(3.0, 6.0), session=f"s{mac}{i}")      # every port is 3.0
    wire_net(CLONER1, 10000, 10000)             # the wire is faster than the ports, so a suggestion is possible -- but nothing is faster
    got = _wiring(admin, CLONER1)
    assert got["enough"] is True and got["avoid"] == [] and got["upgrades"] == [] and got["upgrade_notes_he"] == [], got
    assert all(p["better_port"] is None and p["advice"] != "avoid" for p in got["ports"].values())


def test_below_the_threshold_there_is_no_suggestion(room_server, wire_net):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    _hello(anon, CLONER1, [1, 3])
    _obs(conn, CLONER1, 1)
    _obs(conn, CLONER1, 3, link=(3.0, 3.0))
    wire_net(CLONER1, 10000, 10000)
    got = _wiring(admin, CLONER1)
    assert got["enough"] is False and got["upgrades"] == []


def test_a_port_with_the_cable_evidence_is_still_avoided_and_is_not_an_upgrade_source(room_server):
    admin = _fleet(room_server)                  # SATA 3 (port 4) has a CRC history on both machines, and is connected
    got = _wiring(admin, CLONER1)
    assert got["avoid"] == [4] and got["ports"]["4"]["advice"] == "avoid"
    assert got["moves"] == [{"from": 4, "to": 5, "drawer": 4}], "the cable's move is the recommendation"
    assert got["upgrades"] == [] and got["ports"]["4"]["better_port"] is None


def test_the_speed_never_enters_the_avoid_list_even_when_it_is_the_only_difference(room_server, wire_net):
    admin, anon, conn = room_server["admin"], room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = 1")
    conn.commit()
    _hello(anon, CLONER1, [3])
    _hello(anon, CLONER2, [1])
    for i in range(3):
        for mac in (CLONER1, CLONER2):
            _obs(conn, mac, 1, session=f"s{mac}{i}")                              # 6.0
            _obs(conn, mac, 3, link=(1.5, 6.0), session=f"s{mac}{i}")             # even 1.5 (#3591: no speed is a suspicion)
    wire_net(CLONER1, 10000, 10000)             # a wire that is faster than even 6.0: 1.5 is the bottleneck, and still no suspicion
    got = _wiring(admin, CLONER1)
    assert got["avoid"] == [] and got["ports"]["3"]["advice"] is None, got
    assert [(u["from"], u["to"]) for u in got["upgrades"]] == [(3, 1)]
