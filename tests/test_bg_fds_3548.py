"""#3548 -- the other background jobs of the agent held a `$( )` capture open the way netprobe's did (#3524).

The mechanism (tests/test_gap_subshell_fd_3524.py): a redirected call inside `$( )` (`fn >&2`) parks the capture's
pipe on fd 10 and up; a `( ... ) &` started from inside inherits it, and `$( )` reads to EOF -- it returns when the
job ends, not when the function does. `> /dev/null 2>&1 < /dev/null` covers 0-2 only. The fix is ONE helper,
`bg_detach_fds` (bgfds.sh), the first line of each job -- not a closed fd (`exec N>&-` breaks ash's unwinding).

Here each starter runs inside a capture, with a job body that says it started and waits for a file (bounded: the
cap is the end of the negative control), and the capture must come back while the job is still waiting. Under
`busybox ash`, the agent's shell, as #3524. The jobs' own bodies are stubs: the subject is what the job inherits.
"""

from __future__ import annotations

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, posix
from test_beeps_2067 import BUSYBOX, run_ash
from test_gap_subshell_fd_3524 import AFTER, WAIT_GO, prelude

pytestmark = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")

#: The job body stub: started, wait for `go` (or the cap), finished -- then the job ends (exit, as a beat that stopped).
JOB = ': > "$RUN_DIR/job.started"; ' + WAIT_GO + ': > "$RUN_DIR/job.finished"'

#: name -> (libraries, stubs for what the job calls, the call that starts it)
STARTERS = {
    "attended_start": (("attended",), f"attended_hello() {{ {JOB}; exit 0; }}", "attended_start q"),
    "held_beat_start": (("attended", "hold"), f"held_report() {{ {JOB}; exit 0; }}", "held_beat_start"),
    "_monitor_waiter": (("monitor",), f"_monitor_launch() {{ {JOB}; return 0; }}; MONITOR_FB_WAIT_S=5", "_monitor_waiter"),
    "event_beep": (("roundbeep",), f"_rb_note() {{ :; }}; _rb_lock() {{ {JOB}; }}; _rb_play() {{ :; }}", "event_beep success"),
}


@pytest.mark.parametrize("name", sorted(STARTERS))
def test_the_job_does_not_hold_the_capture(tmp_path, name):
    libs, stubs, call = STARTERS[name]
    out = run_ash(
        prelude(tmp_path) + f'. {posix(AGENT)}/lib/core/common.sh; ' + agent_sources(*libs)
        + stubs + "; " + r'''
f() { ''' + call + r''' >&2; echo list; }
out=$(f); echo "capture=$out"
''' + AFTER % {"started": "job.started", "finished": "job.finished"}, timeout=60)
    assert "capture=list" in out, out
    assert "started=yes" in out, f"{name}: the job never ran -- the room is not the call\n" + out
    assert "finished_when_capture_returned=no" in out, (
        f"{name}: `$( )` came back only after the job ended: it held the capture open\n" + out)
