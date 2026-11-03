"""#2201 (the server's half) -- a selected drawer with no disk is closed for the round as failed.

Metal 25/10, cloner 2, room_edc35340: drawer 5 left the kernel one second after
the round opened; drawer 2 was ready. Since #2312 the cloner writes the drawer that
is there and only logs the one that is not ("selected drawer 5 has no disk on this
machine"); its report carries a target for every drawer it wrote, none for the
missing one.

Decision 03/11 (the coordinator, on Nadav's authority): the missing slot is closed
for the round like a red drawer (#1760) or a cancelled one (#1936) -- no wave 2 that
waits for it; the round ends `room_done written=1/2` and the missing drawer is
counted as failed, by name -- not as "skipped". The evidence is the cloner's own
report: a port this machine was sent in `target_ports` with no target in a report
that does carry targets. A report the server cannot place (no targets at all, or a
target whose port it cannot tell) decides nothing (principle 5).

Negative control (#439): revert server/rounds/room.py, server/console/journal_he.py,
server/static/console.js, server/static/index.html and server/static/monitor.html
together (the console's half is tests/cloners_builders_page.test.cjs).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from test_cancel_drawer_1386_server import _progress
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1, cloner_hello, heartbeat, room, room_body, room_server,
)


def _events(ctx, name: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (name,))]


def _round(ctx, round_id: str):
    return ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (round_id,)).fetchone()


def _slots(row) -> list[dict]:
    return json.loads(row["target_slots_json"])


def _open(room_server, target: int, serials: list[str]) -> str:
    opened = room_server["deploy"].post(
        "/api/console/room", json=room_body(room_server, target, {CLONER1: serials}))
    assert opened.status_code == 200, opened.text
    return opened.json()["id"]


def _start(room_server) -> None:
    if room(room_server["deploy"])["round"]["wave_state"] == "open":
        assert room_server["deploy"].post("/api/console/room/start").status_code == 200


def test_edc35340_drawer_gone_after_the_hello_closes_the_round_1_of_2(room_server):
    """The metal case: both drawers in the hello, drawer 2 gone before the stream.
    The report has sda done and nothing for sdb -> room_done written=1/2 no_disk=1,
    no wave 2, no WoL, and the slot is named in the round."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    round_id = _open(room_server, 2, ["S1", "S2"])
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    _start(room_server)
    woken = len(room_server["woken"])
    _progress(anon, wave, CLONER1, {"sda": "done"}, top="done")
    heartbeat(room_server)

    row = _round(ctx, round_id)
    assert (row["state"], row["written_drives"], row["failed_reason"]) == ("closed", 1, None)
    assert _events(ctx, "room_done") == [f"{round_id} written=1/2 no_disk=1"]
    assert _events(ctx, "room_wave") == [] and _events(ctx, "room_failed") == []
    assert len(room_server["woken"]) == woken, "the cloner was woken for a wave 2"
    item = _slots(row)[0]
    assert (item["ports"], item["no_disk"]) == ([1], [2]), item
    assert _events(ctx, "room_drawer_no_disk") == [f"{round_id} shich-1 port=2"]
    # not a red memory: the disk itself did nothing wrong (#874)
    assert ctx.conn.execute("SELECT COUNT(*) FROM disk_failures").fetchone()[0] == 0


def test_drawer_gone_before_the_hello_is_the_same(room_server):
    """The cloner was awake when the round opened (so no #1954 `expect` binding -- a
    sleeping one's empty slot is #1954's `wake_skipped`, not this); the drawer was
    already out when it said hello for the wave (its inventory has drawer 1 only). The
    server still sends both ports, and the same report closes the round 1/2."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"] is None     # awake, no round yet
    round_id = _open(room_server, 2, ["S1", "S2"])
    answer = cloner_hello(anon, CLONER1, ["S1"])["session"]
    assert answer["target_ports"] == [1, 2], answer     # sent both: the server cannot know yet
    wave = answer["id"]
    _start(room_server)
    _progress(anon, wave, CLONER1, {"sda": "done"}, top="done")
    heartbeat(room_server)

    assert _round(ctx, round_id)["state"] == "closed"
    assert _events(ctx, "room_done") == [f"{round_id} written=1/2 no_disk=1"]


def test_get_room_names_the_slot_and_it_is_not_remaining(room_server):
    """Target 3, three drawers: 1 written, 2 missing, 3 failed. The round goes on for
    drawer 3 only -- remaining 1, not 2; wave 2 is sent [3], not [2, 3]; the open
    wave does not wait for drawer 2 ("אין דיסק חדש במגירה 2" is not said again)."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    round_id = _open(room_server, 3, ["S1", "S2", "S3"])
    wave = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]
    _start(room_server)
    _progress(anon, wave, CLONER1, {"sda": "done", "sdc": "failed"}, top="partial")
    heartbeat(room_server)

    view = room(deploy)["round"]
    assert (view["written_drives"], view["remaining_drives"], view["wave_number"]) == (1, 1, 2), view
    assert view["target_slots"][0]["no_disk"] == [2], view["target_slots"]
    assert view["target_slots"][0]["ports"] == [1, 3], view["target_slots"]
    assert _events(ctx, "room_failed") == []
    session = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]
    assert session is not None and session["target_ports"] == [3], session
    for m in room(deploy)["round"]["missing"]:
        assert 2 not in m["ports"], m
    # the tally is idempotent: a recount of the same wave (the _resume path) adds nothing
    assert _round(ctx, round_id)["target_drives"] == 3
    assert _events(ctx, "room_drawer_no_disk") == [f"{round_id} shich-1 port=2"]


def test_a_report_without_targets_decides_nothing(room_server):
    """The guard (principle 5): a cloner that never reported a target -- an old agent
    that died on the missing drawer (die_local), or one lost before its first report --
    is no evidence that any drawer is missing. The wave is a dead wave, as before."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    round_id = _open(room_server, 2, ["S1", "S2"])
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    _start(room_server)
    r = anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "failed", "targets": []})
    assert r.status_code == 200, r.text
    heartbeat(room_server)

    row = _round(ctx, round_id)
    assert row["state"] == "failed"
    assert "no_disk" not in _slots(row)[0]
    assert _events(ctx, "room_drawer_no_disk") == []


def test_a_target_the_server_cannot_place_decides_nothing(room_server):
    """A target whose dev is not in the hello (a disk that appeared later, renamed)
    cannot be placed on a port -- the server does not know which slot it wrote, and
    so does not know which one is missing. Nothing is closed: wave 2, as before."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    round_id = _open(room_server, 2, ["S1", "S2"])
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    _start(room_server)
    _progress(anon, wave, CLONER1, {"sda": "done", "sdz": "failed"}, top="partial")
    heartbeat(room_server)

    row = _round(ctx, round_id)
    assert "no_disk" not in _slots(row)[0]
    assert room(deploy)["round"]["remaining_drives"] == 1
    assert _events(ctx, "room_drawer_no_disk") == []


def test_every_selected_drawer_reported_closes_nothing(room_server):
    """The ordinary wave: both drawers reported, one failed -- the failed one stays
    fresh for wave 2 (#1760/#1936 unchanged), no slot is closed as missing."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    round_id = _open(room_server, 2, ["S1", "S2"])
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    _start(room_server)
    _progress(anon, wave, CLONER1, {"sda": "done", "sdb": "failed"}, top="partial")
    heartbeat(room_server)

    row = _round(ctx, round_id)
    assert row["state"] == "active" and row["wave_number"] == 2
    assert "no_disk" not in _slots(row)[0]
    session = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert session is not None and session["target_ports"] == [2], session
