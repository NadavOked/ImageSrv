"""#1212 (Nadav 06/11) -- a Windows image without a partition's unique GUID is
refused before the disk is touched.

An image captured before the unique GUIDs were kept has ``unique_guid`` empty.
Until now ``apply_gpt`` skipped ``-u``, sgdisk invented a GUID, ``verify_unique_guids``
logged "not checked" and the restore reached ``done`` -- and Windows did not boot
(``0xc000000e``: the BCD binds to the disk + partition GUID pair, #26).

Now ``plan_preflight`` (expand.sh) refuses it, on every path that writes a table:
the station (``run_restore`` -> ``plan_gate``), a round's drawers (direct send
included: ``write_check_round`` -> ``plan_gate``), and ``apply_gpt`` itself (local
clone). "Windows" = a partition whose role is ``windows`` (type Microsoft basic
data) -- the rule the capture refuses by (``unique_guid_gate``) and ``.os`` is
derived from (``_image_os``). Linux keeps restoring as before.

The evidence that the disk did not change: every destructive step is a recorder --
the write check, the TRIM, the RAID wipe as functions; sgdisk, dd, wipefs,
partclone.* and mkswap as programs on PATH.

Negative control: against a82cf0c1 (expand.sh and progress.sh as they were) the
refusal tests fail -- the station reaches ``verifying`` with sgdisk ``--zap-all``
in its calls (the commit message has the output).
"""

from __future__ import annotations

import json

import pytest

from native import requires_native
from test_agent import BASH, posix, sh
from test_plan_before_destroy_2412 import DESTROY, destroyed
from test_restore_evidence import PLAN, build_box, log_of, rc_of, restore_run, state_of, target_error, wrote

pytestmark = requires_native(("bash", BASH))

MESSAGE = ("האימג' נקלט לפני שמירת מזהי המחיצות — Windows לא יעלה ממנו."
           " קלטו את האימג' מחדש. הדיסק לא שונה.")
REASON = "windows_no_partition_guid"
LINUX_TYPE = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"

#: programs that write a disk; each records its argv and exits 0
WRITERS = ("dd", "wipefs", "partclone.ntfs", "partclone.fat", "partclone.ext4",
           "partclone.dd", "mkswap")


def without_guid(row: str) -> str:
    fields = row.split("|")
    fields[9] = ""
    return "|".join(fields)


def as_linux(row: str) -> str:
    fields = row.split("|")
    fields[1], fields[2], fields[3] = LINUX_TYPE, "linux", "ext4"
    return "|".join(fields)


#: the old Windows image: the ESP lost its GUID (any partition of the image counts)
OLD_WINDOWS = [without_guid(PLAN[0]), *PLAN[1:]]
#: a Linux image without a unique GUID (capture exempts it, #1212)
OLD_LINUX = [PLAN[0], without_guid(as_linux(PLAN[1])), PLAN[2]]


def box_with_recorders(tmp_path, plan):
    box, run, prelude = build_box(tmp_path, plan=plan)
    stubs = box / "stubs"
    for name in WRITERS:
        (stubs / name).write_text(
            f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "{posix(box)}/writers"\nexit 0\n',
            newline="\n")
    prelude = prelude.replace('export PATH=', f"chmod 0755 {posix(stubs)}/*; export PATH=", 1)
    return box, run, prelude + DESTROY


def disk_untouched(box, run) -> None:
    assert destroyed(run) == [], destroyed(run)
    writers = box / "writers"
    assert not writers.exists(), writers.read_text()
    calls = (box / "sgdisk.calls").read_text().splitlines() if (box / "sgdisk.calls").exists() else []
    for call in calls:
        argv = call.split()
        assert not {"-Z", "--zap-all", "-o", "--clear", "-n", "-u", "-U"} & set(argv) or "--pretend" in argv, call


def refused_by_name(run) -> None:
    assert (run / "targets/sda/state").read_text().strip() == "failed"
    assert target_error(run) == MESSAGE
    assert (run / "targets/sda/plan_why").read_text() == REASON
    assert f"sda: refused: {REASON}" in log_of(run)


def test_the_station_refuses_an_old_windows_image_before_the_disk(tmp_path):
    box, run, prelude = box_with_recorders(tmp_path, OLD_WINDOWS)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert wrote(run) == []
    refused_by_name(run)
    disk_untouched(box, run)


def test_a_round_refuses_the_drawer_before_its_raid_wipe_and_write_check(tmp_path):
    """The round's path -- a server round and a direct send alike end in
    run_restore_drawers -> write_check_round."""
    box, run, prelude = box_with_recorders(tmp_path, OLD_WINDOWS)
    out = sh(prelude + 'disk_fits() { return 0; }; echo md > "$RUN_DIR/targets/sda/raid_wipe"; '
             'write_check_round m.json sda; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    refused_by_name(run)
    disk_untouched(box, run)


def test_apply_gpt_refuses_it_too(tmp_path):
    """The local clone (lc_prepare_target) and every other caller of apply_gpt."""
    box, run, prelude = box_with_recorders(tmp_path, OLD_WINDOWS)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert (run / "targets/sda/plan_why").read_text() == REASON
    disk_untouched(box, run)


def test_the_report_carries_the_fixed_reason_beside_the_message(tmp_path):
    _box, run, prelude = box_with_recorders(tmp_path, OLD_WINDOWS)
    out = sh(restore_run(prelude) + '; build_progress ses_1 aa:bb:cc:dd:ee:ff > "$RUN_DIR/report.json"')
    assert "rc=1" in out, out
    target = json.loads((run / "report.json").read_text(encoding="utf-8"))["targets"][0]
    assert target["plan_why"] == REASON
    assert target["error"] == MESSAGE


@pytest.mark.parametrize("plan", [PLAN, OLD_LINUX], ids=["windows_with_guids", "linux_without_guid"])
def test_positive_control_these_still_restore(tmp_path, plan):
    """Positive control: the same box with the GUIDs, and Linux without one,
    reach verifying -- the table written (zap-all ran), no reason recorded."""
    box, run, prelude = box_with_recorders(tmp_path, plan)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=0", out
    assert state_of(run) == "verifying"
    assert wrote(run) == ["1", "2", "3"]
    assert not (run / "targets/sda/plan_why").exists()
    assert any(c.startswith("--zap-all ") for c in (box / "sgdisk.calls").read_text().splitlines())
