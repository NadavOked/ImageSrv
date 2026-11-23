"""#3940 — LR24 keeps the link speed the wave reported at the start.

On metal (20/11) three of four waves stored ``link_mbps=unmeasured`` while the
same wave's ``wave_rate`` line said ``1000Mb/s reported``. ``nic_speed.view``
at the end of the wave only accepts a live hello (gone after 120 s, and a
cloner does not hello during a wave) or a probe younger than 900 s. A wave
longer than 15 minutes, and a shorter one whose probe was already old or from
the previous boot, both end as ``assumed``. An assumption is not a measurement
and must not be stored. The speed the wave itself used, when that source was
``reported`` or ``manual``, is.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.agent import probe, reports
from server.brain import memory, veto
from server.rounds import drive_rates, nic_live, nic_speed

SLOW = "78:ac:c0:9b:11:c2"
FAST = "00:0e:0c:6c:26:0a"
BOOT = "3940aaaa-0000-4000-8000-000000000001"
BOOT_NEXT = "3940bbbb-0000-4000-8000-000000000002"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    nic_live.forget()
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    nic_live.forget()
    veto.forget()


def _plant(conn, mac: str, mbps: int) -> None:
    probe.record(conn, mac, probe.well_formed(
        {"nic": [{"name": "eth0", "speed_mbps": mbps, "ip_conflict": None}]}))
    nic_live.note(mac, {"iface": "eth0", "speed_mbps": mbps}, BOOT)


def _drive(mac: str, serial: str) -> dict:
    return {"serial": serial, "model": "disk", "mac": mac, "port": 1}


def _age_past_the_wave(conn) -> None:
    """What the end of a wave longer than 15 minutes looks like: no hello, probe older than 900 s."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=37)).isoformat(timespec="seconds")
    conn.execute("UPDATE machine_probe SET sampled_at = ?", (old,))
    conn.commit()
    nic_live.forget()


def _learn(conn, session: str, mac: str, decision: dict) -> dict:
    conn.execute("UPDATE sessions SET state = 'closed' WHERE id != ? AND state IN ('open', 'running')", (session,))
    conn.execute("INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at, started_at) VALUES (?, 'grp_CLONERS', 'img', 'p', 1,"
                 " 300, 'running', 't', ?, 0, ?)", (session, db.now_iso(), db.now_iso()))
    conn.execute("INSERT OR IGNORE INTO session_members (session_id, mac, state, updated_at) VALUES (?, ?, 'writing', ?)",
                 (session, mac, db.now_iso()))
    conn.execute("INSERT OR REPLACE INTO wave_rates (session_id, decision, stream, at) VALUES (?, ?, ?, ?)",
                 (session, json.dumps(decision), json.dumps({"chunk": {"asked_bps": 600_000_000, "achieved_bps": 600_000_000}}),
                  db.now_iso()))
    conn.commit()
    body = {"session_id": session, "mac": mac, "state": "done", "targets": [],
            "wave_senses": {"window_s": 5, "windows": 3,
                            "cpu": [{"core": 0, "busy": 40, "busy_max": 40, "irq": 0, "softirq": 0,
                                     "softirq_max": 0, "net_rx_ps": 0, "net_rx_ps_max": 0}],
                            "nic": {"iface": "eth0", "ethtool": True, "rx_missed": 0, "rx_no_buffer": 0,
                                    "ring_rx": 256, "ring_rx_max": 4096},
                            "disks": {"sda": {"util": 30, "util_max": 30, "inflight": 1, "inflight_max": 2,
                                              "w_ms": 1.0, "w_ms_max": 2.0}}},
            "senses": {"rx": {"passes": 1, "read": 1, "pkts": 100, "foreign": 0, "stray": 0, "bad_pkt": 0, "dup": 0,
                              "evicted": 0, "busy": 0, "bad_chunk": 0, "good": 80, "skipped": 0,
                              "conflict": 0, "version": 0}},
            "udp_drops": {"rcvbuf": 9, "inerrors": 0, "abandoned": 0}}
    assert reports.ingest(conn, body)["ok"]
    rows = [d for d in memory.decisions(conn, kind="machine", key=mac, rule="LR24", limit=20, round_id=session)
            if d["action"] == "MACHINE_CAPABILITY"]
    assert len(rows) == 1, rows
    return rows[0]


def _saved(conn, mac: str):
    return (memory.summary(conn, "machine", mac).get("capability") or {}).get("num", {})


