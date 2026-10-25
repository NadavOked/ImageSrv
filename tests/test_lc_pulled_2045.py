"""#2045 -- a drawer pulled in the middle of a LOCAL clone is out of the stream in seconds.

Metal D2 (23/10, cloner 2): disk 2 pulled at 26% of a local clone to three
targets. The kernel dropped it at once (``ata2.00: detaching``, ``/sys/block/sda``
gone), but:

1. the drawer stayed blue "writing 47%" for ~5 minutes -- until its partition ended;
2. its reason was fanout's ``stalled the stream (too slow to drain)`` and a
   partclone line -- Hebrew and English mixed, and not "pulled";
3. the shared stream stood ~60 s: the relay's reader had stopped, and fanout only
   lets go of a drawer that holds stdin after ``BLOCK_EVICT_MS`` (45 s, #1796).

The round's path has had the kernel watcher since #1598 (``drawer_watch``,
verdict.sh): positive evidence from the kernel -- the disk left sysfs -- kills
the fifo's reader, fanout's next write is EPIPE, and the drawer fails by name.
The local clone's ``lc_stream`` (lccopy.sh) never ran it. These tests run the
real ``lc_stream`` with the **real fanout** (compiled from ``agent/fanout.c``),
partclone stubbed and sysfs under ``SYSROOT``.

The guard against #660 holds: a drawer that only goes quiet, with no word from
the kernel, is not declared pulled.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_local_clone_agent import posix, run

REPO = Path(__file__).resolve().parent.parent
pytestmark = requires_native("gcc", posix=True, why="fanout האמיתי צריך gcc ו-fifo")

#: 64MB: far more than the pulled drawer swallows before it blocks (its 2MB, the
#: relay's 16MB ring and 1MB pipe, the feed's pipe, the 2MB ring of the stream's
#: fanout) -- so without the watcher the stream really stands.
PAYLOAD = hashlib.sha256(b"2045").digest() * (64 * 1024 * 1024 // 32)

#: partclone stubbed. As the source (-s <node>): reads it. As a target (-s -):
#: sda writes everything; sdb takes 2MB, "is pulled" by $DEATH, and stops
#: reading without exiting -- partclone stuck on a disk that is gone.
PARTCLONE = r'''#!/bin/sh
src=; out=
while [ $# -gt 0 ]; do case "$1" in -s) src=$2; shift 2 ;; -O) out=$2; shift 2 ;; *) shift ;; esac; done
[ "$src" = - ] || exec cat "$src"
case "$out" in
  */sdb1)
    head -c 2097152 > /dev/null
    date +%s > "$STUB_BOX/died"
    case "$DEATH" in
      vanish) rm -rf "$SYSROOT/sys/block/sdb" ;;
      quiet)  : ;;
    esac
    echo $$ >> "$STUB_BOX/pids"
    exec sleep 60 ;;
  *) exec cat > "$out" ;;
esac
'''

STUBS = {
    "partclone.dd": PARTCLONE,
    "dmesg": '#!/bin/sh\ncat "$STUB_BOX/kmsg"\n',
}


@pytest.fixture(scope="module")
def fanout_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fanout-bin")
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out / "fanout"),
                    str(REPO / "agent" / "fanout.c")], check=True, stdin=subprocess.DEVNULL)
    return out


def copy(tmp_path: Path, fanout_dir: Path, death: str, **env) -> tuple[dict, Path]:
    box = tmp_path / "box"; stubs = box / "stubs"; dev = box / "dev"
    dev.mkdir(parents=True)
    (box / "proc").mkdir()
    (box / "proc" / "uptime").write_text("100.00 50.00\n")
    for d in ("sda", "sdb"):
        sysd = box / "sys" / "block" / d / "device"
        sysd.mkdir(parents=True)
        (sysd / "state").write_text("running\n")
    (box / "kmsg").write_text("")
    (box / "pids").write_text("")
    (dev / "sdc1").write_bytes(PAYLOAD)
    make = [f"mkdir -p {posix(stubs)!r}"]
    for name, body in STUBS.items():   # from bash: a Windows-made file is not executable in Git Bash
        make += [f"cat > {posix(stubs)}/{name} <<'STUB'\n{body}STUB", f"chmod 0755 {posix(stubs)}/{name}"]
    t = "\n".join(make) + "\n"
    t += (f'export PATH="$(cd {posix(stubs)!r} && pwd):{posix(fanout_dir)}:$PATH" STUB_BOX={posix(box)!r} '
          f'DEVROOT={posix(dev)!r} SYSROOT={posix(box)!r} DEATH={death}; ')
    t += 'mkdir -p "$LC/run"; ata_capture() { :; }; target_init sda 100; target_init sdb 100; '
    t += 'printf "sda\\nsdb\\n" > "$LC/run/targets"; '
    t += 't0=$(date +%s); lc_stream 1 dd sdc sda sdb > "$STUB_BOX/stream.log" 2>&1; echo "rc=$?"; '
    t += 'echo "took=$(( $(date +%s) - t0 ))"; '
    t += 'for d in sda sdb; do echo "$d=$(cat $RUN_DIR/targets/$d/state)|$(cat $RUN_DIR/targets/$d/lc_ok 2>/dev/null)|$(cat $RUN_DIR/targets/$d/error 2>/dev/null)"; '
    t += 'echo "$d.marked=$(date -r "$RUN_DIR/targets/$d/error" +%s 2>/dev/null)"; '
    t += 'echo "$d.dead=$(cat "$RUN_DIR/targets/$d/dead" 2>/dev/null)"; done; '
    t += 'lc_gui_run | grep "^lc_tgt="; '
    t += f'for p in $(cat {posix(box / "pids")!r}); do kill -9 "$p" 2>/dev/null; done; :'
    started = time.monotonic()
    out = run(tmp_path, t, timeout=150, WAIT_HELPER_S="5", WAIT_DRAWER_S="20", WAIT_POLL_S="1",
              FANOUT_BUFFER="2097152", **env)
    assert out.returncode == 0, out.stderr
    got: dict = {"wall": time.monotonic() - started, "out": out.stdout}
    for line in out.stdout.splitlines():
        if line.startswith("lc_tgt="):
            fields = line[len("lc_tgt="):].split("|")
            got["tgt." + fields[0]] = fields
        elif "=" in line:
            k, v = line.split("=", 1)
            got[k] = v
    return got, box


def test_a_pulled_drawer_is_out_in_seconds_in_hebrew_and_the_other_gets_every_byte(tmp_path, fanout_dir):
    """D2 on metal. Before the fix: sdb holds the stream for BLOCK_EVICT_MS (45 s),
    fails only when the partition ends, as "stalled the stream (too slow to drain)"."""
    got, box = copy(tmp_path, fanout_dir, "vanish")
    log = got["out"] + (box / "stream.log").read_text(encoding="utf-8", errors="replace")
    assert got["rc"] == "0", log
    # sda -- the healthy one -- took every byte (principle 4: not "almost").
    assert got["sda"] == "waiting|1|", log
    assert (box / "dev" / "sda1").read_bytes() == PAYLOAD
    # sdb -- pulled -- failed in the open, as pulled, within seconds of the pull.
    state, _, why = got["sdb"].split("|", 2)
    assert state == "failed", log
    assert "נשלף" in why, why
    assert not re.search("[A-Za-z]", why), f"English on the operator's screen: {why!r}"   # Hebrew only
    assert got["sdb.dead"], "the watcher did not mark the pulled drawer"
    died = int((box / "died").read_text().strip())
    assert int(got["sdb.marked"]) - died <= 6, (died, got["sdb.marked"])
    # the stream did not wait for it: far under fanout's 45 s hold
    assert int(got["took"]) < 25, got["took"]
    # the screen's record says "pulled" for sdb only (lc_tgt's 12th field)
    assert got["tgt.sdb"][11] == "1" and got["tgt.sda"][11] == "0", (got["tgt.sda"], got["tgt.sdb"])


def test_a_drawer_that_only_goes_quiet_is_not_called_pulled(tmp_path, fanout_dir):
    """The guard of #660: silence with no word from the kernel is not a pull. sdb
    stops reading, sysfs still has it: fanout's own ceiling (here 3 s) takes it out,
    nobody marks it dead, and its screen record does not say "pulled"."""
    got, box = copy(tmp_path, fanout_dir, "quiet", FANOUT_BLOCK_EVICT_MS="3000")
    log = got["out"] + (box / "stream.log").read_text(encoding="utf-8", errors="replace")
    assert got["rc"] == "0", log
    assert got["sda"] == "waiting|1|", log
    assert got["sdb"].startswith("failed|"), log
    assert "נשלף" not in got["sdb"], got["sdb"]
    assert not got["sdb.dead"], got["sdb.dead"]
    assert got["tgt.sdb"][11] == "0", got["tgt.sdb"]
