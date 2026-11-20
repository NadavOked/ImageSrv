"""#3125 (V4, V5): the counters of `chunkcast send` and the place in its stream (chunk k of N), read from the log.

What is checked:

* the parser -- the last `sent`, the `done`, a half-written last line (not a number: the line is not counted), the
  end of a long log;
* N -- the whole index in a first pass, the repair list in a repair pass, and not-known when the list is gone;
* a window -- the chunks and Mbps a sender did inside it, only for the same process, and not-measured (`null`, never
  0) for a counter that went down, a process that began after the window did, and a log that was not read;
* the wave -- `result_json.server.passes` keeps the last place each pass was seen at, `peaks.sent_mbps` the best window.

Negative control: server/learn/sender_senses.py removed (these tests import it), and the wiring test of the production
runner is in test_sender_senses_wiring_3125.py, which fails on an assert.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.learn import sender_senses as SS, server_senses as S
from test_chunksend_2533 import Lib

HZ = 100


# --- the log ------------------------------------------------------------------------------------------------------


def test_the_last_sent_line_and_the_done_line_are_what_the_log_says():
    got = SS.parse_log("sent 1 4000\nsent 2 8100\nsent 3 12500\n")
    assert got == {"sent": {"chunks": 3, "bytes": 12500}, "done": None}
    got = SS.parse_log("sent 9 36000\ndone 10 40000 7312\n")
    assert got["done"] == {"chunks": 10, "bytes": 40000, "ms": 7312}


def test_a_half_written_last_line_is_not_a_number():
    """`sent 12 4` could be the start of `sent 12 4800`: only a line that ended with its newline is counted."""
    assert SS.parse_log("sent 3 12500\nsent 12 4")["sent"] == {"chunks": 3, "bytes": 12500}
    assert SS.parse_log("sent 12 4")["sent"] is None


def test_only_the_end_of_a_long_log_is_read_and_its_first_line_is_not_trusted(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("sent 1 1\n" * 3000 + "sent 77 8800\n", encoding="ascii")
    assert log.stat().st_size > SS.TAIL_BYTES
    assert SS.read_log(log)["sent"] == {"chunks": 77, "bytes": 8800}
    assert SS.read_log(tmp_path / "missing.log") is None            # not read is not "nothing said"
    log.write_text("chunkcast: send: cannot send\n", encoding="ascii")
    assert SS.read_log(log)["sent"] is None                          # no `sent` line yet: not measured, not 0


# --- N ------------------------------------------------------------------------------------------------------------


def test_n_is_the_index_in_a_first_pass_and_the_repair_list_in_a_repair_pass(tmp_path):
    Lib(tmp_path, chunks=10)
    part = tmp_path / "p3.windows.pcl.zst"
    assert SS.total(["chunkcast", "send", "--file", str(part)]) == 10
    listed = tmp_path / "chunks.txt"
    listed.write_text("0-3,7-7\n", encoding="ascii")
    assert SS.total(["chunkcast", "send", "--file", str(part), "--chunks-file", str(listed)]) == 5
    listed.unlink()                                                  # the engine removes it when the pass ends
    assert SS.total(["chunkcast", "send", "--file", str(part), "--chunks-file", str(listed)]) is None
    listed.write_text("5-2\n", encoding="ascii")
    assert SS.total(["chunkcast", "send", "--file", str(part), "--chunks-file", str(listed)]) is None
    assert SS.total(["chunkcast", "send", "--file", str(tmp_path / "other.zst")]) is None     # no index: unknown


# --- a window -----------------------------------------------------------------------------------------------------


def _seen(chunks, nbytes, *, start=500, of=10, part=3, pass_=1, elapsed=12.0, done=None):
    return {"start": start, "part": part, "pass": pass_, "chunks": chunks, "bytes": nbytes, "of": of,
            "elapsed_s": elapsed, "done": done}


def test_a_window_says_the_place_the_chunks_and_the_mbps_of_the_same_sender():
    """V5: chunk 4 of 10 = 40%. V4: 3 chunks and 5 MB in 10 s = 4 Mbps, by the stream's own bytes."""
    got = SS.step({"77": _seen(1, 1_000_000)}, {"77": _seen(4, 6_000_000)}, 10.0)["77"]
    assert (got["chunks"], got["of"], got["pct"]) == (4, 10, 40.0)
    assert (got["chunks_in_window"], got["mbps"], got["bytes"], got["elapsed_s"]) == (3, 4.0, 6_000_000, 12.0)
    assert SS.mbps({"77": got}) == 4.0


