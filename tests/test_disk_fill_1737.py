"""‏#1737 — השרת לא מתמלא: רזרבה למערכת, תקרה לטבלאות, ‏staging נטוש, ‏journald.

נדב (18/10): "איך דואגים שהשרת לא יתמלא בדברים לא חיוניים, ושעוד חצי שנה הוא
לא יפסיק לעבוד?" — והכרעתו: מחיקה **אוטומטית** של ישן, פעם ביום. אימג'ים
לעולם לא נמחקים (עיקרון 3).

הבדיקות כאן אינן רק "נמחק": לכל טבלה שקוראים ממנה "מצב נוכחי" נבדק שהקורא
מחזיר **אותה תשובה** לפני הניקוי ואחריו — מחיקה שמשנה התנהגות אסורה.
הרזרבה בקליטה, ב-pull, ב-receive וב-vmupdate נבדקת בקבצים של כל מסלול
(‏test_capture_room, ‏test_capture_preflight, ‏test_storage_pull,
‏test_push_stream_1457, ‏test_storage_transfer, ‏test_vmupdate*).
"""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, write_image

from server.library import archive, work_areas
from server.console import console_library
from server.rounds import drive_rates, room_precheck
from server.agent import inventory, probe
from server.system import housekeeping
from server.storage import disk_reserve
from server.interserver import storage_transfer
from server.library.archive import tar_stream
from server.db import connect

#: #1737: this module reasons about the system reserve -- it stays 10GiB here
#: (conftest `_host_free_space` sets it to 0 elsewhere).
pytestmark = pytest.mark.system_reserve

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
R = disk_reserve.SYSTEM_RESERVE_BYTES


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


@pytest.fixture()
def conn(tmp_path: Path):
    return connect(tmp_path / "imagesrv.db")


def events(conn) -> dict[str, str]:
    return {row["event"]: row["detail"]
            for row in conn.execute("SELECT event, detail FROM journal ORDER BY id")}


# --- 1. הרזרבה: הערך וההנמקה ---------------------------------------------------


def test_the_reserve_is_ten_gib_and_covers_what_its_docstring_counts():
    """ההנמקה ב-disk_reserve.py נשענת על מספרים מקובץ ההתקנה של journald —
    אם מישהו ישנה אותם, הטסט נופל ולא ההנמקה בשקט."""
    assert R == 10 * 1024 ** 3
    conf = (REPO / "deploy" / "journald-imagesrv.conf").read_text(encoding="utf-8")
    values = dict(re.findall(r"^(\w+)=(\S+)$", conf, flags=re.M))
    assert values == {"SystemMaxUse": "1G", "SystemKeepFree": "4G", "MaxRetentionSec": "90day"}
    gib = 1024 ** 3
    # journald keep-free 4 + journald max 1 + עדכוני מערכת 2 + גרסה 1 + DB/tmp 1
    assert R >= (4 + 1 + 2 + 1 + 1) * gib


@pytest.mark.parametrize("free,need,refused", [
    (R + 100, 100, False), (R + 99, 100, True), (R, 0, False), (R - 1, 0, True),
])
def test_refusal_is_exactly_at_the_reserve(free, need, refused):
    text = disk_reserve.refusal(free, need, "העלאת האימג'")
    assert (text is not None) == refused
    if refused:
        assert text.startswith("אין מקום בשרת להעלאת האימג': ")
        assert f"({need} בייט)" in text and f"({free} בייט)" in text
        assert "נשמרים למערכת" in text


def test_a_stream_guard_that_cannot_measure_stops_the_stream():
    """עיקרון 5: "לא נמדד" אינו "יש מקום"."""
    def broken():
        raise OSError("EIO")
    guard = disk_reserve.StreamGuard(broken, "הקליטה", every=1)
    with pytest.raises(disk_reserve.ReserveReached, match="EIO"):
        guard.wrote(1)


# --- 1ב. העלאת אימג' מהקונסולה -------------------------------------------------


