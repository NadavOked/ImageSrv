"""‏#1886 — מקום פנוי בשרת לאורך זמן, ובדיקה לפני קליטה לפי גודל הגרסה הקודמת.

הכלל: אין מקום לגרסה הקודמת + מרווח + הרזרבה — **סירוב לפני שהקליטה מתחילה**, עם המספרים. בלי גרסה
קודמת — אזהרה, לא סירוב. מחיקה רק **מוצעת** (עיקרון 7) — שום דבר כאן אינו מוחק.
"""

from __future__ import annotations

import copy
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.agent import registry
from server.system import housekeeping
from server.storage import disk_reserve, storage_history

GB = 10 ** 9
Usage = namedtuple("Usage", "total used free")
MAC = "aa:bb:cc:dd:18:86"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


class Library:
    def __init__(self, images: dict):
        self.images = images

    def scan(self):
        return copy.deepcopy(self.images)


def _rows(conn, loc, points):
    """(ימים אחורה, פנוי בבייטים) — דגימות ישנות, כמו שהסיבוב היומי היה כותב."""
    now = datetime.now(timezone.utc)
    for days, free in points:
        conn.execute("INSERT INTO storage_usage (location_id, at, total_bytes, free_bytes, source)"
                     " VALUES (?,?,?,?, 'daily')",
                     (loc, (now - timedelta(days=days)).isoformat(timespec="seconds"), 1000 * GB, free))
    conn.commit()


# --- הדגימה וההיסטוריה ---------------------------------------------------------


def test_a_sample_is_stored_with_its_source_and_capped(conn, monkeypatch):
    monkeypatch.setattr(storage_history, "KEEP_PER_LOC", 3)
    for free in (500, 400, 300, 200):
        assert storage_history.sample(conn, "loc_local", "/x", "capture",
                                      usage=lambda p, f=free: Usage(1000 * GB, 0, f * GB))
    rows = conn.execute("SELECT free_bytes, source FROM storage_usage ORDER BY rowid").fetchall()
    assert [r["free_bytes"] for r in rows] == [400 * GB, 300 * GB, 200 * GB]
    assert {r["source"] for r in rows} == {"capture"}


def test_an_unreadable_location_is_not_a_zero(conn):
    def boom(path):
        raise OSError("stale NFS handle")
    assert storage_history.sample(conn, "loc_nas", "/mnt/nas", "daily", usage=boom) is None
    assert conn.execute("SELECT COUNT(*) FROM storage_usage").fetchone()[0] == 0


def test_only_connected_locations_are_sampled(conn, tmp_path):
    from server.storage import storage_locations
    storage_locations.ensure_local(conn, tmp_path)
    assert storage_history.sample_all(conn, "daily",
                                      usage=lambda p: Usage(100 * GB, 0, 40 * GB)) == 1
    conn.execute("UPDATE storage_locations SET state = 'unreachable'")
    conn.commit()
    assert storage_history.sample_all(conn, "daily", usage=lambda p: Usage(1, 0, 0)) == 0


def test_the_trend_says_when_it_fills_and_needs_more_than_a_day(conn):
    _rows(conn, "loc_local", [(0.2, 500 * GB)])
    t = storage_history.trend(conn, "loc_local")
    assert t["free_bytes"] == 500 * GB and t["per_day"] is None and t["days_left"] is None
    _rows(conn, "loc_local", [(10, 600 * GB)])
    t = storage_history.trend(conn, "loc_local")
    assert t["per_day"] == pytest.approx(100 * GB / 9.8, rel=0.01)
    assert t["days_left"] == pytest.approx(49.0, abs=0.2)


def test_the_health_line_is_orange_under_a_month(conn):
    _rows(conn, "loc_local", [(10, 300 * GB), (0, 100 * GB)])   # 20GB ליום → 5 ימים
    [line] = storage_history.health_checks(conn)
    assert line["state"] == "warn" and "מתמלא בעוד כ-5 ימים" in line["detail"]
    _rows(conn, "loc_nas", [(0.1, 100 * GB)])
    lines = {c["id"]: c for c in storage_history.health_checks(conn)}
    assert lines["storage_trend:loc_nas"]["state"] == "off"


