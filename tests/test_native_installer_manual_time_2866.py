"""#2866 (mockup 2866-manual-time, Nadav 11/11): the manual time as five boxes with ▲▼.

Nadav, after the install at work: typing the date and time "was not comfortable" -- one empty field in
a fixed format. Now, on "ידני — בלי שרת זמן":

* five boxes -- year, month, day, hour, minute -- filled in advance from the hardware clock (in the
  zone chosen above); usually a few minutes are corrected and that is all;
* ▲▼ (a click) and the arrow keys step a box: 59 -> 00, the month's last day -> 1, no carry; Tab moves
  between the boxes; every value stays in its range (a day in its month, 29/2 only in a leap year);
* digits typed -- also from the keyboard on the screen (#2776/#2778) -- go into the box in focus;
* what is sent is ``manual_time`` in the format the engine already takes (``clock_manual``) -- the
  engine is unchanged.

The C is built and run on Linux (the testrunner); the source contracts run everywhere.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from test_native_installer_backup_1956 import NATIVE, SRC
from test_native_installer_gui import GUI, _build, _size_and_ink

MOCKUP_STRINGS = (   # docs/design/mockups-1112/src/2866-manual-time.html, word for word -- but ↑ ↓ for its ▲ ▼: the font has none
    "תאריך ושעה (לפי אזור הזמן שלמעלה)", '"שנה","חודש","יום","שעה","דקה"',
    "מתמלא מראש בשעון החומרה (%s). לחצו ↑ ↓ או חיצי המקלדת כדי לתקן, בלי להקליד פורמט. "
    "אימות דו-שלבי ותעודת TLS נשענים על השעה הזו, ושעון ידני מתרחק עם הזמן.",
    "Tab / Shift-Tab  מעבר    ↑ ↓  שינוי ערך    Enter  הבא    Esc  חזור",
)


def _c(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_every_new_string_is_the_mockups():
    text = _c("screens_install_forms.c") + _c("screens_install.c")
    missing = [s for s in MOCKUP_STRINGS if s not in text]
    assert not missing, missing


def test_the_text_field_is_gone_and_the_boxes_are_the_steps_fields():
    forms = _c("screens_install_forms.c")
    assert "HIT_INSTALL_MANUAL_TIME" not in forms, "no typed format any more"
    control = _c("install.c")
    assert "if(s->ntp_manual)for(int i=0;i<MT_BOXES;i++)ids[n++]=HIT_INSTALL_MT_BASE+i;}" in control   # fields(): the OSK
    assert control.count("for(int i=0;i<MT_BOXES;i++)ids[n++]=HIT_INSTALL_MT_BASE+i;") == 2            # and the ring
    head = _c("install.h")
    for line in ("#define HIT_INSTALL_MT_BASE        1160", "#define HIT_INSTALL_MT_UP_BASE     1165",
                 "#define HIT_INSTALL_MT_DOWN_BASE   1170", "#define HIT_INSTALL_OSK_BASE       1200"):
        assert line in head


def test_the_arrows_are_drawn_not_glyphs_and_the_boxes_use_the_field_token():
    forms = _c("screens_install_forms.c")
    body = forms[forms.index("static void mt_arrow("):forms.index("/* #2144 (mockup 2144-time")]
    assert "draw_field_box(cr,t,f,focus)" in body and "cairo_line_to" in body
    assert not re.search(r"#[0-9a-fA-F]{6}", body), "colours only from the theme"


DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }
static App app;
static void key(App *a, InstallBridge *b, int k) { Event e = {0}; e.type = UIEV_KEY; e.key = k; install_handle(a, b, &e); }
static void type(App *a, InstallBridge *b, const char *s) { for (; *s; s++) { Event c = {0}; c.type = UIEV_CHAR; c.ch = *s; install_handle(a, b, &c); } }
static void click(App *a, InstallBridge *b, int id) { choose(a, b, id); }
static void osk(App *a, InstallBridge *b, char ch) {   /* a key of the keyboard on the screen, by its character */
    for (int k = 0; k < OSK_CHARS; k++) if (osk_char(k, 0) == ch) { a->install.osk = a->install.view; osk_press(a, b, HIT_INSTALL_OSK_BASE + k); return; }
}
static void show(App *a, const char *tag) { int v[5], got = install_mt_values(&a->install, v);
    printf("%%s=%%04d-%%02d-%%02d %%02d:%%02d|%%d|%%s|focus%%d\n", tag, v[0], v[1], v[2], v[3], v[4], got, a->install.manual_time, install_mt_box(a->focus)); }

/* argv: BRIDGE ZONE "YYYY-MM-DD HH:MM:SS" ACTION... -- the step with "manual" chosen and the time set as if
 * seeded; each action prints the boxes. Actions: up:I down:I (a click on ▲/▼ of box I), key:UP key:DOWN key:TAB,
 * type:DIGITS, osk:DIGITS, bksp, focus:I, next, seed (pick "manual" on a fresh step) */
int main(int argc, char **argv) {
    if (argc < 4) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    if (install_start(a, &b, 0)) { printf("start-failed\n"); return 3; }
    snprintf(a->install.timezone, sizeof a->install.timezone, "%%s", argv[2]);
    a->install.view = INSTALL_TIME; refocus(a);
    if (strcmp(argv[3], "-")) { a->install.ntp_manual = 1; snprintf(a->install.manual_time, sizeof a->install.manual_time, "%%s", argv[3]); a->focus = HIT_INSTALL_MT_BASE; }
    for (int i = 4; i < argc; i++) {
        const char *t = argv[i];
        if (!strncmp(t, "up:", 3)) click(a, &b, HIT_INSTALL_MT_UP_BASE + atoi(t + 3));
        else if (!strncmp(t, "down:", 5)) click(a, &b, HIT_INSTALL_MT_DOWN_BASE + atoi(t + 5));
        else if (!strcmp(t, "key:UP")) key(a, &b, KEYSYM_UP);
        else if (!strcmp(t, "key:DOWN")) key(a, &b, KEYSYM_DOWN);
        else if (!strcmp(t, "key:TAB")) key(a, &b, KEYSYM_TAB);
        else if (!strncmp(t, "type:", 5)) type(a, &b, t + 5);
        else if (!strncmp(t, "osk:", 4)) { for (const char *p = t + 4; *p; p++) osk(a, &b, *p); }
        else if (!strcmp(t, "bksp")) key(a, &b, KEYSYM_BACKSPACE);
        else if (!strncmp(t, "focus:", 6)) click(a, &b, HIT_INSTALL_MT_BASE + atoi(t + 6));
        else if (!strcmp(t, "seed")) { for (int k = 0; k < 3; k++) key(a, &b, KEYSYM_DOWN); }   /* org, its field, "ידני" */
        else if (!strcmp(t, "next")) { key(a, &b, KEYSYM_ENTER); printf("moved_on=%%d\n", a->install.view != INSTALL_TIME); continue; }
        show(a, t);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-manual-time")
    source = tmp / "install_manual_time.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_manual_time"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _run(driver: Path, tmp_path: Path, start: str, *actions: str, zone: str = "Asia/Jerusalem") -> tuple[list[dict], dict | None]:
    bridge_cmd = (f'f(){{ cat > "{tmp_path}/$1.json"; case "$1" in '
                  "inventory) printf 'disk=/dev/sda|model=X|size=1|bus=sata|removable=0|has=empty|iso=0\\n' ;; "
                  "progress) printf 'state=ready\\n' ;; esac; }; f")
    proc = subprocess.run([str(driver), bridge_cmd, zone, start, *actions], capture_output=True, text=True,
                          timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = []
    for line in proc.stdout.splitlines():
        tag, _, rest = line.partition("=")
        if tag == "moved_on":
            rows.append({"tag": tag, "moved_on": rest})
            continue
        shown, got, text, focus = rest.split("|")
        rows.append({"tag": tag, "shown": shown, "from_text": got, "text": text, "box": int(focus[5:])})
    sent = tmp_path / "validate.json"
    return rows, json.loads(sent.read_text()) if sent.exists() else None


@NATIVE
def test_the_boxes_are_filled_from_the_hardware_clock_in_the_zone(driver, tmp_path):
    """Choosing "ידני" seeds the five boxes with the clock, in the zone chosen above: the same minute as `date`."""
    rows, _ = _run(driver, tmp_path, "-", "seed")
    seeded = rows[-1]
    want = subprocess.run(["date", "+%Y-%m-%d %H:%M"], capture_output=True, text=True,
                          env={**os.environ, "TZ": "Asia/Jerusalem"}, stdin=subprocess.DEVNULL).stdout.strip()
    assert seeded["from_text"] == "1" and seeded["text"].startswith(seeded["shown"])
    assert seeded["shown"] == want or seeded["shown"][:-1] == want[:-1], (seeded, want)   # a minute may turn
    assert seeded["box"] == -1, "landing on the radio does not take the boxes"


@NATIVE
@pytest.mark.parametrize("start, actions, shown", [
    ("2026-11-12 21:59:30", ("up:4",), "2026-11-12 21:00"),          # 59 -> 00, and the hour stays: no carry
    ("2026-11-12 21:00:00", ("down:4",), "2026-11-12 21:59"),         # and back
    ("2026-11-12 23:47:00", ("up:3",), "2026-11-12 00:47"),           # 23 -> 0
    ("2026-01-31 10:00:00", ("up:2",), "2026-01-01 10:00"),           # the month's last day -> 1
    ("2026-04-30 10:00:00", ("up:2",), "2026-04-01 10:00"),           # a 30-day month
    ("2026-04-01 10:00:00", ("down:2",), "2026-04-30 10:00"),
    ("2024-02-28 10:00:00", ("up:2",), "2024-02-29 10:00"),           # a leap year has the 29th
    ("2026-02-28 10:00:00", ("up:2",), "2026-02-01 10:00"),           # another does not
    ("2026-12-05 10:00:00", ("up:1",), "2026-01-05 10:00"),           # 12 -> 1, no carry into the year
    ("2026-01-31 10:00:00", ("up:1",), "2026-02-28 10:00"),           # 31/1 -> February: its last day
    ("2024-03-31 10:00:00", ("down:1",), "2024-02-29 10:00"),
    ("2024-02-29 10:00:00", ("up:0",), "2025-02-28 10:00"),           # 29/2 into a year without it
    ("2099-06-01 10:00:00", ("up:0",), "2000-06-01 10:00"),           # the engine's years, round
])
def test_a_step_stays_in_range_and_rounds_within_the_box(driver, tmp_path, start, actions, shown):
    rows, _ = _run(driver, tmp_path, start, *actions)
    assert rows[-1]["shown"] == shown, rows
    assert rows[-1]["text"] == shown + ":00", "written in the engine's format, seconds from now on"


@NATIVE
def test_the_keyboard_alone_up_down_and_tab(driver, tmp_path):
    rows, sent = _run(driver, tmp_path, "2026-11-12 21:47:12", "key:UP", "key:TAB", "key:DOWN", "key:TAB", "key:TAB",
                      "key:UP", "key:UP", "key:TAB", "key:DOWN", "next")
    assert [r["box"] for r in rows[:-1]] == [0, 1, 1, 2, 3, 3, 3, 4, 4], rows
    assert rows[-2]["shown"] == "2027-10-12 23:46"
    assert rows[-1]["moved_on"] == "1"
    assert sent["clock_manual"].startswith("2027-10-12 23:46:"), sent


@NATIVE
@pytest.mark.parametrize("box, digits, shown", [
    (4, "05", "2026-11-12 21:05"), (3, "9", "2026-11-12 09:47"), (2, "31", "2026-11-01 21:47"),   # 31/11 is not a day: "1" starts again
    (2, "3", "2026-11-03 21:47"), (1, "02", "2026-02-12 21:47"), (0, "2030", "2030-11-12 21:47"),
    (0, "1999", "2026-11-12 21:47"), (4, "75", "2026-11-12 21:05"),                                # 07, then 75 is no minute: 05
])
def test_digits_typed_into_a_box(driver, tmp_path, box, digits, shown):
    rows, _ = _run(driver, tmp_path, "2026-11-12 21:47:00", f"focus:{box}", f"type:{digits}")
    assert rows[-1]["shown"] == shown, rows


@NATIVE
def test_the_keyboard_on_the_screen_types_into_the_boxes(driver, tmp_path):
    """#2776/#2778: the mouse alone -- a box, then the digits of the keyboard on the screen."""
    rows, sent = _run(driver, tmp_path, "2026-11-12 21:47:00", "focus:4", "osk:15", "focus:3", "osk:08", "next")
    assert rows[-2]["shown"] == "2026-11-12 08:15", rows
    assert sent["clock_manual"].startswith("2026-11-12 08:15:")


