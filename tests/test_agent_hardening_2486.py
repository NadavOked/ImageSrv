"""#2486 -- hardening leads in the agent (the 06/11 triage, leads 1-5; lead 6 needs a VM).

1. **os-release was read with no bound** (`linuxdrivers.sh`): `cat` followed a
   link out of the image and read a device or a FIFO to its end. Now a plain
   file only, the first 64 KiB of it -- and the Linux root is mounted
   `nodev,nosuid` (`hostname_linux.sh` `_mount_linux`, shared by the name, the
   identity reset and this check).
2. **The name was written through whatever /etc/hostname, /etc/hosts and
   /etc/hosts.new were**: a link resolves in the agent's root, not the
   image's. A link, a device or a directory is refused by name
   (`hostname_target_not_regular`) before any of the three is written; a file
   that is not there is still created.
3. **`esp-fsck-repair` took any /dev/... path** (`tools_boot.sh`), `../`, the
   clone stick and a loop device included. Now a partition the kernel lists,
   on one disk that is not the stick -- like `_tools_disk_dev_ok` (#2431).
4. **A capture did not refuse a twin** (`capturegate.sh`): a second disk with
   the same serial and size (#2448) answers the same order. Refused by name,
   before the table is read.
5. **The cancel of a restore compared serial|size only** (`srgui.sh`): two
   disks with no serial and one size are the same "identity". It now also asks
   `assert_target` -- the disk bound at target_init, slot included, and no
   twin. A disk with no serial alone in its slot is still wiped: the cancel's
   promise (#1561) is an empty disk, and `wipe_written_gpt` would break it
   (the cancel removes write_started, and it refuses an empty serial).

Negative control: every production file of the fix back to main -- see the PR body.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_agent import BASH, posix, sh
from test_capture_gates import NO_PARTITIONS, SGDISK_MARK, _serial_pre
from test_capture_refusals import capture_run, refusal_reason
from test_captured_stdout import LINUX_PLAN
from test_hostname_linux_dual import BTRFS_PLAN, box as name_box, name
from test_linuxdrivers import AHCI, NVME, initrd, linux_box, stage_linux
from test_tools_boot import out_text as boot_out, run_boot

pytestmark = requires_native(("bash", BASH))

NOT_REGULAR = "hostname_target_not_regular"


def _symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here (Windows without the privilege) -- runs on the Testrunner")


# --- 1: os-release and the mount ----------------------------------------------------


def test_os_release_past_64k_is_not_read(tmp_path):
    """/dev/zero in an image is one endless line for sed; the cap is what ends it."""
    box = linux_box(tmp_path, image=initrd([AHCI, NVME]))
    (box.root / "usr/lib/os-release").write_bytes(b"x" * 70000 + b'\nPRETTY_NAME="Past the cap"\n')
    result = stage_linux(box, "sda")   # "os" rides in every state (_linux_result), not only generic
    assert "os" not in result, result


def test_an_os_release_link_out_of_the_image_is_not_followed(tmp_path):
    outside = tmp_path / "agent-os-release"
    outside.write_text('PRETTY_NAME="The agent root"\n', newline="\n")
    box = linux_box(tmp_path, image=initrd([AHCI, NVME]))
    (box.root / "usr/lib/os-release").unlink()
    _symlink(outside, box.root / "usr/lib/os-release")
    result = stage_linux(box, "sda")
    assert "os" not in result, result


def test_the_debian_layout_still_names_the_os(tmp_path):
    """usr/lib/os-release is the file; /etc/os-release, a link to it, is not needed."""
    box = linux_box(tmp_path, image=initrd([AHCI, NVME]))
    try:
        os.symlink("../usr/lib/os-release", box.root / "etc/os-release")
    except (OSError, NotImplementedError):
        pass   # the file alone is the same case
    assert stage_linux(box, "sda")["os"] == "Debian GNU/Linux 13 (trixie)"


def test_the_linux_root_is_mounted_nodev_nosuid(tmp_path):
    script, run = name_box(tmp_path, [LINUX_PLAN], default_etc=True)
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True), result
    mounts = (run / "mounts").read_text().splitlines()
    assert mounts and all("-o nodev,nosuid " in m for m in mounts if m.startswith("mount ")), mounts


def test_the_btrfs_top_level_is_mounted_nodev_nosuid_too(tmp_path):
    script, run = name_box(tmp_path, [BTRFS_PLAN], subvols="root")
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True), result
    mounts = [m for m in (run / "mounts").read_text().splitlines() if m.startswith("mount ")]
    assert len(mounts) == 2, mounts
    assert "-o nodev,nosuid " in mounts[0] and "-o subvolid=5,nodev,nosuid " in mounts[1], mounts


# --- 2: /etc/hostname, /etc/hosts, /etc/hosts.new -----------------------------------


def _etc(tmp_path: Path) -> tuple[str, Path, Path]:
    script, run = name_box(tmp_path, [LINUX_PLAN], default_etc=True)
    etc = run / "linux" / "etc"
    etc.mkdir(parents=True)
    return script, run, etc


@pytest.mark.parametrize("victim", ["hostname", "hosts", "hosts.new"])
def test_a_link_in_etc_is_refused_by_name_and_nothing_is_written(tmp_path, victim):
    script, run, etc = _etc(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("ORIGINAL\n", newline="\n")
    (etc / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tBUILD-01\n", newline="\n")
    if victim == "hosts":
        (etc / "hosts").unlink()
    _symlink(outside, etc / victim)
    rc, result = name(script)
    assert rc == 1 and result["ok"] is False, result
    assert result["code"] == NOT_REGULAR, result
    assert f"/etc/{victim}" in result["error"], result
    assert outside.read_text() == "ORIGINAL\n"
    assert not (etc / "hostname").is_file() or victim == "hostname", "written before the refusal"
    assert (etc / victim).is_symlink(), "the link itself was replaced"


def test_a_directory_named_hostname_is_refused_by_name(tmp_path):
    """Not a regular file -- the same refusal, with no link involved."""
    script, run, etc = _etc(tmp_path)
    (etc / "hostname").mkdir()
    rc, result = name(script)
    assert (rc, result["code"]) == (1, NOT_REGULAR), result
    assert "/etc/hostname" in result["error"], result
    assert not (etc / "hosts").exists(), "written before the refusal"


def test_absent_files_are_still_created(tmp_path):
    script, run, etc = _etc(tmp_path)
    rc, result = name(script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05", "method": "etc-hostname"}), result
    assert (etc / "hostname").read_text().strip() == "LAB1-05"
    assert "127.0.1.1\tLAB1-05" in (etc / "hosts").read_text()


def test_regular_files_are_rewritten(tmp_path):
    script, run, etc = _etc(tmp_path)
    (etc / "hostname").write_text("BUILD-01\n", newline="\n")
    (etc / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tBUILD-01\n", newline="\n")
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True), result
    assert (etc / "hostname").read_text().strip() == "LAB1-05"
    assert (etc / "hosts").read_text() == "127.0.0.1\tlocalhost\n127.0.1.1\tLAB1-05\n"


# --- 3: esp-fsck-repair -------------------------------------------------------------


def _fsck(tmp_path: Path, arg: str, pre: str = "") -> tuple[int, str, bool]:
    ran = tmp_path / "fsck.ran"
    proc = run_boot(tmp_path, "esp-fsck-repair", arg=arg, confirm="lab-pc-01", pre=pre,
                    stubs={"fsck.vfat": (f'echo "$*" > {posix(ran)!r}; echo fsck', 0)})
    return proc.returncode, boot_out(tmp_path, "esp-fsck-repair"), ran.exists()


@pytest.mark.parametrize("arg", ["/dev/pts/../../sda1", "/dev/sda/../sda1", "/dev/sdb1", "/dev/loop0p1",
                                 "/dev/sda9", "/dev/sda", "/dev/SDA1", "/dev/"])
def test_only_a_listed_partition_off_the_stick_is_repaired(tmp_path, arg):
    rc, text, ran = _fsck(tmp_path, arg)
    assert rc == 1, (arg, rc, text)
    assert not ran, (arg, text)
    assert "לא חוקית" in text, text


def test_a_partition_on_an_internal_disk_is_repaired(tmp_path):
    rc, text, ran = _fsck(tmp_path, "/dev/sda1")
    assert rc == 0 and ran, text


def test_a_stick_list_that_does_not_read_is_a_refusal_not_a_pass(tmp_path):
    """grep's 2 is the check that broke -- not "not the stick" (#231)."""
    pre = 'rm -f "$RUN_DIR/usb-media.disks"; mkdir "$RUN_DIR/usb-media.disks"'
    rc, text, ran = _fsck(tmp_path, "/dev/sda1", pre=pre)
    assert rc == 1 and not ran, text


