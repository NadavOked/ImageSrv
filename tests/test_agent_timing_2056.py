"""#2056 + #2059 (mockup 05, Nadav 24/10) -- wait / power-off timings as console settings.

Three values that were constants: how long the round waits for a cloner that stopped
reporting (``room.LOST_SECONDS`` = 180, only ever larger, up to 30 min), the idle
power-off (300 s) and the end-of-task countdown (20 s) of the small GUI.  One setting
for every machine type.  A value the server stored but the agent did not read back is
NOT "applied" (principle 5): the agent echoes ``applied_timing`` in hello and the
console shows "applied on N of M".  The agent-side consumption is another change.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import hello_body
from test_room_precheck_432_65 import disk, drawers_hello
from test_room_vanished_member import (   # noqa: F401
    _age_started, _member_state, _open, _progress_writing)
from test_server_room import (            # noqa: F401 -- room_server is a fixture
    CLONER1, CLONER2, cloner_hello, heartbeat, report, room, room_server)

pytest.importorskip("fastapi")

URL = "/api/console/settings"
DEFAULTS = {"lost_wait_seconds": 180, "idle_poweroff_seconds": 300, "end_poweroff_seconds": 20}
KEYS = {"lost_wait_seconds": "lost_wait_seconds",
        "gui_idle_poweroff_seconds": "idle_poweroff_seconds",
        "gui_end_poweroff_seconds": "end_poweroff_seconds"}


@pytest.mark.parametrize("key, raw, want", [
    ("lost_wait_seconds", "180", 180), ("lost_wait_seconds", 1800, 1800),
    ("lost_wait_seconds", "179", None),            # only ever larger than today
    ("lost_wait_seconds", 1801, None), ("lost_wait_seconds", "0180", None),
    ("lost_wait_seconds", True, None), ("lost_wait_seconds", 5.5, None),
    ("lost_wait_seconds", "", None), ("lost_wait_seconds", None, None),
    ("gui_idle_poweroff_seconds", 60, 60), ("gui_idle_poweroff_seconds", "3600", 3600),
    ("gui_idle_poweroff_seconds", 59, None), ("gui_idle_poweroff_seconds", 3601, None),
    ("gui_end_poweroff_seconds", 10, 10), ("gui_end_poweroff_seconds", "300", 300),
    ("gui_end_poweroff_seconds", 9, None), ("gui_end_poweroff_seconds", 301, None),
])
def test_parse_takes_a_whole_number_in_range_only(key, raw, want):
    from server.agent import agent_timing   # inside the test: the negative control removes the module
    assert agent_timing.parse(key, raw) == want


def test_the_defaults_are_todays_values_and_are_served_when_nothing_is_set(room_server):
    admin = room_server["admin"]
    got = admin.get(URL).json()
    assert [got[k] for k in KEYS] == ["180", "300", "20"]       # in force, not "unset"
    answer = drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])
    assert {v: answer[v] for v in KEYS.values()} == DEFAULTS


def test_a_saved_value_reaches_hello_for_every_registered_role(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.post(URL, json={"lost_wait_seconds": 600, "gui_idle_poweroff_seconds": "60",
                                 "gui_end_poweroff_seconds": 45}).status_code == 200
    assert admin.get(URL).json()["lost_wait_seconds"] == "600"
    got = drawers_hello(anon, CLONER2, [disk("S3", 1)])
    assert (got["lost_wait_seconds"], got["idle_poweroff_seconds"], got["end_poweroff_seconds"]) == (600, 60, 45)
    # a build machine gets them too (one setting for every machine type)
    assert admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:00:00:99", "name": "build-1", "group_id": "grp_BUILD"}).status_code == 200
    built = anon.post("/api/v1/agent/hello", json=hello_body("aa:bb:cc:00:00:99")).json()
    assert built["role"] == "build" and built["idle_poweroff_seconds"] == 60
    # an unknown MAC is an unknown machine: it boots the local disk, no timings (principle 1)
    stranger = anon.post("/api/v1/agent/hello", json=hello_body("de:ad:be:ef:00:01")).json()
    assert stranger["known"] is False and "lost_wait_seconds" not in stranger


@pytest.mark.parametrize("bad", [{"lost_wait_seconds": 179}, {"lost_wait_seconds": 1801},
                                 {"gui_idle_poweroff_seconds": 59}, {"gui_end_poweroff_seconds": 301},
                                 {"gui_end_poweroff_seconds": True}, {"lost_wait_seconds": "abc"}])
def test_a_bad_value_is_refused_before_anything_is_written(room_server, bad):
    admin, ctx = room_server["admin"], room_server["ctx"]
    r = admin.post(URL, json={"session_wait_seconds": "77", **bad})
    assert r.status_code == 400 and "מספר שלם של שניות" in r.text, r.text
    stored = {k: v for k, v in ctx.conn.execute("SELECT key, value FROM settings")}
    assert stored.get("session_wait_seconds") != "77" and not set(bad) & set(stored)


def test_only_an_admin_sets_them_and_the_change_is_journaled(room_server):
    admin, deploy, ctx = room_server["admin"], room_server["deploy"], room_server["ctx"]
    assert deploy.post(URL, json={"lost_wait_seconds": 300}).status_code == 403
    assert admin.post(URL, json={"lost_wait_seconds": 300}).status_code == 200
    detail = [r["detail"] for r in ctx.conn.execute("SELECT detail FROM journal WHERE event = 'setting_change'")]
    assert "lost_wait_seconds=300" in detail


def test_a_corrupt_stored_value_falls_back_to_the_default_not_to_zero_or_forever(room_server):
    from server.agent import agent_timing
    ctx = room_server["ctx"]
    ctx.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('lost_wait_seconds', '5')")
    ctx.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('gui_end_poweroff_seconds', 'x')")
    ctx.conn.commit()
    assert agent_timing.lost_seconds(ctx.conn) == 180 and agent_timing.seconds(ctx.conn, "gui_end_poweroff_seconds") == 20


# --- the round honours it ------------------------------------------------------------------


def _vanish(room_server, silent_seconds: int):
    """Two cloners, one finishes, the other writes once and goes silent for ``silent_seconds``."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _open(room_server, target=4)
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})
    _progress_writing(anon, wave, CLONER2, ["sda", "sdb"])
    old = _age_started(ctx, wave, silent_seconds)
    ctx.conn.execute("UPDATE session_members SET updated_at = ? WHERE session_id = ? AND mac = ?",
                     (old, wave, CLONER2))
    ctx.conn.commit()
    heartbeat(room_server)
    return wave, _member_state(ctx, wave, CLONER2)[0], deploy


