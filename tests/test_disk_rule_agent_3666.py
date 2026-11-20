"""#3666 (אפיק #3653, הכרעת נדב 18/11): הגואי לא מחשב את צבע הדיסק -- הוא לוקח אותו כמו שהגיע.

מי שמחליט הוא המוח בשרת (`server/brain/diskcolor.py`); מכונה בלי שרת -- שיכפול מקומי, ומסך ההמתנה של משכפל -- מחזיקה את העובדות
של המגירות שלה ואין לה את מי לשאול. שם `agent/lib/smart/diskrule.sh` מחיל את **אותה** טבלה (``agent/lib/data/disk-rule.tsv``) ומוסר לגואי
פסק: ``cdisk_rule=`` / ``lc_rule=``. ב-C אין טבלה, אין משפטים, ואין "SMART ok אז ירוק".

מה נבדק:
1. **שקילות** -- כל 112 צירופי העובדות נותנים בסוכן את הפסק שהטבלה נתנה לפני השינוי (`tests/fixtures/disk_rule_golden_3666.json`), ואת
   אותו פסק כמו השרת על אותם צירופים, ובגרסת הרשומה (`disk_rule_record`) גם לכל המילים שהמסך מכיר ("passed", סימן כתיבה, מילות חומרה).
2. **טבלה שלא נקראה = אין פסק** -- קובץ חסר, שורה שבורה, או טבלה בלי שורת "לא נבדק" לא מדפיסה דבר, והגואי אומר "לא נבדק". לעולם לא ירוק.
3. **C לוקח ולא מחשב** -- `disk_rule_take` מקבל רק צבע מוכר והרשאה 0/1; זבל = אין פסק; ואין ב-C טבלה, משפט או `DiskFacts`.
4. **הקצה לקצה** -- הרשומה שהסוכן כותב היא זו שה-C מקבל, בלי שינוי (נבנה ב-cc).
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from native import requires_native
from server.disks import disk_rule
from test_agent import AGENT, BASH, posix

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/fixtures/disk_rule_golden_3666.json").read_text(encoding="utf-8"))
SRC = ROOT / "native-gui/src"
TABLE = ROOT / "agent/lib/data/disk-rule.tsv"
LIB = AGENT / "lib/smart/diskrule.sh"

SMARTS = ("ok", "warn", "fail", "failed_last", "unchecked", None, "something_new")
FLAGS = ("failed_last", "write_incomplete", "sata_issue", "write_slow")
SIG = ("color", "source", "target", "reason", "source_reason", "target_reason")


def facts_cases() -> list[dict]:           # the order the golden file was written in
    out = []
    for sm in SMARTS:
        for bits in itertools.product((False, True), repeat=len(FLAGS)):
            f = dict(zip(FLAGS, bits))
            if sm is not None:
                f["smart"] = sm
            out.append(f)
    return out


def sig_of(v: dict) -> str:
    return "|".join(str(int(v[k])) if isinstance(v[k], bool) else (v[k] or "") for k in SIG)


def run_sh(script: str, table: Path = TABLE) -> subprocess.CompletedProcess:
    """The script goes through a file, not `bash -c`: Git for Windows' bash cuts a long -c script silently and exits 0 (#893)."""
    env = {**os.environ, "DISK_RULE_TABLE": posix(table)}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "case.sh"
        path.write_text('export PATH="/usr/bin:$PATH"' + chr(10) + script + chr(10), encoding="utf-8", newline=chr(10))
        return subprocess.run([BASH, posix(path)], capture_output=True, text=True, encoding="utf-8",
                              env=env, stdin=subprocess.DEVNULL, timeout=180, cwd=str(ROOT))


pytestmark = requires_native(("bash", BASH))


def agent_sig(line: str) -> str:
    color, source, target, reason, sreason, treason, _he = line.split("|", 6)
    return "|".join((color, source, target, reason, sreason, treason))


