"""Seconds the cloner spent waiting on itself, measured on metal 17/10 (#1704, #1705).

Source: ``C:\\ImageCtl-Lab\\evening-1015\\time-budget-1017.md`` (server journal, TA,
17/10/2026). Two numbers drove this change:

- **#1704** -- manifest -> first udp connection: 13 s with one drawer, 24-25 s with
  two (three rounds). ~11 s per extra drawer. The work is ``apply_gpt`` +
  ``expand_last`` per drawer, one after another; every sgdisk that *writes* sleeps
  1 s inside gdisk's own ``DiskSync`` (diskio-unix.cc: ``sleep(1)`` before
  ``BLKRRPART``), and a Windows image with a recovery tail is ~10 writing calls.
  The drawers' tables are now written at once, one job each.
- **#1705** -- 3 s between two partitions (6/6 transitions), 6-8 s p3->p4. Each wait
  slept a whole ``WAIT_POLL_S`` before it looked: the stream's end (wait_progress),
  the drawer watcher (its own sleep, then wait_pid's), each drawer's pipeline. The
  end of a process is now seen within a 0.1 s tick (``_nap``, waits.sh); the
  sampling of counters, logs and dmesg stays once per ``WAIT_POLL_S``.

#2480: no test here decides by the wall clock. A difference against a baseline
run still measured the host: three tables took 6.70 s on Windows (05/11) against
a 4.5 s bound. Each test now reads what the code itself did -- the tables meet
at a barrier only jobs that run at once can pass; the waits' own ``sleep`` calls
are recorded with their seconds; the hung drawer leaves a mark only if its table
came back. A clock is left only as a ceiling against a hang.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_drawer_progress import COUNTING_FANOUT
from test_timeouts import make_stubs, run_sh
from agentlib import agent_sources

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "waits", "jsonq", "sysinfo", "diskinfo", "progress", "restore", "expand", "grow", "ntfsunhib", "expandshare", "hostname", "hostname_linux", "writeback", "drawers", "drawerfeed",
        "verdict", "failmark", "ntfsboot", "writecheck",
        "writemark")   # #1877: finish_grow -> writemark_release (writemark.sh) -> writemark_clear (stubbed)


def _room(tmp_path: Path, apply_gpt: str, drawers=("sda", "sdb", "sdc"), **env) -> tuple[Path, str]:
    """run_restore_drawers around stubbed table/stream steps; ``apply_gpt`` is the
    stub's body. Everything after the tables (the stream) is a no-op here."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    exports = " ".join(f"{k}={v}" for k, v in env.items())
    prelude = (
        f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev {exports}; "
        + agent_sources(*LIBS)   # #2272: imagesrv-agent's order
        + "log() { :; }; disk_fits() { return 0; }; write_check() { return 0; }; boot_hidden_disk() { return 0; }; "   # #2015
        "write_speed_verdict() { return 0; }; readback_drawers() { :; }; grow_expanded() { return 0; }; writemark_clear() { return 0; }; "   # #1798: the table is stubbed, so is its mark
        f"apply_gpt() {{ {apply_gpt} }}; plan_gate() {{ return 0; }}; "   # #2412: the table is stubbed, so is the check of its plan
        "expand_last() { return 0; }; "
        "manifest_plan() { echo '1|G|win|ntfs|2048|1024|p1.zst|sha|false|UG|'; }; "
        "restore_partition_drawers() { return 0; }; "
        + "".join(f"target_init {d} 100; " for d in drawers)
    )
    return run, prelude


def _state(run: Path, dev: str) -> str:
    return (run / "targets" / dev / "state").read_text(encoding="utf-8").strip()


def _error(run: Path, dev: str) -> str:
    path = run / "targets" / dev / "error"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# --- #1704: the drawers' tables at once ----------------------------------------


#: #2480: a ceiling against a hang, never the verdict -- the tenths of a second
#: a table waits at the barrier for the other two (>= 20 s whatever the host).
BARRIER_TENTHS = 200


