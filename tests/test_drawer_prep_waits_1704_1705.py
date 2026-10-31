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

Every timing here is a **difference against a baseline run** of the same prelude,
with delays the stubs make (``sleep``), so the number is the code's waiting and
not the host's speed. The old code cannot meet any of the bounds: its sleeps
alone exceed them.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_drawer_progress import COUNTING_FANOUT
from test_timeouts import make_stubs, run_sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "waits", "jsonq", "progress", "restore", "expand", "grow", "ntfsunhib", "expandshare", "hostname", "hostname_linux", "drawers", "drawerfeed",
        "verdict", "failmark", "ntfsboot", "writecheck",
        "writemark")   # #1877: finish_grow -> writemark_release (writemark.sh) -> writemark_clear (stubbed)


def _timed(script: str, timeout: int = 90) -> tuple[str, float]:
    started = time.monotonic()
    out = run_sh(script, timeout=timeout)
    return out, time.monotonic() - started


def _extra(prelude: str, body: str, timeout: int = 90) -> tuple[str, float]:
    """The body's own seconds: the same prelude without the body is the baseline."""
    _, base = _timed(prelude + ":", timeout)
    out, spent = _timed(prelude + body, timeout)
    return out, spent - base


def _room(tmp_path: Path, apply_gpt: str, drawers=("sda", "sdb", "sdc"), **env) -> tuple[Path, str]:
    """run_restore_drawers around stubbed table/stream steps; ``apply_gpt`` is the
    stub's body. Everything after the tables (the stream) is a no-op here."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    exports = " ".join(f"{k}={v}" for k, v in env.items())
    prelude = (
        f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev {exports}; "
        + "".join(f". {posix(flat_lib())}/{n}.sh; " for n in LIBS)
        + "log() { :; }; disk_fits() { return 0; }; write_check() { return 0; }; boot_hidden_disk() { return 0; }; "   # #2015
        "write_speed_verdict() { return 0; }; readback_drawers() { :; }; grow_expanded() { return 0; }; writemark_clear() { return 0; }; "   # #1798: the table is stubbed, so is its mark
        f"apply_gpt() {{ {apply_gpt} }}; "
        "expand_last() { sleep 1; return 0; }; "
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


def test_three_drawers_write_their_tables_in_the_time_of_one(tmp_path):
    """Each drawer's table costs 2 s here (apply_gpt 1 s + expand_last 1 s, the
    stubs' sleeps). One after another that is >= 6 s; at once, ~2 s."""
    run, prelude = _room(tmp_path, f'sleep 1; echo "$1 ${{PREP_TAG:-none}}" >> {posix(tmp_path)!r}/calls; return 0;')
    out, extra = _extra(prelude, 'run_restore_drawers multicast http://s img m.json sda sdb sdc; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    assert extra < 4.5, f"{extra:.2f}s for three tables -- one after another is >= 6 s"
    print(f"#1704: three drawers' tables took {extra:.2f}s over the baseline (serial: >= 6 s)")
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
    failed by name. One after another, the whole machine waited for it."""
    run, prelude = _room(tmp_path, '[ "$1" = sdb ] && sleep 40; return 0;',
                         WAIT_DRAWER_S=2)
    out, extra = _extra(prelude, 'run_restore_drawers multicast http://s img m.json sda sdb sdc; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    assert extra < 15, f"{extra:.2f}s -- the hung drawer held the machine"
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


def _waits(tmp_path: Path) -> str:
    box = tmp_path / "box"
    box.mkdir()
    return (f"export RUN_DIR={posix(box)!r}; . {posix(AGENT)}/lib/core/common.sh; "
            f". {posix(AGENT)}/lib/core/waits.sh; log() {{ :; }}; ")


def test_wait_pid_returns_when_the_process_ends_not_a_second_later(tmp_path):
    """Three helpers of 0.2 s: the old loop slept a whole second before it looked
    (>= 3 s); now ~0.6 s. The writecheck of every drawer waits through this."""
    out, extra = _extra(_waits(tmp_path),
                        'for i in 1 2 3; do sleep 0.2 & wait_pid $! 30 helper || echo late; done; echo ok')
    assert out.strip() == "ok", out
    assert extra < 2.0, f"{extra:.2f}s for three 0.2 s helpers (old: >= 3 s)"
    print(f"#1705: three 0.2 s helpers waited {extra:.2f}s over the baseline (old: >= 3 s)")


def test_wait_progress_returns_when_the_stream_ends_not_a_second_later(tmp_path):
    t = posix(tmp_path)
    out, extra = _extra(_waits(tmp_path),
                        f': > {t}/c; for i in 1 2 3; do ( echo 5 >> {t}/c; sleep 0.2 ) & '
                        f'wait_progress $! {t}/c 30 30 stream || echo late; done; echo ok')
    assert out.strip() == "ok", out
    assert extra < 2.0, f"{extra:.2f}s for three 0.2 s streams (old: >= 3 s)"


def test_a_ceiling_is_still_a_ceiling_in_tenths(tmp_path):
    """The ceiling counts what was slept, in tenths: 2 s is ~2 s, never 0.2 s."""
    out, extra = _extra(_waits(tmp_path), 'sleep 30 & wait_pid $! 2 stuck; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    assert 1.5 <= extra <= 5.0, extra


# #2118: a wall-clock bound under one second; under `-n auto` it measured
# 8.2 s and 9.0 s (2/2) and passes alone (3/3). Serial phase, same bound.
@pytest.mark.serial
def test_one_partition_s_own_waiting_in_the_room_is_under_a_second(tmp_path):
    """restore_partition_drawers end to end with a stream that is over at once
    (64 KB, the counting fanout of test_drawer_progress): what is left is the
    waiting between "the bytes ended" and "the next partition may start" -- the
    3 s of every transition on metal. Old: wait_progress alone slept >= 1 s (the
    stream is alive at its first look), then the watcher's and the drawers' waits,
    up to a second each. Measured over two partitions."""
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
        "WAIT_POLL_S=1 WAIT_DRAWER_S=10 WAIT_HELPER_S=10 WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20; "
        + "".join(f". {posix(flat_lib())}/{n}.sh; " for n in
                  ("common", "waits", "progress", "restore", "drawers", "drawerfeed", "verdict", "failmark", "ntfsboot"))
        + "target_init sda 131072; target_init sdb 131072; "
        f"stream_source() {{ cat {posix(payload)!r}; }}; "
    )
    one = (f"restore_partition_drawers unicast http://s img 3 dd part.zst {sha} '' sda sdb "
           f">> {posix(box)}/pipe.out 2>&1 || echo bad; ")
    out, extra = _extra(prelude, one + one + "echo ok")
    assert out.strip().endswith("ok") and "bad" not in out.split(),         out + (box / "pipe.out").read_text(encoding="utf-8", errors="replace")
    per = extra / 2
    print(f"#1705: one partition in the room took {per:.2f}s over the baseline (old: >= 1 s)")
    assert per < 0.9, f"{per:.2f}s a partition -- the old first wait alone was >= 1 s"
