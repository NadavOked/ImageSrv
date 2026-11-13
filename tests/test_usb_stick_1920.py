"""#1920 -- the clone stick plugged into the server: page API, plan, and the one approved sync.

The device is never real here: ``usb_hooks`` hands the app a directory in place of a mounted
IMGSRV_DATA, and records how it was opened (read-only or read-write). What is proved:

* admin only (deploy 403, anonymous 401) on both paths;
* looking at the stick (GET) writes NOTHING -- it is only ever opened read-only, and a stick whose
  files are hashed before and after is byte-identical;
* a sync writes only with the digest of the summary the operator saw and ``confirm: true``; a stale
  digest, a stick that changed, a stick that went away, or a second sync at once all stop with
  nothing written;
* it writes ONLY ``server-sync/`` on the stick -- ``reports/`` and ``settings.json`` stay byte for byte;
* a record is taken once, stamped source=usb with the stick's id, never overwrites what the
  server measured, and a serial in a record rejects that record by name while the others go in;
* what goes to the stick is data about computer models and the SMART table -- no disk serial anywhere,
  and every file is read back and compared before the sync says "ok".
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from server.usbstick import device, knowledge, records, sync  # noqa: E402

BASE = "/api/console/usb-stick"
GOOD = {
    "mac": "", "source": {"dev": "sda", "port": 1},
    "targets": [{"dev": "sdb", "port": 2, "state": "done", "error": "", "ntfs_note": ""}],
    "shrunk": False, "source_restore": "", "stopped": False, "bytes": 85_000_000_000, "elapsed_s": 1500,
    "shrink_path": None, "snapshot_teardown": "", "info_s": 12, "resize_s": None, "relocations_mb": None,
    "copy_s": 1000, "verify_s": 400, "restore_s": None,
    "machine": {"vendor": "HP", "product": "HP Compaq 8000 Elite SFF PC"}, "controller": "ide|sda=ata_piix"}
STICK = device.Stick(device_id="usb-0123456789AB", label="IMGSRV_DATA", fstype="vfat", size_bytes=15_000_000_000,
                     vendor="SanDisk", model="Cruzer", partition="/dev/sdb2", by_id=None, has_serial=True)


class FakeStick:
    """A directory standing for the mounted IMGSRV_DATA; records each open (write or not)."""

    def __init__(self, root: Path):
        self.root = root
        self.scan_result = device.Scan(device.CONNECTED, stick=STICK)
        self.opens: list[bool] = []
        self.open_error: Exception | None = None
        self.write_error: Exception | None = None
        self.corrupt_after_write: str | None = None

    def scan(self):
        return self.scan_result

    @contextmanager
    def open(self, stick, *, write=False):
        self.opens.append(write)
        if self.open_error is not None:
            raise self.open_error
        if write and self.write_error is not None:
            raise self.write_error
        yield self.root
        if write and self.corrupt_after_write:
            target = self.root / knowledge.SYNC_DIR / self.corrupt_after_write
            target.write_bytes(target.read_bytes() + b"x")

    def report(self, stamp: str, **over) -> str:
        name = f"clone-{stamp}.json"
        (self.root / "reports").mkdir(exist_ok=True)
        (self.root / "reports" / name).write_text(json.dumps({**GOOD, **over}), encoding="utf-8")
        return name

    def snapshot(self, skip_sync_dir=False) -> dict[str, str]:
        out = {}
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if path.is_file() and not (skip_sync_dir and rel.startswith(knowledge.SYNC_DIR + "/")):
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out


@pytest.fixture()
def env(tmp_path: Path, images_root: Path, clock):
    from server.app import create_app
    from server.console import users

    root = tmp_path / "stick"
    root.mkdir()
    fake = FakeStick(root)
    (root / "settings.json").write_text('{"cloner_beep": true}', encoding="utf-8")
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     usb_hooks={"scan": fake.scan, "open": fake.open})
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    return {"admin": admin, "deploy": deploy, "anon": TestClient(app), "fake": fake, "conn": conn}


def three_reports(fake: FakeStick) -> None:
    fake.report("20261106-184400")
    fake.report("20261106-160200", copy_s=800, machine={"vendor": "HP", "product": "HP EliteDesk 8100 SFF"},
                controller="other|sda=ahci")
    # a failed clone on the same model and controller, at a very different speed: it must not move that model's rate
    fake.report("20261105-112000", copy_s=200, targets=[{"dev": "sdb", "port": 2, "state": "failed", "error": "x"}])


def get(env) -> dict:
    r = env["admin"].get(BASE)
    assert r.status_code == 200, r.text
    return r.json()


def sync_now(env, **over):
    plan = get(env)["plan"]
    return env["admin"].post(BASE + "/sync", json={"digest": plan["digest"], "confirm": True, **over})


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- who may ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [("get", BASE), ("post", BASE + "/sync")])
def test_deploy_gets_403_and_anonymous_401(env, method, path):
    body = {"json": {"digest": "x", "confirm": True}} if method == "post" else {}
    assert getattr(env["deploy"], method)(path, **body).status_code == 403
    assert getattr(env["anon"], method)(path, **body).status_code == 401
    assert env["fake"].opens == []


# --- looking is not writing ----------------------------------------------------------------------------


def test_nothing_plugged_in_is_said_and_nothing_is_opened(env):
    env["fake"].scan_result = device.Scan(device.NONE)
    got = get(env)
    assert got["state"] == "none" and got["device"] is None and got["plan"] is None and got["rows"] == []
    assert env["fake"].opens == []


@pytest.mark.parametrize("state", ["unsafe", "ambiguous", "unread"])
def test_a_device_that_is_not_safe_or_not_known_is_not_opened(env, state):
    env["fake"].scan_result = device.Scan(state, reason="הסיבה בשם")
    got = get(env)
    assert got["state"] == state and got["reason"] == "הסיבה בשם" and got["plan"] is None
    assert env["fake"].opens == []


def test_the_page_reads_the_stick_read_only_and_leaves_every_file_as_it_was(env):
    fake = env["fake"]
    three_reports(fake)
    before = fake.snapshot()
    got = get(env)
    assert got["state"] == "connected" and got["device"]["device_id"] == "usb-0123456789AB"
    assert fake.opens and not any(fake.opens), "the page opened the stick for writing"
    assert fake.snapshot() == before
    assert count(env["conn"], "usb_records") == 0 and count(env["conn"], "usb_syncs") == 0


def test_the_plan_counts_new_known_and_rejected_and_the_rows_say_which_is_in_the_server(env):
    fake = env["fake"]
    three_reports(fake)
    fake.report("20261104-090000", serial="WD-X1")
    got = get(env)
    plan = got["plan"]["to_server"]
    assert plan["new"] == 3 and plan["already"] == 0 and plan["total"] == 4
    assert plan["rejected"] == [{"name": "clone-20261104-090000.json", "reason": "שדה serial אסור בסכמה"}]
    assert [r["recorded_at"] for r in got["rows"]] == ["2026-11-06T18:44:00Z", "2026-11-06T16:02:00Z", "2026-11-05T11:20:00Z"]
    assert not any(r["in_server"] for r in got["rows"])
    assert got["plan"]["nothing_to_do"] is False and got["plan"]["to_stick"]["files"]


def test_the_log_that_cannot_be_read_is_an_error_with_the_reason_not_an_empty_log(env):
    env["fake"].open_error = device.StickError("העיגון נכשל: boom")
    got = get(env)
    assert got["state"] == "connected" and got["plan"] is None and "העיגון נכשל" in got["read_error"]


# --- the sync ---------------------------------------------------------------------------------------------


def test_a_sync_without_confirm_or_with_a_stale_digest_writes_nothing(env):
    fake = env["fake"]
    three_reports(fake)
    before = fake.snapshot()
    digest = get(env)["plan"]["digest"]
    fake.opens.clear()
    assert env["admin"].post(BASE + "/sync", json={"digest": digest}).status_code == 400
    assert env["admin"].post(BASE + "/sync", json={"digest": digest, "confirm": False}).status_code == 400
    stale = env["admin"].post(BASE + "/sync", json={"digest": "0" * 32, "confirm": True})
    assert stale.status_code == 409 and stale.json()["code"] == "usb_stick_plan_changed"
    assert not any(fake.opens) and fake.snapshot() == before
    assert count(env["conn"], "usb_records") == 0


def test_a_stick_that_changed_after_the_summary_is_a_new_plan_not_a_write(env):
    fake = env["fake"]
    three_reports(fake)
    digest = get(env)["plan"]["digest"]
    fake.report("20261107-080000")                    # someone cloned in the meantime
    before = fake.snapshot()
    r = env["admin"].post(BASE + "/sync", json={"digest": digest, "confirm": True})
    assert r.status_code == 409 and r.json()["code"] == "usb_stick_plan_changed"
    assert fake.snapshot() == before and count(env["conn"], "usb_records") == 0


def test_a_stick_that_went_away_is_refused_before_anything_is_opened(env):
    three_reports(env["fake"])
    digest = get(env)["plan"]["digest"]
    env["fake"].opens.clear()
    env["fake"].scan_result = device.Scan(device.NONE)
    r = env["admin"].post(BASE + "/sync", json={"digest": digest, "confirm": True})
    assert r.status_code == 409 and r.json()["code"] == "usb_stick_none" and env["fake"].opens == []


def test_a_sync_imports_once_writes_only_server_sync_and_reads_it_back(env):
    fake, conn = env["fake"], env["conn"]
    three_reports(fake)
    before = fake.snapshot()
    done = sync_now(env)
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["state"] == "ok" and out["by"] == "noc"
    assert out["to_server"]["imported"] == 3 and out["to_server"]["rejected"] == []
    assert out["to_stick"]["verified"] is True
    assert sorted(out["to_stick"]["written"]) == ["hw-issues.tsv", "manifest.json", "rates.json", "smart-knowledge.tsv"]
    after = fake.snapshot()
    # everything that was on the stick is byte for byte what it was; the only new files are server-sync/*
    assert {k: v for k, v in after.items() if not k.startswith("server-sync/")} == before
    assert sorted(k for k in after if k.startswith("server-sync/")) == [
        "server-sync/hw-issues.tsv", "server-sync/manifest.json", "server-sync/rates.json", "server-sync/smart-knowledge.tsv"]
    rows = [dict(r) for r in conn.execute("SELECT * FROM usb_records ORDER BY recorded_at")]
    assert len(rows) == 3
    assert {r["source"] for r in rows} == {"usb"} and {r["device_id"] for r in rows} == {"usb-0123456789AB"}
    assert {r["imported_by"] for r in rows} == {"noc"} and all(r["imported_at"] for r in rows)
    ev = conn.execute("SELECT event, detail, user FROM journal WHERE event = 'usb_stick_sync'").fetchone()
    assert ev and ev["user"] == "noc" and "נקלטו 3" in ev["detail"]
    assert get(env)["last_sync"]["state"] == "ok"


def test_the_stick_is_opened_for_writing_only_by_the_confirmed_sync_and_then_read_back_read_only(env):
    three_reports(env["fake"])
    get(env)
    assert env["fake"].opens == [False]
    sync_now(env)
    assert env["fake"].opens == [False, False, False, True, False]   # page, the sheet's plan, the sync's plan, write, read back


def test_the_same_records_are_never_taken_twice_and_a_second_sync_has_nothing_to_do(env):
    fake, conn = env["fake"], env["conn"]
    three_reports(fake)
    assert sync_now(env).json()["to_server"]["imported"] == 3
    again = get(env)
    assert again["plan"]["nothing_to_do"] is True and again["plan"]["to_server"]["new"] == 0
    assert again["plan"]["to_server"]["already"] == 3 and again["plan"]["to_stick"]["nothing_to_write"] is True
    assert all(r["in_server"] and r["imported_at"] for r in again["rows"])
    fake.opens.clear()
    second = sync_now(env).json()
    assert second["to_server"]["imported"] == 0 and second["to_stick"]["skipped"] is True
    assert True not in fake.opens, "nothing changed, so nothing was written"
    assert count(conn, "usb_records") == 3


def test_a_record_with_a_serial_is_rejected_by_name_while_the_rest_go_in_and_the_state_is_partial(env):
    fake, conn = env["fake"], env["conn"]
    three_reports(fake)
    fake.report("20261104-090000", **{"source": {"dev": "sda", "port": 1, "serial": "WD-X1"}})
    out = sync_now(env).json()
    assert out["state"] == "partial"
    assert out["to_server"]["imported"] == 3
    assert out["to_server"]["rejected"] == [{"name": "clone-20261104-090000.json", "reason": "שדה serial אסור בסכמה"}]
    assert count(conn, "usb_records") == 3
    assert "WD-X1" not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM usb_records")])
    assert get(env)["last_sync"]["to_server"]["rejected"][0]["reason"] == "שדה serial אסור בסכמה"


def test_what_the_server_measured_itself_is_not_touched_by_an_import(env):
    conn = env["conn"]
    conn.execute("INSERT INTO machine_inventory (mac, seen_at, inventory_json) VALUES (?, ?, ?)",
                 ("aa:bb:cc:00:00:01", "2026-11-01T00:00:00+00:00",
                  json.dumps({"dmi": {"product_name": "HP Compaq 8000 Elite SFF PC", "sys_vendor": "HP"}})))
    for n, mbps in enumerate((100, 110, 120)):
        conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (f"s{n}", f"SER{n}", "ST500", "aa:bb:cc:00:00:01", mbps, "2026-11-01T00:00:00+00:00"))
    conn.commit()
    speeds = [tuple(r) for r in conn.execute("SELECT * FROM model_speeds ORDER BY session_id")]
    three_reports(env["fake"])
    sync_now(env)
    assert [tuple(r) for r in conn.execute("SELECT * FROM model_speeds ORDER BY session_id")] == speeds
    assert count(conn, "drive_rates") == 0 and count(conn, "wave_rates") == 0


def test_the_rates_sent_back_say_what_each_number_is_and_carry_no_disk_identity(env):
    fake, conn = env["fake"], env["conn"]
    conn.execute("INSERT INTO machine_inventory (mac, seen_at, inventory_json) VALUES (?, ?, ?)",
                 ("aa:bb:cc:00:00:01", "2026-11-01T00:00:00+00:00",
                  json.dumps({"dmi": {"product_name": "HP EliteDesk 8100 SFF", "sys_vendor": "HP"}})))
    for n, mbps in enumerate((100, 110, 120)):
        conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (f"s{n}", f"SER{n}", "ST500", "aa:bb:cc:00:00:01", mbps, "2026-11-01T00:00:00+00:00"))
    conn.commit()
    three_reports(fake)
    sync_now(env)
    raw = (fake.root / "server-sync" / "rates.json").read_text(encoding="utf-8")
    doc = json.loads(raw)
    assert doc["schema"] == 1 and doc["kind"] == "imagesrv-rates"
    by = {(e["model"], e["controller"], e["basis"]): e for e in doc["entries"]}
    assert by[("HP EliteDesk 8100 SFF", None, "preflight")] == {
        "model": "HP EliteDesk 8100 SFF", "controller": None, "basis": "preflight", "source": "server", "mbps": 110, "samples": 3}
    assert by[("HP Compaq 8000 Elite SFF PC", "ide", "copy")]["mbps"] == 85
    assert by[("HP Compaq 8000 Elite SFF PC", "ide", "copy")]["samples"] == 1      # the failed clone (425 MB/s) is no sample
    assert by[("HP EliteDesk 8100 SFF", "ahci", "copy")]["mbps"] == 106
    assert ("HP Compaq 8000 Elite SFF PC", "ide", "copy") in by and len(by) == 3      # the failed clone is no rate
    for needle in ("SER0", "ST500", "serial", "smart", "aa:bb:cc"):
        assert needle not in raw, needle


def test_the_smart_table_goes_byte_for_byte_and_the_manifest_vouches_for_every_file(env):
    fake = env["fake"]
    three_reports(fake)
    sync_now(env)
    out = fake.root / "server-sync"
    assert (out / "smart-knowledge.tsv").read_bytes() == knowledge.SMART_TABLE.read_bytes()
    assert (out / "hw-issues.tsv").read_bytes() == knowledge.HW_TABLE.read_bytes()
    manifest = knowledge.parse_manifest((out / "manifest.json").read_bytes())
    assert manifest["device_id"] == "usb-0123456789AB" and manifest["smart_table_version"] == knowledge.smart_version(
        knowledge.SMART_TABLE.read_bytes(), knowledge.HW_TABLE.read_bytes())
    for name, meta in manifest["files"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == meta["sha256"], name
    assert sorted(manifest["files"]) == ["hw-issues.tsv", "rates.json", "smart-knowledge.tsv"]
    assert not list(out.glob("*.new")), "a temporary file was left on the stick"


def test_a_write_that_fails_is_said_and_the_import_that_did_happen_is_not_hidden(env):
    fake, conn = env["fake"], env["conn"]
    three_reports(fake)
    fake.write_error = device.StickError("העיגון נכשל: Read-only file system")
    out = sync_now(env).json()
    assert out["state"] == "partial"
    assert out["to_server"]["imported"] == 3
    assert "הכתיבה להתקן נכשלה" in out["to_stick"]["error"] and "Read-only" in out["to_stick"]["error"]
    assert count(conn, "usb_records") == 3 and not (fake.root / "server-sync").exists()


def test_a_file_that_does_not_read_back_as_written_is_not_ok(env):
    fake = env["fake"]
    three_reports(fake)
    fake.corrupt_after_write = "rates.json"
    out = sync_now(env).json()
    assert out["state"] == "partial" and "rates.json" in out["to_stick"]["error"]
    assert "verified" not in out["to_stick"]


def test_a_db_failure_on_import_does_not_write_the_stick_and_is_failed(env, monkeypatch):
    import sqlite3
    three_reports(env["fake"])
    monkeypatch.setattr(sync, "import_records", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    out = sync_now(env).json()
    # #2543: the sqlite text goes to the server log; the operator reads our sentence.
    assert out["state"] == "failed" and "הפרטים ביומן השרת" in out["to_server"]["error"]
    assert "disk I/O error" not in out["to_server"]["error"]
    assert "הקליטה בשרת נכשלה" in out["to_stick"]["error"] and not (env["fake"].root / "server-sync").exists()


def test_the_import_is_all_or_none(env):
    """One bad row in the batch (a result the table does not allow) leaves no record behind."""
    three_reports(env["fake"])
    recs = sync.plan(env["conn"], {"open": env["fake"].open}, STICK).new
    import dataclasses
    broken = [recs[0], dataclasses.replace(recs[1], result="bogus")]
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        sync.import_records(env["conn"], broken, "usb-x", "noc")
    assert count(env["conn"], "usb_records") == 0


def test_a_second_sync_at_the_same_time_is_refused(env):
    three_reports(env["fake"])
    digest = get(env)["plan"]["digest"]
    assert sync.LOCK.acquire(blocking=False)
    try:
        busy = env["admin"].post(BASE + "/sync", json={"digest": digest, "confirm": True})
        assert busy.status_code == 409 and busy.json()["code"] == "usb_stick_busy"
        assert get(env)["state"] == "busy"
    finally:
        sync.LOCK.release()
    assert count(env["conn"], "usb_records") == 0


def test_a_bad_body_is_a_422_and_never_a_write(env):
    three_reports(env["fake"])
    env["fake"].opens.clear()
    assert env["admin"].post(BASE + "/sync", json={}).status_code == 422
    assert env["admin"].post(BASE + "/sync", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert env["fake"].opens == []


def test_the_download_row_of_the_settings_page_now_lives_here_and_the_api_stays(env):
    """#1914's /usb-clone* paths are unchanged: the page only moved the button."""
    assert env["admin"].get("/api/console/usb-clone").status_code == 200
    assert records.REPORTS_DIR == "reports" and knowledge.SYNC_DIR == "server-sync"