def test_three_drawers_write_their_tables_in_the_time_of_one(tmp_path):
    """The tables are written at once (#1704), shown without a clock: each
    drawer's table waits at a barrier until all three have started. Jobs at once
    meet there; one after another, the first waits alone past the barrier's
    ceiling and fails its drawer. (#2302/#2480: this was a wall-clock bound of
    4.5 s over a baseline run -- 6.07 s in a loaded gate, 6.70 s on Windows.)"""
    t = repr(posix(tmp_path))
    run, prelude = _room(tmp_path, (
        f'echo "$1" >> {t}/started; _n=0; '
        f"until [ \"$(awk 'END {{ print NR }}' {t}/started)\" -ge 3 ]; do "
        f'_n=$((_n + 1)); [ "$_n" -lt {BARRIER_TENTHS} ] || {{ PLAN_ERROR="$1 waited alone at the barrier"; return 1; }}; '
        'sleep 0.1; done; '
        f'echo "$1 ${{PREP_TAG:-none}}" >> {t}/calls; return 0;'))
    out = run_sh(prelude + 'run_restore_drawers multicast http://s img m.json sda sdb sdc; echo "rc=$?"',
                 timeout=120)
    assert out.strip().endswith("rc=0"), out
    assert [_error(run, d) for d in ("sda", "sdb", "sdc")] == ["", "", ""], "the tables were not written at once"
    # every drawer's job saw its own scratch-file tag
    calls = sorted((tmp_path / "calls").read_text(encoding="utf-8").split("\n"))
    assert [c for c in calls if c] == ["sda .sda", "sdb .sdb", "sdc .sdc"], calls
    assert [_state(run, d) for d in ("sda", "sdb", "sdc")] == ["done"] * 3


def test_a_drawer_whose_table_is_refused_fails_alone_and_by_its_own_reason(tmp_path):
    """Isolation at once as it was one by one: the job of sdb fails sdb, with the
    plan's own reason; sda and sdc are written, the machine is partial."""
    run, prelude = _room(tmp_path, '[ "$1" = sdb ] && { PLAN_ERROR="הסיבה של sdb"; return 1; }; return 0;')
    out = run_sh(prelude + 'run_restore_drawers multicast http://s img m.json sda sdb sdc; echo "rc=$?"; '
                 'echo "machine=$(_machine_state "sda sdb sdc")"')
    assert out.strip().splitlines()[-2] == "rc=0", out
    assert _state(run, "sdb") == "failed" and _error(run, "sdb") == "הסיבה של sdb"
    assert _state(run, "sda") == "done" and _state(run, "sdc") == "done"
    assert out.strip().splitlines()[-1] == "machine=partial", out
    # #1935: run_restore_drawers leaves 'verifying'; partial is do_restore_drawers' last word
    assert (run / "state").read_text(encoding="utf-8").strip() == "verifying"


def test_a_hung_table_fails_its_drawer_by_name_and_the_others_go_on(tmp_path):
    """A drawer whose sgdisk never returns: past WAIT_DRAWER_S that drawer alone is
    failed by name. One after another, the whole machine waited for it.
    #2480: "did not wait for it" is read from the hung table, not the clock: its
    job is killed at the ceiling, so the line after its sleep is never written. A
    machine that waited it out has that line (the 40 s only end a hang)."""
    t = repr(posix(tmp_path))
    run, prelude = _room(tmp_path, f'[ "$1" = sdb ] && {{ sleep 40; echo back > {t}/sdb.back; }}; return 0;',
                         WAIT_DRAWER_S=5)
    out = run_sh(prelude + 'run_restore_drawers multicast http://s img m.json sda sdb sdc; echo "rc=$?"',
                 timeout=120)
    assert out.strip().endswith("rc=0"), out
    assert not (tmp_path / "sdb.back").exists(), "the machine waited for the hung table to come back"
    assert _state(run, "sdb") == "failed"
    assert "פג הזמן — טבלת המחיצות" in _error(run, "sdb"), _error(run, "sdb")
    assert _state(run, "sda") == "done" and _state(run, "sdc") == "done"


def test_two_drawers_planning_at_once_do_not_read_each_other_s_plan(tmp_path):
    """manifest_plan materialises the plan to a file (#51) named per process
    (``plan.$$``, the kiosk). A subshell keeps the parent's ``$$``: two drawers'
    jobs wrote ONE file, and each could ``cat`` the other's plan -- the other
    drawer's table. PREP_TAG makes it one file per drawer. A slow jq makes the
    overlap certain: two lines of the same length at the same offsets."""
    run = tmp_path / "run"
    run.mkdir()
    (tmp_path / "m1").write_text("a1\na2\na3\n", encoding="utf-8", newline="\n")
    (tmp_path / "m2").write_text("b1\nb2\nb3\n", encoding="utf-8", newline="\n")
    t = posix(tmp_path)
    out = run_sh(
        f"export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
        "log() { :; }; "
        'jq() { case "$2" in *length*) awk \'END { print NR }\' "$3" ;; '
        '*) while read -r l; do echo "$l"; sleep 0.3; done < "$3" ;; esac; }; '
        f"( PREP_TAG=.sda; manifest_plan {t}/m1 > {t}/o1; echo $? > {t}/r1 ) & "
        f"( sleep 0.1; PREP_TAG=.sdb; manifest_plan {t}/m2 > {t}/o2; echo $? > {t}/r2 ) & "
        "wait; echo finished"
    )
    assert out.strip().endswith("finished"), out
    assert (tmp_path / "r1").read_text().strip() == "0" and (tmp_path / "r2").read_text().strip() == "0"
    assert (tmp_path / "o1").read_text() == "a1\na2\na3\n", "sda read another drawer's plan"
    assert (tmp_path / "o2").read_text() == "b1\nb2\nb3\n", "sdb read another drawer's plan"


