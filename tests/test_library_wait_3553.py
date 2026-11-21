"""#3553: the library disk is `server_disk` only when the sender WAITED for it -- measured by `read_ms` / `readq_empty_ms`, not by its sleep.

`server_io.sender_waits` asked "did the sender sleep for its pacing more than 25% of the first pass?" and answered "not decided" when it did. The new
engine sleeps 57-69% of the first pass on purpose (`--kbps`, `--write-kbps`), so 22 of 24 chunk waves of the metal (r14+r16, 16/11) came out
`server_disk_undecided why=sender_paced`: whatever the library disk did, `basis` was empty, and what learns from it -- the read ceiling
(`server_cap`) and the network ceiling (`netcap._pushed` wants `basis == ok`) -- could never learn. The sender's own `sendstats` line says what the gate asks:
how long it waited for the file. Inline reader: `read_ms`. Reader thread: `readq_empty_ms` (its own `read_ms` runs beside the sending).

**The numbers below are the real waves of `lab/metal-1116-r16` and `-r14`** (`runs/<run>/sender-raw/*.log`, `db-extra.json`, `journal.txt`): the four partitions'
`sendstats` lines as the sender printed them, the wave's first pass and sleep as `rate_outcome` summed them, and the disk's util / await / read from the journal
line `server_disk_undecided` the old rule wrote for the same wave. r14 has no `sendstats` (an older sender): the real "not measured".

**Not touched, and tested as such:** a wave whose sender is held by its own pacing still proves no network ceiling (#3536, `netcap._pushed`), and
`sendstats` that is missing is "not measured", never "did not wait" (principle 5).

**Negative control:** `server/learn/server_io.py`, `server/rounds/chunklog.py`, `server/rounds/chunksend.py`, `server/rounds/chunk_rate.py` and
`server/brain/netcap.py` back at the base: the real waves come out `undecided` with `sender_paced`, and every test that reads a `sendstats` fails on behaviour.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.brain import netcap
from server.learn import server_io
from server.rounds import chunk_rate, chunklog, chunksend

from test_chunk_nic_gate_2533 import _sending                      # noqa: F401 -- fixture dependency of the rig
from test_chunksend_2533 import ALL, SID, Runner, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_server_room import room_server                          # noqa: F401 -- shared fixture

KNOBS = "knobs ahead=4 readahead=on order=mixed fb_low=2 fb_high=6 qcap=8 pace={pace} batch=64 reader={reader} readq=8"
STATS = ("sendstats read_ms={read} lag_forgiven_wire=0 lag_forgiven_disk=0 forgiven_ms_wire=0 forgiven_ms_disk=0 enobufs=0 eagain=0 eintr=0"
         " refused=0 over_ms=0 readq_empty_ms={empty} readq_full_ms=0")

#: wave -> (reader, pace, [(read_ms, readq_empty_ms) of p1..p4], first_pass_s, paced_ms, (read MB/s, util, await ms) of the library disk, ended up as)
#: from `lab/metal-1116-r16/runs/<run>-chunk`; `sender-raw/*-p<N>.*.log`, `db-extra.json` (wave_rates.stream.chunk), `journal.txt`.
R16 = {
    "M0-1": ("inline", "legacy", [(68, 0), (135, 0), (64446, 0), (5284, 0)], 334.2560730279997,
             {"wire": 107268, "disk": 110934}, (52.8, 0.52, 10.0)),
    "G35-1": ("inline", "legacy", [(92, 0), (145, 0), (68566, 0), (5513, 0)], 332.8941166730001,
              {"wire": 107378, "disk": 105144}, (48.0, 0.48, 10.4)),
    "RT-1": ("thread", "legacy", [(105, 0), (205, 0), (110564, 3590), (9373, 2618)], 321.47776908799915,
             {"wire": 128127, "disk": 140317}, (54.3, 0.51, 18.2)),
    "RS-1": ("thread", "smooth", [(107, 0), (184, 0), (73306, 274), (9944, 3308)], 353.5895461039963,
             {"wire": 116656, "disk": 126985}, (50.1, 0.40, 14.0)),
    "RS-2": ("thread", "smooth", [(96, 0), (184, 0), (75290, 108), (9639, 3017)], 340.9823318109993,
             {"wire": 116908, "disk": 113586}, (52.2, 0.42, 14.0)),
}
#: r14 `MX-1`: the sender of that day printed no `sendstats` at all
R14_MX1 = {"first_pass_s": 335.81602769804886, "paced_ms": {"wire": 107283, "disk": 108714}, "sendstats": None}
R14_MX1_DISK = (51.7, 0.52, 10.3)


def _log(reader: str, pace: str, read: int, empty: int) -> str:
    return "sent 1 2\npaced 1 2\n" + KNOBS.format(pace=pace, reader=reader) + "\n" + STATS.format(read=read, empty=empty) + "\ndone 1 2 3\n"


def _outcome(run: str) -> dict:
    """What `ChunkEngine.rate_outcome` gives for the wave: the sender's logs of the four partitions, read and summed as the engine does."""
    reader, pace, parts, seconds, paced, _disk = R16[run]
    each = [chunklog.parse_sendstats(_log(reader, pace, read, empty)) for read, empty in parts]
    return {"first_pass_s": seconds, "paced_ms": paced, "sendstats": chunksend._sum_sendstats(each)}


