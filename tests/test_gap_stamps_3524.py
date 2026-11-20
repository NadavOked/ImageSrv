"""#3524 -- the 35 seconds between "the server confirmed it is open" and "writing N drawers".

On metal (r11, r12, metal-1115-compare: 15 of 20 runs, both engines) the cloner sits 35 s
between the two lines, with no line of its own in between. The mechanism is not proven, so
this change only OBSERVES, and changes no behaviour:

* every step of `do_restore_drawers` between the two lines says `gap: <step> begin|end` in the
  agent log (short lines -- the log is shared);
* the hardware probe that `probe_json` starts in the background (probe_bg.sh) no longer sends
  its output to /dev/null: start, the ceiling kill and the end with rc and seconds go to
  `$RUN_DIR/probe_bg.log`. No RUN_DIR, or a file that cannot be opened = /dev/null, as before.

The stamps go to stderr: the whole span runs inside `$( )`, where stdout is the disk list.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import fixtures_smart as F
from agentlib import AGENT_MAIN, lib_files
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_agent import sh as agent_sh   # build_box's prelude is test_agent's
from test_restore_evidence import PLAN, build_box
from test_smart import PRELUDE, env, preflight, sh

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq אינו מותקן — build_hello/smart_learned לא היו רצים")
needs_bash = requires_native(("bash", BASH))


def stamps(run: Path) -> list[str]:
    """The `gap:` lines of the agent log, in order, without the clock."""
    log = run / "agent.log"
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return [ln.split("gap: ", 1)[1] for ln in text.splitlines() if "gap: " in ln]


def in_order(lines: list[str], wanted: list[str]) -> None:
    """`wanted` appear in `lines` as a subsequence, each as a prefix of a line."""
    i = 0
    for w in wanted:
        while i < len(lines) and not lines[i].startswith(w):
            i += 1
        assert i < len(lines), f"stamp {w!r} missing or out of order in {lines}"
        i += 1


# --- the SMART gate: probe, the one hello, the events --------------------------------------

STUBS = ('build_hello() { printf \'{"schema":2,"mac":"aa:bb:cc:dd:ee:ff"}\'; }; '   # the hello's body is not the subject
         'SERVER=http://x; ')


@needs_jq
def test_the_smart_gate_stamps_each_step_in_order(tmp_path):
    written, run, err = preflight(tmp_path, F.evo860(), extra=STUBS)
    assert written == "sda", "a stamp reached stdout: the disk list is what `$( )` captures\n" + err
    got = stamps(run)
    in_order(got, [
        "disk_failures_refresh begin", "disk_failures_refresh end",
        "smart_probe sda begin cache=miss", "smart_probe sda end",
        "attended_hello build begin", "attended_hello build end bg: probe=",
        "attended_hello post end rc=0", "attended_hello + smart_learned_refresh end",
        "smart_probe2 sda begin", "smart_probe2 sda end",
        "smart_send_event sda begin", "smart_send_event sda end delivered",
    ])


@needs_jq
def test_a_second_round_says_the_probe_was_cached(tmp_path):
    preflight(tmp_path, F.evo860(), extra=STUBS)
    _, run, _ = preflight(tmp_path, F.evo860(), extra=STUBS)
    probes = [s for s in stamps(run) if s.startswith("smart_probe sda begin")]
    assert probes == ["smart_probe sda begin cache=miss", "smart_probe sda begin cache=hit"], probes


@needs_jq
def test_a_failed_post_is_stamped_with_its_rc_and_the_gate_goes_on(tmp_path):
    """Principle 5: not delivered is said, and it is the same word the gate already used."""
    extra = STUBS + 'http_post_json() { return 7; }; '
    written, run, err = preflight(tmp_path, F.evo860(), extra=extra)
    assert written == "sda", err
    got = stamps(run)
    in_order(got, ["attended_hello post end rc=7", "smart_send_event sda end not delivered"])


@needs_jq
def test_the_beat_of_a_waiting_screen_does_not_fill_the_log(tmp_path):
    """attended_hello runs every ATTENDED_BEAT_S while a question waits; only the gate asks it to talk."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    out = sh(env(run, dev) + PRELUDE + STUBS + 'attended_hello ""; echo "rc=$?"')
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    assert stamps(run) == [], stamps(run)


# --- the drawer gates: the plan (sgdisk) and the RAID probe ---------------------------------

@needs_bash
def test_the_drawer_gates_stamp_the_plan_the_sgdisk_and_the_raid_probe(tmp_path):
    _box, run, prelude = build_box(tmp_path, plan=PLAN)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    out = agent_sh(prelude + f". {posix(AGENT)}/lib/restore/drawergate.sh; "
                   + 'raid_member_probe() { echo none; }; share_plan() { :; }; '
                   + 'drawer_gates sda > "$RUN_DIR/out" 2> "$RUN_DIR/err"; echo "rc=$?"')
    assert "rc=0" in out, out   # test_agent.sh returns the stdout
    assert (run / "out").read_text().split() == ["sda"], "a stamp reached stdout\n" + (run / "err").read_text()
    in_order(stamps(run), [
        "drawer_plan_gate sda begin", "sgdisk pretend begin", "sgdisk pretend end rc=0",
        "drawer_plan_gate sda end ok", "raid_member_probe sda begin", "raid_member_probe sda end",
    ])


