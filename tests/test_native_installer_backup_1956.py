"""#1956 (mockup 1956-installer-backup, Nadav 06/11): the native installer's "גיבוי" step.

* step 6 of nine, between "שעון השרת" and "חשבון ניהול" -- the rail, the titles, the keys;
* 5 / 10 / 20 GB and "גודל אחר", the hour, and the fixed orange "אותו כונן" -- there is no "skip";
* the form sends ``backup_time`` and ``backup_reserve_gb`` with its own step (7);
* the summary's "גיבוי המסד" row, whose "שינוי" opens step 6.

The C is built and run on Linux (the testrunner); the source contracts run everywhere.
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

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
MOCKUP_STRINGS = (   # docs/design/mockups-1107/1956-installer-backup.html, word for word
    "גיבוי המסד", "הגיבויים נשמרים על הכונן של השרת, במקום ששמור להם מראש. אחסון הרשת (SMB, iSCSI) משמש רק לאימג׳ים.",
    "<b>מתי מגבים:</b> פעם ביום ב-%s (אפשר לשנות מתחת), ובנוסף מיד בכל העלאה של אימג׳ חדש ובכל מחיקה של אימג׳ מהשרת.",
    "מוצע. מספיק לגיבויים רבים של המסד, ומשאיר כמעט את כל הכונן לאימג׳ים.", "יותר היסטוריה.", "היסטוריה ארוכה.",
    "גודל אחר", "הקלידו מספר ב-GB.", "שעת הגיבוי היומי", "מקום שמור (GB)",
    "מוצע 02:00: שעה בלי סבבים. אם רץ סבב באותה שעה, הגיבוי מחכה לסיומו.",
    "שמורים לגיבויים %s מתוך %s פנויים בכונן",
    "<b>אותו כונן, לא מגן מתקלת כונן.</b> הגיבוי עוזר נגד מחיקה או שחיתות של המסד, לא נגד מות הדיסק. עותק על כונן אחר אפשר להגדיר אחר כך בקונסולה.",
    "מינימום 1 GB. לא יותר מ-20% מהכונן, כדי שיישאר מקום לאימג׳ים.",
    "%s שמורים על כונן השרת · יומי ב-%s ובכל שינוי אימג׳",
)


def _c(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_the_backup_is_step_six_of_nine_after_the_clock():
    assert "INSTALL_TIME, INSTALL_BACKUP, INSTALL_ADMIN," in _c("install_state.h")
    assert "#define INSTALL_STEPS 9" in _c("install_state.h")
    frame = _c("screens_install.c")
    assert '"שעון השרת","גיבוי","חשבון ניהול","החלה","סיום"};' in frame
    titles = re.search(r'const char \*titles\[\]=\{(.*?)\};', frame).group(1)
    assert titles.split(",")[6] == '"גיבוי המסד"'
    subs = re.search(r'const char \*subs\[\]=\{(.*?)\};', frame).group(1)
    assert subs.split('","')[6].startswith("הגיבויים נשמרים על הכונן של השרת")


def test_every_new_string_is_the_mockups():
    text = _c("screens_install.c") + _c("screens_install_forms.c") + _c("screens_install_final.c")
    missing = [s for s in MOCKUP_STRINGS if s not in text]
    assert not missing, missing


def test_there_is_no_skip_and_no_network_destination_on_the_step():
    body = _c("screens_install_forms.c")
    step = body[body.index("void install_draw_backup("):body.index("void install_draw_forms(")]
    assert "דלג" not in step and "NFS" not in step
    assert "t->warning_bg" in step, "the orange note is drawn"


def test_the_summary_has_the_row_and_the_form_its_own_step():
    assert '"גיבוי המסד",value,5);' in _c("screens_install_final.c")
    control = _c("install.c")
    assert 'json_pair(&p, &n, "backup_time", s->backup_time, 1);' in control
    assert 'json_pair(&p, &n, "backup_reserve_gb", install_backup_gb(s), 1);' in control
    assert "(view==INSTALL_BACKUP?7:" in control and "[INSTALL_BACKUP]={18,19}" in control


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs], stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("cairo", _pkgconfig("cairo")),
    posix=True, why="install.c is built and run on Linux only",
)

DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }
static App app;
static void key(App *a, InstallBridge *b, int k) { Event e = {0}; e.type = UIEV_KEY; e.key = k; install_handle(a, b, &e); }
static void type(App *a, InstallBridge *b, const char *s) { for (; *s; s++) { Event c = {0}; c.type = UIEV_CHAR; c.ch = *s; install_handle(a, b, &c); } }

/* argv: BRIDGE default | pick N | other TEXT | other-empty | hour TEXT | summary */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    if (install_start(a, &b, 0)) { printf("start-failed\n"); return 3; }
    a->install.view = INSTALL_BACKUP; refocus(a);
    if (!strcmp(argv[2], "default")) key(a, &b, KEYSYM_ENTER);
    else if (!strcmp(argv[2], "pick") && argc > 3) { for (int i = 0; i < atoi(argv[3]); i++) key(a, &b, KEYSYM_DOWN); key(a, &b, KEYSYM_ENTER); }
    else if (!strcmp(argv[2], "other") && argc > 3) { for (int i = 0; i < 5; i++) key(a, &b, KEYSYM_DOWN); type(a, &b, argv[3]); key(a, &b, KEYSYM_ENTER); }   /* #2865: past "בלי גיבוי כרגע" (not chosen on the way) */
    else if (!strcmp(argv[2], "other-empty")) { choose(a, &b, HIT_INSTALL_BACKUP_BASE + 3); key(a, &b, KEYSYM_ENTER); }
    else if (!strcmp(argv[2], "hour") && argc > 3) { a->focus = HIT_INSTALL_BACKUP_TIME; for (int i = 0; i < 5; i++) key(a, &b, KEYSYM_BACKSPACE); type(a, &b, argv[3]); key(a, &b, KEYSYM_ENTER); }
    else if (!strcmp(argv[2], "summary")) {
        a->install.view = INSTALL_SUMMARY; choose(a, &b, HIT_INSTALL_SUMMARY_BASE + 5); printf("row5=%%d\nbackup=%%d\n", a->install.view, INSTALL_BACKUP); return 0;
    } else return 2;
    printf("moved_on=%%d\npick=%%d\ngb=%%s\nhour=%%s\nerror18=%%s\n", a->install.view != INSTALL_BACKUP, a->install.backup_pick, install_backup_gb(&a->install), a->install.backup_time, a->install.field_error[18]);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-backup")
    source = tmp / "install_backup.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_backup"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _drive(driver: Path, tmp_path: Path, *args: str, error: str = "") -> tuple[dict, dict | None]:
    bridge_cmd = (f'f(){{ cat > "{tmp_path}/$1.json"; case "$1" in '
                  "inventory) printf 'disk=/dev/sda|model=X|size=500000000000|bus=sata|removable=0|has=empty|iso=0\\n' ;; "
                  "progress) printf 'state=ready\\n' ;; "
                  'validate) [ -z "$RESERVE_ERR" ] || printf "errors.backup_reserve_gb=%s\\n" "$RESERVE_ERR" ;; esac; }; f')
    proc = subprocess.run([str(driver), bridge_cmd, *args], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL, env={**os.environ, "RESERVE_ERR": error})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    state = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    sent = tmp_path / "validate.json"
    return state, json.loads(sent.read_text()) if sent.exists() else None


@NATIVE
def test_five_gb_at_two_is_the_default_and_moves_on(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "default")
    assert state["moved_on"] == "1" and state["pick"] == "0"
    assert (sent["backup_reserve_gb"], sent["backup_time"], sent["step"]) == ("5", "02:00", 7)


@NATIVE
@pytest.mark.parametrize("steps, gb", [("1", "10"), ("2", "20")])
def test_the_arrows_pick_a_size(driver, tmp_path, steps, gb):
    state, sent = _drive(driver, tmp_path, "pick", steps)
    assert state["moved_on"] == "1" and sent["backup_reserve_gb"] == gb


@NATIVE
def test_another_size_is_typed_and_sent(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "other", "40")
    assert state["pick"] == "3" and state["moved_on"] == "1", state
    assert sent["backup_reserve_gb"] == "40"


@NATIVE
def test_another_size_left_empty_keeps_next_off(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "other-empty")
    assert state["moved_on"] == "0" and sent is None, "an empty size is not even sent to the bridge"


@NATIVE
def test_a_refused_size_keeps_the_person_on_the_step_with_the_engines_line(driver, tmp_path):
    state, _ = _drive(driver, tmp_path, "other", "500", error="המקום השמור אינו תקין")
    assert state["moved_on"] == "0" and state["error18"] == "המקום השמור אינו תקין"


@NATIVE
def test_the_hour_is_typed_and_sent(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "hour", "23:15")
    assert state["hour"] == "23:15" and sent["backup_time"] == "23:15" and state["moved_on"] == "1"


@NATIVE
def test_the_summary_row_opens_the_backup_step(driver, tmp_path):
    state, _ = _drive(driver, tmp_path, "summary")
    assert state["row5"] == state["backup"]
