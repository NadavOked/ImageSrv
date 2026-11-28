"""A silent cloner gets the drawer's network wait before the server marks it lost."""

from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.rounds import room, sessions
from test_brain_silence_3657 import conn  # noqa: F401 -- fixture


@pytest.mark.parametrize(("net_wait", "silent_for", "expected"), [
    (300, 240, "writing"),
    (300, 330, "writing"),
    (300, 340, "lost"),
    (120, 180, "writing"),
    (120, 181, "lost"),
])
def test_cloner_loss_follows_network_wait(conn, monkeypatch, net_wait, silent_for, expected):
    last_progress = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    now = last_progress + timedelta(seconds=silent_for)
    monkeypatch.setattr(room, "_utcnow", lambda: now)
    db.set_setting(conn, "lost_wait_seconds", "180")
    db.set_setting(conn, "drawer_net_wait_seconds", str(net_wait))
    wave = {"id": "W-4044", "started_at": last_progress.isoformat()}
    mac = "aa:bb:cc:00:00:01"
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, updated_at) VALUES (?, ?, ?, ?)",
        (wave["id"], mac, "writing", last_progress.isoformat()),
    )
    conn.commit()
    store = sessions.SessionStore(conn)

    room._mark_lost(conn, store, wave, store.members(wave["id"]))

    assert store.members(wave["id"])[0]["state"] == expected
    events = conn.execute(
        "SELECT machine FROM journal WHERE round_id = ? AND event = 'client_lost'",
        (wave["id"],),
    ).fetchall()
    assert [event["machine"] for event in events] == ([mac] if expected == "lost" else [])
