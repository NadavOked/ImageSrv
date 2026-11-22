"""#2647 -- the plan's second check, in ``apply_gpt``, after the write check wrote the disk.

Since #2412 the plan is checked twice: once in ``plan_gate`` (before the write check
and the TRIM), once inside ``apply_gpt`` (the last gate before ``--zap-all``). Every
``PLAN_ERROR`` ends "-- the disk was not changed" / "-- not a byte was written". When
the *second* check fails, the write check has already written the disk, so that
message lies: the plan itself is deterministic, so it can fail there only because the
disk stopped answering (``blockdev --getss`` / ``--getsz``) between the write check and
the table -- a disk that drops off the line during the TRIM.

Done when:
  * the second check fails and ``write_started`` exists for the target -> the message
    says the disk stopped answering after the write check, and on the station the
    failure goes through ``fail_written_target`` (ATA evidence), not ``target_set``;
  * it fails with NO ``write_started`` (local clone, ``plan_gate``) -> today's
    "not changed" message stays, and nothing is collected.

Real here: ``run_restore`` / ``run_restore_drawers``, ``plan_gate``, ``apply_gpt``,
``plan_preflight``, ``validate_restore_plan``, ``fail_written_target``, the write
check's body. The disk that "stops answering" is the box's ``blockdev`` stub losing its
answer file at the moment the real TRIM step would run (``discard_before_restore`` is
replaced by that one act) -- the harness of test_refused_plan_keeps_table_1409.py.

Negative control: expand.sh's ``apply_gpt`` back to ``plan_preflight "$1" "$2" || return 1``.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import BASH, posix, sh
from test_refused_plan_keeps_table_1409 import (
    SERIAL, UNTOUCHED_HE, box, destroyed, round_run, target, zaps)
from test_restore_evidence import PLAN, rc_of, restore_run

pytestmark = requires_native(("bash", BASH))

STOPPED_HE = "הדיסק הפסיק לענות אחרי בדיקת הכתיבה"
NOT_WRITTEN_HE = "לא נכתב בייט"


def goes_quiet(b: Path, answer: str) -> str:
    """The TRIM step, replaced by the disk's answer vanishing (the Issue's probe).
    `answer` = the box file of the question that stops being answered."""
    return f'discard_before_restore() {{ rm -f {posix(b)}/{answer}; return 0; }}; '


def assert_stopped_after_write(b: Path, run: Path):
    err = target(run, "error")
    assert STOPPED_HE in err, err
    assert UNTOUCHED_HE not in err and NOT_WRITTEN_HE not in err, err   # the lie is gone
    assert target(run, "state") == "failed"
    assert target(run, "write_started") == SERIAL   # the write check wrote it
    assert "dd sda" in destroyed(run)
    assert (run / "targets/sda/ata_log.json").exists()   # evidence: the server records disk_failure


def test_a_round_drawer_that_stops_answering_the_sector_size_after_the_write_check(tmp_path):
    b, run, prelude = box(tmp_path, PLAN)
    out = round_run(prelude + goes_quiet(b, "getss"))
    assert rc_of(out) == "rc=1", out
    assert_stopped_after_write(b, run)
    assert len(zaps(b)) == 1, zaps(b)   # the drawer was written: it may look broken (#1388)


def test_a_round_drawer_that_stops_answering_the_size_after_the_write_check(tmp_path):
    b, run, prelude = box(tmp_path, PLAN)
    out = round_run(prelude + goes_quiet(b, "disksize"))
    assert rc_of(out) == "rc=1", out
    assert_stopped_after_write(b, run)
    assert len(zaps(b)) == 1, zaps(b)


def test_a_station_that_stops_answering_the_sector_size_after_the_write_check(tmp_path):
    b, run, prelude = box(tmp_path, PLAN)
    out = sh(restore_run(prelude + goes_quiet(b, "getss")))
    assert rc_of(out) == "rc=1", out
    assert_stopped_after_write(b, run)


def test_a_station_that_stops_answering_the_size_after_the_write_check(tmp_path):
    """The Issue's `--getsz` station case: it was `target_set` with no ATA evidence."""
    b, run, prelude = box(tmp_path, PLAN)
    out = sh(restore_run(prelude + goes_quiet(b, "disksize")))
    assert rc_of(out) == "rc=1", out
    assert_stopped_after_write(b, run)


def second_check(tmp_path, *, written: bool, target_ss: str | None = None, quiet: str = "getss"):
    """apply_gpt on its own: the second check failing, with or without a write that began."""
    b, run, prelude = box(tmp_path, PLAN)
    if target_ss is not None:
        (b / "getss").write_text(target_ss + "\n", newline="\n")
    else:
        (b / quiet).unlink()
    marker = f'echo {SERIAL} > "$RUN_DIR/targets/sda/write_started"; ' if written else ""
    out = sh(prelude + marker + 'apply_gpt sda m.json; echo "err=$PLAN_ERROR"')
    err = next(line for line in out.splitlines() if line.startswith("err="))[4:]
    return b, run, err


def test_without_a_write_the_untouched_message_stays(tmp_path):
    """Local clone / plan_gate: nothing was written, so "not a byte was written" is true."""
    b, _run, err = second_check(tmp_path, written=False)
    assert err.endswith(NOT_WRITTEN_HE), err
    assert STOPPED_HE not in err
    assert zaps(b) == []


def test_without_a_write_a_refusal_of_the_size_keeps_the_untouched_message(tmp_path):
    b, _run, err = second_check(tmp_path, written=False, quiet="disksize")
    assert err.endswith(UNTOUCHED_HE), err
    assert zaps(b) == []


def test_a_disk_answering_another_sector_size_after_the_write_is_also_named(tmp_path):
    """The check did not fail to run -- it ran and refused (4Kn now). Still after a write."""
    b, _run, err = second_check(tmp_path, written=True, target_ss="4096")
    assert STOPPED_HE in err and UNTOUCHED_HE not in err, err
    assert zaps(b) == []   # the gate is still before --zap-all: apply_gpt wrote nothing more


def test_the_gate_stays_before_the_zap_when_the_disk_stops_answering(tmp_path):
    """The Issue forbids dropping the second check: the disk that stopped answering is
    never zapped by apply_gpt itself (the failure path's wipe is a separate, evidenced step)."""
    b, _run, err = second_check(tmp_path, written=True)
    assert STOPPED_HE in err, err
    assert zaps(b) == []
