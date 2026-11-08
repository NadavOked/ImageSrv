"""#2431 -- hardening findings in the agent (security scan 04/11, scan8 findings 4-10).

1. **The wipe tools said 0 when they failed** (`tools_disk.sh`). Every tool now
   reads its command's exit code: a failure is rc 1 and says so, never the
   success words. `hdparm`: a SECURITY ERASE that failed after the NULL password
   was set leaves a disk that locks at the next power-off -- the password is
   taken off again (`--security-disable`), and when that fails too the output
   says the disk is locked with NULL and how to open it.
2. **The device argument was a free path** -- `/dev/pts/../../tmp/x`, a
   partition, loop, dm and the clone stick were all accepted. Now: a whole disk
   the kernel lists in /sys/block, by its plain name, never loop/dm/ram/md/sr,
   never the stick (usb-media.disks); and the output names the disk (model,
   serial, size) before anything runs.
3. **The stick's data partition was found by label alone, the first match won**
   (`usbclone.sh`): an internal drawer labelled IMGSRV_DATA was mounted rw and
   written to. Now only a data partition on a disk that is not internal
   (`disk_internal`), and exactly one -- two candidates mount none.
4. **Duplicate targets** (`lc-start|sdc|sdb,sdb|0`) were accepted (`lcgui.sh`).
5. **`gui_error` wrote the server's text with its newlines** into the
   key=value error file (`guibridge.sh`): a `detail` carrying a newline added
   records of its own. It is one line now.
6. **"ביטול השחזור" zapped the disk it was given without asking which disk it
   is** (`srgui.sh`): the identity (serial, size) is taken when the restore
   starts, and the wipe runs only on the same disk.
7. **The used-bytes scan kept mounting while the local-clone screen was up**
   (`diskused.sh`): the local clone owns the disks there as a round does.

Negative control: every production file of the fix back to main -- each
group below fails on its own assertion (see the PR body).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_agent import BASH, posix, sh
from test_drawer_card_2030 import box_with_disk
from test_local_clone_agent import DISP, run as lc_run
from test_tools_disk import chmod_all, make_fakes, out_text, write_fake

pytestmark = requires_native(("bash", BASH))

REPO = Path(__file__).resolve().parent.parent
DISK_SH = REPO / "agent" / "lib" / "tools" / "tools_disk.sh"


# --- 1 + 2: the wipe tools ---------------------------------------------------------------


def fake_sysroot(tmp_path: Path) -> Path:
    root = tmp_path / "sysroot"
    for name, model, serial in (("sda", "Samsung SSD 870", "S6PNNS0T"), ("sdb", "USB Stick", "STICK1"),
                                ("nvme0n1", "WD SN770", "22X1")):
        d = root / "sys" / "block" / name
        (d / "device").mkdir(parents=True)
        (d / "size").write_text("1000215216\n", newline="\n")
        (d / "device" / "model").write_text(model + "\n", newline="\n")
        (d / "device" / "serial").write_text(serial + "\n", newline="\n")
    (root / "sys" / "block" / "sda" / "sda1").mkdir()          # a partition lives under its disk
    for virt in ("loop0", "dm-0", "ram0", "md0", "sr0"):
        (root / "sys" / "block" / virt).mkdir(parents=True)
    return root


def tools(tmp_path: Path, cmd: str, fakes: dict[str, tuple[str, int]] | None = None,
          pre: str = "") -> subprocess.CompletedProcess:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    (run / "usb-media.disks").write_text("sdb\n", newline="\n")
    box = tmp_path / "bin"; box.mkdir(exist_ok=True)
    make_fakes(box)
    for name, (body, rc) in (fakes or {}).items():
        write_fake(box, name, body, rc)
    chmod_all(box, [p.name for p in box.iterdir()])
    root = fake_sysroot(tmp_path)
    script = (f"export RUN_DIR=$(cd {posix(run)!r} && pwd) MACHINE_NAME=lab-pc SYSROOT={posix(root)!r} "
              f"FAKE_LOG=$(cd {posix(tmp_path)!r} && pwd)/touched.log "
              f'PATH="$(cd {posix(box)!r} && pwd):/usr/bin:/bin"; {pre}. {posix(DISK_SH)!r}; ' + cmd)
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(REPO), stdin=subprocess.DEVNULL)


def touched(tmp_path: Path) -> list[str]:
    log = tmp_path / "touched.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@pytest.mark.parametrize("tool_id,binary,arg", [
    ("disk-blkdiscard", "blkdiscard", "/dev/sda"),
    ("disk-nvme-format", "nvme", "/dev/nvme0n1"),
    ("disk-dd-zero", "dd", "/dev/sda"),
    ("disk-wipefs-all", "wipefs", "/dev/sda"),
])
def test_a_wipe_command_that_failed_is_rc_1_and_says_so(tmp_path, tool_id, binary, arg):
    r = tools(tmp_path, f"tools_disk_run {tool_id} {arg} lab-pc",
              {binary: (f'echo "{binary.upper()} $*" >> "$FAKE_LOG"; echo "{binary}: I/O error" >&2', 1)})
    body = out_text(tmp_path, tool_id)
    assert r.returncode == 1, (r.returncode, body)
    assert "נכשל" in body and "rc 1" in body, body
    assert touched(tmp_path), "the command ran -- this is its exit code, not a refusal"


def test_dd_writes_the_disks_own_size_and_nothing_past_it(tmp_path):
    """Run off the end, dd says ENOSPC (rc 1) on every good wipe -- so it is told the size."""
    r = tools(tmp_path, "tools_disk_run disk-dd-zero /dev/sda lab-pc")
    assert r.returncode == 0, out_text(tmp_path, "disk-dd-zero")
    full, rest = divmod(1000215216, 32768)
    assert touched(tmp_path) == [f"DD if=/dev/zero of=/dev/sda bs=16M count={full} conv=notrunc,fsync",
                                 f"DD if=/dev/zero of=/dev/sda bs=512 seek={full * 32768} count={rest} conv=notrunc,fsync"], \
        touched(tmp_path)                                    # fsync: on the disk before rc 0


def test_a_wipe_that_worked_is_still_rc_0(tmp_path):
    r = tools(tmp_path, "tools_disk_run disk-wipefs-all /dev/sda lab-pc")
    assert r.returncode == 0, out_text(tmp_path, "disk-wipefs-all")
    assert "נכשל" not in out_text(tmp_path, "disk-wipefs-all")


HDPARM = ('echo "HDPARM $*" >> "$FAKE_LOG"; '
          'case "$*" in *--security-set-pass*) exit "${SET_RC:-0}" ;; *--security-erase*) exit "${ERASE_RC:-0}" ;; '
          '*--security-disable*) exit "${DISABLE_RC:-0}" ;; esac')


def test_an_erase_that_failed_takes_the_null_password_off_again(tmp_path):
    r = tools(tmp_path, "ERASE_RC=5 tools_disk_run disk-hdparm-erase /dev/sda lab-pc", {"hdparm": (HDPARM, 0)})
    body = out_text(tmp_path, "disk-hdparm-erase")
    assert r.returncode == 1, body
    calls = touched(tmp_path)
    assert [c.split()[3] for c in calls] == ["--security-set-pass", "--security-erase", "--security-disable"], calls
    assert "הסיסמה הוסרה" in body and "נכשל" in body, body


def test_an_erase_whose_password_stays_says_the_disk_will_lock(tmp_path):
    r = tools(tmp_path, "ERASE_RC=5 DISABLE_RC=5 tools_disk_run disk-hdparm-erase /dev/sda lab-pc",
              {"hdparm": (HDPARM, 0)})
    body = out_text(tmp_path, "disk-hdparm-erase")
    assert r.returncode == 1, body
    assert "יינעל" in body and "--security-unlock NULL" in body, body


def test_a_password_that_was_not_set_never_erases(tmp_path):
    r = tools(tmp_path, "SET_RC=5 tools_disk_run disk-hdparm-erase /dev/sda lab-pc", {"hdparm": (HDPARM, 0)})
    assert r.returncode == 1
    assert [c.split()[3] for c in touched(tmp_path)] == ["--security-set-pass"], touched(tmp_path)


@pytest.mark.parametrize("arg", ["/dev/pts/../../tmp/x", "/dev/sda1", "/dev/loop0", "/dev/dm-0", "/dev/ram0",
                                 "/dev/md0", "/dev/sr0", "/dev/sdz", "/dev/sdb", "/dev/", "sda", "/dev/sda/"])
def test_only_a_whole_disk_that_is_not_the_stick_is_a_target(tmp_path, arg):
    r = tools(tmp_path, f"tools_disk_run disk-wipefs-all {arg!r} lab-pc")
    assert r.returncode == 2, (arg, out_text(tmp_path, "disk-wipefs-all"))
    assert "לא חוקי" in out_text(tmp_path, "disk-wipefs-all")
    assert touched(tmp_path) == [], touched(tmp_path)


def test_the_output_names_the_disk_before_the_wipe(tmp_path):
    r = tools(tmp_path, "tools_disk_run disk-blkdiscard /dev/sda lab-pc")
    body = out_text(tmp_path, "disk-blkdiscard")
    assert r.returncode == 0, body
    head = body.split("--- blkdiscard ---")[0]
    assert "Samsung SSD 870" in head and "S6PNNS0T" in head and str(1000215216 * 512) in head, head


def test_a_sata_disk_shows_its_serial_through_disk_serial(tmp_path):
    """device/serial is NVMe only; a SATA disk has its wwid (or hdparm) -- disk_serial reads both."""
    pre = (f"rm -f {posix(tmp_path / 'sysroot/sys/block/sda/device/serial')!r}; "
           f"echo 't10.ATA     Samsung SSD 870 S6PNNS0TSATA' > {posix(tmp_path / 'sysroot/sys/block/sda/device/wwid')!r}; "
           f". {posix(flat_lib())}/common.sh; . {posix(flat_lib())}/sysinfo.sh; . {posix(flat_lib())}/diskinfo.sh; ")
    r = tools(tmp_path, "tools_disk_run disk-blkdiscard /dev/sda lab-pc", pre=pre)
    body = out_text(tmp_path, "disk-blkdiscard")
    assert r.returncode == 0, body
    assert "S6PNNS0TSATA" in body.split("--- blkdiscard ---")[0], body


# --- 3: the stick's data partition --------------------------------------------------------


USB_LIBS = ("common", "sysinfo", "diskinfo", "usbclone")


def usb_root(tmp_path: Path, disks: dict[str, bool]) -> Path:
    """disk -> internal? An internal disk: removable 0 and a device on no USB bus."""
    root = tmp_path / "sysroot"
    for name, internal in disks.items():
        d = root / "sys" / "block" / name
        (d / "device").mkdir(parents=True)
        (d / "removable").write_text("0\n" if internal else "1\n", newline="\n")
        for i in (1, 2):
            (d / f"{name}{i}").mkdir()
            (d / f"{name}{i}" / "partition").write_text(f"{i}\n", newline="\n")
    return root


def data_mount(tmp_path: Path, disks: dict[str, bool], media: str) -> tuple[str, list[str]]:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    (run / "usb-media.disks").write_text(media, newline="\n")
    root = usb_root(tmp_path, disks)
    src = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in USB_LIBS)
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r} IMAGESRV_TEST=1; {src} "
             'blkid() { eval "_b=\\${$#}"; case "${_b##*/}" in *1) echo IMGSRV_USB ;; *2) echo IMGSRV_DATA ;; esac; }; '
             f'mount() {{ eval "_n=\\${{$(($# - 1))}}"; echo "${{_n##*/}}" >> {posix(run / "mounts")!r}; }}; '
             'usb_data_mount; echo "rc=$? state=$USB_DATA_STATE"')
    mounts = (run / "mounts").read_text().split() if (run / "mounts").exists() else []
    return out.strip().splitlines()[-1], mounts


def test_the_stick_is_mounted_and_an_internal_drawer_with_the_label_is_not(tmp_path):
    last, mounts = data_mount(tmp_path, {"sda": True, "sdb": False}, "sda\nsdb\n")
    assert last == "rc=0 state=mounted", last
    assert mounts == ["sdb2"], mounts


def test_only_internal_disks_with_the_label_mount_nothing(tmp_path):
    last, mounts = data_mount(tmp_path, {"sda": True}, "sda\n")
    assert last.startswith("rc=1"), last
    assert mounts == [], mounts


def test_two_sticks_with_a_data_partition_mount_neither(tmp_path):
    last, mounts = data_mount(tmp_path, {"sdb": False, "sdc": False}, "sdb\nsdc\n")
    assert last == "rc=1 state=failed", last
    assert mounts == [], mounts


def find_media(tmp_path: Path, disks: dict[str, bool]) -> tuple[str, str | None]:
    """usb_find_media with every disk labelled like the stick: a drawer must not be it."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    root = usb_root(tmp_path, disks)
    src = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in USB_LIBS)
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r} IMAGESRV_TEST=1 USB_FIND_S=0; {src} "
             'blkid() { eval "_b=\\${$#}"; case "${_b##*/}" in *1) echo IMGSRV_USB ;; *2) echo IMGSRV_DATA ;; esac; }; '
             'usb_find_media; echo "rc=$?"')
    media = run / "usb-media.disks"
    return out.strip().splitlines()[-1], media.read_text() if media.exists() else None


