"""#3524 -- the 35 seconds were a background job holding the capture of `$(smart_preflight ...)` open.

r16, G35-1 (metal, 16/11): the last stamp inside `smart_preflight` is 14:36:31, and `smart_gate`
returns at 14:37:06 -- 35 s later, with `echo $_pf_write` the only command in between. The run
without the gap (B0-1) had `netprobe=cached`; this one had `netprobe=none`.

The mechanism, measured on busybox ash 1.37 (and bash): `attended_hello "" >&2` runs inside
`$( )`. A redirected function call parks the descriptor it redirected (here: the capture's pipe,
stdout) on a spare one, fd 10 and up. `netprobe_json` starts `( _netprobe_fresh ... ) &` from inside
that call; the subshell inherits the spare fd, and -- a shell that waits for `lldpsniff
$LLDP_WAIT` -- keeps it until it ends. `$( )` reads to EOF: it returns when the last writer is gone,
not when the function is. `> /dev/null 2>&1 < /dev/null` closes 0-2 and not the spare one.

The fix points every open descriptor 3..31 at /dev/null at the top of the background job (netprobe.sh,
probe_bg.sh). Not `exec N>&-`: ash restores the parked fds when the job exits (`dup2(10,1)`), and with fd 10
closed that fails, loops, and runs the PARENT's code in the child (measured, busybox 1.37).

The rooms run under `busybox ash` -- the agent's shell, and the only one where "ash does X" is
measured, not assumed -- with a fake `lldpsniff` / probe that waits for a file, not a clock.
"""

from __future__ import annotations

from pathlib import Path

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, posix
from test_beeps_2067 import BUSYBOX, run_ash

pytestmark = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")

#: One job waits for `$RUN_DIR/go` for at most this long (0.1 s steps) -- the cap that ends the
#: negative control. With the fix nobody waits for it; without it `$( )` does, all of it.
JOB_CAP_TICKS = 100

WAIT_GO = ('_t=0; until [ -e "$RUN_DIR/go" ] || [ "$_t" -ge %d ]; do sleep 0.1; _t=$((_t+1)); done; ' % JOB_CAP_TICKS)

#: After the capture came back: wait (bounded) for the job to have started, then say what it is doing.
AFTER = r'''
_t=0; until [ -e "$RUN_DIR/%(started)s" ] || [ "$_t" -ge 100 ]; do sleep 0.1; _t=$((_t+1)); done
[ -e "$RUN_DIR/%(started)s" ] && echo started=yes || echo started=no
[ -e "$RUN_DIR/%(finished)s" ] && echo finished_when_capture_returned=yes || echo finished_when_capture_returned=no
: > "$RUN_DIR/go"
_t=0; until [ -e "$RUN_DIR/%(finished)s" ] || [ "$_t" -ge 100 ]; do sleep 0.1; _t=$((_t+1)); done
'''


def prelude(tmp_path: Path) -> str:
    run = tmp_path / "run"; run.mkdir()
    return (f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "agent.log")!r} '
            f'SYSFS_ROOT={posix(tmp_path / "sys")!r} IFACE=eth0 ETHTOOL=ethtool-not-here '
            f'IMAGESRV_TEST=1 MAC=aa:bb:cc:dd:ee:ff SERVER=http://x; ')


