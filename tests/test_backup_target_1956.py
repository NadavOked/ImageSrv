"""#1956 -- where the database backup goes (Nadav 06/11).

Two destinations: the server's own drive (the default, with a fixed orange "אותו
כונן, לא מגן מתקלת כונן") and a mounted folder. On either a test file is written
**and read back** while the destination is being set, so a destination that does
not take a file is refused now and not at the first backup (principle 5).

SMB and NFS are for the images only. A server that saved one before that decision
keeps going safely: it backs up to its own drive at once, the saved setting, the
fstab line and the share stay as they are and the console says so, until the
operator chooses a destination -- and nothing is ever deleted from the share.

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

from console_src import console_js
from native import requires_native
from server.system import backup_job
from server import db
from server.storage import locations as sl
from server.storage.locations import FAILED
from test_storage_locations import FakeHooks

try:
    from server.system import backup_target
except ImportError:   # the server before #1956: the endpoint answers 404 and these say nothing, so every
    # assertion below fails on what the server does -- not on a missing module (bakara-shlilit)
    from types import SimpleNamespace
    backup_target = SimpleNamespace(same_disk_devs=lambda *a: None, same_disk=lambda a, b: None,
                                    _read_back=lambda path: b"")

REPO = Path(__file__).resolve().parent.parent
TARGET = "backup_target"          # the setting -- JSON, no secret
MOUNT_NAME = ".db-backup"          # a share saved before SMB/NFS left: under <data_dir>/storage, which the backups skip
NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
SAME_DISK = "אותו כונן, לא מגן מתקלת כונן"
SMB = {"server": "10.44.10.20", "share": "dbbackup", "username": "imagesrv", "domain": "COLLEGE"}
NFS = {"server": "10.44.10.21", "export": "/pool/dbbackup", "version": "4.1"}


@pytest.fixture()
def bk(tmp_path: Path, images_root: Path, clock, monkeypatch):
    from server.rounds import sender as sender_module
    from server.console import users
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


# --- the server's own drive ------------------------------------------------------------

def test_with_nothing_chosen_the_backup_goes_to_the_servers_drive_and_says_it_is_one_drive(bk):
    got = bk["admin"].get("/api/console/settings").json()["backup_target"]
    assert (got["type"], got["same_disk"]) == ("default", True), got
    assert backup_job.main(["--data-dir", str(bk["data"])]) == 0
    copies = list((bk["data"] / "backups" / "db").glob("imagesrv-*.db"))
    assert len(copies) == 1
    row = backup_job.health_check(bk["conn"], bk["data"], "active")
    assert row["state"] == "warn" and SAME_DISK in row["detail"] and "גובה" in row["detail"], row


def test_choosing_the_servers_drive_back_writes_the_test_file_there(bk):
    folder = bk["tmp"] / "second-disk"
    folder.mkdir()
    assert _set(bk, "local", {"path": str(folder)}).status_code == 200
    r = _set(bk, "default")
    assert r.status_code == 200 and SAME_DISK in r.json()["warnings"], r.text
    assert r.json()["target"]["type"] == "default" and _settings(bk)[backup_job.SETTING] == ""
    assert list((bk["data"] / "backups").iterdir()) == []                # created, and the test file is gone
    assert any("backup_target=default" in d for d in _journal(bk))


# --- (1) a local folder ---------------------------------------------------------------

def test_a_local_folder_is_tested_saved_and_left_clean(bk):
    folder = bk["tmp"] / "second-disk"
    folder.mkdir()
    r = _set(bk, "local", {"path": str(folder)})
    assert r.status_code == 200, r.text
    assert r.json()["target"]["type"] == "local" and r.json()["target"]["dest"] == folder.as_posix()
    saved = _settings(bk)
    assert saved[backup_job.SETTING] == folder.as_posix()                 # what the job reads
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


# --- SMB and NFS are for the images ------------------------------------------------------

@pytest.mark.parametrize("kind, params", [("smb", SMB), ("nfs", NFS)])
def test_smb_and_nfs_are_refused_by_name_and_no_tool_runs(bk, kind, params):
    fake = bk["fake"]
    r = _set(bk, kind, params)
    assert r.status_code == 400 and "לאחסון האימג'ים" in r.json()["detail"], r.text
    assert fake.mounts == [] and fake.creds_writes == [] and fake.fstab == []
    assert _settings(bk).get(TARGET) is None


def test_an_unknown_kind_is_refused(bk):
    assert _set(bk, "iscsi", {"portal": "10.0.0.1"}).status_code == 400
    assert _set(bk, "none").status_code == 400              # there is no "no backup" any more
    assert _settings(bk).get(TARGET) is None


def _saved_share(bk, kind="smb") -> Path:
    """What a server has after #1965: the setting, a mount point, and the backups already on the share."""
    point = Path(_real_point(bk))
    (point / "db").mkdir(parents=True)
    old = point / "db" / "imagesrv-20261020-120000.db"
    old.write_bytes(b"a backup on the share")
    public = SMB if kind == "smb" else NFS
    db.set_settings(bk["conn"], {backup_job.SETTING: point.as_posix(),
                                 TARGET: json.dumps({"type": kind, **public, "mount_point": point.as_posix()})})
    return old


