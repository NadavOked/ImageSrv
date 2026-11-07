"""#1956 -- when the database is backed up and how much it may keep (Nadav 06/11).

The decision, in the approved mockup ``1956-installer-backup``:

* every day at the hour the operator chose (02:00 by default), and right after an
  image is uploaded or deleted -- not every hour;
* a round running: the backup waits for its end and no day is skipped;
* the backups keep to a reserved size (5 GB, at most 20% of the drive): the oldest
  backup goes first and an image is never touched; when not even one backup fits,
  the status turns orange with the reason and nothing else is deleted;
* review of this change: a run of image events is one backup, and the pruning always
  keeps the last good backup and one from each of the last days, so that a burst
  cannot push out every good copy and leave only the state after a fault.

These run ``backup_job.main`` -- what the timer runs every minute -- against a real
database, and read what it left behind: the files, ``last-backup.json``, the health row.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server import db
from server.storage import locations as sl
from server.system import backup, backup_job
from test_storage_locations import FakeHooks

REPO = Path(__file__).resolve().parent.parent
FAR = "backup_reserve_gb"
HOUR = "backup_time"


@pytest.fixture()
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = db.connect(data / "imagesrv.db")
    db.set_setting(conn, "marker", "from-the-live-db")
    return {"data": data, "conn": conn, "dest": data / "backups", "db": data / "backups" / "db"}


def tick(box, *extra) -> int:
    return backup_job.main(["--data-dir", str(box["data"]), *extra])


def copies(box) -> list[str]:
    return sorted(p.name for p in box["db"].glob("imagesrv-[0-9]*.db")) if box["db"].is_dir() else []


def status(box) -> dict:
    return json.loads((box["data"] / "last-backup.json").read_text(encoding="utf-8"))


def health(box, now=None) -> dict:
    return backup_job.health_check(box["conn"], box["data"], "active", now)


def ago(**kw) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


def hhmm(moment: datetime) -> str:
    return moment.astimezone().strftime("%H:%M")


def last_run(box, moment: datetime, **kw) -> None:
    """The file a run left at ``moment``, as the job writes it."""
    at = moment.isoformat(timespec="seconds")
    value = {"at": at, "dest": box["dest"].as_posix(), "ok": True, "file": "imagesrv-x.db", "error": None,
             "last_ok": at, **kw}
    (box["data"] / "last-backup.json").write_text(json.dumps(value), encoding="utf-8")


def hour_is(box, moment: datetime) -> None:
    db.set_setting(box["conn"], HOUR, hhmm(moment))


# --- when --------------------------------------------------------------------------------

def test_the_first_tick_backs_up_and_the_next_ones_do_nothing(box):
    assert tick(box) == 0
    first, written = copies(box), status(box)
    assert len(first) == 1 and written["ok"] is True, written
    assert tick(box) == 0 and tick(box) == 0
    assert copies(box) == first and status(box) == written


def test_an_hour_after_a_backup_there_is_no_backup(box):
    """The hourly timer's whole complaint: nothing changed, the hour is not the chosen one."""
    hour_is(box, ago(hours=3))                  # the chosen hour passed, before the last run
    last_run(box, ago(hours=1))
    before = status(box)
    assert tick(box) == 0
    assert copies(box) == [] and status(box) == before


def test_the_chosen_hour_backs_up_once(box):
    hour_is(box, ago(minutes=30))
    last_run(box, ago(hours=2))
    assert tick(box) == 0
    assert len(copies(box)) == 1
    assert tick(box) == 0
    assert len(copies(box)) == 1


def test_a_server_that_was_off_at_the_chosen_hour_backs_up_when_it_is_back(box):
    hour_is(box, ago(hours=5))
    last_run(box, ago(hours=30))
    assert tick(box) == 0 and len(copies(box)) == 1


def test_an_hour_that_was_never_set_is_two_in_the_morning(box):
    """02:00 local, whatever the test's clock says: a last run a day before 02:00 today is overdue."""
    local = datetime.now().astimezone()
    today = local.replace(hour=2, minute=0, second=0, microsecond=0)
    slot = today if today <= local else today - timedelta(days=1)
    last_run(box, slot - timedelta(minutes=1))
    assert tick(box) == 0 and len(copies(box)) == 1
    last_run(box, slot + timedelta(seconds=1))
    assert tick(box) == 0 and len(copies(box)) == 1


@pytest.mark.parametrize("event", ["image_upload", "image_delete", "capture_done", "storage_image_received",
                                   "vmupdate_saved"])
