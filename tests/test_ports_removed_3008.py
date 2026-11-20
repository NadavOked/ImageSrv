"""#3008 — a pulled disk (`cause='removed'`) is not a cable failure in the learned port history.

`ports.record_wave` writes `cable_fail` from `disk_failures WHERE cause='cable'`; `ports.verdict` calls a connection "cable/connector,
not the disk" once >= 2 different disks were problematic on it. A manual pull counted as one of the two would paint the healthy disk
that sits there next red. Only a real `cable` raises the drawer's `cable_fail`. (The classification itself: `ata_cause`, see
test_ata_cause_pull_3008.py.)
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.learn import ports as learn_ports

MAC = "78:ac:c0:9b:11:c2"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _wave(conn, cause):
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)",
                 (MAC, json.dumps([{"dev": "sda", "serial": "S1", "port": 1}])))
    conn.execute("INSERT INTO disk_failures (session_id, mac, dev, serial, port, at, cause)"
                 " VALUES ('ses_w', ?, 'sda', 'S1', 1, 'x', ?)", (MAC, cause))
    conn.commit()
    member = {"mac": MAC, "targets_json": json.dumps([{"dev": "sda", "state": "failed", "serial": "S1"}])}
    assert learn_ports.record_wave(conn, "ses_w", [member]) == 1
    return conn.execute("SELECT cable_fail FROM port_history WHERE port = 1").fetchone()["cable_fail"]


def test_a_pulled_disk_does_not_raise_the_drawers_cable_fail(conn):
    assert _wave(conn, "removed") == 0


def test_a_cable_failure_raises_the_drawers_cable_fail(conn):
    assert _wave(conn, "cable") == 1
