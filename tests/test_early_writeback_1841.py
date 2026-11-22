"""#1841 -- a target that rejects its writes is failed during the partition, by
name and as ``write_error``; one that keeps them is left alone.

FI D4 (19/10, cloner 2, ``dm error`` on /dev/sda3): every write to the drawer
failed, yet partclone ran to the end of the partition -- write() lands in the
page cache and the EIO came back only at partclone's own fsync, 18.8 of 19.5 GB
later -- and the cause was ``unclassified``.

The real pipe -- ``restore_pipe`` (drawers.sh, the single restore; #3173: the
drawers' pipes of the old receiver are gone with it, chunkcast writes the
drawers and its own write errors are test_chunk_write_error_1841.py) -- with ``writeback_watch`` (writeback.sh): a stream fed slowly through the
fifo, a stand-in partclone that copies it to the node, and a stand-in ``dd``
whose fsync is refused once ``$BOX/eio`` exists -- what the kernel answers on a
partition whose writeback failed (errseq; the real thing is in the PR, a
dm-error device on the lab). Then the target's failure line as run_restore
builds it (writeback_why), classified by the server.

**Negative control** (imagesrv-agent, drawers.sh, verdict.sh, restore.sh,
smart.sh, server/disks/ata_cause.py of 9001fc91; writeback.sh removed; Testrunner
22/10): all three fail -- the EIO pipe runs to the end of the stream with rc 0
and no ``writeback.failed``; the clean target and the killed pipe were never
watched (no fsync at all).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from native import requires_native
from server.disks import ata_cause
from test_agent import BASH, posix
from agentlib import agent_sources

pytestmark = requires_native(("bash", BASH), posix=True, why="fifos, #! stand-ins and kill -9")

#: seconds the stream takes to arrive -- the fsync interval is 1 s
STREAM_S = 8

STANDINS = {
    # zstd -dc: the feed as it is
    "zstd": '#!/bin/sh\nexec cat\n',
    # partclone.ntfs -r -s - -O <node>: the stream onto the node
    "partclone.ntfs": '#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = -O ] && o=$2; shift; done\ncat > "$o"\n',
    # the fsync of the watcher: refused once the disk "rejects its writes"
    "dd": ('#!/bin/sh\necho "dd $*" >> "$BOX/dd.calls"\n'
           'if [ -e "$BOX/eio" ]; then echo "dd: fsync failed for \'$BOX/node\': Input/output error" >&2; exit 1; fi\n'
           'exit 0\n'),
}


def _pipe(tmp_path: Path, *, eio_after_s: float | None) -> tuple[str, Path, float]:
    """``restore_pipe`` (#3173: the drawers' own pipes of the old receiver are gone with it)."""
    box = tmp_path / "box"
    (box / "bin").mkdir(parents=True)
    run, t = box / "run", box / "run" / "targets" / "sdb"
    t.mkdir(parents=True)
    for name, body in STANDINS.items():
        p = box / "bin" / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    (box / "node").write_bytes(b"")
    node = posix(box / "node")
    call = f'restore_pipe "$T" partclone.ntfs {node!r} pipeline.rc; '
    script = (
        f'export PATH={posix(box / "bin")!r}:"$PATH" BOX={posix(box)!r} RUN_DIR={posix(run)!r} '
        'WRITE_FSYNC_INTERVAL_S=1; '
        + agent_sources("common", "restore", "writeback", "drawers", "verdict")   # #2272: imagesrv-agent's order
        + f'T={posix(t)!r}; mkfifo "$T/feed"; '
        f'( i=0; while [ "$i" -lt {STREAM_S * 4} ]; do head -c 4096 /dev/zero; sleep 0.25; i=$((i + 1)); done ) > "$T/feed" & '
        + call
        + 'echo "RC $(cat "$T/pipeline.rc")"; '
        'echo "WB $(cat "$T/writeback.failed" 2>/dev/null)"; '
        # the reason run_restore puts on the target (restore.sh: "N of M partitions written" + writeback_why)
        'echo "ERR written 0 of 1$(writeback_why sdb)"; '
        'kill $(jobs -p) 2>/dev/null; wait'
    )
    proc = subprocess.Popen([BASH, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, cwd=str(tmp_path))
    t0 = time.monotonic()
    if eio_after_s is not None:
        time.sleep(eio_after_s)
        (box / "eio").write_text("")
    out, _ = proc.communicate(timeout=60)
    return out, box, time.monotonic() - t0


def _line(out: str, tag: str) -> str:
    return next((ln[len(tag) + 1:] for ln in out.splitlines() if ln.startswith(tag + " ")), "")


def test_a_target_that_rejects_its_writes_fails_mid_partition_as_write_error(tmp_path):
    out, box, took = _pipe(tmp_path, eio_after_s=1.5)
    assert _line(out, "RC") == "1", out
    assert _line(out, "WB").startswith("periodic fsync failed (write_error)"), out
    assert "Input/output error" in _line(out, "WB")                  # the kernel's word, kept
    assert took < STREAM_S - 2, f"the pipe ran {took:.1f}s -- to the end of the stream, not cut at the EIO"
    error = _line(out, "ERR")
    assert "periodic fsync failed (write_error)" in error, out
    assert ata_cause.classify([], error) == "write_error"


def test_a_target_that_keeps_its_writes_is_written_to_the_end(tmp_path):
    out, box, took = _pipe(tmp_path, eio_after_s=None)
    assert _line(out, "RC") == "0", out
    assert _line(out, "WB") == "", out
    assert took >= STREAM_S - 1, took                                   # the whole stream went through
    assert (box / "node").stat().st_size == STREAM_S * 4 * 4096
    calls = (box / "dd.calls").read_text().splitlines()
    assert len(calls) >= STREAM_S - 2, calls                              # it was watched all along
    assert all("conv=notrunc,fsync" in c and posix(box / "node") in c for c in calls), calls


def test_the_watcher_leaves_with_a_pipe_that_was_killed(tmp_path):
    """A drawer's pipe killed on its ceiling (drawers.sh wait_pid) must not leave
    a watcher fsyncing that drawer -- and killing a stale zstd pid -- to the next boot."""
    box = tmp_path / "box"
    (box / "bin").mkdir(parents=True)
    t = box / "run" / "targets" / "sdb"
    t.mkdir(parents=True)
    for name, body in STANDINS.items():
        p = box / "bin" / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    (box / "node").write_bytes(b"")
    script = (
        f'export PATH={posix(box / "bin")!r}:"$PATH" BOX={posix(box)!r} RUN_DIR={posix(box / "run")!r} '
        'WRITE_FSYNC_INTERVAL_S=0.3; '
        + agent_sources("common", "restore", "writeback", "drawers")   # #2272: imagesrv-agent's order
        + f'T={posix(t)!r}; mkfifo "$T/feed"; '
        # every process here is stopped by its pid, and none holds this test's pipe
        '( exec 3> "$T/feed"; exec sleep 30 ) > /dev/null 2>&1 & feeder=$!; '
        f'( restore_pipe "$T" partclone.ntfs {posix(box / "node")!r} pipeline.rc ) > /dev/null 2>&1 & pipe=$!; '
        'sleep 1.5; kill -9 "$pipe"; sleep 2; n1=$(wc -l < "$BOX/dd.calls"); sleep 2; '
        'n2=$(wc -l < "$BOX/dd.calls"); echo "CALLS $n1 $n2"; '
        'kill "$(cat "$T/reader.pid")" "$feeder" 2>/dev/null; true'
    )
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL, cwd=str(tmp_path)).stdout
    n1, n2 = (int(x) for x in _line(out, "CALLS").split())
    assert n1 >= 2, out                       # it watched while the pipe lived
    assert n2 == n1, f"the watcher outlived its pipe: {n1} -> {n2} fsyncs"
