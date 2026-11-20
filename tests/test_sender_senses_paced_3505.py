"""#3505 (V4): which of the sender's two pacings held it -- `paced <wire_ms> <disk_ms>` -- read per window.

`chunkcast send` prints `paced` once a second after `sent` (cumulative sleeps for `--kbps` and `--write-kbps`, #3358).
metal-1115-compare: the new engine was 24% slower and the one number that decides whether `--write-kbps` was the limit
was not kept. These tests check the parser, `read` (from a process list and the log the runner wrote), and a window.

What is checked:

* the last `paced` of a log, whole lines only, and that `parse_log` still answers what it answered;
* `read` carries `wire_ms` / `disk_ms`, and `null` (never 0) when the log has no `paced` line yet or was not read;
* a window carries how much each grew, for the same process only, and `null` for a counter that went down.

Negative control: server/learn/sender_senses.py back to the base -- every test here fails on an assert or an import.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.learn import sender_senses as SS
from server.rounds import chunklog
from test_chunksend_2533 import Lib

HZ = 100


def test_the_last_paced_line_is_the_sleep_so_far():
    text = "sent 1 4000\npaced 10 0\nsent 2 8000\npaced 40 7\n"
    assert SS.parse_paced(text) == {"wire_ms": 40, "disk_ms": 7}
    assert SS.parse_paced("sent 1 4000\n") is None                       # not printed yet: not measured, not 0
    assert SS.parse_paced("") is None


def test_a_half_written_paced_line_is_not_a_number():
    assert SS.parse_paced("paced 10 5\npaced 4") == {"wire_ms": 10, "disk_ms": 5}
    assert SS.parse_paced("paced 4") is None


def test_the_first_line_of_a_tail_read_from_the_middle_is_not_trusted():
    assert SS.parse_paced("ced 99 99\npaced 5 6\n", whole=False) == {"wire_ms": 5, "disk_ms": 6}
    assert SS.parse_paced("paced 99 99\n", whole=False) is None          # the only line is the cut one


def test_parse_log_still_says_only_sent_and_done_and_ignores_paced():
    got = SS.parse_log("sent 3 12500\npaced 7 9\ndone 3 12500 800\n")
    assert got == {"sent": {"chunks": 3, "bytes": 12500}, "done": {"chunks": 3, "bytes": 12500, "ms": 800}}


def test_read_log_keeps_paced_and_a_log_without_it_says_none(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("sent 3 12500\npaced 120 4\n", encoding="ascii")
    got = SS.read_log(log)
    assert got["sent"] == {"chunks": 3, "bytes": 12500} and got["paced"] == {"wire_ms": 120, "disk_ms": 4}
    log.write_text("sent 3 12500\n", encoding="ascii")
    assert SS.read_log(log)["paced"] is None
    assert SS.read_log(tmp_path / "missing.log") is None                # not read is not "nothing said"


@pytest.fixture()
def one_sender(tmp_path, monkeypatch):
    """A fake /proc with one `chunkcast send`, and the log the runner wrote for it."""
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(chunklog, "LOG_DIR", logs)
    parts = tmp_path / "img_x"
    parts.mkdir()
    Lib(parts, chunks=10)
    part = parts / "p3.windows.pcl.zst"
    proc = tmp_path / "proc" / "77"
    proc.mkdir(parents=True)
    argv = ["chunkcast", "send", "--part", "3", "--pass", "1", "--file", str(part), "--kbps", "700000"]
    (proc / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    procs = {"hz": HZ, "uptime": 50.0, "chunkcast": {"77": (500, 0)}}
    return tmp_path / "proc", procs, chunklog.log_path(part)


def test_read_says_how_long_each_sender_has_slept_for_each_rate(one_sender):
    proc, procs, log = one_sender
    log.write_text("sent 4 6000000\npaced 1500 20\n", encoding="ascii")
    seen = SS.read(proc, procs)["77"]
    assert (seen["chunks"], seen["wire_ms"], seen["disk_ms"]) == (4, 1500, 20)
    log.write_text("sent 4 6000000\n", encoding="ascii")                 # the first `paced` is after the first second
    seen = SS.read(proc, procs)["77"]
    assert (seen["chunks"], seen["wire_ms"], seen["disk_ms"]) == (4, None, None)
    log.unlink()                                                         # the log was not read
    seen = SS.read(proc, procs)["77"]
    assert (seen["chunks"], seen["wire_ms"], seen["disk_ms"]) == (None, None, None)


def _seen(chunks, nbytes, wire, disk, *, start=500):
    return {"start": start, "part": 3, "pass": 1, "chunks": chunks, "bytes": nbytes, "of": 10, "elapsed_s": 12.0,
            "done": None, "wire_ms": wire, "disk_ms": disk}


def test_a_window_says_how_much_each_sleep_grew_in_it():
    got = SS.step({"77": _seen(1, 1_000_000, 100, 4000)}, {"77": _seen(4, 6_000_000, 130, 7600)}, 10.0)["77"]
    assert (got["wire_wait_ms"], got["disk_wait_ms"]) == (30, 3600)       # the disk pacing held it 36% of the window
    assert (got["wire_ms"], got["disk_ms"]) == (130, 7600)                # and what it has slept in all
    assert got["mbps"] == 4.0                                             # V4's own numbers are unchanged


def test_what_was_not_measured_is_null_in_a_window_never_zero():
    after = {"77": _seen(4, 6_000_000, 130, 7600)}
    began = SS.step({}, after, 10.0)["77"]                                # began inside the window
    assert (began["wire_wait_ms"], began["disk_wait_ms"]) == (None, None) and began["disk_ms"] == 7600
    other = SS.step({"77": _seen(1, 1, 5, 5, start=100)}, after, 10.0)["77"]      # another process under the same pid
    assert (other["wire_wait_ms"], other["disk_wait_ms"]) == (None, None)
    down = SS.step({"77": _seen(1, 1, 999, 9999)}, after, 10.0)["77"]    # a counter that went down is not a negative sleep
    assert (down["wire_wait_ms"], down["disk_wait_ms"]) == (None, None)
    quiet = SS.step({"77": _seen(1, 1, None, None)}, {"77": _seen(2, 2, None, None)}, 10.0)["77"]
    assert (quiet["wire_ms"], quiet["wire_wait_ms"], quiet["disk_wait_ms"]) == (None, None, None)
    one_side = SS.step({"77": _seen(1, 1, None, None)}, after, 10.0)["77"]  # `paced` appeared inside the window
    assert (one_side["wire_wait_ms"], one_side["disk_wait_ms"]) == (None, None)