def _snaps(seconds: float, disk: tuple[float, float, float]) -> tuple[dict, dict]:
    """The two `/proc` snapshots of a wave of `seconds` on a library disk that read `read` MB/s, was `util` busy, `await_ms` a request."""
    read, util, await_ms = disk
    ios = 100_000
    snap = {"device": "sdb", "write_sectors": 0, "write_ios": 0, "psi_some_us": 0, "psi_full_us": 0}
    return ({"at": 0.0, "read_sectors": 0, "io_ms": 0, "read_ios": 0, "ios_ms": 0, **snap},
            {"at": seconds, "read_sectors": int(read * 1e6 * seconds / 512), "io_ms": int(util * seconds * 1000), "read_ios": ios,
             "ios_ms": int(await_ms * ios), **snap})


@pytest.mark.parametrize("run", sorted(R16))
def test_a_real_chunk_wave_of_the_metal_that_the_sender_slept_through_is_decided_ok_by_its_wait_for_the_file(run):
    """(neg) The thresholds said busy for each of these (the old rule wrote `server_disk_undecided` for all five); the sender slept 64-84% for its
    own pacing and waited 1-23% for the file. Decided now: `ok` -- the disk was not what the sender waited for."""
    out = _outcome(run)
    assert server_io.sender_asleep(out) == "sender_paced"                          # why the old rule gave up on it
    start, end = _snaps(out["first_pass_s"], R16[run][5])
    got = server_io.measure(start, end, seconds=out["first_pass_s"], sender=out, need_sender=True)
    assert got["looked"] == "server_disk" and "undecided" not in got               # the thresholds did say busy
    assert got["basis"] == "ok" and 0 <= got["library_wait"] < 0.25, got


def test_a_reader_thread_is_not_waited_for_by_its_own_reads_the_real_rt1_would_have_been_server_disk():
    """(neg) `RT-1`: the thread's `read_ms` is 37% of the first pass (it reads beside the sending) and the sender waited for the empty queue 1.9% of it."""
    out = _outcome("RT-1")
    assert out["sendstats"]["reader"] == "thread" and out["sendstats"]["read_ms"] == 120247 and out["sendstats"]["readq_empty_ms"] == 6208
    assert out["sendstats"]["read_ms"] / (out["first_pass_s"] * 1000) > server_io.LIBRARY_WAIT_MAX     # the wrong field
    assert server_io.library_wait(out) == pytest.approx(6208 / (out["first_pass_s"] * 1000))          # the right one: 1.9%
    start, end = _snaps(out["first_pass_s"], R16["RT-1"][5])
    assert server_io.measure(start, end, seconds=out["first_pass_s"], sender=out, need_sender=True)["basis"] == "ok"


def test_an_inline_wave_that_waited_for_the_file_over_the_share_is_server_disk_and_saturated():
    """Not a measurement: the real `M0-1` with the big partition's `read_ms` doubled (the sender waited 40% of the pass), on a library disk at 96%:
    `server_disk` and saturated -- the wave `server_cap` learns a read ceiling from, which no real wave of 16/11 could be."""
    _reader, _pace, _parts, seconds, paced, _disk = R16["M0-1"]
    out = {"first_pass_s": seconds, "paced_ms": paced, "sendstats": {"read_ms": 68 + 135 + 2 * 64446 + 5284, "readq_empty_ms": 0, "reader": "inline"}}
    start, end = _snaps(seconds, (84.0, 0.96, 10.0))
    got = server_io.measure(start, end, seconds=seconds, sender=out, need_sender=True)
    assert got["basis"] == "server_disk" and 0.40 < got["library_wait"] < 0.41 and got["util"] >= 0.9


