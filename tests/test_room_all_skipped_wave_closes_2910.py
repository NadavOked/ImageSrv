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
from test_server_room import CLONER1, heartbeat, report, room, room_server  # noqa: E402,F401


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
