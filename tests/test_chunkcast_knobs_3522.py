"""#3522: opt-in experiments, observed through real UDP, disk writes and libc calls.

The unchanged binary must fail assertions, including for legacy behavior that is
deliberately retained: each successful sender also proves its resolved knobs.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="real chunkcast UDP and LD_PRELOAD observations")
KNOBS = ("CHUNKCAST_AHEAD", "CHUNKCAST_QCAP", "CHUNKCAST_FB_LOW", "CHUNKCAST_FB_HIGH")
DEFAULT = "knobs ahead=4 readahead=legacy order=seq fb_low=2 fb_high=6 qcap=8 pace=legacy batch=64 reader=inline readq=8"   # #3535: + pace, batch, reader, readq
FIELDS = ("read_ms lag_forgiven_wire lag_forgiven_disk forgiven_ms_wire forgiven_ms_disk "
          "enobufs eagain eintr refused over_ms readq_empty_ms readq_full_ms").split()      # #3535: over_ms, readq_*
REPORT = struct.Struct(">IBBHQIIQQQQ")


def env(values=None):
    return {**{k: v for k, v in C.ENV.items() if k not in KNOBS}, **(values or {})}


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("knobs-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("knobs-img"), cc / "fanout", kind="raw",
                   chunks=10, noisy=(0, 1, 2, 3, 4))


@pytest.fixture(scope="module")
def noisy(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("knobs-noisy"), cc / "fanout", kind="raw",
                   chunks=8, noisy=tuple(range(8)))


class Listener:
    """Keep complete datagrams in arrival order; a local marker drains the queue."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 << 20)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.address = self.sock.getsockname()
        self.addr = f"127.0.0.1:{self.address[1]}"
        self.packets = []
        self.stop = threading.Event()
        self.drained = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while not self.stop.is_set():
            try:
                packet = self.sock.recv(65536)
            except socket.timeout:
                continue
            if packet == b"knobs-test-drain":
                self.drained.set()
                return
            self.packets.append(packet)

    def close(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake:
                wake.sendto(b"knobs-test-drain", self.address)
            assert self.drained.wait(5), "UDP observer did not drain"
        finally:
            self.stop.set()
            self.thread.join(5)
            self.sock.close()

    def data(self):
        return [(int.from_bytes(p[16:24], "big"), int.from_bytes(p[28:32], "big"))
                for p in self.packets if len(p) >= 32 and p[4] == C.DATA]

    def chunks(self):
        return list(dict.fromkeys(k for k, _ in self.data()))


def stop(run):
    if run is not None and run.process.poll() is None:
        run.process.kill()
        run.process.wait()


def sender(cc, img, addr, tag, *, values=None, extra=(), session=None, stdin=False,
           write_kbps=100_000_000):
    return C._spawn([str(cc / "chunkcast"), "send", "--to", addr,
                     "--session", session or C.new_session(), "--part", "3", "--pass", "1",
                     "--index", str(img.idx), *(["--stdin"] if stdin else ["--file", str(img.file)]),
                     "--kbps", "80000", "--write-kbps", str(write_kbps), "--feedback", "off",
                     "--lead-ms", "0", "--tail-ms", "0", *extra], tag, env(values))


def capture(cc, img, tag, **kwargs):
    listener, run = Listener(), None
    try:
        run = sender(cc, img, listener.addr, tag, **kwargs)
        assert run.wait() == 0, run.log()
    finally:
        stop(run)
        listener.close()
    assert len([s for s in run.out.read_text().splitlines() if s.startswith("knobs ")]) == 1, run.log()
    assert listener.chunks(), run.log()
    return run, listener


def mixed(img, indices=None):
    by_size = sorted(range(img.n) if indices is None else indices, key=lambda k: (img.zlens[k], k))
    half = len(by_size) // 2
    light, heavy = sorted(by_size[:half]), sorted(by_size[half:])
    return [k for j, h in enumerate(heavy) for k in ([h, light[j]] if j < len(light) else [h])]


def stats(run):
    lines = run.out.read_text().splitlines()
    found = [s for s in lines if s.startswith("sendstats ")]
    assert len(found) == 1, run.log()
    assert re.fullmatch("sendstats " + " ".join(k + r"=\d+" for k in FIELDS), found[0]), found
    assert lines[-3] == found[0] and lines[-2].startswith("paced ") and lines[-1].startswith("done "), lines
    return {k: int(v) for k, v in (field.split("=") for field in found[0].split()[1:])}


def test_defaults_preserve_data_order_and_report_legacy_knobs(cc, img, tmp_path):
    a, first = capture(cc, img, tmp_path / "default")
    _, second = capture(cc, img, tmp_path / "explicit", values=dict(zip(KNOBS[1:], ("8", "2", "6"))),
                        extra=("--order", "seq"))
    assert a.out.read_text().splitlines().count(DEFAULT) == 1
    assert first.data() == second.data()
    assert first.chunks() == list(range(img.n))


INVALID = [(name, {name: value}) for name, values in (
    (KNOBS[0], ("0", "65", "x", "-1", "", "04", "4.5")),
    (KNOBS[1], ("7", "257", "abc")), (KNOBS[2], ("0", "255", "x")),
    (KNOBS[3], ("1", "8", "256", "y"))) for value in values]
INVALID += [(KNOBS[2], {KNOBS[2]: "6"}), (KNOBS[3], {KNOBS[3]: "3", KNOBS[2]: "3"}),
            (KNOBS[3], {KNOBS[3]: "20"})]


@pytest.mark.parametrize("name,values", INVALID)
def test_invalid_sender_knob_names_error_and_sends_nothing(cc, img, tmp_path, name, values):
    listener, run = Listener(), None
    try:
        run = sender(cc, img, listener.addr, tmp_path / "invalid", values=values)
        assert run.wait() == 2, run.log()
        assert name in run.err.read_text(), run.log()
    finally:
        stop(run)
        listener.close()
    assert listener.packets == []


def rejected_receiver(cc, img, tmp_path, command, value, targets=1):
    # A missing index makes the base binary end promptly; the knob must be checked first.
    return C._spawn([str(cc / "chunkcast"), command, "--listen", f"127.0.0.1:{C.free_port()}",
                     "--session", C.new_session(), "--pass", "1", "--part", "3", *C.image_args(img),
                     "--index", str(tmp_path / "missing-index"),
                     "--state", str(tmp_path / "state"), "--first", "0", "--count", "1",
                     *(str(tmp_path / f"absent-{i}") for i in range(targets))], tmp_path / "reject",
                    env({KNOBS[1]: value}))


@pytest.mark.parametrize("command", ["recv", "apply"])
@pytest.mark.parametrize("value", ["7", "257", "abc", ""])
def test_invalid_receiver_queue_is_rejected_by_name(cc, img, tmp_path, command, value):
    run = rejected_receiver(cc, img, tmp_path, command, value)
    assert run.wait() == 2, run.log()
    assert "CHUNKCAST_QCAP" in run.err.read_text(), run.log()


@pytest.mark.parametrize("command", ["recv", "apply"])
def test_queue_memory_bound_precedes_opening_targets(cc, img, tmp_path, command):
    run = rejected_receiver(cc, img, tmp_path, command, "128", targets=8)
    assert run.wait() == 2, run.log()
    assert "CHUNKCAST_QCAP" in run.err.read_text(), run.log()
    assert "4294967296" in run.err.read_text() and "8 targets" in run.err.read_text(), run.log()
    assert not (tmp_path / "state").exists()


@pytest.fixture
def state_home(tmp_path):
    """#3542: where the receiver's state file lives -- memory, as in the agent (CHUNK_DIR is under RUN_DIR, a tmpfs).

    The receive loop writes the state file itself (fopen, rename). On testrunner2 `/` is an ext4 with `discard` on a
    SATA SSD; a journal commit that waits there stopped the loop for 100ms and more, the 425KB socket buffer
    (rmem_max, no root) holds ~26ms of this stream, and the kernel dropped the rest (RcvbufErrors, a chunk lost,
    "pass ended" instead of "pass complete"). Only this file moves: the targets stay real files on the disk.
    Falls back to tmp_path when the host has no writable /dev/shm."""
    shm = Path("/dev/shm")
    if not (shm.is_dir() and os.access(shm, os.W_OK)):
        yield tmp_path
        return
    home = Path(tempfile.mkdtemp(prefix=f"knobs3522-{os.getpid()}-", dir=shm))
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


def clean(cc, img, tmp_path, state_home, *, qcap=None, order=None, targets=2):
    disks = [img.blank(tmp_path / f"disk-{i}") for i in range(targets)]
    addr, session, state = f"127.0.0.1:{C.free_port()}", C.new_session(), state_home / "state"
    r = C.recv(cc, img, disks, addr, session, state, tag=tmp_path / "recv",
               env=env({KNOBS[1]: str(qcap)} if qcap else {}), extra=("--feedback", "off"))
    s = None
    try:
        s = sender(cc, img, addr, tmp_path / "send", session=session, write_kbps=800_000,
                   extra=("--lead-ms", "200", "--tail-ms", "500", *(("--order", order) if order else ())))
        assert s.wait() == 0, s.log()
        assert r.wait() == 0, r.log()
        assert "pass complete" in r.out.read_text(), r.log()
        for disk in disks:
            assert disk.read_bytes() == img.expected()
            assert C.state_of(state)[str(disk)] == {"alive": True, "durable": set(range(img.n)), "reason": ""}
        cap = qcap or 8
        assert f"knobs qcap={cap} feed_q={cap // 2}" in r.out.read_text().splitlines(), r.log()
        assert f"knobs qcap={cap}" in r.err.read_text(), r.log()
        return s, r
    finally:
        stop(s)
        stop(r)


@pytest.mark.parametrize("qcap,targets", [(None, 2), (64, 2), (64, 1)])
def test_queue_capacity_pass_is_complete_on_every_target(cc, img, tmp_path, state_home, qcap, targets):
    clean(cc, img, tmp_path, state_home, qcap=qcap, targets=targets)


@pytest.fixture(scope="module")
def feedback_img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("knobs-feedback"), cc / "fanout", kind="raw", chunks=48, noisy=())