def test_an_internal_drawer_with_the_sticks_labels_is_not_the_stick(tmp_path):
    last, media = find_media(tmp_path, {"sda": True, "sdb": False})
    assert last == "rc=0" and media is not None and media.split() == ["sdb"], (last, media)


def test_only_an_internal_drawer_with_the_labels_is_no_stick_at_all(tmp_path):
    """Before the stick enumerated: keep waiting (here USB_FIND_S=0 -- not found), never the drawer."""
    last, media = find_media(tmp_path, {"sda": True})
    assert last == "rc=1" and media is None, (last, media)


# --- 4: duplicate targets ---------------------------------------------------------------


def test_a_start_that_names_one_target_twice_is_refused(tmp_path):
    out = lc_run(tmp_path, DISP + 'echo local > "$LC/screen"; echo idle > "$LC/phase"; '
                 'try "lc-start|sdc|sda,sda|0"; try "lc-start|sdc|sda,sdb,sda|0"; try "lc-start|sdc|sda,sdb|0"')
    lines = out.stdout.splitlines()
    assert "refused lc-start|sdc|sda,sda|0" in lines and "refused lc-start|sdc|sda,sdb,sda|0" in lines, lines
    assert "ok lc-start|sdc|sda,sdb|0" in lines, lines


# --- 5: gui_error is one record ---------------------------------------------------------


