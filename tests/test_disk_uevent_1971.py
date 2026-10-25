"""#1971 -- drawer insert/remove seen from the kernel's uevents, not a poll.

The listener (agent/uevent.c) touches a file on a whole-disk add/remove/change;
the cloner's state loop (clonergui.sh) wakes on it. Verified 22/10: a record
whose last string has no NUL was read past its end (strcmp on the raw buffer);
a full socket buffer (ENOBUFS) ended the listener; both fixed. The last test is
a REAL kernel uevent: a loop device of the test's own, `change` written to its
sysfs uevent through sudo -- the listener must see it on the netlink socket.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from native import requires_native
from test_agent import AGENT, posix
from test_timeouts import run_sh

GCC = shutil.which("gcc") or shutil.which("cc")
SBIN_PATH = os.environ.get("PATH", "") + ":/usr/sbin:/sbin"
pytestmark = requires_native(("gcc", GCC))


@pytest.fixture(scope="module")
def listener(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("uevent") / "imagectl-uevent"
    build = subprocess.run([GCC, "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out), str(AGENT / "uevent.c")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return out


def _parse(listener: Path, mark: Path, record: bytes) -> int:
    mark.unlink(missing_ok=True)
    return subprocess.run([listener, "--parse", mark], input=record, capture_output=True).returncode


@pytest.mark.parametrize("action", [b"add", b"remove", b"change"])
def test_a_whole_disk_event_touches_the_file(listener, tmp_path, action):
    mark = tmp_path / "changed"
    rec = b"%s@/devices/x/block/sda\0ACTION=%s\0SUBSYSTEM=block\0DEVTYPE=disk\0DEVNAME=sda\0" % (action, action)
    assert _parse(listener, mark, rec) == 0 and mark.read_text() == "1\n"


@pytest.mark.parametrize("rec", [
    b"add@/x\0ACTION=add\0SUBSYSTEM=block\0DEVTYPE=partition\0DEVNAME=sda1\0",   # a partition
    b"add@/x\0ACTION=add\0SUBSYSTEM=net\0DEVTYPE=disk\0",                        # not block
    b"bind@/x\0ACTION=bind\0SUBSYSTEM=block\0DEVTYPE=disk\0",                     # not add/remove/change
], ids=["partition", "net", "bind"])
def test_other_events_touch_nothing(listener, tmp_path, rec):
    mark = tmp_path / "changed"
    assert _parse(listener, mark, rec) == 2 and not mark.exists()


def test_a_record_cut_without_its_last_nul_is_read_to_its_end_only(listener, tmp_path):
    """The last string ends at the buffer: `DEVTYPE=disk` with no NUL must still
    match, and a cut `DEVTYPE=dis` must not -- nothing past the bytes received."""
    mark = tmp_path / "changed"
    assert _parse(listener, mark, b"add@/x\0ACTION=add\0SUBSYSTEM=block\0DEVTYPE=disk") == 0
    assert _parse(listener, mark, b"add@/x\0ACTION=add\0SUBSYSTEM=block\0DEVTYPE=dis") == 2


def test_a_full_buffer_with_no_nul_is_never_read_past_its_end(tmp_path):
    """The memory-safety half, made visible: AddressSanitizer build, a record that
    fills the whole buffer and ends in `DEVTYPE=disk` with no NUL. The first
    version strcmp-ed past the array (ASan: stack-buffer-overflow, measured
    22/10 on the testrunner); a cut record must be refused, not overrun."""
    asan = tmp_path / "uevent-asan"
    build = subprocess.run([GCC, "-O1", "-g", "-fsanitize=address", "-fno-omit-frame-pointer",
                            "-o", str(asan), str(AGENT / "uevent.c")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    head, tail = b"add@/x\0ACTION=add\0SUBSYSTEM=block\0", b"DEVTYPE=disk"
    rec = head + b"X" * (4096 - len(head) - len(tail) - 1) + b"\0" + tail
    assert len(rec) == 4096
    mark = tmp_path / "changed"
    run = subprocess.run([asan, "--parse", mark], input=rec, capture_output=True)
    assert b"AddressSanitizer" not in run.stderr, run.stderr.decode(errors="replace")[-800:]
    assert run.returncode in (0, 2), run.returncode


def test_the_state_loop_wakes_on_the_file_not_on_its_second(tmp_path):
    """clonergui's loop: a disk-event file ends its 1 s wait at once (0.1 s ticks)."""
    gui = tmp_path / "gui"
    gui.mkdir()
    passes = tmp_path / "passes"
    run_sh(f"""export RUN_DIR={posix(tmp_path)!r} GUI_DIR={posix(gui)!r}
. {posix(AGENT)}/lib/clonergui.sh
lc_gui_active() {{ return 1; }}
n=0; cloner_gui_state() {{ n=$((n+1)); echo "pass $n $(date +%s%N)" >> {posix(passes)!r}; [ $n -lt 3 ] || exit 0; }}
( sleep 0.3; : > "$GUI_DIR/disk-event" ) &
cloner_gui_state_loop
""", timeout=20)
    t = [int(line.split()[2]) for line in passes.read_text().splitlines()]
    assert len(t) == 3, t
    assert (t[1] - t[0]) / 1e9 < 0.8, f"{(t[1] - t[0]) / 1e9:.2f}s -- the event did not cut the 1 s wait"


def _sudo_ok() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


@requires_native(("sudo -n", _sudo_ok()), ("losetup", shutil.which("losetup", path=SBIN_PATH)))
def test_a_real_kernel_uevent_on_a_loop_device_of_our_own_is_seen(listener, tmp_path):
    img, mark = tmp_path / "disk.img", tmp_path / "changed"
    img.write_bytes(b"\0" * (4 << 20))
    loop = subprocess.run(["sudo", "-n", "env", f"PATH={SBIN_PATH}", "losetup", "-f", "--show", str(img)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert loop.returncode == 0, loop.stderr
    dev = loop.stdout.strip()
    assert dev.startswith("/dev/loop"), dev
    proc = subprocess.Popen([listener, mark], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)
        mark.unlink(missing_ok=True)   # the losetup's own add/change may have landed first
        name = dev.rsplit("/", 1)[1]
        t = subprocess.run(["sudo", "-n", "sh", "-c", f"echo change > /sys/block/{name}/uevent"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert t.returncode == 0, t.stderr
        end = time.monotonic() + 5
        while not mark.exists() and time.monotonic() < end:
            time.sleep(0.05)
        assert mark.exists(), "the kernel's change uevent for a whole disk was not seen"
    finally:
        proc.kill()
        proc.wait(timeout=5)
        subprocess.run(["sudo", "-n", "env", f"PATH={SBIN_PATH}", "losetup", "-d", dev],
                       capture_output=True, stdin=subprocess.DEVNULL)
