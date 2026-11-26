"""#2910 (server half) -- what the room does with the report of a cloner whose drawers were all skipped.

The agent half is test_all_skipped_reports_failed_2910.py: it must say `failed`. Here is why
that word matters to the room. A final `failed` with every drawer skipped red closes the wave
at once -- the round ends `written=0/2 skipped_red=2`. The `waiting` the agent used to send
is not terminal: the member stays in the wave until `_mark_lost` (180 s), and the wave closes
late, with a wrong reason. Both are pinned, so a change on either side shows here.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from test_red_skip_closes_drawer_1760 import _disk_event, _events, _wave1  # noqa: E402
from test_server_room import (  # noqa: E402,F401
    CLONER1, CLONER2, cloner_hello, heartbeat, report, room, room_body, room_server,
)


def _both_red(room_server) -> str:
    anon = room_server["anon"]
    wave, _opened = _wave1(room_server, red_hello=False)
    _disk_event(anon, wave, "sda", "S1", "fail", "readonly", "skip")
    _disk_event(anon, wave, "sdb", "S2", "fail", "readonly", "skip")
    return wave


def test_a_failed_report_with_every_drawer_skipped_closes_the_wave_at_once(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _both_red(room_server)
    report(anon, wave, CLONER1, {"sda": "skipped", "sdb": "skipped"}, top="failed")
    heartbeat(room_server)

    assert room(deploy)["round"] is None, room(deploy)["round"]
    row = ctx.conn.execute("SELECT * FROM room_rounds").fetchone()
    assert row["state"] == "closed" and row["written_drives"] == 0
    assert json.loads(row["skipped_serials"]) == ["S1", "S2"]
    assert _events(ctx, "room_done") == [f'{row["id"]} written=0/2 skipped_red=2']


def test_a_waiting_report_leaves_the_wave_open_for_the_lost_timer(room_server):
    """The symptom of #2910, from the room's side: `waiting` closes nothing."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _both_red(room_server)
    report(anon, wave, CLONER1, {"sda": "skipped", "sdb": "skipped"}, top="waiting")
    heartbeat(room_server)

    assert room(deploy)["round"]["wave_state"] == "running"
    assert ctx.conn.execute("SELECT state FROM room_rounds").fetchone()["state"] == "active"


@pytest.mark.parametrize("preflight", ["slow", "failed"])
def test_only_a_disk_failed_as_slow_closes_its_slot_without_a_retry(room_server, preflight):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert cloner_hello(anon, CLONER2, ["S3", "S4"])["session"]["id"] == wave
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"
    report(anon, wave, CLONER2, {"sda": "done", "sdb": "done"})
    error = "איטי — 19 MB/s, מתחת לסף 74 MB/s" if preflight == "slow" else "שגיאת כתיבה"
    response = anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "partial",
        "targets": [
            {"dev": "sda", "state": "done", "bytes_written": 100, "bytes_total": 100},
            {"dev": "sdb", "state": "failed", "bytes_written": 0, "bytes_total": 100,
             "error": error, "write_preflight": {"state": preflight, "reason": error,
                                                 "mbps": 19 if preflight == "slow" else None}},
        ],
    })
    assert response.status_code == 200 and response.json().get("ok"), response.text
    heartbeat(room_server)

    row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (opened.json()["id"],)).fetchone()
    assert row["written_drives"] == 3
    if preflight == "slow":
        assert _events(ctx, "room_wave") == []
        assert _events(ctx, "room_rewake") == []
        assert row["state"] == "closed" and row["wave_number"] == 1
        assert json.loads(row["skipped_serials"]) == ["S2"]
        assert _events(ctx, "room_done") == [f'{row["id"]} written=3/4 skipped_red=1']
    else:
        assert row["state"] == "active" and row["wave_number"] == 2
        assert json.loads(row["skipped_serials"]) == []
