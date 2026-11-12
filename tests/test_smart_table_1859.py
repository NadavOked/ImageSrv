"""#1859 (Nadav 07/11, mockup 1859-smart-table) -- the agent's side of the cloner's SMART table.

`agent/lib/smart/smarttable.sh` turns a disk's cached `smartctl -x` output, the judge's findings
(`.find`) and the two knowledge tables into the state records the GUI draws -- `csmart=` and
`csmartrow=`. The GUI knows no table, so everything the operator reads is decided here:

* the Hebrew name of (id, the name smartctl printed); a name the tables do not know is the
  vendor's, `u` -- never fine, never a colour (principle 5);
* `c` critical -- a neutral tag; `o` orange only where the judge said orange; `k` the cable's (199);
* `f` at the vendor's threshold -- the BIOS stops there; RED (Nadav 07/11: "if I have to press F1,
  it is red"), whatever the mockup drew;
* "no comparison" is three words: 1 compared, 0 the server has no previous round, - the answer
  was not read (an older server) -- never "stable".

The disk outputs are real: tests/fixtures/smart-1921/cloner2-sdc.txt is the MZ7LN256 of the lab's
cloner 2; the others are fixtures_smart.py's.
"""

from __future__ import annotations

import shutil
import pytest

import fixtures_smart as F
from native import requires_native
from test_smart import BASH, PRELUDE, REPO, env, make_smartctl, posix, sh

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")

TABLE = f". {posix(REPO)}/agent/lib/smart/smarttable.sh; "
MZ7LN = (REPO / "tests" / "fixtures" / "smart-1921" / "cloner2-sdc.txt").read_text(encoding="utf-8").split("\n", 1)[1]


def run_table(tmp_path, body, *, dev="sda", cmp="1", rose="", keep_find=True, script=""):
    """smart_probe on a fake smartctl, then smart_table_one; returns (head, {id: row fields}, rows in order)."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev_dir = tmp_path / "dev"; dev_dir.mkdir(exist_ok=True)
    (dev_dir / dev).write_bytes(b"\x00")
    blk = tmp_path / "sysro" / "sys" / "block" / dev; blk.mkdir(parents=True, exist_ok=True)
    (blk / "ro").write_text("0\n", newline="\n")
    sc = make_smartctl(tmp_path, body)
    drop = "" if keep_find else f"rm -f {posix(run)}/smart/{dev}.find; "
    out = sh(env(run, dev_dir, sysroot=tmp_path / "sysro", smartctl=sc) + PRELUDE + TABLE
             + f"{script}smart_probe {dev} > /dev/null; {drop}smart_table_one {dev} '{cmp}' '{rose}'")
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    rows = [l.split("=", 1)[1].split("|") for l in lines if l.startswith("csmartrow=")]
    head = next((l.split("=", 1)[1].split("|") for l in lines if l.startswith("csmart=")), None)
    return head, {r[1]: r for r in rows}, rows


# --- the rows ----------------------------------------------------------------------------


def test_the_mockups_disk_is_every_attribute_in_hebrew_by_id(tmp_path):
    head, by_id, rows = run_table(tmp_path, MZ7LN, rose="5=3,9=31")
    assert head == ["sda", "20", "1"]
    assert [r[1] for r in rows] == ["5", "9", "12", "170", "171", "172", "173", "174", "178", "180", "184",
                                    "187", "194", "199", "233", "241", "242", "249", "250", "251"]
    # [dev, id, flags, raw, rose, name]
    assert by_id["5"][5] == "סקטורים שהועברו" and by_id["9"][5] == "שעות עבודה" and by_id["194"][5] == "טמפרטורה"
    assert by_id["250"][3] == "39765357082", "a number past 32 bits is not cut"
    assert by_id["9"][4] == "31" and by_id["5"][4] == "3" and by_id["12"][4] == ""   # the rose: only what rose


def test_critical_is_a_neutral_tag_and_the_cable_is_not_the_disks(tmp_path):
    """Grok's list: 5/10/187/196/197/198/199 -- a tag, with no colour at zero. 199 is the cable (#872): `k`."""
    _, by_id, _ = run_table(tmp_path, MZ7LN)
    assert by_id["5"][2] == "c" and by_id["187"][2] == "c"            # critical at 0: a tag, not orange
    assert "k" in by_id["199"][2] and "c" in by_id["199"][2]
    assert "o" not in by_id["5"][2] + by_id["187"][2], "critical at zero is not orange"
    assert by_id["9"][2] == by_id["194"][2] == by_id["173"][2] == ""  # information: no tag at all


def test_orange_only_where_the_judge_said_orange(tmp_path):
    """5/187/10/196 over 0 -- the #1921 table, Nadav's 20/10 rule; never by the agent's own opinion."""
    _, by_id, _ = run_table(tmp_path, F.mz7ln256(realloc=3))
    assert by_id["5"][2] == "co" and by_id["5"][3] == "3"
    assert not [i for i, r in by_id.items() if i != "5" and "o" in r[2]], by_id
    _, by_id, _ = run_table(tmp_path, F.mz7ln256(uncorr=1))
    assert by_id["187"][2] == "co"


