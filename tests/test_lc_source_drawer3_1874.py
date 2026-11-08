"""#1874 -- the local clone proposes drawer 3 as the source, or nothing.

Nadav 20/10: "במכללה אני רוצה שזה יהיה כמו היום: שהוא מציע את דיסק 3 כמקור,
ואפשר לשנות." The Issue: when the proposed drawer has no system, or is empty,
there is no proposed source ("בחרו מקור") -- never another disk quietly in its
place. The screen (``lc_state.c`` ``default_roles``) took the first disk with a
system anywhere when drawer 3 had none: a source the operator did not see
proposed for the reason they expect, and every other drawer a target.

The clone stick (``lc_usb=1``, #2008 "Enter, Enter") is the same rule (Nadav
07/11): SATA 2 is the source -- drawer 3 by the mapping, SATA 0 = drawer 1 --
and the mapping the operator sets in the stick's settings (#1959/#2001, the
``drawer`` of ``lc_disk``) moves it. No disk in drawer 3, or not proposable:
no source, never "the first disk with a system".

Driven through the real ``lc_handle``/``lc_after_reload`` by the driver of
``test_native_local_clone.py`` (Linux only). Roles: 0 דלג, 1 מקור, 2 יעד.
"""

from __future__ import annotations

import shutil

from native import requires_native
from test_cloner_gui import BASH, _pkgconfig
from test_native_local_clone import drive, driver, records  # noqa: F401

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

LOCAL = "lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
D1_WIN = "lc_disk=1|sda|Samsung|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
D2_LINUX = "lc_disk=2|sdb|WDC|S2|500107862016|ok|ok|Ubuntu|2|38000000000|500107862016|1|\n"
D3_WIN = "lc_disk=3|sdc|Crucial|S3|480103981056|ok|ok|Windows|4|148600000000|480103981056|1|\n"
#: no partition table (the inventory: not usable)
D3_EMPTY = "lc_disk=3|sdc|Crucial|S3|480103981056|ok|ok||0|0|-1|0|אין עליו טבלת מחיצות\n"
#: a table, but no system the inventory recognised
D3_BARE = "lc_disk=3|sdc|Crucial|S3|480103981056|ok|ok||2|1|480103981056|1|\n"
#: #1798: Windows on it, but the last write to it was cut -- never a source
D3_CUT = ("lc_disk=3|sdc|Crucial|S3|480103981056|ok|ok|Windows|4|148600000000|480103981056|0|"
          "הכתיבה האחרונה אליו לא הושלמה|||incomplete\n")


def roles(out: list[str]) -> str:
    seen = [l.split("roles=", 1)[1] for l in out if l.startswith("@ screen")]
    assert seen, out
    return seen[-1]


def test_drawer_three_with_windows_is_the_proposed_source(driver, tmp_path):
    """Drawer 1 has Windows too: the proposal is drawer 3, not the first disk with a system."""
    out = drive(driver, tmp_path, LOCAL + D1_WIN + D2_LINUX + D3_WIN, "move:5,5")
    assert roles(out) == "sda:2,sdb:2,sdc:1,", out
    assert "lc-measure|sdc" in out, out


def test_an_empty_drawer_three_proposes_no_source(driver, tmp_path):
    out = drive(driver, tmp_path, LOCAL + D1_WIN + D2_LINUX + D3_EMPTY, "move:5,5")
    assert ":1," not in roles(out), out
    assert not any(l.startswith("lc-measure") for l in out), out


def test_drawer_three_with_no_system_proposes_no_source(driver, tmp_path):
    out = drive(driver, tmp_path, LOCAL + D1_WIN + D2_LINUX + D3_BARE, "move:5,5")
    assert roles(out) == "sda:2,sdb:2,sdc:2,", out


def test_a_cut_write_in_drawer_three_proposes_no_source(driver, tmp_path):
    """#1798's disk is not proposed, and neither is drawer 1 in its place."""
    out = drive(driver, tmp_path, LOCAL + D1_WIN + D2_LINUX + D3_CUT, "move:5,5")
    assert ":1," not in roles(out), out


def test_no_disk_in_drawer_three_proposes_no_source(driver, tmp_path):
    out = drive(driver, tmp_path, LOCAL + D1_WIN + D2_LINUX, "move:5,5")
    assert roles(out) == "sda:2,sdb:2,", out


