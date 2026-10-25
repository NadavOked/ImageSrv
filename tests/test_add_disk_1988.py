""""הוסף כונן" (#1988) — כל כלי מערכת מזויף. אף טסט כאן אינו נוגע בדיסק.

הכרעת נדב 22/10: רק כוננים ריקים לפי ראיה; אישור בכפתור אדום; GPT + ext4
+ fstab לפי UUID עם nofail; קריאה חוזרת; כשל באמצע — גלוי, בלי מיקום חלקי.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from server import storage_disks
from server import storage_locations as sl
from server.storage_locations import FAILED, OK, UNCHECKED, HookResult

UUID = "0b5e2a43-6c1e-4f43-9a1c-6d2f0c7e9a11"
BLANK = {"device": "/dev/sdb", "name": "sdb", "size_bytes": 4_000_787_030_016,
         "model": "WDC WD40EFRX", "serial": "WD-WCC7K1234567", "bus": "sata"}
FULL = {"device": "/dev/sdc", "name": "sdc", "size_bytes": 2_000_398_934_016,
        "model": "ST2000DM008", "serial": "ZFL1ABCD", "bus": "sata",
        "reason": "blkid מצא עליו ntfs"}


# --- כלי המערכת: lsblk / blkid -p / ראש וסוף הכונן ------------------------------

def _tree(*nodes):
    return {"blockdevices": list(nodes)}


def _disk(name, **extra):
    node = {"name": name, "path": f"/dev/{name}", "type": "disk", "size": 1_000_000_000,
            "model": "M", "serial": f"S-{name}", "ro": False, "rm": False, "tran": "sata",
            "fstype": None, "pttype": None}
    node.update(extra)
    return node


def test_classify_offers_only_whole_writable_fixed_disks_without_any_signature():
    tree = _tree(
        _disk("sda", pttype="gpt", children=[{"name": "sda1", "type": "part"}]),
        _disk("sdb"),
        _disk("sdc", pttype="dos"),
        _disk("sdd", fstype="ext4"),
        _disk("sde", rm=True),
        _disk("sdf", ro=True),
        _disk("sdg", tran="usb"),
        _disk("sdh", fstype="linux_raid_member"),
        _disk("sdi", size=None),
        _disk("sdj", children=[{"name": "sdj1", "type": "part"}]),   # PTTYPE ריק (udev לא קרא) — הילדים מכריעים
        _disk("zram0"),
        {"name": "loop0", "path": "/dev/loop0", "type": "loop", "size": 10},
        {"name": "sr0", "path": "/dev/sr0", "type": "rom", "size": 10},
    )
    candidates, hidden = sl.classify_disks(tree)
    assert [d["device"] for d in candidates] == ["/dev/sdb"]
    reasons = {d["device"]: d["reason"] for d in hidden}
    assert set(reasons) == {f"/dev/sd{c}" for c in "acdefghij"}
    assert "מחיצות" in reasons["/dev/sdj"]
    assert "מחיצות" in reasons["/dev/sda"]
    assert "dos" in reasons["/dev/sdc"] and "ext4" in reasons["/dev/sdd"]
    assert "linux_raid_member" in reasons["/dev/sdh"]
    assert "USB" in reasons["/dev/sde"] and "USB" in reasons["/dev/sdg"]


class Runner:
    """argv → HookResult לפי קידומת. כל קריאה נרשמת."""

    def __init__(self, table):
        self.table = table
        self.calls: list[list[str]] = []

    def __call__(self, cmd, timeout=30, input_text=None):
        del timeout, input_text
        self.calls.append(list(cmd))
        for prefix, result in self.table:
            if cmd[:len(prefix)] == prefix:
                return result(cmd) if callable(result) else result
        raise AssertionError(f"unexpected command {cmd}")


def _failed(rc, err=""):
    return HookResult(FAILED, err or f"exit {rc}", {"returncode": rc, "stdout": "", "stderr": err})


@pytest.mark.parametrize("blkid, zeros, blank, why", [
    (_failed(2), True, True, ""),
    (HookResult(OK, payload={"stdout": "PTTYPE=gpt\n"}), True, False, "gpt"),
    (HookResult(OK, payload={"stdout": "TYPE=ntfs\n"}), True, False, "ntfs"),
    (_failed(2), False, False, "יש נתונים"),
    (_failed(2), "Input/output error", False, "Input/output error"),
    (_failed(8, "ambivalent result"), True, False, "יציאה 8"),
    (HookResult(UNCHECKED, "blkid לא נמצא במערכת"), True, False, "לא נבדק"),
])
def test_probe_blank_needs_positive_evidence_and_a_failed_read_is_not_blank(blkid, zeros, blank, why):
    run = Runner([(["blkid", "-p"], blkid)])
    got, reason = sl.probe_blank("/dev/sdb", run=run, zeros=lambda dev: zeros)
    assert got is blank
    assert why in reason
    assert run.calls == [["blkid", "-p", "-o", "export", "/dev/sdb"]]


def test_strict_zeros_reads_head_and_tail_and_an_unreadable_tail_is_not_zeros(tmp_path):
    mib = 1 << 20
    zero = tmp_path / "zero.img"
    zero.write_bytes(b"\x00" * (3 * mib))
    assert sl._zeros_evidence(str(zero), strict_tail=True) is True
    tail = tmp_path / "tail.img"
    tail.write_bytes(b"\x00" * (3 * mib - 1) + b"\x01")      # GPT משני בסוף
    assert sl._zeros_evidence(str(tail), strict_tail=True) is False
    small = tmp_path / "small.img"                            # אין 1MB לסוף
    small.write_bytes(b"\x00" * 4096)
    assert isinstance(sl._zeros_evidence(str(small), strict_tail=True), str)
    assert sl._zeros_evidence(str(small)) is True             # הקריאה הישנה לא השתנתה


def test_list_blank_disks_probes_each_candidate_and_names_why_the_rest_are_hidden():
    tree = _tree(_disk("sda", pttype="gpt", children=[{"type": "part"}]), _disk("sdb"), _disk("sdc"))
    run = Runner([(["lsblk", "-J", "-b"], HookResult(OK, payload={"stdout": json.dumps(tree)}))])
    probed = []

    def probe(dev):
        probed.append(dev)
        return (True, "") if dev == "/dev/sdb" else (False, "יש נתונים בראש הכונן או בסופו")

    result = sl.list_blank_disks(run=run, probe=probe)
    assert result.status == OK
    assert [d["device"] for d in result.payload["items"]] == ["/dev/sdb"]
    assert {d["device"] for d in result.payload["hidden"]} == {"/dev/sda", "/dev/sdc"}
    assert probed == ["/dev/sdb", "/dev/sdc"]                  # sda לא נקרא בכלל


@pytest.mark.parametrize("listed, status", [
    (HookResult(UNCHECKED, "lsblk לא נמצא במערכת"), UNCHECKED),
    (HookResult(OK, payload={"stdout": "not json"}), FAILED),
    (HookResult(OK, payload={"stdout": "[]"}), FAILED),
])
def test_list_blank_disks_that_could_not_read_is_never_an_empty_list(listed, status):
    result = sl.list_blank_disks(run=Runner([(["lsblk"], listed)]), probe=lambda d: (True, ""))
    assert result.status == status
    assert "items" not in (result.payload or {})


def _lsblk(stdout):
    return HookResult(OK, payload={"stdout": stdout})


def _prepare_runner(tmp_path, *, parts=1, blkid="TYPE=ext4\nUUID=" + UUID + "\n", mkfs=None,
                    shape="flat", lsblk=None):
    """‏`shape="flat"` היא הצורה ש-lsblk 2.41 (דביאן 13) מחזיר בלי NAME (#2102);
    ‏`"tree"` — צורת `children`. ‏`lsblk` דורס את התשובה כולה."""
    nodes = []
    for n in range(1, parts + 1):
        node = tmp_path / f"sdb{n}"
        node.touch()
        nodes.append({"path": str(node), "type": "part", "pkname": "sdb"})
    disk = {"path": "/dev/sdb", "type": "disk", "pkname": None}
    devices = [disk, *nodes] if shape == "flat" else [{**disk, "children": nodes}]
    return Runner([
        (["sgdisk"], HookResult(OK, payload={"stdout": ""})),
        (["udevadm", "settle"], HookResult(OK, payload={"stdout": ""})),
        (["lsblk", "-J"], lsblk or _lsblk(json.dumps({"blockdevices": devices}))),
        (["mkfs.ext4"], mkfs or HookResult(OK, payload={"stdout": ""})),
        (["blkid", "-p"], HookResult(OK, payload={"stdout": blkid})),
    ])


@pytest.mark.parametrize("shape", ["flat", "tree"])
def test_prepare_disk_partitions_formats_and_reads_the_uuid_back(tmp_path, shape):
    run = _prepare_runner(tmp_path, shape=shape)
    result = sl.prepare_disk("/dev/sdb", "loc_ab12cd34", run=run, sleep=lambda s: None)
    assert result.status == OK, result.reason
    assert result.payload == {"partition": str(tmp_path / "sdb1"), "uuid": UUID, "fs_type": "ext4"}
    assert run.calls[0] == ["sgdisk", "--new=1:1MiB:0", "--typecode=1:8300",
                            "--change-name=1:loc_ab12cd34", "/dev/sdb"]
    assert ["mkfs.ext4", "-F", "-L", "loc_ab12cd34", str(tmp_path / "sdb1")] in run.calls


# --- #2102: פלט lsblk אמיתי, לא צורה שהטסט המציא --------------------------------
# נאסף 24/10 על ה-testrunner (דביאן 13, ‏util-linux 2.41.5) — ראו README שבתיקייה.
LSBLK_REAL = Path(__file__).resolve().parent / "fixtures" / "lsblk-2102"


@pytest.mark.parametrize("fixture, device, why", [
    ("flat-sda-3parts.json", "/dev/sda", "3 מחיצות"),       # 2.41 בלי NAME — שטוח
    ("tree-sda-3parts.json", "/dev/sda", "3 מחיצות"),       # עם NAME — עץ children
    ("flat-sdb-no-parts.json", "/dev/sdb", "לא הופיעה"),    # כונן בלי מחיצות (עדיין)
    ("flat-sda-3parts.json", "/dev/sdb", "lsblk לא החזיר את /dev/sdb"),
    ("flat-sda-no-pkname.json", "/dev/sda", "PKNAME"),      # הפקודה הישנה — אי אפשר לדעת הורה
])
def test_prepare_disk_counts_partitions_in_real_lsblk_output(tmp_path, fixture, device, why):
    real = (LSBLK_REAL / fixture).read_text(encoding="utf-8")
    run = _prepare_runner(tmp_path, lsblk=_lsblk(real))
    slept = []
    result = sl.prepare_disk(device, "loc_x", run=run, sleep=slept.append)
    assert result.status == FAILED
    assert why in result.reason
    assert ["lsblk", "-J", "-o", "PATH,TYPE,PKNAME", device] in run.calls
    assert "mkfs.ext4" not in {c[0] for c in run.calls}


@pytest.mark.parametrize("listed, why", [
    (_lsblk("not json"), "אינו JSON"),
    (_lsblk("[]"), "blockdevices"),
    (_lsblk('{"blockdevices": {"path": "/dev/sdb"}}'), "blockdevices"),
    (_lsblk('{"blockdevices": [{"path": "/dev/sdb", "type": "disk", "pkname": null,'
            ' "children": "sdb1"}]}'), "children"),
    (HookResult(UNCHECKED, "lsblk לא נמצא במערכת"), "lsblk לא נמצא"),
])
def test_prepare_disk_that_could_not_read_partitions_says_so_at_once(tmp_path, listed, why):
    """פלט שאי אפשר לפרש אינו "המחיצה עוד לא הופיעה" — לא מחכים עליו 10 שניות."""
    run = _prepare_runner(tmp_path, lsblk=listed)
    slept = []
    result = sl.prepare_disk("/dev/sdb", "loc_x", run=run, sleep=slept.append)
    assert result.status == FAILED
    assert "לא הצלחנו לקרוא את המחיצות" in result.reason and why in result.reason
    assert slept == []


@pytest.mark.parametrize("kwargs, why", [
    ({"blkid": "TYPE=xfs\nUUID=" + UUID + "\n"}, "לא קרא ext4"),
    ({"blkid": "TYPE=ext4\n"}, "לא קרא ext4"),
    ({"parts": 2}, "2 מחיצות"),
    ({"parts": 0}, "לא הופיעה"),
    ({"mkfs": HookResult(FAILED, "mkfs.ext4: Device size reported to be zero")}, "מערכת הקבצים"),
])
def test_prepare_disk_success_is_evidence_not_the_absence_of_an_error(tmp_path, kwargs, why):
    run = _prepare_runner(tmp_path, **kwargs)
    slept = []
    result = sl.prepare_disk("/dev/sdb", "loc_x", run=run, sleep=slept.append)
    assert result.status != OK
    assert why in result.reason
    assert len(slept) <= sl.PARTITION_WAIT_S                 # המתנה עם תקרה


def test_fstab_line_for_a_disk_is_by_uuid_with_nofail_and_without_netdev(tmp_path, monkeypatch):
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=root / ext4 defaults 0 1\n", encoding="utf-8")
    monkeypatch.setattr(sl, "FSTAB_PATH", fstab)
    monkeypatch.setattr(sl, "daemon_reload", lambda: HookResult(OK), raising=False)   # ‏#2122: לא PID 1 של המריץ
    entries = [{"source": "UUID=" + UUID, "target": "/var/lib/imagectl/storage/big",
                "fstype": "ext4", "opts": "defaults,nofail,x-systemd.device-timeout=10s",
                "local": True},
               {"source": "10.0.0.5:/images", "target": "/var/lib/imagectl/storage/nas",
                "fstype": "nfs", "opts": "vers=4.1"}]
    assert sl.fstab_write(entries).status == OK
    lines = fstab.read_text(encoding="utf-8").splitlines()
    disk = next(line for line in lines if line.startswith("UUID=" + UUID))
    assert "nofail" in disk and "_netdev" not in disk
    nfs = next(line for line in lines if line.startswith("10.0.0.5"))
    assert "_netdev" in nfs and "nofail" in nfs
    assert lines[0] == "UUID=root / ext4 defaults 0 1"


# --- הזרימה דרך ה-API ---------------------------------------------------------

class DiskFake:
    """כל ה-hooks של מיקומי האחסון, מזויפים; מונים לכל פעולה הרסנית."""

    def __init__(self):
        self.listing = HookResult(OK, payload={"items": [dict(BLANK)], "hidden": [dict(FULL)]})
        self.prepare_result = HookResult(OK, payload={"partition": "/dev/sdb1", "uuid": UUID,
                                                      "fs_type": "ext4"})
        self.mount_status = OK
        self.fstab_status = OK
        self.mounted: set[str] = set()
        self.lie_about_mount = False
        self.prepared: list[tuple] = []
        self.mounts: list[tuple] = []
        self.umounts: list[str] = []
        self.fstab: list[list] = []

    def blank_disks(self):
        return self.listing

    def prepare_disk(self, device, label):
        self.prepared.append((device, label))
        return self.prepare_result

    def mount(self, fstype, source, target, opts=""):
        self.mounts.append((fstype, source, target, opts))
        if self.mount_status != OK:
            return HookResult(self.mount_status, "wrong fs type, bad superblock")
        Path(target).mkdir(parents=True, exist_ok=True)
        if not self.lie_about_mount:
            self.mounted.add(str(Path(target)))
        return HookResult(OK)

    def umount(self, target):
        self.umounts.append(target)
        self.mounted.discard(str(Path(target)))
        return HookResult(OK)

    def is_mount(self, target):
        return str(Path(target)) in self.mounted

    def fstab_write(self, entries):
        self.fstab.append(list(entries))
        if self.fstab_status != OK:
            return HookResult(self.fstab_status, "Read-only file system")
        return HookResult(OK)

    def df(self, target):
        return HookResult(OK, payload={"free_bytes": 3_900_000_000_000,
                                       "total_bytes": 3_940_000_000_000})

    def as_dict(self):
        def refuse(*args, **kwargs):
            raise AssertionError(f"a real storage tool would have run: {args}")
        return {name: refuse for name in sl.default_hooks()} | {
            "blank_disks": self.blank_disks, "prepare_disk": self.prepare_disk,
            "mount": self.mount, "umount": self.umount, "is_mount": self.is_mount,
            "fstab_write": self.fstab_write, "df": self.df, "interfaces": lambda: [],
        }


@pytest.fixture()
def env(tmp_path: Path, images_root: Path, clock, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from server import sender as sender_module
    from server import users
    from server.app import create_app
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    fake = DiskFake()
    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")
    health_hooks = {
        "ss": lambda: "", "unit_active": lambda name: "", "http_get": lambda url: 200,
        "http_size": lambda url: (200, 9_000_000), "interfaces": lambda: [],
        "tftp_root": lambda: tftp, "udp_sender_pids": lambda: [],
    }
    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=Recorder(block=True),
        storage_hooks=fake.as_dict(), storage_check_interval=10**9,
        health_hooks=health_hooks,
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test",
                 check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login",
                      json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login",
                       json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "deploy": deploy, "fake": fake, "data": tmp_path / "data"}
    ctx.sender.stop()


BASE = "/api/console/storage-locations"


def _add(env, **changes):
    body = {"device": BLANK["device"], "serial": BLANK["serial"], "size_bytes": BLANK["size_bytes"],
            "name": "big-disk", "confirm_erase": True}
    body.update(changes)
    return env["admin"].post(BASE + "/add-disk", json=body)


def _rows(env):
    return [r["id"] for r in sl.list_rows(env["ctx"].conn)]


def test_the_list_shows_empty_disks_and_why_the_others_are_not_offered(env):
    body = env["admin"].get(BASE + "/disks").json()
    assert [d["device"] for d in body["items"]] == ["/dev/sdb"]
    assert body["hidden"][0]["device"] == "/dev/sdc" and "ntfs" in body["hidden"][0]["reason"]


def test_deploy_gets_403_on_both_disk_endpoints(env):
    assert env["deploy"].get(BASE + "/disks").status_code == 403
    assert env["deploy"].post(BASE + "/add-disk", json={"confirm_erase": True}).status_code == 403
    assert env["fake"].prepared == []


def test_a_list_that_could_not_be_read_is_503_not_no_disks(env):
    env["fake"].listing = HookResult(UNCHECKED, "lsblk לא נמצא במערכת")
    resp = env["admin"].get(BASE + "/disks")
    assert resp.status_code == 503 and "lsblk" in resp.json()["detail"]


def test_no_red_button_no_erase(env):
    before = _rows(env)
    resp = _add(env, confirm_erase=None)
    assert resp.status_code == 422
    assert env["fake"].prepared == [] and _rows(env) == before


def test_add_disk_partitions_mounts_by_uuid_reads_back_and_writes_fstab(env):
    resp = _add(env)
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["type"] == "local" and row["type_label"] == "כונן"
    assert row["state"] == "connected" and row["removable"] is True
    assert row["params"]["uuid"] == UUID and row["params"]["serial"] == BLANK["serial"]
    fake = env["fake"]
    assert fake.prepared == [("/dev/sdb", row["id"])]
    assert fake.mounts == [("ext4", "UUID=" + UUID, row["mount_point"], "")]
    mount = Path(row["mount_point"])
    assert mount.parent == env["data"] / "storage"
    assert (mount / "images").is_dir()
    assert not (mount / storage_disks.READBACK_NAME).exists()
    last = fake.fstab[-1]
    assert {"source": "UUID=" + UUID, "target": row["mount_point"], "fstype": "ext4",
            "opts": "defaults,nofail,x-systemd.device-timeout=10s", "local": True} in last
    assert sl.images_dir(sl.get(env["ctx"].conn, row["id"])) == mount / "images"
    events = [r["event"] for r in env["ctx"].conn.execute("SELECT event FROM journal").fetchall()]
    assert "storage_location_add_disk" in events
    assert not (env["data"] / storage_disks.LOCK_NAME).exists()


@pytest.mark.parametrize("changes, why", [
    ({"device": "/dev/sdc", "serial": FULL["serial"], "size_bytes": FULL["size_bytes"]}, "אינו ריק"),
    ({"device": "/dev/sdz"}, "אינו ברשימת"),
    ({"serial": "OTHER-DISK"}, "השתנה"),
    ({"size_bytes": 1}, "השתנה"),
])
def test_a_disk_that_is_not_empty_now_or_changed_is_never_touched(env, changes, why):
    before = _rows(env)
    resp = _add(env, **changes)
    assert resp.status_code == 409 and why in resp.json()["detail"]
    assert env["fake"].prepared == [] and env["fake"].mounts == []
    assert _rows(env) == before


def _assert_nothing_left(env, before):
    fake = env["fake"]
    assert _rows(env) == before
    assert not any(e.get("local") for e in (fake.fstab[-1] if fake.fstab else []))
    assert fake.mounted == set()
    assert not (env["data"] / storage_disks.LOCK_NAME).exists()


def test_prepare_failure_is_shown_and_no_location_is_added(env):
    env["fake"].prepare_result = HookResult(FAILED, "mkfs.ext4: Input/output error")
    before = _rows(env)
    resp = _add(env)
    assert resp.status_code == 409
    assert "הכנת הכונן נכשלה" in resp.json()["detail"] and "Input/output" in resp.json()["detail"]
    assert env["fake"].mounts == []
    _assert_nothing_left(env, before)


def test_a_uuid_that_was_not_read_is_not_a_success(env):
    env["fake"].prepare_result = HookResult(OK, payload={"partition": "/dev/sdb1", "uuid": ""})
    before = _rows(env)
    resp = _add(env)
    assert resp.status_code == 409 and "UUID" in resp.json()["detail"]
    assert env["fake"].mounts == []
    _assert_nothing_left(env, before)


def test_mount_failure_leaves_no_location(env):
    env["fake"].mount_status = FAILED
    before = _rows(env)
    resp = _add(env)
    assert resp.status_code == 409 and "העיגון נכשל" in resp.json()["detail"]
    _assert_nothing_left(env, before)


def test_mount_that_reported_ok_but_is_not_a_mountpoint_is_a_failure(env):
    env["fake"].lie_about_mount = True                        # הקובץ היה נכתב לדיסק המערכת
    before = _rows(env)
    resp = _add(env)
    assert resp.status_code == 409 and "אינה מעוגנת" in resp.json()["detail"]
    assert env["fake"].umounts                                # נוסה לנתק בכל זאת
    _assert_nothing_left(env, before)


def test_readback_failure_unmounts_and_leaves_no_location(env, monkeypatch):
    monkeypatch.setattr(storage_disks, "readback", lambda target: "הקובץ שנקרא בחזרה אינו מה שנכתב")
    before = _rows(env)
    resp = _add(env)
    assert resp.status_code == 409 and "אינו מה שנכתב" in resp.json()["detail"]
    assert env["fake"].umounts
    _assert_nothing_left(env, before)


def test_fstab_failure_removes_the_row_and_unmounts(env):
    env["fake"].fstab_status = FAILED
    before = _rows(env)
    resp = _add(env)
    assert resp.status_code == 409 and "fstab" in resp.json()["detail"]
    assert env["fake"].umounts
    assert _rows(env) == before
    assert env["fake"].mounted == set()


def test_another_add_in_progress_is_refused_and_a_stale_lock_is_not_forever(env):
    lock = env["data"] / storage_disks.LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("4242", encoding="utf-8")
    resp = _add(env)
    assert resp.status_code == 409 and "רצה עכשיו" in resp.json()["detail"]
    assert env["fake"].prepared == []
    old = time.time() - storage_disks.LOCK_STALE_S - 60
    os.utime(lock, (old, old))
    assert _add(env).status_code == 200


def test_a_disk_that_did_not_come_up_is_unreachable_not_connected(env):
    row = _add(env).json()
    env["fake"].mounted.clear()                               # nofail: השרת עלה בלעדיו
    checked = env["admin"].post(f"{BASE}/{row['id']}/check").json()
    assert checked["state"] == "unreachable" and "אינו מעוגן" in checked["state_detail"]
    env["fake"].mounted.add(str(Path(row["mount_point"])))
    assert env["admin"].post(f"{BASE}/{row['id']}/check").json()["state"] == "connected"


def test_disconnect_drops_the_fstab_line_and_connect_mounts_by_uuid_again(env):
    row = _add(env).json()
    fake = env["fake"]
    env["admin"].post(f"{BASE}/{row['id']}/disconnect")
    assert not any(e.get("local") for e in fake.fstab[-1])
    fake.mounts.clear()
    again = env["admin"].post(f"{BASE}/{row['id']}/connect")
    assert again.status_code == 200 and again.json()["state"] == "connected"
    assert fake.mounts == [("ext4", "UUID=" + UUID, row["mount_point"], "")]


def test_connect_after_disconnect_puts_the_fstab_line_back(env):
    """‏#1993: ‏connect כתב את fstab כשהמצב עוד היה `disconnected`, והשורה דולגה —
    המיקום "מחובר" ולא שרד אתחול."""
    row = _add(env).json()
    env["admin"].post(f"{BASE}/{row['id']}/disconnect")
    again = env["admin"].post(f"{BASE}/{row['id']}/connect")
    assert again.status_code == 200 and again.json()["state"] == "connected"
    assert any(e.get("source") == "UUID=" + UUID for e in env["fake"].fstab[-1])


def test_connect_whose_fstab_write_fails_stays_disconnected_and_unmounted(env):
    """‏#1993 / ‏#1123 (4): עיגון בלי שורת fstab אינו `connected` — והמיקום
    נשאר מנותק, לא "לא נגיש" ולא מעוגן."""
    row = _add(env).json()
    fake = env["fake"]
    env["admin"].post(f"{BASE}/{row['id']}/disconnect")
    fake.fstab_status = FAILED
    r = env["admin"].post(f"{BASE}/{row['id']}/connect")
    assert r.status_code == 409 and "fstab" in r.json()["detail"]
    assert any(e.get("source") == "UUID=" + UUID for e in fake.fstab[-1]), "השורה נוסתה"
    assert sl.get(env["ctx"].conn, row["id"])["state"] == "disconnected"
    assert not fake.is_mount(row["mount_point"])


# --- מסך השרת (DCUI) ---------------------------------------------------------

def _dcui(tmp_path, conn, fake):
    from dataclasses import replace
    from types import SimpleNamespace

    from server.dcui.app import MENU_ITEMS, Runtime
    from server.dcui.data import demo_snapshot
    keys = SimpleNamespace(KEY_ENTER=343, KEY_F2=266, KEY_F4=268, KEY_F5=269, KEY_F9=273,
                           KEY_F12=276, KEY_BACKSPACE=263, KEY_UP=259, KEY_DOWN=258,
                           KEY_LEFT=260, KEY_RIGHT=261, KEY_BTAB=353)
    runtime = Runtime(tmp_path, conn, replace(demo_snapshot()), last_key_at=time.monotonic(),
                      storage_hooks=fake.as_dict())
    runtime.screen = "menu"
    runtime.menu_index = MENU_ITEMS.index("Add Disk")
    return runtime, keys


def _press(runtime, keys, *pressed):
    from server.dcui.app import _handle_key
    for key in pressed:
        runtime.last_key_at = time.monotonic()
        _handle_key(runtime, key, keys)


@pytest.fixture()
def dcui_conn(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "imagectl.db")
    sl.ensure_local(conn, tmp_path / "images")
    return conn


def test_dcui_lists_only_empty_disks_and_enter_alone_erases_nothing(tmp_path, dcui_conn):
    from server.dcui.app import _current_lines
    fake = DiskFake()
    runtime, keys = _dcui(tmp_path, dcui_conn, fake)
    _press(runtime, keys, "\n")
    assert runtime.screen == "disk"
    screen = "\n".join(_current_lines(runtime, time.monotonic()))
    assert "/dev/sdb" in screen and "Not offered (1)" in screen
    _press(runtime, keys, "\n")                               # → אישור, הפוקוס על ביטול
    assert runtime.screen == "disk_confirm" and runtime.disk_focus == "cancel"
    assert "ALL DATA ON THIS DISK WILL BE ERASED" in "\n".join(_current_lines(runtime, time.monotonic()))
    _press(runtime, keys, "\n")                               # Enter על ביטול
    assert runtime.screen == "disk" and fake.prepared == []


def test_dcui_red_button_adds_the_disk_through_the_same_flow(tmp_path, dcui_conn):
    fake = DiskFake()
    runtime, keys = _dcui(tmp_path, dcui_conn, fake)
    _press(runtime, keys, "\n", "\n", "\t", "\n")
    assert runtime.screen == "result" and runtime.result.ok, runtime.result
    assert fake.prepared and fake.prepared[0][0] == "/dev/sdb"
    rows = [r for r in sl.list_rows(dcui_conn) if r["id"] != sl.LOCAL_ID]
    assert len(rows) == 1 and rows[0]["name"] == "disk-234567" and sl.is_disk(rows[0])
    assert "נוסף כמיקום האחסון" in runtime.result.he


def test_dcui_failure_is_shown_in_english_on_tty1_and_hebrew_on_the_graphical_screen(tmp_path, dcui_conn):
    from datetime import datetime

    from server.dcui import view
    fake = DiskFake()
    fake.prepare_result = HookResult(FAILED, "sgdisk: Could not create partition")
    runtime, keys = _dcui(tmp_path, dcui_conn, fake)
    _press(runtime, keys, "\n", "\n", "\t", "\n")
    assert runtime.screen == "result" and not runtime.result.ok
    assert runtime.result.message.isascii() and "no location was added" in runtime.result.message
    frame = view.render(runtime, time.monotonic(), time.time(), "12:00", datetime.now())
    assert "הכנת הכונן נכשלה" in frame and "sgdisk" in frame
    assert [r for r in sl.list_rows(dcui_conn) if r["id"] != sl.LOCAL_ID] == []


def test_dcui_list_that_could_not_be_read_says_so(tmp_path, dcui_conn):
    from server.dcui.app import _current_lines
    fake = DiskFake()
    fake.listing = HookResult(UNCHECKED, "lsblk לא נמצא במערכת")
    runtime, keys = _dcui(tmp_path, dcui_conn, fake)
    _press(runtime, keys, "\n")
    assert runtime.screen == "disk" and runtime.disks is None
    assert "NOT 'no empty disk'" in "\n".join(_current_lines(runtime, time.monotonic()))
    _press(runtime, keys, "\n")                               # אין מה לאשר
    assert runtime.screen == "disk" and fake.prepared == []
