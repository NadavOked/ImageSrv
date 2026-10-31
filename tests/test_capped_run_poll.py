"""capped_run polls in 0.1 s steps with the same ceilings (#1450).

Every smartctl/nvme call goes through capped_run; with `sleep 1` polling a
50 ms command cost a full second, three drawers three seconds inside the
first hello. The ceiling is unchanged and a timeout is still a timeout
(principle 5). A busybox that refuses `sleep 0.1` must not turn the poll into
a hot loop that declares every command timed out.
"""

from __future__ import annotations

import subprocess
import time

from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH))


def _run(body: str) -> tuple[subprocess.CompletedProcess[str], float]:
    prefix = (
        'export PATH="/usr/bin:$PATH"; '
        'RUN_DIR=$(mktemp -d); export RUN_DIR; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; '
    )
    started = time.monotonic()
    proc = subprocess.run(
        [BASH, "-c", prefix + body], capture_output=True, text=True,
        encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60,
    )
    return proc, time.monotonic() - started


def _extra(body: str) -> tuple[subprocess.CompletedProcess[str], float]:
    _, baseline = _run(":")
    proc, elapsed = _run(body)
    return proc, elapsed - baseline


def test_a_fast_command_does_not_cost_a_whole_second():
    proc, extra = _extra(
        'capped_run 30 "$RUN_DIR/out" true; '
        'echo "rc=$? t=$CAPPED_TIMED_OUT"'
    )
    assert proc.returncode == 0
    assert "rc=0 t=0" in proc.stdout
    assert extra < 0.7      # old code: >= 1.0 s

    proc, extra = _extra(
        'capped_run 30 "$RUN_DIR/out" true; '
        'capped_run 30 "$RUN_DIR/out" true; '
        'capped_run 30 "$RUN_DIR/out" true; '
        'echo "rc=$? t=$CAPPED_TIMED_OUT"'
    )
    assert proc.returncode == 0
    assert "rc=0 t=0" in proc.stdout
    assert extra < 2.4      # old code: >= 3.0 s; ~0.4 s measured on Windows


def test_the_exit_code_and_stdout_are_the_command_s_own():
    proc, _ = _extra(
        "capped_run 5 \"$RUN_DIR/out\" sh -c 'echo hi; exit 3'; "
        'echo "rc=$? t=$CAPPED_TIMED_OUT out=$(cat "$RUN_DIR/out")"'
    )
    assert proc.returncode == 0
    assert "rc=3 t=0 out=hi" in proc.stdout


def test_the_ceiling_still_holds_and_is_reported_as_a_timeout():
    proc, extra = _extra(
        'capped_run 1 "$RUN_DIR/out" sleep 5; '
        'echo "rc=$? t=$CAPPED_TIMED_OUT"'
    )
    assert proc.returncode == 0
    assert "rc=124 t=1" in proc.stdout
    assert 0.8 <= extra <= 3.0


def test_a_sleep_that_refuses_fractions_does_not_spin_or_stretch_the_ceiling():
    fallback = 'sleep() { case "$1" in *.*) return 1 ;; esac; command sleep "$1"; }; '
    proc, extra = _extra(
        fallback + 'capped_run 2 "$RUN_DIR/out" command sleep 10; '
        'echo "rc=$? t=$CAPPED_TIMED_OUT"'
    )
    assert proc.returncode == 0
    assert "rc=124 t=1" in proc.stdout
    assert 1.5 <= extra <= 4.5

    proc, _ = _extra(
        fallback + 'capped_run 5 "$RUN_DIR/out" true; '
        'echo "rc=$? t=$CAPPED_TIMED_OUT"'
    )
    assert proc.returncode == 0
    assert "rc=0 t=0" in proc.stdout
