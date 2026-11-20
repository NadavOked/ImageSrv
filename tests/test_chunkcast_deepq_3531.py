"""#3531: the receiver's decode queue in compressed bytes, and the feedback's thresholds as write time.

Both are opt-in (`CHUNKCAST_VQ_MIB`, `CHUNKCAST_FB_LOW_MS` / `CHUNKCAST_FB_HIGH_MS`); unset, the binary is today's.
The receiver tests are the real receiver and sender on loopback; the verifier is slowed by an external interposer
(the one of test_chunkcast_verifier_pool_3518.py), never by a switch in production code. The sender tests feed it
synthetic TRF1 reports, as test_chunkcast_feedback.py does: the outcome is a function of the reports and the
thresholds, not of the wall clock.
"""
from __future__ import annotations

import os
import socket
import struct
import subprocess
import threading
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native
from test_chunkcast_verifier_pool_3518 import SLOW_VERIFY

pytestmark = requires_native("gcc", "zstd", posix=True, why="real chunkcast UDP, and an interposed ZSTD_decompress")
KNOBS = ("CHUNKCAST_VQ_MIB", "CHUNKCAST_FB_LOW", "CHUNKCAST_FB_HIGH", "CHUNKCAST_FB_LOW_MS", "CHUNKCAST_FB_HIGH_MS")
REPORT = struct.Struct(">IBBHQIIQQQQ")
START, MIN, MAX = 400_000, 100_000, 800_000     # kbit/s; START / 8 = 50,000 bytes of disk write a millisecond
TIME_MS = {"CHUNKCAST_FB_LOW_MS": "500", "CHUNKCAST_FB_HIGH_MS": "2000"}    # 25 MB and 100 MB at START


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("deepq-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("deepq-img"), cc / "fanout", kind="raw", chunks=48, noisy=())


@pytest.fixture(scope="module")
def slow(tmp_path_factory):
    d = tmp_path_factory.mktemp("deepq-slow")
    (d / "slow.c").write_text(SLOW_VERIFY)
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", str(d / "slow.c"),
                            "-ldl", "-o", str(d / "slow.so")], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return d / "slow.so"


@pytest.fixture(autouse=True)
def _free_the_memory_drives():
    yield
    for left in Path("/dev/shm").glob(f"deepq3531-{os.getpid()}-*.disk"):
        left.unlink()


def env(values=None):
    return {**{k: v for k, v in C.ENV.items() if k not in KNOBS}, **(values or {})}


# --- the sender: thresholds as the time the backlog takes to write ------------------------------------------------

def report(session, backlog):
    return REPORT.pack(0x54524631, 1, 0, 3, int(session, 16), 1, 0, 1, 0, backlog, 0)


def rates(run):
    return [(int(x.split()[1]), x.split()[2]) for x in run.out.read_text().splitlines() if x.startswith("rate ")]