def _archive(tmp_path: Path) -> bytes:
    source = tmp_path / "source"
    write_image(source, {**MANIFEST_256, "id": "img_0a1b2c"})
    return b"".join(tar_stream(source / "img_0a1b2c", "img_0a1b2c"))


def _uploads(root: Path) -> list[Path]:
    return [p for p in root.iterdir() if p.name.startswith(console_library.UPLOAD_PREFIX)]


@pytest.mark.parametrize("spare,status", [(-1, 507), (0, 200)])
def test_an_upload_needs_twice_its_size_plus_the_reserve(server, images_root, tmp_path,
                                                        monkeypatch, spare, status):
    """הקובץ הזמני והחילוץ שלו יושבים יחד על הדיסק — השיא הוא פעמיים."""
    raw = _archive(tmp_path)
    monkeypatch.setattr(console_library, "_library_free", lambda root: R + 2 * len(raw) + spare)
    response = server["admin"].post("/api/console/images/upload", content=raw)
    assert response.status_code == status, response.text
    if status == 507:
        assert "נשמרים למערכת" in response.json()["detail"]
        assert "img_0a1b2c" not in server["ctx"].library.scan()
    assert _uploads(images_root) == []


def test_a_chunked_upload_stops_at_the_reserve_mid_stream(server, images_root, tmp_path,
                                                         monkeypatch):
    raw = _archive(tmp_path)
    monkeypatch.setattr(console_library, "_library_free", lambda root: R - 1)
    monkeypatch.setattr(disk_reserve, "STREAM_CHECK_EVERY", 1)
    response = server["admin"].post("/api/console/images/upload", content=iter([raw]))
    assert response.status_code == 507, response.text
    assert "נעצרה" in response.json()["detail"]
    assert _uploads(images_root) == []


def test_a_chunked_upload_is_checked_again_before_it_is_extracted(server, images_root,
                                                                  tmp_path, monkeypatch):
    raw = _archive(tmp_path)
    monkeypatch.setattr(console_library, "_library_free", lambda root: R + len(raw) - 1)
    response = server["admin"].post("/api/console/images/upload", content=iter([raw]))
    assert response.status_code == 507, response.text
    assert "לחילוץ האימג' שהועלה" in response.json()["detail"]
    assert "img_0a1b2c" not in server["ctx"].library.scan()
    assert _uploads(images_root) == []


def test_the_overview_reports_the_reserve_next_to_free_space(server):
    storage = server["admin"].get("/api/console/overview").json()["storage"]
    assert storage["reserve_bytes"] == R
    assert storage["free_bytes"] >= 0 and storage["total_bytes"] > 0


# --- 2. תקרה לטבלאות -----------------------------------------------------------


def _seed_versions(conn, table: str, column: str) -> None:
    rows = [("aa:00:00:00:00:01", 200, "v1"), ("aa:00:00:00:00:01", 120, "v2"),
            ("aa:00:00:00:00:01", 10, "v3"), ("aa:00:00:00:00:02", 300, "only")]
    for mac, days, body in rows:
        extra = ", sampled_at" if table == "machine_probe" else ""
        marks = ", ?" if table == "machine_probe" else ""
        conn.execute(f"INSERT INTO {table} (mac, seen_at{extra}, {column})"
                     f" VALUES (?, ?{marks}, ?)",
                     (mac, ago(days), *((ago(days),) if extra else ()), f'{{"v": "{body}"}}'))