def test_an_image_coming_in_or_going_backs_up_on_the_next_tick(box, event):
    hour_is(box, ago(hours=3))
    last_run(box, ago(hours=1))
    db.journal(box["conn"], event, "img-1")
    assert tick(box) == 0 and len(copies(box)) == 1
    assert tick(box) == 0 and len(copies(box)) == 1            # one backup per change


def test_events_right_after_a_backup_wait_for_the_end_of_the_window(box):
    hour_is(box, ago(hours=3))
    last_run(box, ago(minutes=5))
    for name in ("img-1", "img-2", "img-3"):
        db.journal(box["conn"], "image_delete", name)
    assert tick(box) == 0 and copies(box) == []
    last_run(box, ago(minutes=16))                # the window is over: all three, in one backup
    assert tick(box) == 0 and len(copies(box)) == 1
    assert tick(box) == 0 and len(copies(box)) == 1


def test_an_event_in_the_same_second_as_the_last_backup_is_not_taken_as_covered(box):
    """``at`` is read before the copy: an event of that second may have missed it."""
    hour_is(box, ago(hours=3))
    moment = ago(minutes=16)
    box["conn"].execute("INSERT INTO journal (ts, event, detail) VALUES (?, 'image_delete', 'img-1')",
                        (moment.isoformat(timespec="seconds"),))
    box["conn"].commit()
    last_run(box, moment)
    assert tick(box) == 0 and len(copies(box)) == 1


def test_an_edit_of_an_image_is_not_a_reason_to_back_up(box):
    hour_is(box, ago(hours=3))
    last_run(box, ago(hours=1))
    db.journal(box["conn"], "image_edit", "img-1")
    assert tick(box) == 0 and copies(box) == []


def test_an_image_change_older_than_the_last_backup_is_already_in_it(box):
    db.journal(box["conn"], "image_upload", "img-1")
    hour_is(box, ago(hours=3))
    last_run(box, datetime.now(timezone.utc) + timedelta(seconds=2))
    assert tick(box) == 0 and copies(box) == []


# --- a running round ---------------------------------------------------------------------

@pytest.fixture()
def round_running(box, monkeypatch):
    state = {"reason": "יש סבב הפצה פתוח או רץ"}
    monkeypatch.setattr(backup_job.busy_db, "reason", lambda conn: state["reason"], raising=False)
    return state


def test_the_backup_waits_for_the_round_and_then_runs_without_losing_the_day(box, round_running):
    hour_is(box, ago(minutes=30))
    last_run(box, ago(hours=2))
    at = status(box)["at"]
    for _ in range(3):
        assert tick(box) == 0
    assert copies(box) == []
    assert status(box)["at"] == at and status(box)["waiting"] == round_running["reason"]
    assert "ממתין: יש סבב הפצה" in health(box)["detail"]

    round_running["reason"] = None
    assert tick(box) == 0
    assert len(copies(box)) == 1 and "waiting" not in status(box)


def test_an_image_change_during_a_round_waits_too(box, round_running):
    hour_is(box, ago(hours=3))
    last_run(box, ago(hours=1))
    db.journal(box["conn"], "image_delete", "img-1")
    assert tick(box) == 0 and copies(box) == []
    round_running["reason"] = None
    assert tick(box) == 0 and len(copies(box)) == 1


def test_the_hour_moved_during_the_wait_does_not_leave_a_false_waiting(box, round_running):
    hour_is(box, ago(minutes=30))
    last_run(box, ago(hours=2))
    assert tick(box) == 0 and status(box)["waiting"]
    hour_is(box, ago(hours=3))                  # the operator moved it to before the last run: nothing is due
    assert tick(box) == 0
    assert "waiting" not in status(box) and "waiting_since" not in status(box) and copies(box) == []
    assert "ממתין" not in health(box)["detail"]


def test_a_waiting_tick_when_no_backup_is_due_writes_nothing(box, round_running):
    hour_is(box, ago(hours=3))
    last_run(box, ago(hours=1))
    before = status(box)
    assert tick(box) == 0
    assert status(box) == before


