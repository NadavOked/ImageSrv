import json
from datetime import datetime, timedelta, timezone

import pytest

from server.db import set_setting
import test_server_room
from test_server_room import CLONER1, CLONER2, cloner_hello, room_body

room_server = test_server_room.room_server     # the fixture, re-exported (an import would shadow it as F811)


def open_wave(server, targets, drives):
    opened = server["deploy"].post("/api/console/room", json=room_body(server, drives, targets))
    assert opened.status_code == 200, opened.text
    return opened.json()["wave_session_id"]


def decision(conn, wave):
    row = conn.execute("SELECT * FROM brain_decisions WHERE rule='SCHEDULED_CAST' AND entity_key=?",
                       (wave,)).fetchone()
    assert row is not None, "missing Scheduled-Cast start comparison"
    return row


@pytest.mark.parametrize("mode,enabled", [("shadow", "true"), ("enforce", "false")])
def test_single_cloner_starts_immediately_and_records_shadow(room_server, mode, enabled):
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", mode)
    set_setting(conn, "brain_scheduled_cast", enabled)
    wave = open_wave(room_server, {CLONER1: ["S1", "S2"]}, 2)
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    assert answer["session"]["state"] == "running"
    row = decision(conn, wave)
    assert (row["verdict"], row["mode"]) == ("WAIT", "shadow")
    started = conn.execute("SELECT started_at FROM sessions WHERE id=?", (wave,)).fetchone()[0]
    assert f"actual_start={started}" in json.loads(row["evidence_json"])


def test_enforced_hold_expires_from_open_despite_repeat_hello(room_server):
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", "enforce")
    set_setting(conn, "brain_scheduled_cast", "true")
    wave = open_wave(room_server, {CLONER1: ["S1", "S2"]}, 2)
    for _ in range(2):
        answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
        assert answer["session"]["state"] == "open"
    old = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
    conn.execute("UPDATE sessions SET created_at=? WHERE id=?", (old, wave))
    conn.commit()
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    assert answer["session"]["state"] == "running"
    assert decision(conn, wave)["verdict"] == "DEADLINE"


def test_second_member_releases_before_deadline(room_server):
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", "enforce")
    set_setting(conn, "brain_scheduled_cast", "true")
    wave = open_wave(room_server, {CLONER1: ["S1"], CLONER2: ["S2"]}, 2)
    assert cloner_hello(room_server["anon"], CLONER1, ["S1"])["session"]["state"] == "open"
    assert cloner_hello(room_server["anon"], CLONER2, ["S2"])["session"]["state"] == "running"
    assert decision(conn, wave)["verdict"] == "MINIMUM"


def backdate(conn, wave, seconds=31):
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    conn.execute("UPDATE sessions SET created_at=? WHERE id=?", (old, wave))
    conn.commit()


def legacy_round(conn, wave):
    """A round with no stored selection (roster None): every cloner of the group may join, as before #1410."""
    conn.execute("UPDATE room_rounds SET target_slots_json=NULL WHERE wave_session_id=?", (wave,))
    conn.execute("UPDATE sessions SET roster_json=NULL WHERE id=?", (wave,))
    conn.commit()


def test_new_mac_joining_at_the_deadline_pulse_joins_the_wave(room_server):
    """The pulse that crosses the deadline runs before the hello's own join is recorded: the joiner must not be dropped."""
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", "enforce")
    set_setting(conn, "brain_scheduled_cast", "true")
    wave = open_wave(room_server, {CLONER1: ["S1", "S2"]}, 2)
    legacy_round(conn, wave)
    assert cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]["state"] == "open"
    backdate(conn, wave)
    answer = cloner_hello(room_server["anon"], CLONER2, ["S3", "S4"])
    assert answer["session"] is not None and answer["session"]["state"] == "running"
    assert conn.execute("SELECT COUNT(*) FROM session_members WHERE session_id=?", (wave,)).fetchone()[0] == 2
    assert decision(conn, wave)["verdict"] == "BOTH"


def test_joiner_outside_the_roster_does_not_hold_the_deadline(room_server):
    """A hello that cannot join must not postpone the start: the wave goes out at the deadline."""
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", "enforce")
    set_setting(conn, "brain_scheduled_cast", "true")
    wave = open_wave(room_server, {CLONER1: ["S1", "S2"]}, 2)
    assert cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]["state"] == "open"
    backdate(conn, wave)
    cloner_hello(room_server["anon"], CLONER2, ["S3", "S4"])
    assert conn.execute("SELECT state FROM sessions WHERE id=?", (wave,)).fetchone()[0] == "running"
    assert decision(conn, wave)["verdict"] == "DEADLINE"


def test_switch_off_new_mac_at_the_deadline_is_unchanged(room_server):
    """Switch off: the wave starts as before this feature -- on the first pulse, with whoever joined."""
    conn = room_server["ctx"].conn
    set_setting(conn, "brain_veto_mode", "enforce")
    set_setting(conn, "brain_scheduled_cast", "false")
    wave = open_wave(room_server, {CLONER1: ["S1", "S2"]}, 2)
    legacy_round(conn, wave)
    backdate(conn, wave)
    cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    assert conn.execute("SELECT state FROM sessions WHERE id=?", (wave,)).fetchone()[0] == "running"
    assert not cloner_hello(room_server["anon"], CLONER2, ["S3", "S4"]).get("session")
