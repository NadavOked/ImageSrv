"""#1956 -- choosing where the hourly database backup goes (Nadav 22/10).

The decision: three kinds of destination -- (1) a local folder, with an orange
"אותו דיסק — לא מגן מתקלת דיסק" when it sits on the database's disk; (2) an SMB
share with a user and password; (3) NFS. On every one of them a test file is
written **and read back** while the destination is being set, so a destination
that does not take a file is refused now and not at the first backup an hour later
(principle 5). The SMB password is kept the way the server already keeps it for
storage locations (#1123): a 0600 ``credentials=`` file under /etc/imagectl/creds,
never in the database and never in a mount option.

Every system tool runs through the storage hooks injected into ``create_app``
(``FakeHooks`` from test_storage_locations); nothing here mounts anything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from native import requires_native
from server import backup_job, db
from server import storage_locations as sl
from server.storage_locations import FAILED
from test_storage_locations import FakeHooks

try:
    from server import backup_target
except ImportError:   # the server before #1956: the endpoint answers 404 and these say nothing, so every
    # assertion below fails on what the server does -- not on a missing module (bakara-shlilit)
    from types import SimpleNamespace
    backup_target = SimpleNamespace(same_disk_devs=lambda *a: None, same_disk=lambda a, b: None,
                                    _read_back=lambda path: b"")

REPO = Path(__file__).resolve().parent.parent
TARGET = "backup_target"          # the setting -- JSON, no secret
MOUNT_NAME, TEST_NAME = ".db-backup", ".db-backup-test"     # under <data_dir>/storage, which the backups skip
NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
SAME_DISK = "אותו דיסק — לא מגן מתקלת דיסק"
PASSWORD = "Nas-Secret-77"
SMB = {"server": "10.44.10.20", "share": "dbbackup", "username": "imagesrv", "secret": PASSWORD, "domain": "COLLEGE"}
NFS = {"server": "10.44.10.21", "export": "/pool/dbbackup", "version": "4.1"}


@pytest.fixture()
def bk(tmp_path: Path, images_root: Path, clock, monkeypatch):
    from server import sender as sender_module
    from server import users
    from server.app import create_app
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    fake = FakeHooks()
    fake.mounted = set()                      # what the fake mount "mounted" -- the is_mount evidence

    def mount(fstype, source, target, opts=""):
        result = FakeHooks.mount(fake, fstype, source, target, opts)
        if result.status == "ok":
            fake.mounted.add(str(target))
        return result

    def umount(target):
        fake.mounted.discard(str(target))
        return FakeHooks.umount(fake, target)

    hooks = {**fake.as_dict(), "mount": mount, "umount": umount,
             "is_mount": lambda path: str(path) in fake.mounted}
    data = tmp_path / "data"
    app = create_app(data, images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), storage_hooks=hooks,
                     storage_check_interval=10**9)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    yield {"ctx": ctx, "conn": ctx.conn, "admin": admin, "deploy": deploy, "fake": fake, "data": data,
           "tmp": tmp_path}
    ctx.sender.stop()


def _set(bk, kind, params=None):
    return bk["admin"].post("/api/console/backup-target", json={"type": kind, "params": params or {}})


def _settings(bk) -> dict:
    return {r["key"]: r["value"] for r in bk["conn"].execute("SELECT key, value FROM settings")}


def _journal(bk) -> list[str]:
    return [r["detail"] or "" for r in bk["conn"].execute(
        "SELECT detail FROM journal WHERE event = 'setting_change' ORDER BY id")]


def _real_point(bk) -> str:
    return str(bk["data"] / "storage" / MOUNT_NAME)


# --- (1) a local folder ---------------------------------------------------------------

def test_a_local_folder_is_tested_saved_and_left_clean(bk):
    folder = bk["tmp"] / "second-disk"
    folder.mkdir()
    r = _set(bk, "local", {"path": str(folder)})
    assert r.status_code == 200, r.text
    assert r.json()["target"]["type"] == "local" and r.json()["target"]["dest"] == folder.as_posix()
    saved = _settings(bk)
    assert saved[backup_job.SETTING] == folder.as_posix()                 # what the hourly job reads
    assert json.loads(saved[TARGET])["type"] == "local"
    assert list(folder.iterdir()) == []                                  # the test file was removed after the read-back
    assert any("backup_target=local" in d for d in _journal(bk))


def test_a_folder_that_does_not_keep_the_test_file_is_refused_and_nothing_is_saved(bk, monkeypatch):
    folder = bk["tmp"] / "flaky"
    folder.mkdir()
    monkeypatch.setattr(backup_target, "_read_back", lambda path: b"something else")
    r = _set(bk, "local", {"path": str(folder)})
    assert r.status_code == 409 and "לא נקרא בחזרה" in r.json()["detail"], r.text
    assert _settings(bk).get(backup_job.SETTING) in (None, "")
    assert list(folder.iterdir()) == []


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a POSIX folder the test user cannot write into")
def test_a_folder_that_refuses_a_write_is_refused_by_name(bk):
    folder = bk["tmp"] / "readonly"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        r = _set(bk, "local", {"path": str(folder)})
    finally:
        folder.chmod(0o700)
    assert r.status_code == 409 and "קובץ הבדיקה" in r.json()["detail"], r.text
    assert _settings(bk).get(backup_job.SETTING) in (None, "")


def test_a_folder_on_the_databases_disk_is_saved_with_the_orange_warning(bk):
    folder = bk["tmp"] / "same-disk"                                      # tmp_path: the data dir's own file system
    folder.mkdir()
    r = _set(bk, "local", {"path": str(folder)})
    assert r.status_code == 200 and SAME_DISK in r.json()["warnings"], r.text
    assert r.json()["target"]["same_disk"] is True
    backup_job.run(bk["data"], folder.as_posix(), NOW)
    row = backup_job.health_check(bk["conn"], bk["data"], "active", NOW + timedelta(minutes=5))
    assert row["state"] == "warn" and SAME_DISK in row["detail"], row


def test_a_folder_on_another_disk_is_green_once_backed_up(bk, monkeypatch):
    monkeypatch.setattr(backup_target, "same_disk", lambda a, b: False)
    folder = bk["tmp"] / "other-disk"
    folder.mkdir()
    r = _set(bk, "local", {"path": str(folder)})
    assert r.status_code == 200 and r.json()["warnings"] == [], r.text
    backup_job.run(bk["data"], folder.as_posix(), NOW)
    assert backup_job.health_check(bk["conn"], bk["data"], "active", NOW + timedelta(minutes=5))["state"] == "ok"


def test_a_local_path_is_checked_before_anything_is_written(bk):
    for bad in ("relative/path", "/", str(bk["tmp"] / "missing")):
        r = _set(bk, "local", {"path": bad})
        assert r.status_code == 400 and "יעד הגיבוי" in r.json()["detail"], (bad, r.text)
    assert _settings(bk).get(TARGET) is None


# --- the same-disk evidence -----------------------------------------------------------

def _fake_sys(root: Path) -> Path:
    """/sys/dev/block/<maj:min> -> the device: sda (8:0) with sda1 (8:1) and sda2 (8:2), sdb (8:16) with
    sdb1 (8:17), and dm-0 (253:0) whose slave is sda2 -- an LVM volume on the database's disk."""
    devices = root / "devices"
    for disk, parts in (("sda", ("sda1", "sda2")), ("sdb", ("sdb1",))):
        for part in parts:
            (devices / disk / part).mkdir(parents=True)
            (devices / disk / part / "partition").write_text("1\n", encoding="utf-8")
    (devices / "dm-0" / "slaves").mkdir(parents=True)
    os.symlink(devices / "sda" / "sda2", devices / "dm-0" / "slaves" / "sda2")
    block = root / "dev" / "block"
    block.mkdir(parents=True)
    for num, target in (("8:0", "sda"), ("8:1", "sda/sda1"), ("8:2", "sda/sda2"), ("8:16", "sdb"),
                        ("8:17", "sdb/sdb1"), ("253:0", "dm-0")):
        os.symlink(devices / target, block / num)
    return root