@pytest.mark.parametrize("kind, shown", [("smb", "//10.44.10.20/dbbackup"), ("nfs", "10.44.10.21:/pool/dbbackup")])
def test_a_saved_share_moves_to_the_servers_drive_and_nothing_is_lost(bk, kind, shown):
    fake = bk["fake"]
    old = _saved_share(bk, kind)
    before = {k: v for k, v in _settings(bk).items() if k in (TARGET, backup_job.SETTING)}
    assert backup_job.main(["--data-dir", str(bk["data"])]) == 0

    assert len(list((bk["data"] / "backups" / "db").glob("imagesrv-*.db"))) == 1      # the backups go on
    assert len(list(old.parent.iterdir())) == 1 and old.read_bytes() == b"a backup on the share"
    assert {k: v for k, v in _settings(bk).items() if k in (TARGET, backup_job.SETTING)} == before
    assert fake.umounts == [] and fake.creds_removes == []                            # nothing was taken down
    row = backup_job.health_check(bk["conn"], bk["data"], "active")
    assert row["state"] == "warn" and shown in row["detail"] and "לא נמחקו" in row["detail"], row
    got = bk["admin"].get("/api/console/settings").json()["backup_target"]
    assert got["type"] == "default" and got["legacy"]["display"] == shown, got


def test_a_destination_cancelled_before_this_change_is_the_servers_drive(bk):
    """#1965 had a "none": it saved an empty destination with {"type": "none"}."""
    db.set_settings(bk["conn"], {backup_job.SETTING: "", TARGET: json.dumps({"type": "none"})})
    got = bk["admin"].get("/api/console/settings").json()["backup_target"]
    assert got["type"] == "default" and "legacy" not in got, got
    assert backup_job.main(["--data-dir", str(bk["data"])]) == 0
    assert len(list((bk["data"] / "backups" / "db").glob("imagesrv-*.db"))) == 1


def test_a_saved_share_keeps_its_fstab_line_when_a_storage_location_rewrites_fstab(bk):
    _saved_share(bk, "nfs")
    r = bk["admin"].post("/api/console/storage-locations", json={
        "name": "nas-images", "type": "nfs", "tested": True,
        "params": {"server": "10.44.10.20", "export": "/mnt/pool/images", "version": "4.1"}})
    assert r.status_code == 200, r.text
    targets = [e["target"] for e in bk["fake"].fstab[-1]]
    assert _real_point(bk) in targets and len(targets) == 2