def test_a_worn_ssd_row_is_orange_by_the_table(tmp_path):
    _, by_id, _ = run_table(tmp_path, F.nvme(used=93), dev="nvme0n1")
    assert "o" in by_id["percentage_used"][2], by_id["percentage_used"]
    assert by_id["percentage_used"][3] == "93"


def test_at_the_vendors_threshold_is_flagged_red_f_and_does_not_colour_the_judge(tmp_path):
    """184 End-to-End_Error at its threshold (value 97 <= 97): `f` -- what the BIOS stops on. Not orange."""
    body = MZ7LN.replace("184 End-to-End_Error        0x0033   100   100   097", "184 End-to-End_Error        0x0033   096   100   097")
    assert body != MZ7LN
    _, by_id, _ = run_table(tmp_path, body)
    assert by_id["184"][2] == "f", by_id["184"]
    assert "f" not in by_id["5"][2] + by_id["9"][2], "a threshold of 0 is no threshold"


def test_an_attribute_the_tables_do_not_know_keeps_the_vendors_name_and_is_unknown(tmp_path):
    body = MZ7LN.replace("233 Media_Wearout_Indicator", "213 Mystery_Vendor_Thing  ")
    _, by_id, _ = run_table(tmp_path, body)
    assert by_id["213"][5] == "Mystery_Vendor_Thing" and by_id["213"][2] == "u"
    assert by_id["213"][3] == "15728639"


def test_the_cable_row_is_information_whatever_the_threshold(tmp_path):
    """#2846 (Nadav 11/11): 199 at 163, the setting at 100 -- no `h`: an absolute count is history.
    The row stays the cable's (`k`: a rise there is not "replace the disk") and critical (`c`)."""
    _, by_id, _ = run_table(tmp_path, MZ7LN, script='json_get() { case "$2" in .crc_warn_threshold) echo 100 ;; *) echo null ;; esac; }; ')
    fl = by_id["199"][2]
    assert "h" not in fl and "o" not in fl and "R" not in fl and "k" in fl and "c" in fl, fl


def test_nvme_rows_come_after_ata_by_the_tables_key_and_media_errors_are_critical(tmp_path):
    head, by_id, rows = run_table(tmp_path, F.nvme(media_errors=2), dev="nvme0n1", rose="media_errors=2")
    assert head[1] == "10" and [r[1] for r in rows][0] == "critical_warning"
    assert by_id["media_errors"][2].startswith("c") and by_id["media_errors"][4] == "2"
    assert by_id["temperature"][5] == "טמפרטורה" and by_id["temperature"][3] == "38"


def test_nothing_is_written_for_a_disk_smartctl_did_not_read(tmp_path):
    head, _, rows = run_table(tmp_path, F.NO_SMART)
    assert head is None and not rows


def test_nothing_is_written_when_the_judges_findings_are_missing(tmp_path):
    """No `.find` = nothing was judged: no table, never a table whose rows all look fine (principle 5)."""
    head, _, rows = run_table(tmp_path, MZ7LN, keep_find=False)
    assert head is None and not rows


def test_the_head_carries_the_comparison_word_as_given(tmp_path):
    for cmp in ("1", "0", "-"):
        head, _, _ = run_table(tmp_path, MZ7LN, cmp=cmp)
        assert head[2] == cmp


# --- smart_tables_state: what the server said -------------------------------------------------


def state_script(tmp_path, *, rose_file, serial="S1", smart_rose=None):
    """smart_tables_state sda on a probed MZ7LN256; the answer file is made by hand or by smart_rose_refresh."""
    run = tmp_path / "run"
    pre = f"disk_serial() {{ echo {serial}; }}; list_disks() {{ echo sda; }}; "
    if rose_file is not None:
        (run / "smart_rose").write_text(rose_file, newline="\n")
    return pre


def run_state(tmp_path, rose_file, **kw):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    pre = state_script(tmp_path, rose_file=rose_file, **kw)
    (tmp_path / "dev").mkdir(exist_ok=True); (tmp_path / "dev" / "sda").write_bytes(b"\x00")
    blk = tmp_path / "sysro" / "sys" / "block" / "sda"; blk.mkdir(parents=True, exist_ok=True)
    (blk / "ro").write_text("0\n", newline="\n")
    sc = make_smartctl(tmp_path, MZ7LN)
    out = sh(env(run, tmp_path / "dev", sysroot=tmp_path / "sysro", smartctl=sc) + PRELUDE + TABLE + pre
             + "smart_probe sda > /dev/null; smart_rose_refresh() { return 1; }; smart_tables_state sda")
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def test_the_server_said_this_serial_rose_by_five_nine_is_the_column(tmp_path):
    lines = run_state(tmp_path, "ok\nS1|5=3,9=31\nOTHER|5=9\n")
    assert lines[0] == "csmart=sda|20|1"
    assert "csmartrow=sda|5|c|0|3|סקטורים שהועברו" in lines
    assert "csmartrow=sda|9||9487|31|שעות עבודה" in lines