def feedback_run(cc, img, tag, backlog, values=None):
    control, session, done = C.free_port(), C.new_session(), threading.Event()
    packet = REPORT.pack(0x54524631, 1, 0, 3, int(session, 16), 1, 0, 1, 0, backlog * C.CH, 0)
    def feed():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            while not done.wait(0.025):
                sock.sendto(packet, ("127.0.0.1", control))
    thread = threading.Thread(target=feed)
    listener, run = Listener(), None
    thread.start()
    try:
        run = sender(cc, img, listener.addr, tag, values=values, session=session, write_kbps=400_000,
                     extra=("--feedback", "on", "--control-port", str(control),
                            "--rate-min", "200000", "--rate-max", "800000"))
        assert run.wait() == 0, run.log()
        lines = run.out.read_text().splitlines()
        assert any(s.startswith("knobs ") for s in lines), run.log()
        rates = [(int(s.split()[1]), s.split()[2]) for s in lines if s.startswith("rate ")]
        malformed = [int(s.split("=")[1]) for s in lines if s.startswith("feedback malformed=")]
        assert rates and malformed, run.log()
        return rates, malformed
    finally:
        done.set()
        thread.join(5)
        stop(run)
        listener.close()


def test_feedback_thresholds_change_the_rate_decision(cc, feedback_img, tmp_path):
    default, _ = feedback_run(cc, feedback_img, tmp_path / "default", 7)
    tuned, malformed = feedback_run(cc, feedback_img, tmp_path / "tuned", 7,
                                   dict(zip(KNOBS[1:], ("32", "10", "20"))))
    assert any(rate < 400_000 and reason == "decrease" for rate, reason in default)
    assert not any(reason == "decrease" for _, reason in tuned)
    assert any(reason == "increase" for _, reason in tuned) and max(malformed) == 0


