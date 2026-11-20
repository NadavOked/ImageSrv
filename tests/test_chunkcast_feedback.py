"""Trident in-wave feedback, real binaries and UDP on loopback.

The native fixture compiles with -O2 -Wall -Wextra -Werror -pthread.
The slow writer is an external pwrite interposer, not a production test switch.
"""
from __future__ import annotations

import socket
import struct
import subprocess
import threading
import time

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="real chunkcast UDP and slow pwrite")
REPORT = struct.Struct(">IBBHQIIQQQQ")
START, MIN, MAX = 400_000, 200_000, 800_000


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("feedback-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("feedback-img"), cc / "fanout", kind="raw", chunks=48, noisy=())


def report(session, *, who=1, backlog=0, drops=0, version=1, written=0, pass_=1):
    return REPORT.pack(0x54524631, version, 0, 3, int(session, 16), pass_, 0,
                       who, written, backlog, drops)


def rates(run):
    return [(int(line.split()[1]), line.split()[2]) for line in run.out.read_text().splitlines()
            if line.startswith("rate ")]


def start_sender(cc, img, tmp_path, control, session, *, extra=()):
    return C.send(cc, img, f"127.0.0.1:{C.free_port()}", session, tag=tmp_path / "send",
                  write_kbps=START, extra=("--control-port", str(control), "--rate-min", str(MIN),
                                           "--rate-max", str(MAX), *extra))


def synthetic(cc, img, tmp_path, packets, *, extra=()):
    control, session = C.free_port(), C.new_session()
    stop = threading.Event()
    def feed():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            while not stop.wait(0.025):
                for packet in packets(session):
                    sock.sendto(packet, ("127.0.0.1", control))
    thread = threading.Thread(target=feed)
    thread.start()
    run = None
    try:
        run = start_sender(cc, img, tmp_path, control, session, extra=extra)
        assert run.wait() == 0, run.log()
        return run
    finally:
        stop.set()
        thread.join(2)
        if run and run.process.poll() is None:
            run.process.kill()
            run.process.wait()


def test_rate_rises_with_fresh_healthy_reports(cc, img, tmp_path):
    run = synthetic(cc, img, tmp_path, lambda s: [report(s)])
    values = rates(run)
    assert any(rate > START and reason == "increase" for rate, reason in values), run.log()
    assert all(MIN <= rate <= MAX for rate, _ in values)


def test_rate_falls_then_recovers_after_slow_receiver_is_left_behind(cc, img, tmp_path):
    run = synthetic(cc, img, tmp_path,
                    lambda s: [report(s), report(s, who=2, backlog=8 * C.CH, drops=1)])
    lines = run.out.read_text().splitlines()
    left = next(i for i, line in enumerate(lines) if line.startswith("left-behind 0000000000000002 "))
    assert any(rate < START and reason == "decrease" for rate, reason in rates(run)), run.log()
    after = [int(line.split()[1]) for line in lines[left:] if line.startswith("rate ")]
    assert len(after) > 2 and after[-1] > after[0], run.log()
    assert all(MIN <= rate <= MAX for rate, _ in rates(run))


def test_a_lone_slow_receiver_is_not_left_behind(cc, img, tmp_path):
    run = synthetic(cc, img, tmp_path, lambda s: [report(s, backlog=8 * C.CH)])
    assert "left-behind" not in run.out.read_text()
    assert any(rate == MIN for rate, _ in rates(run))


def test_a_constant_cumulative_drop_count_is_one_event_not_a_standing_loss(cc, img, tmp_path):
    run = synthetic(cc, img, tmp_path, lambda s: [report(s, drops=5)])
    values = rates(run)
    first = next(i for i, (_, reason) in enumerate(values) if reason == "decrease")
    assert any(reason == "increase" for _, reason in values[first:]), run.log()


def test_a_rising_drop_count_holds_the_rate_down_and_never_raises_it(cc, img, tmp_path):
    seen = [0]
    def packets(s):
        seen[0] += 1
        return [report(s, drops=seen[0])]
    run = synthetic(cc, img, tmp_path, packets)
    values = rates(run)
    assert any(reason == "decrease" for _, reason in values), run.log()
    assert not any(reason == "increase" for _, reason in values), run.log()
    assert values[-1][0] == MIN, run.log()


def test_no_feedback_keeps_start_rate(cc, img, tmp_path):
    run = synthetic(cc, img, tmp_path, lambda s: [])
    assert rates(run) and set(rates(run)) == {(START, "no-feedback")}, run.log()


def test_malformed_and_foreign_reports_do_not_change_rate(cc, img, tmp_path):
    def packets(s):
        return [b"bad", report(s)[:-1], report(s) + b"x", report(s, who=0),
                report(s, written=img.n + 1), report(s, backlog=100 * C.CH),
                report(s, version=99), report(s, pass_=2), report("0000000000000000")]
    run = synthetic(cc, img, tmp_path, packets)
    assert rates(run) and {rate for rate, _ in rates(run)} == {START}, run.log()
    counts = [int(line.split("=")[1]) for line in run.out.read_text().splitlines()
              if line.startswith("feedback malformed=")]
    assert counts and counts[-1] > 0, run.log()


def test_silent_receiver_is_not_reused_as_success(cc, img, tmp_path):
    at = time.monotonic()
    run = synthetic(cc, img, tmp_path,
                    lambda s: [report(s)] if time.monotonic() - at < 0.8 else [])
    values = rates(run)
    assert any(reason == "increase" for _, reason in values), run.log()
    missing = next(i for i, (_, reason) in enumerate(values) if reason == "missing")
    assert len({rate for rate, _ in values[missing:]}) == 1, run.log()
    assert values[-1][1] == "no-feedback", run.log()


def test_feedback_off_ignores_datagrams_and_keeps_legacy_pacing(cc, img, tmp_path):
    run = synthetic(cc, img, tmp_path, lambda s: [report(s, backlog=8 * C.CH, drops=1)],
                    extra=("--feedback", "off"))
    assert not rates(run)
    lines = run.out.read_text().splitlines()
    assert lines[-2].startswith("paced ") and lines[-1].startswith("done ")
    # Independent expected duration from the existing decompressed-byte clock.
    elapsed = int(lines[-1].split()[3])
    expected = len(img.data) * 8 / START
    assert expected <= elapsed < expected + 2000, run.log()


@pytest.mark.parametrize("enabled", [True, False])
def test_receiver_switch_and_feedback_fields(cc, tmp_path, enabled):
    img = C.build(tmp_path / "img", cc / "fanout", kind="raw", chunks=4, noisy=())
    session, port = C.new_session(), C.free_port()
    target = img.blank(tmp_path / "target")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        control.bind(("127.0.0.1", 0))
        control.settimeout(0.2)
        receiver = C.recv(cc, img, [target], f"127.0.0.1:{port}", session, tmp_path / "state",
                          tag=tmp_path / "recv", extra=("--feedback", "on" if enabled else "off",
                                                       "--control-port", str(control.getsockname()[1])))
        sender = None
        try:
            # Sender off leaves the control port available to this independent protocol observer.
            sender = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send",
                            write_kbps=80_000, extra=("--feedback", "off"))
            assert sender.wait() == 0, sender.log()
            assert receiver.wait() == 0, receiver.log()
            packets = []
            while True:
                try:
                    packets.append(control.recv(1024))
                except socket.timeout:
                    break
            assert target.read_bytes() == img.expected()
            if not enabled:
                assert not packets
            else:
                assert len(packets) >= 5
                fields = [REPORT.unpack(p) for p in packets]
                assert all(f[:7] == (0x54524631, 1, 0, 3, int(session, 16), 1, 0) for f in fields)
                assert len({f[7] for f in fields}) == 1 and fields[0][7] != 0
                assert all(0 <= f[8] <= img.n and f[9] <= 14 * C.CH for f in fields)
                assert [f[8] for f in fields] == sorted(f[8] for f in fields)
                assert fields[-1][8] > 0
        finally:
            for run in (receiver, sender):
                if run and run.process.poll() is None:
                    run.process.kill()
                    run.process.wait()