def test_a_real_wave_of_r14_with_no_sendstats_is_not_measured_not_ok():
    """(neg) r14 `MX-1`: the sender of that day printed no `sendstats`. The thresholds said busy (52%, 10.3ms); nobody says the sender waited or not."""
    start, end = _snaps(R14_MX1["first_pass_s"], R14_MX1_DISK)
    got = server_io.measure(start, end, seconds=R14_MX1["first_pass_s"], sender=R14_MX1, need_sender=True)
    assert got["basis"] is None and got["looked"] == "server_disk" and got["undecided"] == "sender_not_measured" and got["library_wait"] is None


# --- from the sender's log to the outcome ---------------------------------------------------------------------------------------


def test_the_sendstats_line_is_read_whole_with_its_reader_and_a_missing_one_is_not_measured(tmp_path):
    log = tmp_path / "send.log"
    log.write_text(_log("thread", "smooth", 73306, 274), encoding="utf-8")
    assert chunklog.sendstats_of(log) == {"read_ms": 73306, "readq_empty_ms": 274, "reader": "thread"}
    log.write_text("sent 1 10\npaced 1 2\nchunkcast: send: stopped\n", encoding="utf-8")
    assert chunklog.sendstats_of(log) is None                               # a sender killed before it printed it
    assert chunklog.sendstats_of(tmp_path / "missing.log") is None          # no log
    log.write_text(_log("inline", "legacy", 0, 0), encoding="utf-8")
    assert chunklog.sendstats_of(log) == {"read_ms": 0, "readq_empty_ms": 0, "reader": "inline"}   # a sender that never waited
    half = _log("inline", "legacy", 5, 0).replace("\ndone 1 2 3\n", "")
    log.write_text(half[:-12], encoding="utf-8")                            # the last line half written, no newline
    assert chunklog.sendstats_of(log) is None
    old = "knobs ahead=4 readahead=on order=seq fb_low=2 fb_high=6 qcap=8\nsendstats read_ms=7 lag_forgiven_wire=0 lag_forgiven_disk=0 " \
          "forgiven_ms_wire=0 forgiven_ms_disk=0 enobufs=0 eagain=0 eintr=0 refused=0\n"
    log.write_text(old, encoding="utf-8")                                   # a sender from before the reader thread: no reader, no queue
    assert chunklog.sendstats_of(log) == {"read_ms": 7, "readq_empty_ms": None, "reader": None}


def test_a_partition_that_was_not_read_makes_the_whole_wave_not_measured():
    """Principle 5: a partial sum is a number that looks like a measurement."""
    ok = chunklog.parse_sendstats(_log("inline", "legacy", 5, 0))
    assert chunksend._sum_sendstats([ok, ok]) == {"read_ms": 10, "readq_empty_ms": 0, "reader": "inline"}
    assert chunksend._sum_sendstats([ok, None]) is None and chunksend._sum_sendstats([None]) is None and chunksend._sum_sendstats([]) is None
    thread = chunklog.parse_sendstats(_log("thread", "legacy", 5, 1))
    assert chunksend._sum_sendstats([ok, thread])["reader"] is None        # two senders that read differently: nobody's wait
    assert server_io.library_wait({"first_pass_s": 100.0, "sendstats": chunksend._sum_sendstats([ok, thread])}) is None


def test_the_four_real_partitions_sum_to_the_wave():
    assert _outcome("M0-1")["sendstats"] == {"read_ms": 69933, "readq_empty_ms": 0, "reader": "inline"}
    assert _outcome("RS-1")["sendstats"] == {"read_ms": 83541, "readq_empty_ms": 3582, "reader": "thread"}


class _StatsRunner(Runner):
    """A fake `chunkcast send` that leaves the log a real one leaves: the file of `--file`, with its `sendstats` line (or without)."""

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def __call__(self, cmd):
        process = super().__call__(cmd)
        chunklog.log_path(cmd[cmd.index("--file") + 1]).write_text(self.text)
        return process