# --- 1. equivalence ------------------------------------------------------------------------------------------------

def test_the_agent_gives_every_combination_the_verdict_the_table_gave_before():
    lines = []
    for f in facts_cases():
        args = " ".join(["'%s'" % f.get("smart", "")] + [str(int(f[k])) for k in FLAGS])
        lines.append(f". {posix(LIB)}; disk_rule_judge {args}; echo \"rc=$?\"")
    run = run_sh("\n".join(lines))
    assert run.returncode == 0, run.stderr
    got = run.stdout.splitlines()
    assert len(got) == 2 * len(facts_cases()), run.stdout[:300]
    verdicts = [agent_sig(got[i]) for i in range(0, len(got), 2)]
    assert all(got[i] == "rc=0" for i in range(1, len(got), 2))
    bad = [(f, g, w) for f, g, w in zip(facts_cases(), verdicts, GOLDEN["facts"]) if g != w]
    assert not bad, bad[:3]
    assert verdicts == [sig_of(disk_rule.judge(f)) for f in facts_cases()]          # ...and the server's, today


def test_the_agent_says_the_tables_sentence_for_the_reason_that_won():
    run = run_sh(f". {posix(LIB)}; disk_rule_judge fail 0 1 0 1; disk_rule_judge ok 0 1 0 0; disk_rule_judge warn 0 0 0 0")
    first, second, third = run.stdout.splitlines()
    assert first.split("|")[3:] == ["smart_fail", "write_incomplete", "smart_fail", disk_rule.REASON_HE["smart_fail"]]   # red and yellow: red's words
    assert second.split("|")[3] == "write_incomplete" and second.split("|")[6] == disk_rule.REASON_HE["write_incomplete"]
    assert third == "orange|1|1||||"                                                                            # a colour with no reason has no sentence


FACT_SMARTS = ("passed", "ok", "warn", "fail", "failed_last", "unchecked", "junk", "")
MARKS = ("", "clean", "incomplete", "unchecked")
WORDS = ("", "usb2 icrc_logged", "link_reset", "drawer_gone usb2")
WSTATES = ("", "ok", "slow", "failed", "checking", "unchecked")


def test_the_record_maps_what_the_screen_knows_to_the_servers_facts():
    """`passed` is ok; a cut write, a SATA word and the waiting screen's `slow` are the table's facts -- same as the server's."""
    cases = list(itertools.product(FACT_SMARTS, MARKS, WORDS, WSTATES))
    script = "\n".join(f". {posix(LIB)}; disk_rule_record cdisk_rule sda '{s}' '{m}' '{w}' '{ws}'" for s, m, w, ws in cases)
    run = run_sh(script)
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert len(lines) == len(cases)
    for (smart, mark, words, wstate), line in zip(cases, lines):
        key, _, rest = line.partition("=")
        assert key == "cdisk_rule" and rest.startswith("sda|"), line
        disk = {"smart": "ok" if smart == "passed" else smart or "unchecked", "write_mark": mark or None,
                "sata_issues": words.split()}
        want = disk_rule.judge(disk_rule.facts_of(disk, write_slow=wstate == "slow"))
        assert agent_sig(rest[len("sda|"):]) == sig_of(want), (smart, mark, words, wstate, line)


def test_lc_records_cover_every_inventoried_drawer(tmp_path):
    lc = tmp_path / "lc"
    (lc / "inv").mkdir(parents=True)
    (lc / "inv/sda.line").write_text("1|sda|870 EVO|S1|256060514304|ok|\n", encoding="utf-8", newline="\n")
    (lc / "inv/sdb.line").write_text("2|sdb|870 EVO|S2|256060514304|failed_last|disk\n", encoding="utf-8", newline="\n")
    (lc / "inv/sdb.mark").write_text("incomplete\n", encoding="utf-8", newline="\n")
    (lc / "inv/sdc.line").write_text("3|sdc|870 EVO|S3|256060514304|fail|health_failed\n", encoding="utf-8", newline="\n")
    run = run_sh(f"LC={posix(lc)}; . {posix(LIB)}; disk_rule_lc_records")
    assert run.returncode == 0, run.stderr
    got = {l.split("|")[0].split("=")[1]: l for l in run.stdout.splitlines()}
    assert {d: l.split("|")[1:5] for d, l in got.items()} == {
        "sda": ["green", "1", "1", ""], "sdb": ["yellow", "0", "1", "failed_last"], "sdc": ["red", "1", "0", "smart_fail"]}
    assert all(l.startswith("lc_rule=") for l in got.values())