def test_every_scratch_file_of_the_table_step_is_per_drawer():
    """The files apply_gpt's call tree writes under RUN_DIR (the plan, the gate,
    the preflight's plan and sparse disk, the table read-back) all carry PREP_TAG.
    One left shared is the race above on another file."""
    restore = (AGENT / "lib" / "restore" / "restore.sh").read_text(encoding="utf-8")
    expand = (AGENT / "lib" / "restore" / "expand.sh").read_text(encoding="utf-8")
    for needle, src in (('plan.$$${PREP_TAG:-}.txt', restore), ('preflight${PREP_TAG:-}.plan', restore),
                        ('preflight${PREP_TAG:-}.disk', restore), ('table${PREP_TAG:-}.plan', expand),
                        ('gate${PREP_TAG:-}.plan', expand)):
        assert needle in src, needle
    assert '"$RUN_DIR/gate.plan"' not in expand and '"$RUN_DIR/table.plan"' not in expand


# --- #1705: a process's end is seen at once --------------------------------------


def _waits(tmp_path: Path, *, real: bool = True, poll_s: int = 10) -> str:
    """waits.sh with a `sleep` that records every sleep the code asks for (#2480,
    as #2457 below): the waiting is read from the code, not the clock. What the
    tests wait ON uses `command sleep`, so only the waits are in the record.
    real=False records without sleeping. poll_s = WAIT_POLL_S (10 by default,
    as WAITS_POLL_S below: a wait that sleeps a period out is then >= 10 s)."""
    box = tmp_path / "box"
    box.mkdir()
    then = 'command sleep "$@"' if real else ":"
    return (f"export RUN_DIR={posix(box)!r} WAIT_POLL_S={poll_s}; . {posix(AGENT)}/lib/core/common.sh; "
            f". {posix(AGENT)}/lib/core/waits.sh; log() {{ :; }}; "
            f'sleep() {{ echo "$1" >> {posix(box)!r}/slept; {then}; }}; ')


def _slept(tmp_path: Path) -> list[float]:
    f = tmp_path / "box" / "slept"
    slept = [float(s) for s in f.read_text(encoding="utf-8").split()] if f.exists() else []
    assert slept, "no wait slept at all -- the recording broke, not a pass"
    return slept


def _looked_every_tick(tmp_path: Path, what: str) -> None:
    """Every sleep is one tick, and all of them together are less than ONE poll
    period: a wait that sleeps a period out before it looks is >= WAITS_POLL_S
    alone. Ticks while a helper is still alive are honest waiting; a slow host
    adds a few, never a period."""
    slept = _slept(tmp_path)
    assert max(slept) <= 0.1, f"{what}: a sleep of {max(slept)} s at once -- a wait sleeps a tick, then looks"
    assert sum(slept) < WAITS_POLL_S, f"{what}: {sum(slept):.1f}s of ticks -- a wait slept a poll period out"
    print(f"#1705: {what} slept {sum(slept):.1f}s in {len(slept)} ticks (one poll period: {WAITS_POLL_S} s)")


def test_wait_pid_returns_when_the_process_ends_not_a_second_later(tmp_path):
    """Three helpers of 0.2 s: the old loop slept a whole poll period before it
    looked. The writecheck of every drawer waits through this."""
    out = run_sh(_waits(tmp_path)
                 + 'for i in 1 2 3; do command sleep 0.2 & wait_pid $! 30 helper || echo late; done; echo ok')
    assert out.strip() == "ok", out
    _looked_every_tick(tmp_path, "three 0.2 s helpers")


def test_wait_progress_returns_when_the_stream_ends_not_a_second_later(tmp_path):
    t = posix(tmp_path)
    out = run_sh(_waits(tmp_path)
                 + f': > {t}/c; for i in 1 2 3; do ( echo 5 >> {t}/c; command sleep 0.2 ) & '
                 f'wait_progress $! {t}/c 30 30 stream || echo late; done; echo ok')
    assert out.strip() == "ok", out
    _looked_every_tick(tmp_path, "three 0.2 s streams")


