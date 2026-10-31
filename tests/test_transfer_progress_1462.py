"""‏#1462: ‏``seconds_since_update`` בשורות ``/storage-transfers``.

הקונסולה מציגה "זמן שעבר" בשלב האימות וקצב/ETA בשלב השליחה. הזמן שעבר
נמדד **בשעון השרת** (עכשיו פחות ``updated_at``) — לא שעון הדפדפן פחות חותמת
של השרת, ששונים זה מזה במחשב שהשעון שלו זז. חותמת שלא נקראה = ‏``None``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server import db
from server.interserver import storage_transfer

ADMIN = ("noc", "admin")


def _conn(tmp_path):
    conn = db.connect(tmp_path / "p.db")
    conn.execute(
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
        " VALUES ('n1', 'חיפה', 'https://10.0.0.2:8443', 'x', '2026-10-16T00:00:00+00:00')")
    return conn


def _row(conn, tid, state, updated_at):
    conn.execute(
        "INSERT INTO storage_transfers (id, node_id, image_id, image_name, state,"
        " bytes_sent, bytes_total, started_by, created_at, updated_at)"
        " VALUES (?, 'n1', ?, 'x', ?, 0, 100, 'noc', ?, ?)",
        (tid, f"img_{tid}", state, updated_at, updated_at))
    conn.commit()


def test_seconds_since_update_is_server_clock_and_unknown_is_none(tmp_path):
    conn = _conn(tmp_path)
    ninety = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="seconds")
    _row(conn, "a00001", "verifying", ninety)
    _row(conn, "a00002", "sending", "not-a-time")
    _row(conn, "a00003", "sending", "2026-10-16T10:00:00")          # בלי אזור זמן
    rows = {r["id"]: r for r in storage_transfer.list_transfers(conn, ADMIN)}
    assert 89 <= rows["a00001"]["seconds_since_update"] <= 95
    assert rows["a00002"]["seconds_since_update"] is None
    assert rows["a00003"]["seconds_since_update"] is None