def test_a_long_wave_keeps_the_link_speed_it_reported_at_the_start(conn):
    """37 minutes, one cloner. The speed was reported when the wave opened; at the end the probe is stale."""
    _plant(conn, SLOW, 1000)
    chosen = drive_rates.choose(conn, [_drive(SLOW, "S1")], None)
    assert chosen["cloner"]["source"] == "reported" and chosen["cloner"]["speed_mbps"] == 1000

    _age_past_the_wave(conn)
    assert nic_speed.view(conn, SLOW)["why"] == "stale"

    row = _learn(conn, "ses_long", SLOW, chosen)
    assert "link_mbps=1000" in row["evidence"], row["evidence"]
    assert "link_mbps=unmeasured" not in row["evidence"]
    assert _saved(conn, SLOW)["link_mbps"]["last"] == 1000


def test_a_second_cloner_keeps_its_own_reported_speed(conn):
    """The wave_rate line names only the slowest mac. The other mac's reported speed is kept too."""
    _plant(conn, SLOW, 1000)
    _plant(conn, FAST, 10000)
    chosen = drive_rates.choose(conn, [_drive(SLOW, "S1"), _drive(FAST, "S2")], None)
    assert chosen["cloner"]["mac"] == SLOW

    _age_past_the_wave(conn)
    row = _learn(conn, "ses_two", FAST, chosen)
    assert "link_mbps=10000" in row["evidence"], row["evidence"]
    assert _saved(conn, FAST)["link_mbps"]["last"] == 10000


def test_a_probe_from_the_previous_boot_still_keeps_the_speed_the_wave_started_with(conn):
    """A 7-minute wave can end ``older_boot``: the start-of-wave 'reported' was the live hello, and the probe is the previous boot."""
    _plant(conn, SLOW, 1000)
    chosen = drive_rates.choose(conn, [_drive(SLOW, "S1")], None)
    assert chosen["cloner"]["source"] == "reported"

    nic_live.note(SLOW, None, BOOT, now=datetime.now(timezone.utc).timestamp() - 30)
    nic_live.note(SLOW, None, BOOT_NEXT, now=datetime.now(timezone.utc).timestamp() + 5)
    assert nic_speed.view(conn, SLOW)["why"] == "older_boot"

    row = _learn(conn, "ses_boot", SLOW, chosen)
    assert "link_mbps=1000" in row["evidence"], row["evidence"]
    assert _saved(conn, SLOW)["link_mbps"]["last"] == 1000


def test_an_assumed_speed_in_the_wave_decision_is_not_stored(conn):
    """Guard: 1000 from an assumption is not a measurement, at the start or at the end."""
    chosen = drive_rates.choose(conn, [_drive(SLOW, "S1")], None)
    assert chosen["cloner"]["source"] == "assumed" and chosen["cloner"]["speed_mbps"] == 1000
    row = _learn(conn, "ses_assumed", SLOW, chosen)
    assert "link_mbps=unmeasured" in row["evidence"], row["evidence"]
    assert "link_mbps" not in _saved(conn, SLOW)


def test_a_manual_speed_at_the_end_of_a_long_wave_still_wins(conn):
    """Guard: a speed the console set is what gets stored, not the snapshot from the opening."""
    _plant(conn, SLOW, 1000)
    chosen = drive_rates.choose(conn, [_drive(SLOW, "S1")], None)
    _age_past_the_wave(conn)
    nic_speed.set_manual(conn, SLOW, 2500)
    row = _learn(conn, "ses_manual", SLOW, chosen)
    assert "link_mbps=2500" in row["evidence"], row["evidence"]
    assert _saved(conn, SLOW)["link_mbps"]["last"] == 2500


def test_a_link_that_cannot_be_read_at_the_end_is_not_filled_in_from_the_start(conn):
    """Guard: only an *old* report (stale / older_boot) falls back to the wave's own. A link now reported as unknown stays unmeasured."""
    _plant(conn, SLOW, 1000)
    chosen = drive_rates.choose(conn, [_drive(SLOW, "S1")], None)
    assert chosen["cloner"]["source"] == "reported"
    nic_live.forget()
    probe.record(conn, SLOW, probe.well_formed({"nic": [{"name": "eth0", "speed_mbps": None, "ip_conflict": None}]}))
    assert nic_speed.view(conn, SLOW)["why"] == "unknown"
    row = _learn(conn, "ses_unknown", SLOW, chosen)
    assert "link_mbps=unmeasured" in row["evidence"], row["evidence"]
    assert "link_mbps" not in _saved(conn, SLOW)
