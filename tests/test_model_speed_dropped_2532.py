"""‏#2532: מדידת בדיקת כתיבה שהושלמה לדיסק בלי `model` ב-hello לא נזרקת בשקט.

`model_speeds.model` הוא NOT NULL, ולכן המדידה אינה נשמרת — אבל נרשמת ביומן, כדי שה"לא נמדד"
של הבדיקה המקדימה (#447) לא יסתיר מדידה שהייתה."""

from __future__ import annotations

import json

import pytest

from server import db
from server.console import journal_he
from server.learn import models as learn_models

MAC = "78:ac:c0:9b:11:c2"
EVO = "Samsung SSD 870 EVO 500GB"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _wave(conn, disks, targets):
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)", (MAC, json.dumps(disks)))
    conn.commit()
    return {"mac": MAC, "targets_json": json.dumps(targets)}


def _pre(mbps, state="ok"):
    return {"state": state, "mbps": mbps}


def _dropped(conn):
    return conn.execute("SELECT * FROM journal WHERE event = 'model_speed_dropped'").fetchall()


def test_a_measurement_without_a_model_is_journaled_not_silently_dropped(conn):
    member = _wave(conn, [{"dev": "sda", "serial": "S1", "model": EVO}, {"dev": "sdb", "serial": "S2"}],
                   [{"dev": "sda", "write_preflight": _pre(410)},
                    {"dev": "sdb", "write_preflight": _pre(300)}])     # בלי דגם
    assert learn_models.record_wave(conn, "ses_w", [member]) == 1
    (row,) = _dropped(conn)
    assert row["machine"] == MAC and row["round_id"] == "ses_w"
    assert row["detail"] == "sdb serial=S2 300 MB/s: no model"
    assert [r["serial"] for r in conn.execute("SELECT serial FROM model_speeds")] == ["S1"]


def test_a_measurement_without_a_serial_is_journaled_too(conn):
    member = _wave(conn, [{"dev": "sda", "model": EVO}], [{"dev": "sda", "write_preflight": _pre(250, "slow")}])
    assert learn_models.record_wave(conn, "ses_w", [member]) == 0
    (row,) = _dropped(conn)
    assert row["detail"] == "sda serial=- 250 MB/s: no serial"


def test_a_disk_absent_from_the_hello_is_journaled(conn):
    member = _wave(conn, [], [{"dev": "sda", "write_preflight": _pre(250)}])
    assert learn_models.record_wave(conn, "ses_w", [member]) == 0
    assert len(_dropped(conn)) == 1


def test_no_measurement_means_nothing_was_lost_so_nothing_is_journaled(conn):
    """בדיקה שנכשלה/לא רצה אינה מדידה — אין מה לדווח כאבוד (עיקרון 5: לא מזייפים אירוע)."""
    member = _wave(conn, [{"dev": "sda", "serial": "S1"}, {"dev": "sdb"}],
                   [{"dev": "sda", "write_preflight": _pre(12, "failed")},
                    {"dev": "sdb", "write_preflight": _pre(12, "unchecked")}, {"dev": "sdc"}])
    assert learn_models.record_wave(conn, "ses_w", [member]) == 0
    assert _dropped(conn) == []


def test_a_complete_disk_is_recorded_and_not_journaled(conn):
    member = _wave(conn, [{"dev": "sda", "serial": "S1", "model": EVO}], [{"dev": "sda", "write_preflight": _pre(410)}])
    assert learn_models.record_wave(conn, "ses_w", [member]) == 1
    assert _dropped(conn) == []


def test_a_failing_journal_does_not_undo_what_was_recorded(conn, monkeypatch):
    member = _wave(conn, [{"dev": "sda", "serial": "S1", "model": EVO}, {"dev": "sdb", "serial": "S2"}],
                   [{"dev": "sda", "write_preflight": _pre(410)}, {"dev": "sdb", "write_preflight": _pre(300)}])
    monkeypatch.setattr(learn_models, "journal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert learn_models.record_wave(conn, "ses_w", [member]) == 1
    assert conn.execute("SELECT COUNT(*) FROM model_speeds").fetchone()[0] == 1


def test_the_event_has_a_hebrew_label_and_is_a_warning():
    assert "model_speed_dropped" in journal_he.EVENTS_HE
    assert journal_he.severity("model_speed_dropped") == "warn"