@pytest.mark.skipif(os.name == "nt", reason="symlinks and os.makedev: the server is Linux")
def test_the_disk_behind_a_device_comes_from_sysfs(tmp_path):
    sys_root = _fake_sys(tmp_path / "sys")
    dev = os.makedev
    assert backup_target.same_disk_devs(dev(8, 1), dev(8, 2), sys_root) is True      # two partitions, one disk
    assert backup_target.same_disk_devs(dev(8, 1), dev(8, 17), sys_root) is False    # another disk
    assert backup_target.same_disk_devs(dev(253, 0), dev(8, 1), sys_root) is True    # LVM on the same disk
    assert backup_target.same_disk_devs(dev(253, 0), dev(8, 17), sys_root) is False
    assert backup_target.same_disk_devs(dev(8, 1), dev(0, 44), sys_root) is None     # not read = not checked


def test_a_local_target_whose_disk_was_not_checked_is_not_green(bk):
    folder = bk["tmp"] / "legacy"
    folder.mkdir()
    db.set_setting(bk["conn"], backup_job.SETTING, folder.as_posix())    # set before #1956: no backup_target
    backup_job.run(bk["data"], folder.as_posix(), NOW)
    row = backup_job.health_check(bk["conn"], bk["data"], "active", NOW + timedelta(minutes=5))
    assert row["state"] == "warn" and "לא נבדק" in row["detail"], row


