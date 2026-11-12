"""#2865 (mockup 2865-backup-off, Nadav 11/11): "בלי גיבוי כרגע" on the native installer's backup step.

* a fifth option after "גודל אחר", with the orange note while it is chosen; the default stays 5 GB;
* chosen: no hour field, no space -- the form sends ``backup_reserve_gb=""`` and ``backup_enabled=false``;
* the keyboard reaches it (Tab, Shift+Tab, the arrows) without choosing it on the way -- Space or a
  click chooses it; every other option is still chosen by landing on it;
* the summary row says "כבוי · אפשר להדליק בקונסולה", and its "שינוי" opens step 6;
* the screen and the summary in that state render, both themes (``--png``).

The C is built and run on Linux (the testrunner); the source contracts run everywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from test_native_installer_backup_1956 import NATIVE, SRC
from test_native_installer_gui import GUI, _build, _size_and_ink

MOCKUP_STRINGS = (   # docs/design/mockups-1112/src/2865-backup-off.html, word for word
    "בלי גיבוי כרגע", "הגיבוי לא ירוץ, ואין מקום שמור בכונן.",
    "<b>אפשר להדליק אחר כך בקונסולה ← הגדרות ← גיבוי</b>",
    'בלי גיבוי, תקלת כונן מוחקת את רשימת המחשבים וההגדרות. בדף הבית ובמסך השרת יופיע פס כתום \\"הגיבוי כבוי\\" עד שתדליקו.',
    '"כבוי"', "· אפשר להדליק בקונסולה",
)


def _c(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_every_new_string_is_the_mockups():
    text = _c("screens_install_forms.c") + _c("screens_install_final.c")
    missing = [s for s in MOCKUP_STRINGS if s not in text]
    assert not missing, missing


def test_the_option_is_its_own_hit_not_the_size_field():
    head = _c("install.h")
    assert "#define HIT_INSTALL_BACKUP_OFF     1156" in head and "#define HIT_INSTALL_BACKUP_OTHER_GB 1154" in head
    assert "#define INSTALL_BACKUP_OFF 4" in _c("install_state.h")


def test_the_note_is_the_warning_tokens():
    body = _c("screens_install_forms.c")
    step = body[body.index("void install_draw_backup("):body.index("void install_draw_forms(")]
    off = step[step.index("if(off){note("):]
    assert off.split(";", 1)[0].endswith("t->warning_bg,t->warning_line,t->warning_ink,1)")


DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }
static App app;
static void key(App *a, InstallBridge *b, int k) { Event e = {0}; e.type = UIEV_KEY; e.key = k; install_handle(a, b, &e); }
static void space(App *a, InstallBridge *b) { Event c = {0}; c.type = UIEV_CHAR; c.ch = ' '; install_handle(a, b, &c); }

/* argv: BRIDGE walk | keyboard | click | then-five | summary */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    if (install_start(a, &b, 0)) { printf("start-failed\n"); return 3; }
    a->install.view = INSTALL_BACKUP; refocus(a);
    printf("home=%%d\n", a->focus == HIT_INSTALL_BACKUP_BASE);
    if (!strcmp(argv[2], "walk")) {            /* Down from 5 GB to the end and round: OFF is passed, not chosen */
        int seen_off = 0, chose_off = 0;
        for (int i = 0; i < 8; i++) { key(a, &b, KEYSYM_DOWN); if (a->focus == HIT_INSTALL_BACKUP_OFF) seen_off = 1; if (a->install.backup_pick == INSTALL_BACKUP_OFF) chose_off = 1; }
        printf("seen_off=%%d\nchose_off=%%d\n", seen_off, chose_off);
        a->install.backup_pick = 0; refocus(a);
        key(a, &b, KEYSYM_BACKTAB);           /* 5 GB -> the hour (the last stop) */
        printf("last_is_hour=%%d\n", a->focus == HIT_INSTALL_BACKUP_TIME);
        return 0;
    }
    if (!strcmp(argv[2], "keyboard")) { key(a, &b, KEYSYM_BACKTAB); key(a, &b, KEYSYM_BACKTAB); printf("on_off=%%d\nstill=%%d\n", a->focus == HIT_INSTALL_BACKUP_OFF, a->install.backup_pick); space(a, &b); }
    else if (!strcmp(argv[2], "click")) { choose(a, &b, HIT_INSTALL_BACKUP_OFF); }
    else if (!strcmp(argv[2], "then-five")) { choose(a, &b, HIT_INSTALL_BACKUP_OFF); choose(a, &b, HIT_INSTALL_BACKUP_BASE); }
    else if (!strcmp(argv[2], "summary")) {
        choose(a, &b, HIT_INSTALL_BACKUP_OFF); a->install.view = INSTALL_SUMMARY; choose(a, &b, HIT_INSTALL_SUMMARY_BASE + 5);
        printf("row5=%%d\nbackup=%%d\nhome_off=%%d\n", a->install.view, INSTALL_BACKUP, a->focus == HIT_INSTALL_BACKUP_OFF); return 0;
    } else return 2;
    int f[8], n = fields(&a->install, f), hour = 0; for (int i = 0; i < n; i++) hour |= f[i] == HIT_INSTALL_BACKUP_TIME;   /* on the step, before "next" */
    int ready = install_backup_ready(&a->install); key(a, &b, KEYSYM_ENTER);
    printf("moved_on=%%d\npick=%%d\nready=%%d\nhour_field=%%d\n", a->install.view != INSTALL_BACKUP, a->install.backup_pick, ready, hour);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-backup-off")
    source = tmp / "install_backup_off.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_backup_off"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _drive(driver: Path, tmp_path: Path, *args: str) -> tuple[dict, dict | None]:
    bridge_cmd = (f'f(){{ cat > "{tmp_path}/$1.json"; case "$1" in '
                  "inventory) printf 'disk=/dev/sda|model=X|size=500000000000|bus=sata|removable=0|has=empty|iso=0\\n' ;; "
                  "progress) printf 'state=ready\\n' ;; esac; }; f")
    proc = subprocess.run([str(driver), bridge_cmd, *args], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    state = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    sent = tmp_path / "validate.json"
    return state, json.loads(sent.read_text()) if sent.exists() else None


@NATIVE
def test_the_arrows_pass_the_option_without_choosing_it(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "walk")
    assert state["home"] == "1", "5 GB is still the default"
    assert state["seen_off"] == "1" and state["chose_off"] == "0", state
    assert state["last_is_hour"] == "1", "the hour is still reached from the keyboard"
    assert sent is None


@NATIVE
def test_keyboard_only_space_chooses_it_and_enter_sends_no_backup(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "keyboard")
    assert state["on_off"] == "1" and state["still"] == "0", "landing did not choose it"
    assert state["pick"] == "4" and state["moved_on"] == "1" and state["hour_field"] == "0", state
    assert (sent["backup_enabled"], sent["backup_reserve_gb"], sent["step"]) == ("false", "", 7), sent


@NATIVE
def test_a_click_chooses_it(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "click")
    assert state["pick"] == "4" and state["ready"] == "1" and state["moved_on"] == "1"
    assert sent["backup_enabled"] == "false" and sent["backup_reserve_gb"] == ""


@NATIVE
def test_choosing_a_size_again_turns_it_back_on(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "then-five")
    assert state["pick"] == "0" and state["hour_field"] == "1"
    assert (sent["backup_enabled"], sent["backup_reserve_gb"], sent["backup_time"]) == ("", "5", "02:00"), sent


@NATIVE
def test_the_summary_row_opens_the_step_on_the_option(driver, tmp_path):
    state, _ = _drive(driver, tmp_path, "summary")
    assert state["row5"] == state["backup"] and state["home_off"] == "1"


@NATIVE
def test_the_off_screens_render_in_both_themes(tmp_path):
    binary = _build(tmp_path)
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    prefix = tmp_path / "off"
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png", str(prefix), "--size", "1280x800"],
                          capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    for card in ("install-3g-backup-off", "install-5e-summary-backup-off"):
        for theme in ("light", "dark"):
            width, height, colors = _size_and_ink(tmp_path / f"off-{card}-{theme}.png")
            assert (width, height) == (1280, 800) and colors > 20, (card, theme)
