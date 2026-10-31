"""#1738 — the local clone toward the SATA II ceiling (224MB/s on metal 17/10,
the source alone 267).

Two changes in the product, and the knobs the metal measurement turns:

* fanout writes a chunk straight from its read buffer when nothing is queued
  for that target, instead of copying every byte into a 256MB ring first (one
  cache-cold memcpy per byte, per fanout -- two per local clone target). The
  ring still holds what a full pipe did not take, in order.
* the copy's watch loop sees the end of a partition within a tenth of a second
  (``_nap``), not after a whole ``sleep 1``.
* ``$LC/tune`` (lctune.sh): block sizes, read-ahead, write-back and partclone -b
  for one run, restored after; nothing there can make a target "ok".
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from test_fanout import make_fifo, needs_fanout, run_fanout
from test_local_clone_agent import STREAM_STUBS, run
from native import requires_native
from test_cloner_gui import BASH, posix

pytestmark = requires_native(("bash", BASH))

F_GETPIPE_SZ = 1032
QUEUE_LINE = re.compile(r"fanout: (\S+) took (\d+) bytes, (\d+) of them through its queue")


@pytest.fixture(scope="module")
def fanout(tmp_path_factory):
    binary = tmp_path_factory.mktemp("build") / "fanout"
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(binary),
                    str(Path(__file__).resolve().parent.parent / "agent" / "fanout.c")],
                   check=True, stdin=subprocess.DEVNULL)
    return binary


def _tune(tmp_path: Path, text: str) -> None:
    """$LC/tune as a person writes it over SSH (run(): LC = $RUN_DIR/lc)."""
    lc = tmp_path / "run" / "lc"; lc.mkdir(parents=True, exist_ok=True)
    (lc / "tune").write_bytes(text.encode())


def _queue_report(err: str) -> dict[str, tuple[int, int]]:
    return {Path(m.group(1)).parent.name: (int(m.group(2)), int(m.group(3))) for m in QUEUE_LINE.finditer(err)}


# --- fanout: straight from the read buffer ------------------------------------------

@needs_fanout
def test_a_target_that_keeps_up_takes_every_byte_straight_from_the_read(fanout, tmp_path):
    """A file never says "full": nothing may go through the ring at all."""
    data = os.urandom(6 * 1024 * 1024 + 12345)
    out = tmp_path / "sda"; out.mkdir(); target = out / "feed"; target.write_bytes(b"")
    code, report, err = run_fanout(fanout, [target], data, buffer=str(4 * 1024 * 1024))
    assert code == 0 and report == {"sda": "ok"}, (report, err)
    assert target.read_bytes() == data
    assert _queue_report(err) == {"sda": (len(data), 0)}, err


@needs_fanout
def test_a_target_that_falls_behind_queues_and_keeps_every_byte_in_order(fanout, tmp_path):
    """The mixed path: the first chunk straight to the pipe, the rest behind it in
    the ring while the reader sleeps -- and still byte for byte, in order."""
    data = os.urandom(8 * 1024 * 1024)
    fifo = make_fifo(tmp_path, "sdb"); sink: list[bytes] = []

    def late_reader():
        with open(fifo, "rb") as handle:
            time.sleep(0.5)
            while chunk := handle.read(64 * 1024):
                sink.append(chunk)
    thread = threading.Thread(target=late_reader, daemon=True); thread.start()
    code, report, err = run_fanout(fanout, [fifo], data, buffer=str(16 * 1024 * 1024))
    thread.join(timeout=60)
    assert not thread.is_alive()
    assert code == 0 and report == {"sdb": "ok"}, (report, err)
    assert hashlib.sha256(b"".join(sink)).hexdigest() == hashlib.sha256(data).hexdigest()
    took, queued = _queue_report(err)["sdb"]
    assert took == len(data) and 0 < queued < len(data), err


@needs_fanout
@pytest.mark.parametrize("env,want", [({}, 1 << 20), ({"FANOUT_PIPE_BYTES": str(4 << 20)}, 4 << 20)])
def test_the_pipe_size_is_the_knob_or_the_measured_default(fanout, tmp_path, env, want):
    data = os.urandom(2 * 1024 * 1024)
    fifo = make_fifo(tmp_path, "sdc"); seen: list[int] = []

    def reader():
        with open(fifo, "rb") as handle:
            first = handle.read(1)
            seen.append(fcntl.fcntl(handle.fileno(), F_GETPIPE_SZ))
            while handle.read(64 * 1024):
                pass
            assert first
    thread = threading.Thread(target=reader, daemon=True); thread.start()
    code, report, err = run_fanout(fanout, [fifo], data, env=env)
    thread.join(timeout=30)
    assert code == 0 and report == {"sdc": "ok"}, (report, err)
    # Not root (the Testrunner, 18/10): the kernel refuses more than fs.pipe-max-size,
    # and fanout takes that ceiling, aloud -- never the 64KB default in silence.
    ceiling = int(Path("/proc/sys/fs/pipe-max-size").read_text())
    if os.geteuid() == 0 or want <= ceiling:
        assert seen == [want], (seen, err)
        assert "refused" not in err and "could not enlarge" not in err, err
    else:
        assert seen == [ceiling], (seen, ceiling, err)
        assert f"pipe {fifo}: {want} refused (Operation not permitted), set to {ceiling} (fs.pipe-max-size)" in err, err


@needs_fanout
@pytest.mark.parametrize("env,refused", [
    ({"FANOUT_CHUNK": str(4 << 20)}, None),                  # 4MB reads into an 8MB buffer
    ({"FANOUT_CHUNK": "abc"}, "FANOUT_CHUNK"),
    ({"FANOUT_CHUNK": str(64 << 20)}, "FANOUT_CHUNK"),       # above one target's buffer: make_room could not hold it
    ({"FANOUT_CHUNK": "4096"}, "FANOUT_CHUNK"),              # below READ_CHUNK
    ({"FANOUT_PIPE_BYTES": str(1 << 31)}, "FANOUT_PIPE_BYTES"),
])
def test_block_size_knobs_are_taken_or_refused_aloud_never_half(fanout, tmp_path, env, refused):
    data = os.urandom(9 * 1024 * 1024)
    out = tmp_path / "sda"; out.mkdir(); target = out / "feed"; target.write_bytes(b"")
    code, report, err = run_fanout(fanout, [target], data, buffer=str(8 * 1024 * 1024), env=env)
    assert code == 0 and report == {"sda": "ok"}, (report, err)
    assert target.read_bytes() == data
    assert ("ignoring invalid" in err) == (refused is not None), err
    if refused:
        assert f"ignoring invalid {refused}" in err, err


# --- lccopy.sh: the end of a partition, seen at once ------------------------------

def test_the_end_of_a_stream_is_seen_within_a_tenth_not_a_second(tmp_path):
    """#1705 did this for wait_pid; the copy's own watch loop still slept a whole
    second, once per partition, after the stream had already ended."""
    out = run(tmp_path, 'mkdir -p "$LC/run"; echo 5 > "$LC/run/c"; ( sleep 0.2 ) & p=$!; '
              't0=$(date +%s%N); _lc_wait_stream "$p" "$LC/run/c"; rc=$?; '
              'echo "rc=$rc ms=$(( ($(date +%s%N) - t0) / 1000000 ))"')
    assert out.returncode == 0, out.stderr
    rc, ms = re.search(r"rc=(\d+) ms=(\d+)", out.stdout).groups()
    assert rc == "0" and int(ms) < 900, out.stdout


# --- lctune.sh: the knobs -------------------------------------------------------------

def test_no_tune_file_is_exactly_the_product(tmp_path):
    out = run(tmp_path, 'mkdir -p "$LC/run"; lc_tune_begin sdc sda; '
              'echo "z=$LC_SRC_Z pipe=${FANOUT_PIPE_BYTES-unset} direct=$LC_EXP_DIRECT"; cat "$LC/run/tune.applied"')
    assert out.returncode == 0, out.stderr
    lines = [l for l in out.stdout.splitlines() if not l.startswith("imagesrv:")]
    # the one default (metal 18/10, #1749) -- and a test run with no SYSROOT never
    # writes the host's kernel, so here it says it was not applied
    assert lines == ["z=524288 pipe=unset direct=0",
                     "LC_DIRTY_BG_BYTES=67108864 (default) (LC_DIRTY_BG_BYTES not applied)"], out.stdout


def test_known_numbers_are_applied_and_everything_else_refused_aloud(tmp_path):
    tune = "LC_SRC_Z=4194304\nFANOUT_PIPE_BYTES=16777216\nBOGUS=1\nLC_SRC_RA_KB=abc\n# a note\n"
    _tune(tmp_path, tune)
    out = run(tmp_path, 'mkdir -p "$LC/run"; LOG_FILE=$RUN_DIR/log; '
              'lc_tune_begin sdc sda; echo "z=$LC_SRC_Z ra=${LC_SRC_RA_KB:-none}"; sh -c \'echo "child=$FANOUT_PIPE_BYTES"\'; '
              'lc_tune_end sda; sh -c \'echo "after=${FANOUT_PIPE_BYTES-unset}"\'; cat "$LC/run/tune.applied"; cat "$RUN_DIR/log"')
    assert out.returncode == 0, out.stderr
    lines = [l for l in out.stdout.splitlines() if not l.startswith("imagesrv:")]   # log() also prints
    assert lines[:3] == ["z=4194304 ra=none", "child=16777216", "after=unset"], lines
    assert any(l.startswith("LC_SRC_Z=4194304 FANOUT_PIPE_BYTES=16777216 ") for l in lines), lines
    assert "unknown key 'BOGUS' -- refused" in out.stdout and "LC_SRC_RA_KB='abc' is not a number -- refused" in out.stdout


def test_read_ahead_and_write_back_are_set_for_the_copy_and_restored(tmp_path):
    sysroot = tmp_path / "root"
    (sysroot / "sys/block/sdc/queue").mkdir(parents=True); (sysroot / "proc/sys/vm").mkdir(parents=True)
    (sysroot / "sys/block/sdc/queue/read_ahead_kb").write_text("128\n")
    (sysroot / "proc/sys/vm/dirty_background_bytes").write_text("0\n")
    (sysroot / "proc/sys/vm/dirty_background_ratio").write_text("10\n")
    s = posix(sysroot)
    out = run(tmp_path, 'mkdir -p "$LC/run"; printf "LC_SRC_RA_KB=8192\\nLC_DIRTY_BG_BYTES=33554432\\n" > "$LC/tune"; '
              f'export SYSROOT={s!r}; lc_tune_begin sdc sda; '
              'echo "during=$(cat $SYSROOT/sys/block/sdc/queue/read_ahead_kb)|$(cat $SYSROOT/proc/sys/vm/dirty_background_bytes)"; '
              'echo 0 > $SYSROOT/proc/sys/vm/dirty_background_bytes; lc_tune_end sda; '
              'echo "after=$(cat $SYSROOT/sys/block/sdc/queue/read_ahead_kb)|$(cat $SYSROOT/proc/sys/vm/dirty_background_ratio)"; '
              'cat "$LC/run/tune.applied"')
    assert out.returncode == 0, out.stderr
    assert "during=8192|33554432" in out.stdout and "after=128|10" in out.stdout, out.stdout
    assert "not applied" not in out.stdout, out.stdout


def _vm(tmp_path: Path, bytes_: str, ratio: str) -> Path:
    vm = tmp_path / "root" / "proc/sys/vm"; vm.mkdir(parents=True)
    (vm / "dirty_background_bytes").write_text(bytes_ + "\n"); (vm / "dirty_background_ratio").write_text(ratio + "\n")
    return tmp_path / "root"


# The kernel: writing one of the pair zeroes the other. The script does that by
# hand during the copy, so "after" proves the restore wrote the old value back.
KERNEL_SIDE = ('b=$(cat $SYSROOT/proc/sys/vm/dirty_background_bytes); r=$(cat $SYSROOT/proc/sys/vm/dirty_background_ratio); '
               'echo "during=$b|$r"; [ "$b" = 0 ] || echo 0 > $SYSROOT/proc/sys/vm/dirty_background_ratio; ')
AFTER = 'echo "after=$(cat $SYSROOT/proc/sys/vm/dirty_background_bytes)|$(cat $SYSROOT/proc/sys/vm/dirty_background_ratio)"; '


@pytest.mark.parametrize("before,field,want", [
    (("0", "10"), 1, "10"),                    # the usual kernel: a ratio, and the ratio is written back
    (("134217728", "0"), 0, "134217728"),      # a machine set in bytes gets its bytes back
])
def test_the_write_back_default_holds_for_the_copy_with_no_tune_file_and_is_restored(tmp_path, before, field, want):
    """Metal 18/10 (#1749): 64MB dirty_background_bytes, copy_s 188 -> 183, 3 of 3.
    (A fake /proc does not zero the other of the pair, so only the one written back is read.)"""
    root = _vm(tmp_path, *before)
    out = run(tmp_path, f'mkdir -p "$LC/run"; export SYSROOT={posix(root)!r}; lc_tune_begin sdc sda; ' + KERNEL_SIDE +
              'lc_tune_end sda; ' + AFTER + 'cat "$LC/run/tune.applied"')
    assert out.returncode == 0, out.stderr
    assert "during=67108864|" in out.stdout, out.stdout
    after = re.search(r"^after=(\d+)\|(\d+)$", out.stdout, re.M).groups()
    assert after[field] == want, out.stdout
    assert "LC_DIRTY_BG_BYTES=67108864 (default)" in out.stdout and "not applied" not in out.stdout, out.stdout


def test_zero_in_the_tune_file_leaves_the_kernels_write_back_alone(tmp_path):
    root = _vm(tmp_path, "0", "10")
    _tune(tmp_path, "LC_DIRTY_BG_BYTES=0\n")
    out = run(tmp_path, f'mkdir -p "$LC/run"; export SYSROOT={posix(root)!r}; lc_tune_begin sdc sda; ' + KERNEL_SIDE +
              'lc_tune_end sda; ' + AFTER + 'cat "$LC/run/tune.applied"')
    assert out.returncode == 0, out.stderr
    assert "during=0|10" in out.stdout and "after=0|10" in out.stdout, out.stdout
    assert "LC_DIRTY_BG_BYTES=0" in out.stdout and "(default)" not in out.stdout, out.stdout


def test_a_knob_the_kernel_did_not_take_is_said_so_not_listed_as_set(tmp_path):
    out = run(tmp_path, 'mkdir -p "$LC/run"; printf "LC_SRC_RA_KB=8192\\n" > "$LC/tune"; '
              f'export SYSROOT={posix(tmp_path / "nothing")!r}; lc_tune_begin sdc sda; cat "$LC/run/tune.applied"')
    assert out.returncode == 0, out.stderr
    assert "LC_SRC_RA_KB=8192 (LC_SRC_RA_KB not applied)" in out.stdout, out.stdout


# --- the knobs reach the copy -----------------------------------------------------------

NTFS_STUB = r'''#!/bin/sh
echo "$*" >> "$STUB_BOX/partclone.args"
src=; out=
while [ $# -gt 0 ]; do case "$1" in -s) src=$2; shift 2 ;; -O) out=$2; shift 2 ;; *) shift ;; esac; done
if [ "$src" = - ]; then cat > "$out"; elif [ -n "$out" ]; then cat "$src" > "$out"; else cat "$src"; fi
'''


def _copy(tmp_path: Path, tune: str, fs: str) -> tuple[str, Path]:
    box = tmp_path / "box"; stubs = box / "stubs"; dev = box / "dev"; dev.mkdir(parents=True)
    (dev / "sdc1").write_bytes(b"imagesrv" * 8192)
    bodies = {**STREAM_STUBS, "partclone.ntfs": NTFS_STUB, "partclone.dd": NTFS_STUB}
    t = f"mkdir -p {posix(stubs)!r}\n"
    for name, body in bodies.items():
        t += f"cat > {posix(stubs)}/{name} <<'STUB'\n{body}STUB\nchmod 0755 {posix(stubs)}/{name}\n"
    t += f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH" STUB_BOX={posix(box)!r} DEVROOT={posix(dev)!r}; '
    t += 'mkdir -p "$LC/run"; ata_capture() { :; }; ntfs_boot_target() { return 0; }; target_init sda 100; '
    t += f'manifest_plan() {{ echo "1|g|r|{fs}|0|0|f|s|x|u|uuid|a"; }}; '
    _tune(tmp_path, tune)
    t += f'lc_copy m sdc sda > {posix(box)}/copy.log 2>&1; echo "rc=$?"; '
    t += 'echo "sda=$(cat $RUN_DIR/targets/sda/state)|$(cat $RUN_DIR/targets/sda/lc_ok 2>/dev/null)|$(cat $RUN_DIR/targets/sda/error 2>/dev/null)"; '
    t += f'cat {posix(box)}/partclone.args'
    out = run(tmp_path, t, timeout=120, WAIT_HELPER_S="20", WAIT_DRAWER_S="20")
    assert out.returncode == 0, out.stderr
    return out.stdout, dev


@pytest.mark.parametrize("tune,want", [("", "-z 524288"), ("LC_SRC_Z=4194304\n", "-z 4194304")])
def test_the_source_buffer_is_the_knob_and_the_copy_still_judges_every_record(tmp_path, tune, want):
    out, dev = _copy(tmp_path, tune, "ntfs")
    assert "sda=waiting|1|" in out, out                       # the four records still agree -> the partition counts
    source = [l for l in out.splitlines() if " -c " in f" {l} "]
    assert len(source) == 1 and f" {want} " in f" {source[0]} " and "-a0" in source[0], out
    assert (dev / "sda1").read_bytes() == (dev / "sdc1").read_bytes()


def test_partclone_b_writes_but_the_target_is_never_ok(tmp_path):
    """The measurement of the tool's own ceiling has no xxh64: the bytes land,
    the target is failed by name -- principle 5, a run with no evidence is not
    a pass, however fast."""
    out, dev = _copy(tmp_path, "LC_EXP_DIRECT=1\n", "ntfs")
    assert "sda=failed||" in out and "ניסוי (partclone -b, בלי fanout)" in out and "אין ראיה" in out, out
    assert [l for l in out.splitlines() if " -b " in f" {l} "], out     # partclone -b, device to device
    assert (dev / "sda1").read_bytes() == (dev / "sdc1").read_bytes()
