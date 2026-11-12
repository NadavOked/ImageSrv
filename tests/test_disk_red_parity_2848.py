"""#2848 (Nadav 11/11): one table for the disk's colour, the same on the cloner and in the console.

red = SMART failed, a slow SATA link, a SATA problem (``link_reset`` / ``drawer_gone``), a slow
write check -- never a target, still a source. USB 2.0 and an ICRC in the disk's log are words.

The links this file locks (``test_disk_red_rules_2848`` has the local clone screen):

* **the agent's hello** (``hwissues.sh`` ``hw_sata_json`` via ``build_disk_entry``):
  ``sata_issues`` -- ``[]`` read and none, ``null`` not read (principle 5), else the words.
* **the server** (``room.py``): ``drawer_list[].sata_issues`` -- the known words only.
* **the cloner's records**: ``cdisk=`` field 17 = the hw words (``clonergui.sh``);
  ``room_drawer_rule=mi|serial|color|target|source|reason`` after each ``room_drawer=`` (``guistate.sh``) --
  the server's one rule (``drawer_list[].rule``, ``server/disks/disk_rule.py``), shown, not re-made.
* **the GUI** (``--png``, the Testrunner only): the standby bay and the room slot turn red on
  them; a USB 2.0 note does not colour the bay.
"""

from __future__ import annotations

import json

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_cloner_gui import sh as proc_sh
from test_crc_f1_native_1366 import _standby_record
from test_room_precheck_native_432_65 import _GUI, C1, _build_gui, _count, _render, gui_state_lines
import test_room_precheck_native_432_65 as precheck

pytestmark = requires_native(("bash", BASH))


