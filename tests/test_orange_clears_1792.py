"""#1792 (metal D2/D5, rc7nl, cloner 2, 19/10) -- orange closes on a verified write.

D5c: the disk that failed once (dm injection, wave 1) and was then written in full
(wave 2, `room_done 2/2`) stayed orange ("נכשל בשיכפול הקודם"), and the cloner stopped
on the question in every round until a manual "נקה". A write the round counts
(`_tally`: a `done` target bound to that serial) clears its open `disk_failures` row,
journaled "נוקה אוטומטית: נכתב בהצלחה בסבב X". **Red does not clear itself** (#1742,
#1580): a `readonly` row -- as read today -- stays until the operator's "נקה".

Negative control: server/room.py and server/disk_failures.py back to the base, both
(#439) -- see the PR.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server import disk_failures
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1, cloner_hello, heartbeat, report, room as room_view, room_body, room_server,
)
from test_wave_retry_1790 import _journal, _wave1_fails_drawer_2


# --- #1792: orange closes on a verified write ---------------------------------------


def _orange(anon, serials: list[str]) -> list[dict]:
    return cloner_hello(anon, CLONER1, serials, joining=False)["disk_failures"]


def test_a_disk_that_failed_and_was_then_written_is_no_longer_orange(room_server):
    """D2→D5c: fail in wave 1 → orange; written in wave 2 → the next round's hello says
    nothing about it, so the cloner asks nothing. Journaled with the round."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    round_id, _ = _wave1_fails_drawer_2(room_server, ata_log=True)
    assert [f["serial"] for f in _orange(anon, ["S1", "S2"])] == ["S2"], "wave 1 made it orange"
    failure_id = ctx.conn.execute("SELECT id FROM disk_failures WHERE serial = 'S2'").fetchone()["id"]

    second = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert second["target_ports"] == [2]
    heartbeat(room_server)
    report(anon, second["id"], CLONER1, {"sdb": "done"})
    heartbeat(room_server)
    assert room_view(deploy)["round"] is None

    assert _orange(anon, ["S1", "S2"]) == []
    assert disk_failures.list_open(ctx.conn) == []
    assert _journal(ctx, "disk_failure_cleared") == [
        f"#{failure_id} serial=S2 נוקה אוטומטית: נכתב בהצלחה בסבב {round_id}"]

    # the next round: S2 joins as a plain green drawer -- no failed_last memory in the hello
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert answer["session"]["target_ports"] == [1, 2] and answer["disk_failures"] == []


def test_a_failure_that_was_not_written_again_stays_orange(room_server):
    """Only the write closes it: wave 2 fails S2 again -- the row stays, nothing is cleared."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    _wave1_fails_drawer_2(room_server, ata_log=True)
    second = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    heartbeat(room_server)
    report(anon, second["id"], CLONER1, {"sdb": "failed"}, top="failed")
    heartbeat(room_server)
    assert [f["serial"] for f in _orange(anon, ["S1", "S2"])] == ["S2"]
    assert _journal(ctx, "disk_failure_cleared") == []


def test_red_is_not_cleared_by_a_write(room_server):
    """#1742/#1580: a read-only row -- recorded so (`record_readonly`) or an
    `unclassified` row whose own evidence is EPERM -- stays until the operator's
    "נקה". The orange row of the same disk closes."""
    ctx = room_server["ctx"]
    disk_failures.record(ctx.conn, "ses_a", CLONER1, {
        "dev": "sdb", "serial": "S2", "port": 2, "ata_log": [], "error": "I/O error"})
    disk_failures.record(ctx.conn, "ses_b", CLONER1, {
        "dev": "sdb", "serial": "S2", "port": 2, "ata_log": [],
        "error": "dd: error writing '/dev/sdb': Operation not permitted"})
    assert disk_failures.record_readonly(ctx.conn, "ses_c", CLONER1, "sdc", "S3")
    causes = {r["session_id"]: disk_failures._cause(r) for r in ctx.conn.execute(
        "SELECT * FROM disk_failures")}
    assert causes == {"ses_a": "unclassified", "ses_b": "readonly", "ses_c": "readonly"}

    cleared = disk_failures.clear_written(ctx.conn, {"S2", "S3"}, "room_x")
    left = sorted(r["session_id"] for r in ctx.conn.execute(
        "SELECT session_id FROM disk_failures WHERE cleared_at IS NULL"))
    assert len(cleared) == 1 and left == ["ses_b", "ses_c"]
    assert disk_failures.clear_written(ctx.conn, {"S2", "S3"}, "room_x") == [], "once"
    assert disk_failures.clear_written(ctx.conn, set(), "room_x") == []
