"""#1959 / #1960 -- the USB stick's settings screen: the mouse, and "החזר לברירת המחדל".

* #1959 (Nadav 22/10, on the stick at 802b82f8): a click on an item of the settings
  screen did nothing; the keys worked. The screen is the DCUI's (screens_dcui.c), which
  only ever read keys: app_draw handed it the frame and cleared the App's hit table, and
  lc_handle sent dcui_token -- 0 for a click. Now dcui_draw records what a click can
  press, and a click is sent as the keys a person would have pressed for the same thing
  (dcui_click) -- the backend (usbset.sh) cannot tell them apart, so a click can never do
  what a key cannot.

  The test runs the whole way: usbset.sh writes the frame, the real lc_handle gets a
  click at the item's pixels (found in the drawn picture, not from the new table -- so
  the test means the same on the code before the fix), and the tokens it sends go back
  into usbset_key. The item's dialog must be open.

* #1960: a fifth item, "החזר לברירת המחדל", behind the server's own confirmation (F12:
  the focus on "ביטול", the red button, no typed word). It writes usbclone.sh's
  USB_DEF_* -- the values in force when there is no file -- into the settings file, and
  reads the file back (principle 5): a write that did not land is said, never shown
  as done.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix
from test_usb_clone import KIOSK, LIB, needs_jq, run

REPO = Path(__file__).resolve().parent.parent
DEFAULTS = {"min_free_gb": 20, "readback_mode": "sample", "crc_warn_threshold": 500, "temp_warn_c": 60,
            "cloner_beep": True}   # #2131
FSI, PDI = "⁨", "⁩"   # view.iso: a Latin run in a Hebrew line, "20 GB" and never "GB 20"
ITEMS = ["מקום פנוי מינימלי אחרי שחזור", "אימות מלא אחרי הכתיבה", "אזהרת CRC למגירה", "סף חום", "מיפוי חיבורים",
         "צליל בסיום סבב שיכפול", "החזר לברירת המחדל"]   # #1962: the mapping, #2131: the beep, before the reset

pytestmark = requires_native(("bash", BASH))
native_gui = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("jq", shutil.which("jq")),
    why="native-gui נבנה על המעבדה בלבד",
)


def kiosk(tmp_path: Path, keys: list[str], data: str = "mounted", pre: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    """usbset_open, the keys, the frame. The same stick (usbdata) across calls in one test."""
    usb = tmp_path / "usbdata"; usb.mkdir(exist_ok=True)
    (tmp_path / "sysroot").mkdir(exist_ok=True)   # no disks, no model: never this host's /sys
    script = (KIOSK + f'echo {data} > "$RUN_DIR/usb.data"; USB_DATA_DIR={posix(usb)!r}; {pre} usbset_open; '
              + "".join(f'usbset_key {k!r}; ' for k in keys) + 'cat "$GUI_DIR/usbset.frame"')
    return run(tmp_path, script, IMAGECTL_MODE="usbclone", SYSROOT=posix(tmp_path / "sysroot")), usb


def dialog_title(frame: str) -> str | None:
    for line in frame.splitlines():
        rec = line.split("\t")
        if rec[0] == "dlg":
            return rec[3]
    return None


CUSTOM = ["KEY ENTER", "KEY BACKSPACE", "KEY BACKSPACE", "CHAR 33", "CHAR 30", "KEY ENTER",   # 30 GB
          "KEY DOWN", "KEY ENTER", "KEY RIGHT", "KEY ENTER",                                   # full
          "KEY DOWN", "KEY ENTER", "CHAR 31", "KEY ENTER",                                     # CRC 5001
          "KEY DOWN", "KEY ENTER", "KEY BACKSPACE", "KEY BACKSPACE", "CHAR 37", "CHAR 30", "KEY ENTER"]   # 70 C
TO_RESET = ["KEY DOWN"] * 6 + ["KEY ENTER"]


# --- #1960: the reset, in usbset.sh -------------------------------------------------------------

@needs_jq
def test_reset_writes_the_defaults_reads_them_back_and_they_are_in_force(tmp_path):
    out, usb = kiosk(tmp_path, CUSTOM)
    assert json.loads((usb / "settings.json").read_text()) == {
        "min_free_gb": 30, "readback_mode": "full", "crc_warn_threshold": 5001, "temp_warn_c": 70}, out.stdout + out.stderr
    out, usb = kiosk(tmp_path, TO_RESET + ["KEY TAB", "KEY ENTER"])
    assert json.loads((usb / "settings.json").read_text()) == DEFAULTS, out.stdout + out.stderr
    answer = json.loads((tmp_path / "run" / "response.json").read_text(encoding="utf-8"))
    assert (answer["min_free_gb"], answer["readback_mode"], answer["crc_warn_threshold"], answer["usb"]["temp_warn_c"]) \
        == (20, "sample", 500, 60)
    assert (tmp_path / "run" / "min_free_gb").read_text().strip() == "20"
    assert dialog_title(out.stdout) is None, out.stdout          # done: the dialog closed
    assert "prow\tמקום פנוי מינימלי\t\t20 GB\tmono\tברירת המחדל" in out.stdout, out.stdout


@needs_jq
@pytest.mark.parametrize("keys", [["KEY ENTER"], ["KEY ESC"], ["KEY TAB", "KEY ESC"], ["KEY TAB", "KEY TAB", "KEY ENTER"]])
def test_reset_happens_only_on_the_red_button(tmp_path, keys):
    kiosk(tmp_path, CUSTOM)
    out, usb = kiosk(tmp_path, TO_RESET + keys)
    assert json.loads((usb / "settings.json").read_text())["min_free_gb"] == 30, out.stdout + out.stderr
    assert dialog_title(out.stdout) is None, out.stdout


@needs_jq
def test_the_confirmation_is_the_servers_red_button_with_the_focus_on_cancel(tmp_path):
    kiosk(tmp_path, CUSTOM)
    out, _usb = kiosk(tmp_path, TO_RESET)
    assert dialog_title(out.stdout) == "להחזיר את ברירות המחדל?", out.stdout
    assert "dlg\tdanger\t640\t" in out.stdout
    assert "d.btn\tביטול\tEsc\tplain\t1" in out.stdout and "d.btn\tהחזר לברירת המחדל\t\tdanger\t0" in out.stdout
    assert f"d.kv\tמקום פנוי מינימלי\t{FSI}20 GB{PDI}\t0" in out.stdout and f"d.kv\tאזהרת CRC\t{FSI}500{PDI}\t0" in out.stdout
    assert "d.field" not in out.stdout                                 # no typed word (#1270)
    out, _usb = kiosk(tmp_path, TO_RESET + ["KEY TAB"])
    assert "d.btn\tהחזר לברירת המחדל\t\tdanger\t1" in out.stdout, out.stdout


@needs_jq
def test_a_reset_whose_file_does_not_read_back_is_said_and_not_shown_as_done(tmp_path):
    kiosk(tmp_path, CUSTOM)
    # an mv into settings.json that "succeeds" and moves nothing: the file still says 30 --
    # the read-back must see it
    out, usb = kiosk(tmp_path, TO_RESET + ["KEY TAB", "KEY ENTER"], pre='mv() { case "$2" in *settings.json) return 0 ;; esac; command mv "$@"; };')
    assert json.loads((usb / "settings.json").read_text())["min_free_gb"] == 30
    assert "d.error\tלא הוחזר: הכתיבה לקובץ ההגדרות או הקריאה החוזרת שלו נכשלה" in out.stdout, out.stdout + out.stderr
    assert dialog_title(out.stdout) == "להחזיר את ברירות המחדל?"


@needs_jq
def test_without_a_data_partition_the_reset_is_for_this_session(tmp_path):
    out, usb = kiosk(tmp_path, TO_RESET + ["KEY TAB", "KEY ENTER"], data="none")
    assert json.loads((tmp_path / "run" / "usb-settings.json").read_text()) == DEFAULTS, out.stdout + out.stderr
    assert not (usb / "settings.json").exists()


def test_the_reset_writes_the_values_in_force_with_no_file():
    """No second copy of the numbers: usbset_reset writes USB_DEF_*, the ones usb_settings_load starts from."""
    text = (LIB / "usbset.sh").read_text(encoding="utf-8")
    body = text[text.index("usbset_reset() {"):text.index("\n}\n", text.index("usbset_reset() {"))]
    for name in ("USB_DEF_MIN_FREE_GB", "USB_DEF_READBACK", "USB_DEF_CRC_WARN", "USB_DEF_TEMP_WARN_C"):
        assert f'"${name}"' in body, name
    assert not re.search(r"\b(20|500|60|sample)\b", body.replace("USB_DEF_", "")), "a number copied into the reset"


def test_the_sticks_defaults_are_the_servers_where_the_server_has_one(monkeypatch):
    """min_free_gb, the read-back and the temperature are the server's own defaults. CRC is not:
    the server's crc_warn_threshold is 0 (off, Nadav 13/10), the stick's 500 (Nadav 20/10) -- a
    decision of its own, kept and named here, not a twin."""
    import sys
    sys.path.insert(0, str(REPO))
    from server import minfree, readbackhash, smart_history
    text = (LIB / "usbclone.sh").read_text(encoding="utf-8")
    got = dict(re.findall(r"^USB_DEF_(\w+)=(\S+)", text, re.M))
    assert int(got["MIN_FREE_GB"]) == minfree.MIN_FREE_GB_DEFAULT
    assert int(got["TEMP_WARN_C"]) == smart_history.TEMP_WARN_C
    monkeypatch.setattr(readbackhash, "get_setting", lambda conn, key: None)   # a server with no setting
    assert got["READBACK"] == readbackhash.mode(None)
    assert int(got["CRC_WARN"]) == 500


# --- #1959: the mouse, through the real lc_handle ----------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "dcui.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
enum { W = 1280, H = 800 };
static cairo_surface_t *sf;
static void draw(App *a) {
    if (sf) cairo_surface_destroy(sf);
    sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    cairo_t *cr = cairo_create(sf); app_draw(a, cr, W, H); cairo_destroy(cr); cairo_surface_flush(sf);
}
static unsigned px(int x, int y) {
    return ((unsigned *)(cairo_image_surface_get_data(sf) + y * cairo_image_surface_get_stride(sf)))[x] & 0xffffff;
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    snprintf(a.tools_dir, sizeof a.tools_dir, "%s", argv[1]);
    char st[] = "lc_screen=usbset\nlc_usb=1\nlc_inv=done\nlc_phase=idle\n";
    FILE *f = fmemopen(st, strlen(st), "r"); state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    app_route(&a); draw(&a);
    for (int i = 2; i < argc; i++) {
        const char *v = argv[i];
        if (!strcmp(v, "items")) {
            /* the settings items as drawn: a column through the list (at the right, RTL), the
             * runs that are not the page's background and are tall enough to be an item */
            int x = W - 32 - 100, y0 = -1; unsigned bg = px(4, H / 2);
            for (int y = 60; y < H - 40; y++) {
                int in = px(x, y) != bg;
                if (in && y0 < 0) y0 = y;
                if (!in && y0 >= 0) { if (y - y0 >= 30) printf("item %d %d %d\n", x, y0, y); y0 = -1; }
            }
        } else if (!strcmp(v, "hits")) {
#ifdef WITH_HITS
            const DcuiHit *h; int n = dcui_hits(&h);
            for (int k = 0; k < n; k++) printf("hit %.0f %.0f %.0f %.0f %s\n", h[k].x, h[k].y, h[k].w, h[k].h, h[k].what);
#endif
        } else if (!strncmp(v, "click:", 6)) {
            Event e; memset(&e, 0, sizeof e); e.type = UIEV_CLICK; sscanf(v + 6, "%lf,%lf", &e.x, &e.y);
            app_route(&a); lc_handle(&a, &e); draw(&a);
            printf("@ clicked\n");
        } else if (!strncmp(v, "key:", 4)) {
            Event e; memset(&e, 0, sizeof e); e.type = UIEV_KEY;
            e.key = !strcmp(v + 4, "DOWN") ? KEYSYM_DOWN : !strcmp(v + 4, "UP") ? KEYSYM_UP : KEYSYM_ENTER;
            app_route(&a); lc_handle(&a, &e); draw(&a);
        }
        fflush(stdout);
    }
    return 0;
}
'''


def _compile(d: Path, name: str, *defines: str) -> Path:
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    assert srcs, "make print-gui-src gave no sources"
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE ' + " ".join(defines) +
                            ' -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / name), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / name


class _Drivers:
    """Built when first asked for. "plain" reads nothing of the fix (the items are found in the
    picture), so it builds on the code before it too -- the negative control of the item tests
    is behavioural; "hits" reads the new table, for the dialog's buttons and the status keys."""

    def __init__(self, d: Path) -> None:
        self.d, self.built = d, {}
        (d / "driver.c").write_text(DRIVER, newline="\n")

    def __getitem__(self, name: str) -> Path:
        if name not in self.built:
            self.built[name] = _compile(self.d, name, *(["-DWITH_HITS"] if name == "hits" else []))
        return self.built[name]


