"""#3636 -- cable-live.json reached the hello only after NETPROBE_TTL when a netprobe job was already running.

`cable_live_check` (cablelive.sh) ends with `rm -f netprobe.json.at`, to age the netprobe cache so the
next hello carries cable-live.json. Since #3632 only one job runs at a time -- and a job that was
started BEFORE the live test measured the cable without its result, then wrote a fresh `.at` when it ended
(35 s of LLDP later). The `rm` was lost: the cache without the result counted as fresh for 900 s.

Now cable_live_check also writes `$RUN_DIR/netprobe.json.stale` (epoch); a job that started at or before it
leaves the cache aged (netcache.sh `_np_stamp`), so the next hello measures again; a job that started after
it stamps the cache and clears the flag.

Rooms run under busybox ash. The lldpsniff stub claims its slot (atomic) AFTER the job has read
cable-live.json (`_np_cable` runs before `_np_lldp`), so waiting for the slot orders the events.
"""

from __future__ import annotations

from pathlib import Path

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, posix
from test_beeps_2067 import BUSYBOX, run_ash
from test_gap_subshell_fd_3524 import prelude
from test_netprobe_cache_3632 import SETTLE, parsed

pytestmark = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")

OPEN_A = {"status": "open", "pairs": [{"pair": "A", "code": "Open", "length_m": 3}]}

#: #3663: the agent no longer decides whether the live link is tested -- the server's verdict (hello answer, link_verdict.cable_test) does.
RUN_VERDICT = '''echo '{"link_verdict":{"cable_test":"RUN","cable_why":"slow: 100Mb/s"}}' > "$RUN_DIR/response.json"; '''


def stubs(tmp_path: Path) -> tuple[Path, Path]:
    """(lldpsniff: claims a slot, measures 0.8 s, answers; ethtool: one open pair)."""
    sniff = tmp_path / "lldpsniff"
    sniff.write_text('#!/bin/sh\nn=1; until mkdir "$RUN_DIR/lldp.slot.$n" 2>/dev/null; do n=$((n+1)); done\n'
                     'sleep 0.8; printf \'{"switch":"sw"}\'\n: > "$RUN_DIR/lldp.done.$n"\n', encoding="utf-8", newline="\n")
    ethtool = tmp_path / "ethtool"
    ethtool.write_text('#!/bin/sh\necho "Pair A code Open, length 3m"\n', encoding="utf-8", newline="\n")
    sniff.chmod(0o755)
    ethtool.chmod(0o755)
    return sniff, ethtool


def room(tmp_path: Path, body: str) -> str:
    sniff, ethtool = stubs(tmp_path)
    nic = tmp_path / "sys/class/net/eth0"
    return (prelude(tmp_path) + f'mkdir -p {posix(nic)!r}; echo 1 > {posix(nic)!r}/carrier; echo 100 > {posix(nic)!r}/speed; ' + RUN_VERDICT
            + f'export LLDPSNIFF={posix(sniff)!r} ETHTOOL={posix(ethtool)!r} D_ROLE=cloner D_SESSION_STATE=none D_TASK=null; '
            + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
            + agent_sources("netprobe", "cablelive") + body)


#: Until the job has read cable-live.json (it claims the lldp slot after `_np_cable`), at most 5 s.
JOB_READ_IT = '_t=0; until [ -d "$RUN_DIR/lldp.slot.1" ] || [ "$_t" -ge 50 ]; do sleep 0.1; _t=$((_t+1)); done\n'


def test_a_job_started_before_the_live_test_does_not_stamp_the_cache_fresh(tmp_path):
    """hello (job A starts, link up: `skipped`) -> the idle cloner tests the live link -> A ends -> the next
    hello still serves the old cache but measures again -> the one after carries cable-live.json."""
    out = run_ash(room(tmp_path, r'''
netprobe_json > "$RUN_DIR/h1"
''' + JOB_READ_IT + r'''cable_live_check
''' + SETTLE + r'''[ -e "$RUN_DIR/netprobe.json.at" ] && echo at=yes || echo at=no
sleep 1.1   # the flag and the jobs are stamped in whole seconds: hello 2 must start in a later one than the flag
netprobe_json > "$RUN_DIR/h2"
''' + SETTLE + r'''[ -e "$RUN_DIR/netprobe.json.stale" ] && echo stale=left || echo stale=cleared
[ -s "$RUN_DIR/netprobe.json.at" ] && echo at2=yes || echo at2=no
echo HELLO3:; netprobe_json; echo
'''), timeout=60)
    assert "at=no" in out, "the job that ended after the live test stamped the cache fresh: cable-live.json waits NETPROBE_TTL\n" + out
    assert "count=2" in out, "no second measurement after the live test\n" + out
    assert "stale=cleared" in out and "at2=yes" in out, out
    third = parsed(out.split("HELLO3:", 1)[1].strip())
    assert third["cable"] == OPEN_A, "the hello after the live test does not carry cable-live.json\n" + out


def test_a_flag_that_cannot_be_read_ages_the_cache(tmp_path):
    """Principle 5: not knowing whether the job was older than the live test is not 'it was newer'."""
    out = run_ash(room(tmp_path, r'''
printf 'junk' > "$RUN_DIR/netprobe.json.stale"
netprobe_json > "$RUN_DIR/h1"
''' + SETTLE + r'''[ -e "$RUN_DIR/netprobe.json.at" ] && echo at=yes || echo at=no
[ -s "$RUN_DIR/netprobe.json" ] && echo cache=yes || echo cache=no
'''), timeout=60)
    assert "cache=yes" in out and "at=no" in out, out


def test_a_job_started_after_the_flag_stamps_the_cache_and_clears_it(tmp_path):
    """Passes only with the flag handled, and shows it does not block for ever: an old flag, a new job."""
    out = run_ash(room(tmp_path, r'''
echo 1 > "$RUN_DIR/netprobe.json.stale"
netprobe_json > "$RUN_DIR/h1"
''' + SETTLE + r'''[ -s "$RUN_DIR/netprobe.json.at" ] && echo at=yes || echo at=no
[ -e "$RUN_DIR/netprobe.json.stale" ] && echo stale=left || echo stale=cleared
'''), timeout=60)
    assert "at=yes" in out and "stale=cleared" in out and "count=1" in out, out
