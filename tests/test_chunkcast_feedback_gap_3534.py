"""#3534: the feedback's decrease (x0.8 on every 100 ms decision that sees a bad receiver) as an opt-in, damped one.

`CHUNKCAST_FB_DEC_GAP_MS` is the least time between two decreases; unset, the sender is today's, decision for decision.
Like test_chunkcast_feedback.py: the real sender, and a receiver that is synthetic TRF1 reports. The assertions are about the
ORDER of the sender's decisions, never about how many fit in a time.
"""
from __future__ import annotations

import socket
import struct
import threading

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="real chunkcast UDP")
REPORT = struct.Struct(">IBBHQIIQQQQ")
START, MIN, MAX = 400_000, 100_000, 800_000
KNOB = "CHUNKCAST_FB_DEC_GAP_MS"


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("gap-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("gap-img"), cc / "fanout", kind="raw", chunks=48, noisy=())


def sender(cc, img, tmp_path, backlog, gap, *, expect=0):
    """One sender against a receiver that says `backlog` (always over FB_HIGH) every 25 ms."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    control, session = C.free_port(), C.new_session()
    stop = threading.Event()

    def feed():
        packet = REPORT.pack(0x54524631, 1, 0, 3, int(session, 16), 1, 0, 1, 0, backlog, 0)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            while not stop.wait(0.025):
                sock.sendto(packet, ("127.0.0.1", control))

    thread = threading.Thread(target=feed)
    thread.start()
    env = {k: v for k, v in C.ENV.items() if k != KNOB}
    if gap is not None:
        env[KNOB] = gap
    run = C._spawn([str(cc / "chunkcast"), "send", "--to", f"127.0.0.1:{C.free_port()}", "--session", session,
                    "--part", "3", "--pass", "1", "--index", str(img.idx), "--file", str(img.file), "--kbps", "80000",
                    "--write-kbps", str(START), "--lead-ms", "0", "--tail-ms", "0", "--control-port", str(control),
                    "--rate-min", str(MIN), "--rate-max", str(MAX)], tmp_path / "send", env)
    try:
        assert run.wait() == expect, run.log()
        return run
    finally:
        stop.set()
        thread.join(2)
        if run.process.poll() is None:
            run.process.kill()
            run.process.wait()


def decisions(run):
    return [(int(x.split()[1]), x.split()[2]) for x in run.out.read_text().splitlines() if x.startswith("rate ")]


def lowered(reason):
    return reason in ("decrease", "retract")


def adjacent_decreases(run):
    seq = [reason for _, reason in decisions(run)]
    return sum(1 for a, b in zip(seq, seq[1:]) if lowered(a) and lowered(b))


def test_today_a_receiver_that_stays_bad_is_lowered_on_every_decision(cc, img, tmp_path):
    """The control of the next test: without the knob, decisions in a row lower the rate (x0.8 each) down to the minimum."""
    run = sender(cc, img, tmp_path, 8 * C.CH, None)
    assert adjacent_decreases(run) >= 3, run.log()
    assert min(rate for rate, _ in decisions(run)) == MIN, run.log()
    assert "fb_damp" not in run.out.read_text() and "damped" not in run.out.read_text()


def test_with_a_gap_a_decrease_is_never_followed_by_another_at_once_and_the_rate_never_rises(cc, img, tmp_path):
    run = sender(cc, img, tmp_path, 8 * C.CH, "1500")
    seq = decisions(run)
    reasons = [reason for _, reason in seq]
    assert adjacent_decreases(run) == 0, run.log()
    assert reasons.count("decrease") >= 2 and "damped" in reasons, run.log()      # lowered again, but not at once
    assert "fb_damp gap_ms=1500" in run.out.read_text().splitlines(), run.log()
    assert "increase" not in reasons, run.log()                                   # damped is not a healthy decision either
    rates = [rate for rate, _ in seq]
    assert all(a >= b for a, b in zip(rates, rates[1:])) and rates[-1] < START, run.log()
    assert min(rates) > MIN, run.log()                                            # it did not fall to the minimum in a few decisions, as today's does


@pytest.mark.parametrize("gap", ["0", "10001", "abc", ""])
def test_a_gap_that_is_not_a_number_of_milliseconds_is_refused_not_ignored(cc, img, tmp_path, gap):
    run = sender(cc, img, tmp_path, 0, gap, expect=2)
    assert "nothing is started" in run.log() and not decisions(run), run.log()
