"""#2544 -- a drawers round asks the operator nothing about a drawer the plan refuses anyway.

``drawer_gates`` (drawergate.sh) ran the RAID question and the split question
before ``plan_gate`` (expand.sh, in ``write_check_round``): the operator answered,
and then got a refusal that was known from the first second (a Windows image
without the partitions' unique GUIDs, #1212; PR #2541). Nothing was written either
way -- it was an unnecessary question.

Now ``drawer_gates`` first asks ``plan_gate`` (read only, no disk touched) per
drawer: a refused one fails by name, is not asked and is not returned.

The box is test_restore_evidence's (a fake jq that answers the plan; sgdisk and
blockdev stubs). The two questions are recorders: a question that was asked
leaves a line in ``asked``.
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import BASH, posix, sh
from test_refuse_no_guid_1212 import OLD_WINDOWS
from test_restore_evidence import AGENT, PLAN, build_box

pytestmark = requires_native(("bash", BASH))

# OLD_WINDOWS: the plan of an old Windows image, a partition without its unique GUID (#1212) -- plan_gate refuses it
GATES = (
    "raid_member_probe() { echo isw_raid_member; }; "              # every drawer carries old RAID metadata
    'disk_port() { echo 1; }; '
    '_drawer_ask() { echo "$2" >> "$RUN_DIR/asked"; target_set "$1" asking "$4"; case "$2" in raid) echo wipe ;; *) echo split ;; esac; }; '
    'share_plan() { echo "2|100|200"; }; '                          # every drawer would be asked about the split
)


def gates(tmp_path, plan):
    box, run, prelude = build_box(tmp_path, plan=plan)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    out = sh(prelude + f". {posix(AGENT)}/lib/restore/drawergate.sh; " + GATES
             + 'drawer_gates sda > "$RUN_DIR/out" 2> "$RUN_DIR/err"; echo "rc=$?"')
    return out, run


def asked(run) -> list[str]:
    f = run / "asked"
    return f.read_text().split() if f.exists() else []


def test_a_refused_plan_is_not_asked_about_raid_or_the_split(tmp_path):
    out, run = gates(tmp_path, OLD_WINDOWS)
    assert asked(run) == [], f"the operator was asked about a drawer the plan refuses: {asked(run)} {out}"
    assert (run / "out").read_text().split() == [], (run / "out").read_text()
    assert (run / "targets/sda/state").read_text().strip() == "failed"
    assert "הדיסק לא שונה" in (run / "targets/sda/error").read_text(encoding="utf-8")
    assert not (run / "targets/sda/raid_wipe").exists()


def test_an_accepted_plan_is_asked_as_before(tmp_path):
    """Positive control: without it "nothing was asked" would pass on a gate that refuses everything."""
    out, run = gates(tmp_path, PLAN)
    assert asked(run) == ["raid", "share"], out
    assert (run / "out").read_text().split() == ["sda"], out
    assert (run / "targets/sda/raid_wipe").read_text().strip() == "isw_raid_member"
