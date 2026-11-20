"""#2848 (Nadav 11/11): one rule for a disk -- its colour, may it be a SOURCE, may it be a TARGET --
and "there is no difference between the server, the build machine and the cloner".

| state                                         | colour | source | target |
|-----------------------------------------------|--------|--------|--------|
| SMART ok                                      | green  | yes    | yes    |
| SMART half-good                               | orange | yes    | yes    |
| failed the last clone / write not completed   | yellow | NO     | yes    |
| SMART failed                                  | red    | yes    | NO     |
| slow in THIS round                            | red    | yes    | NO     |
| a SATA problem (link reset / drawer gone)     | red    | yes    | NO     |

One table, ``agent/lib/data/disk-rule.tsv``, applied in two places (#3666): ``server/disks/disk_rule.py`` (the server -- drawer_list[].rule and its
refusals, served by the brain's K3/DISK_COLOR) and ``agent/lib/smart/diskrule.sh`` (a machine with no server: the local clone and the
cloner's waiting screen). The console and the GUI (C) have no copy -- they show the verdict they are given
(tests/console_disk_rule_3666.test.cjs, tests/test_disk_rule_agent_3666.py). The agent's equivalence with the server over every
combination of the facts is held there; here is the server's own table and its enforcement.

Then the server's own enforcement, end to end over HTTP: a red target and a yellow source are refused with
the rule's sentence; and "slow" belongs to the round it was measured in -- round 1 slow (red), round 2
re-measured fine: green and picked as a target.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest

from server.disks import disk_rule

ROOT = Path(__file__).resolve().parents[1]
CONSOLE_JS = ROOT / "server/static/console.js"
RULE_C = ROOT / "native-gui/src/disk_rule.c"

SMARTS = ("ok", "warn", "fail", "failed_last", "unchecked")
FLAGS = ("failed_last", "write_incomplete", "sata_issue", "write_slow")   # #3591: no link_slow -- a link speed is no fact of the rule
CASES = [{"smart": sm, **dict(zip(FLAGS, bits))}
         for sm in SMARTS for bits in itertools.product((False, True), repeat=len(FLAGS))]
KEYS = ("color", "source", "target", "reason", "source_reason", "target_reason")


def _py(case: dict) -> dict:
    got = disk_rule.judge(case)
    return {**{k: got[k] for k in KEYS}, "he": disk_rule.REASON_HE.get(got["reason"]) if got["reason"] else ""}


# --- the table itself (Python) -------------------------------------------------------------------

@pytest.mark.parametrize("facts, color, source, target", [
    ({"smart": "ok"}, "green", True, True),
    ({"smart": "warn"}, "orange", True, True),
    ({"smart": "ok", "failed_last": True}, "yellow", False, True),
    ({"smart": "failed_last"}, "yellow", False, True),
    ({"smart": "ok", "write_incomplete": True}, "yellow", False, True),
    ({"smart": "warn", "write_incomplete": True}, "yellow", False, True),   # yellow over orange
    ({"smart": "fail"}, "red", True, False),
    ({"smart": "ok", "write_slow": True}, "red", True, False),
    ({"smart": "ok", "link_slow": True}, "green", True, True),               # #3591: an unknown fact is ignored -- no link speed is red
    ({"smart": "ok", "sata_issue": True}, "red", True, False),
    ({"smart": "fail", "failed_last": True}, "red", False, False),          # both: neither, drawn red
    ({"smart": "unchecked"}, "grey", True, True),                            # not read is not green
])
def test_the_table(facts, color, source, target):
    got = disk_rule.judge(facts)
    assert (got["color"], got["source"], got["target"]) == (color, source, target), got


def test_every_reason_has_its_sentence():
    assert set(disk_rule.REASON_HE) == set(disk_rule.RED) | set(disk_rule.YELLOW)


# --- the GUI's room shows the server's rule, it does not re-make it ------------------------------

def test_the_room_grid_takes_the_servers_rule():
    gs = (ROOT / "agent/lib/gui/guistate.sh").read_text(encoding="utf-8")
    assert '"room_drawer_rule="' in gs and "room_drawer_red" not in gs
    st = (ROOT / "native-gui/src/state.c").read_text(encoding="utf-8")
    assert 'KEY("room_drawer_rule")' in st and 'KEY("room_drawer_red")' not in st
    rr = (ROOT / "native-gui/src/screens_rounds.c").read_text(encoding="utf-8")
    assert re.search(r"return d->rule_set && !d->rule_target;", rr)


# --- the server enforces it --------------------------------------------------------------------------

def test_drawer_list_carries_the_rule_and_this_rounds_slow_only():
    """`write_slow` comes from this wave's target alone; `last_write_check` (a past round) colours nothing."""
    from server.rounds import room
    assert room._write_slow({"write_preflight": {"state": "slow"}}) is True
    assert room._write_slow({"write_preflight": {"state": "failed"}}) is True
    assert room._write_slow({"write_preflight": {"state": "ok"}}) is False
    assert room._write_slow({}) is False
    past = {"smart": "ok", "serial": "X", "last_write_check": {"mbps": 30, "slow": True, "text_he": "x"}}
    assert disk_rule.judge_disk(past)["color"] == "green"
    assert disk_rule.judge_disk(past, write_slow=True)["color"] == "red"


def test_the_server_file_says_the_rules_sentences():
    import test_disk_rule_server_2848 as e2e
    assert e2e.REASON_HE == disk_rule.REASON_HE