def _first_pass(rig):
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": ALL}, skipped={"C1": 0})["action"] == "done"


def test_the_engine_reports_how_long_the_sender_waited_for_the_file_over_the_first_pass(rig, tmp_path, monkeypatch):
    """(neg) `rate_outcome["sendstats"]`: the log of the first pass, read right after it, summed over the partitions."""
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    rig.runner = _StatsRunner(_log("thread", "smooth", 73306, 274))
    engine = rig.make()
    _first_pass(rig)
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine.rate_outcome(SID)["sendstats"] == {"read_ms": 73306, "readq_empty_ms": 274, "reader": "thread"}


def test_a_sender_that_printed_no_sendstats_is_not_measured_not_zero(rig, tmp_path, monkeypatch):
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    rig.runner = _StatsRunner("sent 10 20\npaced 1 2\ndone 10 20 5\n")
    engine = rig.make()
    _first_pass(rig)
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine.rate_outcome(SID)["sendstats"] is None


def test_sample_of_keeps_a_measured_wait_and_drops_a_malformed_one():
    base = {"chunks": 10, "lost_chunks": 0, "first_pass_s": 100, "bytes": 10, "stream_zbytes": 5, "ended": "send_done"}
    good = {"read_ms": 5, "readq_empty_ms": 2, "reader": "thread"}
    assert chunk_rate.sample_of(1000, {**base, "sendstats": good})["sendstats"] == good
    assert chunk_rate.sample_of(1000, {**base, "sendstats": {**good, "readq_empty_ms": None}})["sendstats"]["readq_empty_ms"] is None
    for bad in (None, {}, {**good, "read_ms": -1}, {**good, "read_ms": True}, {**good, "reader": None}, {**good, "reader": "x"},
                {**good, "readq_empty_ms": -2}, "5"):
        assert chunk_rate.sample_of(1000, {**base, "sendstats": bad})["sendstats"] is None
    assert chunk_rate.sample_of(1000, base)["sendstats"] is None


# --- the wave through the server, and what it must still not prove ---------------------------------------------------------------


def test_the_server_decides_the_real_wave_from_the_engines_outcome(room_server, monkeypatch):
    """(neg) `server.app.on_chunk_event` hands `rate_outcome` (with `sendstats`) to `server_io.end`: the real RS-1 comes out `ok`, not `sender_paced`."""
    ctx = room_server["ctx"]
    out = _outcome("RS-1")
    start, end = _snaps(out["first_pass_s"], R16["RS-1"][5])
    monkeypatch.setattr(server_io, "snapshot", lambda root, proc=server_io.PROC: end)
    monkeypatch.setattr(ctx.chunk, "rate_outcome", lambda session_id: out)
    ctx.conn.execute("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json) VALUES ('wave', 'ses_rs1', 'x', ?)", (json.dumps(start),))
    ctx.conn.commit()
    ctx.chunk.on_event("send_done", "x", "ses_rs1")
    got = json.loads(ctx.conn.execute("SELECT result_json FROM server_io WHERE ref = 'ses_rs1'").fetchone()["result_json"])
    assert got["basis"] == "ok" and "undecided" not in got and got["library_wait"] < 0.02
    assert ctx.conn.execute("SELECT basis FROM server_io WHERE ref = 'ses_rs1'").fetchone()["basis"] == "ok"
    assert server_io.bound(ctx.conn, "ses_rs1") is False


def test_a_decided_wave_whose_sender_was_held_by_its_own_pacing_still_proves_no_network_ceiling():
    """Guard, both states (#3536/#3530): the real RS-1 -- `basis` ok now, 419.6Mb/s sent of 669m asked, asleep 69% -- is no proof of a network ceiling."""
    out = _outcome("RS-1")
    sample = {"asked_bps": 669_000_000, "sent_bps": 419_592_605, "first_pass_s": out["first_pass_s"], "paced_ms": out["paced_ms"]}
    assert netcap._pushed(sample, "ok") is False
    assert netcap._pushed({**sample, "sent_bps": 669_000_000, "paced_ms": {"wire": 0, "disk": 0}}, "ok") is True     # awake and kept up
    assert netcap._pushed({**sample, "sent_bps": 669_000_000, "paced_ms": {"wire": 0, "disk": 0}}, None) is False    # disk not measured