def _seed(conn) -> None:
    conn.execute("INSERT INTO journal (ts, event, detail) VALUES (?, 'login', 'old')", (ago(400),))
    conn.execute("INSERT INTO journal (ts, event, detail) VALUES (?, 'login', 'kept')", (ago(300),))
    _seed_versions(conn, "machine_probe", "probe_json")
    _seed_versions(conn, "machine_inventory", "inventory_json")
    for mac, days in (("bb:00:00:00:00:01", 100), ("bb:00:00:00:00:02", 5)):
        conn.execute("INSERT INTO boot_steps (mac, step, idx, at, first_at) VALUES (?, 'menu', 1, ?, ?)",
                     (mac, ago(days), ago(days)))
        conn.execute("INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
                     " VALUES (?, 'task:t', 1, ?, ?)", (mac, ago(days), ago(days)))
        conn.execute("INSERT INTO agent_loops (mac, hits, first_at, last_at) VALUES (?, 1, ?, ?)",
                     (mac, ago(days), ago(days)))
        conn.execute("INSERT INTO off_vlan_contacts (mac, address, hits, first_at, last_at)"
                     " VALUES (?, '10.0.0.1', 1, ?, ?)", (mac, ago(days), ago(days)))
    disk = ("INSERT INTO disk_events (session_id, mac, disk, serial, verdict, reason, crc, updated_at)"
            " VALUES (?, 'cc:00:00:00:00:01', 'sda', ?, ?, ?, ?, ?)")
    conn.execute(disk, ("s_old2", "S1", "ok", None, 3, ago(120)))     # ישן, לא הבסיס
    conn.execute(disk, ("s_old1", "S1", "ok", None, 5, ago(100)))     # ישן — אבל הבסיס של S1
    conn.execute(disk, ("s_new", "S1", "unchecked", "timeout", 0, ago(5)))  # לא קריא — לא בסיס
    conn.execute(disk, ("s_old3", None, "ok", None, 1, ago(100)))     # בלי סידורי
    fail = ("INSERT INTO disk_failures (session_id, mac, dev, serial, at, cause, cleared_at)"
            " VALUES (?, 'cc:00:00:00:00:01', ?, 'S9', ?, 'disk', ?)")
    conn.execute(fail, ("f1", "sda", ago(300), None))        # פתוחה — צובעת, נשארת
    conn.execute(fail, ("f2", "sdb", ago(300), ago(100)))    # נוקתה מזמן
    conn.execute(fail, ("f3", "sdc", ago(300), ago(10)))     # נוקתה לאחרונה
    for n in range(1, 56):                                   # 55 גלים ישנים, בסדר
        conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?, '{}', ?)",
                     (f"w{n}", ago(200 - n)))
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES ('w_new', '{}', ?)",
                 (ago(1),))
    rate = ("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes,"
            " seconds, at) VALUES (?, 'M', ?, ?, 100000000, 2000000000, 100, ?)")
    for n in range(1, 8):                                    # X: גלים w1..w7
        conn.execute(rate, ("X", f"w{n}", 20_000_000 + n, ago(200 - n)))
    for n in range(8, 14):                                   # Y: גלים w8..w13
        conn.execute(rate, ("Y", f"w{n}", 30_000_000 + n, ago(200 - n)))
    login = "INSERT INTO login_attempts (key, failures, locked_until, last_at) VALUES (?, ?, ?, ?)"
    t = NOW.timestamp()
    conn.execute(login, ("quiet|1.1.1.1", 3, None, t - 100 * 86400))
    conn.execute(login, ("locked|1.1.1.1", 10, t + 600, t - 100 * 86400))
    conn.execute(login, ("slow|1.1.1.1", 40, None, t - 100 * 86400))   # השהיה של 2^35 שניות
    conn.execute(login, ("recent|1.1.1.1", 3, None, t - 10 * 86400))
    conn.execute("INSERT INTO users (username, pw_hash, role, created_at) VALUES ('u', 'x', 'admin', ?)",
                 (ago(1),))
    conn.execute("INSERT INTO mfa_challenges (token_hash, username, expires_at) VALUES ('old', 'u', ?)",
                 (t - 60,))
    conn.execute("INSERT INTO mfa_challenges (token_hash, username, expires_at) VALUES ('live', 'u', ?)",
                 (t + 60,))
    conn.commit()


