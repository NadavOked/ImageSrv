"""#1409 -- every condition of the wipe guard is pinned on its own.

The table of a failed target is wiped only when this run provably wrote *that* disk
(``wipe_written_gpt``, failmark.sh): ``write_started`` holds the serial recorded at the
write, the serial now must be the same, and the disk must still be the one ``target_init``
bound (``target_same``: serial, size, slot). The station adds ``evidence_only`` -- it records
the evidence and never wipes -- and ``target_init`` clears a ``write_started`` left by an
earlier run, so a disk that was merely chosen has none.

The two older guards of the Issue are not these: test_no_wipe_before_write_1409.py drives
the real ``lc_run`` and its pre-write refusals, test_refused_plan_keeps_table_1409.py the
refused plan of a round and a station. A mutation run (23/11, #1409) showed what they leave
open: the serial comparison, ``target_same``, ``evidence_only``, the "wiped once" rule and
``target_init``'s clearing of ``write_started`` each survived being removed, because the
other guards in the same function failed first (a test without ``target_bind`` is refused by
``target_same`` alone, so the serial line was never reached). Here every test binds the disk
and then breaks exactly one thing.

Negative control: one mutation per guard, see the PR.
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import BASH, posix
from test_failmark import fail_run, sgdisk_calls, zap_calls
from test_local_clone_agent import run
from test_no_wipe_before_write_1409 import HARNESS, ZAP_HE
from test_timeouts import log_of

pytestmark = requires_native(("bash", BASH))

SERIAL = "S3TWNE0JB04745"   # sda in test_failmark.IDENT, port 1; sdb is SB, port 2
WS = '"$RUN_DIR/targets/sda/write_started"'


def zaps_of(box, dev="sda"):
    return [c for c in zap_calls(box) if c.endswith(f"/dev/{dev}")]


def test_a_bound_disk_that_was_written_is_wiped_once_and_only_it(tmp_path):
    """The control of everything below: the guard lets the one right case through."""
    box, run_dir, out = fail_run(tmp_path, script=(
        'mkdir -p "$RUN_DIR/targets/sdb"; echo writing > "$RUN_DIR/targets/sdb/state"; echo SB > "$RUN_DIR/targets/sdb/write_started"; '
        'target_bind sda; target_bind sdb; fail_written_target sda "cut"; echo "rc=$?"'))
    assert out.strip().endswith("rc=0"), out
    assert zap_calls(box) == [f"--zap-all {posix(box)}/dev/sda"], sgdisk_calls(box)
    assert (run_dir / "targets" / "sdb" / "state").read_text().strip() == "writing"   # the neighbour is not touched


def test_a_disk_whose_write_started_names_another_serial_is_not_wiped(tmp_path):
    """The serial comparison: bound and present, but what was recorded at the write is not this disk."""
    box, run_dir, _ = fail_run(tmp_path, script=(
        f'target_bind sda; printf "SOMEONE-ELSE\\n" > {WS}; fail_written_target sda "cut"'))
    assert sgdisk_calls(box) == [], sgdisk_calls(box)
    log = log_of(run_dir)
    assert "GPT NOT wiped" in log and "SOMEONE-ELSE" in log, log   # and says so (B-DSK-06)


@pytest.mark.parametrize("what,change", [
    ("the slot", 'disk_port() { printf 7; }'),
    ("the size", 'echo 123 > "$SYSROOT/sys/block/sda/size"'),
])
def test_the_same_serial_in_another_slot_or_size_is_not_wiped(tmp_path, what, change):
    """target_same: the serial matches what the write recorded, but it is not the disk that
    target_init bound (#2434/#2448) -- a twin that moved, or a disk of another size."""
    box, run_dir, _ = fail_run(tmp_path, script=(
        'mkdir -p "$SYSROOT/sys/block/sda"; echo 100 > "$SYSROOT/sys/block/sda/size"; target_bind sda; '
        f'{change}; fail_written_target sda "cut"'))
    assert sgdisk_calls(box) == [], (what, sgdisk_calls(box))
    assert "GPT NOT wiped" in log_of(run_dir)


def test_a_disk_nothing_was_written_to_says_nothing_and_is_not_wiped(tmp_path):
    """No write_started: no wipe, and no "keeps a partial image" -- there is no image on it."""
    box, run_dir, _ = fail_run(tmp_path, write_started=False, script=(
        'target_bind sda; fail_written_target sda "refused"'))
    assert sgdisk_calls(box) == [], sgdisk_calls(box)
    log = log_of(run_dir)
    assert "GPT NOT wiped" not in log and "wiped primary" not in log, log


def test_a_station_target_keeps_its_evidence_and_is_never_wiped(tmp_path):
    """evidence_only (run_restore): bound, written, same serial -- and still not wiped."""
    box, run_dir, _ = fail_run(tmp_path, script=(
        'target_bind sda; : > "$RUN_DIR/targets/sda/evidence_only"; fail_written_target sda "cut"'))
    assert sgdisk_calls(box) == [], sgdisk_calls(box)
    assert (run_dir / "targets" / "sda" / "ata_log.json").exists(), "the evidence is still collected"


def test_a_target_that_already_failed_is_not_wiped_a_second_time(tmp_path):
    box, _, _ = fail_run(tmp_path, script=(
        'target_bind sda; fail_written_target sda "first"; fail_written_target sda "second"'))
    assert zap_calls(box) == [f"--zap-all {posix(box)}/dev/sda"], sgdisk_calls(box)


# --- the local clone: a write_started left by an earlier run ------------------------

def lc_extra(tmp_path, prefix: str, targets: str, **env):
    out = run(tmp_path, HARNESS + prefix + f'lc_run sdc "{targets}" 0; '
              'for d in sda sdb sdc; do [ -f "$RUN_DIR/targets/$d/state" ] && '
              'echo "$d=$(cat "$RUN_DIR/targets/$d/state")|$(cat "$RUN_DIR/targets/$d/error" 2>/dev/null)"; done; :', **env)
    assert out.returncode == 0, out.stderr
    calls_file = tmp_path / "run" / "sgdisk.calls"
    calls = calls_file.read_text(encoding="utf-8").splitlines() if calls_file.exists() else []
    states = dict(line.split("=", 1) for line in out.stdout.splitlines() if line[:3] in ("sda", "sdb", "sdc"))
    return states, calls, out


def test_a_leftover_write_started_does_not_make_a_chosen_disk_wipeable(tmp_path):
    """target_init clears it. sdb wrote in an earlier job (its serial is still there), is chosen
    now, and does not fit: refused before any write -- the table stays."""
    d = tmp_path / "run" / "targets" / "sdb"
    d.mkdir(parents=True)
    (d / "write_started").write_text("S-sdb\n", newline="\n")
    states, calls, out = lc_extra(tmp_path, "", "sdb", VERDICT="short")
    assert states["sdb"].startswith("failed|") and states["sdb"].endswith("— לא נכתב"), (states, out.stderr)
    assert calls == [], calls


def test_a_target_missing_from_the_inventory_is_failed_and_never_written(tmp_path):
    states, calls, out = lc_extra(tmp_path, 'rm -f "$LC/inv/sdb.line"; ', "sdb")
    assert states["sdb"] == f"failed|{ZAP_HE}", (states, out.stderr)
    assert calls == [], calls


def test_a_target_without_a_serial_is_not_the_disk_that_was_checked(tmp_path):
    """An empty serial on both sides is not "the same disk" (#2429)."""
    noserial = ('disk_serial() { case "$1" in sdb) ;; *) printf "S-%s" "$1" ;; esac; }; '
                'lc_inv_field() { case "$2" in 4) [ "$1" = sdb ] || printf "S-%s" "$1" ;; 12) echo 1 ;; *) echo ;; esac; }; ')
    states, calls, out = lc_extra(tmp_path, noserial, "sdb")
    assert states["sdb"] == f"failed|{ZAP_HE}", (states, out.stderr)
    assert calls == [], calls


def test_a_target_that_moved_slot_after_it_was_chosen_is_failed_and_never_written(tmp_path):
    """Same serial, but target_init bound it in slot 5 and it answers from slot 9 now.
    lc_inv_field is first asked right after target_init, so it flips the slot."""
    moved = ('disk_port() { if [ -f "$RUN_DIR/moved" ]; then printf 9; else printf 5; fi; }; '
             'lc_inv_field() { : > "$RUN_DIR/moved"; case "$2" in 4) printf "S-%s" "$1" ;; 12) echo 1 ;; *) echo ;; esac; }; ')
    states, calls, out = lc_extra(tmp_path, moved, "sdb")
    assert states["sdb"] == f"failed|{ZAP_HE}", (states, out.stderr)
    assert calls == [], calls