@pytest.mark.parametrize("qcap", [None, "32"])
@pytest.mark.parametrize("backlog", [8, 100])
def test_feedback_backlog_sanity_accepts_eight_and_rejects_hundred(cc, feedback_img, tmp_path, qcap, backlog):
    rates, malformed = feedback_run(cc, feedback_img, tmp_path / "feedback", backlog,
                                    {KNOBS[1]: qcap} if qcap else {})
    if backlog == 8:
        assert max(malformed) == 0
        assert any(reason == "decrease" for _, reason in rates)
    else:
        assert max(malformed) > 0
        assert {rate for rate, _ in rates} == {400_000}


def test_mixed_order_is_complete_and_heavy_first(cc, img, tmp_path):
    run, listener = capture(cc, img, tmp_path / "mixed", extra=("--order", "mixed"))
    assert listener.chunks() == mixed(img) != list(range(img.n))
    assert "readahead=on order=mixed" in run.out.read_text()


def test_mixed_order_writes_both_targets_and_all_chunks_durable(cc, img, tmp_path, state_home):
    s, _ = clean(cc, img, tmp_path, state_home, order="mixed")
    assert "readahead=on order=mixed" in s.out.read_text()


def test_mixed_order_repair_sends_only_selected_chunks(cc, img, tmp_path):
    pick = tmp_path / "chunks"
    pick.write_text("2-3,7-7\n", encoding="ascii")
    _, listener = capture(cc, img, tmp_path / "repair", extra=("--order", "mixed", "--chunks-file", str(pick)))
    assert listener.chunks() == mixed(img, [2, 3, 7])


