"""Index rejections must identify a shared chunk before the wave blames its source."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native
from server.brain import integrity
from test_brain_integrity_3064 import conn, member
from test_chunksend_2533 import SID, rig, wait_for


@requires_native("sh")
def test_receive_pass_forwards_rejections_to_ended(tmp_path):
    script = r'''
RUN_DIR="$1"; LOG_FILE="$1/log"
. agent/lib/restore/chunkgate.sh
. agent/lib/restore/chunkrecv.sh
_chunk_nodes() { echo test-disk; }
target_counter() { :; }
jq() { echo abc; }
http_get() { echo 'IMAGESRV-IDX 2 raw'; }
fanout() { echo abc; }
chunk_b3_fetch() { :; }
next_stream_ceiling() { :; }
cancel_pending() { :; }
chunk_loss_mark() { :; }
chunk_loss_end() { :; }
chunkcast() { printf 'listening\nbad_chunk 2\nbad_chunk 2\nstray 3\npass ended\n'; }
_chunk_run() { wait "$2"; }
log() { :; }
_chunk_verdicts() { :; }
_chunk_counters() { :; }
_any_alive() { :; }
json_get() { :; }
_chunk_tell() {
    printf '{"state":"%s"%s}\n' "$1" "$(chunk_stray_json "$1")"
    [ "$1" != ended ] || exit 0
}
chunk_partition_drawers server img_1 3 raw file manifest disk
'''
    result = subprocess.run(["sh", "-c", script, "sh", tmp_path.as_posix()],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"state": "listening"}, {"state": "ended", "bad_chunks": [2], "stray": 3}]


def test_ended_report_carries_receiver_rejections(tmp_path):
    if os.name == "nt":
        probe = subprocess.run(["sh", "-c", "exit 0"], stdin=subprocess.DEVNULL, capture_output=True)
        if probe.returncode and b"CreateFileMapping" in probe.stderr:
            pytest.skip("Git sh cannot start in this Windows sandbox (CreateFileMapping denied)")
    (tmp_path / "skipped").write_text("bad_chunk 5\nbad_chunk 5\nstray 2\n", encoding="ascii")
    script = '. agent/lib/restore/chunkgate.sh; CHUNK_DIR="$1"; chunk_stray_json "$2"'
    for state in ("ended", "listening"):
        fields = subprocess.check_output(["sh", "-c", script, "sh", tmp_path.as_posix(), state],
                                         stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         cwd=Path(__file__).resolve().parents[1], text=True)
        body = json.loads('{"pass":1' + fields + "}")
        if state == "ended":
            assert body.get("bad_chunks") == [5]
        else:
            assert "bad_chunks" not in body


@pytest.mark.parametrize("last,shared", [([5], True), ([6], False), (None, False), ([True], False)])
def test_only_matching_rejections_stop_the_wave(rig, last, shared):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac: []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    for mac, bad in zip(rig.members, ([5, 7], [5], last)):
        body = {"part": 3, "pass": 1, "state": "ended", "targets": [
            {"serial": mac, "skipped": 0, "durable": [{"part": 3, "ranges": [[0, 4], [8, 9]]}]}]}
        if bad is not None:
            body["bad_chunks"] = bad
        engine.report(SID, mac, body)
    if last == [True]:
        assert engine._wave.marks[3, "c"].bad_chunks is None
    assert wait_for(lambda: engine.status()["state"] in ("failed", "repairing"))
    if shared:
        assert engine.status()["state"] == "failed"
        assert "SOURCE_FAILURE p3/chunk5" in engine.status()["error"]
        assert not rig.named("chunk_net_gap")
        assert engine.status()["repair_rounds"] == 0
    else:
        assert engine.status()["state"] == "repairing"


def _ended(rig, mac, rnd, ranges, bad):
    rig.engine.report(SID, mac, {"part": 3, "pass": rnd, "state": "ended", "bad_chunks": bad,
                                "targets": [{"serial": mac, "durable": [{"part": 3, "ranges": ranges}]}]})


def test_shared_rejection_stops_repair_after_a_listener_leaves(rig):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac: []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    missing = [[0, 4], [6, 9]]
    for mac in ("a", "b"):
        _ended(rig, mac, 1, missing, [])
    _ended(rig, "c", 1, [[0, 9]], [])
    assert wait_for(lambda: engine.status()["state"] == "repairing")
    rig.members.remove("c")
    for mac in rig.members:
        rig.tell(mac, 2, "listening", {mac: missing})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    for mac in rig.members:
        _ended(rig, mac, 2, missing, [5])
    assert wait_for(lambda: bool(rig.named("send_failed")) or len(rig.named("chunk_repair")) > 1)
    assert "SOURCE_FAILURE p3/chunk5" in (engine.status()["error"] or ""), rig.events
    assert len(rig.named("chunk_repair")) == 1


def test_durable_chunk_cancels_same_pass_rejections(rig):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac: []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    for mac in rig.members:
        _ended(rig, mac, 1, [[0, 9]], [5])
    assert wait_for(lambda: bool(rig.named("send_done") or rig.named("send_failed")))
    assert rig.named("send_done"), rig.named("send_failed")


@requires_native("gcc", "zstd", posix=True)
@pytest.mark.parametrize("corrected", [False, True])
def test_receiver_reports_only_unresolved_rejections_at_pass_end(tmp_path, corrected):
    cc = C.build_binaries(tmp_path)
    img = C.build(tmp_path / "image", cc / "fanout", kind="raw", chunks=1, noisy=())
    broken = tmp_path / "broken.zst"
    data = img.file.read_bytes()
    broken.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    relay = C.Relay(1, lambda _r, kind, _k, _s, pkt: [] if kind == C.END else [pkt])
    disk, state, session = img.blank(tmp_path / "disk"), tmp_path / "state", C.new_session()
    recv = None
    try:
        recv = C.recv(cc, img, [disk], relay.listen(0), session, state, tag=tmp_path / "recv")
        bad = C.send(cc, img, relay.addr, session, tag=tmp_path / "bad", file=broken)
        assert bad.wait() == 0, bad.log()
        deadline = time.monotonic() + 3
        while "does not match the index" not in recv.err.read_text() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "does not match the index" in recv.err.read_text(), recv.log()
        relay.rule = lambda _r, kind, _k, _s, pkt: [pkt] if corrected or kind == C.END else []
        good = C.send(cc, img, relay.addr, session, tag=tmp_path / "good")
        assert good.wait() == 0, good.log()
        assert recv.wait() == 0, recv.log()
        out = recv.out.read_text().splitlines()
        assert C.state_of(state)[str(disk)]["durable"] == ({0} if corrected else set())
        assert [line for line in out if line.startswith("bad_chunk ")] == ([] if corrected else ["bad_chunk 0"])
        if not corrected:
            assert max(i for i, line in enumerate(out) if line.startswith("abandoned ")) < out.index("bad_chunk 0")
    finally:
        if recv is not None and recv.process.poll() is None:
            recv.process.kill()
            recv.process.wait()
        relay.close()


def test_integrity_keeps_source_reason_for_interrupted_target(conn):
    reason = "SOURCE_FAILURE p3/chunk5: all receivers rejected the chunk"
    member(conn, "current", "a", [{"dev": "sda", "serial": "A", "state": "writing"},
                                  {"dev": "sdb", "serial": "B", "state": "done", "readback": "verified"}])
    conn.execute("UPDATE sessions SET state='failed', failed_reason=? WHERE id='current'", (reason,))
    conn.execute("UPDATE session_members SET state='failed', error=? WHERE session_id='current'", (reason,))
    decisions = {d.target: d for d in integrity.record_round(conn, "current")}
    assert decisions["a/sda/A"].verdict == integrity.SOURCE_FAILURE
    assert "p3/chunk5" in decisions["a/sda/A"].reason
    assert decisions["a/sdb/B"].verdict == integrity.VERIFIED_OK


def test_integrity_keeps_an_independent_target_interruption(conn):
    reason = "SOURCE_FAILURE p3/chunk5: repeated index rejection"
    member(conn, "current", "a", [{"dev": "sda", "serial": "A", "state": "writing"},
                                  {"dev": "sdb", "serial": "B", "state": "failed",
                                   "error": "השיכפול נעצר לפני מחיצה 1"}])
    conn.execute("UPDATE sessions SET state='failed', failed_reason=? WHERE id='current'", (reason,))
    conn.execute("UPDATE session_members SET state='failed', error=? WHERE session_id='current'", (reason,))
    decisions = {d.target: d for d in integrity.record_round(conn, "current")}
    assert decisions["a/sda/A"].verdict == integrity.SOURCE_FAILURE
    assert decisions["a/sdb/B"].verdict == integrity.HALF_WRITTEN
