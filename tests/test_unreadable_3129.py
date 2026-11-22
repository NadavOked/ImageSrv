"""#3129 -- DB, files and state: a stored value that cannot be read is said, not folded away.

`except ValueError: return {}` on a JSON column, or on a time stamp, made "the row is corrupt" look
like "the row says nothing" (principle 5). Each reader keeps its safe answer; the server log now says
where the value was bad, once per distinct value, and **never prints the value** (a storage location's
`params_json` carries secrets). Also counted: the WAL a checkpoint saw (A-DB-02), and the journal rows
lost to a full disk (A-DB-03).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import pytest

from server import db
from server.core import unreadable

PLANTED = "hunter2-chap-3129"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(unreadable, "_seen", set())
    monkeypatch.setattr(unreadable, "suppressed", 0)


def _said(caplog, where: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if where in r.getMessage()]


def test_a_bad_value_is_said_once_with_its_place_and_never_printed(caplog):
    with caplog.at_level(logging.WARNING, logger="imagesrv.unreadable"):
        for _ in range(5):
            unreadable.said("storage_locations.params_json", '{"secret": "' + PLANTED)
        unreadable.said("storage_locations.params_json", '{"other": "' + PLANTED)
    lines = _said(caplog, "storage_locations.params_json")
    assert len(lines) == 2, lines                          # two distinct values, five reads of the first
    assert all(PLANTED not in line for line in lines), lines


def test_the_memory_of_said_values_is_capped(monkeypatch, caplog):
    monkeypatch.setattr(unreadable, "MAX_SEEN", 3)
    with caplog.at_level(logging.WARNING, logger="imagesrv.unreadable"):
        for n in range(10):
            unreadable.said("x", f"value {n}")
    assert len(_said(caplog, "unreadable value in x")) == 3 and unreadable.suppressed == 7


def test_the_storage_location_readers_keep_their_answers_and_say_so(caplog):
    from server.storage import locations
    row = {"params_json": '{"secret": "' + PLANTED, "last_df_json": "{broken", "last_images_json": "[broken"}
    with caplog.at_level(logging.WARNING, logger="imagesrv.unreadable"):
        assert locations.params_of(row) == {} and locations.last_df(row) == {} and locations.last_images(row) == []
    for column in ("params_json", "last_df_json", "last_images_json"):
        assert len(_said(caplog, f"storage_locations.{column}")) == 1, column
    assert not [r for r in caplog.records if PLANTED in r.getMessage()]
    good = {"params_json": '{"a": 1}', "last_df_json": None, "last_images_json": None}
    caplog.clear()
    assert locations.params_of(good) == {"a": 1} and locations.last_df(good) == {} and not caplog.records


def test_the_other_json_readers_say_it(caplog):
    from server.disks import disk_failures
    from server.learn import tuning
    with caplog.at_level(logging.WARNING, logger="imagesrv.unreadable"):
        assert disk_failures._lines("[not json") == []
        assert tuning._json("{not json") == {}
        assert tuning._json("") == {} and disk_failures._lines(None) == []        # absent is not unreadable
    assert len(_said(caplog, "disk_failures.ata_log")) == 1 and len(_said(caplog, "learning.json")) == 1
    assert len(caplog.records) == 2


def test_a_time_stamp_that_cannot_be_read_is_said_by_both_agent_readers(caplog):
    from server.agent import identity, timing
    now = datetime.now(timezone.utc)
    with caplog.at_level(logging.WARNING, logger="imagesrv.unreadable"):
        assert timing._recent("not a time", now) is False
        assert identity._recent("not a time", now) is False
        assert timing._recent("2026-11-15T10:00:00", now) is False      # no time zone
        assert identity._recent("2026-11-15T10:00:00", now) is False
        assert timing._recent(None, now) is False and identity._recent(None, now) is False
    assert len(_said(caplog, "net_devices.last_seen")) == 2             # one per distinct value (said once for both)
    assert len(_said(caplog, "no time zone")) == 1
    caplog.clear()
    stamp = now.isoformat(timespec="seconds")
    assert timing._recent(stamp, now) is True and identity._recent(stamp, now) is True and not caplog.records


def test_a_corrupt_disks_row_of_another_machine_is_said_and_skipped(tmp_path, caplog):
    from server.rounds import room
    conn = db.connect(tmp_path / "t.db")
    db.net_seen(conn, "aa:bb:cc:00:31:29", "10.0.0.5", disks_json="S1 {broken")
    member = {"session_id": "none", "mac": "aa:bb:cc:00:31:30"}
    round_row = {"target_slots_json": "[]"}
    with caplog.at_level(logging.WARNING, logger="imagesrv.unreadable"):
        assert room._held_elsewhere(conn, round_row, member, "S1") is None
    assert len(_said(caplog, "net_devices.disks_json")) == 1


def test_the_wal_a_checkpoint_saw_is_a_number(tmp_path):
    before = db.wal_stats()
    conn = db.connect(tmp_path / "t.db")
    for n in range(db.CHECKPOINT_EVERY_COMMITS + 5):
        db.set_setting(conn, "k", str(n))
    after = db.wal_stats()
    assert after["checkpoints"] > before["checkpoints"], after
    assert isinstance(after["last_log_frames"], int) and after["max_log_frames"] >= after["last_log_frames"] >= 0


def test_a_journal_row_lost_to_a_full_disk_is_counted(tmp_path, monkeypatch):
    db.connect(tmp_path / "t.db")                              # creates the schema
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db, "_last_full", {})
    monkeypatch.setattr(db, "_journal_lost", {"count": 0, "last_event": None})
    conn.execute(f"PRAGMA max_page_count = {conn.execute('PRAGMA page_count').fetchone()[0]}")
    db.journal(conn, "unknown_mac", "y" * 65536)               # the partition is full: not raised, not written
    assert db.journal_rows_lost() == {"count": 1, "last_event": "unknown_mac"}