def test_a_real_room_round_makes_the_backup_wait(box):
    """No fake here: the row a running round leaves, read through the read-only connection of the job."""
    hour_is(box, ago(minutes=30))
    last_run(box, ago(hours=2))
    box["conn"].execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
                        " VALUES ('r1', 'img', 1, 'active', 'noc', '2026-11-06T02:00:00+00:00')")
    box["conn"].commit()
    assert tick(box) == 0
    assert copies(box) == [] and status(box)["waiting"] == "יש סבב חדר פתוח או רץ"
    box["conn"].execute("UPDATE room_rounds SET state = 'closed'")
    box["conn"].commit()
    assert tick(box) == 0 and len(copies(box)) == 1


def test_now_ignores_the_schedule_and_the_round(box, round_running):
    hour_is(box, ago(hours=3))
    last_run(box, ago(minutes=5))
    assert tick(box, "--now") == 0 and len(copies(box)) == 1


# --- how much -----------------------------------------------------------------------------

def seed(box, sizes: dict[str, int]) -> None:
    box["db"].mkdir(parents=True, exist_ok=True)
    for name, size in sizes.items():
        (box["db"] / name).write_bytes(b"x" * size)


@pytest.fixture()
def bytes_are_gb(monkeypatch):
    """The reserve is in GB; here one "GB" is a byte, so a real database is measured against real numbers."""
    monkeypatch.setattr(backup_job, "GB", 1, raising=False)


def db_size(box) -> int:
    from server.system.backup import consistent_db_copy
    src = sqlite3.connect(f"file:{(box['data'] / 'imagesrv.db').as_posix()}?mode=ro", uri=True)
    try:
        return len(consistent_db_copy(src))
    finally:
        src.close()


OLD, MID, NEW = "imagesrv-20260101-020000.db", "imagesrv-20260102-020000.db", "imagesrv-20260103-020000.db"


def test_the_oldest_backups_go_until_the_reserve_fits_and_nothing_else_does(box, bytes_are_gb):
    size = db_size(box)
    seed(box, {OLD: 1000, MID: 1000, NEW: 1000})
    foreign = box["db"] / "imagesrv-notes.db"
    foreign.write_bytes(b"x" * 5000)
    image = box["data"] / "images" / "win11" / "manifest.json"
    image.parent.mkdir(parents=True)
    image.write_text("{}", encoding="utf-8")
    db.set_setting(box["conn"], FAR, str(size + 2500))
    assert tick(box) == 0
    left = copies(box)
    assert OLD not in left and MID in left and NEW in left and len(left) == 3, left
    assert status(box)["pruned"] == [OLD]
    assert foreign.exists() and image.exists()


def test_a_reserve_that_holds_everything_deletes_nothing(box, bytes_are_gb):
    seed(box, {OLD: 1000, MID: 1000})
    db.set_setting(box["conn"], FAR, str(db_size(box) + 5000))
    assert tick(box) == 0
    assert OLD in copies(box) and MID in copies(box) and status(box)["pruned"] == []


def test_backups_are_not_pruned_by_age_any_more(box):
    """A backup of 2026-01-01 is older than the old 30 days; 5 GB holds it, so it stays."""
    seed(box, {OLD: 10})
    assert tick(box) == 0 and OLD in copies(box)


def test_when_not_even_one_backup_fits_the_status_is_orange_and_nothing_is_deleted(box, bytes_are_gb):
    seed(box, {OLD: 10, MID: 10})
    db.set_setting(box["conn"], FAR, str(db_size(box) - 1))
    assert tick(box) == 1                                       # a backup that did not happen is not a success
    assert copies(box) == [OLD, MID]
    written = status(box)
    assert written["ok"] is False and written["too_small"] is True, written
    row = health(box)
    assert row["state"] == "warn" and "המקום השמור" in row["detail"] and "קטן מגיבוי אחד" in row["detail"], row


def test_a_failed_write_prunes_nothing(box, bytes_are_gb, monkeypatch):
    seed(box, {OLD: 10})
    db.set_setting(box["conn"], FAR, str(db_size(box) + 10))
    real = backup_job.os.replace

    def refuse(src, dst):
        if Path(dst).parent.name == "db":
            raise OSError(28, "No space left on device")
        return real(src, dst)
    monkeypatch.setattr(backup_job.os, "replace", refuse)
    assert tick(box) == 1
    assert copies(box) == [OLD]
    row = health(box)
    assert row["state"] == "bad" and "No space left" in row["detail"], row


