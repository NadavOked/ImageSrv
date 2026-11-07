"""#2144 (mockup 2144-time, Nadav 04/11): the native installer's "שעון השרת" step.

* step 5 of nine, after "שם השרת" -- the rail, the titles, the keys;
* automatic (default) or the organisation's server: "next" stays off until
  the organisation's server has an address; the form sends ``ntp_servers``
  (empty = automatic) with its own step (6 -- the bridge's 4 is "passwords");
* the summary's "שעון השרת" row, whose "שינוי" opens step 5;
* the done screen reads the bridge's clock report -- anything it does not
  know is "not checked" (principle 5).

The C is built and run on Linux (the testrunner); the source contracts run
everywhere.
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
MOCKUP_STRINGS = (   # docs/design/mockups-1104/2144-time.html, the strings table -- word for word
    "שעון השרת", "מאיפה השרת מקבל את השעה. אפשר לשנות אחר כך בקונסולה.", "שרת זמן (NTP)",
    "שרתי הזמן של Debian. צריך גישה לאינטרנט ב-UDP 123.", "כשרשת המכללה חוסמת NTP לאינטרנט.",
    "השעה עכשיו בשרת", "לפי שעון החומרה, עוד בלי סנכרון. הסנכרון נבדק בסוף ההתקנה.",
    "ההתקנה הושלמה — השעון לא סונכרן",
    "לא ענה עד סוף ההתקנה. עד שיענה, השעה בשרת עלולה להיות שגויה, ואיתה הזמנים ביומן, בסבבים ובבדיקת תוקף התעודה.",
    "אחרי ההפעלה מחדש: לבדוק שהרשת מאפשרת NTP (UDP 123) אל שרת הזמן, או לשנות בקונסולה: הגדרות ← מקור השעה. "
    'עד אז מסך השרת יציג \\"השעון לא מסונכרן\\".',
    "ההתקנה הושלמה — לא הצלחנו לבדוק את השעון", "chrony לא החזיר תשובה, ולכן לא ידוע אם השעון מסונכרן.",
    'אחרי ההפעלה מחדש: בקונסולה, הגדרות ← מצב השעון ← \\"בדוק עכשיו\\".',
    "מסונכרן", "לא מסונכרן", "לא נבדק", "סטייה %s ms",
    "אוטומטי (שרתי זמן באינטרנט)", "שרת זמן של הארגון", "כתובות (IP או שם, מופרדות ברווח)",
)


def _c(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# --- the source contracts (every machine) ---------------------------------------

def test_the_clock_is_step_five_after_the_name():
    state = _c("install_state.h")
    assert re.search(r"INSTALL_HOSTNAME, INSTALL_TIME, INSTALL_BACKUP, INSTALL_ADMIN,", state)
    assert "#define INSTALL_STEPS 9" in state
    frame = _c("screens_install.c")
    assert ('STEP_NAMES[INSTALL_STEPS]={"דיסק","תפקיד השרת","כתובת השרת","שם השרת","שעון השרת","גיבוי",'
            '"חשבון ניהול","החלה","סיום"};') in frame
    # the rail: "החלה" is the summary, progress and failure; "סיום" is the last
    assert "#define APPLY_STEP (INSTALL_STEPS-1)" in frame
    assert "return APPLY_STEP;if(s->view==INSTALL_DONE)return INSTALL_STEPS;" in frame
    assert "bad=a->install.view==INSTALL_FAILED&&i==APPLY_STEP-1;" in frame
    titles = re.search(r'const char \*titles\[\]=\{(.*?)\};', frame).group(1)
    assert titles.split(",")[5] == '"שעון השרת"'
    subs = re.search(r'const char \*subs\[\]=\{(.*?)\};', frame).group(1)
    assert subs.split('","')[5] == "מאיפה השרת מקבל את השעה. אפשר לשנות אחר כך בקונסולה."
    assert "case INSTALL_ROLE:case INSTALL_TIME:case INSTALL_BACKUP:return" in frame, "the role step's keys: options"


def test_every_new_string_is_the_mockups():
    text = _c("screens_install.c") + _c("screens_install_forms.c") + _c("screens_install_final.c")
    missing = [s for s in MOCKUP_STRINGS if s not in text]
    assert not missing, missing


def test_the_form_sends_the_source_and_its_own_step():
    control = _c("install.c")
    assert 'json_pair(&p, &n, "ntp_servers", s->ntp_org ? s->ntp_servers : "", 1);' in control
    assert "(view==INSTALL_TIME?6:(view==INSTALL_BACKUP?7:view-INSTALL_DISK))" in control
    assert "&&(s->view!=INSTALL_TIME||install_time_ready(s))" in control
    assert '"root_password_confirm","ntp_servers",' in control
    assert "[INSTALL_TIME]={17,17}" in control and "case HIT_INSTALL_NTP_SERVERS:return 17;" in control


def test_an_unknown_clock_report_is_not_checked():
    control = _c("install.c")
    assert ('s->clock=!strcmp(eq,"synced")?INSTALL_CLOCK_SYNCED:!strcmp(eq,"unsynced")?'
            'INSTALL_CLOCK_UNSYNCED:INSTALL_CLOCK_UNCHECKED;') in control
    assert "enum { INSTALL_CLOCK_UNCHECKED = 0," in _c("install_state.h"), "a zeroed state is 'not checked'"


def test_the_summary_has_the_clock_row_and_its_change_opens_step_five():
    final = _c("screens_install_final.c")
    assert '"שעון השרת",s->ntp_org?s->ntp_servers:"אוטומטי",4);' in final
    assert '"משתמש הניהול",value,6);' in final and '"סיסמת root","נקבעה",6);' in final
    assert "id<HIT_INSTALL_SUMMARY_BASE+7)" in _c("install.c")


def test_the_time_on_the_step_never_carries_another_zones_name():
    control = _c("install.c")
    body = control[control.index("int install_clock_now("):control.index("/* #1310: green")]
    assert 'access(path,R_OK)==0' in body and 'snprintf(zone,zn,"UTC")' in body and "gmtime_r" in body


# --- the C itself (Linux) ---------------------------------------------------------

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

/* argv: BRIDGE MODE [TEXT] -- auto | org-empty | org TEXT | summary | report TEXT */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    if (install_start(a, &b, 0)) { printf("start-failed\n"); return 3; }
    a->install.view = INSTALL_TIME; refocus(a);
    if (!strcmp(argv[2], "auto")) key(a, &b, KEYSYM_ENTER);
    else if (!strcmp(argv[2], "org-empty")) { key(a, &b, KEYSYM_DOWN); key(a, &b, KEYSYM_ENTER); }
    else if (!strcmp(argv[2], "org") && argc > 3) { key(a, &b, KEYSYM_DOWN); key(a, &b, KEYSYM_DOWN); type(a, &b, argv[3]); key(a, &b, KEYSYM_ENTER); }
    else if (!strcmp(argv[2], "summary")) {
        a->install.view = INSTALL_SUMMARY; choose(a, &b, HIT_INSTALL_SUMMARY_BASE + 4); printf("row4=%%d\n", a->install.view);
        a->install.view = INSTALL_SUMMARY; choose(a, &b, HIT_INSTALL_SUMMARY_BASE + 6); printf("row6=%%d\n", a->install.view);
        printf("time=%%d\nadmin=%%d\n", INSTALL_TIME, INSTALL_ADMIN); return 0;
    } else if (!strcmp(argv[2], "report") && argc > 3) {
        char text[512]; snprintf(text, sizeof text, "%%s", argv[3]); parse_lines(&a->install, text, "finish");
        printf("clock=%%d\nserver=%%s\noffset=%%s\n", a->install.clock, a->install.clock_server, a->install.clock_offset); return 0;
    } else return 2;
    printf("moved_on=%%d\nntp_org=%%d\nntp_servers=%%s\n", a->install.view != INSTALL_TIME, a->install.ntp_org, a->install.ntp_servers);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-time")
    source = tmp / "install_time.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_time"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _drive(driver: Path, tmp_path: Path, *args: str, error: str = "") -> tuple[dict, dict | None]:
    bridge_cmd = (f'f(){{ cat > "{tmp_path}/$1.json"; case "$1" in '
                  "inventory) printf 'disk=/dev/sda|model=X|size=1|bus=sata|removable=0|has=empty|iso=0\\n' ;; "
                  "progress) printf 'state=ready\\n' ;; "
                  'validate) [ -z "$NTP_ERR" ] || printf "errors.ntp_servers=%s\\n" "$NTP_ERR" ;; esac; }; f')
    proc = subprocess.run([str(driver), bridge_cmd, *args], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL, env={**os.environ, "NTP_ERR": error})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    state = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    sent = tmp_path / "validate.json"
    return state, json.loads(sent.read_text()) if sent.exists() else None


@NATIVE
def test_automatic_is_the_default_and_sends_an_empty_source(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "auto")
    assert state["moved_on"] == "1" and state["ntp_org"] == "0"
    assert sent["ntp_servers"] == "" and sent["step"] == 6


@NATIVE
def test_the_organisations_server_without_an_address_keeps_next_off(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "org-empty")
    assert state["ntp_org"] == "1" and state["moved_on"] == "0"
    assert sent is None, "an empty address is not even sent to the bridge"


@NATIVE
def test_the_typed_address_is_sent_and_moves_on(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "org", "10.44.0.250 ntp.college")
    assert state["ntp_servers"] == "10.44.0.250 ntp.college" and state["moved_on"] == "1"
    assert sent["ntp_servers"] == "10.44.0.250 ntp.college" and sent["step"] == 6


@NATIVE
def test_a_refused_address_keeps_the_person_on_the_step(driver, tmp_path):
    state, _ = _drive(driver, tmp_path, "org", "bad_host", error="כתובת שרת הזמן אינה תקינה")
    assert state["moved_on"] == "0"


@NATIVE
def test_the_summary_rows_open_the_clock_and_the_admin_steps(driver, tmp_path):
    state, _ = _drive(driver, tmp_path, "summary")
    assert state["row4"] == state["time"] and state["row6"] == state["admin"]


@NATIVE
@pytest.mark.parametrize("report,clock", [
    ("clock=synced\nclock_server=10.44.0.250\nclock_offset_ms=3\n", "1"),
    ("clock=unsynced\nclock_server=10.44.0.250\n", "2"),
    ("clock=unchecked\n", "0"),
    ("clock=maybe\n", "0"),
    ("user=admin\n", "0"),      # an older bridge, no report at all
])
def test_the_done_screen_reads_the_report_and_anything_else_is_not_checked(driver, tmp_path, report, clock):
    state, _ = _drive(driver, tmp_path, "report", report)
    assert state["clock"] == clock