def sender_against_reports(cc, img, tmp_path, backlog, values, *, write_kbps=START, expect=0):
    """One sender, a receiver that says `backlog` every 25 ms. Returns the sender's Run."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    control, session = C.free_port(), C.new_session()
    stop = threading.Event()

    def feed():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            while not stop.wait(0.025):
                sock.sendto(report(session, backlog), ("127.0.0.1", control))

    thread = threading.Thread(target=feed)
    thread.start()
    cmd = [str(cc / "chunkcast"), "send", "--to", f"127.0.0.1:{C.free_port()}", "--session", session, "--part", "3",
           "--pass", "1", "--index", str(img.idx), "--file", str(img.file), "--kbps", "80000", "--lead-ms", "0",
           "--tail-ms", "0", "--control-port", str(control), "--rate-min", str(MIN), "--rate-max", str(MAX)]
    if write_kbps:
        cmd += ["--write-kbps", str(write_kbps)]
    run = C._spawn(cmd, tmp_path / "send", env(values))
    try:
        assert run.wait() == expect, run.log()
        return run
    finally:
        stop.set()
        thread.join(2)
        if run.process.poll() is None:
            run.process.kill()
            run.process.wait()


def kinds(run):
    return {reason for _, reason in rates(run)}


def test_a_backlog_of_little_write_time_lets_the_rate_rise_where_the_chunk_count_only_holds_it(cc, img, tmp_path):
    """5 chunks = 21 MB = 0.42 s of write at START. In chunks it is above FB_LOW (2) and below FB_HIGH (6): hold, never
    an increase. In time (LOW 0.5 s) it is healthy."""
    legacy = sender_against_reports(cc, img, tmp_path / "a", 5 * C.CH, {})
    timed = sender_against_reports(cc, img, tmp_path / "b", 5 * C.CH, TIME_MS)
    assert "increase" not in kinds(legacy) and "decrease" not in kinds(legacy) and "hold" in kinds(legacy), legacy.log()
    assert "increase" in kinds(timed) and "decrease" not in kinds(timed), timed.log()


def test_a_backlog_of_between_the_two_thresholds_in_time_holds_and_one_over_the_high_one_lowers(cc, img, tmp_path):
    """20 chunks = 84 MB = 1.7 s: in chunks far over FB_HIGH (decrease), in time between LOW 0.5 s and HIGH 2 s (hold).
    30 chunks = 126 MB = 2.5 s is over HIGH in time too: a queue with a lot of work to write is still told to slow down."""
    legacy = sender_against_reports(cc, img, tmp_path / "a", 20 * C.CH, {})
    between = sender_against_reports(cc, img, tmp_path / "b", 20 * C.CH, TIME_MS)
    over = sender_against_reports(cc, img, tmp_path / "c", 30 * C.CH, TIME_MS)
    assert "decrease" in kinds(legacy), legacy.log()
    assert "hold" in kinds(between) and not kinds(between) & {"increase", "decrease"}, between.log()
    assert "decrease" in kinds(over), over.log()
    assert min(rate for rate, _ in rates(over)) < START, over.log()


def test_a_report_past_the_default_sanity_bound_is_malformed_unless_the_deep_queue_is_asked_for(cc, img, tmp_path):
    """100 chunks is past (qcap + 32 + 8 + 4 + 2) chunks. With CHUNKCAST_VQ_MIB the receiver may hold 256 frames, and the
    sender's bound follows -- the report is then an ordinary, very high backlog."""
    refused = sender_against_reports(cc, img, tmp_path / "a", 100 * C.CH, {})
    taken = sender_against_reports(cc, img, tmp_path / "b", 100 * C.CH, {"CHUNKCAST_VQ_MIB": "64"})
    assert {rate for rate, _ in rates(refused)} == {START}, refused.log()
    assert "decrease" in kinds(taken), taken.log()


@pytest.mark.parametrize("values,write_kbps", [
    ({"CHUNKCAST_FB_LOW_MS": "500"}, START),                                              # one of two
    ({"CHUNKCAST_FB_HIGH_MS": "2000"}, START),
    ({"CHUNKCAST_FB_LOW_MS": "2000", "CHUNKCAST_FB_HIGH_MS": "500"}, START),              # LOW >= HIGH
    ({**TIME_MS, "CHUNKCAST_FB_LOW": "1"}, START),                                        # two ways to say the same
    ({**TIME_MS}, 0),                                                                     # no write rate to turn time into bytes
    ({"CHUNKCAST_FB_LOW_MS": "0", "CHUNKCAST_FB_HIGH_MS": "2000"}, START),                # not in 1..60000
    ({"CHUNKCAST_VQ_MIB": "0"}, START),
    ({"CHUNKCAST_VQ_MIB": "1025"}, START),
])
def test_a_threshold_or_queue_setting_that_cannot_be_applied_is_refused_not_ignored(cc, img, tmp_path, values, write_kbps):
    run = sender_against_reports(cc, img, tmp_path, 0, values, write_kbps=write_kbps, expect=2)
    assert "nothing is started" in run.log(), run.log()
    assert not rates(run), run.log()


def test_the_knobs_line_names_the_new_settings_only_when_they_are_set(cc, img, tmp_path):
    legacy = sender_against_reports(cc, img, tmp_path / "a", 0, {})
    timed = sender_against_reports(cc, img, tmp_path / "b", 0, {**TIME_MS, "CHUNKCAST_VQ_MIB": "64"})
    line = lambda run: next(x for x in run.out.read_text().splitlines() if x.startswith("knobs "))   # noqa: E731
    assert "fb_low_ms" not in line(legacy) and "vq_mib" not in line(legacy), legacy.log()
    assert line(timed).endswith(" fb_low_ms=500 fb_high_ms=2000 vq_mib=64"), timed.log()


# --- the receiver: the queue in compressed bytes ------------------------------------------------------------------

