"""#3112 -- the record a pull opened closes when the same drawer comes back and completes.

Metal 13-14/11 (chunk engine, cloner 2): a drawer pulled mid-round and pushed back is completed
in the background and reports ``done`` under the name the round chose it by. The server had
opened a ``disk_failures`` row on the pull (``cause=removed``); it closed only at the wave's tally
(`room._clear_written_failures`) -- never, if the round was stopped before it -- and until then
``disk_sets.health`` reads the disk as orange, so the next round shows it as a problem although it
was written in full. A ``done`` report of the same (round, machine, target) with the same serial
is the evidence, and closes it there, journaled.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.disks import disk_failures, disk_sets
from test_disk_failures import HOTPLUG_LINES, PULLED_ERROR, failed_target
from test_server_api import open_session

SERIAL = "S3TWNE0JB04745"
PULL = failed_target(ata_log=HOTPLUG_LINES, error=PULLED_ERROR)


def _round(server):
    """A round with the machine joined; its sda is SERIAL (#2492: the hello's inventory)."""
    ids = open_session(server, expected=2)
    from conftest import hello_body
    body = hello_body(ids["mac1"])
    body["disks"][0]["serial"] = SERIAL
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    return ids


def _report(server, ids, **target):
    report = {"session_id": ids["session"], "mac": ids["mac1"], "state": "writing",
              "targets": [failed_target(**target)]}
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]


def _open_rows(server):
    return server["ctx"].conn.execute(
        "SELECT id, cause, serial FROM disk_failures WHERE cleared_at IS NULL").fetchall()


def _state(server) -> str:
    return disk_sets.health(server["ctx"].conn, [SERIAL])[SERIAL]["state"]


def _journal(server) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'disk_failure_cleared' ORDER BY id")]


def _inventory(server, ids, **disk) -> None:
    """The machine's hello, as the round's `done` stamp reads it: `stamp_done_serials` (#1126) replaces the
    serial a `done` target carries with the one the hello inventory holds for that device name."""
    from conftest import hello_body
    body = hello_body(ids["mac1"])
    body["disks"][0].update(disk)
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def test_a_pulled_drawer_that_came_back_and_completed_closes_its_record(server):
    ids = _round(server)
    _report(server, ids, **PULL)                                      # the pull: failed, removed
    (row,) = _open_rows(server)
    assert row["cause"] == "removed" and row["serial"] == SERIAL
    assert _state(server) != "orange", "#3147: an open pull row is quiet -- the hello filters it, and so does the health"

    _report(server, ids, state="done", error=None, ata_log=[])        # back under the same name, done
    assert _open_rows(server) == []
    assert _state(server) != "orange"
    assert _journal(server) == [f"#{row['id']} serial={SERIAL} נוקה אוטומטית: המגירה חזרה והושלמה בסבב {ids['session']}"]


def test_the_close_is_once_and_a_repeated_done_report_is_quiet(server):
    ids = _round(server)
    _report(server, ids, **PULL)
    for _ in range(3):                                                # the report repeats every 2 s
        _report(server, ids, state="done", error=None, ata_log=[])
    assert len(_journal(server)) == 1


def test_a_drawer_that_is_still_failed_keeps_its_record(server):
    ids = _round(server)
    _report(server, ids, **PULL)
    _report(server, ids, **PULL)
    assert len(_open_rows(server)) == 1 and _journal(server) == []


def test_another_disk_done_under_the_same_name_does_not_close_it(server):
    """Guard against over-closing: `sda` completed, but the hello says it is another disk than the one
    that failed (the identity a `done` target carries is the inventory's, not the agent's claim)."""
    ids = _round(server)
    _report(server, ids, **PULL)
    _inventory(server, ids, serial="S-ANOTHER-DISK")
    _report(server, ids, state="done", error=None, ata_log=[])
    assert len(_open_rows(server)) == 1 and _journal(server) == []


def test_a_done_target_the_server_cannot_name_closes_nothing(server):
    """No serial in the inventory for that name = no identity (principle 5), and not "the same disk"."""
    ids = _round(server)
    _report(server, ids, **PULL)
    _inventory(server, ids, serial=None)
    _report(server, ids, state="done", error=None, ata_log=[])
    assert len(_open_rows(server)) == 1 and _journal(server) == []


def test_red_does_not_close_on_a_done_report(server):
    """#1742: a read-only row stays until the operator's "נקה"."""
    ids = _round(server)
    conn = server["ctx"].conn
    disk_failures.record(conn, ids["session"], ids["mac1"], {
        "dev": "sda", "serial": SERIAL, "port": 1, "ata_log": [],
        "error": "dd: error writing '/dev/sda': Operation not permitted"})
    (row,) = _open_rows(server)
    assert row["cause"] == "readonly"
    _report(server, ids, state="done", error=None, ata_log=[])
    assert len(_open_rows(server)) == 1


def test_another_round_or_machine_is_not_touched(server):
    ids = _round(server)
    conn = server["ctx"].conn
    disk_failures.record(conn, "ses_earlier_round", ids["mac1"], failed_target(**PULL))
    _report(server, ids, state="done", error=None, ata_log=[])
    assert len(_open_rows(server)) == 1       # the earlier round's row: only the wave tally or "נקה" closes it