# --- 4: a capture from one of two twins -------------------------------------------


def _twin_pre(size: str | None = None) -> str:
    size_cmd = (f'echo {size} > "$SYSROOT/sys/block/sdb/size"; ' if size
                else 'cp "$SYSROOT/sys/block/sda/size" "$SYSROOT/sys/block/sdb/size"; ')
    return (_serial_pre("SERIAL-A") + 'mkdir -p "$SYSROOT/sys/block/sdb/device"; '
            'printf "%s\\n" SERIAL-A > "$SYSROOT/sys/block/sdb/device/serial"; ' + size_cmd)


def test_a_capture_with_a_twin_in_the_machine_is_refused_by_name(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK},
                                shell_pre=_twin_pre(), serial="SERIAL-A")
    reason = refusal_reason(box, run, out)
    assert "אותו מספר סידורי" in reason and "sdb" in reason, reason
    assert not (run / "sgdisk.ran").exists(), "the table was read -- the refusal was not before it"


def test_the_same_serial_on_a_disk_of_another_size_is_no_twin(tmp_path):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": SGDISK_MARK},
                                shell_pre=_twin_pre("999999"), serial="SERIAL-A")
    assert NO_PARTITIONS in refusal_reason(box, run, out)
    assert (run / "sgdisk.ran").exists()