def test_the_cap_holds_even_when_the_saved_reserve_is_larger(box, bytes_are_gb, monkeypatch):
    """A destination moved to a smaller drive: the saved value does not eat the images' room."""
    size = db_size(box)
    monkeypatch.setattr(backup_job, "drive_cap_bytes", lambda path: size + 25, raising=False)
    seed(box, {OLD: 10, MID: 10, NEW: 10, "imagesrv-20260104-020000.db": 10})
    db.set_setting(box["conn"], FAR, str(size + 1000))
    assert tick(box) == 0
    assert OLD not in copies(box) and MID not in copies(box) and NEW in copies(box)


def stamp(day: int, hour: int = 2, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def name_of(moment: datetime) -> str:
    return f"imagesrv-{moment.strftime('%Y%m%d-%H%M%S')}.db"


def test_a_burst_of_backups_does_not_push_out_the_daily_ones(box, bytes_are_gb):
    """The review's case: many images deleted in a day, each backup a little later than the last. By
    size alone the dailies go first, and what is left is only the state after the fault."""
    size = db_size(box)
    dailies = [stamp(day) for day in (3, 4, 5)]
    dest = box["dest"].as_posix()
    for moment in dailies:
        assert backup_job.run(box["data"], dest, moment, size * 6)["ok"]
    burst = [stamp(5, 9, minute) for minute in range(0, 60, 2)]
    for moment in burst:
        assert backup_job.run(box["data"], dest, moment, size * 6)["ok"]
    left = copies(box)
    assert name_of(stamp(4)) in left and name_of(stamp(5)) in left, left
    assert name_of(burst[-1]) in left and name_of(burst[-2]) in left, left      # the new one, the last good one
    assert sum((box["db"] / n).stat().st_size for n in left) <= size * 6        # and the reserve still holds


def test_a_day_past_the_window_goes_and_the_last_good_one_stays(box, bytes_are_gb):
    size = db_size(box)
    dest = box["dest"].as_posix()
    for moment in (stamp(3), stamp(4), stamp(5), stamp(6), stamp(6, 3), stamp(6, 4)):
        assert backup_job.run(box["data"], dest, moment, size * 5)["ok"]
    left = copies(box)
    assert name_of(stamp(6, 3)) in left and name_of(stamp(6, 4)) in left, left
    assert name_of(stamp(3)) not in left


def test_the_last_good_backup_is_not_traded_for_a_new_one_even_after_a_failed_run(box, bytes_are_gb, monkeypatch):
    """Four copies fit and the fifth would need the last good one gone: it is the only one not protected by
    its day. The failed run in between writes the status too, and the name must come through it."""
    size = db_size(box)
    dest = box["dest"].as_posix()
    for moment in (stamp(4), stamp(5), stamp(6), stamp(6, 3)):
        assert backup_job.run(box["data"], dest, moment, size * 4)["ok"]
    real = backup_job._atomic_write
    monkeypatch.setattr(backup_job, "_atomic_write", _refuse_db_writes(real))
    assert backup_job.run(box["data"], dest, stamp(6, 4), size * 5)["ok"] is False
    monkeypatch.setattr(backup_job, "_atomic_write", real)
    written = backup_job.run(box["data"], dest, stamp(6, 5), size * 4)
    assert written["ok"] is False and written["too_small"] is True, written
    assert written["last_good"] == name_of(stamp(6, 3))
    assert name_of(stamp(6, 3)) in copies(box), copies(box)


def _refuse_db_writes(real):
    def write(target, payload, mode=0o600):
        if target.parent.name == "db":
            raise OSError(28, "No space left on device")
        real(target, payload, mode)
    return write


def test_when_the_protected_backups_do_not_fit_nothing_is_written_and_nothing_is_deleted(box, monkeypatch):
    """Not "written and not deleted": that way every event adds a file and the reserve has no limit."""
    monkeypatch.setattr(backup_job, "GB", 1000, raising=False)   # a stored reserve has at most six digits
    size = db_size(box)
    dest = box["dest"].as_posix()
    for moment in (stamp(3), stamp(4), stamp(5)):
        assert backup_job.run(box["data"], dest, moment, size)["ok"]
    before = copies(box)
    assert len(before) == 3
    db.set_setting(box["conn"], FAR, str(size * 3 // 1000))
    assert tick(box) == 1
    assert copies(box) == before
    written = status(box)
    assert written["ok"] is False and written["too_small"] is True, written
    row = health(box)
    assert row["state"] == "warn" and "קטן ממה שחייב להישמר" in row["detail"], row


def test_a_symlink_named_like_a_backup_is_not_counted_and_not_followed(box, bytes_are_gb):
    secret = box["data"] / "keep-me"
    secret.write_bytes(b"x" * 100)
    seed(box, {OLD: 10})
    (box["db"] / MID).symlink_to(secret)
    db.set_setting(box["conn"], FAR, str(db_size(box) + 10))
    assert tick(box) == 0
    assert secret.exists() and (box["db"] / MID).is_symlink() and OLD in copies(box)


# --- failure is visible -------------------------------------------------------------------

def test_a_database_that_cannot_be_read_is_a_named_failure_not_nothing_to_do(box):
    box["conn"].close()
    for name in ("imagesrv.db-wal", "imagesrv.db-shm"):
        (box["data"] / name).unlink(missing_ok=True)
    (box["data"] / "imagesrv.db").write_bytes(b"not a database, not at all" * 200)
    assert tick(box) == 1
    assert status(box)["ok"] is False and "המסד לא נקרא" in status(box)["error"]


def test_a_failed_backup_is_tried_again_after_an_hour_not_every_minute(box):
    hour_is(box, ago(hours=4))
    last_run(box, ago(minutes=10), ok=False, error="OSError: boom", last_ok=None)
    assert tick(box) == 0 and copies(box) == []
    last_run(box, ago(minutes=61), ok=False, error="OSError: boom", last_ok=None)
    assert tick(box) == 0 and len(copies(box)) == 1


# --- the health row -----------------------------------------------------------------------

@pytest.fixture()
def another_disk(box, tmp_path):
    folder = tmp_path / "usb"
    folder.mkdir()
    db.set_settings(box["conn"], {backup_job.SETTING: folder.as_posix(),
                                  "backup_target": json.dumps({"type": "local", "path": folder.as_posix(),
                                                               "same_disk": False})})
    box["dest"] = folder
    return folder


@pytest.mark.parametrize("hours, state", [(3, "ok"), (20, "ok"), (25, "ok"), (27, "warn")])
def test_stale_is_a_missed_day_not_two_hours(box, another_disk, hours, state):
    """A daily backup is 23 hours old every evening: the hourly job's two-hour threshold would paint
    the health page orange most of the day, and a wrong status is a bug."""
    moment = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
    last_run(box, moment)
    row = health(box, moment + timedelta(hours=hours))
    assert row["state"] == state, row
    assert row["detail"].startswith("ישן") is (state == "warn"), row


def test_a_stale_backup_says_it_is_waiting_for_the_round(box, another_disk):
    moment = datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc)
    last_run(box, moment, waiting="יש סבב חדר פתוח או רץ")
    row = health(box, moment + timedelta(hours=30))
    assert row["state"] == "warn" and "ממתין: יש סבב חדר" in row["detail"], row


# --- the folder and the units --------------------------------------------------------------

def test_the_backups_folder_stays_out_of_the_settings_archive_and_the_storage_locations(box):
    (box["db"]).mkdir(parents=True)
    (box["db"] / OLD).write_bytes(b"a copy of the database")
    payload, _ = backup.build_archive(box["conn"], box["data"], "v0")
    import io
    import tarfile
    names = tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz").getnames()
    assert not [n for n in names if "backups" in n], names
    assert "backups" in sl.DATA_DIR_RESERVED
    script = (REPO / "tools" / "backup-server.sh").read_text(encoding="utf-8")
    assert "'branding' 'backups'" in script


def _unit(name: str) -> str:
    path = REPO / "deploy" / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_the_timer_asks_every_minute_and_is_not_the_hourly_one():
    timer, unit = _unit("imagesrv-backup.timer"), _unit("imagesrv-backup.service")
    assert "OnCalendar=minutely" in timer.splitlines() and "OnCalendar=hourly" not in timer
    assert "ExecStart=/usr/bin/python3 -m server.system.backup_job --data-dir /var/lib/imagesrv" in unit
    assert "ProtectSystem=full" in unit.splitlines()                   # a chosen folder is outside ReadWritePaths
    assert "LogLevelMax=notice" in unit.splitlines() and "SyslogLevel=notice" in unit.splitlines()
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert "run systemctl enable --now imagesrv-backup.timer" in setup


def test_the_default_folder_is_writable_in_both_units():
    """ProtectSystem=strict on the server and full on the job: the server writes the test file, the job the
    backup. A folder outside the server's ReadWritePaths would fail only on a real server."""
    line = next(l for l in _unit("imagesrv-server.service").splitlines() if l.startswith("ReadWritePaths="))
    assert "/var/lib/imagesrv" in line.split("=", 1)[1].split()
    assert backup_job.DEFAULT_DIR == "backups"
    assert "ProtectSystem=full" in _unit("imagesrv-backup.service").splitlines()   # /var stays writable


# --- the console's policy -------------------------------------------------------------------

@pytest.fixture()
def console(tmp_path: Path, images_root: Path, clock, monkeypatch):
    from server.app import create_app
    from server.console import users
    from server.rounds import sender as sender_module
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    data = tmp_path / "data"
    app = create_app(data, images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), storage_hooks=FakeHooks().as_dict(),
                     storage_check_interval=10**9)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    yield {"conn": ctx.conn, "admin": admin, "deploy": deploy, "data": data}
    ctx.sender.stop()


def _post(console, **body):
    return console["admin"].post("/api/console/backup-policy", json=body)


def _saved(console) -> dict:
    return {key: db.get_setting(console["conn"], key) for key in (HOUR, FAR)}


def test_the_policy_starts_at_two_oclock_and_five_gb(console):
    got = console["admin"].get("/api/console/settings").json()["backup_policy"]
    assert got["time"] == "02:00" and got["reserve_gb"] == 5 and got["cap_percent"] == 20, got
    assert isinstance(got["cap_gb"], int), got


def test_the_operator_sets_the_hour_and_the_space(console, monkeypatch):
    monkeypatch.setattr(backup_job, "drive_cap_bytes", lambda path: 93 * 10 ** 9, raising=False)
    r = _post(console, time="23:15", reserve_gb=40)
    assert r.status_code == 200, r.text
    assert r.json()["policy"]["time"] == "23:15" and r.json()["policy"]["reserve_gb"] == 40
    assert r.json()["policy"]["cap_gb"] == 93
    assert _saved(console) == {HOUR: "23:15", FAR: "40"}
    rows = console["conn"].execute("SELECT detail FROM journal WHERE event = 'setting_change'").fetchall()
    assert any("backup_policy=23:15, 40 GB" in row["detail"] for row in rows)


@pytest.mark.parametrize("body, word", [
    ({"time": "25:00", "reserve_gb": 5}, "HH:MM"), ({"time": "2:00", "reserve_gb": 5}, "HH:MM"),
    ({"reserve_gb": 5}, "HH:MM"), ({"time": "02:00", "reserve_gb": 0}, "מקום שמור"),
    ({"time": "02:00", "reserve_gb": "many"}, "מקום שמור"), ({"time": "02:00", "reserve_gb": True}, "מקום שמור"),
    ({"time": "02:00", "reserve_gb": 2.5}, "מקום שמור"), ({"time": "02:00"}, "מקום שמור"),
])
def test_a_bad_policy_is_refused_and_nothing_is_saved(console, body, word):
    r = console["admin"].post("/api/console/backup-policy", json=body)
    assert r.status_code == 400 and word in r.json()["detail"], r.text
    assert _saved(console) == {HOUR: None, FAR: None}


def test_above_twenty_percent_of_the_drive_is_refused_with_the_number(console, monkeypatch):
    monkeypatch.setattr(backup_job, "drive_cap_bytes", lambda path: 93 * 10 ** 9, raising=False)
    r = _post(console, time="02:00", reserve_gb=94)
    assert r.status_code == 400 and "20%" in r.json()["detail"] and "93 GB" in r.json()["detail"], r.text
    assert _saved(console) == {HOUR: None, FAR: None}
    assert _post(console, time="02:00", reserve_gb=93).status_code == 200


def test_a_drive_that_could_not_be_read_saves_nothing_not_anything(console, monkeypatch):
    def broken(path):
        raise OSError("statvfs failed")
    monkeypatch.setattr(backup_job, "drive_cap_bytes", broken, raising=False)
    r = _post(console, time="02:00", reserve_gb=5)
    assert r.status_code == 409, r.text
    assert _saved(console) == {HOUR: None, FAR: None}
    assert console["admin"].get("/api/console/settings").json()["backup_policy"]["cap_gb"] is None


def test_only_an_admin_sets_the_policy_and_settings_cannot_skip_the_cap(console):
    r = console["deploy"].post("/api/console/backup-policy", json={"time": "02:00", "reserve_gb": 5})
    assert r.status_code in (401, 403)
    for key in (HOUR, FAR):
        r = console["admin"].post("/api/console/settings", json={key: "5"})
        assert r.status_code == 400 and key in r.json()["detail"], r.text
