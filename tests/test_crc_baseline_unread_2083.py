"""#2083 — a red SMART record whose counters were never read is no CRC baseline.

``fail health_unread`` (#2078) and ``fail readonly`` (#1742) store ``crc=0``
that nobody read. The server took every ``verdict='fail'`` as read
(``room_precheck.crc_baseline``, ``housekeeping``'s keep-rule), so the next
round compared the live counter to that 0: a fake "CRC rose by N" (principle
5). The baseline now comes only from a record whose counters were read --
the agent's ``smart_was_read`` (smart.sh): ``fail`` with ``health_failed`` or
``media_readonly``, never ``health_unread`` / ``readonly``.

**Negative control** (in the commit): with the base's ``room_precheck.py`` and
``housekeeping.py`` the unread-record tests fail (baseline 0, rise 42; the
real baseline row purged).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server.system import housekeeping
from server.rounds import room_precheck
from server.db import connect

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
EVENT = ("INSERT INTO disk_events (session_id, mac, disk, serial, verdict, reason, crc, updated_at)"
         " VALUES (?, 'cc:00:00:00:00:01', 'sda', ?, ?, ?, ?, ?)")


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


@pytest.fixture()
def conn(tmp_path: Path):
    return connect(tmp_path / "imagesrv.db")


@pytest.mark.parametrize("reason", ["health_unread", "readonly"])
def test_an_unread_red_record_is_not_the_baseline(conn, reason):
    conn.execute(EVENT, ("s1", "S1", "ok", "ok", 40, ago(3)))
    conn.execute(EVENT, ("s2", "S1", "fail", reason, 0, ago(1)))       # newer, counters unread
    conn.commit()
    assert room_precheck.crc_baseline(conn, "S1") == 40
    fields = room_precheck.drawer_fields(conn, {"serial": "S1", "crc": 42})
    assert fields["crc_rise"] == 2, fields                             # not 42 against an unread 0


def test_only_unread_records_mean_no_baseline_not_zero(conn):
    conn.execute(EVENT, ("s1", "S1", "fail", "health_unread", 0, ago(1)))
    conn.commit()
    assert room_precheck.crc_baseline(conn, "S1") is None
    assert room_precheck.drawer_fields(conn, {"serial": "S1", "crc": 42})["crc_rise"] is None


@pytest.mark.parametrize("reason", ["health_failed", "media_readonly"])
def test_a_red_record_that_was_read_is_still_the_baseline(conn, reason):
    """The other side: a disk that reported FAILED was read -- its counter counts."""
    conn.execute(EVENT, ("s1", "S1", "ok", "ok", 40, ago(3)))
    conn.execute(EVENT, ("s2", "S1", "fail", reason, 55, ago(1)))
    conn.commit()
    assert room_precheck.crc_baseline(conn, "S1") == 55


def test_retention_keeps_the_read_baseline_not_the_unread_red_record(conn):
    """Both rows are past the 90 days; the one kept is the one crc_baseline reads."""
    conn.execute(EVENT, ("s1", "S1", "ok", "ok", 5, ago(120)))
    conn.execute(EVENT, ("s2", "S1", "fail", "health_unread", 0, ago(100)))
    conn.commit()
    assert room_precheck.crc_baseline(conn, "S1") == 5
    result = housekeeping.purge_tables(conn, NOW)
    assert result["failed"] == {}, result
    assert room_precheck.crc_baseline(conn, "S1") == 5
    rows = [(r["verdict"], r["reason"]) for r in conn.execute(
        "SELECT verdict, reason FROM disk_events WHERE serial = 'S1'")]
    assert rows == [("ok", "ok")], rows
