"""‏#1334 — שני ממצאי האבטחה: האורח אינו מגיע למארח ולרשתות הפנימיות, והדיסק
שהאורח שינה אינו נקרא כ-root ואינו נעגן בקרנל של השרת.

1. **רשת** (`server/vm/vmnet.py`, ‏#1468): הטבלה שנטענת לפני כל הדלקה — משתמש
   ה-VM אינו שולח דבר, ומה שבא מה-MAC/הכתובת של האורח אינו מגיע למארח; ושורת
   הפקודה של QEMU (ב-`test_vmupdate.py`) — ‏`-run-with` למשתמש ה-VM, ‏macvtap
   בלבד, בלי hostfwd/guestfwd/smb. ה-macvtap וה-lease — `test_vmnet_1468.py`.
2. **הדיסק** (`server/vm/vmdisk.py`): ערך שנקרא מהדיסק ואינו בפורמט הצפוי —
   נדחה לפני שהוא נוגע בפקודה; מחיצה שמערכת הקבצים שלה אינה זו שבמניפסט
   המקור — נדחית לפני כל loop, ‏partclone או כיווץ.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from server.vm import vmdisk, vmnet

# --- 1. רשת (‏#1468: macvtap — הטבלה חוסמת את משתמש ה-VM ואת האורח במארח) -----

MAC = "0a:1b:2c:3d:4e:5f"


def test_the_ruleset_blocks_the_vm_user_and_the_guest_from_the_host():
    text = vmnet.ruleset(999, MAC)
    lines = [ln.strip() for ln in text.splitlines()]
    # QEMU עם macvtap אינו צריך socket רשת אחד: כל מה שמשתמש ה-VM שולח — נזרק
    assert "meta skuid 999 drop" in lines
    # #1405: no negative uid match -- an orphaned socket stays on the accept policy
    assert not any("skuid !=" in ln for ln in lines)
    out = lines.index("chain out {")
    assert lines[out + 1].startswith("type filter hook output") and "policy accept" in lines[out + 1]
    # במארח: כל מסגרת מה-MAC של האורח — בטבלת `inet`, כלומר IPv4 **ו-IPv6** (סקירה 16/10)
    assert lines[0] == "add table inet imagesrv_vm"
    inn = lines.index("chain in {")
    assert lines[inn + 1].startswith("type filter hook input") and "policy accept" in lines[inn + 1]
    assert lines[inn + 2:inn + 4] == [f"ether saddr {MAC} drop", "}"]
    # שום כתובת מה-DHCP אינה נכנסת לטבלה — ACK מזויף אינו יכול לבחור מה המארח זורק
    assert not any("saddr" in ln and "ether" not in ln for ln in lines)
    assert not any("nfproto" in ln or "ip6" in ln for ln in lines)     # אין החרגה למשפחה
    # אין יותר "יציאה רק דרך כרטיס הניהול" של slirp: האורח יוצא בכתובת שלו
    assert not any("oifname" in ln or "dport 53" in ln for ln in lines)


@pytest.mark.parametrize("uid,mac", [
    (0, MAC), (999, "01:1b:2c:3d:4e:5f"),                   # multicast — לא ה-MAC שלנו
    (999, "00:1b:2c:3d:4e:5f"),                              # globally administered
    (999, 'x" drop; #')])
def test_the_ruleset_rejects_values_it_cannot_trust(uid, mac):
    with pytest.raises(ValueError):
        vmnet.ruleset(uid, mac)


def _runner(listed: str, rc: int = 0):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw.get("input")))
        out = listed if argv[:2] == ["nft", "list"] else ""
        return subprocess.CompletedProcess(argv, rc, out, "")
    return run, calls


def test_apply_loads_and_reads_the_table_back():
    good = vmnet.ruleset(999, MAC).replace("add table", "").replace("delete table", "")
    run, calls = _runner(good)
    assert vmnet.apply(999, MAC, run=run) is None
    assert calls[0][0] == ["nft", "-f", "-"] and "skuid 999 drop" in calls[0][1]


def test_a_table_that_did_not_come_back_is_a_refusal_not_a_pass():
    run, _calls = _runner("table inet imagesrv_vm {\n}\n")
    assert "לא ידוע מה נטען" in vmnet.apply(999, MAC, run=run)
    # כלל ה-MAC שלא חזר — גם הוא סירוב, לא "בערך נטען"
    partial = vmnet.ruleset(999, MAC).replace(f"ether saddr {MAC} drop", "")
    run, _calls = _runner(partial)
    assert f"ether saddr {MAC} drop" in vmnet.apply(999, MAC, run=run)


# --- 2. הדיסק שהאורח שינה ----------------------------------------------------------

WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
DISK = "0E239172-A515-D8B4-92A1-AD790E239172"
SOURCE = {"id": "img_aaaaaa", "disk_guid": DISK, "partitions": [
    {"index": 1, "type_guid": ESP, "fs": "vfat"},
    {"index": 2, "type_guid": WIN, "fs": "ntfs"}]}


class FakeGuest:
    """ה-handle של libguestfs — מה שהאורח השאיר על הדיסק."""

    def __init__(self, *, parts=None, guids=None, fs=None, disk_guid=DISK, attrs=0):
        self.parts = parts or [{"part_num": 1, "part_start": 1 << 20, "part_size": 64 << 20},
                               {"part_num": 2, "part_start": 65 << 20, "part_size": 2 << 30}]
        self.guids = guids or {1: "6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D4",
                               2: "6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D5"}
        self.types = {1: ESP, 2: WIN}
        self.fsmap = fs or {"/dev/sda1": "vfat", "/dev/sda2": "ntfs"}
        self.disk_guid = disk_guid
        self.attrs = attrs
        self.calls = []

    def set_memsize(self, _n): pass
    def add_drive_opts(self, *_a, **_k): pass
    def launch(self): pass
    def shutdown(self): pass
    def close(self): pass
    def part_get_parttype(self, _dev): return "gpt"
    def part_get_disk_guid(self, _dev): return self.disk_guid
    def part_list(self, _dev): return self.parts
    def part_get_gpt_type(self, _dev, n): return self.types[n]
    def part_get_gpt_guid(self, _dev, n): return self.guids[n]
    def part_get_gpt_attributes(self, _dev, _n): return self.attrs

    def vfs_type(self, dev):
        self.calls.append(("vfs_type", dev))
        return self.fsmap[dev]

    def pread_device(self, *_a):
        return b"NTFS    "

    def ntfs_3g_probe(self, *_a):
        return 0


class NoHost:
    """כל נגיעה במארח לפני שהאימות עבר — כישלון הטסט."""

    def run(self, argv, **_kw):
        raise AssertionError(f"host touched before validation: {argv}")

    def popen(self, argv, **_kw):
        raise AssertionError(f"host touched before validation: {argv}")

    def agent(self, func, *args, **_kw):
        raise AssertionError(f"agent function {func} ran before validation")


def capture_with(tmp_path, guest):
    work = tmp_path / "work"
    work.mkdir()
    (work / "disk.raw").write_bytes(b"")
    (work / "source-manifest.json").write_text(json.dumps(SOURCE), encoding="utf-8")
    with pytest.raises(vmdisk.DiskError) as caught:
        # ‏#1535: ‏capture_disk מוסר את אזור העבודה לקבוצה של משתמש ה-VM (‏chown) —
        # בלי root, רק לקבוצה שלנו. ה-uid אינו נוגע בדיסק לפני האימות.
        vmdisk.capture_disk(NoHost(), work, work / "out", lambda n: None,
                            user=vmdisk.VmUser(999, os.getgid() if hasattr(os, "getgid") else 989),
                            appliance=lambda disk: vmdisk.Appliance(disk, factory=lambda: guest))
    return caught.value


def test_a_partition_whose_filesystem_changed_is_refused_before_anything_runs(tmp_path):
    """האורח הפך את מחיצה 2 ל-ext4 (או לכל מה שמפרסר אחר יקרא) — נדחה
    בשמו, לפני loop, ‏partclone, ‏ntfsresize או פונקציית סוכן."""
    exc = capture_with(tmp_path, FakeGuest(fs={"/dev/sda1": "vfat", "/dev/sda2": "ext4"}))
    assert exc.reason == "fs_changed" and "מחיצה 2" in exc.message


def test_a_partition_the_guest_added_is_refused(tmp_path):
    guest = FakeGuest()
    guest.parts.append({"part_num": 3, "part_start": 3 << 30, "part_size": 1 << 20})
    guest.types[3] = WIN
    guest.guids[3] = "6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D6"
    guest.fsmap["/dev/sda3"] = "ntfs"
    exc = capture_with(tmp_path, guest)
    assert exc.reason == "layout" and "נוספו [3]" in exc.message


def test_another_disks_guid_is_refused(tmp_path):
    exc = capture_with(tmp_path, FakeGuest(disk_guid="11111111-2222-3333-4444-555555555555"))
    assert exc.reason == "layout"


@pytest.mark.parametrize("field,value", [
    ("guid", "6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D5; rm -rf /"),
    ("guid", "$(reboot)"),
    ("disk_guid", "0E239172-A515-D8B4-92A1-AD790E23917`id`"),
    ("start", "1048576; true"),
    ("start", 1000),            # לא כפולה של 512
    ("attrs", 1 << 64),        # ‏-1 הוא int64 חוקי (כל הביטים, #1421) — מחוץ לטווח הוא 2**64
    ("attrs", -(1 << 63) - 1),
])
def test_values_read_from_the_disk_are_validated_before_use(tmp_path, field, value):
    guest = FakeGuest()
    if field == "guid":
        guest.guids[2] = value
    elif field == "disk_guid":
        guest.disk_guid = value
    elif field == "start":
        guest.parts[1] = {**guest.parts[1], "part_start": value}
    else:
        guest.attrs = value
    exc = capture_with(tmp_path, guest)
    assert exc.reason == "table", exc.message


def test_bitlocker_is_refused_by_its_signature(tmp_path):
    guest = FakeGuest()
    guest.pread_device = lambda *_a: b"-FVE-FS-"
    assert capture_with(tmp_path, guest).reason == "bitlocker"


@pytest.mark.parametrize("rc,reason", [(14, "hibernated"), (15, "hibernated"), (9, "unchecked")])
def test_hibernation_codes_from_the_appliance(tmp_path, rc, reason):
    guest = FakeGuest()
    guest.ntfs_3g_probe = lambda *_a: rc
    assert capture_with(tmp_path, guest).reason == reason


def test_a_hibernated_vm_disk_gives_the_fix_that_works_not_a_full_shutdown(tmp_path):
    """#1504 (המשך #1502, ברזל 16/10): עם Fast Startup דלוק כיבוי רגיל **הוא**
    שינה, ו"כיבוי מלא" לא משחרר את הדיסק. ההודעה אומרת זאת ונותנת את התיקון
    שעובד — ‏`powercfg /h off` — כמו `capture_hibernated_message` של הסוכן.
    עברית קודם: שורה שנפתחת באסימון LTR נפרסת הפוך."""
    guest = FakeGuest()
    guest.ntfs_3g_probe = lambda *_a: 14
    exc = capture_with(tmp_path, guest)
    assert exc.reason == "hibernated"
    assert "powercfg /h off" in exc.message
    assert "כיבוי רגיל" in exc.message and "Fast Startup" in exc.message
    assert "כיבוי מלא" not in exc.message and "shutdown /s" not in exc.message
    assert re.match("[\u0590-\u05FF]", exc.message), exc.message


def test_the_unprivileged_wrapper_drops_everything():
    argv = vmdisk.as_user(vmdisk.VmUser(999, 989), ["partclone.ntfs", "-c"])
    assert argv[:6] == ["setpriv", "--reuid=999", "--regid=989", "--clear-groups",
                        "--no-new-privs", "--"]
    assert argv[6:] == ["partclone.ntfs", "-c"]


# --- 3. הורדת ההרשאות: root אינו כותב ואינו משנה בעלות מתחת לתיקייה של המשתמש ----



from native import requires_native  # noqa: E402
from server.vm import agent_shell  # noqa: E402

posix_only = requires_native(posix=True, why="symlinks ו-chown של POSIX")


@posix_only
def test_fresh_user_dir_refuses_a_parent_that_is_a_planted_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "run").symlink_to(outside)
    with pytest.raises(PermissionError):
        agent_shell.fresh_user_dir(tmp_path / "work" / "run", os.getuid(), os.getgid())
    assert list(outside.iterdir()) == []          # שום תיקייה לא נוצרה בחוץ


@posix_only
def test_fresh_user_dir_refuses_a_parent_others_can_write(tmp_path):
    parent = tmp_path / "run"
    parent.mkdir()
    parent.chmod(0o777)
    with pytest.raises(PermissionError):
        agent_shell.fresh_user_dir(parent, os.getuid(), os.getgid())


@posix_only
def test_fresh_user_dir_is_new_private_and_owned_by_the_user(tmp_path):
    parent = tmp_path / "run"
    first = agent_shell.fresh_user_dir(parent, os.getuid(), os.getgid())
    second = agent_shell.fresh_user_dir(parent, os.getuid(), os.getgid())
    assert first != second and first.parent == parent
    st = os.lstat(first)
    assert not first.is_symlink() and st.st_mode & 0o777 == 0o700 and st.st_uid == os.getuid()


@posix_only
def test_a_vars_file_planted_as_a_symlink_is_not_followed(tmp_path):
    """המשתמש שולט בתיקייה שלו בזמן הקריאה: symlink במקום קובץ הסיבות
    אינו נקרא (‏O_NOFOLLOW), ותיקיית הקריאה נמחקת בלי לעקוב אחריו."""
    secret = tmp_path / "secret"
    secret.write_text("PLAN_ERROR=root-only-content\n", encoding="utf-8")
    seen = {}

    def runner(argv, **kw):
        run = Path(kw["env"]["RUN_DIR"])
        seen["run"] = run
        (run / agent_shell._VARS_FILE).symlink_to(secret)
        return subprocess.CompletedProcess(argv, 0, "", "")

    got = agent_shell.call("true", run_dir=tmp_path / "run", runner=runner,
                           as_user=(os.getuid(), os.getgid()))
    assert got.errors == {}
    assert not seen["run"].exists() and secret.read_text(encoding="utf-8").startswith("PLAN")


class FakeBox:
    def fetch(self, _index, paths, dest):
        for path in paths:
            target = dest / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"MZ")


@posix_only
def test_boot_files_never_land_through_a_stale_planted_symlink(tmp_path):
    """שמירה קודמת השאירה `unpriv/esp` ו-`run/esp-x` שמשתמש ה-VM החזיק —
    עכשיו symlinks לתיקייה בחוץ. ההורדה (כ-root) נוחתת רק בתיקייה חדשה של
    root, והחוץ נשאר ריק."""
    outside = tmp_path / "outside"
    outside.mkdir()
    work = tmp_path / "work"
    run_dir = work / "run"
    run_dir.mkdir(parents=True)
    (work / "unpriv").mkdir()
    (work / "unpriv" / "esp").symlink_to(outside)
    (run_dir / "esp-old").symlink_to(outside)
    (run_dir / "u-old").symlink_to(outside)
    vmdisk._clear_stale(work, run_dir)
    assert not (work / "unpriv").exists() and not (run_dir / "esp-old").exists()
    target = vmdisk.boot_files(FakeBox(), 1, ["/EFI/BOOT/bootx64.efi"], run_dir, os.getuid())
    assert target.parent == run_dir and not target.is_symlink()
    assert (target / "EFI" / "BOOT" / "bootx64.efi").read_bytes() == b"MZ"
    assert os.lstat(target).st_uid == os.geteuid()
    assert list(outside.iterdir()) == []


class LoopHost:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def run(self, argv, **_kw):
        self.calls.append(argv)
        rc = 1 if argv[0] in self.fail else 0
        out = "/dev/loop7\n" if argv[:2] == ["losetup", "--find"] else ""
        return subprocess.CompletedProcess(argv, rc, out, "")


PART = {"index": 2, "first": 2048, "sectors": 4096}
USER = vmdisk.VmUser(999, 989)


def _tail(host):
    return [c[:2] for c in host.calls[-3:]]


def test_loop_device_ownership_is_restored_after_success():
    host = LoopHost()
    with vmdisk.partition_loop(host, Path("/w/disk.raw"), PART, USER, writable=False) as node:
        assert node == "/dev/loop7"
    assert ["chown", "999:989", "/dev/loop7"] in host.calls
    assert _tail(host) == [["chown", "root:disk"], ["chmod", "0660"], ["losetup", "-d"]]
    assert "--read-only" in host.calls[0] and "--partscan" not in host.calls[0]


def test_loop_device_ownership_is_restored_after_a_failure():
    host = LoopHost()
    with pytest.raises(RuntimeError):
        with vmdisk.partition_loop(host, Path("/w/disk.raw"), PART, USER, writable=True):
            raise RuntimeError("partclone died")
    assert _tail(host) == [["chown", "root:disk"], ["chmod", "0660"], ["losetup", "-d"]]


def test_a_restore_that_failed_is_reported_not_swallowed():
    host = LoopHost(fail={"chmod"})
    with pytest.raises(vmdisk.DiskError) as caught:
        with vmdisk.partition_loop(host, Path("/w/disk.raw"), PART, USER, writable=False):
            pass
    assert caught.value.reason == "loop" and "root:disk" in caught.value.message


@posix_only
def test_a_vars_file_planted_as_a_fifo_does_not_hang_the_server(tmp_path):
    """‏FIFO במקום קובץ הסיבות: ‏open רגיל היה נחסם לנצח. נפתח ב-O_NONBLOCK,
    נדחה כלא-רגיל, ומחזיר מהר."""
    import time

    def runner(argv, **kw):
        os.mkfifo(Path(kw["env"]["RUN_DIR"]) / agent_shell._VARS_FILE)
        return subprocess.CompletedProcess(argv, 0, "", "")

    started = time.monotonic()
    got = agent_shell.call("true", run_dir=tmp_path / "run", runner=runner,
                           as_user=(os.getuid(), os.getgid()))
    assert got.errors == {} and time.monotonic() - started < 5


@pytest.mark.parametrize("raw,hexed", [
    (-(1 << 63), "8000000000000000"),          # ‏ESP של Windows (#1421)
    (-(1 << 63) + 1, "8000000000000001"),      # ‏Recovery
    (0, "0000000000000000"),
])
def test_gpt_attributes_come_back_as_signed_int64_and_are_read_as_bits(tmp_path, raw, hexed):
    """‏#1421: ‏libguestfs מחזיר את ה-attrs כ-int64 עם סימן; ביט 63 = שלילי.
    זה לא ערך זדוני — זה כל ESP ו-Recovery של Windows."""
    guest = FakeGuest(attrs=raw)
    disk = tmp_path / "disk.raw"
    disk.write_bytes(b"")
    _guid, parts = vmdisk.Appliance(disk, factory=lambda: guest).table()
    assert {p["attrs"] for p in parts} == {hexed}