@pytest.mark.parametrize("extra", [("--rate-min", "400001"), ("--rate-max", "399999"),
                                   ("--feedback", "maybe"), ("--control-port", "65536")])
def test_invalid_feedback_arguments_are_rejected(cc, img, tmp_path, extra):
    run = start_sender(cc, img, tmp_path, C.free_port(), C.new_session(), extra=extra)
    assert run.wait() == 2, run.log()


SLOW_WRITE = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/types.h>
ssize_t pwrite(int fd, const void *buf, size_t n, off_t off) {
    ssize_t (*real)(int, const void *, size_t, off_t) = dlsym(RTLD_NEXT, "pwrite");
    char link[64], path[4096];
    snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
    ssize_t len = readlink(link, path, sizeof path - 1);
    if (len >= 0) {
        path[len] = 0;
        if (getenv("SLOW_TARGET") && !strcmp(path, getenv("SLOW_TARGET"))) usleep(600000);
    }
    return real(fd, buf, n, off);
}
'''


def test_real_receivers_report_slow_writer_and_fast_disk_finishes(cc, img, tmp_path):
    src, so = tmp_path / "slow.c", tmp_path / "slow.so"
    src.write_text(SLOW_WRITE)
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                            str(src), "-ldl", "-o", str(so)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    port, control, session = C.free_port(), C.free_port(), C.new_session()
    group = f"239.255.72.91:{port}"
    fast, slow = img.blank(tmp_path / "fast.disk"), img.blank(tmp_path / "slow.disk")
    runs = []
    try:
        for name, disk in (("fast", fast), ("slow", slow)):
            runs.append(C.recv(cc, img, [disk], group, session, tmp_path / f"{name}.state",
                               tag=tmp_path / name, extra=("--control-port", str(control)),
                               env={**C.ENV, "LD_PRELOAD": str(so), "SLOW_TARGET": str(slow)}))
        sender = C.send(cc, img, group, session, tag=tmp_path / "send", write_kbps=START,
                        extra=("--ttl", "0", "--control-port", str(control),
                               "--rate-min", str(MIN), "--rate-max", str(MAX)))
        runs.append(sender)
        assert sender.wait() == 0, sender.log()
        for receiver in runs[:2]:
            assert receiver.wait() == 0, receiver.log()
        out = sender.out.read_text()
        assert "left-behind " in out and any(reason == "decrease" for _, reason in rates(sender)), out
        after = out.split("left-behind ", 1)[1]
        assert " increase\n" in after, out
        assert fast.read_bytes() == img.expected()
        slow_state = C.state_of(tmp_path / "slow.state")[str(slow)]
        assert slow_state["alive"] and len(slow_state["durable"]) < img.n
        # Missing chunks remain eligible for the existing HTTP/apply completion path.
        missing = sorted(set(range(img.n)) - slow_state["durable"])
        repair = C.apply(cc, img, [slow], tmp_path / "slow.state", 0, len(missing),
                         b"".join(img.frame(k) for k in missing), tag=tmp_path / "repair",
                         chunks=C.ranges_of(missing))
        assert repair.process.returncode == 0, repair.log()
        assert slow.read_bytes() == img.expected()
    finally:
        for run in runs:
            if run.process.poll() is None:
                run.process.kill()
                run.process.wait()
