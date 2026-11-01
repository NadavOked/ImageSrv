"""The first hello is cheap; the bounded probe joins a later hello."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH))


def run(script: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def prelude(run_dir: Path) -> str:
    return (f'export RUN_DIR={posix(run_dir)!r} LOG_FILE={posix(run_dir / "agent.log")!r}; '
            f'. {posix(AGENT / "lib/core/common.sh")!r}; . {posix(AGENT / "lib/core/waits.sh")!r}; '
            f'. {posix(AGENT / "lib/hw/probe.sh")!r}; . {posix(AGENT / "lib/hw/probe_bg.sh")!r}; ')


def test_first_hello_precedes_slow_probe_and_next_hello_carries_it(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    script = prelude(run_dir) + r'''
# #2275: the probe is held until the first hello is out (5 s at most -- so a
# probe_json that waited still ends, with the probe in its first hello), then slow.
_probe_fresh() { _g=0; until [ -e "$RUN_DIR/go" ] || [ "$_g" -ge 50 ]; do sleep 0.1; _g=$((_g+1)); done
                 sleep 2; printf ',"probe":{"probe_seconds":2}'; }
printf '{"schema":2%s}\n' "$(probe_json)"
# The first hello came back while the probe still ran -- its marker, not `date`
# seconds (one tick under load). A first hello that waited finds it gone.
[ -e "$RUN_DIR/probe.json.running" ] && [ ! -e "$RUN_DIR/probe.json" ] && echo first=while-probing || echo first=after-probe
: > "$RUN_DIR/go"
# The probe's own end, not a 4 s guess: its job removes the marker after it wrote
# probe.json and probe.json.at (probe_bg.sh's trap). Capped by the job's ceiling.
_i=0; while [ -e "$RUN_DIR/probe.json.running" ] && [ "$_i" -lt $(( (PROBE_MAX_S + 5) * 10 )) ]; do sleep 0.1; _i=$((_i+1)); done
printf '{"schema":2%s}\n' "$(probe_json)"
'''
    started = time.monotonic(); done = run(script, timeout=60); elapsed = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    first, second = json.loads(lines[0]), json.loads(lines[2])
    assert "probe" not in first
    assert lines[1] == "first=while-probing", lines
    assert "probe" in second, (lines, run_dir.joinpath("agent.log").read_text() if run_dir.joinpath("agent.log").exists() else "")
    assert second["probe"]["probe_seconds"] == 2
    assert elapsed >= 2, "the test must really run a slow probe"


def test_never_finishing_probe_is_killed_at_ceiling_and_logged(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    script = prelude(run_dir) + r'''
PROBE_MAX_S=1
_probe_fresh() { while :; do sleep 10; done; }
probe_json
_i=0; while [ -e "$RUN_DIR/probe.json.running" ] && [ "$_i" -lt 40 ]; do sleep 0.1; _i=$((_i+1)); done
[ ! -e "$RUN_DIR/probe.json.running" ] || exit 8
grep -q 'killed at 1s ceiling' "$LOG_FILE"
'''
    done = run(script)
    assert done.returncode == 0, done.stdout + done.stderr