def fake_lldpsniff(tmp_path: Path) -> Path:
    """Says it started, waits for `go` (or its cap), says it finished, then answers."""
    path = tmp_path / "lldpsniff"
    path.write_text('#!/bin/sh\n: > "$RUN_DIR/lldp.started"\n' + WAIT_GO
                    + ': > "$RUN_DIR/lldp.finished"\nprintf \'{"neighbor":"sw1"}\'\n', encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def test_the_smart_gate_does_not_wait_for_lldp(tmp_path):
    """The real smart_gate -> smart_preflight -> attended_hello -> netprobe_json chain, on the first hello
    (no netprobe.json): the capture returns while lldpsniff is still listening, and its answer is cached after."""
    sniff = fake_lldpsniff(tmp_path)
    out = run_ash(
        prelude(tmp_path) + f'export LLDPSNIFF={posix(sniff)!r}; '
        + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
        + agent_sources("netprobe") + f'. {posix(AGENT)}/lib/core/attended.sh; . {posix(AGENT)}/lib/smart/smartgate.sh; '
        + r'''
build_hello() { printf '{"schema":2%s}' "$(netprobe_json)"; }   # the hello's own body is not the subject: its netprobe fragment is
http_post_json() { return 0; }; attended_keep() { :; }; disk_failures_refresh() { :; }
smart_probe() { echo ok; }; smart_field() { :; }; disk_failure_cause() { return 1; }
smart_event_json() { echo '{}'; }; smart_learned_refresh() { return 0; }
list=$(smart_gate ses sda); echo "gate_rc=$? list=$list"
''' + AFTER % {"started": "lldp.started", "finished": "lldp.finished"}
        + r'''_t=0; until [ -s "$RUN_DIR/netprobe.json" ] || [ "$_t" -ge 50 ]; do sleep 0.1; _t=$((_t+1)); done
cat "$RUN_DIR/netprobe.json"; echo
''', timeout=60)
    assert "gate_rc=0 list=sda" in out, out
    assert "started=yes" in out, "the background lldpsniff never ran -- the room is not the chain\n" + out
    assert "finished_when_capture_returned=no" in out, (
        "`$(smart_gate ...)` came back only after lldpsniff ended: the background job held the capture open\n" + out)
    assert '"lldp":{"neighbor":"sw1"}' in out, "the cache must still get lldpsniff's answer\n" + out


def test_the_hardware_probe_job_does_not_hold_the_capture(tmp_path):
    """Same leak through probe_bg.sh: its job outlives the call that started it by up to PROBE_MAX_S."""
    out = run_ash(
        prelude(tmp_path)
        + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; '
        + f'. {posix(AGENT)}/lib/hw/probe.sh; . {posix(AGENT)}/lib/core/bgfds.sh; . {posix(AGENT)}/lib/hw/probe_bg.sh; '
        + r'''
_probe_fresh() { : > "$RUN_DIR/probe.started"; ''' + WAIT_GO + r''': > "$RUN_DIR/probe.finished"; printf ',"probe":{"probe_seconds":1}'; }
hello() { probe_json >&2; echo list; }
list=$(hello); echo "capture=$list"
''' + AFTER % {"started": "probe.started", "finished": "probe.finished"}
        + r'''_t=0; while [ -e "$RUN_DIR/probe.json.running" ] && [ "$_t" -lt 100 ]; do sleep 0.1; _t=$((_t+1)); done
[ -s "$RUN_DIR/probe.json" ] && echo cached=yes || echo cached=no
''', timeout=60)
    assert "capture=list" in out, out
    assert "started=yes" in out, out
    assert "finished_when_capture_returned=no" in out, (
        "`$( )` came back only after the probe job ended: it held the capture open\n" + out)
    assert "cached=yes" in out, "the job must still cache its result\n" + out


def test_the_job_lets_go_of_what_it_inherits_and_the_agent_lib_has_the_helpers():
    """A guard against the next background job copying the old line: both starters call the closer first."""
    net = (AGENT / "lib/hw/netcache.sh").read_text(encoding="utf-8")   # #3632: the one starter, _np_bg
    bg = (AGENT / "lib/hw/probe_bg.sh").read_text(encoding="utf-8")
    starter = net[net.index("_np_bg() {"):]
    assert net.count("_netprobe_fresh >") == 1 and starter.index("bg_detach_fds") < starter.index("_netprobe_fresh >"), (
        "an un-closed background line")
    assert "_netprobe_fresh >" not in (AGENT / "lib/hw/netprobe.sh").read_text(encoding="utf-8"), "a second starter beside _np_bg"
    assert "bg_detach_fds   # #3548 (#3524)\n        trap " in bg


def test_the_closer_leaves_ash_able_to_unwind_its_own_redirections(tmp_path):
    """The job is forked inside a redirected call. Closing the parked fd instead of pointing it at /dev/null made ash
    fail `dup2(10,1)` on the way out and loop (the job's stderr is /dev/null on the agent, so it dies unseen):
    here the job's stderr is the capture, and it must say nothing and finish."""
    out = run_ash(
        prelude(tmp_path) + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
        + agent_sources("netprobe")
        + r'''
f() { ( bg_detach_fds; x=$(echo in-job); echo "job=$x" ) 2>&1; }
out=$( { f >&2; echo list; } 2>&1 ); echo "$out"
''', timeout=30)
    assert out.split() == ["job=in-job", "list"], out
