"""#2702 -- טסטי השיכפול המקומי לא משאירים loop, tmpfs או תהליך של root.

ב-08/11 (סבב 1107c) נמצאו על ה-testrunner, תחת חמש תיקיות ריצה, loops עם
קובץ גיבוי `(deleted)` ונקודות עגינה tmpfs של 96MB (`fx/full`, מ-
``test_when_the_drop_fails...`` ומ-``test_bad_sectors_ask...``). ‏`rm -rf` על
תיקיית הריצה דילג עליהן, ‏`/work` התמלא ב-100%, וילדי `sudo` של ריצה שנקטעה
החזיקו את `/tmp/imagesrv-lc-engine.lock` עד שריצה 4 פקעה.

השומר: אחרי שה-fixture של הדיסקים נסגר -- גם כשההרמה נקטעה באמצע -- אין תחת
תיקיית הטסט עגינה או loop, **נקרא ישירות מ-/proc/mounts ומ-sysfs** ולא דרך
הקוד שמנקה. החלק הראשון (הקורא עצמו, ‏`session_verdict`, ההנחיה שכל
fixture עובר דרך `lcfx`) רץ בכל מקום; השני דורש root ו-loop devices.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import hygiene
import lcfx
import mountleak
import orphan_guard
from native import requires_native
from test_local_clone_engine import _sudo_ok, _tool

TESTS = Path(__file__).resolve().parent


# --- the reader: it sees what the kernel holds, and only under the root asked ---------

def _fake_kernel(tmp_path: Path, mounts: list[str], loops: dict[str, str]) -> tuple[Path, Path]:
    proc = tmp_path / "mounts"
    proc.write_text("\n".join(mounts) + "\n", encoding="utf-8")
    sysblock = tmp_path / "block"
    for name, backing in loops.items():
        (sysblock / name / "loop").mkdir(parents=True)
        (sysblock / name / "loop" / "backing_file").write_text(backing + "\n", encoding="utf-8")
    sysblock.mkdir(exist_ok=True)
    return proc, sysblock


def test_the_reader_sees_a_tmpfs_and_a_deleted_loop_under_the_root(tmp_path):
    root = "/work/run/tmp/pytest-0/test_x0/fx"
    proc, sysblock = _fake_kernel(
        tmp_path,
        [f"lcfull {root}/full tmpfs rw,size=98304k 0 0", "/dev/sda1 / ext4 rw 0 0"],
        {"loop21": f"{root}/src.img (deleted)", "loop4": f"{root}/full/full.img", "loop9": "/srv/other/x.img"})
    left = mountleak.leftovers(root, proc, sysblock)
    assert left == [f"mount lcfull (tmpfs) at {root}/full",
                    f"loop loop21 -> {root}/src.img (deleted)",
                    f"loop loop4 -> {root}/full/full.img"], left


def test_the_reader_keeps_to_whole_names_and_decodes_spaces(tmp_path):
    root = "/work/run/fx"
    proc, sysblock = _fake_kernel(
        tmp_path,
        [f"t {root}2/full tmpfs rw 0 0", f"t {root}-old tmpfs rw 0 0", r"t /work/run/fx/with\040space tmpfs rw 0 0"],
        {"loop1": f"{root}2/a.img", "loop2": "/work/run/f/a.img"})
    assert mountleak.leftovers(root, proc, sysblock) == ["mount t (tmpfs) at /work/run/fx/with space"]


def test_a_clean_kernel_reads_clean(tmp_path):
    proc, sysblock = _fake_kernel(tmp_path, ["/dev/sda1 / ext4 rw 0 0"], {})
    assert mountleak.leftovers("/work/run/fx", proc, sysblock) == []


def test_the_run_is_red_when_a_mount_survives_it(monkeypatch):
    """session_verdict is what turns a leftover into a failed run, and detaches it."""
    swept = []
    monkeypatch.setattr(mountleak, "supported", lambda *_: True)
    monkeypatch.setattr(mountleak, "wait_clean", lambda root, timeout=0: [f"mount lcfull (tmpfs) at {root}/full"])
    monkeypatch.setattr(mountleak, "sweep", lambda root: swept.append(root))
    monkeypatch.setattr(hygiene, "scan_supported", lambda: True)
    monkeypatch.setattr(hygiene, "live_stream_processes", lambda _marker: [])
    verdict = hygiene.session_verdict("/work/run/tmp/pytest-3", False)
    assert any("2702" in line for line in verdict) and any("/pytest-3/full" in line for line in verdict), verdict
    assert swept == ["/work/run/tmp/pytest-3"]


# --- no fixture of this family sets itself up outside lcfx ----------------------------

def test_every_loop_fixture_goes_through_lcfx():
    """A fixture that calls the script's setup by hand writes its own try/finally -- the
    four copies of it are how three of them came to leave nothing read back (#2702)."""
    by_hand = re.compile(r"FIXTURE\), " + r'"setup')
    offenders = [p.name for p in sorted(TESTS.glob("test_*.py"))
                 if p.name != Path(__file__).name and by_hand.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"use lcfx.fixture_dir(fx, 'setup...') instead of calling the script: {offenders}"


# --- an interrupted run unwinds its fixtures --------------------------------------------

VICTIM = """import sys, time
sys.path.insert(0, {tests!r})
import hygiene
assert hygiene.install_sigterm_as_interrupt()
try:
    print('ready', flush=True)
    time.sleep(60)
except KeyboardInterrupt:
    print('interrupted', flush=True)
finally:
    print('finalizer ran', flush=True)
"""


@pytest.mark.skipif(os.name == "nt", reason="תחנת פיתוח בלבד: SIGTERM אינו קיים בווינדוס")
def test_sigterm_unwinds_like_ctrl_c_so_the_finalizers_run(tmp_path):
    """`timeout 5400 pytest` ends a run with SIGTERM, which killed it with no finalizer: the fixtures
    that detach loops and unmount the tmpfs never ran. With the trap it is a KeyboardInterrupt."""
    script = tmp_path / "victim.py"
    script.write_text(VICTIM.format(tests=str(TESTS)), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        os.kill(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
    finally:
        hygiene.kill_group(proc.pid)
    assert out.split() == ["interrupted", "finalizer", "ran"], out


# --- on the Testrunner: the real thing -------------------------------------------------

NATIVE = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, _tool(t)) for t in ("losetup", "sgdisk", "mkntfs", "mkfs.vfat", "mkfs.ext4", "ntfs-3g", "blockdev")],
    posix=True, why="loop devices and a tmpfs as root: the Testrunner")


def _own_eyes(root: Path) -> list[str]:
    """/proc/mounts and sysfs read here, not through the code that cleans."""
    names = {str(root), str(root.resolve())}
    mounts = [ln for ln in Path("/proc/mounts").read_text(errors="replace").splitlines()
              if any(n in ln.split()[1] for n in names)]
    loops = [f"{b.parent.parent.name}: {b.read_text().strip()}" for b in Path("/sys/block").glob("loop*/loop/backing_file")
             if any(n in b.read_text() for n in names)]
    return mounts + loops


@NATIVE
def test_the_fixture_with_its_tmpfs_leaves_nothing_under_the_test_dir(tmp_path):
    """`setup` is the one with fx/full: a 96MB tmpfs and a loop on a file in it."""
    fx = tmp_path / "fx"
    with lcfx.fixture_dir(fx, "setup"):
        assert any("full" in ln for ln in _own_eyes(tmp_path)), "the fixture did not mount its tmpfs: the guard is blind"
    assert _own_eyes(tmp_path) == []


@NATIVE
def test_a_setup_cut_short_leaves_nothing_either(tmp_path):
    """The cut lands in the middle of `setup`: it is made once the first loop exists (#2738),
    not after N seconds -- a fast machine finishes setup in any fixed window."""
    fx = tmp_path / "fx"
    with pytest.raises(lcfx.SetupCut) as cut:
        with lcfx.fixture_dir(fx, "setup", cut_when=lambda seen: any(s.startswith("loop ") for s in seen)):
            pytest.fail("setup finished before the first loop was seen: the interruption was not tested")
    assert any(s.startswith("loop ") for s in cut.value.seen), cut.value.seen
    assert not any("(tmpfs)" in s for s in cut.value.seen), f"cut too late, the tmpfs was already up: {cut.value.seen}"
    assert _own_eyes(tmp_path) == []
    assert orphan_guard.processes_mentioning(str(fx)) == [], "root's children of the cut setup are still running"


@NATIVE
def test_a_timed_out_root_command_takes_its_children_with_it(tmp_path):
    """subprocess.run kills the `sudo` and leaves the root child running, holding what it held
    (the engine lock). The group is killed by the PID saved at start."""
    marker = f"{tmp_path}/lock-holder"
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            lcfx.root("sh", "-c", f"sleep 300 # {marker}", timeout=2)
        assert orphan_guard.processes_mentioning(marker) == []
    finally:
        for pid, _ in orphan_guard.processes_mentioning(marker):      # never leave it if the check is red
            hygiene.kill_pid(pid)


@NATIVE
def test_the_orphan_guard_kills_a_root_orphan_by_pid(tmp_path):
    marker = f"{tmp_path}/orphan"
    holder = subprocess.Popen(["sudo", "-n", "sh", "-c", f"sleep 300 # {marker}"], start_new_session=True,
                              stdin=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 5
        while len(orphan_guard.processes_mentioning(marker)) < 2 and time.monotonic() < end:
            time.sleep(0.1)
        assert orphan_guard.processes_mentioning(marker), "the holder did not start"
        holder.kill()                       # SIGKILL on the sudo only: root's sh outlives it, as in the field
        holder.wait(timeout=5)
        left = orphan_guard.settle_and_kill(marker, grace=0.2)
        assert left, "nothing was left to kill: the test did not reproduce the orphan"
        assert orphan_guard.processes_mentioning(marker) == [], "a root orphan survived the guard"
    finally:
        for pid, _ in orphan_guard.processes_mentioning(marker):
            hygiene.kill_pid(pid)
