"""#1929 (M3, 20/10): after `kill -9` to the agent in the middle of a pull,
more processes named imagectl-agent with ppid 1 were seen, each with its own
lldpsniff -- the hello's probe.

What the code says (investigated, not measured on metal):
* nothing starts a second agent: /init runs it once, in the foreground, and
  reboots when it exits; watchdog_supervise only reboots, never respawns.
* a forked subshell of the agent carries the agent's command line. The
  attended beat (attended.sh) is one, and it runs build_hello (lldpsniff) at
  every beat. When the agent dies it is orphaned (ppid 1) and went on saying
  hello AND feeding the watchdog's lease -- for an agent that was gone.

The fix, each half tested here:
1. agent_lock (watchdog.sh): an atomic pid file; a second live agent stops
   before it touches anything; a dead agent's pid file is taken over.
2. the beat stops when its agent is gone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix

import hygiene

pytestmark = requires_native(("bash", BASH))


def _sh(tmp_path: Path, script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    pre = (f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "log")!r}; '
           f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/watchdog.sh; ')
    return hygiene.run_group([BASH, "-c", pre + script], capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL)


def test_the_first_agent_takes_the_lock(tmp_path):
    out = _sh(tmp_path, 'agent_lock; echo "rc=$? me=$$ file=$(cat "$RUN_DIR/agent.pid")"')
    rc, me, file = (kv.split("=")[1] for kv in out.stdout.split())
    assert rc == "0" and me == file, out.stdout + out.stderr


def test_a_second_agent_stops_while_the_first_is_alive(tmp_path):
    fake = tmp_path / "imagectl-agent"                       # a live process with the agent's name
    fake.write_text("sleep 20\n", newline="\n")
    out = _sh(tmp_path, f'sh {posix(fake)!r} > /dev/null 2>&1 & a=$!; sleep 0.5; '
                        'echo "$a" > "$RUN_DIR/agent.pid"; agent_lock; echo "rc=$?"; '
                        'echo "file=$(cat "$RUN_DIR/agent.pid") a=$a"; kill $a')
    assert "rc=1" in out.stdout, out.stdout + out.stderr
    file, a = (kv.split("=")[1] for kv in out.stdout.splitlines()[-1].split())
    assert file == a                                         # the first agent keeps it
    assert "another agent" in out.stderr and "stops before it touches anything" in out.stderr


def test_a_dead_agents_pid_file_is_taken_over_aloud(tmp_path):
    out = _sh(tmp_path, 'sh -c "exit 0" & d=$!; wait $d; echo "$d" > "$RUN_DIR/agent.pid"; '
                        'agent_lock; echo "rc=$? me=$$ file=$(cat "$RUN_DIR/agent.pid")"')
    rc, me, file = (kv.split("=")[1] for kv in out.stdout.split())
    assert rc == "0" and me == file, out.stdout + out.stderr
    assert "taken over" in out.stderr


def test_a_live_process_that_is_not_an_agent_does_not_hold_the_lock(tmp_path):
    out = _sh(tmp_path, 'sleep 20 & s=$!; echo "$s" > "$RUN_DIR/agent.pid"; '
                        'agent_lock; echo "rc=$?"; kill $s')
    assert "rc=0" in out.stdout, out.stdout + out.stderr


def test_the_agent_takes_the_lock_before_anything_else():
    main = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert main.index("agent_lock || exit 1") < main.index("parse_cmdline\n")


def test_a_beat_whose_agent_was_killed_stops(tmp_path):
    """The agent: a shell that starts the beat (attended_hello stubbed to a
    line in a file) and is killed with -9. The beat may finish the beat it
    is in; after that, nothing."""
    run = tmp_path / "run"
    run.mkdir()
    agent = (f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "log")!r} ATTENDED_BEAT_S=1; '
             f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/attended.sh; '
             'attended_hello() { echo b >> "$RUN_DIR/beats"; }; attended_start q; sleep 60')
    (tmp_path / "agent.sh").write_text(agent + "\n", newline="\n")
    out = _sh(tmp_path, f'bash {posix(tmp_path / "agent.sh")!r} > /dev/null 2>&1 & a=$!; sleep 3; kill -9 $a; sleep 1.5; '
                        'n1=$(wc -l < "$RUN_DIR/beats"); sleep 3; n2=$(wc -l < "$RUN_DIR/beats"); '
                        'echo "n1=$n1 n2=$n2"', timeout=40)
    n1, n2 = (int(kv.split("=")[1]) for kv in out.stdout.split())
    assert n1 >= 2, out.stdout + out.stderr                  # it did beat while the agent lived
    assert n2 == n1, f"the beat went on after its agent was killed: {n1} -> {n2}"
