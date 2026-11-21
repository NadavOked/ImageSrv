"""#3632 -- the cloner's hello was 400 bad_json on every send: netprobe.json was two writes in one file.

18/11, cloner 2 (metal): every hello that found the netprobe cache missing or stale started its own
35 s background job, and all of them opened the SAME `$RUN_DIR/netprobe.json.tmp` (`cablelive.sh`
aging the cache started more). The first job's `mv` made that name the cache; the next job still held
the same inode and wrote at offset 0 -- when the first answer was longer, its tail stayed:
`...}]}}`. Not JSON, stamped fresh for NETPROBE_TTL; the server rejected every hello.

Rooms run under busybox ash (the agent's shell). The lldpsniff stub answers with a different length
on each call, so two jobs that both run leave a torn file: the negative control for the one-job rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sizelimit
from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, posix
from test_beeps_2067 import BUSYBOX, run_ash
from test_gap_subshell_fd_3524 import prelude

pytestmark = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")

LONG = "sw-" + "a" * 60
GOOD = ',"netprobe":{"cable":null,"lldp":{"switch":"sw1"},"report":{"tcp":[{"host":"10.55.0.1","ok":true}]}}'


def fake_lldpsniff(tmp_path: Path, body: str = "") -> Path:
    """Call 1: long answer after 0.6 s. Later calls: short answer after 1.2 s -- so a SECOND job
    finishes after the first one's mv, which is the whole bug. `body` replaces both answers.
    The call number is claimed with mkdir (atomic): two jobs that start in the same millisecond must not
    both be "call 1" (a read-then-write counter did exactly that, and the control passed for nothing)."""
    path = tmp_path / "lldpsniff"
    answers = (f"printf '%s' '{body}'" if body else
               f"case $n in 1) sleep 0.6; printf '{{\"switch\":\"{LONG}\"}}' ;; *) sleep 1.2; printf '{{\"switch\":\"s\"}}' ;; esac")
    path.write_text(
        '#!/bin/sh\nn=1; until mkdir "$RUN_DIR/lldp.slot.$n" 2>/dev/null; do n=$((n+1)); done\n'
        + answers + '\n: > "$RUN_DIR/lldp.done.$n"\n', encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def room(tmp_path: Path, sniff: Path, body: str, extra: str = "") -> str:
    """The agent's own libraries, the real netprobe_json, a RUN_DIR. `body` is the test."""
    return (prelude(tmp_path) + f'export LLDPSNIFF={posix(sniff)!r} {extra}; '
            + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; ' + agent_sources("netprobe") + body)


#: Bounded wait for every stub that started to have finished, then for the agent's job marker (if any) to go.
SETTLE = r'''
_t=0; while [ "$_t" -lt 100 ]; do
  _c=$(ls "$RUN_DIR" | grep -c '^lldp\.slot\.' || true); _d=$(ls "$RUN_DIR" | grep -c '^lldp\.done\.' || true)
  [ "$_c" -ge 1 ] && [ "$_c" = "$_d" ] && [ ! -e "$RUN_DIR/netprobe.json.running" ] && break
  sleep 0.1; _t=$((_t+1))
done
sleep 0.3
echo count=$(ls "$RUN_DIR" | grep -c '^lldp\.slot\.' || true)
'''


def parsed(fragment: str) -> dict:
    try:
        return json.loads('{"x":0' + fragment + "}")["netprobe"]
    except ValueError as exc:   # what the server's 400 bad_json is
        raise AssertionError(f"not JSON ({exc}): {fragment!r}") from None


def cache_of(out: str) -> str:
    return out.split("CACHE:", 1)[1].strip()


def test_two_hellos_while_the_first_job_runs_leave_one_whole_cache(tmp_path):
    """The reproduction: hello, hello, both before the first job ends, answers of different length."""
    out = run_ash(room(tmp_path, fake_lldpsniff(tmp_path), r'''
netprobe_json > "$RUN_DIR/h1"; netprobe_json > "$RUN_DIR/h2"
echo hello1=$(cat "$RUN_DIR/h1") hello2=$(cat "$RUN_DIR/h2")
''' + SETTLE + r'''echo CACHE:; cat "$RUN_DIR/netprobe.json"
'''), timeout=60)
    assert 'hello1=,"netprobe":{"pending":true} hello2=,"netprobe":{"pending":true}' in out, out
    assert "count=1" in out, "a second lldpsniff job ran while the first was still measuring\n" + out
    assert parsed(cache_of(out))["lldp"] == {"switch": LONG}, out


def test_cablelive_aging_the_cache_mid_job_starts_no_second_job(tmp_path):
    """cablelive.sh line 37 (`rm -f netprobe.json.at`) turns a fresh cache stale: three hellos, three aged
    caches, ONE job -- and the cache is still whole afterwards."""
    cache = ',"netprobe":{"cable":null,"lldp":null,"report":null}'
    out = run_ash(room(tmp_path, fake_lldpsniff(tmp_path), f'''
printf '%s' '{cache}' > "$RUN_DIR/netprobe.json"; : > "$RUN_DIR/netprobe.json.at"
''' + r'''for _i in 1 2 3; do netprobe_json > "$RUN_DIR/h$_i"; rm -f "$RUN_DIR/netprobe.json.at"; done
echo served=$(for _i in 1 2 3; do cat "$RUN_DIR/h$_i"; echo; done | grep -c 'lldp":null')
''' + SETTLE + r'''echo CACHE:; cat "$RUN_DIR/netprobe.json"
'''), timeout=60)
    assert "served=3" in out, "an aged cache is still served while the job measures\n" + out
    assert "count=1" in out, "a hello after the cache was aged started another job\n" + out
    assert parsed(cache_of(out))["lldp"] == {"switch": LONG}, out


def test_a_torn_cache_is_never_sent_and_is_measured_again(tmp_path):
    """The file from the field: a whole fragment with a tail of the longer write after it, stamped fresh."""
    torn = GOOD + '_rc":0}]}}'
    out = run_ash(room(tmp_path, fake_lldpsniff(tmp_path), f'''
printf '%s' '{torn}' > "$RUN_DIR/netprobe.json"; date +%s > "$RUN_DIR/netprobe.json.at"
''' + r'''netprobe_json > "$RUN_DIR/h1"; echo HELLO:; cat "$RUN_DIR/h1"; echo
''' + SETTLE + r'''echo CACHE:; cat "$RUN_DIR/netprobe.json"
'''), timeout=60)
    sent = out.split("HELLO:", 1)[1].split("count=")[0].strip()
    assert parsed(sent) == {"pending": True}, f"a body that is not JSON went into the hello: {sent!r}"
    assert "count=1" in out, out
    assert parsed(cache_of(out))["lldp"] == {"switch": LONG}, "the dropped cache was not measured again\n" + out
    assert "not whole JSON" in (tmp_path / "run" / "agent.log").read_text(encoding="utf-8"), "dropping it must be logged"


def test_a_job_whose_answer_is_not_json_caches_nothing(tmp_path):
    """The job checks what it wrote before the mv: a cut-off lldpsniff answer is `not measured`, not a cache."""
    out = run_ash(room(tmp_path, fake_lldpsniff(tmp_path, body='{"switch":"cut'), r'''
netprobe_json > "$RUN_DIR/h1"
''' + SETTLE + r'''[ -e "$RUN_DIR/netprobe.json" ] && echo cache=yes || echo cache=no
[ -e "$RUN_DIR/netprobe.json.running" ] && echo marker=left || echo marker=gone
netprobe_json
'''), timeout=60)
    assert "cache=no" in out, "a fragment that is not JSON was cached\n" + out
    assert "marker=gone" in out, "a failed job must free the marker, or no job ever runs again\n" + out
    assert out.rstrip().endswith('{"pending":true}'), out
    assert "job failed" in (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")


def test_a_live_marker_blocks_a_second_job_and_a_dead_one_does_not(tmp_path):
    """Guard against over-blocking (passes with and without the lock only for the second half):
    a marker of a job that is running now starts nothing; one older than the ceiling is a dead job."""
    out = run_ash(room(tmp_path, fake_lldpsniff(tmp_path), r'''
_np_mono > "$RUN_DIR/netprobe.json.running"; netprobe_json >/dev/null; sleep 0.5
echo live_count=$(ls "$RUN_DIR" | grep -c '^lldp\.slot\.' || true)
echo $(( $(_np_mono) - 200 )) > "$RUN_DIR/netprobe.json.running"; netprobe_json >/dev/null   # 200 s old (or not a number: uptime < 200 s)
''' + SETTLE + r'''[ -s "$RUN_DIR/netprobe.json" ] && echo cache=yes || echo cache=no
'''), timeout=60)
    assert "live_count=0" in out, "a marker of a running job did not stop a second one\n" + out
    assert "count=1" in out and "cache=yes" in out, "a marker from a dead job blocked the cache for ever\n" + out


@pytest.mark.parametrize("awk", ["system", "busybox"])
def test_whole_check_table(tmp_path, awk):
    """`_np_whole` accepts exactly one ,"netprobe":{...} fragment -- under the system awk and busybox awk."""
    bs = chr(92)
    cases = {
        "whole": (GOOD, True),
        "newline": (GOOD + "\n", True),
        "pending": (',"netprobe":{"pending":true}', True),
        "brace-in-string": (',"netprobe":{"a":"}]{"}', True),
        "escaped-quote": (',"netprobe":{"a":"x' + bs + '"}"}', True),
        "multi-line": (',"netprobe":{' + "\n" + '"a":[1,' + "\n" + '2]}', True),
        "tail-after-whole": (GOOD + '_rc":0}]}}', False),
        "second-object": (GOOD + "{}", False),
        "cut-off": (',"netprobe":{"cable":null,"lldp":{"sw', False),
        "unclosed": (',"netprobe":{"cable":null', False),
        "mismatch": (',"netprobe":{"a":[}]}', False),
        "other-key": ('{"netprobe":{}}', False),
        "not-object": (',"netprobe":null', False),
        "key-only": (',"netprobe":', False),
        "empty": ("", False),
    }
    for i, (text, _want) in enumerate(cases.values()):
        (tmp_path / f"c{i}").write_bytes(text.encode("utf-8"))
    pre = 'awk() { "%s" awk "$@"; }; ' % posix(Path(BUSYBOX)) if awk == "busybox" else ""
    out = run_ash(pre + f'. {posix(AGENT)}/lib/core/common.sh; ' + agent_sources("netprobe")
                  + f'for i in $(seq 0 {len(cases) - 1}); do _np_whole {posix(tmp_path)}/c$i; echo "$i $?"; done', timeout=30)
    got = {int(a): (b == "0") for a, b in (ln.split() for ln in out.splitlines() if ln.count(" ") == 1 and ln.split()[0].isdigit())}
    want = {i: w for i, (_t, w) in enumerate(cases.values())}
    names = list(cases)
    assert {names[i]: v for i, v in got.items()} == {names[i]: v for i, v in want.items()}, out


def test_netcache_is_loaded_by_the_agent_and_stays_small():
    assert '. "$LIB_DIR/netcache.sh"' in (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert sizelimit.assert_within_limit(AGENT / "lib" / "hw" / "netcache.sh") <= 120