def _sata_json(tmp_path, words: str | None) -> str:
    run = tmp_path / "run"; (run / "smart").mkdir(parents=True)
    if words is not None:
        (run / "smart/sda.hw").write_text(words, newline="\n")
    out = proc_sh(f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/smart/hwissues.sh; hw_sata_json sda')
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.mark.parametrize("words, want", [
    (None, None),                                         # no hw_refresh yet: not read, never []
    ("", []),
    ("usb2\nicrc_logged\nlink_slow\n", []),               # notes, and the link the server reads itself
    ("link_reset\ndrawer_gone\n", ["link_reset", "drawer_gone"]),
])
def test_the_hello_says_the_sata_problems_or_that_it_did_not_look(tmp_path, words, want):
    assert json.loads("{" + _sata_json(tmp_path, words).lstrip(",") + "}") == {"sata_issues": want}


def test_build_disk_entry_carries_sata_issues():
    text = (AGENT / "lib/hw/diskinfo.sh").read_text(encoding="utf-8")
    assert 'hw_sata_json "$_name"' in text


def test_the_server_passes_only_the_known_words():
    from server.rounds import room
    assert room._sata_issues(["link_reset", "usb2", "drawer_gone"]) == ["link_reset", "drawer_gone"]
    assert room._sata_issues([]) == []
    assert room._sata_issues(None) is None and room._sata_issues("link_reset") is None


def test_the_standby_record_carries_the_hw_words_in_field_17(tmp_path, monkeypatch):
    import test_crc_f1_native_1366 as base
    orig = base.proc_sh

    def with_words(cmd, *a, **k):   # the words where hw_refresh puts them, in the run _standby_record made
        (tmp_path / "run/smart/sda.hw").write_text("link_reset\nusb2\n", newline="\n")
        return orig(cmd, *a, **k)
    monkeypatch.setattr(base, "proc_sh", with_words)
    f = _standby_record(tmp_path, "ok ok 0 0 0 0", None)
    assert len(f) == 17 and f[16].split() == ["link_reset", "usb2"], f


@requires_native(("bash", BASH), "jq", why="gui_state builds the state with jq")
def test_gui_state_carries_the_servers_rule_after_each_room_drawer(tmp_path, monkeypatch):
    room = json.loads(json.dumps(precheck.ROOM))
    first, second = room["machines"][0]["drawer_list"]
    first.update(rule={"color": "red", "target": False, "source": True, "reason": "sata_issue"})
    second.pop("rule", None)                                  # an older server: no rule -- empty fields
    monkeypatch.setattr(precheck, "ROOM", room)
    lines = gui_state_lines(tmp_path)
    red = [l for l in lines if l.startswith("room_drawer_rule=0|")]
    assert red == [f"room_drawer_rule=0|{first['serial']}|red|0|1|sata_issue",
                   f"room_drawer_rule=0|{second['serial']}||||"], lines
    i = lines.index(red[0])
    assert lines[i - 1].startswith("room_drawer=0|1|"), lines   # right after its own drawer


STANDBY = ("hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
           "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0|\n"
           "cdisk=2|sdb|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0|||||||||{words}\n"
           "message=x|y\n")
NOTE = STANDBY.replace("passed||6.0|6.0|||||||||{words}", "passed||6.0|6.0|||||{hw}||||{words}")   # words = field 17, hw = field 13


@_GUI
def test_the_standby_bay_is_red_on_a_sata_problem_and_plain_on_a_usb2_note(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, STANDBY.format(words=""), tmp_path / "plain")
    _render(binary, STANDBY.format(words="link_reset"), tmp_path / "reset")
    _render(binary, NOTE.format(hw="מחובר ב-USB 2.0 — איטי; לחבר ליציאת USB 3", words="usb2"), tmp_path / "usb2")
    for theme in ("light", "dark"):
        p, r, u = (tmp_path / f"{n}-standby-{theme}.png" for n in ("plain", "reset", "usb2"))
        assert _count(r, "--clr-danger", theme) > _count(p, "--clr-danger", theme) + 200, theme
        assert _count(u, "--clr-warning", theme) == _count(p, "--clr-warning", theme), theme   # a note is no colour
        assert _count(u, "--clr-danger", theme) == _count(p, "--clr-danger", theme), theme


ROOM = (f"machine=CLONE-01|{C1}|1|1|2|waiting|-1|0|\n"
        "machine_drawers=0|2\n"
        "room_drawer=0|1|S1|M|256060514304|passed|1|1||||ok\n"
        "room_drawer=0|2|S2|M|256060514304|passed|1|1||||ok\n"
        "{red}"
        "round=img|1|1|0|5|4|5|-1|-1|-1\n"
        "message=x|y\n")


@_GUI
@pytest.mark.parametrize("red", ["room_drawer_rule=0|S2|red|0|1|sata_issue\n", "room_drawer_rule=0|S2|red|0|1|write_slow\n"])
def test_the_room_slot_is_red_on_a_sata_problem_or_a_slow_write(tmp_path, red):
    binary = _build_gui(tmp_path)
    _render(binary, ROOM.format(red=""), tmp_path / "a")
    _render(binary, ROOM.format(red=red), tmp_path / "b")
    _render(binary, ROOM.format(red="room_drawer_rule=0|S9|red|0|1|sata_issue\n"), tmp_path / "c")   # another serial
    for theme in ("light", "dark"):
        a, b, c = (sum(_count(f, "--clr-danger", theme) for f in tmp_path.glob(f"{n}-room*-{theme}.png")) for n in "abc")
        assert b > a + 100, (theme, a, b)
        assert c == a, (theme, a, c)


# --- #2848 (Nadav 11/11): yellow -- failed the last clone / a cut write -- its own colour, not SMART's orange

YELLOW_STANDBY = ("hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
                  "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0|\n"
                  "cdisk=2|sdb|Samsung SSD 870 EVO|500107862016|{smart}|{cause}|6.0|6.0||||{mark}|||||\n"
                  "message=x|y\n")


@_GUI
@pytest.mark.parametrize("smart, cause, mark", [("failed_last", "cable", ""), ("passed", "", "incomplete")])
def test_the_standby_bay_is_yellow_not_orange(tmp_path, smart, cause, mark):
    binary = _build_gui(tmp_path)
    _render(binary, YELLOW_STANDBY.format(smart="passed", cause="", mark=""), tmp_path / "plain")
    _render(binary, YELLOW_STANDBY.format(smart=smart, cause=cause, mark=mark), tmp_path / "yel")
    for theme in ("light", "dark"):
        p, y = (tmp_path / f"{n}-standby-{theme}.png" for n in ("plain", "yel"))
        assert _count(y, "--clr-yellow", theme) > _count(p, "--clr-yellow", theme) + 200, theme
        assert _count(y, "--clr-warning", theme) <= _count(p, "--clr-warning", theme) + 50, theme   # not orange


@_GUI
def test_the_room_slot_is_yellow_by_the_servers_rule(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, ROOM.format(red=""), tmp_path / "a")
    _render(binary, ROOM.format(red="room_drawer_rule=0|S2|yellow|1|0|failed_last\n"), tmp_path / "b")
    for theme in ("light", "dark"):
        a, b = (sum(_count(f, "--clr-yellow", theme) for f in tmp_path.glob(f"{n}-room*-{theme}.png")) for n in "ab")
        assert b > a + 100, (theme, a, b)


# --- #2897 (Nadav 11/11): "SMART לא נבדק" is grey -- the neutral --led-idle -- never the success green

UNCHECKED = ("hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
             "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|{smart}||6.0|6.0|\n"
             "message=x|y\n")


@_GUI
def test_the_standby_unchecked_disk_is_grey_not_green(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, UNCHECKED.format(smart="passed"), tmp_path / "ok")
    _render(binary, UNCHECKED.format(smart="unchecked"), tmp_path / "un")
    for theme in ("light", "dark"):
        ok, un = (tmp_path / f"{n}-standby-{theme}.png" for n in ("ok", "un"))
        green_ok, green_un = _count(ok, "--clr-success", theme), _count(un, "--clr-success", theme)
        assert green_ok > green_un + 30, (theme, green_ok, green_un)   # the chip's dot and words, the LED
        assert _count(un, "--clr-warning", theme) <= _count(ok, "--clr-warning", theme) + 20, theme   # not orange either
