"""#3681 -- the idle power-off clock (`idle.since`) was wall-clock, and the hello answer moves the wall clock.

`clock_from_server` (hello.sh) sets the clock from the server at any hello that is more than 2 s off. A stamp written
before such a step was off by the step: +835 s (the RTC offset measured on cloner 2, #3652) made a machine that had waited
seconds "idle for 835 s" -- the 30 s warning and a power-off; -835 s made the same machine wait 300 + 835 s.

Now `idle.since` is whole seconds of /proc/uptime (idle.sh `idle_up`). A stamp that is not a number, or is later than now,
or no readable uptime, is no evidence of idleness: the clock restarts and the machine stays on (principle 5).

The room is test_agent_idle's: IDLE_POWEROFF_S=2, a fixed uptime file that `up N` moves, `poweroff` faked. `date` is a function
that adds `$RUN_DIR/clock.offset` -- the step. Only the state under test differs between the cases.
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import BASH
from test_agent_idle import run_idle

pytestmark = requires_native(("bash", BASH))

CLOCK = r'''
up() { echo "$1.50 0.00" > "$UPTIME_FILE"; }
date() { if [ "$1" = "+%s" ]; then echo $(( $(command date +%s) + $(cat "$RUN_DIR/clock.offset" 2>/dev/null || echo 0) )); else command date "$@"; fi; }
step() { echo "$1" > "$RUN_DIR/clock.offset"; }
'''


def test_a_clock_step_forward_does_not_power_off_a_machine_that_just_started_waiting(tmp_path):
    out, calls = run_idle(tmp_path, CLOCK + "idle_reset", "step 835; idle_check; echo rc=$?")
    assert "rc=0" in out, out
    assert not calls.exists(), "the clock stepped 835 s and the machine powered off after 0 s of waiting\n" + out
    assert "without activity" not in out, out


def test_a_clock_step_back_does_not_delay_the_power_off(tmp_path):
    out, calls = run_idle(tmp_path, CLOCK + "idle_reset", "step -835; up 100003; idle_check")
    assert calls.exists() and calls.read_text(encoding="utf-8") == "POWEROFF -f\n", (
        "3 s of uptime past the 2 s limit, and the clock stepped back 835 s: the power-off waited for the clock\n" + out)


@pytest.mark.parametrize("stamp", ["$(( $(idle_up) + 5000 ))", "junk"], ids=["from-the-future", "not-a-number"])
def test_a_stamp_that_cannot_be_right_restarts_the_clock_and_powers_off_nothing(tmp_path, stamp):
    out, calls = run_idle(tmp_path, CLOCK + f'echo "{stamp}" > "$RUN_DIR/idle.since"',
                          'idle_check; echo rc=$?; echo SINCE=$(cat "$RUN_DIR/idle.since")')
    assert "rc=0" in out and not calls.exists(), out
    assert "SINCE=100000" in out, "the unusable stamp was left in place\n" + out


def test_without_a_readable_uptime_the_machine_stays_on_and_says_so(tmp_path):
    """A stamp of 1000 s ago on `date`'s clock is what the old code powered off on; no uptime = no evidence."""
    out, calls = run_idle(tmp_path, CLOCK + 'echo $(( $(date +%s) - 1000 )) > "$RUN_DIR/idle.since"; export UPTIME_FILE=/nonexistent',
                          "idle_check; echo rc=$?")
    assert "rc=0" in out and not calls.exists(), out
    assert "no uptime" in out, "the missing clock was not logged\n" + out
