"""#1444 -- the classroom station and the direct send hash and count inside fanout.

The last two paths with busybox `pv | tee | sha256sum` (`restore.sh`,
`restore_partition`; `directsend.sh`, pass 2) now run
`fanout -S <sha> -P <counter> 16MiB <fifo>`, like the room round and the
capture since #1438 (busybox tee copies 1KiB per read: 195 -> 246MB/s when
replaced in the local clone; busybox sha256sum 108MB/s vs fanout 149MB/s,
cloner 2, i5-650, 15/10).

What must not move with it:

* the evidence: the sha file is the hash of the compressed bytes exactly as
  received/sent, and the counter is the pv -n format wait_progress watches;
* principle 5: `-S` is written only at a clean EOF, so "no hash" is now a
  state of its own -- it fails by name, never compares as "mismatch" and
  never passes;
* the ceilings: a fanout that dies before opening the fifo must not leave
  the reader blocked in open() until a long ceiling (#49).

#3173: the direct send's half (udp-sender's pass 2) left with the old engine.
"""

from __future__ import annotations

import time
from pathlib import Path

from native import requires_native
from test_agent import BASH, posix
from test_stream_honesty import FANOUT_WITHOUT_HASH, GOOD_SHA, PAYLOAD
from test_timeouts import log_of, make_stubs, run_sh
from agentlib import agent_sources

pytestmark = requires_native(("bash", BASH))

#: fanout that dies at once: reads nothing, opens no fifo, exits 2.
FANOUT_DIES_AT_ONCE = "#!/bin/sh\nexit 2\n"



# --- the classroom station: restore_partition ---------------------------------


def station(tmp_path, sha: str, stubs: dict, env: str = "", setup: str = "") -> tuple[Path, str, float]:
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(PAYLOAD)
    (run / "targets").mkdir(parents=True)
    started = time.monotonic()
    out = run_sh(
        make_stubs(box / "stubs", stubs)
        + f"export TMPDIR={posix(tmp_path)!r} RUN_DIR={posix(run)!r} DEVROOT={posix(box)!r} "
        "WAIT_POLL_S=1 WAIT_HELPER_S=3 WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20 "
        + env + "; "
        + agent_sources("common", "gptboot", "sysinfo", "waits", "progress", "failmark", "restore", "ntfsboot", "writeback", "drawers")   # #1487: restore_partition calls ntfs_boot_target; #1906: and restore_pipe; #2272: imagesrv-agent's order
        + f"target_init sda {len(PAYLOAD)}; "
        + setup +
        f"stream_source() {{ cat {posix(payload)!r}; }}; "
        "node_is_block() { true; }; "
        f"restore_partition unicast http://s img sda 2 dd p2.zst {sha} '' "
        '> /dev/null 2>&1; echo "rc=$?"',
        timeout=150,
    )
    return run, out, time.monotonic() - started


def counted(target: Path) -> int:
    """What target_bytes (progress.sh) reports: the sum of the per-partition
    counters `bytes.<N>`, the last numeric line of each (#1657 -- no `base`)."""
    total = 0
    for counter in sorted(target.glob("bytes.[0-9]*")):
        numbers = [ln.strip() for ln in counter.read_text().splitlines() if ln.strip().isdigit()]
        total += int(numbers[-1]) if numbers else 0
    return total


def test_the_station_hash_and_counter_are_the_files_fanout_writes(tmp_path):
    """Negative control: on the old pipeline sha.out was sha256sum's
    "<hex>  -" and bytes.raw was pv's (the stub's 65536), so both fail."""
    run, out, _ = station(tmp_path, GOOD_SHA, {})
    assert out.strip().endswith("rc=0"), out + log_of(run)
    target = run / "targets" / "sda"
    assert (target / "sha.out").read_bytes() == GOOD_SHA.encode() + b"\n"
    # The -P counter, kept as the partition's bytes.<N> when it is done (#1657).
    assert counted(target) == len(PAYLOAD)
    assert not (target / "feed").exists() and not (target / "shafifo").exists()


def test_a_station_whose_hash_was_never_written_fails_as_not_computed(tmp_path):
    """fanout delivered every byte and exited 0, but wrote no -S: no evidence,
    so the partition fails -- and says "not computed", not "mismatch" (#73).
    Negative control: the old pipeline did not run fanout, hashed with
    sha256sum, and passed."""
    run, out, _ = station(tmp_path, GOOD_SHA, {"fanout": FANOUT_WITHOUT_HASH})
    assert out.strip().endswith("rc=1"), out
    log = log_of(run)
    assert "לא חושב" in log, log
    assert "sha256 mismatch" not in log, log


def test_a_station_fanout_that_dies_before_open_fails_fast_by_its_code(tmp_path):
    """fanout exits 2 before opening the feed: zstd is blocked opening the
    fifo. The feed is unblocked as soon as fanout's rc is not 0, so the
    partition fails at once with fanout's code -- not after WAIT_DRAWER_S
    with a kill. Check ordering at the wait boundary, rather than whether a
    loaded runner finishes all setup in 40 seconds. The reader and unblock
    stand-ins rendezvous on the real FIFO; fanout still never opens it.
    wait_pid/wait_progress's clocks have their own tests in test_timeouts.
    Negative control: remove restore.sh's unblock_fifo call: wait_pid records
    wait-before-unblock, kills the blocked reader, and rc=2 is not reported."""
    setup = r'''
restore_pipe() {
    read -r released < "$1/feed"
    echo 0 > "$1/zstd.rc"; echo 0 > "$1/$4"
}
unblock_fifo() {
    printf '%s\n' "$1" > "$RUN_DIR/unblocked"
    printf 'released\n' > "$1"
}
wait_pid() {
    if [ ! -s "$RUN_DIR/unblocked" ]; then
        echo wait-before-unblock > "$RUN_DIR/bad-wait"
        kill "$1"; wait "$1"; return 1
    fi
    wait "$1"
}
wait_progress() { wait "$1"; }
'''
    run, out, _ = station(tmp_path, GOOD_SHA, {"fanout": FANOUT_DIES_AT_ONCE},
                          env="WAIT_DRAWER_S=60", setup=setup)
    assert not (run / "bad-wait").exists(), "waited for the reader before releasing it"
    assert (run / "unblocked").read_text().strip() == posix(run / "targets/sda/feed")
    assert out.strip().endswith("rc=1"), out
    # #2289: the agent's line -- restore_blame (failmark.sh) puts a stream that never
    # began on the stream; a room without failmark logged "(rc=2)" alone.
    assert "write failed (rc=2, blamed on the stream)" in log_of(run), log_of(run)


# #3173: the build machine's direct send through udp-sender (pass 2, direct_send_one) left with it. The
# chunk engine's pass hashes and counts in fanout as well (directchunk.sh): test_directchunk_agent_3161.py.
