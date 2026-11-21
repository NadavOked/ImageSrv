"""#3147 + #3148 -- what a pulled drawer leaves in `disk_failures`, and what a second failure does to it.

#3147: ``disk_sets.health`` (the console's colour of a disk) painted an open ``removed`` row orange while the
hello (`disk_failures.open_for`, what the agent colours from) filtered it. The two answered one question
with two sets of words. Both now ask `disk_failures.quiet`, so the parity test below walks every kind of
row and demands one answer from both.

#3148: `record` wrote once per (round, machine, target). A drawer pulled, returned and then failed for
real in the same round had its second failure swallowed by the pull's row (principle 5: a real failure
hidden). A quiet row now reopens under the name of the real failure; a real row stays as it was.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import db
from server.disks import disk_failures, disk_sets
from test_disk_failures import CABLE_LINES, DISK_LINES, HOTPLUG_LINES, PULLED_ERROR, failed_target

MAC = "b4:2e:99:07:1a:c4"
SERIAL = "S3TWNE0JB04745"
SES = "ses_round_1"
STOPPED = "מחיצה 1: פג הזמן -- הזרם לא הגיע"          # an interruption (#1580), not a disk
WRITE_FAIL = "מחיצה 3: הכתיבה נכשלה (partclone rc=1)"

PULL = failed_target(ata_log=HOTPLUG_LINES, error=PULLED_ERROR)
DISK_FAIL = failed_target(ata_log=DISK_LINES, error=WRITE_FAIL)
CABLE_FAIL = failed_target(ata_log=CABLE_LINES, error=WRITE_FAIL)


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "t.db")


def _rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, cause, error, port, cleared_at FROM disk_failures ORDER BY id")]


def _state(conn) -> str:
    return disk_sets.health(conn, [SERIAL])[SERIAL]["state"]


def _hello(conn) -> list[dict]:
    return disk_failures.open_for(conn, MAC, [{"serial": SERIAL}])


def _journal(conn) -> list[str]:
    return [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'disk_failure' ORDER BY id")]


# --- #3147 ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("target, painted", [
    (CABLE_FAIL, True),                                                       # orange: the cable
    (DISK_FAIL, True),                                                        # red: the disk
    (PULL, False),                                                            # a pull
    (failed_target(ata_log=[], error=STOPPED), False),                        # an interruption (#1580)
    (failed_target(ata_log=[], error="מחיצה 2: הזרם נקטע לפני סופו (מקור rc=1)"), False),
])
def test_the_hello_and_the_health_say_the_same_thing_about_an_open_row(conn, target, painted):
    """The property, not a list of causes: whatever the hello hands the agent to colour, the console colours;
    whatever the hello filters, the console leaves alone."""
    disk_failures.record(conn, SES, MAC, target)
    assert len(_rows(conn)) == 1, "the row is there -- only its colour is in question"
    assert bool(_hello(conn)) is painted
    assert (_state(conn) in ("orange", "red")) is painted


def test_a_row_the_agent_read_as_removed_from_its_evidence_is_quiet_in_the_health_too(conn):
    """`unclassified` written before the classifier knew a pull is read again by its evidence (`quiet`): the
    hello does it, and so must the health -- id 1241 on the lab."""
    conn.execute("INSERT INTO disk_failures (session_id, mac, dev, serial, port, ata_port, at, cause, error, ata_log)"
                 " VALUES (?, ?, 'sda', ?, 1, 1, '2026-11-14T00:00:00+00:00', 'unclassified', ?, '[]')",
                 (SES, MAC, SERIAL, PULLED_ERROR))
    conn.commit()
    assert _hello(conn) == []
    assert _state(conn) == "unchecked"


def test_a_pulled_drawer_that_came_back_is_not_a_problem_even_before_the_wave_tally_closes_the_row(conn):
    disk_failures.record(conn, SES, MAC, PULL)
    assert [r["cause"] for r in _rows(conn)] == ["removed"] and _rows(conn)[0]["cleared_at"] is None
    assert _state(conn) != "orange"


# --- #3148 ---------------------------------------------------------------------------------------------------

def test_a_real_failure_after_a_pull_and_a_return_is_recorded_by_its_own_name(conn):
    """The case of the Issue: pulled -> back and completed (the row closes, #3112) -> a real write failure in
    the same round. Before: the second failure was swallowed, nothing was painted."""
    disk_failures.record(conn, SES, MAC, PULL)
    disk_failures.clear_returned(conn, SES, MAC, "sda", SERIAL)
    assert _rows(conn)[0]["cleared_at"] is not None and _state(conn) != "orange"

    row = disk_failures.record(conn, SES, MAC, DISK_FAIL)

    assert row is not None and row["cause"] == "disk", "the real failure is written, by its own cause"
    (only,) = _rows(conn)
    assert only["cause"] == "disk" and only["error"] == WRITE_FAIL and only["cleared_at"] is None
    assert _state(conn) == "red"                                              # the console sees it
    assert [h["cause"] for h in _hello(conn)] == ["disk"]                     # and so does the agent, next boot
    log = _journal(conn)
    assert len(log) == 2 and "cause=removed" in log[0] and "cause=disk" in log[1], log   # the pull stays in the journal


def test_the_same_when_the_pull_was_never_closed(conn):
    """The drawer came back and the round did not reach its `done` before the failure: the row is still open and quiet."""
    disk_failures.record(conn, SES, MAC, PULL)
    assert disk_failures.record(conn, SES, MAC, CABLE_FAIL)["cause"] == "cable"
    assert [r["cause"] for r in _rows(conn)] == ["cable"]
    assert _state(conn) == "orange" and [h["cause"] for h in _hello(conn)] == ["cable"]


def test_a_new_identity_replaces_the_pulls_when_another_disk_failed_under_the_same_name(conn):
    disk_failures.record(conn, SES, MAC, PULL)
    disk_failures.record(conn, SES, MAC, {**DISK_FAIL, "serial": "S-OTHER", "port": 2})
    (only,) = conn.execute("SELECT serial, port FROM disk_failures").fetchall()
    assert (only["serial"], only["port"]) == ("S-OTHER", 2)


def test_the_report_repeats_every_two_seconds_and_the_second_failure_is_written_once(conn):
    disk_failures.record(conn, SES, MAC, PULL)
    first = disk_failures.record(conn, SES, MAC, DISK_FAIL)
    at = conn.execute("SELECT at FROM disk_failures").fetchone()["at"]
    for _ in range(3):
        assert disk_failures.record(conn, SES, MAC, DISK_FAIL) is None
    assert first is not None and len(_journal(conn)) == 2
    assert conn.execute("SELECT at FROM disk_failures").fetchone()["at"] == at


def test_a_late_pull_report_after_the_real_failure_does_not_take_it_back(conn):
    """The guard of the other direction: the row is real now, and a quiet report may not turn it quiet."""
    disk_failures.record(conn, SES, MAC, PULL)
    disk_failures.record(conn, SES, MAC, DISK_FAIL)
    assert disk_failures.record(conn, SES, MAC, PULL) is None
    assert [r["cause"] for r in _rows(conn)] == ["disk"] and _state(conn) == "red"


def test_a_quiet_failure_after_a_quiet_row_opens_nothing(conn):
    """An interruption after a pull is not a real failure: no escalation, nothing painted."""
    disk_failures.record(conn, SES, MAC, PULL)
    assert disk_failures.record(conn, SES, MAC, failed_target(ata_log=[], error=STOPPED)) is None
    assert [r["cause"] for r in _rows(conn)] == ["removed"] and _state(conn) != "orange"


def test_a_real_row_is_not_overwritten_by_another_real_one(conn):
    """Where the change stops: quiet -> real only. The failure already painted keeps its cause."""
    disk_failures.record(conn, SES, MAC, CABLE_FAIL)
    assert disk_failures.record(conn, SES, MAC, DISK_FAIL) is None
    assert [r["cause"] for r in _rows(conn)] == ["cable"]


def test_another_round_or_target_has_its_own_row(conn):
    disk_failures.record(conn, SES, MAC, PULL)
    disk_failures.record(conn, "ses_round_2", MAC, DISK_FAIL)
    disk_failures.record(conn, SES, MAC, {**DISK_FAIL, "dev": "sdb"})
    assert sorted((r["cause"]) for r in _rows(conn)) == ["disk", "disk", "removed"]
