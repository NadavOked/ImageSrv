"""#3599 (a technical decision of the coordinator): the feedback's `--rate-max` is a ceiling that GROWS while every receiver is healthy.

J1 (metal 17/11): the sender sat on `--rate-max` (2 x the write check's start) 94.5% of the ticks with `increase` verdicts, a drive that
could write 253MB/s held at 224. Now `chunkcast send --rate-cap N` (N >= `--rate-max`) lets the ceiling grow by 10% after every
`CC_FB_WIDEN_TICKS` decisions pinned on it with all receivers healthy (reason `widen`); the first decrease after a widening puts it
back to the last ceiling that was healthy for a whole window (reason `retract`), and it does not widen again in that pass. Without
`--rate-cap` nothing changes. The receivers, the left-behind rule, the reports (#3512) are untouched: only who sets the ceiling.

The sender is a real binary (-Wall -Wextra -Werror) fed with synthetic receiver reports on loopback, like `test_chunkcast_feedback.py`.
A "pressure" report is sent only AFTER the sender printed `widen` (read from its own output), so the order is not a matter of timing.

**Negative control:** `agent/chunkcast.c` and `server/rounds/chunksend.py` back at the base: `--rate-cap` is an unknown option (exit 2), every
test of the sender fails on behaviour (no `widen` / `retract` in the sender's output, or the run is refused) except the ones marked (guard).
"""

from __future__ import annotations

import socket
import threading

import pytest

import chunkcast_rig as C
from native import requires_native
from test_chunkcast_feedback import cc, img, rates, report   # noqa: F401 -- fixtures and the packet builders

pytestmark = requires_native("gcc", "zstd", posix=True, why="real chunkcast UDP on loopback")

# kbit/s; 48 chunks of 4MiB at ~14MB/s: ~14 s. #3870: the sender decides once per ~100 ms of its pacing sleep, and at the old
# 200_000/220_000 a chunk (~150 ms) held ~1 decision: ~48 in the whole run, where two widenings need ~51 (5 to the ceiling,
# 20, 6 to the new one, 20). It passed on the odd extra decision, and one `missing` under load (a report late by 300 ms)
# ended it with a single `widen` (1119-223212-argus-r44-bt4, 1120-042152-argus-r44d-bt4, 1120-052838-argus-r44d2-bt3).
# A chunk of ~300 ms holds ~3: ~140 decisions, room for several restarted windows. What is asserted is unchanged.
START, MIN, MAX, CAP = 100_000, 90_000, 110_000, 800_000


def drive(cc, img, tmp_path, packets, *, cap=None):
    """Runs `chunkcast send` while a thread feeds it `packets(session, sender_output_text)`; returns the run."""
    control, session = C.free_port(), C.new_session()
    stop, holder = threading.Event(), {}

    def feed():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            while not stop.wait(0.025):
                run = holder.get("run")
                said = run.out.read_text() if run is not None and run.out.exists() else ""
                for packet in packets(session, said):
                    sock.sendto(packet, ("127.0.0.1", control))

    thread = threading.Thread(target=feed)
    thread.start()
    run = None
    try:
        extra = ("--control-port", str(control), "--rate-min", str(MIN), "--rate-max", str(MAX),
                 *(("--rate-cap", str(cap)) if cap is not None else ()))
        run = C.send(cc, img, f"127.0.0.1:{C.free_port()}", session, tag=tmp_path / "send", write_kbps=START, extra=extra)
        holder["run"] = run
        assert run.wait() == 0, run.log()
        return run
    finally:
        stop.set()
        thread.join(2)
        if run and run.process.poll() is None:
            run.process.kill()
            run.process.wait()


def reasons(run):
    return [reason for _rate, reason in rates(run)]


def test_the_ceiling_grows_by_ten_percent_while_every_receiver_is_healthy(cc, img, tmp_path):
    run = drive(cc, img, tmp_path, lambda s, said: [report(s)], cap=CAP)
    values = rates(run)
    assert reasons(run).count("widen") >= 2, run.log()
    assert max(rate for rate, _ in values) > MAX, run.log()                    # it went above the old ceiling...
    assert max(rate for rate, _ in values) <= CAP, run.log()                   # ...never above the cap
    first = next(i for i, (_, reason) in enumerate(values) if reason == "widen")
    assert values[first][0] == MAX                                              # the step happens ON the ceiling
    assert any(rate > MAX and reason == "increase" for rate, reason in values[first:]), run.log()
    assert "retract" not in reasons(run)


def test_pressure_after_a_widening_puts_the_ceiling_back_and_it_does_not_widen_again(cc, img, tmp_path):
    def packets(s, said):
        return [report(s, backlog=8 * C.CH, drops=1)] if "widen" in said else [report(s)]
    run = drive(cc, img, tmp_path, packets, cap=CAP)
    seen = reasons(run)
    assert "retract" in seen, run.log()
    after = rates(run)[seen.index("retract"):]
    assert all(rate <= MAX for rate, _ in after), after                         # the last ceiling that was healthy for a whole window: --rate-max
    assert "widen" not in seen[seen.index("retract"):], seen


def test_pressure_before_a_whole_window_never_widens(cc, img, tmp_path):
    """(guard) a receiver in trouble from the start: the ceiling stays where it was asked."""
    run = drive(cc, img, tmp_path, lambda s, said: [report(s, backlog=8 * C.CH)], cap=CAP)
    assert "widen" not in reasons(run) and all(rate <= MAX for rate, _ in rates(run)), run.log()


def test_without_a_cap_the_ceiling_is_where_it_was_asked(cc, img, tmp_path):
    """(guard) the bounds of #3512, as they were: healthy for the whole run, and the rate never leaves [--rate-min, --rate-max]."""
    run = drive(cc, img, tmp_path, lambda s, said: [report(s)])
    assert "widen" not in reasons(run) and all(MIN <= rate <= MAX for rate, _ in rates(run)), run.log()
    assert any(rate == MAX for rate, _ in rates(run)), run.log()


def test_a_cap_below_the_ceiling_is_refused(cc, img, tmp_path):
    control = C.free_port()
    run = C.send(cc, img, f"127.0.0.1:{C.free_port()}", C.new_session(), tag=tmp_path / "send", write_kbps=START,
                 extra=("--control-port", str(control), "--rate-min", str(MIN), "--rate-max", str(MAX), "--rate-cap", str(MAX - 1)))
    assert run.wait() == 2, run.log()
