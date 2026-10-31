"""#1366 (Nadav 13/10) in the native GUI: the absolute CRC threshold and the F1 reason.

The links:

* **the kiosk** (``clonergui.sh``): ``cdisk=`` field 9 = the CRC count when it is
  over the hello's ``crc_warn_threshold`` (empty otherwise, or with an older
  server); on ``fail`` field 6 = the SMART reason of the F1 stop.
* **the agent** (``guistate.sh``): ``room_drawer=`` field 13 = the count when the
  server says ``crc_suspect``.
* **the GUI** (``--png``, the Testrunner only): the standby bay and the live room
  slot turn orange on it, like a slow link.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_cloner_gui import sh as proc_sh
from test_disklink_432 import _sata
from test_room_precheck_native_432_65 import _GUI, C1, _build_gui, _count, _render, gui_state_lines
import test_room_precheck_native_432_65 as precheck
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))


def _standby_record(tmp_path: Path, smart: str, hello: dict | None) -> list[str]:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    sysroot = tmp_path / "sysroot"
    (sysroot / "sys/block/sda/device").mkdir(parents=True)
    (sysroot / "sys/block/sda/size").write_text("1000\n", newline="\n")
    (sysroot / "sys/block/sda/ro").write_text("0\n", newline="\n")   # #1742: unread = not checked
    _sata(sysroot)
    (run / "smart").mkdir()
    (run / "smart/sda.v").write_text(smart + "\n", newline="\n")      # verdict reason realloc pending uncorr crc
    if hello is not None:
        (run / "response.json").write_text(json.dumps(hello), newline="\n")
    out = proc_sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
                  f'SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; '
                  f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; cloner_gui_state')
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr
    (line,) = [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("cdisk=")]
    return line.split("|")


@pytest.mark.parametrize("smart, hello, want", [
    ("ok ok 0 0 0 667", {"crc_warn_threshold": 500}, "667"),     # 13/10: the disk that failed at 0.86GB
    ("ok ok 0 0 0 500", {"crc_warn_threshold": 500}, ""),        # over, not at
    ("ok ok 0 0 0 55242", {"crc_warn_threshold": 0}, ""),        # 0 = off
    ("ok ok 0 0 0 1752", {"schema": 1}, ""),                     # an older server: no threshold, no colour
    ("ok ok 0 0 0 1752", None, ""),                              # no hello answer read yet
    ("unchecked open_failed 0 0 0 0", {"crc_warn_threshold": 500}, ""),   # 199 never read
])
def test_the_standby_record_carries_the_crc_only_over_the_threshold(tmp_path, smart, hello, want):
    assert _standby_record(tmp_path, smart, hello)[8] == want


@pytest.mark.parametrize("reason", ["health_failed", "pending_sectors", "uncorrectable"])
def test_a_fail_carries_its_f1_reason_in_field_six(tmp_path, reason):
    f = _standby_record(tmp_path, f"fail {reason} 0 4 2 0", {"crc_warn_threshold": 500})
    assert f[4:6] == ["fail", reason], f


@requires_native(("bash", BASH), "jq", why="gui_state builds the state with jq")
def test_gui_state_carries_the_crc_as_field_13(tmp_path, monkeypatch):
    room = json.loads(json.dumps(precheck.ROOM))
    first, second = room["machines"][0]["drawer_list"]
    first.update(crc=667, crc_suspect=True)
    second.update(crc=137, crc_suspect=False)
    monkeypatch.setattr(precheck, "ROOM", room)
    lines = gui_state_lines(tmp_path)
    crc = {l.split("|")[1]: l.split("|")[12] for l in lines if l.startswith("room_drawer=0|")}
    assert crc == {"1": "667", "2": ""}, lines


STANDBY = ("hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
           "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0|\n"
           "cdisk=2|sdb|Samsung SSD 870 EVO|500107862016|{smart}|{cause}|6.0|6.0|{crc}\n"
           "message=x|y\n")


@_GUI
def test_the_standby_bay_turns_orange_over_the_crc_threshold(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, STANDBY.format(smart="passed", cause="", crc=""), tmp_path / "clean")
    _render(binary, STANDBY.format(smart="passed", cause="", crc="667"), tmp_path / "crc")
    for theme in ("light", "dark"):
        clean = _count(tmp_path / f"clean-standby-{theme}.png", "--clr-warning", theme)
        crc = _count(tmp_path / f"crc-standby-{theme}.png", "--clr-warning", theme)
        assert crc - clean > 300, (theme, clean, crc)


@_GUI
def test_the_f1_reason_is_drawn_in_words(tmp_path):
    """Field 6 on a fail is read: two reasons draw two different bays."""
    binary = _build_gui(tmp_path)
    _render(binary, STANDBY.format(smart="fail", cause="pending_sectors", crc=""), tmp_path / "p")
    _render(binary, STANDBY.format(smart="fail", cause="uncorrectable", crc=""), tmp_path / "u")
    assert (tmp_path / "p-standby-light.png").read_bytes() != (tmp_path / "u-standby-light.png").read_bytes()


ROOM_LIVE = (f"machine=CLONE-01|{C1}|1|1|2|waiting|-1|0|\n"
             "machine_drawers=0|2\n"
             "room_drawer=0|1|S1|M|256060514304|passed|1|1||||ok|\n"
             "room_drawer=0|2|S2|M|256060514304|passed|1|1||||ok|{crc}\n"
             "round=img|1|0|0|5|4|5|-1|-1|-1\n"
             "message=x|y\n")


@_GUI
def test_the_live_room_slot_turns_orange_over_the_crc_threshold(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, ROOM_LIVE.format(crc=""), tmp_path / "a")
    _render(binary, ROOM_LIVE.format(crc="1752"), tmp_path / "b")
    for theme in ("light", "dark"):
        # every room screen of the render (the grid of the distribute screen draws the slots)
        a = sum(_count(f, "--clr-warning", theme) for f in tmp_path.glob(f"a-room*-{theme}.png"))
        b = sum(_count(f, "--clr-warning", theme) for f in tmp_path.glob(f"b-room*-{theme}.png"))
        assert b - a > 100, (theme, a, b, sorted(p.name for p in tmp_path.glob("b-*.png")))
