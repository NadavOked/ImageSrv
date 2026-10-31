"""#2131 -- the clone stick beeps as the cloner does (#2067), and its own settings mute it.

* The same five-tune table (agent/lib/roundbeep.sh) -- there is no second copy. On the
  stick there is no server: usb_answer writes the answer a hello would have given, and
  the tunes play from the stick's local clone exactly where they play on a cloner --
  lc_end (all done = success; partial, or the source failed = failure) and drawer_watch
  (a drawer pulled = pulled, a drawer dead mid-copy = failure). netlost/netback are
  the network's, and the stick has none.
* The switch: `cloner_beep` in settings.json on the stick's data partition, set on the
  stick's settings screen (usbset.sh), the same name as the server's setting. It
  reaches roundbeep.sh as `ui.cloner_beep` in response.json -- where the server's
  switch comes on a cloner -- and it holds after a reboot. Only a JSON true/false is
  taken; anything else is refused aloud and the beep stays on (missing = on).

Every check decodes the bytes written to a fake "PC Speaker" evdev node; nothing here
touches a real device or disk.
"""

from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

import pytest

from test_beeps_2067 import TUNES
from test_cloner_gui import posix
from test_usb_clone import KIOSK, LIB, needs_jq, run

BEEP_ITEM = 5                                   # the item's index on the settings screen
TO_BEEP = ["KEY DOWN"] * BEEP_ITEM + ["KEY ENTER"]
FLIP = ["KEY LEFT", "KEY ENTER"]                # the other choice, saved
TO_RESET = ["KEY DOWN"] * (BEEP_ITEM + 2) + ["KEY ENTER", "KEY TAB", "KEY ENTER"]


def expected(*kinds: str) -> list[int]:
    return [value for kind in kinds for hz, _on, _off in TUNES[kind] for value in (hz, 0)]


def speaker(tmp_path: Path) -> str:
    """A "PC Speaker" input device; roundbeep.sh sourced the way the agent does; sleep instant."""
    device = tmp_path / "sys/event5/device"
    if not device.is_dir():
        device.mkdir(parents=True)
        (device / "name").write_text("PC Speaker\n", encoding="utf-8", newline="\n")
        (tmp_path / "dev/input").mkdir(parents=True)
    return (f'export SYSFS_INPUT={posix(tmp_path / "sys")!r} DEVROOT={posix(tmp_path / "dev")!r}; '
            f'LOG_FILE="$RUN_DIR/agent.log"; . {posix(LIB)}/roundbeep.sh; sleep() {{ :; }}; ')


def tones(tmp_path: Path) -> list[int]:
    node = tmp_path / "dev/input/event5"
    data = node.read_bytes() if node.is_file() else b""
    assert len(data) % 24 == 0
    events = list(struct.iter_unpack("<qqHHi", data))
    assert all((kind, code) == (0x12, 0x02) for _s, _u, kind, code, _v in events)
    return [value for *_x, value in events]


def stick(tmp_path: Path, script: str, *, reboot: bool = False):
    """One boot of the stick: the data partition mounted, settings.json on it (tmp/usbdata).
    reboot=True starts from an empty RAM ($RUN_DIR) and an unplayed speaker -- only the
    stick's partition is kept."""
    usb = tmp_path / "usbdata"; usb.mkdir(exist_ok=True)
    (tmp_path / "sysroot").mkdir(exist_ok=True)   # no disks, no model: never this host's /sys
    if reboot:
        shutil.rmtree(tmp_path / "run", ignore_errors=True)
        (tmp_path / "dev/input/event5").unlink(missing_ok=True)
    pre = speaker(tmp_path) + KIOSK + f'echo mounted > "$RUN_DIR/usb.data"; USB_DATA_DIR={posix(usb)!r}; '
    out = run(tmp_path, pre + script, IMAGECTL_MODE="usbclone", SYSROOT=posix(tmp_path / "sysroot"))
    assert out.returncode == 0, out.stdout + out.stderr
    return out, usb


# What usbclone_main writes before the first screen -- the stick's answer. No D_ROLE:
# the role is the answer's, as the kiosk reads it.
BOOT = 'usb_data_state_read; usb_settings_load; usb_answer || exit 9; '


def lc_end_with(states: tuple[str, str]) -> str:
    """The real lc_end of the local clone, two targets in these states."""
    return (BOOT + '_lr_r="$LC/run"; mkdir -p "$_lr_r"; _lr_all="sda sdb "; _lr_beat=999999; '
            'mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb"; '
            f'echo {states[0]} > "$RUN_DIR/targets/sda/state"; echo {states[1]} > "$RUN_DIR/targets/sdb/state"; '
            'lc_report() { :; }; wait_pid() { :; }; lc_end; wait; ')


def drawer_dies(pulled: bool) -> str:
    """The real drawer_watch (lccopy.sh runs it beside the copy): one drawer goes."""
    why = '"$DRAWER_GONE_WHY"' if pulled else "media-error"
    return (BOOT + 'mkdir -p "$RUN_DIR/targets/sda"; echo writing > "$RUN_DIR/targets/sda/state"; turns=0; '
            'kill() { [ "$1" = -0 ] || return 0; turns=$((turns + 1)); [ "$turns" = 1 ]; }; '
            f'_nap() {{ :; }}; dmesg() {{ :; }}; drawer_dead_reason() {{ echo {why}; }}; '
            'fail_written_target() { echo failed > "$RUN_DIR/targets/$1/state"; }; '
            'drawer_watch 123 1 sda; wait; ')


