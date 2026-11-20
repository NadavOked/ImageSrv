"""#3260 -- the receiver's end is seen at once, not up to a second later.

`_chunk_run` (agent/lib/restore/chunkrecv.sh) polled the receiver with a whole `sleep 1`: every pass paid, on
average, half a second between the receiver's last word and the next step -- on every partition. The old receiver
had the same wait and #1705 (metal 25/10) replaced it with `poll_tick` (0.1 s); `_nap` (waits.sh) is that, and
chunkwatch.sh already uses it for the same process.

What is proved here is the LATENCY from the receiver's end to `_chunk_run`'s return, with a real shell and a real
sleep, and that what is counted in seconds (the pass's silence ceiling) still counts seconds.
The second `sleep` of the file (between two questions to the server, CHUNK_ASK_POLL_S) stays and is not tested away:
it waits for ANOTHER machine's report, which nothing in this shell can wait on.

Negative control: agent/lib/restore/chunkrecv.sh back to the base -- the latency is a second minus the child's life.
"""

from __future__ import annotations

import subprocess

from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = AGENT.parent
pytestmark = requires_native(("bash", BASH), why="the agent's shell, and a real sleep")

SCRIPT = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log"; mkdir -p "$RUN_DIR/chunk"
. agent/lib/core/common.sh; . agent/lib/core/jsonq.sh; . agent/lib/core/waits.sh
. agent/lib/restore/chunkledger.sh; . agent/lib/restore/chunkrecv.sh
CHUNK_STATE="$RUN_DIR/chunk/p3.state"; CHUNK_OUT="$RUN_DIR/chunk/recv.out"; : > "$CHUNK_OUT"
STREAM_START_CEILING=${CEILING:-100}; WAIT_STREAM_STALL_S=${CEILING:-100}; WAIT_DRAWER_S=${CEILING:-100}
chunk_watch_start() { :; }; chunk_watch_end() { :; }; chunk_loss_tell() { :; }; _chunk_counters() { :; }   # the watch and the ledger are not what is measured
round_closed() { return 1; }
( command sleep "$LIFE"; date +%s%N > "$RUN_DIR/ended" ) &
_chunk_run "the receiver" "$!" 3 sda; echo "RC=$?"
t1=$(date +%s%N); t0=$(cat "$RUN_DIR/ended")
echo "LATENCY_MS=$(( (t1 - t0) / 1000000 ))"
'''


def _run(tmp_path, life: str, **env) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "run.sh").write_text(SCRIPT, encoding="utf-8", newline="\n")
    assigns = " ".join(f"{k}={v}" for k, v in {"LIFE": life, **env}.items())
    proc = subprocess.run([BASH, "-c", f"{assigns} . {posix(tmp_path / 'run.sh')!r} {posix(tmp_path)!r}"],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    out = proc.stdout
    assert "RC=0" in out and "LATENCY_MS=" in out, proc.stdout + proc.stderr
    return {"latency_ms": int(out.split("LATENCY_MS=")[1].split()[0]), "out": out}


def test_the_receivers_end_is_seen_in_a_fraction_of_a_second(tmp_path):
    """The child lives 0.15 s. With `sleep 1` the loop looks again at 1.0 s: ~850 ms late. With `_nap`: ~100 ms."""
    got = _run(tmp_path, "0.15")
    assert got["latency_ms"] < 500, got


def test_a_receiver_that_lives_for_whole_seconds_is_still_seen_at_once(tmp_path):
    """Not only the first tick: after two and a bit rounds of the loop the end is still seen within a tick."""
    got = _run(tmp_path, "2.3")
    assert got["latency_ms"] < 500, got


def test_the_silence_ceiling_still_counts_real_seconds(tmp_path):
    """`_cr_quiet` counts rounds of the loop and the ceiling is in SECONDS: a receiver that is silent and does not
    end is still killed after about the ceiling (here 1+1+1 = 3 s), not after ten times that and not at once."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = SCRIPT.replace('( command sleep "$LIFE"; date +%s%N > "$RUN_DIR/ended" ) &',
                            'command sleep 30 & _held=$!; date +%s%N > "$RUN_DIR/ended"; : > "$RUN_DIR/started"').replace(
                                '"$!" 3 sda', '"$_held" 3 sda')
    (tmp_path / "run.sh").write_text(script, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, "-c", f"LIFE=0 CEILING=1 . {posix(tmp_path / 'run.sh')!r} {posix(tmp_path)!r}"],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert "RC=99" in proc.stdout, proc.stdout + proc.stderr
    took_ms = int(proc.stdout.split("LATENCY_MS=")[1].split()[0])
    assert 2000 <= took_ms <= 6000, took_ms