# --- 2. a table that could not be read says nothing -----------------------------------------------------------------

def test_a_table_that_cannot_be_read_gives_no_verdict_and_no_record(tmp_path):
    text = TABLE.read_text(encoding="utf-8")
    cases = {
        "missing": tmp_path / "nowhere.tsv",
        "no default row": tmp_path / "nodefault.tsv",
        "a broken row": tmp_path / "broken.tsv",
        "empty": tmp_path / "empty.tsv",
    }
    cases["no default row"].write_text("\n".join(l for l in text.splitlines() if not l.startswith("smart\tunchecked")) + "\n", encoding="utf-8", newline="\n")
    cases["a broken row"].write_text(text.replace("smart\tok\tsmart=ok\tgreen\t-\t-", "smart\tok\tsmart=ok\tgreen"), encoding="utf-8", newline="\n")
    cases["empty"].write_text("", encoding="utf-8")
    for name, table in cases.items():
        run = run_sh(f". {posix(LIB)}; disk_rule_judge ok 0 0 0 0; echo \"rc=$?\"; disk_rule_record cdisk_rule sda ok '' '' ''; echo \"rc=$?\"", table)
        assert run.stdout.splitlines() == ["rc=1", "rc=0"], (name, run.stdout, run.stderr)    # no verdict, no record, nothing green


