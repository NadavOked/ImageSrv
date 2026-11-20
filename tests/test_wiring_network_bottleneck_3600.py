"""#3600 (Nadav 17/11, 18/11) -- a slow SATA port is named ONLY when it is the bottleneck of the network that was measured.

"Between the network and the port, the slower one is the reason." On a 1G network (112MB/s) nothing is said about a 1.5, 3.0 or 6.0 port at
all: no warning, no "better, if it is free", no chip. The suggestion appears when the measured wire (the cloner's card AND the server's card,
the slower of the two) is faster than the port's ceiling, and the note says why. A network that was not measured is no recommendation
("not known" is not "bottleneck"). A cable (CRC, a port that saw 6.0 and fell) is not touched by any of this.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server import db  # noqa: E402
from test_learning_2028 import CLONER1, CLONER2, _fleet, _hello, _obs, _wiring, room_server, wire_net  # noqa: E402,F401

#: the setting's key as a literal, not `MARGIN`: a test that imports the new module cannot fail on the old code except by ImportError.
MARGIN = "wiring_net_margin"


def _slow_port_model(room_server, link):
    """CLONER1 has its one drawer on SATA 2 (port 3, `link` Gb/s); SATA 0 and 1 (ports 1, 2) are 6.0 and free. Two machines of the model, 3 waves each."""
    anon, conn = room_server["anon"], room_server["ctx"].conn
    conn.execute("UPDATE machines SET drawer_count = 1")
    conn.commit()
    _hello(anon, CLONER1, [3])
    _hello(anon, CLONER2, [1])
    for i in range(3):
        for mac in (CLONER1, CLONER2):
            _obs(conn, mac, 1, session=f"s{mac}{i}")
            _obs(conn, mac, 2, session=f"s{mac}{i}")
            _obs(conn, mac, 3, link=(link, 6.0), session=f"s{mac}{i}")


def _silent(got):
    assert got["enough"] is True, got
    assert got["upgrades"] == [] and got["upgrade_notes_he"] == [], got
    assert all(p["better_port"] is None and p["better_gbps"] is None and p["advice"] != "avoid" for p in got["ports"].values()), got["ports"]
    assert got["avoid"] == [] and got["moves"] == []


@pytest.mark.parametrize("link", [1.5, 3.0])
def test_on_a_1g_network_a_slow_port_says_nothing_at_all(room_server, wire_net, link):
    """Nadav 17/11: on a 1G network there is no message -- 112MB/s is under even 1.5 (140MB/s): no suggestion, no warning, no chip."""
    _slow_port_model(room_server, link)
    wire_net(CLONER1, 1000, 1000)
    got = _wiring(room_server["admin"], CLONER1)
    _silent(got)
    assert got["network"]["mbs"] == 112.5 and got["network"]["by"] == "cloner"


def test_a_faster_cloner_card_does_not_help_when_the_servers_card_is_1g(room_server, wire_net):
    """The wire is the slower of the two cards, as the brain decides a wave's ceiling (`drive_rates.choose`)."""
    _slow_port_model(room_server, 3.0)
    wire_net(CLONER1, 10000, 1000)
    got = _wiring(room_server["admin"], CLONER1)
    _silent(got)
    assert (got["network"]["by"], got["network"]["mbps"]) == ("server", 1000)


def test_a_faster_server_card_does_not_help_when_the_cloners_card_is_1g(room_server, wire_net):
    _slow_port_model(room_server, 3.0)
    wire_net(CLONER1, 1000, 10000)
    got = _wiring(room_server["admin"], CLONER1)
    _silent(got)
    assert (got["network"]["by"], got["network"]["mbps"]) == ("cloner", 1000)


@pytest.mark.parametrize("link, ceiling", [(1.5, 140), (3.0, 280)])
def test_on_a_network_faster_than_the_port_the_port_is_named_as_the_reason(room_server, wire_net, link, ceiling):
    """10G (1125MB/s): both 1.5 and 3.0 are the bottleneck. The suggestion is information (no suspicion), and the note says WHY: the port's
    ceiling against the network's."""
    _slow_port_model(room_server, link)
    wire_net(CLONER1, 10000, 10000)
    got = _wiring(room_server["admin"], CLONER1)
    assert got["avoid"] == [] and got["moves"] == [] and got["ports"]["3"]["advice"] is None
    assert got["upgrades"] == [{"from": 3, "to": 1, "drawer": 1, "from_gbps": link, "to_gbps": 6.0, "from_mbs": ceiling, "net_mbs": 1125}]
    assert got["ports"]["3"]["better_port"] == 1
    assert f"עד {ceiling} MB/s מול כ-1125 MB/s שהרשת נותנת" in got["upgrade_notes_he"][0]
    assert "כבל" not in got["upgrade_notes_he"][0] and "חשוד" not in got["upgrade_notes_he"][0]


def test_a_6_gbps_port_has_nothing_faster_to_suggest(room_server, wire_net):
    """A 6.0 port on a 10G wire is the slowest thing in the path, but no port the model saw is faster: nothing to suggest."""
    _slow_port_model(room_server, 6.0)
    wire_net(CLONER1, 10000, 10000)
    _silent(_wiring(room_server["admin"], CLONER1))


def test_a_network_that_was_not_measured_is_no_recommendation_and_the_map_says_which_half_is_missing(room_server, wire_net):
    """"Not known" is not "bottleneck" (principle 5): no cloner card, or no server card -- no suggestion, and `network.why` names it."""
    _slow_port_model(room_server, 3.0)
    admin = room_server["admin"]
    got = _wiring(admin, CLONER1)
    _silent(got)
    assert got["network"]["mbs"] is None and got["network"]["why"] == "cloner_no_probe", got["network"]
    wire_net(CLONER1, 10000, None)                                   # the cloner is measured, no wave has recorded the server's card
    got = _wiring(admin, CLONER1)
    _silent(got)
    assert got["network"]["mbs"] is None and got["network"]["why"] == "server_no_wave" and got["network"]["cloner_mbps"] == 10000
    wire_net(CLONER1, 10000, 10000)
    assert _wiring(admin, CLONER1)["upgrades"], "both measured -> the suggestion is back"