# --- 5: the cancel of a restore ----------------------------------------------------


SIZE = "1000215216"


def cancel(tmp_path: Path, disks: dict[str, str | None], between: str = "") -> tuple[str, list[str]]:
    """disk -> serial (None = none). The restore began on sda; `between` runs
    after it began and before the cancel (a disk swapped, a slot changed)."""
    run = tmp_path / "run"; (run / "gui").mkdir(parents=True, exist_ok=True)
    root = tmp_path / "sysroot"
    for disk, serial in disks.items():
        d = root / "sys" / "block" / disk; (d / "device").mkdir(parents=True)
        (d / "size").write_text(SIZE + "\n", newline="\n")
        if serial is not None:
            (d / "device" / "serial").write_text(serial + "\n", newline="\n")
    (run / "port").write_text("1\n", newline="\n")
    src = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in ("common", "sysinfo", "diskinfo", "failmark", "srgui"))
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r} DEVROOT=/nonexistent IMAGESRV_TEST=1; {src} "
             'sgdisk() { echo "SGDISK $*" >> "$RUN_DIR/trace"; }; blockdev() { :; }; pull_close() { :; }; '
             'target_set() { echo "TARGET $*" >> "$RUN_DIR/trace"; }; sr_gui_message() { echo "MSG $1" >> "$RUN_DIR/trace"; }; '
             "sr_gui_open() { :; }; json_get() { echo null; }; sr_disk_label() { printf x; }; "
             'disk_port() { cat "$RUN_DIR/port"; }; '
             'target_bind sda; sr_gui_start /dev/null sda; ' + between + 'sr_cancelled sda ""; echo "rc=$?"')
    trace = (run / "trace").read_text(encoding="utf-8").splitlines() if (run / "trace").exists() else []
    return out.strip().splitlines()[-1], trace


def test_two_disks_with_no_serial_and_one_size_are_not_wiped(tmp_path):
    last, trace = cancel(tmp_path, {"sda": None, "sdb": None})
    assert last == "rc=1", (last, trace)
    assert not any(t.startswith("SGDISK") for t in trace), trace
    assert any(t.startswith("TARGET sda failed") for t in trace), trace
    assert any(t.startswith("MSG השחזור בוטל — הטבלה לא נמחקה") for t in trace), trace


def test_a_disk_with_no_serial_in_another_slot_is_not_wiped(tmp_path):
    """The one the restore began on is gone; another, serial-less and the same
    size, came up in another slot -- serial|size alone cannot tell them apart."""
    last, trace = cancel(tmp_path, {"sda": None}, between='echo 2 > "$RUN_DIR/port"; ')
    assert last == "rc=1", (last, trace)
    assert not any(t.startswith("SGDISK") for t in trace), trace


def test_a_lone_disk_with_no_serial_is_still_wiped_on_cancel(tmp_path):
    """#1561's promise holds where wipe_written_gpt would refuse: a VM, an old disk."""
    last, trace = cancel(tmp_path, {"sda": None})
    assert last == "rc=0", (last, trace)
    assert any(t.startswith("SGDISK --zap-all") for t in trace), trace


def test_a_disk_with_a_serial_next_to_another_is_still_wiped(tmp_path):
    last, trace = cancel(tmp_path, {"sda": "S6PNNS0T", "sdb": "OTHER-1"})
    assert last == "rc=0", (last, trace)
    assert any(t.startswith("SGDISK --zap-all") for t in trace), trace