# --- (2) SMB, and where its password lives ---------------------------------------------

def test_an_smb_share_is_tested_mounted_kept_in_fstab_and_the_password_only_in_a_0600_file(bk):
    fake = bk["fake"]
    r = _set(bk, "smb", SMB)
    assert r.status_code == 200, r.text
    real = _real_point(bk)
    # the test mount first, at its own point, with its own credentials file -- both gone after
    test_point = str(bk["data"] / "storage" / TEST_NAME)
    assert [m[2] for m in fake.mounts] == [test_point, real]
    assert test_point in fake.umounts and real not in fake.umounts
    test_creds, real_creds = sl.creds_path("backup-test"), sl.creds_path("backup")
    assert [p for p, _ in fake.creds_writes] == [test_creds, real_creds]
    assert all(c == {"username": "imagesrv", "password": PASSWORD, "domain": "COLLEGE"} for _, c in fake.creds_writes)
    assert fake.creds_removes == [test_creds]
    # mount and fstab: credentials=<file>, never the password
    fstype, source, target, opts = fake.mounts[-1]
    assert (fstype, source, target) == ("cifs", "//10.44.10.20/dbbackup", real)
    assert f"credentials={real_creds}" in opts and PASSWORD not in opts
    line = next(e for e in fake.fstab[-1] if e["target"] == real)
    assert line["fstype"] == "cifs" and f"credentials={real_creds}" in line["opts"]
    assert "_netdev" in line["opts"] and "nofail" in line["opts"] and PASSWORD not in json.dumps(fake.fstab)
    # the database and the API never see it
    saved = _settings(bk)
    assert saved[backup_job.SETTING] == real
    assert PASSWORD not in json.dumps(saved) and "secret" not in saved[TARGET]
    got = bk["admin"].get("/api/console/settings").json()
    assert got["backup_target"]["type"] == "smb" and got["backup_target"]["display"] == "//10.44.10.20/dbbackup"
    assert PASSWORD not in json.dumps(got)
    assert not any(PASSWORD in d for d in _journal(bk))


def test_an_smb_share_that_does_not_mount_is_refused_and_leaves_nothing(bk):
    fake = bk["fake"]
    fake.mount_status, fake.mount_reason = FAILED, "mount error(13): Permission denied"
    r = _set(bk, "smb", SMB)
    assert r.status_code == 409 and "Permission denied" in r.json()["detail"], r.text
    assert _settings(bk).get(TARGET) is None and fake.fstab == []
    assert fake.creds_removes == [sl.creds_path("backup-test")]          # the test credentials do not stay
    assert len(fake.mounts) == 1                                         # the real point was never touched


def test_a_mount_that_reports_success_but_is_not_mounted_is_refused(bk):
    """The mount tool said 0 and nothing is mounted there: writing the test file would land on
    the server's own disk and read back fine -- a NAS backup that is not on the NAS."""
    bk["fake"].mounted = _NeverMounted()
    r = _set(bk, "nfs", NFS)
    assert r.status_code == 409 and "אינו נקודת עיגון" in r.json()["detail"], r.text
    assert _settings(bk).get(TARGET) is None


class _NeverMounted(set):
    def add(self, item):                     # the tool exits 0; the kernel has nothing mounted there
        return None


def test_when_fstab_cannot_be_written_the_mount_is_undone_and_nothing_is_saved(bk):
    fake = bk["fake"]
    fake.fstab_status = FAILED
    r = _set(bk, "smb", SMB)
    assert r.status_code == 409 and "fstab" in r.json()["detail"], r.text
    assert _real_point(bk) in fake.umounts
    assert sl.creds_path("backup") in fake.creds_removes
    assert _settings(bk).get(TARGET) is None and _settings(bk).get(backup_job.SETTING) in (None, "")


# --- (3) NFS --------------------------------------------------------------------------

