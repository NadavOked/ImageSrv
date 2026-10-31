"""‏#1888 — היסטוריית רשת ו-IP לכל מכונה.

‏`probe.py` דורס את מוני הכרטיס בכל דגימה; ‏`net_devices.ip` דורס את ה-IP. כאן נשמרת ההיסטוריה — בלי
לכתוב בכל hello: שורת IP רק כשהשתנה, ודגימת מונים רק כשמשהו השתנה או כשעברה שעה.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.agent import probe
from server.learn import net_history

MAC = "78:ac:c0:9b:18:88"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def test_an_ip_row_is_written_only_when_it_changes_and_a_change_is_journaled(conn):
    db.net_seen(conn, MAC, "10.44.12.31")
    db.net_seen(conn, MAC, "10.44.12.31")
    db.net_seen(conn, MAC, None)                               # לא נקרא — אינו שינוי
    db.net_seen(conn, MAC, "10.44.12.77")
    got = net_history.ip_history(conn, MAC)
    assert [(r["ip"], r["previous"]) for r in got] == [("10.44.12.77", "10.44.12.31"),
                                                      ("10.44.12.31", None)]
    [line] = conn.execute("SELECT detail FROM journal WHERE event = 'ip_changed'").fetchall()
    assert line["detail"] == f"{MAC} 10.44.12.31 → 10.44.12.77"


def test_ip_rows_per_machine_are_capped(conn, monkeypatch):
    monkeypatch.setattr(net_history, "KEEP_IPS", 2)
    for last in range(5):
        db.net_seen(conn, MAC, f"10.0.0.{last}")
    assert [r["ip"] for r in net_history.ip_history(conn, MAC)] == ["10.0.0.4", "10.0.0.3"]


def _probe(speed=1000, crc=0, dropped=7) -> dict:
    return probe.well_formed({"nic": [{"name": "eth0", "speed_mbps": speed,
                                       "stats": {"rx_crc_errors": crc, "rx_dropped": dropped}}]})


def test_nic_counters_are_kept_not_overwritten(conn, monkeypatch):
    assert probe.record(conn, MAC, _probe(crc=0)) is True
    probe.record(conn, MAC, _probe(crc=3))          # בתוך השעה, אותה מהירות, המונה עלה — לא נדגם
    assert len(net_history.nic_history(conn, MAC)) == 1
    probe.record(conn, MAC, _probe(speed=100, crc=3))     # המהירות ירדה — נדגם
    probe.record(conn, MAC, _probe(speed=100, crc=0))     # המונה ירד — אתחול, סדרה חדשה
    monkeypatch.setattr(net_history, "NIC_GAP_S", 0)
    probe.record(conn, MAC, _probe(speed=100, crc=9))     # עברה "שעה"
    got = [(r["speed_mbps"], r["rx_crc_errors"]) for r in net_history.nic_history(conn, MAC)]
    assert got == [(100, 9), (100, 0), (100, 3), (1000, 0)]


def test_an_unread_counter_is_null_not_zero(conn):
    probe.record(conn, MAC, probe.well_formed({"nic": [{"name": "eth0", "stats": None}]}))
    [row] = net_history.nic_history(conn, MAC)
    assert row["speed_mbps"] is None and row["rx_crc_errors"] is None


def test_wave_drops_come_from_the_existing_wave_history(conn):
    for i, drops in enumerate(({MAC: {"rcvbuf": 12, "inerrors": 0}}, {MAC: None}, {"x": {}})):
        conn.execute("INSERT INTO wave_rates (session_id, decision, at, drops) VALUES (?, '{}', ?, ?)",
                     (f"s{i}", db.now_iso(), json.dumps(drops)))
    conn.commit()
    assert [(r["session_id"], r["rcvbuf"]) for r in net_history.wave_drops(conn, MAC)] == \
        [("s1", None), ("s0", 12)]


def test_a_failure_never_breaks_the_probe(conn, monkeypatch):
    monkeypatch.setattr(net_history, "_settle", lambda c: 1 / 0)
    assert net_history.record_nic(conn, MAC, _probe()) == 0


def test_the_console_shows_it(server):
    from server.agent import registry
    ctx = server["ctx"]
    registry.add_machine(ctx.conn, MAC, "cloner-3", "grp_CLONERS", "test")
    db.net_seen(ctx.conn, MAC, "10.1.1.1")
    probe.record(ctx.conn, MAC, _probe())
    got = server["admin"].get(f"/api/console/machines/{MAC}/data").json()
    assert got["ip_history"][0]["ip"] == "10.1.1.1"
    assert got["nic_history"][0]["speed_mbps"] == 1000 and got["wave_drops"] == []
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "ip_history" in doc and "nic_history" in doc
