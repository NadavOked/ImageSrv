"""#3721: two overlapping refreshes of the server-memory files must not trample each other.

`disk_failures_refresh` (and `disk_f1_refresh`, which it calls) are run by the kiosk and by the agent at the same time
on one RUN_DIR. They built the new file under a fixed `.next` name, so the second run truncated the first run's
file, and the first run then failed with "not read" (the wait screen: "unchecked") for no real reason.

The test runs the real functions: a first run is held in the middle of its write (the stubbed `json_get_join` has already
printed `ok` and then sleeps), a second one runs to the end in that time. Both must return 0, the final file must be whole,
and no temp file may be left in RUN_DIR.
"""
from __future__ import annotations

import re

import pytest

from test_agent import AGENT, posix
from test_failmark import memory_run, hello_answer

SLOW = (
    'json_get_join() { [ -s "$1" ] || return 1; echo ok; '
    'case "$2" in *"$SLOW_ON"*) [ -n "$SLOW" ] && sleep 1 ;; esac; echo "S3TWNE0JB04745||cable"; }; '
)
F1_LIB = f". {posix(AGENT)}/lib/smart/f1mark.sh; "


@pytest.mark.parametrize("fn,final,slow_on", [
    ("disk_failures_refresh", "disk_failures", "disk_failures"),
    ("disk_f1_refresh", "disk_f1", "f1_observed"),
])
def test_two_overlapping_refreshes_both_succeed_and_leave_no_temp_file(tmp_path, fn, final, slow_on):
    reply = hello_answer({"serial": "S3TWNE0JB04745", "port": None, "cause": "cable", "at": "t"})
    reply = reply[:-1] + ',"f1_observed":[{"serial":"S3TWNE0JB04745","date":"d","source":"manual"}]}'
    script = (
        f'export SLOW_ON={slow_on}; '
        f'( SLOW=1 {fn}; echo "slow=$?" ) & slow_pid=$!; sleep 0.3; '
        f'{fn}; echo "fast=$?"; wait "$slow_pid"; '
        f'echo "first=$(head -n 1 "$RUN_DIR/{final}")"; wc -l < "$RUN_DIR/{final}" | tr -d " " | sed "s/^/lines=/"; '
        'echo "left=$(ls "$RUN_DIR" | grep -E "^disk_(failures|f1)[.]" | tr "\n" " ")"'
    )
    run, out, _ = memory_run(tmp_path, reply, script, F1_LIB + SLOW)
    assert "fast=0" in out and "slow=0" in out, out
    assert "first=ok" in out and "lines=2" in out, out
    assert "left=\n" in out + "\n" or out.rstrip().endswith("left="), out
