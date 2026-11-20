"""#2996 / #2633 -- the build machine's live room says "נבדק" only for a drawer the server says was read back.

``screens_room.c`` wrote "הושלם · נבדק" as a constant on every ``done`` drawer: "we could not check"
shown as "we checked" (principle 5), the same defect #2613 fixed on the cloner's own screen. The server
already serves ``drawer_list[].readback`` (verified | sampled | unverified, ``readback_why``); the
kiosk (``guistate.sh``) now carries it as ``room_drawer_rb=mi|serial|verdict|why`` and the screen says,
in the words of the cloner's screen:

* ``unverified``      -> "הושלם · לא אומת", orange (``list_not_fetched``: "…— הרשימה לא הורדה")
* ``sampled``         -> "הושלם · נבדק במדגם"
* ``verified``        -> "הושלם · נבדק"
* no verdict at all   -> "הושלם", and nothing about a check

The agent layer runs with bash + jq; the screen is C and runs on the lab only (``requires_native``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from native import requires_native
from test_agent import BASH
from test_cloner_gui import GUI, _build_gui, posix
import test_room_precheck_native_432_65 as precheck
from test_room_precheck_native_432_65 import _GUI, _count, _render, gui_state_lines

SRC = GUI / "src"


# --- the wire: the server's verdict reaches the kiosk's state file -----------------------------


@requires_native(("bash", BASH), "jq", why="gui_state builds the state with jq")
def test_the_state_carries_the_servers_verdict_after_each_room_drawer(tmp_path, monkeypatch):
    room = json.loads(json.dumps(precheck.ROOM))
    first, second = room["machines"][0]["drawer_list"]
    first.update(readback="unverified", readback_why="list_not_fetched")
    second.pop("readback", None)                              # an older server / a drawer not checked yet: nothing said
    third, _ = room["machines"][1]["drawer_list"]
    third.update(readback="sampled", readback_why=None)
    monkeypatch.setattr(precheck, "ROOM", room)
    lines = gui_state_lines(tmp_path)
    rb = [l for l in lines if l.startswith("room_drawer_rb=")]
    assert f"room_drawer_rb=0|{first['serial']}|unverified|list_not_fetched" in rb, rb
    assert f"room_drawer_rb=0|{second['serial']}||" in rb, rb      # empty = not reported, never "verified"
    assert f"room_drawer_rb=1|{third['serial']}|sampled|" in rb, rb


# --- the screen: drawn for real ---------------------------------------------------------------

G = 500107862016
MARKS = {c: None for c in (0x200E, 0x200F, *range(0x2066, 0x206A))}   # bidi marks the screen puts round a run


def _room(rows: list[tuple[str, str]]) -> str:
    """rows = (serial, room_drawer_rb tail 'verdict|why' or '' for none); every drawer is done."""
    out = ("room_label=מחשבי שיכפול\n"
           f"machine=CLONE-01|{precheck.C1}|1|1|0|done|100|0|\nmachine_drawers=0|{len(rows)}\n")
    for port, (serial, _) in enumerate(rows, 1):
        out += (f"room_drawer=0|{port}|{serial}|Samsung SSD 870 EVO|{G}|passed|0|1||||ok||||100|done|sd{'abcd'[port - 1]}||0\n")
    for serial, rb in rows:
        if rb:
            out += f"room_drawer_rb=0|{serial}|{rb}\n"
    return out + "round=office365|1|0|4|4|0|0|192|68000000|300|running\nmessage=x|y\n"


def _words(binary, tmp_path, state: str, name: str) -> list[str]:
    """Every string the live room laid out (IMAGESRV_GUI_TEXTLOG), the bidi marks stripped."""
    log = tmp_path / f"{name}.log"
    log.write_text("")
    os.environ["IMAGESRV_GUI_TEXTLOG"] = posix(log)
    try:
        _render(binary, state, tmp_path / name)
    finally:
        del os.environ["IMAGESRV_GUI_TEXTLOG"]
    out, on = [], False
    for l in log.read_text(encoding="utf-8").splitlines():   # the run draws every card: take the live room's
        if l.startswith("@@ "):
            on = l == "@@ room-live-light"
        elif on:
            out.append(l.translate(MARKS))
    assert out, "no text logged for room-live-light"
    return out


@_GUI
def test_each_verdict_has_its_words_and_none_says_checked_by_default(tmp_path):
    binary = _build_gui(tmp_path)
    w = _words(binary, tmp_path, _room([("S1", "verified|"), ("S2", "sampled|"), ("S3", "unverified|"), ("S4", "")]), "mix")
    assert "הושלם · נבדק" in w, w
    assert "הושלם · נבדק במדגם" in w, w
    assert "הושלם · לא אומת" in w, w
    assert "הושלם" in w, w                                       # the drawer nobody reported on: it finished, nothing more
    assert w.count("הושלם · נבדק") == 1, w                      # one drawer was verified, not four


@_GUI
def test_a_drawer_with_no_verdict_makes_no_claim_about_a_check(tmp_path):
    binary = _build_gui(tmp_path)
    w = _words(binary, tmp_path, _room([("S1", ""), ("S2", "")]), "none")
    assert "הושלם" in w, w
    assert not any("נבדק" in s or "אומת" in s for s in w), w


@_GUI
def test_an_unverified_drawer_is_not_shown_as_checked_and_a_list_that_did_not_come_down_says_so(tmp_path):
    binary = _build_gui(tmp_path)
    w = _words(binary, tmp_path, _room([("S1", "unverified|"), ("S2", "unverified|list_not_fetched")]), "unv")
    assert "הושלם · לא אומת" in w and "הושלם · לא אומת — הרשימה לא הורדה" in w, w
    assert not [s for s in w if s.startswith("הושלם · נבדק")], w


@_GUI
def test_a_verdict_that_is_not_one_of_the_three_is_not_read_as_checked(tmp_path):
    binary = _build_gui(tmp_path)
    w = _words(binary, tmp_path, _room([("S1", "ok|"), ("S2", "VERIFIED|"), ("S3", "full|")]), "junk")
    assert not any("נבדק" in s or "אומת" in s for s in w), w


@_GUI
def test_an_unverified_row_is_orange_and_a_sampled_one_is_not(tmp_path):
    """The words are not enough: the row's line is the orange of #943's warning. Same screen, one verdict apart."""
    binary = _build_gui(tmp_path)
    _render(binary, _room([("S1", "unverified|"), ("S2", "sampled|")]), tmp_path / "unv")
    _render(binary, _room([("S1", "sampled|"), ("S2", "sampled|")]), tmp_path / "smp")
    for theme in ("light", "dark"):
        unv, smp = (tmp_path / f"{n}-room-live-{theme}.png" for n in ("unv", "smp"))
        assert _count(unv, "--clr-warning", theme) > _count(smp, "--clr-warning", theme) + 40, theme


# --- the source: no constant claim is left -----------------------------------------------------


def test_the_room_screen_has_no_constant_checked_line():
    text = (SRC / "screens_room.c").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    lines = [l for l in code.splitlines() if '"הושלם · נבדק")' in l]
    assert lines, "the verified branch is gone"
    assert all('rb_verdict, "verified"' in l for l in lines), lines   # never a constant: only under the server's "verified"
    assert "rb_verdict" in code and "unverified" in code and "sampled" in code
