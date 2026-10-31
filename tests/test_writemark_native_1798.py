"""#1798 in the native GUI: a disk whose last write was cut is orange, on every
inventory screen of the cloner, and says "כתיבה לא הושלמה — לא שמיש".

The records (writemark.sh, read by the agent; test_writemark_1798.py):

* ``cdisk=`` field 12 -- the standby screen and the restore-from-server screen
  ("משיכה", lc_pull.c), both drawn from the same records;
* ``lc_disk=`` field 16 -- the local clone screen, where the agent also set
  ``usable=0`` and ``why`` (never a source).

Each screen is rendered twice by ``--png`` (the Testrunner only), the same
state with the mark ``clean`` and ``incomplete``: the orange of the theme
(``--clr-warning``) grows on the marked one, in light and in dark. ``clean``
draws nothing -- the negative control is the pair itself.
"""

from __future__ import annotations

from test_room_precheck_native_432_65 import _GUI, _build_gui, _count, _render

MARK_HE = "כתיבה לא הושלמה — לא שמיש"

STANDBY = ("hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
           "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0||1|SATA 0|clean\n"
           "cdisk=2|sdb|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0||2|SATA 1|{mark}\n"
           "message=x|y\n")

PULL = ("lc_screen=pull\nmachine_name=CLONER-02\n"
        "image=img_7f3a91|Office 2024|Office\n"
        "cdisk=1|sdc|Crucial MX500|480103981056|passed||6.0|6.0||1|SATA 2|{mark}\n"
        "cdisk=2|sda|Samsung SSD 870|500107862016|passed||6.0|6.0||2|SATA 0|clean\n"
        "message=x|y\n")   # every --png run also draws the message card

LOCAL = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
         "lc_disk=1|sda|Samsung|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1||1|SATA 0|clean\n"
         "lc_disk=2|sdb|MZ7|S2|256060514304|ok|ok|Windows|4|44200000000|256060514304|{usable}|{why}|2|SATA 1|{mark}\n"
         "lc_disk=3|sdc|Samsung 870|S3|500107862016|ok|ok|Windows|4|148600000000|480103981056|1||3|SATA 2|clean\n"
         "lc_src=sdc|none\nlc_fit=sda|fits|480103981056|\nlc_fit=sdb|fits|480103981056|\n"
         "message=x|y\n")


def _orange(tmp_path, state: str, clean: dict, marked: dict, card: str) -> dict[str, tuple[int, int]]:
    binary = _build_gui(tmp_path)
    _render(binary, state.format(**clean), tmp_path / "clean")
    _render(binary, state.format(**marked), tmp_path / "marked")
    return {theme: (_count(tmp_path / f"clean-{card}-{theme}.png", "--clr-warning", theme),
                    _count(tmp_path / f"marked-{card}-{theme}.png", "--clr-warning", theme))
            for theme in ("light", "dark")}


@_GUI
def test_the_standby_bay_of_a_cut_disk_is_orange(tmp_path):
    seen = _orange(tmp_path, STANDBY, {"mark": "clean"}, {"mark": "incomplete"}, "standby")
    assert all(marked - clean > 300 for clean, marked in seen.values()), seen


@_GUI
def test_a_mark_that_was_not_read_is_said_in_grey_not_orange(tmp_path):
    """`unchecked` is never "clean" and never drawn as a warning either: a grey line."""
    seen = _orange(tmp_path, STANDBY, {"mark": "clean"}, {"mark": "unchecked"}, "standby")
    assert all(abs(marked - clean) < 50 for clean, marked in seen.values()), seen
    assert (tmp_path / "clean-standby-light.png").read_bytes() != (tmp_path / "marked-standby-light.png").read_bytes()


@_GUI
def test_the_restore_from_the_server_drawer_of_a_cut_disk_is_orange(tmp_path):
    seen = _orange(tmp_path, PULL, {"mark": "clean"}, {"mark": "incomplete"}, "lcpull")
    assert all(marked - clean > 100 for clean, marked in seen.values()), seen


@_GUI
def test_the_local_clone_bay_of_a_cut_disk_is_orange_and_says_why(tmp_path):
    seen = _orange(tmp_path, LOCAL, {"usable": "1", "why": "", "mark": "clean"},
                   {"usable": "0", "why": MARK_HE, "mark": "incomplete"}, "lcsource")
    assert all(marked - clean > 300 for clean, marked in seen.values()), seen