def test_with_no_source_nothing_can_start(driver, tmp_path):
    """Even with the agent's measurement of drawer 1 at hand: the screen chose nothing."""
    measured = "lc_src=sda|none\nlc_fit=sdb|fits|500107862016|\n"
    out = drive(driver, tmp_path, LOCAL + D1_WIN + D2_LINUX + measured, "hit:NEXT", "hit:GO")
    assert not any(r.startswith("lc-start") for r in records(out)), out


def test_the_operator_chooses_another_source_on_the_screen(driver, tmp_path):
    """"ואפשר לשנות": drawer 3 proposed, the person takes drawer 1, and the start says so."""
    measured = "lc_src=sda|none\nlc_fit=sdb|fits|500107862016|\nlc_fit=sdc|fits|500107862016|\n"
    out = drive(driver, tmp_path, LOCAL + D1_WIN + D2_LINUX + D3_WIN + measured, "char:1", "hit:NEXT", "hit:GO")
    assert "lc-measure|sda" in out, out
    assert records(out)[-1] == "lc-start|sda|sdb,sdc|0", out


USB = "lc_usb=1\n"


def mapped(line: str, drawer: int) -> str:
    """``line`` with the drawer of the stick's mapping (fields 14-15 of lc_disk)."""
    return line.rstrip("\n") + f"|{drawer}|SATA {drawer - 1}\n"


def test_the_stick_proposes_sata_2_as_the_source(driver, tmp_path):
    """Three disks, no mapping on the line: the port is the drawer, SATA 2 = port 3 = drawer 3."""
    out = drive(driver, tmp_path, LOCAL + USB + D1_WIN + D2_LINUX + D3_WIN, "move:5,5")
    assert roles(out) == "sda:2,sdb:2,sdc:1,", out
    assert "lc-measure|sdc" in out, out


def test_the_stick_with_no_disk_in_drawer_three_proposes_no_source(driver, tmp_path):
    """Was: the first disk with a system (sda). Now "בחרו מקור", and nothing can start."""
    measured = "lc_src=sda|none\nlc_fit=sdb|fits|500107862016|\n"
    out = drive(driver, tmp_path, LOCAL + USB + D1_WIN + D2_LINUX + measured, "move:5,5", "hit:NEXT", "hit:GO")
    assert roles(out) == "sda:2,sdb:2,", out
    assert not any(l.startswith("lc-measure") for l in out), out
    assert not any(r.startswith("lc-start") for r in records(out)), out


def test_the_stick_with_an_unusable_drawer_three_proposes_no_source(driver, tmp_path):
    """Drawer 3 has no table: not drawer 1 in its place (the cloner's rule, the stick's too)."""
    out = drive(driver, tmp_path, LOCAL + USB + D1_WIN + D2_LINUX + D3_EMPTY, "move:5,5")
    assert ":1," not in roles(out), out
    out = drive(driver, tmp_path, LOCAL + USB + D1_WIN + D2_LINUX + D3_CUT, "move:5,5")
    assert ":1," not in roles(out), out


def test_the_mapping_of_the_stick_moves_the_source(driver, tmp_path):
    """The operator's "מיפוי חיבורים": the disk on SATA 0 is drawer 3 now, so it is the source."""
    disks = mapped(D1_WIN, 3) + mapped(D2_LINUX, 1) + mapped(D3_WIN, 2)
    out = drive(driver, tmp_path, LOCAL + USB + disks, "move:5,5")
    assert roles(out) == "sda:1,sdb:2,sdc:2,", out
    assert "lc-measure|sda" in out, out


def test_a_sata_2_the_mapping_leaves_out_is_not_the_source(driver, tmp_path):
    """Port 3 is not a drawer (mapped out, drawer 0): the stick proposes nothing, port or no port."""
    disks = mapped(D1_WIN, 1) + mapped(D2_LINUX, 2) + D3_WIN.rstrip("\n") + "|0|SATA 2\n"
    out = drive(driver, tmp_path, LOCAL + USB + disks, "move:5,5")
    assert ":1," not in roles(out), out


def test_the_stick_with_disks_on_sata_0_and_2_only_proposes_the_one_on_sata_2(driver, tmp_path):
    """The agent's default now (usbmap.sh): drawers 1 and 3, an empty drawer 2 between them."""
    disks = mapped(D1_WIN, 1) + mapped(D3_WIN, 3)
    out = drive(driver, tmp_path, LOCAL + USB + disks, "move:5,5")
    assert roles(out) == "sda:2,sdc:1,", out
    assert "lc-measure|sdc" in out, out