def test_a_longer_wait_keeps_a_silent_cloner_in_the_wave(room_server):
    """600 s configured: 4 min of silence is NOT lost (the old constant would say lost)."""
    assert room_server["admin"].post(URL, json={"lost_wait_seconds": 600}).status_code == 200
    _wave, state, _deploy = _vanish(room_server, 240)
    assert state != "lost", "the configured wait was ignored -- the 180 s constant won"


def test_a_longer_wait_still_ends_in_a_visible_failure(room_server):
    """...and past the configured wait it is lost, visibly (not forever)."""
    assert room_server["admin"].post(URL, json={"lost_wait_seconds": 600}).status_code == 200
    _wave, state, _deploy = _vanish(room_server, 660)
    assert state == "lost"


def test_the_default_wait_is_still_180(room_server):
    _wave, state, _deploy = _vanish(room_server, 240)
    assert state == "lost"


# --- read back: applied on N of M ----------------------------------------------------------


def _applied(admin) -> dict:
    return admin.get(URL).json()["agent_timing_applied"]


def _hello_applied(anon, mac, applied):
    body = hello_body(mac)
    if applied is not None:
        body["applied_timing"] = applied
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200


def test_applied_counts_only_machines_that_read_the_value_back(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.post(URL, json={"lost_wait_seconds": 600}).status_code == 200
    _hello_applied(anon, CLONER1, None)                        # old agent: no echo
    _hello_applied(anon, CLONER2, None)
    got = _applied(admin)["lost_wait_seconds"]
    assert got == {"applied": 0, "connected": 2}, "stored on the server is not applied (principle 5)"
    _hello_applied(anon, CLONER1, {"lost_wait_seconds": 600, "idle_poweroff_seconds": 300,
                                   "end_poweroff_seconds": 20})
    _hello_applied(anon, CLONER2, {"lost_wait_seconds": 180})  # echoes the OLD value
    assert _applied(admin)["lost_wait_seconds"] == {"applied": 1, "connected": 2}
    assert _applied(admin)["gui_idle_poweroff_seconds"] == {"applied": 1, "connected": 2}   # CLONER2 sent no idle value
    # an echo in range for one key does not count for the others
    assert _applied(admin)["gui_end_poweroff_seconds"]["applied"] == 1


def test_a_machine_not_seen_lately_is_not_connected(room_server):
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    _hello_applied(anon, CLONER1, DEFAULTS)
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ?", (old, CLONER1))
    ctx.conn.commit()
    assert _applied(admin)["lost_wait_seconds"] == {"applied": 0, "connected": 0}


def test_a_garbage_echo_is_ignored_and_keeps_the_previous_record(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    _hello_applied(anon, CLONER1, DEFAULTS)
    _hello_applied(anon, CLONER1, "banana")
    _hello_applied(anon, CLONER1, {"lost_wait_seconds": 99999, "idle_poweroff_seconds": True})
    row = room_server["ctx"].conn.execute("SELECT applied_timing FROM net_devices WHERE mac = ?", (CLONER1,)).fetchone()
    assert json.loads(row["applied_timing"]) == {}     # the third hello: nothing valid in it, an empty record
    assert _applied(admin)["lost_wait_seconds"]["applied"] == 0


# --- udp-sender follows the wait ------------------------------------------------------------


def test_the_senders_drop_ceiling_never_undercuts_the_configured_wait():
    from server.agent import agent_timing
    from server.rounds.sender import DEFAULT_RETRIES_UNTIL_DROP, SenderEngine
    assert agent_timing.retries_until_drop(200, 180) == 200          # default: unchanged
    assert agent_timing.retries_until_drop(200, 1800) == 1895        # 30 min: scaled 200 per 190 s
    engine = SenderEngine(library=None, lost_wait=lambda: 600)
    assert engine.effective_retries_until_drop() == 632 > DEFAULT_RETRIES_UNTIL_DROP
    assert SenderEngine(library=None).effective_retries_until_drop() == DEFAULT_RETRIES_UNTIL_DROP


def test_every_setting_has_a_journal_label():
    from server.console.journal_he import SETTINGS_HE
    assert set(KEYS) <= set(SETTINGS_HE)
