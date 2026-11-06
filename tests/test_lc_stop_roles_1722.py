"""#1722 — back on the local clone's source screen after a run, nothing is
chosen for the person.

Found on metal (R3, rc5 4ef137ea, 18/10, cloner 2): a local clone from disk 2
(sdb, Windows) to disk 3 (sda) was stopped at 8%. The target's table was gone,
and the source screen came back with disk 3 -- now empty -- as "מקור" and
disk 2 as "יעד": "מדיסק 3 אל דיסק 2". The screen had proposed drawer 3 as the
source when it opened (#1322, it had a system on it then) and kept that role
through the run, never asking the drawers again.

The rules (#1722):

* after a stop -- the person's own last lc-start, exactly (same disks, same
  serials), or nothing;
* after "שכפול נוסף", after a failure, and on the way in again after a run --
  nothing;
* never a source on a disk the inventory does not allow (no table), and the
  first proposal only on a disk with a system on it.

Driven through the real ``lc_handle``/``lc_after_reload`` by the driver of
``test_native_local_clone.py`` (Linux only). Roles: 0 דלג, 1 מקור, 2 יעד.
"""

from __future__ import annotations

import shutil

from native import requires_native
from test_cloner_gui import BASH, _pkgconfig
from test_native_local_clone import BOOT, drive, driver, records  # noqa: F401

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

SDB = "lc_disk=2|sdb|SAMSUNG MZ7LN256|S2|256060514304|ok|ok|Windows|4|44200000000|256060514304|1|\n"
SDA_WIN = "lc_disk=3|sda|Samsung SSD 870|S3|500107862016|ok|ok|Windows|4|44200000000|256060514304|1|\n"
SDA_EMPTY = "lc_disk=3|sda|Samsung SSD 870|S3|500107862016|ok|ok||0|0|-1|0|אין עליו טבלת מחיצות\n"
MEASURED = "lc_src=sdb|none\nlc_fit=sda|fits|256060514304|\n"


def _local(phase: str, *disks: str) -> str:
    return f"lc_screen=local\nlc_inv=done\nlc_phase={phase}\n" + "".join(disks)


#: before: disk 3 has a system on it (an earlier copy) -- the screen proposes it
BEFORE = _local("idle", SDB, SDA_WIN)
#: the copy sdb -> sda at 8% (as the agent draws it)
RUN = (_local("copying", SDB, SDA_WIN) + "lc_src=sdb|none\n"
       "lc_run=sdb|44200000000|3500000000|90|0||3|\nlc_tgt=sda|writing|3500000000|3|2||||44200000000\n")
#: stopped: the agent went stopped -> idle and read the drawers again -- sda has no table
STOPPED = _local("idle", SDB, SDA_EMPTY) + MEASURED
DONE = (_local("done", SDB, SDA_WIN) + "lc_src=sdb|none\n"
        "lc_run=sdb|44200000000|44200000000|300|0|sent|0|\nlc_tgt=sda|done|44200000000|0|4||||44200000000\n")
#: the person's own choice: disk 2 the source, disk 3 the target, started from the screen
CHOOSE_AND_GO = ("char:2", "hit:NEXT", "hit:GO")


def roles(out: list[str]) -> list[str]:
    return [l.split("roles=", 1)[1] for l in out if l.startswith("@ screen")]


def test_the_r3_screen_a_stop_does_not_bring_the_empty_disk_back_as_the_source(driver, tmp_path):
    """R3 as found: the screen's own proposal (disk 3) was still its source
    when the copy sdb -> sda was stopped; after it disk 3 has no table."""
    out = drive(driver, tmp_path, BEFORE, "move:5,5", "state=" + RUN, "state=" + STOPPED, "move:5,5")
    seen = roles(out)
    assert seen[0] == "sdb:2,sda:1,", out          # the proposal before the run (#1322: drawer 3)
    assert seen[-1] == "sdb:0,sda:0,", out          # after the stop: nothing chosen for the person
    assert not any(r.startswith("lc-start") for r in records(out)), out


def test_a_screen_that_came_up_during_the_copy_proposes_nothing_after_it(driver, tmp_path):
    """The GUI restarted mid-copy: its first picture is the old inventory, and
    the run is not one it started -- there is no choice of the person's to keep."""
    out = drive(driver, tmp_path, RUN, "state=" + STOPPED, "move:5,5")
    assert roles(out)[-1] == "sdb:0,sda:0,", out


def test_after_a_stop_the_persons_own_choice_comes_back_exactly(driver, tmp_path):
    out = drive(driver, tmp_path, BEFORE + MEASURED, *CHOOSE_AND_GO, "state=" + RUN, "state=" + STOPPED, "move:5,5")
    assert "lc-start|sdb|sda|0" in records(out), out
    assert roles(out)[-1] == "sdb:1,sda:2,", out


def test_after_a_stop_a_choice_whose_disk_changed_is_cleared(driver, tmp_path):
    """The source in drawer 2 is another disk now (serial): the old choice is not valid."""
    swapped = STOPPED.replace("|S2|", "|S9|")
    out = drive(driver, tmp_path, BEFORE + MEASURED, *CHOOSE_AND_GO, "state=" + RUN, "state=" + swapped, "move:5,5")
    assert roles(out)[-1] == "sdb:0,sda:0,", out


def test_after_again_nothing_is_proposed_even_when_disk_three_has_a_system_now(driver, tmp_path):
    """'שכפול נוסף' (also the way on after a failure): disk 3 now holds the copy
    -- or half of one -- and looks like Windows. It is not proposed."""
    again = _local("idle", SDB, SDA_WIN) + MEASURED
    out = drive(driver, tmp_path, BEFORE + MEASURED, *CHOOSE_AND_GO, "state=" + DONE, "key:TAB", "key:ENTER",
                "state=" + again, "move:5,5")
    assert "lc-again" in records(out), out
    assert roles(out)[-1] == "sdb:0,sda:0,", out


def test_back_to_the_boot_choice_and_in_again_after_a_run_proposes_nothing(driver, tmp_path):
    out = drive(driver, tmp_path, BEFORE + MEASURED, *CHOOSE_AND_GO, "state=" + RUN, "state=" + STOPPED,
                "key:ESC", "state=" + BOOT, "state=" + STOPPED, "move:5,5")
    assert "lc-back" in records(out), out
    assert roles(out)[-1] == "sdb:0,sda:0,", out


def test_the_first_proposal_needs_a_system_on_the_disk(driver, tmp_path):
    """Drawer 3 has a table but nothing the inventory recognised: no source is
    proposed -- #1874 (Nadav 20/10): disk 2's Windows is not taken in its place.
    With no system anywhere, no source is proposed at all."""
    bare3 = "lc_disk=3|sdc|C|S3|500107862016|ok|ok||2|1|500107862016|1|\n"
    out = drive(driver, tmp_path, _local("idle", SDB, bare3), "move:5,5")
    assert roles(out)[-1] == "sdb:2,sdc:2,", out
    bare2 = SDB.replace("|Windows|", "||")
    out = drive(driver, tmp_path, _local("idle", bare2, bare3), "move:5,5")
    assert ":1," not in roles(out)[-1], out


def test_a_source_the_drawers_no_longer_allow_is_dropped(driver, tmp_path):
    """No run in between: the drawers were read again and the chosen source has
    no table now -- it is not the source any more."""
    out = drive(driver, tmp_path, BEFORE, "move:5,5", "state=" + _local("idle", SDB, SDA_EMPTY), "move:5,5")
    assert roles(out)[0] == "sdb:2,sda:1,", out
    assert ":1," not in roles(out)[-1], out