@NATIVE
def test_a_seeded_time_is_sent_as_it_is(driver, tmp_path):
    rows, sent = _run(driver, tmp_path, "2026-11-12 21:47:12", "focus:0", "next")
    assert sent["clock_manual"].startswith("2026-11-12 21:47:"), "an untouched seed keeps its seconds and moves on"


@NATIVE
def test_a_zone_not_known_here_takes_nothing_until_a_box_changes(driver, tmp_path):
    rows, sent = _run(driver, tmp_path, "-", "seed", "next", zone="Nowhere/Zone")
    assert rows[0]["from_text"] == "0" and rows[0]["text"] == "", rows
    assert rows[-1]["moved_on"] == "0" and sent is None
    rows, sent = _run(driver, tmp_path, "-", "seed", "key:DOWN", "up:4", "next", zone="Nowhere/Zone")
    assert rows[-2]["from_text"] == "1" and rows[-1]["moved_on"] == "1" and sent["clock_manual"]


@NATIVE
def test_the_step_renders_in_both_themes(tmp_path):
    binary = _build(tmp_path)
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    prefix = tmp_path / "mt"
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png", str(prefix), "--size", "1280x800"],
                          capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    for theme in ("light", "dark"):
        width, height, colors = _size_and_ink(tmp_path / f"mt-install-3f-time-manual-{theme}.png")
        assert (width, height) == (1280, 800) and colors > 20, theme
        # the footer under the boxes stays clear: the card "השעה עכשיו בשרת" ends above it
        _, _, footer = _size_and_ink(tmp_path / f"mt-install-3f-time-manual-{theme}.png", (40, 737, 600, 739))
        assert footer <= 2, f"{theme}: something is drawn over the footer's edge"