def test_order_rejects_stdin_reordering_and_unknown_order(cc, img, tmp_path):
    # Together: merely rejecting every --order (the base binary) cannot pass this test.
    for order, stdin in (("mixed", True), ("bogus", False)):
        listener, run = Listener(), None
        try:
            run = sender(cc, img, listener.addr, tmp_path / order, stdin=stdin, extra=("--order", order))
            assert run.wait() == 2, run.log()
            message = run.err.read_text()
            if stdin:
                assert "stdin cannot be reordered" in message, message
            else:
                assert "bad option '--order bogus'" in message, message
        finally:
            stop(run)
            listener.close()
        assert not listener.packets


SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
static void delay(size_t n)
{
    if (getenv("KNOBS_SLOW") && n >= (1u << 20)) {
        FILE *f = fopen(getenv("KNOBS_SLOW"), "a");
        if (!f) _exit(90);
        fprintf(f, "%zu\n", n);
        if (fclose(f)) _exit(91);
        usleep(100000);
    }
}
ssize_t pread(int fd, void *b, size_t n, off_t o)
{
    ssize_t (*real)(int, void *, size_t, off_t) = dlsym(RTLD_NEXT, "pread");
    delay(n);
    return real(fd, b, n, o);
}
ssize_t pread64(int fd, void *b, size_t n, off64_t o)
{
    ssize_t (*real)(int, void *, size_t, off64_t) = dlsym(RTLD_NEXT, "pread64");
    delay(n);
    return real(fd, b, n, o);
}
int sendmmsg(int fd, struct mmsghdr *msgs, unsigned int n, int flags)
{
    static unsigned int calls;
    int (*real)(int, struct mmsghdr *, unsigned int, int) = dlsym(RTLD_NEXT, "sendmmsg");
    if (getenv("KNOBS_ENOBUFS") && ++calls % 5 == 0) { errno = ENOBUFS; return -1; }
    return real(fd, msgs, n, flags);
}
static void advice(long long off, long long len, int how)
{
    if (getenv("KNOBS_ADVICE") && how == POSIX_FADV_WILLNEED) {
        FILE *f = fopen(getenv("KNOBS_ADVICE"), "a");
        if (!f) _exit(92);
        fprintf(f, "%lld %lld\n", off, len);
        if (fclose(f)) _exit(93);
    }
}
int posix_fadvise(int fd, off_t off, off_t len, int how)
{
    int (*real)(int, off_t, off_t, int) = dlsym(RTLD_NEXT, "posix_fadvise");
    advice((long long)off, (long long)len, how);
    return real(fd, off, len, how);
}
int posix_fadvise64(int fd, off64_t off, off64_t len, int how)
{
    int (*real)(int, off64_t, off64_t, int) = dlsym(RTLD_NEXT, "posix_fadvise64");
    advice((long long)off, (long long)len, how);
    return real(fd, off, len, how);
}
'''


@pytest.fixture(scope="module")
def shim(tmp_path_factory):
    root = tmp_path_factory.mktemp("knobs-shim")
    src, so = root / "shim.c", root / "shim.so"
    src.write_text(SHIM, encoding="ascii")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                            str(src), "-ldl", "-o", str(so)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return so


def test_sendstats_has_nine_integer_fields_before_final_paced_and_done(cc, img, tmp_path):
    run, _ = capture(cc, img, tmp_path / "stats")
    assert all(value >= 0 for value in stats(run).values())


def test_sendstats_counts_time_in_each_slow_source_read(cc, noisy, shim, tmp_path):
    reads = tmp_path / "reads"
    run, _ = capture(cc, noisy, tmp_path / "slow", values={"LD_PRELOAD": str(shim), "KNOBS_SLOW": str(reads)})
    calls = reads.read_text().splitlines()
    assert len(calls) >= noisy.n and all(int(n) >= 1 << 20 for n in calls)
    assert stats(run)["read_ms"] >= len(calls) * 80


def test_sendstats_counts_enobufs_and_retries_every_chunk(cc, noisy, shim, tmp_path):
    run, listener = capture(cc, noisy, tmp_path / "retry", values={"LD_PRELOAD": str(shim), "KNOBS_ENOBUFS": "1"})
    got = stats(run)
    assert got["enobufs"] >= 1 and all(got[k] >= 0 for k in ("eagain", "eintr", "refused"))
    assert listener.chunks() == list(range(noisy.n))


def test_wire_clock_counts_lag_forgiven_after_sigstop(cc, noisy, tmp_path):
    listener, run = Listener(), None
    try:
        run = sender(cc, noisy, listener.addr, tmp_path / "stopped")
        run.wait_for("sent ")
        os.kill(run.process.pid, signal.SIGSTOP)
        # The injected scheduler stall is the experiment, not a readiness delay.
        until = time.monotonic() + 0.3
        while time.monotonic() < until:
            threading.Event().wait(max(0, until - time.monotonic()))
        os.kill(run.process.pid, signal.SIGCONT)
        assert run.wait() == 0, run.log()
        got = stats(run)
        assert got["lag_forgiven_wire"] >= 1 and got["forgiven_ms_wire"] >= 100, got
    finally:
        stop(run)
        listener.close()


@pytest.mark.parametrize("mode", ["legacy", "ahead", "mixed"])
def test_readahead_advises_actual_future_positions_only_when_enabled(cc, noisy, shim, tmp_path, mode):
    log = tmp_path / "advice"
    values = {"LD_PRELOAD": str(shim), "KNOBS_ADVICE": str(log)}
    if mode != "legacy":
        values[KNOBS[0]] = "3"
    run, _ = capture(cc, noisy, tmp_path / "advised", values=values,
                     extra=("--order", "mixed") if mode == "mixed" else ())
    advised = [tuple(map(int, line.split())) for line in log.read_text().splitlines()]
    offsets = [o for o, _ in advised]
    if mode == "legacy":
        assert DEFAULT in run.out.read_text().splitlines()
        # #3523: the default first pass (no list) asks for the NEXT chunks, one time each, not the current one four times.
        assert advised == [(noisy.offsets[k], noisy.zlens[k]) for k in range(1, noisy.n)]
    else:
        order = mixed(noisy) if mode == "mixed" else list(range(noisy.n))
        assert "readahead=on" in run.out.read_text()
        assert advised == [(noisy.offsets[k], noisy.zlens[k]) for k in order[1:]]
        assert len(set(offsets)) == len(offsets)
        assert offsets[:3] == [noisy.offsets[k] for k in order[1:4]]