def test_what_was_not_measured_is_null_in_a_window():
    after = {"77": _seen(4, 6_000_000)}
    # the sender began inside the window: its place is known, what it did "in the window" is not
    began = SS.step({}, after, 10.0)["77"]
    assert began["pct"] == 40.0 and began["chunks_in_window"] is None and began["mbps"] is None
    # another process under the same pid (it began later): not the same sender
    assert SS.step({"77": _seen(1, 1, start=100)}, after, 10.0)["77"]["mbps"] is None
    # a counter that went down is not a negative rate
    assert SS.step({"77": _seen(9, 9_000_000)}, after, 10.0)["77"]["chunks_in_window"] is None
    # no `sent` line yet, an unread index, a log that was not read
    quiet = SS.step({"77": _seen(None, None, of=None)}, {"77": _seen(None, None, of=None)}, 10.0)["77"]
    assert (quiet["pct"], quiet["chunks_in_window"], quiet["mbps"]) == (None, None, None)
    assert SS.mbps({"77": quiet}) is None and SS.mbps({}) is None and SS.mbps(None) is None


def test_the_list_of_processes_that_was_not_read_is_not_the_same_as_no_sender(tmp_path):
    assert SS.read(tmp_path, None) is None and SS.read(tmp_path, {"chunkcast": None}) is None
    assert SS.read(tmp_path, {"hz": HZ, "uptime": 10.0, "chunkcast": {}}) == {}
    assert SS.step(None, None, 10.0) is None and SS.step(None, {}, 10.0) == {}


# --- the wave -----------------------------------------------------------------------------------------------------


def _bare(at, sender_=None):
    return {"at": at, "disk": None, "cpu_psi": {"some": None, "full": None}, "mem_psi": {"some": None, "full": None},
            "load1": None, "proc": {"hz": HZ, "uptime": None, "server": 0, "children": None, "chunkcast": None},
            "nic": {"tx_bytes": None, "tx_dropped": None}, "http": None, "ledger": None, "engine": None,
            "sender": sender_}


def test_the_wave_keeps_the_last_place_of_each_pass_and_the_best_window(tmp_path):
    conn = db.connect(tmp_path / "imagesrv.db")
    from server.learn import server_io
    server_io.begin(conn, "wave", "ses_1", ".")
    conn.execute("UPDATE server_io SET result_json = ?, ended_at = ? WHERE kind = 'wave' AND ref = ?",
                 (json.dumps({"basis": "ok"}), db.now_iso(), "ses_1"))
    conn.commit()
    readings = iter([_bare(0.0, {}),
                     _bare(30.0, {"77": _seen(3, 3_000_000)}),
                     _bare(60.0, {"77": _seen(9, 12_000_000, elapsed=42.0, done={"chunks": 9, "bytes": 12_000_000, "ms": 41000})}),
                     _bare(90.0, {"78": _seen(2, 2_000_000, start=900, pass_=2, of=5)})])
    senses = S.Senses(conn, lambda session_id: next(readings), lambda: "ses_1")
    senses.begin("ses_1")
    senses.tick()
    senses.tick()
    summary = senses.end("ses_1")
    assert summary["passes"]["p3/r1"] == {"chunks": 9, "of": 10, "bytes": 12_000_000, "elapsed_s": 42.0,
                                          "done": {"chunks": 9, "bytes": 12_000_000, "ms": 41000}}
    assert summary["passes"]["p3/r2"]["chunks"] == 2 and summary["passes"]["p3/r2"]["of"] == 5
    assert summary["peaks"]["sent_mbps"] == 2.4                          # (12 - 3) MB of the stream in 30 s = 2.4 Mbps
    assert "sender" not in summary and "sent_mbps" not in summary        # one window's, not the wave's
    result = json.loads(conn.execute("SELECT result_json FROM server_io WHERE kind = 'wave'").fetchone()[0])
    assert result["server"]["passes"] == summary["passes"]
    rows = conn.execute("SELECT result_json FROM server_io WHERE kind = 'window' AND ref = 'ses_1/2'").fetchone()
    assert json.loads(rows[0])["sender"]["77"]["pct"] == 90.0 and json.loads(rows[0])["sender"]["77"]["mbps"] == 2.4