def memory_cap_mib(asked):
    for text in Path("/proc/meminfo").read_text().splitlines():
        if text.startswith("MemAvailable:"):
            return min(asked, int(text.split()[1]) // 1024 // 16)
    raise AssertionError("MemAvailable is not in /proc/meminfo")


def one_pass(cc, image, tmp_path, slow_so, values, *, slow_ms, write_kbps=800_000):
    """One sender, one receiver (one verifier, `slow_ms` a chunk), a drive in memory. Returns (receiver, target)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    session, port = C.new_session(), C.free_port()
    shm = Path("/dev/shm")
    target = image.blank((shm if shm.is_dir() and os.access(shm, os.W_OK) else tmp_path)
                         / f"deepq3531-{os.getpid()}-{session}.disk")
    r = C.recv(cc, image, [target], f"127.0.0.1:{port}", session, tmp_path / "state", tag=tmp_path / "recv",
               env=env({"LD_PRELOAD": str(slow_so), "SLOW_VERIFY_MS": str(slow_ms), "CHUNKCAST_VERIFIERS": "1", **values}))
    s = C.send(cc, image, f"127.0.0.1:{port}", session, tag=tmp_path / "send", write_kbps=write_kbps)
    try:
        assert s.wait(120) == 0, s.log()
        assert r.wait(120) == 0, r.log()
    finally:
        for run in (r, s):
            if run.process.poll() is None:
                run.process.kill()
                run.process.wait()
    return r, target


def said(run, word):
    found = [x for x in run.out.read_text().splitlines() if x.startswith(word + " ")]
    assert len(found) == 1, (word, run.log())
    return {k: (int(v) if v.isdigit() else v) for k, v in (pair.split("=", 1) for pair in found[0].split()[1:] if "=" in pair)}


def test_a_burst_of_small_frames_that_overflows_the_frame_queue_fits_the_byte_queue(cc, tmp_path, slow):
    """24 chunks of a short pattern (a frame is a few KB), one verifier at 400 ms: the frame queue (8) and the four slots
    overflow and chunks are dropped (`busy`) -- test_chunkcast_verifier_pool_3518.py. Bounded in bytes (64 MiB) the same
    burst is all queued: nothing deferred, nothing dropped, every chunk written."""
    burst = C.build(tmp_path / "img", cc / "fanout", kind="raw", chunks=24, noisy=(), name="p3.windows.pcl.zst")
    r, target = one_pass(cc, burst, tmp_path / "run", slow, {"CHUNKCAST_VQ_MIB": "64"}, slow_ms=400, write_kbps=8_388_608)
    v = said(r, "verifier")
    stat = said(r, "rxstats")
    assert v["deferred"] == 0 and stat["busy"] == 0 and stat["evicted"] == 0 and stat["good"] == burst.n, (v, stat, r.log())
    assert target.read_bytes() == burst.expected()
    assert v.get("queue_mib") == memory_cap_mib(64) and v["queue"] == 256, (v, r.log())


def test_frames_that_do_not_fit_the_byte_limit_wait_in_their_slots_and_a_frame_longer_than_it_still_passes(cc, tmp_path, slow):
    """Three incompressible chunks (a frame ~4 MiB each, one every 0.42 s on the 10 MB/s wire) against a limit of 2 MiB and
    a verifier that takes 2 s a chunk: the queue takes one frame when it is empty (else it could never take any), the next
    waits in its slot (`deferred`) and is not dropped. The same stream in the frame queue (8) never waits."""
    noisy = C.build(tmp_path / "img", cc / "fanout", kind="raw", chunks=3, noisy=(0, 1, 2), name="p3.windows.pcl.zst")
    r, target = one_pass(cc, noisy, tmp_path / "bytes", slow, {"CHUNKCAST_VQ_MIB": "2"}, slow_ms=2000)
    v, stat = said(r, "verifier"), said(r, "rxstats")
    assert v["deferred"] >= 1, (v, r.log())
    assert stat["busy"] == 0 and stat["evicted"] == 0 and stat["good"] == noisy.n, (stat, r.log())
    assert target.read_bytes() == noisy.expected() and v.get("queue_mib") == 2, (v, r.log())
    control, target2 = one_pass(cc, noisy, tmp_path / "frames", slow, {}, slow_ms=2000)
    assert said(control, "verifier")["deferred"] == 0 and "queue_mib" not in control.out.read_text(), control.log()
    assert target2.read_bytes() == noisy.expected()