def test_housekeeping_drops_samples_older_than_the_ceiling(conn):
    _rows(conn, "loc_local", [(storage_history.KEEP_DAYS + 5, GB), (1, GB)])
    assert housekeeping.purge_tables(conn)["deleted"].get("storage_usage") == 1


# --- לפני קליטה --------------------------------------------------------------


def _lib(**more):
    return Library({"img_old": {"id": "img_old", "name": "Win11", "folder": "מעבדה",
                                "total_compressed_bytes": 60 * GB, "version": 1},
                    **more})


def test_no_room_for_the_previous_version_is_refused_with_the_numbers(conn, monkeypatch):
    monkeypatch.setattr(disk_reserve, "reserve", lambda: 10 * GB)
    lib = _lib(img_stale={"id": "img_stale", "name": "ישן", "total_compressed_bytes": 80 * GB})
    got = storage_history.capture_check(conn, lib, "Win11", "מעבדה", MAC, free=70 * GB)
    assert got["status"] == "refused" and got["need_bytes"] == 76 * GB
    assert "פנוי 70.0 GB" in got["reason"] and "60.0 GB" in got["reason"] and "76.0 GB" in got["reason"]
    assert [s["id"] for s in got["suggest"]] == ["img_stale", "img_old"]   # הגדול קודם — הצעה בלבד
    assert "המחיקה בידיים שלכם" in got["reason"]
    ok = storage_history.capture_check(conn, lib, "Win11", "מעבדה", MAC, free=80 * GB)
    assert ok["status"] == "ok"


def test_without_a_previous_version_it_is_a_warning_not_a_refusal(conn):
    got = storage_history.capture_check(conn, _lib(), "Ubuntu", "מעבדה", MAC, free=GB)
    assert got["status"] == "warning" and got["previous"] is None
    assert storage_history.capture_check(conn, _lib(), "Win11", "מעבדה", MAC, free=None)["status"] == "warning"


def test_the_previous_capture_of_the_same_machine_counts(conn):
    conn.execute("INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by,"
                 " created_at, updated_at) VALUES ('t1', ?, 'capture', 'sda', 'img_old', 'x',"
                 " 'done', 't', ?, ?)", (MAC, db.now_iso(), db.now_iso()))
    conn.commit()
    got = storage_history.capture_check(conn, _lib(), "שם אחר", "", MAC, free=GB)
    assert got["status"] == "refused" and got["previous"]["why"] == "same_machine"


def test_an_image_used_lately_is_not_suggested(conn):
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at) VALUES ('s1', 'grp_CLONERS', 'img_old',"
                 " 'p', 1, 300, 'closed', 't', ?, 0)", (db.now_iso(),))
    conn.commit()
    assert storage_history.stale_images(conn, _lib()) == []


def test_the_console_refuses_before_the_task_exists(server, monkeypatch):
    """‏`POST /api/console/tasks/capture` — 409 עם ההסבר, ואין משימה; הדגימה נשמרת."""
    from server.library import capture
    ctx, admin = server["ctx"], server["admin"]
    registry.add_machine(ctx.conn, MAC, "build-1", "grp_BUILD", "test")
    monkeypatch.setattr(capture, "_free_bytes", lambda root: 5 * GB)
    monkeypatch.setattr(ctx.library, "scan", lambda: _lib().scan())
    r = admin.post("/api/console/tasks/capture", json={"mac": MAC, "disk": "sda", "name": "Win11",
                                                       "folder": "מעבדה"})
    assert r.status_code == 409 and "אין מקום בשרת לקליטה" in r.json()["detail"], r.text
    assert ctx.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert ctx.conn.execute("SELECT detail FROM journal WHERE event = 'capture_space_refused'").fetchone()
    r = admin.post("/api/console/tasks/capture", json={"mac": MAC, "disk": "sda", "name": "חדש",
                                                       "folder": "מעבדה"})
    assert r.status_code == 200, r.text
    assert ctx.conn.execute("SELECT detail FROM journal WHERE event = 'capture_space_unchecked'").fetchone()
    assert ctx.conn.execute("SELECT COUNT(*) FROM storage_usage WHERE source = 'capture'").fetchone()[0] == 2


def test_the_interface_documents_it():
    from pathlib import Path
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "storage_usage" in doc and "capture_space_refused" in doc