def test_a_server_detail_with_newlines_stays_one_record_per_key(tmp_path):
    gui = tmp_path / "gui"; gui.mkdir()
    sh(f"export GUI_DIR={posix(gui)!r}; . {posix(flat_lib())}/guibridge.sh; "
       "gui_error \"$(printf 'נדחה\\nform_error=\\r\\nroom_error=x')\" en 2>/dev/null; :")
    lines = (gui / "error").read_text(encoding="utf-8").splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == ["toast", "form_error", "room_error", "class_error"], lines
    assert lines[0].startswith("toast=נדחה ") and "\r" not in lines[0], lines


# --- 6: the cancel wipes only the disk the restore began on -----------------------------


def cancel(tmp_path: Path, serial_now: str | None, size_now: str = "1000215216") -> tuple[str, list[str]]:
    run = tmp_path / "run"; (run / "gui").mkdir(parents=True, exist_ok=True)
    root = tmp_path / "sysroot"; d = root / "sys" / "block" / "sda"; (d / "device").mkdir(parents=True)
    (d / "device" / "serial").write_text("S6PNNS0T\n", newline="\n"); (d / "size").write_text("1000215216\n", newline="\n")
    src = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in ("common", "sysinfo", "diskinfo", "srgui"))
    swap = ""
    if serial_now is not None:
        swap = (f'printf "%s\\n" {serial_now!r} > {posix(d / "device" / "serial")!r}; '
                f'printf "%s\\n" {size_now!r} > {posix(d / "size")!r}; ')
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r} DEVROOT=/nonexistent IMAGESRV_TEST=1; {src} "
             'sgdisk() { echo "SGDISK $*" >> "$RUN_DIR/trace"; }; blockdev() { :; }; pull_close() { :; }; '
             'target_set() { echo "TARGET $*" >> "$RUN_DIR/trace"; }; sr_gui_message() { echo "MSG $1" >> "$RUN_DIR/trace"; }; '
             "sr_gui_open() { :; }; json_get() { echo null; }; sr_disk_label() { printf x; }; "
             'sr_gui_start /dev/null sda; ' + swap + 'sr_cancelled sda ""; echo "rc=$?"')
    trace = (run / "trace").read_text(encoding="utf-8").splitlines() if (run / "trace").exists() else []
    return out.strip().splitlines()[-1], trace


