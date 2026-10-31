"""#2198: USB-only power-off seconds, persistent F4 editor and native rendering.

The clock/power primitive are fake; lc_end_count, validation and end_problems
are real. Each regression includes the new behaviour (also when checking the
unchanged failure hold), so the old production tree must fail behaviourally.
No test executes a real power-off or writes a disk device.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_end_hold_2024 import CLEAN, world
from test_lc_progress_1437_1440 import (  # noqa: F401 — driver is a fixture
    DISK, DISK2, driver, needs_cc, screens,
)
from test_usb_clone import KIOSK, needs_jq, run
from test_usb_settings_1959_1960 import (
    DRIVER, _compile, frame_now, gui, kiosk, native_gui, tokens,
)

TITLE = "כיבוי אוטומטי אחרי הצלחה"
OFF = "המחשב לא ייכבה לבד (כיבוי אוטומטי: 0 בהגדרות)."
TO_POWER = ["KEY DOWN"] * 6 + ["KEY ENTER"]
pytestmark = requires_native(("bash", BASH))


def boot(tmp_path: Path, script: str):
    usb = tmp_path / "usbdata"
    usb.mkdir(exist_ok=True)
    root = tmp_path / "sysroot"
    root.mkdir(exist_ok=True)
    pre = (KIOSK + f'USB_DATA_DIR={posix(usb)!r}; echo mounted > "$RUN_DIR/usb.data"; '
           'LOG_FILE="$RUN_DIR/agent.log"; usb_data_state_read; usb_settings_load; ')
    out = run(tmp_path, pre + script, IMAGESRV_MODE="usbclone", SYSROOT=posix(root))
    assert out.returncode == 0, out.stdout + out.stderr
    return out


def settings(tmp_path: Path, value):
    usb = tmp_path / "usbdata"
    usb.mkdir(exist_ok=True)
    (usb / "settings.json").write_text(json.dumps({"poweroff_s": value}), encoding="utf-8", newline="\n")


def finish(tmp_path: Path, *, problem=None, press=3700, mode="usbclone", then="", abort=False):
    world(tmp_path, {"sdb": CLEAN if problem is None else problem})
    (tmp_path / "run/lc/run/targets").write_text("sdb\n", newline="\n")
    # No wall-clock waiting: each sleep advances one deterministic second.
    # date is fixed so both the published deadline and the off tick are testable.
    script = r'''
tick=0
date() { echo 1000; }
finish_and_stop() { echo "OFF=$tick"; }
watchdog_beat() { echo "$tick" > "$RUN_DIR/watchdog"; }
held_beat_start() { :; }
attended_power_check() { :; }
sleep() {
    tick=$((tick + 1))
    if [ "$tick" = 1 ]; then cp "$RUN_DIR/end.poweroff" "$RUN_DIR/seen"; cat "$RUN_DIR/usb.notice" > "$RUN_DIR/seen.notice" 2>/dev/null; fi
    if [ "$tick" = "$press" ]; then
        if [ "$abort" = 1 ]; then echo again > "$LC/end.req"; else : > "$RUN_DIR/end.now"; fi
    fi
    [ "$tick" -lt 4000 ] || exit 91
}
'''
    out = boot(tmp_path, f'press={press}; abort={int(abort)}; IMAGESRV_MODE={mode}; '
               + script + then + 'lc_end_count; echo "RC=$? VALUE=${USB_POWEROFF_S:-unset}"; ')
    return out.stdout, (tmp_path / "run/seen").read_text().strip().split("|")


@needs_jq
def test_missing_key_defaults_to_twenty_and_counts_twenty(tmp_path):
    text, record = finish(tmp_path)
    assert "VALUE=20" in text and "OFF=20" in text, text
    assert record == ["1020", "localclone", "1", "1", "0"]


@needs_jq
@pytest.mark.parametrize("value", [0, 45, 3600])
def test_f4_saves_and_a_new_process_uses_the_saved_value(tmp_path, value):
    keys = TO_POWER + ["KEY BACKSPACE"] * 2 + [f"CHAR {ord(c):x}" for c in str(value)] + ["KEY ENTER"]
    out, usb = kiosk(tmp_path, keys)
    assert out.returncode == 0, out.stderr
    assert json.loads((usb / "settings.json").read_text())["poweroff_s"] == value
    assert f"{value} שניות" in out.stdout
    text, record = finish(tmp_path)
    assert f"VALUE={value}" in text and f"OFF={value or 3700}" in text, text
    assert record == [str(1000 + value) if value else "0", "localclone", "1", "1", "0"]


@needs_jq
def test_zero_holds_past_the_maximum_and_keeps_the_operator_request(tmp_path):
    settings(tmp_path, 0)
    text, record = finish(tmp_path, abort=True)
    assert "OFF=" not in text and "RC=2" in text, text
    assert record == ["0", "localclone", "1", "1", "0"]
    assert (tmp_path / "run/watchdog").read_text().strip() == "3699"
    assert (tmp_path / "run/lc/end.req").read_text().strip() == "again"


@needs_jq
@pytest.mark.parametrize("value", ["3601", "-1", "abc"])
def test_invalid_submission_shows_error_and_keeps_previous_value(tmp_path, value):
    settings(tmp_path, 45)
    # Inject the submitted buffer to cover validation even for characters the
    # numeric key filter would reject before submission.
    script = ('usbset_open; ' + ''.join(f'usbset_key {k!r}; ' for k in TO_POWER)
              + f'_us_put buf {value!r}; usbset_key "KEY ENTER"; cat "$GUI_DIR/usbset.frame"')
    out = boot(tmp_path, script)
    assert f"dlg\tplain\t640\t{TITLE}" in out.stdout, out.stdout
    assert "d.error\t" in out.stdout and "לא נשמר" in out.stdout
    assert f"d.field\tשניות\t{value}\ttext\t1\t1" in out.stdout
    assert json.loads((tmp_path / "usbdata/settings.json").read_text())["poweroff_s"] == 45


@needs_jq
@pytest.mark.parametrize("value", [3601, -1, "abc", "0", None, False, 1.5, {}, []])
def test_corrupt_file_value_is_logged_and_defaults_to_twenty(tmp_path, value):
    settings(tmp_path, value)
    text, record = finish(tmp_path)
    assert "VALUE=20" in text and "OFF=20" in text, text
    assert record[0] == "1020"
    assert "poweroff_s=" in (tmp_path / "run/agent.log").read_text(encoding="utf-8")


@needs_jq
def test_completion_reloads_settings_written_by_the_kiosk_process(tmp_path):
    settings(tmp_path, 20)
    text, record = finish(tmp_path, then='printf \'{"poweroff_s":5}\' > "$USB_DATA_DIR/settings.json"; ')
    assert "VALUE=5" in text and "OFF=5" in text, text
    assert record[0] == "1005"


@needs_jq
def test_a_failed_setting_read_is_visible_and_never_starts_a_count(tmp_path):
    settings(tmp_path, 5)
    # Fail the new field's read, after the file's initial JSON check succeeds.
    broken = ('jq() { case "$*" in *poweroff_s*) return 74 ;; '
              '*) command jq "$@" ;; esac; }; ')
    text, record = finish(tmp_path, then=broken)
    assert "OFF=3700" in text and record[0] == "0", text
    assert "poweroff_s could not be read" in (tmp_path / "run/agent.log").read_text(encoding="utf-8")
    # said on the screen while it waits (lcgui.sh: usb.notice is the toast), not "0 in the settings" alone
    shown = (tmp_path / "run/seen.notice").read_text(encoding="utf-8")
    assert "הגדרות הדיסק-און-קי לא נקראו — המחשב לא ייכבה לבד" in shown, shown
    assert not (tmp_path / "run/usb.notice").exists()   # there was no notice before: none after


@needs_jq
def test_a_failed_read_gives_the_earlier_notice_back_after_the_count(tmp_path):
    settings(tmp_path, 5)
    broken = ('echo "earlier notice" > "$RUN_DIR/usb.notice"; '
              'jq() { case "$*" in *poweroff_s*) return 74 ;; *) command jq "$@" ;; esac; }; ')
    text, _record = finish(tmp_path, then=broken, abort=True)
    assert "RC=2" in text, text
    assert "לא נקראו" in (tmp_path / "run/seen.notice").read_text(encoding="utf-8")
    assert (tmp_path / "run/usb.notice").read_text(encoding="utf-8") == "earlier notice\n"


@needs_jq
def test_a_clean_read_says_nothing_new_on_the_screen(tmp_path):
    settings(tmp_path, 5)
    text, _record = finish(tmp_path)
    assert "OFF=5" in text, text
    assert not (tmp_path / "run/seen.notice").read_text(encoding="utf-8")


@needs_jq
@pytest.mark.parametrize("problem", [
    {"state": "failed", "error": "write failed"},
    {**CLEAN, "error": "orange disk"},
    {"state": "done"},  # no readback evidence
])
def test_five_seconds_applies_only_when_every_target_is_clean(tmp_path, problem):
    # Paired runs: same saved 5, only the disk result changes. This proves the
    # setting works AND cannot override #2024; the old fixed 20 fails the first.
    for name, result, expected in [("clean", None, 5), ("problem", problem, 3700)]:
        d = tmp_path / name
        d.mkdir()
        settings(d, 5)
        text, record = finish(d, problem=result)
        assert f"OFF={expected}" in text, text
        assert record[0] == ("1005" if result is None else "0")
        assert record[4] == ("0" if result is None else "1")


@needs_jq
def test_setting_does_not_change_a_server_connected_local_clone(tmp_path):
    for mode, expected in [("usbclone", 5), ("cloner", 20)]:
        d = tmp_path / mode
        d.mkdir()
        settings(d, 5)
        text, _record = finish(d, mode=mode)
        assert f"OFF={expected}" in text, text


@needs_jq
def test_reset_restores_twenty_on_the_stick_and_after_reload(tmp_path):
    settings(tmp_path, 0)
    out, usb = kiosk(tmp_path, ["KEY DOWN"] * 7 + ["KEY ENTER", "KEY TAB", "KEY ENTER"])
    assert out.returncode == 0, out.stderr
    assert json.loads((usb / "settings.json").read_text())["poweroff_s"] == 20
    text, _record = finish(tmp_path)
    assert "VALUE=20" in text and "OFF=20" in text, text


@needs_cc
def test_zero_success_dialog_says_no_auto_off_and_keeps_button(driver, tmp_path):
    lc = ("lc_screen=local\nlc_usb=1\nlc_inv=done\nlc_phase=done\n" + DISK + DISK2 +
          "lc_run=sdc|100|100|9|0|sent|2||\nlc_tgt=sdb|done|100|2|2||||100\n")
    zero, problem = screens(driver, tmp_path,
                            lc + "end_poweroff=0|localclone|1|1|0\n",
                            lc + "end_poweroff=0|localclone|1|1|1\nend_problem=sdb|o|orange\n")
    assert OFF in zero and "כבה עכשיו" in zero, zero
    assert not any(s.startswith("המחשב ייכבה בעוד") for s in zero)
    assert OFF not in problem and any("יש דיסק עם בעיה" in s for s in problem), problem


@native_gui
@pytest.mark.parametrize("width", [1024, 1280])
def test_all_eight_rows_are_drawn_and_clickable_inside_768_pixels(tmp_path, width):
    # Reuse the existing DCUI pixel and hit driver at the owner's two sizes.
    source = DRIVER.replace("enum { W = 1280, H = 800 };", f"enum {{ W = {width}, H = 768 }};")
    assert source != DRIVER
    (tmp_path / "driver.c").write_text(source, encoding="utf-8", newline="\n")
    binary = _compile(tmp_path, "rows", "-DWITH_HITS")
    frame, gui_dir = frame_now(tmp_path, [])
    assert len([s for s in frame.splitlines() if s.startswith("item\t")]) == 8
    lines = gui(binary, gui_dir, "items", "hits")
    pixels = [s.split() for s in lines if s.startswith("item ")]
    hits = [s.split(" ", 5) for s in lines if s.startswith("hit ") and " item:" in s]
    assert len(pixels) == len(hits) == 8, lines
    for index, (pixel, hit) in enumerate(zip(pixels, hits)):
        _, x, y, w, h, _name = hit
        x, y, w, h = map(float, (x, y, w, h))
        assert 0 <= x < x + w <= width and 60 <= y < y + h <= 728, hit
        assert abs(int(pixel[3]) - int(pixel[2]) - h) <= 3, (pixel, hit)
        sent = tokens(gui(binary, gui_dir, f"click:{x + w / 2},{y + h / 2}"))
        assert sent == ["KEY DOWN"] * index + ["KEY ENTER"], sent
    pane, _ = frame_now(tmp_path, ["KEY DOWN"] * 6)
    assert "<b>אחרי כשל, או כשיש דיסק כתום, המחשב לא נכבה לבד</b> — כמו היום." in pane