def test_a_ceiling_is_still_a_ceiling_in_tenths(tmp_path):
    """The ceiling counts what was slept, in tenths: 2 s is 20 ticks of 0.1 s,
    never 2. Read from the sleeps the wait asked for (recorded, not slept), at
    the default WAIT_POLL_S of 1: the ceiling is looked at once a period."""
    out = run_sh(_waits(tmp_path, real=False, poll_s=1) + 'command sleep 30 & wait_pid $! 2 stuck; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert _slept(tmp_path) == [0.1] * 20


# #2457: this was a wall-clock bound (< 0.9 s a partition over a baseline run).
# Under load it measured 8.2/9.0 s (#2118, `-n auto`) and failed again in the
# serial phase (05/11, an ISO build and QEMU on the testrunner); 6.1 s on an idle
# Windows. The host's speed, not the code's waiting. Now the waiting is read from
# the code itself: every `sleep` it asks for, by caller -- no clock at all.
WAITS_POLL_S = 10   # one poll period; a wait that sleeps one out is >= 10 s of sleep


def test_one_partition_s_own_waiting_in_the_room_is_under_a_second(tmp_path):
    """restore_partition_drawers end to end with a stream that is over at once
    (64 KB, the counting fanout of test_drawer_progress): what is left is the
    waiting between "the bytes ended" and "the next partition may start" -- the
    3 s of every transition on metal. Old: every wait slept a whole WAIT_POLL_S
    before it looked (wait_progress, the watcher, the drawers' wait_pid), so at
    WAIT_POLL_S=1 a partition waited >= 1 s whatever the host.

    The proof, by what the code asked to sleep (a `sleep` that records its caller
    and its seconds) at WAIT_POLL_S=10 -- two partitions:
    1. every sleep of the waits is one tick (<= 0.1 s), never a poll period;
    2. all their ticks together are less than ONE poll period: a wait that ticks
       a period out without looking (a `_nap` with no kill -0) is >= 10 s alone.
    Ticks while a process is still alive are honest waiting; a slow host adds a
    few of them (3.0 s in all on a loaded Windows, 05/11), never a period."""
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(b"imagesrv" * 8192)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    for dev in ("sda", "sdb"):
        (run / "targets" / dev).mkdir(parents=True)
    prelude = (
        make_stubs(box / "stubs", {"fanout": COUNTING_FANOUT})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)!r} "
        f"WAIT_POLL_S={WAITS_POLL_S} WAIT_DRAWER_S=60 WAIT_HELPER_S=60 WAIT_STREAM_START_S=60 WAIT_STREAM_STALL_S=60; "
        + agent_sources("common", "sysinfo", "diskinfo", "waits", "progress", "restore", "writeback", "drawers", "drawerfeed", "verdict",
                        "failmark", "ntfsboot")   # #2272: imagesrv-agent's order
        + "target_init sda 131072; target_init sdb 131072; "
        f"stream_source() {{ cat {posix(payload)!r}; }}; "
        # every sleep the code asks for, with the function that asked (bash's FUNCNAME;
        # subshells inherit it); the real sleep still runs
        f'sleep() {{ printf "%s %s\\n" "${{FUNCNAME[1]:-main}}" "$1" >> {posix(box)!r}/slept; command sleep "$@"; }}; '
    )
    one = (f"restore_partition_drawers unicast http://s img 3 dd part.zst {sha} '' sda sdb "
           f">> {posix(box)}/pipe.out 2>&1 || echo bad; ")
    out = run_sh(prelude + one + one + "echo ok", timeout=180)
    assert out.strip().endswith("ok") and "bad" not in out.split(),         out + (box / "pipe.out").read_text(encoding="utf-8", errors="replace")
    slept = [line.split() for line in (box / "slept").read_text(encoding="utf-8").splitlines()]
    # writeback_watch's fsync period runs beside the pipe and nobody waits on it
    # (the pipe kills it at its end) -- not waiting, so not counted
    waits = [(who, float(s)) for who, s in slept if who != "writeback_watch"]
    assert waits, f"no wait slept at all -- the recording broke, not a pass: {slept}"
    longest = max(waits, key=lambda w: w[1])
    assert longest[1] <= 0.1, f"{longest[0]} slept {longest[1]} s at once -- a wait sleeps a tick, then looks"
    total = sum(s for _, s in waits)
    print(f"#1705: the room's waits slept {total:.1f}s in {len(waits)} ticks over two partitions "
          f"(one poll period: {WAITS_POLL_S} s)")
    assert total < WAITS_POLL_S, f"{total:.1f}s of ticks over two partitions -- a wait slept a poll period out"
