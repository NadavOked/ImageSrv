"""#2196 -- a bigger target whose NTFS ntfsresize finds inconsistent.

Field, 25/10 (USB v0.76.4, HP 8100, a source left in Fast Startup): the table
of the 500 GB target grew, ntfsresize refused the filesystem ("Totally 813
cluster accounting mismatches ... NTFS is inconsistent"), and the result card
said two things at once -- "partition 1 grew from 225 to 469 GB" and "the grow
was deferred".

Lab, 25/10 (a real Windows 11 in Fast Startup on ESXi, two shutdowns, four
copies): the drop before the grow (today's order) and the grow before the drop
both grew and both booted with no chkdsk; ``ntfsresize -n`` read every step
consistent, and ntfsresize empties $LogFile itself -- no order keeps the
journal. So the order stays; what changes is what is said:

* ``ntfs_grow_target`` (ntfsunhib.sh) is the whole NTFS grow, one function for
  every path (#2053 will restore a hibernated image through it);
* an ntfsresize that says "NTFS is inconsistent" is said for what it is, with
  what to do in Windows -- never forced (--bad-sectors / more -f);
* finish_grow marks a deferred grow (``grow_deferred``), ``lc_tgt``'s 13th
  field carries it, and the result card then says it in ONE line: no
  "הוגדלה" above a filesystem that did not grow.
"""

from __future__ import annotations


import pytest

from native import requires_native
from test_cloner_gui import BASH
from test_lc_ntfs_note_2009 import SETUP, box
from test_local_clone_agent import run
from test_stage_attribution import FAILING, NTFSFIX_LAST_FAILS, error_of, grow_drawers, grow_station, state_of

pytestmark = requires_native(("bash", BASH))

#: ntfsresize's own words on the field disk (diag-20261025-174423.txt), exit 1.
INCONSISTENT = (
    "#!/bin/sh\n"
    "echo 'Accounting clusters ...'\n"
    "echo 'Cluster accounting failed at 152431 (0x2536f): missing cluster in $Bitmap'\n"
    "echo 'Filesystem check failed! Totally 813 cluster accounting mismatches.'\n"
    "echo 'ERROR: NTFS is inconsistent. Run chkdsk /f on Windows then reboot it TWICE!'\n"
    "exit 1\n"
)
SAID = "ה-NTFS במקור אינו עקבי (ntfsresize). ב-Windows: chkdsk /f, ואז הרחבת הכונן בניהול הדיסקים"


@pytest.mark.parametrize("runner", [grow_drawers, grow_station])
def test_an_inconsistent_ntfs_is_said_for_what_it_is_and_marked_deferred(tmp_path, runner):
    run_dir, out = runner(tmp_path, fs="ntfs", stubs={"ntfsresize": INCONSISTENT})
    assert out.strip().endswith("rc=0"), out
    assert state_of(run_dir, "sda") == "done"                       # best-effort, as every deferred grow
    err = error_of(run_dir, "sda")
    assert "ההרחבה נדחתה" in err and SAID in err, err
    assert "טבלת המחיצות הוגדלה, מערכת הקבצים לא" in err, err
    assert len(err.encode()) < 400, len(err.encode())                # the screen's error[400]
    assert (run_dir / "targets" / "sda" / "grow_deferred").exists()
    assert not (run_dir / "ntfsresize.out").exists()                 # its words went to the log, the file did not stay


@pytest.mark.parametrize("runner", [grow_drawers, grow_station])
def test_any_other_ntfsresize_failure_is_not_called_inconsistent(tmp_path, runner):
    run_dir, out = runner(tmp_path, fs="ntfs", stubs={"ntfsresize": FAILING})
    assert out.strip().endswith("rc=0"), out
    err = error_of(run_dir, "sda")
    assert "ntfsresize נכשל (rc=1)" in err and "אינו עקבי" not in err, err
    assert (run_dir / "targets" / "sda" / "grow_deferred").exists()


@pytest.mark.parametrize("stubs", [{}, {"ntfsfix": NTFSFIX_LAST_FAILS}])   # grown; grown and the flag stayed (#2011)
def test_a_filesystem_that_grew_is_never_marked_deferred(tmp_path, stubs):
    run_dir, out = grow_station(tmp_path, fs="ntfs", stubs=stubs)
    assert out.strip().endswith("rc=0"), out
    assert not (run_dir / "targets" / "sda" / "grow_deferred").exists(), error_of(run_dir, "sda")


GUI_LINE = SETUP + ('target_init sda 0; echo sda >> "$LC/run/targets"; target_set sda done "x"; '
                    '[ ! -f "$BOX/deferred" ] || : > "$RUN_DIR/targets/sda/grow_deferred"; '
                    'lc_gui_run | grep "^lc_tgt="')


@pytest.mark.parametrize("deferred,want", [(True, "1"), (False, "0")])
def test_the_screen_record_carries_the_deferred_grow_as_field_13(tmp_path, deferred, want):
    out = run(tmp_path, GUI_LINE, **box(tmp_path, {}, *(("deferred",) if deferred else ())))
    assert out.returncode == 0, out.stderr
    fields = out.stdout.strip().splitlines()[-1][len("lc_tgt="):].split("|")
    assert len(fields) == 13 and fields[12] == want, fields