@needs_bash
def test_a_refused_plan_is_stamped_refused_and_keeps_the_loops_rc(tmp_path):
    """drawer_plan_gate's rc was the last drawer's `plan_gate && echo` -- 1 when it was refused."""
    _box, run, prelude = build_box(tmp_path, plan=PLAN)
    out = agent_sh(prelude + f". {posix(AGENT)}/lib/restore/drawergate.sh; "
                        + 'plan_gate() { return 1; }; drawer_plan_gate "$RUN_DIR/manifest.json" sda > "$RUN_DIR/out" 2>/dev/null; echo "rc=$?"')
    assert "rc=1" in out, out
    assert (run / "out").read_text() == ""
    in_order(stamps(run), ["drawer_plan_gate sda begin", "drawer_plan_gate sda end refused"])


# --- the agent's own line: begin/end around each stage, in order ------------------------------

def test_the_agent_stamps_the_stages_between_confirmed_and_writing():
    src = AGENT_MAIN.read_text(encoding="utf-8")
    marks = ["session_open_gate", 'gap: drawers_bound begin', 'gap: drawers_bound end', 'gap: smart_gate begin',
             'gap: smart_gate end rc=', 'gap: drawer_gates begin', 'gap: drawer_gates end', 'log "writing $(']
    at = [src.index(m, src.index("do_restore_drawers()")) for m in marks]
    assert at == sorted(at), dict(zip(marks, at))
    # the rc of smart_gate is read before anything else runs: `_gr=$?` straight after the capture
    assert re.search(r'_disks=\$\(smart_gate "\$2" \$_disks\); _gr=\$\?; log "gap: smart_gate end', src)


def test_every_gap_stamp_goes_to_stderr_except_the_probe_jobs_own():
    """A stamp on stdout inside `$( )` becomes a drawer name in the list (#875: the same bug, once)."""
    bad = []
    for path in [AGENT_MAIN, *lib_files()]:
        if path.name == "probe_bg.sh":   # its stdout is the job's own file
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if 'log "gap:' in line and ">&2" not in line:
                bad.append(f"{path.name}:{n}")
    assert bad == [], bad


# --- the background hardware probe: its output in a file, not /dev/null -----------------------

def run_sh(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def prelude(run_dir: Path) -> str:
    return (f'export RUN_DIR={posix(run_dir)!r} LOG_FILE={posix(run_dir / "agent.log")!r}; '
            f'. {posix(AGENT / "lib/core/common.sh")!r}; . {posix(AGENT / "lib/core/waits.sh")!r}; '
            f'. {posix(AGENT / "lib/hw/probe.sh")!r}; . {posix(AGENT / "lib/core/bgfds.sh")!r}; . {posix(AGENT / "lib/hw/probe_bg.sh")!r}; ')


WAIT_DONE = r'''_i=0; while [ -e "$MARK" ] && [ "$_i" -lt 400 ]; do sleep 0.1; _i=$((_i+1)); done
[ ! -e "$MARK" ] || exit 8
'''


@needs_bash
def test_the_probe_job_writes_start_and_end_with_its_seconds(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    script = prelude(run_dir) + 'MARK="$RUN_DIR/probe.json.running"; ' + r'''
_probe_fresh() { sleep 1; printf ',"probe":{"probe_seconds":1}'; }
probe_json > /dev/null
''' + WAIT_DONE + 'cat "$RUN_DIR/probe_bg.log"'
    done = run_sh(script)
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert re.search(r"probe_bg: start, ceiling 35s$", lines[0]), lines
    assert re.search(r"probe_bg: end rc=0 timed_out=0 after [0-9]+s$", "\n".join(lines), re.M), lines
    shared = (run_dir / "agent.log").read_text(encoding="utf-8")
    assert "gap: hardware probe start" in shared and "gap: hardware probe end rc=0 timed_out=0 after" in shared, shared
    assert (run_dir / "probe.json").read_text(), "the result is still cached as before"


@needs_bash
def test_the_ceiling_kill_is_in_the_file(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    script = prelude(run_dir) + 'MARK="$RUN_DIR/probe.json.running"; PROBE_MAX_S=1; ' + r'''
_probe_fresh() { exec sleep 30; }
probe_json > /dev/null
''' + WAIT_DONE + 'cat "$RUN_DIR/probe_bg.log"'
    done = run_sh(script)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "probe_bg: killed at 1s ceiling" in done.stdout, done.stdout
    assert re.search(r"probe_bg: end rc=124 timed_out=1 after [0-9]+s", done.stdout), done.stdout
    assert not (run_dir / "probe.json").exists(), "a killed probe caches nothing"


@needs_bash
def test_no_run_dir_means_dev_null_as_before(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    script = prelude(tmp_path / "run") + f'MARK={posix(cache)!r}/probe.json.running; ' + r'''
unset RUN_DIR
_probe_fresh() { printf ',"probe":{"probe_seconds":0}'; }
_probe_background_start "${MARK%.running}" 1
''' + WAIT_DONE + r'''[ -s "${MARK%.running}" ]'''
    (tmp_path / "run").mkdir()
    done = run_sh(script)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    assert not list(tmp_path.rglob("probe_bg.log"))


@needs_bash
def test_a_log_file_that_cannot_be_opened_does_not_stop_the_probe(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    (run_dir / "probe_bg.log").mkdir()   # a directory: `>>` on it fails
    script = prelude(run_dir) + 'MARK="$RUN_DIR/probe.json.running"; ' + r'''
_probe_fresh() { printf ',"probe":{"probe_seconds":0}'; }
probe_json > /dev/null
''' + WAIT_DONE + '[ -s "$RUN_DIR/probe.json" ]'
    done = run_sh(script)
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
