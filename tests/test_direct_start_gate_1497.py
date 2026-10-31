"""#1497 -- direct send: bytes that only filled the pipe do not start the quiet clock.

udp-sender reads nothing until its receivers joined, and for partition 2+
the server lets it wait up to 780 s (start_timeout_later). The pv counter
moves at once anyway (the pipe buffers fill), and wait_progress used to
swap the start ceiling (900 s) for the 300 s quiet ceiling at that first
move -- a healthy send could be killed as "stalled" at ~300 s. Now
direct_send_one hands wait_progress a one-shot start gate (udp-sender's own
"Starting transfer:" line, #438): until it is in the log the start ceiling
holds; from it, the quiet ceiling -- which still ends a stream that stops.

**Negative control:** with waits.sh and directsend.sh from before #1497
(both restored together), test_buffered_byte_does_not_shorten_start_wait,
test_stall_ceiling_starts_when_gate_opens, test_closed_gate_keeps_start_ceiling,
test_an_unreadable_log_keeps_the_start_ceiling and
test_direct_send_waits_for_udp_sender_start_evidence fail (killed at
t=310 / stage=timeout); the ungated and one-shot tests pass on both, which
is their point: the other callers (capture, drawers, restore) are unchanged.
"""

from __future__ import annotations

import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH
from test_directsend import direct_run

pytestmark = requires_native(("bash", BASH))


def _wait(tmp_path, *, gate_at=None, move_after_gate=False, exit_at=None,
          set_gate=True, gate_log="sender.log"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    gate_action = ":"
    if gate_at is not None:
        gate_action = f'[ "$t" -eq {gate_at} ] && echo "Starting transfer:" >> "$gate"'
    later_move = ""
    if move_after_gate and gate_at is not None:
        later_move = f'[ "$t" -gt {gate_at} ] && echo "$t" >> "$counter"'
    alive = ":"
    if exit_at is not None:
        alive = f'[ "$t" -lt {exit_at} ]'
    gate_vars = 'WAIT_STARTED_LOG="$gate" WAIT_STARTED_TEXT="Starting transfer:"' if set_gate else ":"
    make_gate = ': > "$gate"' if gate_log == "sender.log" else ":"   # an unreadable log is never created
    script = f'''
        . {AGENT.as_posix()!r}/lib/core/waits.sh
        WAIT_POLL_S=10; t=0; counter={str(tmp_path.as_posix() + "/counter")!r}
        gate={str(tmp_path.as_posix() + "/" + gate_log)!r}; : > "$counter"; {make_gate}
        log() {{ :; }}
        unblock_fifo() {{ :; }}
        kill() {{
            if [ "$1" = -0 ]; then {alive}; return; fi
            echo "killed=$t"; return 0
        }}
        wait() {{ return 0; }}
        sleep() {{
            t=$((t + 10))
            [ "$t" -eq 10 ] && echo 1 >> "$counter"
            {gate_action}
            {later_move}
        }}
        poll_tick() {{ sleep; _tk=$((WAIT_POLL_S * 10)); }}   # #1705: one fake tick = one poll
        {gate_vars}
        wait_progress 999 "$counter" 900 300 stream
        echo "result=$? t=$t"
    '''
    return subprocess.run([BASH, "--posix", "-c", script], capture_output=True,
                          text=True, timeout=120, stdin=subprocess.DEVNULL)   # ~90 polls x 3 processes: slow on Windows


def test_buffered_byte_does_not_shorten_start_wait(tmp_path):
    out = _wait(tmp_path, gate_at=600, move_after_gate=True, exit_at=1000)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "result=0 t=1000"


def test_stall_ceiling_starts_when_gate_opens(tmp_path):
    out = _wait(tmp_path, gate_at=600)
    assert out.stdout.strip().splitlines() == ["killed=900", "result=1 t=900"]


def test_closed_gate_keeps_start_ceiling(tmp_path):
    out = _wait(tmp_path)
    assert out.stdout.strip().splitlines() == ["killed=910", "result=1 t=910"]


def test_an_unreadable_log_keeps_the_start_ceiling(tmp_path):
    """udp-sender's log not there (yet, or at all): grep exits 2, which is
    "not started" -- the start ceiling, never the quiet one, never success."""
    out = _wait(tmp_path, gate_log="no-such-dir/sender.log")
    assert out.stdout.strip().splitlines() == ["killed=910", "result=1 t=910"], out.stdout + out.stderr
    assert "No such file" not in out.stderr, out.stderr


def test_ungated_call_keeps_existing_counter_behaviour(tmp_path):
    out = _wait(tmp_path, set_gate=False)
    assert out.stdout.strip().splitlines() == ["killed=310", "result=1 t=310"]


def test_gate_variables_are_one_shot(tmp_path):
    script = f'''
        . {AGENT.as_posix()!r}/lib/core/waits.sh
        WAIT_POLL_S=10; t=0; counter={str((tmp_path / "counter").as_posix())!r}
        gate={str((tmp_path / "gate").as_posix())!r}; : > "$counter"; : > "$gate"
        log() {{ :; }}; unblock_fifo() {{ :; }}
        kill() {{ if [ "$1" = -0 ]; then return 0; fi; echo "killed=$t"; }}
        wait() {{ return 0; }}
        sleep() {{ t=$((t + 10)); [ "$t" -eq 10 ] && echo 1 >> "$counter"; }}
        poll_tick() {{ sleep; _tk=$((WAIT_POLL_S * 10)); }}   # #1705: one fake tick = one poll
        WAIT_STARTED_LOG="$gate" WAIT_STARTED_TEXT="Starting transfer:"
        wait_progress 999 "$counter" 20 10 first >/dev/null
        : > "$counter"; t=0
        wait_progress 999 "$counter" 900 300 second
        echo "result=$? t=$t"
    '''
    out = subprocess.run([BASH, "--posix", "-c", script], capture_output=True,
                         text=True, timeout=10, stdin=subprocess.DEVNULL)
    assert out.stdout.strip().splitlines() == ["killed=310", "result=1 t=310"]


def test_direct_send_waits_for_udp_sender_start_evidence(tmp_path):
    delayed_sender = (
        '#!/bin/sh\n'
        'printf "%s\\n" "$@" >> "$UDP_ARGV"\n'
        'sleep 6\n'
        'echo "Starting transfer: 0000000"\n'
        'cat > /dev/null\n'
        'exit 0\n'
    )
    box, run, out = direct_run(
        tmp_path, env={"WAIT_POLL_S": "1", "WAIT_STREAM_START_S": "20",
                       "WAIT_STREAM_STALL_S": "2"},
        udp_sender=delayed_sender, timeout=90,
    )
    assert out.strip().endswith("rc=0"), out + (run / "agent.log").read_text(
        "utf-8", "replace"
    )