def test_an_nfs_export_is_tested_and_mounted_without_credentials(bk):
    fake = bk["fake"]
    r = _set(bk, "nfs", NFS)
    assert r.status_code == 200, r.text
    fstype, source, target, opts = fake.mounts[-1]
    assert (fstype, source, target) == ("nfs", "10.44.10.21:/pool/dbbackup", _real_point(bk))
    assert "vers=4.1" in opts and fake.creds_writes == []
    line = next(e for e in fake.fstab[-1] if e["target"] == _real_point(bk))
    assert line["source"] == "10.44.10.21:/pool/dbbackup" and line["fstype"] == "nfs"
    assert r.json()["target"]["display"] == "10.44.10.21:/pool/dbbackup"


def test_bad_network_parameters_are_refused_before_any_tool_runs(bk):
    fake = bk["fake"]
    assert _set(bk, "smb", {**SMB, "server": "bad host;rm"}).status_code == 400
    assert _set(bk, "nfs", {**NFS, "export": "pool, rw"}).status_code == 400
    assert _set(bk, "iscsi", {"portal": "10.0.0.1"}).status_code == 400
    assert fake.mounts == [] and fake.creds_writes == []


# --- changing and clearing -------------------------------------------------------------

def test_leaving_a_share_unmounts_it_drops_its_fstab_line_and_its_credentials(bk, monkeypatch):
    fake = bk["fake"]
    assert _set(bk, "smb", SMB).status_code == 200
    monkeypatch.setattr(backup_target, "same_disk", lambda a, b: False)
    folder = bk["tmp"] / "usb-disk"
    folder.mkdir()
    assert _set(bk, "local", {"path": str(folder)}).status_code == 200
    assert _real_point(bk) in fake.umounts
    assert all(e["target"] != _real_point(bk) for e in fake.fstab[-1])
    assert sl.creds_path("backup") in fake.creds_removes
    r = _set(bk, "none")
    assert r.status_code == 200 and r.json()["target"]["type"] == "none"
    assert _settings(bk)[backup_job.SETTING] == ""
    row = backup_job.health_check(bk["conn"], bk["data"], "active", NOW)
    assert row["state"] == "warn" and "לא נבחר יעד גיבוי" in row["detail"]


def test_a_storage_location_rewriting_fstab_keeps_the_backup_line(bk):
    fake = bk["fake"]
    assert _set(bk, "nfs", NFS).status_code == 200
    r = bk["admin"].post("/api/console/storage-locations", json={
        "name": "nas-images", "type": "nfs", "tested": True,
        "params": {"server": "10.44.10.20", "export": "/mnt/pool/images", "version": "4.1"}})
    assert r.status_code == 200, r.text
    targets = [e["target"] for e in fake.fstab[-1]]
    assert _real_point(bk) in targets and len(targets) == 2


def test_a_storage_location_cannot_take_the_backup_mount_point(tmp_path):
    """The backup mounts at storage/.db-backup, next to the storage locations' own mount points: a location
    named ".db-backup" would have mounted its NAS on top of the backup's. A dot-name gets its id instead."""
    data = tmp_path / "data"
    for name in (MOUNT_NAME, TEST_NAME, ".hidden"):
        assert sl.mount_point_for(data, name, "loc_1234abcd") == data / "storage" / "loc_1234abcd", name
    assert sl.mount_point_for(data, "nas-images", "loc_1234abcd") == data / "storage" / "nas-images"


def test_only_an_admin_sets_it_and_the_old_settings_key_cannot_skip_the_test(bk):
    folder = bk["tmp"] / "x"
    folder.mkdir()
    r = bk["deploy"].post("/api/console/backup-target", json={"type": "local", "params": {"path": str(folder)}})
    assert r.status_code in (401, 403)
    r = bk["admin"].post("/api/console/settings", json={backup_job.SETTING: str(folder)})
    assert r.status_code == 400 and backup_job.SETTING in r.json()["detail"]
    assert _settings(bk).get(backup_job.SETTING) in (None, "")


# --- the hourly job on a network destination ---------------------------------------------

def _network_db(tmp_path: Path, kind: str = "nfs"):
    data = tmp_path / "data"
    data.mkdir()
    conn = db.connect(data / "imagectl.db")
    dest = data / "storage" / MOUNT_NAME
    dest.mkdir(parents=True)
    db.set_settings(conn, {backup_job.SETTING: dest.as_posix(),
                           TARGET: json.dumps({"type": kind, "mount_point": dest.as_posix()})})
    return data, dest