@pytest.mark.parametrize("kind", ["default", "local"])
def test_choosing_a_destination_lets_the_saved_share_go_and_leaves_its_files(bk, kind, monkeypatch):
    monkeypatch.setattr(backup_target, "same_disk", lambda a, b: False)
    fake = bk["fake"]
    old = _saved_share(bk)
    folder = bk["tmp"] / "usb-disk"
    folder.mkdir()
    r = _set(bk, kind, {"path": str(folder)} if kind == "local" else {})
    assert r.status_code == 200, r.text
    assert _real_point(bk) in fake.umounts
    assert all(e["target"] != _real_point(bk) for e in fake.fstab[-1])
    assert sl.creds_path("backup") in fake.creds_removes
    assert old.read_bytes() == b"a backup on the share"
    assert "legacy" not in r.json()["target"]
    assert backup_job.health_check(bk["conn"], bk["data"], "active")["detail"].find("הוסר") == -1


def test_a_share_that_did_not_come_down_is_said_not_swallowed(bk):
    fake = bk["fake"]
    _saved_share(bk)
    fake.fstab_status = FAILED
    r = _set(bk, "default")
    assert r.status_code == 200, r.text
    assert any("fstab" in w for w in r.json()["warnings"]), r.json()


def test_a_storage_location_cannot_take_the_backup_mount_point(tmp_path):
    """A saved share is mounted at storage/.db-backup, next to the storage locations' own mount points: a
    location named ".db-backup" would have mounted its NAS on top of it. A dot-name gets its id instead."""
    data = tmp_path / "data"
    for name in (MOUNT_NAME, ".hidden"):
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


# --- the console ----------------------------------------------------------------------

NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_the_console_row_says_what_is_chosen_and_warns_in_orange(tmp_path):
    js = console_js()
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
  drive: { type: "default", label: "כונן השרת", dest: "", display: "", same_disk: true },
  same: { type: "local", label: "תיקייה", dest: "/srv/backup", display: "/srv/backup", same_disk: true },
  other: { type: "local", label: "תיקייה", dest: "/mnt/usb", display: "/mnt/usb", same_disk: false },
  unchecked: { type: "local", label: "תיקייה", dest: "/mnt/usb", display: "/mnt/usb", same_disk: null },
  legacy: { type: "default", label: "כונן השרת", dest: "", display: "", same_disk: true,
            legacy: { type: "smb", label: "SMB", display: "//nas/db" } } })) {
  SETTINGS = { backup_target: t };
  rows[name] = backupTargetRowHtml();
}
SETTINGS = {};
rows.unread = backupTargetRowHtml();
const bodies = {
  local: backupTargetBody("local", { path: " /mnt/usb " }),
  drive: backupTargetBody("default", {}) };
console.log(JSON.stringify({ rows, bodies }));
""", encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    rows, bodies = got["rows"], got["bodies"]
    assert "כונן השרת" in rows["drive"] and f"<pill warn>{SAME_DISK}</pill>" in rows["drive"], rows
    assert f"<pill warn>{SAME_DISK}</pill>" in rows["same"] and "/srv/backup" in rows["same"]
    assert "pill" not in rows["other"] and "/mnt/usb" in rows["other"]
    assert "<pill warn>לא נבדק אם על כונן אחר</pill>" in rows["unchecked"]
    assert "SMB" in rows["legacy"] and "//nas/db" in rows["legacy"] and "backupTargetSheet()" in rows["legacy"]
    assert rows["unread"].startswith("unread:")
    assert bodies["local"] == {"type": "local", "params": {"path": "/mnt/usb"}}
    assert bodies["drive"] == {"type": "default", "params": {}}


@requires_native(("node", NODE))
def test_the_console_policy_row_says_the_hour_and_the_space_and_what_it_could_not_read(tmp_path):
    js = console_js()
    found = {n: re.search(rf"^function {n}\(.*?^\}}", js, re.M | re.S) for n in ("backupPolicyRowHtml", "backupPolicyHelp")}
    src = {n: m.group(0) if m else f"function {n}() {{ return ''; }}" for n, m in found.items()}
    probe = tmp_path / "policy.cjs"
    probe.write_text("""