def test_a_table_with_no_default_row_does_not_load_in_the_server_either(tmp_path):
    bad = tmp_path / "t.tsv"
    bad.write_text("\n".join(l for l in TABLE.read_text(encoding="utf-8").splitlines() if not l.startswith("smart\tunchecked")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not read is not green"):
        disk_rule._load(bad)


def test_the_table_is_found_next_to_the_libs_and_not_under_the_sticks_smart_kb(tmp_path):
    """A USB stick points SMART_KB at its server-synced copy of the two SMART tables, which has no disk-rule.tsv (usbsync.sh): the
    verdict must not depend on it, or every disk of a stick's local clone would be "not checked"."""
    lib, stick = tmp_path / "lib", tmp_path / "stick"
    (lib / "data").mkdir(parents=True)
    stick.mkdir()
    shutil.copy(TABLE, lib / "data" / "disk-rule.tsv")
    env = {k: v for k, v in os.environ.items() if k != "DISK_RULE_TABLE"}
    env.update(LIB_DIR=posix(lib), SMART_KB=posix(stick))
    run = subprocess.run([BASH, "-c", f'export PATH="/usr/bin:$PATH"; . {posix(LIB)}; disk_rule_judge fail 0 0 0 0'], capture_output=True,
                         text=True, encoding="utf-8", env=env, stdin=subprocess.DEVNULL, timeout=60, cwd=str(ROOT))
    assert run.stdout.split("|")[:4] == ["red", "1", "0", "smart_fail"], (run.stdout, run.stderr)


# --- 3. C takes a verdict, it does not make one ---------------------------------------------------------------------

def test_the_gui_has_no_table_no_sentence_and_no_fact_to_judge():
    for path in SRC.glob("*.[ch]"):
        text = path.read_text(encoding="utf-8")
        # (`red_refusal_he` in screens_rounds.c is SMART's own red wording -- readonly / F1 / SMART failed, tied to smart.sh -- not the table)
        for gone in ("DiskFacts", "disk_rule(&", "disk_rule_he(", "נפסל כאיטי בבדיקת הכתיבה בסבב הזה",
                     "לא מעתיקים ממנו; אפשר לכתוב עליו", "בעיית SATA (הקישור התאפס"):
            assert gone not in text, f"{path.name}: {gone}"
    assert "disk_rule(" not in (SRC / "disk_rule.c").read_text(encoding="utf-8").replace("disk_rule_", "")


def test_the_screens_take_the_agents_and_the_servers_records():
    assert 'KEY("cdisk_rule")' in (SRC / "state.c").read_text(encoding="utf-8")
    assert '"lc_rule"' in (SRC / "lc_state.c").read_text(encoding="utf-8")
    gui_libs = (AGENT / "lib/gui/guibridge.sh").read_text(encoding="utf-8")
    assert 'diskrule.sh' in gui_libs, "the kiosk does not load diskrule.sh: cdisk_rule= / lc_rule= would be 'not found' on the metal"
    assert "disk_rule_lc_records" in (AGENT / "lib/localclone/lcgui.sh").read_text(encoding="utf-8")
    assert "disk_rule_record cdisk_rule" in (AGENT / "lib/gui/clonergui.sh").read_text(encoding="utf-8")
    assert "disk-rule.tsv" in (ROOT / "tools/build_initramfs.sh").read_text(encoding="utf-8"), "the table is not packed into the initramfs"


_CC = shutil.which("cc") or shutil.which("gcc")


@requires_native(("cc", _CC), why="native-gui נבנה על המעבדה בלבד")
def test_disk_rule_take_takes_a_known_verdict_and_nothing_else(tmp_path):
    main = tmp_path / "main.c"
    main.write_text(r'''
#include <stdio.h>
#include "disk_rule.h"
static void show(const char *c, const char *s, const char *t, const char *r, const char *he) {
    DiskRule x; int ok = disk_rule_take(&x, c, s, t, r, he);
    printf("%d|%d|%s|%d|%d|%s|%s\n", ok, x.set, disk_rule_color(x.color), x.source, x.target, x.reason, x.he);
}
int main(void) {
    show("red", "1", "0", "smart_fail", "משפט");
    show("yellow", "0", "1", "failed_last", "");
    show("green", "1", "1", "", "");
    show("grey", "1", "1", "", "");
    show("purple", "1", "1", "", "");     /* an unknown colour */
    show("red", "2", "0", "x", "");        /* a permission that is not 0 or 1 */
    show("red", "", "0", "x", "");
    show(NULL, "1", "1", "", "");
    DiskRule n = disk_rule_none();
    printf("none|%d|%s|%d|%d\n", n.set, disk_rule_color(n.color), n.source, n.target);
    return 0;
}
''', encoding="utf-8")
    exe = tmp_path / "t"
    built = subprocess.run([_CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SRC), str(main), str(SRC / "disk_rule.c"), "-o", str(exe)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    out = subprocess.run([str(exe)], capture_output=True, text=True, encoding="utf-8", timeout=30, stdin=subprocess.DEVNULL)
    assert out.stdout.splitlines() == [
        "1|1|red|1|0|smart_fail|משפט", "1|1|yellow|0|1|failed_last|", "1|1|green|1|1||", "1|1|grey|1|1||",
        "0|0|grey|1|1||", "0|0|grey|1|1||", "0|0|grey|1|1||", "0|0|grey|1|1||",
        "none|0|grey|1|1"], out.stdout


def test_the_junk_records_are_not_a_verdict_in_the_state_parsers():
    """A wrong colour, a permission that is not 0/1 -- `disk_rule_take` refuses it, so the drawer stays "not checked"."""
    src = (SRC / "disk_rule.c").read_text(encoding="utf-8")
    assert re.search(r"if \(c < 0 \|\| !source \|\| !target", src), "disk_rule_take no longer refuses junk"