EVENTS = {
    "success": (lc_end_with(("done", "done")), ["success"]),
    "failure at the end": (lc_end_with(("done", "failed")), ["failure"]),
    "the source failed": (lc_end_with(("failed", "failed")), ["failure"]),
    "a drawer died mid-copy": (drawer_dies(False), ["failure"]),
    "a drawer was pulled": (drawer_dies(True), ["pulled"]),
}


@needs_jq
@pytest.mark.parametrize("event", list(EVENTS))
def test_the_stick_plays_the_cloners_tune_for_each_event(tmp_path, event):
    script, kinds = EVENTS[event]
    out, _usb = stick(tmp_path, script)
    assert tones(tmp_path) == expected(*kinds), out.stdout + out.stderr
    answer = json.loads((tmp_path / "run" / "response.json").read_text(encoding="utf-8"))
    assert answer["ui"] == {"cloner_beep": True}                  # missing on the stick = on


@needs_jq
def test_the_mute_is_saved_on_the_stick_and_holds_after_a_reboot(tmp_path):
    out, usb = stick(tmp_path, "usbset_open; " + "".join(f"usbset_key {k!r}; " for k in TO_BEEP + FLIP)
                     + 'cat "$GUI_DIR/usbset.frame"')
    assert json.loads((usb / "settings.json").read_text(encoding="utf-8")) == {"cloner_beep": False}
    answer = json.loads((tmp_path / "run" / "response.json").read_text(encoding="utf-8"))
    assert answer["ui"] == {"cloner_beep": False}                 # in force at once, this boot
    assert "prow\tעכשיו\t\tכבוי\tmono\t\tok\tנשמר" in out.stdout, out.stdout

    out, _usb = stick(tmp_path, lc_end_with(("done", "done")), reboot=True)
    assert tones(tmp_path) == [], out.stdout + out.stderr         # the next boot: still silent
    log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
    assert "beep success: off in the console settings -- not heard" in log

    stick(tmp_path, "usbset_open; " + "".join(f"usbset_key {k!r}; " for k in TO_BEEP + FLIP))
    assert json.loads((usb / "settings.json").read_text(encoding="utf-8")) == {"cloner_beep": True}
    out, _usb = stick(tmp_path, lc_end_with(("done", "done")), reboot=True)
    assert tones(tmp_path) == expected("success"), out.stdout + out.stderr


@needs_jq
@pytest.mark.parametrize("value,want,refused", [
    (False, "false", False),           # jq's `//` reads false as missing: false must still be false
    (True, "true", False),
    ("false", "true", True),           # a string is not the switch
    (0, "true", True),
])
def test_only_a_json_true_or_false_is_taken(tmp_path, value, want, refused):
    usb = tmp_path / "usbdata"; usb.mkdir()
    (usb / "settings.json").write_text(json.dumps({"cloner_beep": value}), encoding="utf-8")
    out, _usb = stick(tmp_path, 'usb_data_state_read; usb_settings_load 2>/dev/null; echo "BEEP=$USB_BEEP"; '
                                'echo "NOTICE=$USB_NOTICE"; usb_answer; jq -c .ui "$RUN_DIR/response.json"')
    assert f"BEEP={want}" in out.stdout, out.stdout + out.stderr
    assert ("cloner_beep=" in out.stdout.split("NOTICE=")[1].splitlines()[0]) == refused, out.stdout
    assert '{"cloner_beep":%s}' % want in out.stdout


@needs_jq
def test_the_reset_turns_the_beep_back_on(tmp_path):
    stick(tmp_path, "usbset_open; " + "".join(f"usbset_key {k!r}; " for k in TO_BEEP + FLIP))
    out, usb = stick(tmp_path, "usbset_open; " + "".join(f"usbset_key {k!r}; " for k in TO_RESET))
    assert json.loads((usb / "settings.json").read_text(encoding="utf-8"))["cloner_beep"] is True, out.stdout
    answer = json.loads((tmp_path / "run" / "response.json").read_text(encoding="utf-8"))
    assert answer["ui"] == {"cloner_beep": True}


@needs_jq
def test_the_beep_dialog_is_on_or_off(tmp_path):
    out, _usb = stick(tmp_path, "usbset_open; " + "".join(f"usbset_key {k!r}; " for k in TO_BEEP)
                      + 'cat "$GUI_DIR/usbset.frame"')
    rec = [line.split("\t") for line in out.stdout.splitlines()]
    assert [r[3] for r in rec if r[0] == "dlg"] == ["צליל בסיום סבב שיכפול"], out.stdout
    assert [(r[1], r[3]) for r in rec if r[0] == "d.opt"] == [("דלוק", "1"), ("כבוי", "0")], out.stdout
    out, _usb = stick(tmp_path, "usbset_open; " + "".join(f"usbset_key {k!r}; " for k in TO_BEEP + ["CHAR 20"])
                      + 'cat "$GUI_DIR/usbset.frame"')
    rec = [line.split("\t") for line in out.stdout.splitlines()]
    assert [(r[1], r[3]) for r in rec if r[0] == "d.opt"] == [("דלוק", "0"), ("כבוי", "1")], out.stdout
