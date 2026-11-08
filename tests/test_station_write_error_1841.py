"""#1841 -- the single station's write error is told to the server as ``write_error``.

PR #1986 cut a target that rejects its writes during the partition
(``writeback_watch``, writeback.sh) and made the *drawers'* failure line carry
``periodic fsync failed (write_error)`` (verdict.sh), which ``ata_cause.classify``
reads. The station (a classroom machine, the GUI restore, the unicast pull) goes
through the same ``restore_pipe`` -- but ``run_restore`` then failed the target with
"N of M partitions written", which overwrites the reason, and the server filed it
as ``unclassified`` (the 25/10 comment on the Issue: "the cause is not write_error").

This is the real ``run_restore`` with the real ``fanout`` (gcc): a stream fed
slowly (8 s), a stand-in partclone that copies it to the node, and a stand-in
``dd`` whose fsync is refused from the moment the source drops ``$BOX/eio`` -- what
the kernel answers on a partition whose writeback failed (the real one: a dm-error
device, on metal). The reason and the kernel lines the agent would send are then
classified by the server's own ``ata_cause``.

**Negative control** -- NOT RUN yet (written on a Windows workstation: no gcc, no fifos).
To run on the Testrunner: restore ``agent/lib/restore/restore.sh`` and
``agent/lib/restore/writeback.sh`` to ``origin/main`` (both, without ``writeback_why``;
``grep -c writeback_why`` must print 0 in each); expected: the EIO test fails on the cause
(``unclassified``) and the other three pass.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from native import requires_native
from server.disks import ata_cause
from test_agent import posix
from test_stream_identity_1601_1602 import PRE_STREAM, fanout_dir, sysfs_disk, target  # noqa: F401
from test_timeouts import log_of, make_stubs, run_sh
from agentlib import agent_sources

pytestmark = requires_native("gcc", posix=True, why="the real fanout needs gcc; fifos and a sysfs of symlinks")

#: the stream arrives in 32 pieces of 4 KiB, a quarter of a second apart; the fsync interval is 1 s
TICKS = 32
PAYLOAD_SHA = hashlib.sha256(b"\0" * (TICKS * 4096)).hexdigest()
PLAN = f"3|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|data|dd|2048|{TICKS * 4096}|p3.data.dd.zst|{PAYLOAD_SHA}|false|||\n"

#: partclone.dd -r -s - -O <node>: the stream onto the node. fails_at_end: all of it, then exit 1.
PARTCLONE = r"""#!/bin/sh
node=
while [ $# -gt 0 ]; do case "$1" in -O) node=$2; shift 2 ;; *) shift ;; esac; done
cat > "$node"
[ "$PCL" = ok ] || exit 1
"""

#: the watcher's fsync: refused once the disk "rejects its writes"
DD = ('#!/bin/sh\necho "dd $*" >> "$BOX/dd.calls"\n'
      'if [ -e "$BOX/eio" ]; then echo "dd: fsync failed for \'$2\': Input/output error" >&2; exit 1; fi\n'
      'exit 0\n')

STUBS = {
    "zstd": "#!/bin/sh\nexec cat\n",
    "partclone.dd": PARTCLONE,
    "dd": DD,
    "dmesg": '#!/bin/sh\ncat "$BOX/kmsg"\n',
    "sgdisk": "#!/bin/sh\nexit 0\n",
    "blockdev": "#!/bin/sh\nexit 0\n",
    "fanout": None,   # the real one
}

#: what the kernel prints for a write that failed on sda (the line ata_capture keeps for the report)
KMSG = ("[  150.000000] blk_update_request: I/O error, dev sda, sector 4096 op 0x1:(WRITE) "
        "flags 0x4000 phys_seg 1 prio class 2\n")


def station(tmp_path: Path, fanout_dir: Path, *, eio_at_tick: int | None, pcl: str = "ok") -> tuple[Path, Path, str]:
    """``run_restore`` on a station whose one partition arrives over 8 s. The source
    reports every piece it managed to hand on (``$BOX/sent``) and drops ``$BOX/eio``
    at piece ``eio_at_tick``."""
    box = tmp_path / "box"
    run = box / "run"
    (run / "targets").mkdir(parents=True)
    (box / "dev").mkdir()
    (box / "proc").mkdir()
    (box / "proc" / "uptime").write_text("100.00 50.00\n")
    sysfs_disk(box, "sda", "SER-A", 1)
    (box / "kmsg").write_text(KMSG)
    (box / "plan").write_text(PLAN)
    eio = f'[ "$i" -ne {eio_at_tick} ] || : > "$BOX/eio"; ' if eio_at_tick is not None else ""
    source = (f'i=0; while [ "$i" -lt {TICKS} ]; do {eio}head -c 4096 /dev/zero || break; '
              'echo x >> "$BOX/sent"; sleep 0.25; i=$((i + 1)); done')
    env = (f"export BOX={posix(box)!r} PCL={pcl} SYSROOT={posix(box)!r} RUN_DIR={posix(run)!r} "
           f"DEVROOT={posix(box / 'dev')!r} TMPDIR={posix(tmp_path)!r} WRITE_FSYNC_INTERVAL_S=1 "
           "WAIT_POLL_S=1 WAIT_DRAWER_S=6 WAIT_HELPER_S=4 WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=10; ")
    libs = agent_sources("common", "jsonq", "sysinfo", "diskinfo", "waits", "progress", "restore", "verdict", "failmark",
                         "writeback", "drawers")   # imagesrv-agent's order (#2272)
    out = run_sh(
        make_stubs(box / "stubs", STUBS)
        + f'export PATH="{posix(fanout_dir)}:$PATH"\n'
        + env + libs + PRE_STREAM
        + f"stream_source() {{ {source}; }}; "
        + "target_init sda 1; "
        + "run_restore unicast sda http://s img m.json > /dev/null 2>&1; " 'echo "rc=$?"; '
        f'pkill -9 -f {posix(box)!r} 2>/dev/null; :',
        timeout=90,
    )
    return run, box, out


def pieces_sent(box: Path) -> int:
    f = box / "sent"
    return len(f.read_text().split()) if f.exists() else 0


def cause_of(run: Path) -> str:
    """What the server makes of this target: the error line and the kernel lines of the report."""
    log = run / "targets" / "sda" / "ata_log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return ata_cause.classify(lines, target(run, "sda", "error"))


def test_a_station_whose_disk_rejects_its_writes_is_classified_write_error(tmp_path, fanout_dir):
    run, box, out = station(tmp_path, fanout_dir, eio_at_tick=6)
    log = log_of(run)
    assert out.strip().endswith("rc=1"), out + log
    assert target(run, "sda", "state") == "failed", log
    error = target(run, "sda", "error")
    assert "נכתבו 0 מתוך 1 מחיצות" in error                      # the run's own verdict stays
    assert "periodic fsync failed (write_error)" in error, error
    assert "Input/output error" in error                           # the kernel's word, kept
    assert (run / "targets" / "sda" / "ata_log.json").exists(), log   # the disk's evidence is kept: not the stream's fault
    assert cause_of(run) == "write_error", error
    assert pieces_sent(box) < TICKS - 6, f"{pieces_sent(box)} of {TICKS} pieces went through -- cut at the EIO, not at the end"


def test_without_the_reason_the_same_failure_is_unclassified():
    """What the server saw before: the run's verdict alone, over the kernel's lines. The guard
    that the test above reads the reason, and not the I/O error line beside it."""
    lines = KMSG.splitlines()
    assert ata_cause.classify(lines, "נכתבו 0 מתוך 1 מחיצות") == "unclassified"


def test_a_write_that_failed_for_another_reason_is_not_called_write_error(tmp_path, fanout_dir):
    """partclone exits 1 after taking everything, the disk never refused an fsync."""
    run, box, out = station(tmp_path, fanout_dir, eio_at_tick=None, pcl="fails_at_end")
    log = log_of(run)
    assert out.strip().endswith("rc=1"), out + log
    assert "נכתבו 0 מתוך 1 מחיצות" in target(run, "sda", "error"), log
    assert "write_error" not in target(run, "sda", "error")
    assert cause_of(run) != "write_error"
    assert not (run / "targets" / "sda" / "writeback.failed").exists()


def test_a_station_that_keeps_its_writes_is_written_to_the_end(tmp_path, fanout_dir):
    run, box, out = station(tmp_path, fanout_dir, eio_at_tick=None)
    assert out.strip().endswith("rc=0"), out + log_of(run)
    assert target(run, "sda", "error") == ""
    assert not (run / "targets" / "sda" / "writeback.failed").exists()
    assert pieces_sent(box) == TICKS                               # the whole stream went through
    calls = (box / "dd.calls").read_text().splitlines()
    assert len(calls) >= 4, calls                                  # it was watched all along

