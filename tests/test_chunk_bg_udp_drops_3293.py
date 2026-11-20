"""#3293 -- a drawer completed in the background does not write the round's `udp_drops.json`.

The completion of a drawer that came back (drawerback.sh, prodline.sh) runs `_chunk_run` in a subshell with
`CHUNK_BG=1` and no `CHUNK_TELL`. `_chunk_run` (agent/lib/restore/chunkrecv.sh) told the server's loss report once a
second for anything without `CHUNK_TELL` -- so the completion rewrote the ROUND's file from its own `chunkcast apply`
output (no `abandoned`, no `rcvbuf` -> null) and the report to the server flipped between the two writers.

Proved here with a real shell and the real chunkloss.sh: while the round's receiver is between partitions, a
background completion runs for a while; the round's file is byte for byte what the round wrote, valid JSON, and
nothing of the round's counters (`udp_drops.sum`, `.abandoned`, `.rcvbuf`) moved. The same run WITHOUT `CHUNK_BG`
(a receiver) does rewrite the file -- so the test can see a write when there is one.

Negative control: chunkrecv.sh line `[ -n "${CHUNK_TELL:-}${CHUNK_BG:-}" ] || chunk_loss_tell` back to
`[ -n "${CHUNK_TELL:-}" ] || ...` -- the background run overwrites the file and the first test fails.
"""

from __future__ import annotations

import json
import subprocess

from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
pytestmark = requires_native(("bash", BASH), why="the agent's shell, and a real sleep")

ROUND = '{"rcvbuf":7,"inerrors":2,"abandoned":3,"rcvbuf_bytes":425984,"pass_word":"complete"}\n'

SCRIPT = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log" SYSROOT="$1/sysroot"; mkdir -p "$RUN_DIR/chunk" "$SYSROOT/proc/net"
. agent/lib/core/common.sh; . agent/lib/core/jsonq.sh; . agent/lib/core/waits.sh
. agent/lib/restore/chunkledger.sh; . agent/lib/restore/chunkloss.sh; . agent/lib/restore/chunkrecv.sh
printf 'Udp: InDatagrams NoPorts InErrors OutDatagrams RcvbufErrors\nUdp: 1 0 2 1 7\n' > "$SYSROOT/proc/net/snmp"
CHUNK_STATE="$RUN_DIR/chunk/p3.state"; CHUNK_OUT="$RUN_DIR/chunk/apply.out"; : > "$CHUNK_OUT"   # apply's output: no "abandoned", no "rcvbuf"
STREAM_START_CEILING=100; WAIT_STREAM_STALL_S=100; WAIT_DRAWER_S=100
chunk_watch_start() { :; }; chunk_watch_end() { :; }; _chunk_counters() { :; }; round_closed() { return 1; }
printf '%s' "$ROUND_JSON" > "$RUN_DIR/udp_drops.json"; echo "7 2" > "$RUN_DIR/udp_drops.sum"; echo 3 > "$RUN_DIR/udp_drops.abandoned"; echo 425984 > "$RUN_DIR/udp_drops.rcvbuf"
chunk_loss_mark
( command sleep 1.5 ) & _pid=$!
if [ -n "$BG" ]; then CHUNK_BG=1; _chunk_run "the completion" "$_pid" 3 sda; else _chunk_run "the receiver" "$_pid" 3 sda; fi
echo "RC=$?"
'''


def _run(tmp_path, bg: bool) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "run.sh").write_text(SCRIPT, encoding="utf-8", newline="\n")
    cmd = f"BG={'1' if bg else ''} ROUND_JSON='{ROUND}' . {posix(tmp_path / 'run.sh')!r} {posix(tmp_path)!r}"
    proc = subprocess.run([BASH, "-c", cmd], capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
                          stdin=subprocess.DEVNULL)
    assert "RC=0" in proc.stdout, proc.stdout + proc.stderr
    return (tmp_path / "udp_drops.json").read_text(encoding="utf-8")


def test_a_background_completion_leaves_the_rounds_report_alone(tmp_path):
    got = _run(tmp_path, bg=True)
    assert got == ROUND, got
    assert json.loads(got)["abandoned"] == 3
    assert (tmp_path / "udp_drops.sum").read_text().strip() == "7 2"
    assert (tmp_path / "udp_drops.abandoned").read_text().strip() == "3"
    assert (tmp_path / "udp_drops.rcvbuf").read_text().strip() == "425984"


def test_a_receiver_does_rewrite_it(tmp_path):
    """The control of the test above: without CHUNK_BG the same run writes the file (null where apply said nothing)."""
    got = _run(tmp_path, bg=False)
    assert got != ROUND, got
    assert json.loads(got)["rcvbuf_bytes"] is None
