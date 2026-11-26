import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

from server import db
from server.rounds.sessions import SessionStore


class ScheduledCastMemory(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        db._initialize(self.conn)
        self.store = SessionStore(self.conn)
        self.conn.execute(
            "INSERT INTO sessions(id,group_id,image_id,prefix,expected_clients,wait_seconds,state,"
            "opened_by,created_at,last_join_at) VALUES ('w','grp_CLONERS','img','ROOM',2,30,"
            "'open','',?,0)", (db.now_iso(),))
        self.conn.execute(
            "INSERT INTO room_rounds(id,image_id,target_drives,state,wave_session_id,opened_by,created_at)"
            " VALUES ('r','img',2,'active','w','','now')")
        self.conn.commit()
        wave = self.conn.execute("SELECT * FROM sessions").fetchone()
        self.store.record_hello(wave, "cloner")

    def test_manual_override_records_what_the_brain_would_do(self):
        self.store.start_now("w", "operator")
        self.assertEqual(self.conn.execute("SELECT state FROM sessions").fetchone()[0], "running")
        row = self.conn.execute("SELECT verdict FROM brain_decisions WHERE rule='SCHEDULED_CAST'").fetchone()
        self.assertIsNotNone(row, "missing Scheduled-Cast start comparison")
        self.assertEqual(row["verdict"], "WAIT")

    def test_off_leaves_no_start_comparison(self):
        db.set_setting(self.conn, "brain_veto_mode", "off")
        self.store.start_now("w", "operator")
        self.assertIsNone(self.conn.execute("SELECT id FROM brain_decisions WHERE rule='SCHEDULED_CAST'").fetchone())

    def test_clock_cannot_be_measured(self):
        self.conn.execute("UPDATE sessions SET created_at='unknown'")
        self.conn.commit()
        self.store.start_auto("w")
        row = self.conn.execute("SELECT verdict FROM brain_decisions WHERE rule='SCHEDULED_CAST'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["verdict"], "UNDECIDED")

    def test_ready_room_tick_waits_for_deadline(self):
        from server.rounds import room

        db.set_setting(self.conn, "brain_veto_mode", "enforce")
        db.set_setting(self.conn, "brain_scheduled_cast", "true")
        opened = datetime.now(timezone.utc).replace(microsecond=0)
        self.conn.execute("UPDATE sessions SET created_at=?", (opened.isoformat(),))
        self.conn.commit()
        with patch.object(room, "ready_drives", return_value=2):
            for seconds, expected in ((29, "open"), (30, "running")):
                with patch.object(room, "_utcnow", return_value=opened + timedelta(seconds=seconds)):
                    room.tick(self.conn, self.store)
                self.assertEqual(self.conn.execute("SELECT state FROM sessions").fetchone()[0], expected)

    def test_ready_room_tick_releases_after_second_distinct_join(self):
        from server.rounds import room

        db.set_setting(self.conn, "brain_veto_mode", "enforce")
        db.set_setting(self.conn, "brain_scheduled_cast", "true")
        with patch.object(room, "ready_drives", return_value=2):
            for mac, expected in (("cloner", "open"), ("another-cloner", "running")):
                wave = self.conn.execute("SELECT * FROM sessions").fetchone()
                self.store.record_hello(wave, mac)
                room.tick(self.conn, self.store)
                self.assertEqual(self.conn.execute("SELECT state FROM sessions").fetchone()[0], expected)
        row = self.conn.execute("SELECT verdict,mode FROM brain_decisions WHERE rule='SCHEDULED_CAST'").fetchone()
        self.assertEqual(tuple(row), ("MINIMUM", "enforce"))

    def test_global_enforce_without_switch_starts_in_shadow(self):
        from server.rounds import room

        db.set_setting(self.conn, "brain_veto_mode", "enforce")
        with patch.object(room, "ready_drives", return_value=2):
            room.tick(self.conn, self.store)
        row = self.conn.execute("SELECT verdict,mode FROM brain_decisions WHERE rule='SCHEDULED_CAST'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(tuple(row), ("WAIT", "shadow"))

    def test_waiting_wave_still_runs_rewake_and_relight_and_leaves_no_open_transaction(self):
        from server.rounds import room

        db.set_setting(self.conn, "brain_veto_mode", "enforce")
        db.set_setting(self.conn, "brain_scheduled_cast", "true")
        opened = datetime.now(timezone.utc).replace(microsecond=0)
        self.conn.execute("UPDATE sessions SET created_at=?", (opened.isoformat(),))
        self.conn.commit()
        with patch.object(room, "ready_drives", return_value=2),                 patch.object(room, "_rewake") as rewake, patch.object(room, "_relight") as relight,                 patch.object(room, "_utcnow", return_value=opened + timedelta(seconds=10)):
            room.tick(self.conn, self.store)
        self.assertEqual(self.conn.execute("SELECT state FROM sessions").fetchone()[0], "open")
        rewake.assert_called_once()
        relight.assert_called_once()
        self.assertFalse(self.conn.in_transaction)
