"""#2848 (Nadav 11/11): one rule for a disk -- its colour, may it be a SOURCE, may it be a TARGET --
and "there is no difference between the server, the build machine and the cloner".

| state                                         | colour | source | target |
|-----------------------------------------------|--------|--------|--------|
| SMART ok                                      | green  | yes    | yes    |
| SMART half-good                               | orange | yes    | yes    |
| failed the last clone / write not completed   | yellow | NO     | yes    |
| SMART failed                                  | red    | yes    | NO     |
| slow in THIS round                            | red    | yes    | NO     |
| slow SATA link / a SATA problem               | red    | yes    | NO     |

Three implementations, one table: ``server/disks/disk_rule.py`` (the server: drawer_list[].rule and its
refusals), ``diskRule`` in ``server/static/console.js`` (the console), ``disk_rule()`` in
``native-gui/src/disk_rule.c`` (the cloner / build machine GUI). The parity test runs every combination
of the facts through all three and wants the same answer, the reason's sentence included.

Then the server's own enforcement, end to end over HTTP: a red target and a yellow source are refused with
the rule's sentence; and "slow" belongs to the round it was measured in -- round 1 slow (red), round 2
re-measured fine: green and picked as a target.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server.disks import disk_rule

ROOT = Path(__file__).resolve().parents[1]
CONSOLE_JS = ROOT / "server/static/console.js"
RULE_C = ROOT / "native-gui/src/disk_rule.c"

SMARTS = ("ok", "warn", "fail", "failed_last", "unchecked")
FLAGS = ("failed_last", "write_incomplete", "link_slow", "sata_issue", "write_slow")
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
    ({"smart": "ok", "link_slow": True}, "red", True, False),
    ({"smart": "ok", "sata_issue": True}, "red", True, False),
    ({"smart": "fail", "failed_last": True}, "red", False, False),          # both: neither, drawn red
    ({"smart": "unchecked"}, "grey", True, True),                            # not read is not green
])
def test_the_table(facts, color, source, target):
    got = disk_rule.judge(facts)
    assert (got["color"], got["source"], got["target"]) == (color, source, target), got


def test_every_reason_has_its_sentence():
    assert set(disk_rule.REASON_HE) == set(disk_rule.RED) | set(disk_rule.YELLOW)


# --- parity: the console ---------------------------------------------------------------------------

def _console_rule_source() -> str:
    text = CONSOLE_JS.read_text(encoding="utf-8")
    start = text.index("const DISK_RULE_RED")
    end = text.index("\n}\n", text.index("function diskRule(")) + 3
    return text[start:end]


@requires_native(("node", shutil.which("node")), why="the console rule runs in node")
def test_the_console_decides_exactly_as_the_server(tmp_path):
    script = tmp_path / "rule.cjs"
    script.write_text(_console_rule_source() + """
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map((c) => { const r = diskRule(c);
  return { ...r, he: r.reason ? DISK_RULE_HE[r.reason] : "" }; })));
""", encoding="utf-8")
    run = subprocess.run([shutil.which("node"), str(script)], input=json.dumps(CASES), capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    js = json.loads(run.stdout)
    wrong = [(c, p, j) for c, p, j in zip(CASES, map(_py, CASES), js) if {k: j[k] for k in (*KEYS, "he")} != p]
    assert len(js) == len(CASES) and not wrong, wrong[:3]


# --- parity: the GUI (C) -----------------------------------------------------------------------------

_CC = shutil.which("cc") or shutil.which("gcc")


@requires_native(("cc", _CC), why="native-gui נבנה על המעבדה בלבד")
def test_the_gui_decides_exactly_as_the_server(tmp_path):
    main = tmp_path / "main.c"
    main.write_text(r'''
#include <stdio.h>
#include <string.h>
#include "disk_rule.h"
int main(void) {
    char sm[32]; int fl, wi, ls, si, ws;
    while (scanf("%31s %d %d %d %d %d", sm, &fl, &wi, &ls, &si, &ws) == 6) {
        DiskFacts f = { sm, fl, wi, ls, si, ws };
        DiskRule r = disk_rule(&f);
        printf("%s|%d|%d|%s|%s|%s|%s\n", disk_rule_color(r.color), r.source, r.target,
               r.reason ? r.reason : "", r.source_reason ? r.source_reason : "",
               r.target_reason ? r.target_reason : "", disk_rule_he(r.reason));
    }
    return 0;
}
''', encoding="utf-8")
    exe = tmp_path / "rule"
    build = subprocess.run([_CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(RULE_C.parent),
                            str(main), str(RULE_C), "-o", str(exe)], capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    stdin = "".join(f"{c['smart']} " + " ".join(str(int(c[k])) for k in FLAGS) + "\n" for c in CASES)
    run = subprocess.run([str(exe)], input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert len(lines) == len(CASES)
    wrong = []
    for case, line in zip(CASES, lines):
        color, source, target, reason, sreason, treason, he = line.split("|")
        got = {"color": color, "source": source == "1", "target": target == "1", "reason": reason or None,
               "source_reason": sreason or None, "target_reason": treason or None, "he": he}
        if got != _py(case):
            wrong.append((case, got, _py(case)))
    assert not wrong, wrong[:3]


def test_the_three_say_the_same_sentences():
    """Without node or a compiler: the sentences themselves, read from the sources."""
    js = _console_rule_source()
    c = RULE_C.read_text(encoding="utf-8")
    for reason, he in disk_rule.REASON_HE.items():
        assert f'{reason}: "{he}"' in js, reason
        assert f'{{ "{reason}", "{he}" }}' in c, reason


# --- the GUI's room shows the server's rule, it does not re-make it ------------------------------

def test_the_room_grid_takes_the_servers_rule():
    gs = (ROOT / "agent/lib/gui/guistate.sh").read_text(encoding="utf-8")
    assert '"room_drawer_rule="' in gs and "room_drawer_red" not in gs
    st = (ROOT / "native-gui/src/state.c").read_text(encoding="utf-8")
    assert 'KEY("room_drawer_rule")' in st and 'KEY("room_drawer_red")' not in st
    rr = (ROOT / "native-gui/src/screens_rounds.c").read_text(encoding="utf-8")
    assert re.search(r"if \(d->rule_set\) return !d->rule_target;", rr)


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
