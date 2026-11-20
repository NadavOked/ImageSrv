"""#3652 -- the netprobe job stamps (`.running`, `.at`, `.stale`) were wall-clock, and the wall clock jumps.

18/11, cloner 2 (metal), cold boot: the first hello is sent on the RTC time, and only its answer sets the clock from
the server (`clock: set from the server, was 835s off`). The first netprobe job starts BETWEEN the two, so
`netprobe.json.running` and `netprobe.json.at` carried a time 738 s in the future. Read as real time afterwards:
a job that died was not restarted until the clock caught up (age < 0 is never "> the ceiling"), the cache counted as
fresh for NETPROBE_TTL plus the jump, and `_np_stamp` compared a job start from one clock with a `.stale` flag written on
the other (#3636 again).

Now every stamp is whole seconds of /proc/uptime (netcache.sh `_np_mono`): it does not jump, and /run is empty at each boot.
A stamp that is not usable -- missing, not a number, or in the future of the clock that wrote it -- is not "fresh" and not
"alive" (principle 5). No readable uptime = no job and nothing counted fresh, said in the log.

Rooms run under busybox ash. `date` is a function that adds `$RUN_DIR/clock.offset`; the lldpsniff stub moves it by the
numbers measured in the issue (835 s) while the first job is running, after the job was stamped.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, posix
from test_beeps_2067 import BUSYBOX, run_ash
from test_gap_subshell_fd_3524 import prelude
from test_netprobe_cache_3632 import SETTLE, parsed

pytestmark = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")

JUMP_S = 835
OPEN_A = {"status": "open", "pairs": [{"pair": "A", "code": "Open", "length_m": 3}]}

#: #3663: the agent no longer decides whether the live link is tested -- the server's verdict (hello answer, link_verdict.cable_test) does.
RUN_VERDICT = '''echo '{"link_verdict":{"cable_test":"RUN","cable_why":"slow: 100Mb/s"}}' > "$RUN_DIR/response.json"; '''

#: `date +%s` on a clock that the stub moves; every other `date` call is the real one.
CLOCK = r'''
date() { if [ "$1" = "+%s" ]; then echo $(( $(command date +%s) + $(cat "$RUN_DIR/clock.offset" 2>/dev/null || echo 0) )); else command date "$@"; fi; }
'''
#: Until the first job has moved the clock (it does so right after claiming its slot), at most 5 s.
JUMPED = '_t=0; until [ -e "$RUN_DIR/clock.jumped" ] || [ "$_t" -ge 50 ]; do sleep 0.1; _t=$((_t+1)); done\n'


def room(tmp_path: Path, body: str, offset: int, nap: float, extra: str = "") -> str:
    """The first lldpsniff call moves the clock by `offset` seconds and measures for `nap`; later calls take 0.2 s."""
    sniff = tmp_path / "lldpsniff"
    sniff.write_text('#!/bin/sh\nn=1; until mkdir "$RUN_DIR/lldp.slot.$n" 2>/dev/null; do n=$((n+1)); done\n'
                     f'if [ "$n" = 1 ]; then echo {offset} > "$RUN_DIR/clock.offset"; : > "$RUN_DIR/clock.jumped"; sleep {nap}; '
                     'else sleep 0.2; fi\nprintf \'{"switch":"sw"}\'\n: > "$RUN_DIR/lldp.done.$n"\n', encoding="utf-8", newline="\n")
    ethtool = tmp_path / "ethtool"
    ethtool.write_text('#!/bin/sh\necho "Pair A code Open, length 3m"\n', encoding="utf-8", newline="\n")
    sniff.chmod(0o755)
    ethtool.chmod(0o755)
    nic = tmp_path / "sys/class/net/eth0"
    return (prelude(tmp_path) + f'mkdir -p {posix(nic)!r}; echo 1 > {posix(nic)!r}/carrier; echo 100 > {posix(nic)!r}/speed; ' + RUN_VERDICT
            + f'export LLDPSNIFF={posix(sniff)!r} ETHTOOL={posix(ethtool)!r} D_ROLE=cloner D_SESSION_STATE=none D_TASK=null {extra}; '
            + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
            + agent_sources("netprobe", "cablelive") + CLOCK + body)


def test_a_job_that_outlives_the_ceiling_is_restarted_after_the_clock_jumped_back(tmp_path):
    """The issue's case: the clock goes back 835 s after the job started. Its marker is now 'in the future'
    and, read as wall time, the job could not be called dead until the clock caught up -- ~14 minutes."""
    out = run_ash(room(tmp_path, r'''
netprobe_json > "$RUN_DIR/h1"
''' + JUMPED + r'''sleep 3
netprobe_json > "$RUN_DIR/h2"
''' + SETTLE, -JUMP_S, 5, "NETPROBE_JOB_MAX_S=2"), timeout=60)
    assert "count=2" in out, f"no second job: a marker from before the clock moved back {JUMP_S} s still counts as alive\n" + out


def test_a_cache_older_than_the_ttl_is_measured_again_after_the_clock_jumped_back(tmp_path):
    """`.at` written by a job that straddled the jump: with wall time its age is negative -- 'fresh' for TTL + 835 s."""
    out = run_ash(room(tmp_path, r'''
netprobe_json > "$RUN_DIR/h1"
''' + SETTLE + r'''echo AFTER_FIRST
sleep 3
netprobe_json > "$RUN_DIR/h2"
''' + SETTLE, -JUMP_S, 0.6, "NETPROBE_TTL=2"), timeout=60)
    assert "count=1" in out.split("AFTER_FIRST", 1)[0], out
    assert out.rstrip().endswith("count=2"), "the cache stayed 'fresh' past its TTL after the clock moved back\n" + out


def test_a_clock_that_jumps_forward_does_not_kill_the_running_job(tmp_path):
    """The RTC behind, the server ahead: +835 s while the job runs. With wall time the live job's marker was 'older than the
    ceiling' at once and a second job started on top of it (#3632's rule, broken by the clock)."""
    out = run_ash(room(tmp_path, r'''
netprobe_json > "$RUN_DIR/h1"
''' + JUMPED + r'''netprobe_json > "$RUN_DIR/h2"
''' + SETTLE, JUMP_S, 2), timeout=60)
    assert "count=1" in out, "a second job ran while the first was still measuring: the clock moved, the job did not\n" + out


def test_a_live_test_after_the_jump_still_ages_the_cache_the_running_job_stamps(tmp_path):
    """#3636 across the jump: job started, clock back 835 s, cable_live_check writes the flag, the job ends.
    Flag and job start must be on the same clock, or the job counts as newer than the flag and stamps the cache fresh."""
    out = run_ash(room(tmp_path, r'''
netprobe_json > "$RUN_DIR/h1"
''' + JUMPED + r'''cable_live_check
''' + SETTLE + r'''[ -e "$RUN_DIR/netprobe.json.at" ] && echo at=yes || echo at=no
sleep 1.1
netprobe_json > "$RUN_DIR/h2"
''' + SETTLE + r'''echo HELLO3:; netprobe_json; echo
''', -JUMP_S, 1), timeout=60)
    assert "at=no" in out, "the job that ended after the live test stamped the cache fresh: cable-live.json waits NETPROBE_TTL\n" + out
    assert "count=2" in out, "no second measurement after the live test\n" + out
    assert parsed(out.split("HELLO3:", 1)[1].strip())["cable"] == OPEN_A, out


@pytest.mark.parametrize("stamp", ["netprobe.json.at", "netprobe.json.running"])
def test_a_stamp_in_the_future_is_neither_fresh_nor_alive(tmp_path, stamp):
    """Principle 5: a stamp that cannot be right (later than now on the clock that wrote it) is not 'fresh' and not a live job."""
    out = run_ash(room(tmp_path, r'''
printf ',"netprobe":{"cable":null,"lldp":null,"report":null}' > "$RUN_DIR/netprobe.json"
echo 99999999999 > "$RUN_DIR/''' + stamp + r'''"
netprobe_json > "$RUN_DIR/h1"
''' + SETTLE, 0, 0.3), timeout=60)
    assert "count=1" in out, f"{stamp} from the future was believed\n" + out


def test_without_a_readable_uptime_no_job_starts_and_nothing_is_counted_fresh(tmp_path):
    """No clock to stamp with: say so, start nothing (a stamp from `date` is what #3652 is), serve what there is."""
    out = run_ash(room(tmp_path, r'''
netprobe_json > "$RUN_DIR/h1"; echo hello1=$(cat "$RUN_DIR/h1")
sleep 0.5
echo slots=$(ls "$RUN_DIR" | grep -c '^lldp\.slot\.' || true)
[ -e "$RUN_DIR/netprobe.json.running" ] && echo marker=yes || echo marker=no
''', 0, 0.3, "UPTIME_FILE=/nonexistent/uptime"), timeout=60)
    assert '"pending":true' in out, out
    assert "slots=0" in out and "marker=no" in out, "a job was started without a clock to stamp it with\n" + out
    assert "no uptime" in out, "the missing clock was not logged\n" + out
