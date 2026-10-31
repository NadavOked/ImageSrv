"""‏#1491 — שמירה מ-VM אינה כותבת לדיסק ה-VM: הכיווץ נעשה על עותק.

הכלל של #1466 (נדב 16/10) — "דיסק המקור אינו נכתב" — חל גם על
`vmdisk.capture_disk`: ‏`ntfsresize`, הטבלה החדשה ו-`ntfsfix -d` רצים על
dm-snapshot מעל loop **לקריאה בלבד** של `disk.raw`, וה-copy-on-write הוא קובץ
דליל בגודל התקציב.

כאן — בזיופים, בלי root — המנגנון: מה נשאל לפני שנוצר דבר (מקום, מדידה),
שהמקור מחובר לקריאה בלבד ושזה **נקרא בחזרה**, ושהכול מוסר גם בכשל. ההוכחה
שהבייטים של `disk.raw` לא זזו — מול כלים אמיתיים, ב-
`test_vmupdate_integration.py::test_save_shrinks_a_copy_and_never_writes_the_vm_disk`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from server.vm import vmdisk

GIB = 1 << 30
SIZE = 64 << 20          # disk.raw — הגודל עצמו אינו נבדק כאן, רק שהוא נקרא בחזרה
USER = vmdisk.VmUser(999, 989)


def done(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, out, err)


class ViewHost:
    """מארח מזויף ל-`shrink_view`: ‏losetup/blockdev/dmsetup עם מצב — מה מחובר
    ומה קיים — כדי שהשחרור ייקרא בחזרה ולא רק יישלח."""

    def __init__(self, free=100 * GIB, getro="1", create_rc=0, status_ok=True, relocs="40",
                 resize_rc=0):
        self.calls: list[list[str]] = []
        self.free = free
        self.getro = getro
        self.create_rc = create_rc
        self.status_ok = status_ok
        self.relocs = relocs
        self.resize_rc = resize_rc
        self.loops: dict[str, str] = {}     # /dev/loopN -> קובץ
        self.views: set[str] = set()
        self.next = 20

    def free_bytes(self, _path):
        return self.free

    def run(self, argv, **_kw):
        self.calls.append(list(argv))
        if argv[:3] == ["losetup", "--find", "--show"]:
            node = f"/dev/loop{self.next}"
            self.next += 1
            self.loops[node] = argv[-1]
            return done(argv, out=node + "\n")
        if argv[:2] == ["losetup", "-j"]:
            return done(argv, out="".join(f"{n}: [2049]:1 ({f})\n"
                                          for n, f in self.loops.items() if f == argv[2]))
        if argv[:2] == ["losetup", "-d"]:
            self.loops.pop(argv[2], None)
            return done(argv)
        if argv[:2] == ["blockdev", "--getro"]:
            return done(argv, out=self.getro + "\n")
        if argv[:2] == ["blockdev", "--getsize64"]:
            return done(argv, out=f"{SIZE}\n")
        if argv[:2] == ["dmsetup", "create"]:
            if self.create_rc == 0:
                self.views.add(argv[2])
            return done(argv, rc=self.create_rc, err="" if self.create_rc == 0 else "boom")
        if argv[:2] == ["dmsetup", "ls"]:
            return done(argv, out="".join(f"{v}\t(253:0)\n" for v in self.views)
                        or "No devices found\n")
        if argv[:2] == ["dmsetup", "remove"]:
            self.views.discard(argv[-1])
            return done(argv)
        if argv[:2] == ["dmsetup", "status"]:
            ok = argv[2] in self.views and self.status_ok
            return done(argv, out="0 8388608 snapshot 16/204800 16\n" if ok
                        else "0 8388608 snapshot Invalid\n")
        if "ntfsresize" in argv:
            return done(argv, rc=self.resize_rc,
                        out=f"Needed relocations : 9 ({self.relocs} MB)\n" if self.relocs else "")
        return done(argv)            # chown/chmod של partition_loop

    def agent(self, func, *args, **_kw):
        assert func == "_lc_relocs", func
        text = Path(args[0]).read_text(encoding="utf-8")
        mb = text.split("(")[1].split(" MB")[0] if "MB" in text else ""
        return type("Call", (), {"out": mb + "\n" if mb else "", "ok": True})()


@pytest.fixture()
def disk(tmp_path):
    raw = tmp_path / "disk.raw"
    with raw.open("wb") as fh:
        fh.truncate(SIZE)
    run = tmp_path / "run"
    run.mkdir()
    return raw, run


def dm_create(host):
    return [c for c in host.calls if c[:2] == ["dmsetup", "create"]]


def test_the_vm_disk_is_attached_read_only_and_that_is_read_back(disk):
    raw, run = disk
    host = ViewHost()
    with vmdisk.shrink_view(host, raw, 64 << 20, run) as view:
        assert view == "/dev/mapper/" + vmdisk.VIEW_NAME
        cow = run / "shrink.cow"
        assert cow.stat().st_size == 64 << 20          # התקציב — לא דיסק שלם
        origin = next(c for c in host.calls if c[:3] == ["losetup", "--find", "--show"]
                      and c[-1] == str(raw))
        assert "--read-only" in origin and "--partscan" not in origin
        assert ["blockdev", "--getro", "/dev/loop20"] in host.calls
        table = dm_create(host)[0][-1]
        assert table == f"0 {SIZE // 512} snapshot /dev/loop20 /dev/loop21 N 8", table
        assert host.loops["/dev/loop21"] == str(cow)
    # ביציאה: העותק, שני ה-loop וקובץ ה-copy-on-write — לא נשאר דבר
    assert host.views == set() and host.loops == {} and not (run / "shrink.cow").exists()


def test_a_loop_that_is_not_read_only_stops_before_any_view(disk):
    raw, run = disk
    host = ViewHost(getro="0")
    with pytest.raises(vmdisk.DiskError) as caught:
        with vmdisk.shrink_view(host, raw, 64 << 20, run):
            pytest.fail("a writable source must never get a view")
    assert caught.value.reason == "loop" and "קריאה בלבד" in caught.value.message
    assert dm_create(host) == []
    assert host.loops == {} and not (run / "shrink.cow").exists()


def test_no_room_for_the_copy_is_a_named_refusal_before_anything_is_made(disk):
    raw, run = disk
    host = ViewHost(free=10 << 20)
    with pytest.raises(vmdisk.DiskError) as caught:
        with vmdisk.shrink_view(host, raw, 64 << 20, run):
            pytest.fail("no room — no view")
    assert caught.value.reason == "disk_space"
    assert "נדרשים" in caught.value.message and "פנויים" in caught.value.message
    assert host.calls == [] and not (run / "shrink.cow").exists()


def test_unreadable_free_space_is_not_room(disk):
    raw, run = disk
    host = ViewHost(free=None)
    with pytest.raises(vmdisk.DiskError) as caught:
        with vmdisk.shrink_view(host, raw, 64 << 20, run):
            pytest.fail("free space was not read")
    assert caught.value.reason == "unchecked" and host.calls == []


def test_a_view_that_did_not_come_up_is_released_and_named(disk):
    raw, run = disk
    host = ViewHost(create_rc=1)
    with pytest.raises(vmdisk.DiskError) as caught:
        with vmdisk.shrink_view(host, raw, 64 << 20, run):
            pytest.fail("the view did not come up")
    assert caught.value.reason == "loop" and "boom" in caught.value.message
    assert host.loops == {} and not (run / "shrink.cow").exists()


def test_a_failure_inside_the_view_still_releases_it(disk):
    raw, run = disk
    host = ViewHost()
    with pytest.raises(vmdisk.DiskError) as caught:
        with vmdisk.shrink_view(host, raw, 64 << 20, run):
            raise vmdisk.DiskError("shrink", "ntfsresize died")
    assert caught.value.reason == "shrink"
    assert host.views == set() and host.loops == {} and not (run / "shrink.cow").exists()


def test_a_stale_view_from_a_save_that_died_is_removed_first(disk):
    raw, run = disk
    host = ViewHost()
    host.views.add(vmdisk.VIEW_NAME)
    with vmdisk.shrink_view(host, raw, 64 << 20, run):
        pass
    removes = [c for c in host.calls if c[:2] == ["dmsetup", "remove"]]
    first_create = host.calls.index(dm_create(host)[0])
    assert host.calls.index(removes[0]) < first_create


def test_an_invalid_view_is_not_valid():
    assert vmdisk.view_valid(ViewHost(status_ok=False)) is False


def test_the_budget_is_the_measured_relocations_plus_the_margin(disk):
    raw, run = disk
    host = ViewHost(relocs="40")
    part = {"index": 3, "first": 2048, "sectors": 4096}
    need = vmdisk.view_budget(host, raw, [(part, 1 << 20)], USER, run)
    assert need == (40 + 4) * 10**6 + vmdisk.VIEW_META
    resize = next(c for c in host.calls if "ntfsresize" in c)
    assert resize[:2] == ["setpriv", "--reuid=999"]          # כמשתמש ה-VM
    assert resize[resize.index("ntfsresize"):] == [
        "ntfsresize", "-n", "-s", str(1 << 20), "--no-progress-bar", "/dev/loop20"]
    loop = next(c for c in host.calls if c[:3] == ["losetup", "--find", "--show"])
    assert "--read-only" in loop                              # המדידה אינה כותבת


@pytest.mark.parametrize("relocs,rc", [("", 0), ("40", 1)])
def test_a_budget_that_was_not_measured_is_a_failure_not_zero(disk, relocs, rc):
    raw, run = disk
    host = ViewHost(relocs=relocs, resize_rc=rc)
    part = {"index": 3, "first": 2048, "sectors": 4096}
    with pytest.raises(vmdisk.DiskError) as caught:
        vmdisk.view_budget(host, raw, [(part, 1 << 20)], USER, run)
    assert caught.value.reason == "unchecked" and "מחיצה 3" in caught.value.message


class Guest:
    def __init__(self):
        self.drives = []

    def set_memsize(self, _n): pass
    def launch(self): pass

    def add_drive_opts(self, path, **kw):
        self.drives.append((path, kw))


def test_the_appliance_opens_the_disk_read_only_unless_asked():
    guest = Guest()
    vmdisk.Appliance(Path("disk.raw"), factory=lambda: guest)
    assert guest.drives[0][1]["readonly"] is True          # מה ש-capture_disk פותח קודם
    vmdisk.Appliance(Path("view"), factory=lambda: guest, writable=True)
    assert guest.drives[1][1]["readonly"] is False