@pytest.fixture(scope="module")
def drivers(tmp_path_factory) -> _Drivers:
    return _Drivers(tmp_path_factory.mktemp("usbsetdrv"))


def gui(driver: Path, gui_dir: Path, *cmds: str) -> list[str]:
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    out = subprocess.run([str(driver), posix(gui_dir), *cmds], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    return [line for line in out.stdout.splitlines() if line]


def tokens(lines: list[str]) -> list[str]:
    return [line[len("lc-usbset|"):] for line in lines if line.startswith("lc-usbset|")]


def frame_now(tmp_path: Path, keys: list[str]) -> tuple[str, Path]:
    out, _usb = kiosk(tmp_path, keys)
    assert out.returncode == 0, out.stderr
    return out.stdout, tmp_path / "run" / "gui"


def items_drawn(driver: Path, gui_dir: Path) -> list[tuple[int, int]]:
    """The centre of each item, from the picture."""
    found = [line.split() for line in gui(driver, gui_dir, "items") if line.startswith("item ")]
    return [(int(x), (int(y0) + int(y1)) // 2) for _, x, y0, y1 in found]


def hit(driver: Path, gui_dir: Path, what: str) -> tuple[int, int]:
    for line in gui(driver, gui_dir, "hits"):
        _, x, y, w, h, name = line.split(" ", 5)
        if name == what:
            return int(x) + int(w) // 2, int(y) + int(h) // 2
    raise AssertionError(f"nothing to press for {what!r}")


@native_gui
@pytest.mark.parametrize("index", range(len(ITEMS)))
def test_a_click_on_an_item_opens_it_as_its_keys_do(drivers, tmp_path, index):
    _frame, gui_dir = frame_now(tmp_path, [])
    centres = items_drawn(drivers["plain"], gui_dir)
    assert len(centres) == len(ITEMS), centres                     # the picture has every item
    sent = tokens(gui(drivers["plain"], gui_dir, "click:%d,%d" % centres[index]))
    assert sent, f"a click on {ITEMS[index]!r} sent nothing"
    frame, _ = frame_now(tmp_path, sent)
    want = "להחזיר את ברירות המחדל?" if index == len(ITEMS) - 1 else ITEMS[index]
    assert dialog_title(frame) == want, (sent, frame)
    keys = (["KEY DOWN"] * index) + ["KEY ENTER"]                   # what a person would have pressed
    assert sent == keys


@native_gui
def test_a_click_on_the_selected_item_after_a_key_moved_it_opens_that_one(drivers, tmp_path):
    _frame, gui_dir = frame_now(tmp_path, ["KEY DOWN", "KEY DOWN", "KEY DOWN"])   # the temperature is selected
    centres = items_drawn(drivers["plain"], gui_dir)
    sent = tokens(gui(drivers["plain"], gui_dir, "click:%d,%d" % centres[1]))
    assert sent == ["KEY UP", "KEY UP", "KEY ENTER"]
    frame, _ = frame_now(tmp_path, ["KEY DOWN", "KEY DOWN", "KEY DOWN"] + sent)
    assert dialog_title(frame) == ITEMS[1], frame


@native_gui
def test_a_click_off_the_items_and_behind_a_dialog_sends_nothing(drivers, tmp_path):
    _frame, gui_dir = frame_now(tmp_path, [])
    assert tokens(gui(drivers["plain"], gui_dir, "click:640,60", "click:5,400")) == []
    centres = items_drawn(drivers["plain"], gui_dir)
    _frame, gui_dir = frame_now(tmp_path, ["KEY DOWN", "KEY ENTER"])                # the read-back dialog
    assert tokens(gui(drivers["plain"], gui_dir, *("click:%d,%d" % c for c in centres))) == []
    assert not any(line.startswith("hit ") and " item:" in line for line in gui(drivers["hits"], gui_dir, "hits"))


@native_gui
@needs_jq
def test_the_reset_by_mouse_alone(drivers, tmp_path):
    """Open the item, press the red button -- no key at all."""
    kiosk(tmp_path, CUSTOM)
    _frame, gui_dir = frame_now(tmp_path, [])
    sent = tokens(gui(drivers["hits"], gui_dir, "click:%d,%d" % items_drawn(drivers["plain"], gui_dir)[len(ITEMS) - 1]))
    frame, gui_dir = frame_now(tmp_path, sent)
    assert dialog_title(frame) == "להחזיר את ברירות המחדל?", frame
    press = tokens(gui(drivers["hits"], gui_dir, "click:%d,%d" % hit(drivers["hits"], gui_dir, "btn:החזר לברירת המחדל")))
    assert press == ["KEY TAB", "KEY ENTER"]                       # Tab to it and Enter, as F12 on the server
    frame, _ = frame_now(tmp_path, sent + press)
    assert json.loads((tmp_path / "usbdata" / "settings.json").read_text()) == DEFAULTS
    assert dialog_title(frame) is None


@native_gui
@needs_jq
def test_the_dialogs_buttons_options_and_the_status_keys_are_their_keys(drivers, tmp_path):
    _frame, gui_dir = frame_now(tmp_path, ["KEY DOWN", "KEY ENTER"])              # read-back: sample / full
    assert tokens(gui(drivers["hits"], gui_dir, "click:%d,%d" % hit(drivers["hits"], gui_dir, "opt:מלא"))) == ["KEY LEFT"]
    assert tokens(gui(drivers["hits"], gui_dir, "click:%d,%d" % hit(drivers["hits"], gui_dir, "btn:שמירה"))) == ["KEY ENTER"]
    assert tokens(gui(drivers["hits"], gui_dir, "click:%d,%d" % hit(drivers["hits"], gui_dir, "btn:ביטול"))) == ["KEY ESC"]
    frame, gui_dir = frame_now(tmp_path, ["KEY DOWN", "KEY ENTER", "KEY LEFT", "KEY ENTER"])
    assert dialog_title(frame) is None
    _frame, gui_dir = frame_now(tmp_path, [])
    assert tokens(gui(drivers["hits"], gui_dir, "click:%d,%d" % hit(drivers["hits"], gui_dir, "key:Esc"))) == ["KEY ESC"]
