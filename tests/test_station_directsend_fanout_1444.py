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
  the reader blocked in open() until a long ceiling (#49), and on the direct
  send fanout's own 150s stall eviction must not fire while udp-sender is
  still (legitimately) waiting for its receivers (#957: up to 780s).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_capture_refusals import GPT_DISK, ONE_PARTITION
from test_directsend import CURL_RECORD, JQ_STUB, MAC, PARTCLONE, TASK, UDP_SENDER, _answer
from test_stream_honesty import FANOUT_WITHOUT_HASH, GOOD_SHA, PAYLOAD
from test_timeouts import STUBS, log_of, make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

#: fanout that dies at once: reads nothing, opens no fifo, exits 2.
FANOUT_DIES_AT_ONCE = "#!/bin/sh\nexit 2\n"



def on_the_wire(body: str) -> str:
    """A fanout stub that is the good one for pass 1 (the capture into a bit
    bucket runs fanout too) and `body` only for pass 2, whose output is the
    dfeed.<idx>.fifo udp-sender reads. FANOUT_OPTIONS has already stripped
    -S/-P, so "$2" is the output."""
    good = STUBS["fanout"].replace("#!/bin/sh\n", "", 1)
    wire = body.replace("#!/bin/sh\n", "", 1)
    return ("#!/bin/sh\n"
            'case "$2" in *dfeed.*) ;; *)\n' + good + "exit 0 ;;\nesac\n" + wire)


#: The good fanout, recording both eviction ceilings it got on the wire (#674, #1796).
FANOUT_RECORDS_EVICTION = on_the_wire(
    'echo "${FANOUT_STALL_EVICT_MS:-unset} ${FANOUT_BLOCK_EVICT_MS:-unset}" >> "$EVICT_LOG"\n'
    + STUBS["fanout"])

DIRECT_BYTES = b"A" * 4096          # PARTCLONE's 4096 bytes; zstd is `exec cat`


# --- the classroom station: restore_partition ---------------------------------


def station(tmp_path, sha: str, stubs: dict, env: str = "") -> tuple[Path, str, float]:
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
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        f". {posix(AGENT)}/lib/ntfsboot.sh; . {posix(AGENT)}/lib/drawers.sh; "   # #1487: restore_partition calls ntfs_boot_target; #1906: and restore_pipe
        f"target_init sda {len(PAYLOAD)}; "
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
    with a kill. Negative control: without the unblock_fifo line the writer
    waits the full 60s ceiling."""
    run, out, elapsed = station(tmp_path, GOOD_SHA, {"fanout": FANOUT_DIES_AT_ONCE},
                                env="WAIT_DRAWER_S=60")
    assert out.strip().endswith("rc=1"), out
    assert "write failed (rc=2)" in log_of(run), log_of(run)
    assert elapsed < 40, elapsed


# --- the build machine's direct send: direct_send_one -------------------------


def direct(tmp_path, *, fanout: str | None = None, env: dict | None = None,
           timeout: int = 150) -> tuple[Path, Path, str, float]:
    """test_directsend.direct_run, with the fanout stub replaceable."""
    box = tmp_path / "box"
    dev, run = box / "dev", box / "run"
    dev.mkdir(parents=True); run.mkdir(parents=True)
    (dev / "sda").write_bytes(GPT_DISK)
    (dev / "sda1").write_bytes(bytes(512))
    queue = box / "sys/block/sda/queue"; queue.mkdir(parents=True)
    (queue / "logical_block_size").write_text("512\n")
    nodes = box / "nodes"
    nodes.write_text(f"{posix(dev)}/sda\n{posix(dev)}/sda1\n", newline="\n")
    (box / "answer.json").write_text(json.dumps(_answer()), encoding="utf-8")
    envs = {"RUN_DIR": posix(run), "DEVROOT": posix(dev), "SYSROOT": posix(box),
            "SERVER": "http://s", "IFACE": "eth0", "MAC": MAC, "TASK_TOKEN": "t" * 48,
            "RESP": posix(box / "resp.json"), "IMAGECTL_TEST": "1",
            "PCL_COUNT": posix(box / "pcl.count"), "PCL_BYTE": "A", "PCL_SECOND": "",
            "UDP_ARGV": posix(box / "udp.argv"), "CURL_ARGV": posix(box / "curl.argv"),
            "UDP_SILENT": "", "DIRECT_POLL_S": "0", "EVICT_LOG": posix(box / "evict.log"),
            **(env or {})}
    exports = "".join(f"export {k}={v!r}; " for k, v in envs.items())
    stubs = {"sgdisk": ONE_PARTITION, "partclone.dd": PARTCLONE,
             "udp-sender": UDP_SENDER, "curl": CURL_RECORD}
    if fanout is not None:
        stubs["fanout"] = fanout
    if shutil.which("jq") is None:
        stubs["jq"] = JQ_STUB
    if shutil.which("blkid") is None:   # #1905: a missing blkid is "unreadable"; the real one says rc 2 on these files
        stubs.setdefault("blkid", "#!/bin/sh\nexit 2\n")
    libs =" ".join(f". {posix(AGENT)}/lib/{n}.sh;" for n in (
        "common", "sysinfo", "waits", "jsonq", "progress", "restore", "manifest",
        "bootca", "hibernation", "shrinkplan", "shrink", "lcsnap", "writemark", "diskfit", "capturegate", "capture", "capturesink",
        "directsend", "directrecv", "ui"))   # #1466: lcsnap.sh -- capture_disk reads through lc_src_node; #1901/#1808: the source gates
    started = time.monotonic()
    out = run_sh(
        make_stubs(box / "stubs", stubs) + exports + libs
        + f' node_is_block() {{ grep -qxF "$1" {posix(nodes)!r} 2>/dev/null; }}; '
        "raid_member_reason() { :; }; "   # #1808: tested in test_capture_gates.py; this disk is a file
        f'send_hello() {{ cp {posix(box / "answer.json")!r} "$RESP"; }}; '
        f"direct_send_run {TASK} sda > {posix(box)}/direct.out 2>&1; "
        'echo "rc=$?"; '
        f"build_progress '' {MAC} {TASK} > {posix(box)}/progress.json",
        timeout=timeout,
    )
    return box, run, out, time.monotonic() - started


def _error(box: Path) -> str:
    return json.loads((box / "progress.json").read_text(encoding="utf-8"))["targets"][0]["error"]


def test_the_wire_hash_and_counter_are_the_files_fanout_writes(tmp_path):
    """Negative control: the old pass 2 wrote "<hex>  -" (sha256sum) and
    pv's count (the stub's 65536)."""
    box, run, out, _ = direct(tmp_path)
    assert out.strip().endswith("rc=0"), out + log_of(run)
    assert (run / "dsha.1").read_bytes() == \
        hashlib.sha256(DIRECT_BYTES).hexdigest().encode() + b"\n"
    # The -P counter, kept as the partition's bytes.<N> when it is done (#1657).
    assert counted(run / "targets" / "sda") == len(DIRECT_BYTES)


def test_a_wire_hash_that_was_never_written_fails_the_send_by_name(tmp_path):
    """Every byte went out and fanout exited 0, but there is no -S: no
    evidence the stream matched the manifest. Negative control: the old
    pass 2 never ran fanout and returned rc=0."""
    box, run, out, _ = direct(tmp_path, fanout=on_the_wire(FANOUT_WITHOUT_HASH))
    assert out.strip().endswith("rc=1"), out
    assert "ה-sha256 של מה שנשלח לא חושב" in _error(box)   # #1503: Hebrew on the screen


def test_fanout_eviction_is_pushed_past_the_shell_ceiling(tmp_path):
    """udp-sender reads nothing until its receivers join -- up to 780s on a
    later partition (#957). fanout's own default (150s, #674) would evict
    that healthy reader; the shell's wait_progress (start 600 + stall 300 by
    default) must stay the only ceiling, as it was with tee. The same holds
    for the held-stdin clock (45s, #1796): that wait IS a held stdin. Negative
    control: unset/150000/45000 fails the bound."""
    box, run, out, _ = direct(tmp_path, fanout=FANOUT_RECORDS_EVICTION)
    assert out.strip().endswith("rc=0"), out + log_of(run)
    log = box / "evict.log"
    assert log.exists(), "fanout never ran on the wire (pass 2)"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, lines
    recorded = lines[0].split()
    assert len(recorded) == 2, recorded
    for ceiling in recorded:
        assert ceiling.isdigit() and int(ceiling) >= (600 + 300) * 1000, recorded


def test_a_send_whose_fanout_dies_before_open_fails_fast(tmp_path):
    """udp-sender's shell is blocked opening the fifo when fanout exits 2
    without opening it. It is released at once, and the send fails naming a
    stage -- not after the 60s WAIT_DRAWER_S kill. Negative control: without
    the unblock_fifo line the run takes the full ceiling."""
    box, run, out, elapsed = direct(
        tmp_path, fanout=on_the_wire(FANOUT_DIES_AT_ONCE),
        env={"WAIT_DRAWER_S": "60", "WAIT_HELPER_S": "5", "WAIT_POLL_S": "1",
             "WAIT_STREAM_START_S": "60", "WAIT_STREAM_STALL_S": "60"})
    assert out.strip().endswith("rc=1"), out
    error = _error(box)
    assert "השליחה נכשלה" in error and "שלב=dfanout" in error, error   # #1503
    assert elapsed < 40, elapsed