def test_the_cancel_wipes_the_disk_the_restore_began_on(tmp_path):
    last, trace = cancel(tmp_path, None)
    assert last == "rc=0", (last, trace)
    assert any(t.startswith("SGDISK --zap-all") for t in trace), trace


@pytest.mark.parametrize("serial,size", [("OTHER-DISK", "1000215216"), ("S6PNNS0T", "500107862"), ("", "")])
def test_another_disk_in_the_slot_is_not_wiped(tmp_path, serial, size):
    last, trace = cancel(tmp_path, serial, size)
    assert last == "rc=1", (last, trace)
    assert not any(t.startswith("SGDISK") for t in trace), trace
    assert any(t.startswith("TARGET sda failed") and "לא נמחקה" in t for t in trace), trace


# --- 7: the scan does not mount under the local clone ----------------------------------


def test_the_used_bytes_scan_mounts_nothing_while_the_local_clone_screen_is_up(tmp_path):
    b = box_with_disk(tmp_path)
    (b.run / "lc").mkdir(); (b.run / "lc" / "screen").write_text("local\n", newline="\n")
    b.sh("disk_used_scan")
    assert b.mount_count() == 0
    assert not (b.gui / "used" / "sda").exists()


def test_the_used_bytes_scan_still_mounts_when_idle(tmp_path):
    b = box_with_disk(tmp_path)
    b.sh("disk_used_scan")
    assert b.mount_count() > 0 and (b.gui / "used" / "sda").exists()