const esc = (x) => String(x == null ? "" : x);
const ghostBtn = (label, fn) => `<btn ${fn}>${label}</btn>`;
const settingUnreadHtml = (e) => `unread:${e}`;
let SETTINGS = { backup_policy: { time: "23:15", reserve_gb: 40, min_gb: 1, cap_gb: 93, cap_percent: 20 } };
""" + "\n".join(src.values()) + """
const row = backupPolicyRowHtml();
const help = backupPolicyHelp(SETTINGS.backup_policy);
const unknown = backupPolicyHelp({ ...SETTINGS.backup_policy, cap_gb: null });
SETTINGS = {};
console.log(JSON.stringify({ row, help, unknown, unread: backupPolicyRowHtml() }));
""", encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert "23:15" in got["row"] and "40 GB שמורים" in got["row"] and "backupPolicySheet()" in got["row"], got
    assert "20%" in got["help"] and "עד 93 GB" in got["help"], got
    assert "גודל הכונן לא נקרא" in got["unknown"] and "null" not in got["unknown"], got
    assert got["unread"].startswith("unread:"), got


@requires_native(("node", NODE))
def test_the_console_windows_post_what_the_server_expects(tmp_path):
    """The real sheet functions, with ``sheet`` and ``post`` as recorders: what is on offer, and what goes out."""
    js = console_js()
    names = ("backupPolicyHelp", "backupPolicySheet", "backupTargetBody", "backupTargetSave", "backupTargetSheet",
             "backupTargetFolderSheet")
    found = {n: re.search(rf"^(?:async )?function {n}\(.*?^\}}", js, re.M | re.S) for n in names}
    assert all(found.values()), [n for n, m in found.items() if not m]
    probe = tmp_path / "sheets.cjs"
    probe.write_text("""
const sheets = [], posts = [], toasts = [];
const sheet = (s) => sheets.push(s);
const post = async (path, body) => { posts.push([path, body]); return { warnings: [] }; };
const toast = (t) => toasts.push(t);
const loadSettingsData = async () => {};
const isAdmin = () => true;
const SETTINGS = { backup_target: { type: "default", dest: "" },
                   backup_policy: { time: "02:00", reserve_gb: 5, min_gb: 1, cap_gb: 93, cap_percent: 20 } };
""" + "\n".join(m.group(0) for m in found.values()) + """
(async () => {
  backupPolicySheet();
  const policy = sheets.pop();
  await policy.onSubmit({ time: "23:15", reserve_gb: " 40 " });
  backupTargetSheet();
  const target = sheets.pop();
  await target.onSubmit({ kind: "default" });
  const next = await target.onSubmit({ kind: "local" });
  next();
  const folder = sheets.pop();
  await folder.onSubmit({ path: " /mnt/usb " });
  console.log(JSON.stringify({ posts, fields: policy.fields.map((f) => [f.id, f.type || "", f.value]),
                               kinds: target.fields[0].options.map((o) => o.value) }));
})();
""", encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["fields"] == [["time", "time", "02:00"], ["reserve_gb", "", "5"]], got
    assert got["kinds"] == ["default", "local"], got
    assert got["posts"] == [["/backup-policy", {"time": "23:15", "reserve_gb": "40"}],
                            ["/backup-target", {"type": "default", "params": {}}],
                            ["/backup-target", {"type": "local", "params": {"path": "/mnt/usb"}}]], got


def test_the_console_offers_no_network_destination():
    js = console_js()
    sheet = re.search(r"^function backupTargetSheet\(.*?^\}", js, re.M | re.S).group(0)
    assert '"default"' in sheet and '"local"' in sheet
    assert 'value: "smb"' not in sheet and 'value: "nfs"' not in sheet
    assert "function backupTargetDetailsSheet" not in js


def test_the_console_files_are_bumped_together():
    html = (REPO / "server" / "static" / "index.html").read_text(encoding="utf-8")
    versions = set(re.findall(r"\?v=([0-9.]+)", html))
    # one ?v= for every include; which number is test_console_static's
    assert len(versions) == 1, versions