def test_an_answer_with_no_line_for_the_serial_is_no_previous_round(tmp_path):
    assert run_state(tmp_path, "ok\nOTHER|5=9\n")[0] == "csmart=sda|20|0"


def test_a_compared_disk_with_nothing_risen_is_compared(tmp_path):
    assert run_state(tmp_path, "ok\nS1|\n")[0] == "csmart=sda|20|1"


def test_no_answer_read_yet_is_not_no_comparison(tmp_path):
    """An older server (or a response that never came): '-', so the screen does not say "first round"."""
    assert run_state(tmp_path, None)[0] == "csmart=sda|20|-"


def test_a_disk_with_no_serial_has_no_history(tmp_path):
    assert run_state(tmp_path, "ok\nS1|5=3\n", serial="")[0] == "csmart=sda|20|0"


@requires_native(("jq", shutil.which("jq")), why="smart_rose_refresh reads response.json with jq")
def test_smart_rose_refresh_reads_the_hellos_answer_and_keeps_the_old_file_when_it_cannot(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    (run / "response.json").write_text('{"smart_rose": [{"serial": "S1", "rose": {"5": 3, "9": 31}}, {"serial": "S2", "rose": {}}]}',
                                       encoding="utf-8")
    cmd = env(run, tmp_path) + PRELUDE + TABLE + "smart_rose_refresh; echo rc=$?; cat \"$RUN_DIR/smart_rose\""
    out = sh(cmd)
    assert out.stdout.splitlines() == ["rc=0", "ok", "S1|5=3,9=31", "S2|"], out.stdout + out.stderr
    (run / "response.json").write_text('{"smart_learned": []}', encoding="utf-8")   # an older server: no such field
    out = sh(env(run, tmp_path) + PRELUDE + TABLE + "smart_rose_refresh; echo rc=$?; cat \"$RUN_DIR/smart_rose\"")
    assert out.stdout.splitlines()[0] == "rc=1" and "S1|5=3,9=31" in out.stdout, "the previous answer stays"
    (run / "response.json").write_text("{broken", encoding="utf-8")
    assert sh(env(run, tmp_path) + PRELUDE + TABLE + "smart_rose_refresh; echo rc=$?").stdout.strip() == "rc=1"


# --- the wiring: the kiosk writes it, and loads what writes it -----------------------------------


def test_the_idle_state_writes_the_tables_and_the_kiosk_loads_the_library():
    gui = (REPO / "agent" / "lib" / "gui" / "clonergui.sh").read_text(encoding="utf-8")
    assert 'smart_tables_state "$(list_disks)" >> "$_cg_next"' in gui
    assert gui.index("cloner_cdisks \"$(list_disks)\"") < gui.index("smart_tables_state")
    bridge = (REPO / "agent" / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/smarttable.sh"' in bridge, "gui_libs: not loaded in the kiosk = not found, once a second (#834)"
    agent = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/smarttable.sh"' in agent


def test_the_critical_words_are_the_servers():
    """smart_names.CRITICAL_WORDS | {"crc"} -- one list in two places, kept in step here."""
    from server.disks import smart_names
    src = (REPO / "agent" / "lib" / "smart" / "smarttable.sh").read_text(encoding="utf-8")
    words = next(l for l in src.splitlines() if l.startswith("SMART_CRITICAL_WORDS=")).split('"')[1].split()
    assert set(words) == set(smart_names.CRITICAL_WORDS) | {"crc"}


def test_the_nvme_lines_shown_are_the_ones_smartattrs_sends():
    src = (REPO / "agent" / "lib" / "smart" / "smarttable.sh").read_text(encoding="utf-8")
    shown = set(next(l for l in src.splitlines() if l.startswith("SMART_NVME_SHOWN=")).split('"')[1].split())
    sent = {"critical_warning", "temperature", "available_spare", "available_spare_threshold", "percentage_used",
            "media_errors", "unsafe_shutdowns", "power_on_hours", "power_cycles", "error_log_entries"}
    assert shown == sent
    attrs_sh = (REPO / "agent" / "lib" / "smart" / "smartattrs.sh").read_text(encoding="utf-8")
    assert all(f'k["{k}"]' in attrs_sh for k in sent), "smartattrs.sh drifted from the list here"


def test_the_cable_word_is_not_a_replace_the_disk_word():
    """A rise in 199 is the cable (#872): the table's `k` keeps it out of "להחליף את הדיסק"."""
    from server.disks import smart_names
    assert "crc" not in smart_names.CRITICAL_WORDS
    assert "SMART_CRITICAL_WORDS" in (REPO / "agent" / "lib" / "smart" / "smarttable.sh").read_text(encoding="utf-8")