def _readers(conn) -> dict:
    """מה שהקוד קורא כ"מצב נוכחי" — חייב להיות זהה לפני הניקוי ואחריו."""
    return {
        "probe": probe.latest_all(conn),
        "inventory": inventory.latest_all(conn),
        "crc_S1": room_precheck.crc_baseline(conn, "S1"),
        "limit_X": drive_rates._limit_of(conn, "X", "M"),
        "limit_Y": drive_rates._limit_of(conn, "Y", "M"),
        "wave_memory": [r["session_id"] for r in conn.execute(
            "SELECT session_id FROM wave_rates ORDER BY rowid DESC LIMIT ?",
            (drive_rates.MEMORY_SCAN,))],
        "open_failures": [r["session_id"] for r in conn.execute(
            "SELECT session_id FROM disk_failures WHERE cleared_at IS NULL")],
    }


def _count(conn, table: str, where: str = "1", params=()) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


def test_retention_deletes_the_old_and_every_reader_answers_the_same(conn):
    _seed(conn)
    before = _readers(conn)

    result = housekeeping.purge_tables(conn, NOW)

    assert result["failed"] == {}
    assert _readers(conn) == before, "a purge changed what the code reads"
    assert result["deleted"] == {
        "journal": 1, "machine_probe": 1, "machine_inventory": 1, "boot_steps": 1,
        "boot_attempts": 1, "agent_loops": 1, "off_vlan_contacts": 1, "disk_events": 2,
        "disk_failures": 1, "drive_rates": 2, "wave_rates": 6, "mfa_challenges": 1,
        "login_attempts": 1,
    }
    assert [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event='login'")] == ["kept"]
    assert _count(conn, "machine_probe", "mac = 'aa:00:00:00:00:02'") == 1   # האחרונה — גם כשישנה
    assert _count(conn, "disk_events", "serial = 'S1'") == 2
    assert _count(conn, "disk_failures", "session_id = 'f1'") == 1
    # ‏w8 של Y: מחוץ ל-WINDOW של הכונן, אבל הגל עוד בזיכרון הקצב (‏MEMORY_SCAN) — נשאר
    assert _count(conn, "drive_rates", "serial = 'Y'") == 6
    assert _count(conn, "drive_rates", "serial = 'X'") == drive_rates.WINDOW
    assert [r[0] for r in conn.execute("SELECT key FROM login_attempts ORDER BY key")] == [
        "locked|1.1.1.1", "recent|1.1.1.1", "slow|1.1.1.1"]


def test_every_purge_is_journaled_with_its_numbers(conn):
    _seed(conn)
    housekeeping.purge_tables(conn, NOW)
    detail = events(conn)["retention_purged"]
    assert "journal=1" in detail and "wave_rates=6" in detail and "disk_events=2" in detail
    assert "90 יום" in detail and "365 יום" in detail


def test_a_second_run_deletes_nothing_and_writes_nothing(conn):
    _seed(conn)
    housekeeping.purge_tables(conn, NOW)
    rows = _count(conn, "journal")
    assert housekeeping.purge_tables(conn, NOW) == {"deleted": {}, "failed": {}}
    assert _count(conn, "journal") == rows


def test_a_table_that_fails_is_named_and_does_not_stop_the_rest(conn, monkeypatch):
    _seed(conn)
    real = housekeeping._rules
    monkeypatch.setattr(housekeeping, "_rules",
                        lambda now: [("no_such_table", "DELETE FROM no_such_table", ())] + real(now))
    result = housekeeping.purge_tables(conn, NOW)
    assert "no_such_table" in result["failed"]
    assert result["deleted"]["journal"] == 1
    assert "no_such_table" in events(conn)["retention_failed"]


def test_a_timestamp_that_is_not_iso_is_kept_not_guessed(conn):
    conn.execute("INSERT INTO boot_steps (mac, step, idx, at, first_at)"
                 " VALUES ('dd:00:00:00:00:01', 'menu', 1, 'yesterday', 'x')")
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    assert _count(conn, "boot_steps") == 1


# --- 3. ‏staging נטוש ----------------------------------------------------------


def age(path: Path, seconds: float, now: float) -> None:
    """מזיז את זמן הכתיבה של כל העץ אחורה."""
    stamp = now - seconds
    for top, dirs, files in os.walk(path):
        for name in dirs + files:
            os.utime(os.path.join(top, name), (stamp, stamp))
    os.utime(path, (stamp, stamp))


def area(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "p1.esp.pcl.zst").write_bytes(b"x" * 1024)
    return path


def add_task(conn, task_id: str, state: str) -> None:
    conn.execute("INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by,"
                 " created_at, updated_at) VALUES (?, 'aa:bb:cc:00:00:10', 'capture', 'sda',"
                 " 'img_a8df93', 'W', ?, 'nadav', ?, ?)", (task_id, state, ago(2), ago(2)))
    conn.commit()


DAY = 24 * 3600


def test_stale_orphans_without_an_owner_are_swept_after_a_day(conn, tmp_path):
    root = tmp_path / "images"
    now = time.time()
    for path in (area(root, ".capture-tsk_gone"), area(root, ".import-4b1e77a2"),
                 area(root, ".incoming/img_aaaaaa")):
        age(path, DAY + 60, now)
    upload = root / ".upload-abc123_x.upload"
    upload.write_bytes(b"y" * 2048)
    age(upload, DAY + 60, now)

    result = work_areas.sweep(conn, root, now)

    assert sorted(result["swept"]) == ["import:4b1e77a2", "incoming:img_aaaaaa",
                                       "tsk_gone", "upload:abc123_x"]
    assert result["freed_bytes"] == 3 * 1024 + 2048
    assert sorted(p.name for p in root.iterdir()) == [".incoming"]   # המנגנון עצמו נשאר
    assert "swept=4" in events(conn)["work_area_swept"]


def test_the_same_orphans_are_kept_while_anything_under_them_was_written_today(conn, tmp_path):
    """הראיה היא הכתיבה האחרונה **בכל העץ**: תיקייה ישנה עם קובץ שגדל עכשיו חיה."""
    root = tmp_path / "images"
    now = time.time()
    paths = [area(root, ".capture-tsk_gone"), area(root, ".import-4b1e77a2"),
             area(root, ".incoming/img_aaaaaa")]
    for path in paths:
        age(path, DAY + 60, now)
        os.utime(path / "p1.esp.pcl.zst", (now - 60, now - 60))
    (root / ".upload-abc123_x.upload").write_bytes(b"y")

    result = work_areas.sweep(conn, root, now)

    assert result["swept"] == []
    assert all(p.exists() for p in paths)


def test_an_open_task_is_kept_however_quiet_it_is(conn, tmp_path):
    """"רק אחרי שאומת שהמשימה סגורה": משימה פתוחה עשויה לחזור אחרי אתחול."""
    root = tmp_path / "images"
    now = time.time()
    add_task(conn, "tsk_run", "running")
    add_task(conn, "tsk_ver", "verifying")
    for name in (".capture-tsk_run", ".capture-tsk_ver"):
        age(area(root, name), 30 * DAY, now)
    result = work_areas.sweep(conn, root, now)
    assert result["swept"] == []
    assert dict(result["kept"]) == {".capture-tsk_run": "המשימה עדיין running",
                                    ".capture-tsk_ver": "האימג' ממתין לאימות"}


def test_an_incoming_area_of_an_active_pull_is_kept_however_quiet(conn, tmp_path):
    root = tmp_path / "images"
    now = time.time()
    conn.execute("INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
                 " VALUES ('n1', 'חיפה', 'https://10.0.0.2:8443', 'c', ?)", (ago(1),))
    conn.execute("INSERT INTO storage_transfers (id, node_id, image_id, image_name, state,"
                 " started_by, created_at, updated_at, direction) VALUES"
                 " ('t1', 'n1', 'img_bbbbbb', 'B', 'sending', 'noc', ?, ?, 'pull')",
                 (ago(2), ago(2)))
    conn.commit()
    age(area(root, ".incoming/img_bbbbbb"), 3 * DAY, now)
    result = work_areas.sweep(conn, root, now)
    assert result["swept"] == []
    assert (".incoming/img_bbbbbb", "משיכה פעילה") in result["kept"]


def test_images_are_never_swept_however_old(conn, tmp_path):
    """עיקרון 3, ו-#1737 סעיף 6: אימג'ים לא נמחקים אוטומטית לעולם."""
    root = tmp_path / "images"
    write_image(root, MANIFEST_256)
    (root / "capture-tsk_x").mkdir()          # בלי נקודה — לא אזור עבודה
    now = time.time()
    for path in root.iterdir():
        age(path, 3650 * DAY, now)
    result = housekeeping.run(conn, lambda: [root], datetime.fromtimestamp(now, timezone.utc))
    assert result["work_areas"][str(root)]["swept"] == []
    assert (root / MANIFEST_256["id"] / "manifest.json").is_file()
    assert (root / "capture-tsk_x").is_dir()


def test_the_work_area_names_match_what_the_server_actually_creates(tmp_path):
    """שני עותקים של אותה מוסכמת שם — אם אחד ישתנה, הסחיפה תפסיק לזהות, בשקט."""
    handle = tempfile.NamedTemporaryFile(delete=False, dir=tmp_path,
                                         prefix=console_library.UPLOAD_PREFIX, suffix=".upload")
    handle.close()
    assert work_areas.UPLOAD_AREA.match(Path(handle.name).name)
    assert work_areas.IMPORT_AREA.match(archive.STAGING_PREFIX + "0a1b2c3d")
    assert work_areas.INCOMING == storage_transfer.INCOMING
    assert work_areas.PULL_ACTIVE == storage_transfer.ACTIVE_STATES


# --- 4. פעם ביום, מהשעון הקיים --------------------------------------------------


def test_the_schedule_runs_first_after_startup_then_daily():
    schedule = housekeeping.Schedule(first=2, every=5)
    assert [schedule.due(1) for _ in range(2)] == [False, True]
    assert [schedule.due(1) for _ in range(5)] == [False, False, False, False, True]


def test_the_defaults_are_daily_and_soon_after_startup():
    assert housekeeping.EVERY_S == 24 * 3600
    assert 0 < housekeeping.FIRST_RUN_S <= 3600
    assert housekeeping.RETENTION_DAYS == 90 and housekeeping.JOURNAL_DAYS == 365
    assert work_areas.STALE_AFTER_S == 24 * 3600


def test_the_room_clock_runs_housekeeping_in_the_background(tmp_path, monkeypatch):
    """שעון שקיים ואינו מחובר נראה בדיוק כמו שעון שעובד (עיקרון 5): יתום שנוצר
    **אחרי** העלייה נסחף מהסיבוב הרקע, לא מהסחיפה של העלייה."""
    from fastapi.testclient import TestClient
    from conftest import Clock
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    fired = threading.Event()
    real = housekeeping.run

    def counting(conn, roots, now=None):
        result = real(conn, roots, now)
        fired.set()
        return result

    monkeypatch.setattr(housekeeping, "run", counting)
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080",
                     now_fn=Clock(), room_clock_interval=0.02,
                     housekeeping_first=0.05, housekeeping_every=3600)
    orphan = area(images, ".capture-tsk_late")
    age(orphan, DAY + 60, time.time())
    try:
        with TestClient(app):
            assert fired.wait(timeout=10.0), "the daily round never ran"
    finally:
        app.state.ctx.sender.stop()
    assert not orphan.exists()
    assert "tsk_late" in events(app.state.ctx.conn)["work_area_swept"]


# --- 5. ‏journald -------------------------------------------------------------


def test_new_servers_get_the_journal_ceiling_at_install():
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert ('run install -D -m 0644 "$APP_DIR/deploy/journald-imagesrv.conf"'
            " /etc/systemd/journald.conf.d/90-imagesrv.conf") in setup
    assert "systemctl restart systemd-journald || warn" in setup