def test_a_server_card_that_the_last_wave_could_not_read_is_not_known(room_server, wire_net):
    """A virtual or absent card (`drive_rates.read_link`: speed_mbps None) in the latest wave is "not known" even if an older wave read 10G."""
    _slow_port_model(room_server, 3.0)
    conn = room_server["ctx"].conn
    wire_net(CLONER1, 10000, 10000)
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES ('ses_virtual', ?, '2999-01-01T00:00:00+00:00')",
                 ('{"link": {"interface": "ens192", "speed_mbps": null, "why": "virtual", "applied": false}}',))
    conn.commit()
    got = _wiring(room_server["admin"], CLONER1)
    _silent(got)
    assert got["network"]["why"] == "server_unmeasured"


def test_a_direct_wave_says_nothing_about_the_servers_card(room_server, wire_net):
    """A build machine that sends (`direct`) does not use the server's card: its newer row (10G) is skipped, and the server's own 1G stands."""
    _slow_port_model(room_server, 3.0)
    conn = room_server["ctx"].conn
    wire_net(CLONER1, 10000, 1000)
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES ('ses_direct', ?, '2999-01-01T00:00:00+00:00')",
                 ('{"direct": true, "link": {"interface": "eth1", "speed_mbps": 10000, "why": null, "applied": false}}',))
    conn.commit()
    got = _wiring(room_server["admin"], CLONER1)
    _silent(got)
    assert got["network"]["server_mbps"] == 1000


def test_a_cable_with_crc_is_still_avoided_on_a_1g_network_and_moved(room_server, wire_net):
    """The cable evidence (CRC on both machines) is untouched by the network: still `avoid`, still a move -- on a 1G network, where every speed note is silent."""
    admin = _fleet(room_server)
    wire_net(CLONER1, 1000, 1000)
    got = _wiring(admin, CLONER1)
    assert got["avoid"] == [4] and got["ports"]["4"]["advice"] == "avoid"
    assert got["moves"] == [{"from": 4, "to": 5, "drawer": 4}] and got["move_notes_he"]
    assert got["upgrades"] == []


# ---------- the margin (Zeus 18/11): 2.5G against a 3.0 port is a tie, not a reason; the threshold is a setting, default 1.1 ----------

def test_2_5g_against_a_3_0_port_is_a_tie_and_says_nothing(room_server, wire_net):
    """2.5G = 281MB/s against 3.0 = 280MB/s: moving to 6.0 gains nothing. 281 < 1.1 x 280 = 308."""
    _slow_port_model(room_server, 3.0)
    wire_net(CLONER1, 2500, 2500)
    got = _wiring(room_server["admin"], CLONER1)
    _silent(got)
    assert got["network"]["mbs"] == 281.25 and got["network"]["margin"] == 1.1


def test_2_5g_against_a_1_5_port_is_still_a_real_gain(room_server, wire_net):
    """281MB/s against 1.5 = 140MB/s: twice as fast, so the 1.5 port IS the bottleneck, margin or not."""
    _slow_port_model(room_server, 1.5)
    wire_net(CLONER1, 2500, 2500)
    got = _wiring(room_server["admin"], CLONER1)
    assert [(u["from"], u["from_mbs"], u["net_mbs"]) for u in got["upgrades"]] == [(3, 140, 281)]


def test_the_margin_is_a_setting_and_a_lower_one_brings_the_tie_back(room_server, wire_net):
    _slow_port_model(room_server, 3.0)
    wire_net(CLONER1, 2500, 2500)
    admin, conn = room_server["admin"], room_server["ctx"].conn
    assert admin.get("/api/console/settings").json()[MARGIN] == "1.1", "the value in force, not 'not set'"
    assert admin.post("/api/console/settings", json={MARGIN: "1.0"}).status_code == 200
    assert db.get_setting(conn, MARGIN) == "1"
    got = _wiring(admin, CLONER1)
    assert got["network"]["margin"] == 1.0 and [u["from"] for u in got["upgrades"]] == [3]       # 281.25 >= 1.0 x 280
    assert admin.post("/api/console/settings", json={MARGIN: 1.5}).status_code == 200
    assert admin.get("/api/console/settings").json()[MARGIN] == "1.5"


@pytest.mark.parametrize("bad", ["0.5", "11", "abc", "", "1.123", "nan", "inf", True, None, [1.1]])
def test_a_margin_that_is_not_a_number_in_range_is_refused_and_nothing_is_written(room_server, bad):
    admin, conn = room_server["admin"], room_server["ctx"].conn
    assert admin.post("/api/console/settings", json={MARGIN: bad}).status_code == 400
    assert db.get_setting(conn, MARGIN) is None


@pytest.mark.parametrize("stored", ["abc", "0", "0.9", "99", "nan"])
def test_a_damaged_stored_margin_is_the_default_never_zero(room_server, wire_net, stored):
    """0 would make every port "the bottleneck": a damaged value is 1.1 (as `writefloor.mbps` does), and the 2.5G tie stays silent."""
    _slow_port_model(room_server, 3.0)
    wire_net(CLONER1, 2500, 2500)
    db.set_setting(room_server["ctx"].conn, MARGIN, stored)
    got = _wiring(room_server["admin"], CLONER1)
    _silent(got)
    assert got["network"]["margin"] == 1.1
