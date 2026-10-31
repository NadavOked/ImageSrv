"""‏#1891 — "החל עדכון" (ושינוי הגדרות שמחייב אתחול) נחסם על כל מה ש-
‏`auto_reboot.busy_reason` מגדיר כ"עסוק", ולא רק על `sessions`.

שני המקרים מה-Issue: סבב חדר **בין גלים** (‏`room_rounds` ‏active, ‏
‏`wave_session_id` ‏NULL ואין שורה פתוחה ב-`sessions`) וקליטה רצה. ושלישי:
‏DB שזורק הוא סירוב בשם, לא "לא עסוק".
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")

from test_console_hostcfg import host, reissue  # noqa: F401 — fixture
from test_update import _enable, update_server  # noqa: F401 — fixture

NOW = "2026-10-21T08:00:00+00:00"


def _room_between_waves(conn) -> None:
    conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by,"
        " created_at, wave_session_id) VALUES ('rr1', 'img', '[]', 'active', 'noc', ?, NULL)",
        (NOW,))
    conn.commit()
    assert conn.execute("SELECT 1 FROM sessions WHERE state IN ('open','running')"
                        ).fetchone() is None                 # בין גלים: אין session


def _capture_running(conn) -> None:
    conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, created_by, created_at,"
        " updated_at, state) VALUES ('tsk_1', 'aa:bb:cc:00:00:10', 'capture', 'sda',"
        " 'img_1', 'n', 'noc', ?, ?, 'running')", (NOW, NOW))
    conn.commit()


def _apply(update_server):
    _enable(update_server["admin"])
    return update_server["admin"].post("/api/console/update/apply", json={"tag": "v0.25.0"})


def test_apply_between_two_waves_of_a_room_round_is_409(update_server):
    _room_between_waves(update_server["app"].state.ctx.conn)
    r = _apply(update_server)
    assert r.status_code == 409, r.text
    assert "סבב חדר" in r.json()["detail"]
    assert "כשזה יסתיים" in r.json()["detail"]
    assert update_server["calls"]["run"] == 0


def test_apply_during_a_running_capture_is_409(update_server):
    _capture_running(update_server["app"].state.ctx.conn)
    r = _apply(update_server)
    assert r.status_code == 409, r.text
    assert "קליטת אימג'" in r.json()["detail"]
    assert update_server["calls"]["run"] == 0


def test_apply_when_nothing_is_busy_still_starts(update_server):
    assert _apply(update_server).status_code == 200
    assert update_server["calls"]["run"] == 1


def test_a_database_that_cannot_be_read_is_a_refusal_not_idle():
    from server import update

    class Broken:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")

    class Rt:
        conn = Broken()

    reason = update.restart_blocker(Rt())
    assert reason and "לא ניתן היה לבדוק" in reason


def test_a_settings_change_that_restarts_waits_for_a_room_round_between_waves(host):
    _room_between_waves(host["ctx"].conn)
    r = reissue(host["admin"])
    assert r.status_code == 409, r.text
    assert "סבב חדר" in r.json()["detail"]
    assert host["machine"].restarts == 0


# --- ‏server-upgrade.sh: אותה הגדרה גם כשהסקריפט רץ ביד ------------------------

def test_the_upgrade_script_refuses_between_two_waves_too(tmp_path):
    from test_installer_1131 import (BASH, _fake_repo, _run_upgrade, _seed_db, _status,
                                     _upgrade_fakes, calls)
    if not BASH:
        pytest.skip("server-upgrade.sh is bash")
    from server.db import connect

    bindir = tmp_path / "bin"
    bindir.mkdir()
    data = tmp_path / "data"
    _seed_db(data)
    conn = connect(data / "imagesrv.db")
    _room_between_waves(conn)
    conn.close()
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    assert not [c for c in calls(tmp_path) if c.startswith(("git checkout", "systemctl restart"))]
    status = _status(data)
    assert status and status["state"] == "failed" and "סבב חדר" in status["error"], status


# --- סקירה: אות ממחשב — רק רשום, רק טרי, ועקיפה של מנהל -------------------------

def _copying(conn, mac: str, seconds_ago: float = 1.0, register: bool = False) -> None:
    from datetime import datetime, timedelta, timezone
    seen = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    conn.execute("INSERT INTO net_devices (mac, ip, last_seen, local_clone)"
                 " VALUES (?, '10.0.0.9', ?, 'copying')", (mac, seen))
    if register:
        conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at)"
                     " VALUES (?, '07', 'grp_BUILD', ?)", (mac, NOW))
    conn.commit()


def test_an_unregistered_machine_reporting_a_local_copy_does_not_block(update_server):
    _copying(update_server["app"].state.ctx.conn, "aa:bb:cc:dd:ee:01")
    assert _apply(update_server).status_code == 200
    assert update_server["calls"]["run"] == 1


def test_a_stale_local_copy_signal_does_not_block(update_server):
    _copying(update_server["app"].state.ctx.conn, "aa:bb:cc:dd:ee:02",
             seconds_ago=600, register=True)
    assert _apply(update_server).status_code == 200


def test_a_registered_fresh_local_copy_blocks_and_names_the_machine(update_server):
    _copying(update_server["app"].state.ctx.conn, "aa:bb:cc:dd:ee:03", register=True)
    r = _apply(update_server)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "update_busy"
    assert "aa:bb:cc:dd:ee:03" in r.json()["busy"]
    assert update_server["calls"]["run"] == 0


def test_an_admin_can_update_anyway_and_it_is_journaled(update_server):
    conn = update_server["app"].state.ctx.conn
    _room_between_waves(conn)
    _enable(update_server["admin"])
    r = update_server["admin"].post("/api/console/update/apply",
                                    json={"tag": "v0.25.0", "force": True})
    assert r.status_code == 200, r.text
    assert update_server["calls"]["run"] == 1
    rows = conn.execute("SELECT detail, user FROM journal WHERE event = 'update_forced'").fetchall()
    assert len(rows) == 1 and "סבב חדר" in rows[0]["detail"] and rows[0]["user"] == "noc"
    from server import update
    assert "סבב חדר" in update.get_status(conn)["forced"]


def test_the_scheduled_reboot_never_overrides(update_server):
    from types import SimpleNamespace

    from server import auto_reboot
    conn = update_server["app"].state.ctx.conn
    _room_between_waves(conn)
    assert auto_reboot.busy_reason(SimpleNamespace(conn=conn), include_update=False)


def test_the_upgrade_script_honours_the_admin_override(tmp_path):
    from test_installer_1131 import (BASH, _fake_repo, _run_upgrade, _seed_db,
                                     _upgrade_fakes, calls)
    if not BASH:
        pytest.skip("server-upgrade.sh is bash")
    from server import update
    from server.db import connect

    bindir = tmp_path / "bin"
    bindir.mkdir()
    data = tmp_path / "data"
    _seed_db(data)
    conn = connect(data / "imagesrv.db")
    _room_between_waves(conn)
    update._set_status(conn, {"state": "running", "tag": "v0.49.0", "by": "noc",
                              "forced": "יש סבב חדר פתוח או רץ"})
    conn.close()
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)   # tag="v0.49.0"
    seen = calls(tmp_path)
    assert any(c.startswith("git") and "checkout --detach v0.49.0" in c for c in seen), (
        seen, proc.stderr[-2000:])