def test_the_job_does_not_back_up_into_an_unmounted_share(tmp_path, monkeypatch):
    """nofail in fstab: a NAS that was down at boot leaves an empty folder. A backup written there
    lands on the server's own disk and reads back fine -- it must fail by name instead."""
    data, dest = _network_db(tmp_path)
    tried = []
    monkeypatch.setattr(backup_job.os.path, "ismount", lambda p: False)
    monkeypatch.setattr(backup_job, "_mount_from_fstab", lambda p: tried.append(str(p)) or (32, "mount error: host is down"),
                        raising=False)
    assert backup_job.main(["--data-dir", str(data)]) == 1
    assert tried == [dest.as_posix()]
    saved = json.loads((data / "last-backup.json").read_text(encoding="utf-8"))
    assert saved["ok"] is False and "אינו מעוגן" in saved["error"] and "host is down" in saved["error"]
    assert not (dest / "db").exists()


def test_the_job_mounts_the_share_from_fstab_and_then_backs_up(tmp_path, monkeypatch):
    data, dest = _network_db(tmp_path, "smb")
    state = {"mounted": False}
    monkeypatch.setattr(backup_job.os.path, "ismount", lambda p: state["mounted"])
    monkeypatch.setattr(backup_job, "_mount_from_fstab", lambda p: state.update(mounted=True) or (0, ""),
                        raising=False)
    assert backup_job.main(["--data-dir", str(data)]) == 0
    assert len(list((dest / "db").glob("imagectl-*.db"))) == 1


# --- the console ----------------------------------------------------------------------

NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_the_console_row_says_what_is_chosen_and_warns_in_orange(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    names = ["backupTargetRowHtml", "backupTargetBody"]
    found = {n: re.search(rf"^function {n}\(.*?^\}}", js, re.M | re.S) for n in names}
    # a helper that is not there (the console before #1956) says nothing -- the assertions fail on what it shows
    src = {n: m.group(0) if m else f"function {n}() {{ return {{}}; }}" for n, m in found.items()}
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x == null ? "" : x);
const UI = { pill: (kind, text) => `<pill ${kind}>${text}</pill>` };
const ghostBtn = (label, fn) => `<btn ${fn}>${label}</btn>`;
let SETTINGS = null;
const settingUnreadHtml = (e) => `unread:${e}`;
""" + "\n".join(src.values()) + """
const rows = {};
for (const [name, t] of Object.entries({
  none: { type: "none", dest: "" },
  same: { type: "local", dest: "/srv/backup", display: "/srv/backup", same_disk: true },
  other: { type: "local", dest: "/mnt/usb", display: "/mnt/usb", same_disk: false },
  smb: { type: "smb", dest: "/var/lib/imagectl/storage/.db-backup", display: "//nas/db", same_disk: null } })) {
  SETTINGS = { backup_target: t };
  rows[name] = backupTargetRowHtml();
}
SETTINGS = {};
rows.unread = backupTargetRowHtml();
const bodies = {
  smb: backupTargetBody("smb", { server: " nas ", share: "db", username: "u", secret: "p", domain: "" }),
  local: backupTargetBody("local", { path: " /mnt/usb " }),
  none: backupTargetBody("none", {}) };
console.log(JSON.stringify({ rows, bodies }));
""", encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    rows, bodies = got["rows"], got["bodies"]
    assert "<pill warn>לא נבחר — המסד אינו מגובה</pill>" in rows["none"], rows
    assert f"<pill warn>{SAME_DISK}</pill>" in rows["same"] and "/srv/backup" in rows["same"]
    assert "pill" not in rows["other"] and "/mnt/usb" in rows["other"]
    assert "SMB" in rows["smb"] and "//nas/db" in rows["smb"] and "pill" not in rows["smb"]
    assert "backupTargetSheet()" in rows["smb"] and rows["unread"].startswith("unread:")
    assert bodies["smb"] == {"type": "smb", "params": {"server": "nas", "share": "db", "username": "u",
                                                        "secret": "p", "domain": ""}}, bodies
    assert bodies["local"] == {"type": "local", "params": {"path": "/mnt/usb"}}
    assert bodies["none"] == {"type": "none", "params": {}}


def test_the_console_files_are_bumped_together():
    html = (REPO / "server" / "static" / "index.html").read_text(encoding="utf-8")
    versions = set(re.findall(r"\?v=([0-9.]+)", html))
    # one ?v= for every include; which number is test_console_static's
    assert len(versions) == 1, versions

