"""#2412 -- the restore plan is checked before the first destructive write.

``validate_restore_plan`` (and the sector-size / scheme / "a partition with no
file" gates) ran only inside ``apply_gpt``. The write check (patterns at the
start, middle and end), the whole-disk TRIM and, in a round, the RAID wipe all
ran before it -- so a manifest with a bad GUID, an unsupported scheme or a forged
plan erased the disk's old installation and only then failed "correctly"
(principle 5 held, the damage was done).

Now ``plan_gate`` (expand.sh) runs ``plan_preflight`` -- the same checks apply_gpt
still runs again -- first: on the station before ``write_check``, in a round in
``write_check_round`` before ``raid_wipe`` and ``write_check``.

The box is test_restore_evidence's: a fake jq that answers the plan, sgdisk and
blockdev stubs. The destructive steps are functions that only record the call.

Negative control: against a000bec7 the write check and the TRIM (and in a round
the RAID wipe) are called on a refused plan -- the commit message has the output.
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import BASH, sh
from test_restore_evidence import PLAN, build_box, rc_of, restore_run

pytestmark = requires_native(("bash", BASH))

BAD_GUID = PLAN[0].replace("C12A7328-F81F-11D2-BA4B-00A0C93EC93B", "NOT-A-GUID")

#: every destructive step before apply_gpt, as a recorder
DESTROY = ('write_check() { echo "write_check $1" >> "$RUN_DIR/destroy"; return 0; }; '
           'write_speed_verdict() { return 0; }; '
           'discard_before_restore() { echo "trim $1" >> "$RUN_DIR/destroy"; return 0; }; '
           'raid_wipe() { echo "raid_wipe $1" >> "$RUN_DIR/destroy"; return 0; }; ')


def destroyed(run) -> list[str]:
    f = run / "destroy"
    return f.read_text(encoding="utf-8").split("\n")[:-1] if f.exists() else []


@pytest.mark.parametrize("bad", [
    [BAD_GUID, *PLAN[1:]],                                    # a GUID sgdisk would refuse
    [PLAN[0].replace("|vfat|", "|badfs|"), *PLAN[1:]],          # a file system nobody knows
    [PLAN[0], PLAN[1].replace("|206848|", "|2048|"), PLAN[2]],  # overlaps partition 1
])
def test_the_station_checks_the_plan_before_the_write_check_and_the_trim(tmp_path, bad):
    _box, run, prelude = build_box(tmp_path, plan=bad)
    out = sh(restore_run(prelude + DESTROY))
    assert rc_of(out) == "rc=1", out
    assert destroyed(run) == [], destroyed(run)
    assert (run / "targets/sda/state").read_text().strip() == "failed"
    assert "הדיסק לא שונה" in (run / "targets/sda/error").read_text(encoding="utf-8")


def test_the_station_with_a_good_plan_still_checks_and_trims_first(tmp_path):
    """Positive control: the order of the good path is the one #1366/#1969 decided."""
    _box, run, prelude = build_box(tmp_path)
    sh(restore_run(prelude + DESTROY))
    assert destroyed(run) == ["write_check sda", "trim sda"], destroyed(run)


def test_a_round_checks_each_drawers_plan_before_its_raid_wipe_and_write_check(tmp_path):
    _box, run, prelude = build_box(tmp_path, plan=[BAD_GUID, *PLAN[1:]])
    out = sh(prelude + DESTROY + 'disk_fits() { return 0; }; echo md > "$RUN_DIR/targets/sda/raid_wipe"; '
             'write_check_round m.json sda; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    assert destroyed(run) == [], destroyed(run)
    assert (run / "targets/sda/state").read_text().strip() == "failed"
    assert "הדיסק לא שונה" in (run / "targets/sda/error").read_text(encoding="utf-8")


def test_a_round_with_a_good_plan_still_wipes_the_raid_and_checks(tmp_path):
    _box, run, prelude = build_box(tmp_path)
    sh(prelude + DESTROY + 'disk_fits() { return 0; }; echo md > "$RUN_DIR/targets/sda/raid_wipe"; '
       'write_check_round m.json sda; :')
    assert destroyed(run) == ["raid_wipe sda", "write_check sda"], destroyed(run)
