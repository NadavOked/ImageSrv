"""Exercise the live link reading shared by the brain and the wave rate selector."""

from server.rounds import nic_live, nic_speed
from server import db


def test_fresh_slow_receiver_limits_the_wave(tmp_path):
    mac = "aa:bb:cc:00:00:21"
    conn = db.connect(tmp_path / "link.db")
    try:
        nic_live.note(mac, {"iface": "eth0", "speed_mbps": 100}, None)
        slowest = nic_speed.slowest(conn, [mac])
        assert (slowest["speed_mbps"], slowest["source"]) == (100, "reported")
    finally:
        conn.close()
