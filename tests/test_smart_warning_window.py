"""חלון אזהרת SMART (הכרעת נדב 16/10) — וגם W/N במסך החדר בלי סבב.

ההכרעה: כתיבה על דיסק עם אזהרת SMART מותרת, וגם קריאה ממנו (להציל
נתונים מדיסק שמתחיל להיכשל) — **אבל רק אחרי חלון** שמציג את האזהרה:
הדיסק לפי המיפוי, הצבע, הסיבה, דגם ומספר סידורי, ושני כפתורים: "דלג"
(ברירת מחדל, הפוקוס, Esc) ו"כתוב בכל זאת" אדום. רק הכפתור האדום שבחלון
מבצע. היום (לפני התיקון) Enter על שאלת ה-SMART במחשב השיכפול שלח מיד
‏`smart-rescue`; שחזור לדיסק מחשב הבנייה וקליטה ממנו לא ידעו בכלל על
SMART; ושיכפול מקומי הפך דיסק עם אזהרה ליעד בקליק, ומקור עם אזהרה עבר
בלי מילה.

שני חצאים:

* **הסוכן** (bash, רץ גם בווינדוס כש-Git bash קיים): ‏`gui_smart_choice`
  כותב דגם וסידורי ב-`smart-request`; ‏`attended.sh` עדיין שולח לקונסולה
  את הסיבה לבדה; ‏`gui_state` כותב `dsmart=` במסכי הקליטה והשחזור בלבד
  (צריך jq).
* **הגואי** (C, רק על לינוקס עם cc+pango): דרייבר שמכליל את `main.c`
  ומזין אירועים אמיתיים ל-`handle()` — כל מסלול: Enter על אזהרה פותח
  חלון ולא כותב; "דלג" לא כותב; האדום כותב; מקור עם אזהרה עובר חלון.
  ו-W/N בלי סבב לא שולחים כלום.

**בקרה שלילית:** הטסטים של הגואי נופלים על הקוד הישן על התנהגות (רשומה
‏`smart-rescue`/`restore-start`/`capture-start` שנשלחה בלי חלון, ‏`room-wake`
בלי סבב); טסטי הסוכן נופלים על `smart-request` של ארבעה שדות ועל
‏`gui_state` בלי `dsmart=`. הפקודות — בגוף ה-PR.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import (BASH, GUI, PORTS, PRELUDE, _build_gui, _pkgconfig, _png_rgb,
                             env, posix, sh)

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

bash_only = requires_native(("bash", BASH))
native_gui = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)


# --- הסוכן: smart-request נושא דגם וסידורי ----------------------------------------


def _fake_sysfs(root: Path, dev: str, model: str, serial: str) -> None:
    d = root / "sys" / "block" / dev / "device"
    d.mkdir(parents=True)
    (d / "model").write_text(model + "\n", newline="\n")
    (d / "serial").write_text(serial + "\n", newline="\n")


@bash_only
def test_the_smart_request_carries_the_model_and_the_serial(tmp_path):
    """הבקשה שהגואי מצייר היא nonce|port|verdict|reason|model|serial — הפרטים
    שהחלון מציג. ‏`|` בתוך דגם אינו מפצל רשומה (מוחלף ברווח)."""
    gui = tmp_path / "gui"; gui.mkdir()
    run = tmp_path / "run"; run.mkdir()
    sysroot = tmp_path / "root"
    _fake_sysfs(sysroot, "sdb", "  WDC WD5000|AAKX  ", "WD-WCC2EVC71234")
    script = (env(run, gui) + f"export SYSROOT={posix(sysroot)!r}; " + PRELUDE + PORTS + "D_ROLE=cloner; " + f"""
sleep 20 & _gui_pid=$!
( gui_smart_choice sdb fail pending_sectors > {posix(gui)}/out ) &
CH=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -f {posix(gui)}/smart-request ] && break; sleep 0.3; done
cp {posix(gui)}/smart-request {posix(gui)}/seen
N=$(IFS='|' read -r n _ < {posix(gui)}/smart-request; echo "$n")
printf '%s|skip\\n' "$N" > {posix(gui)}/smart-decision
wait $CH; kill $_gui_pid 2>/dev/null; true
""")
    out = sh(script)
    assert out.returncode == 0, out.stderr
    seen = (gui / "seen").read_text().strip()
    fields = seen.split("|")
    assert fields[1:] == ["2", "fail", "pending_sectors", "WDC WD5000 AAKX", "WD-WCC2EVC71234"], seen


@requires_native(("bash", BASH), "jq", why="cloner_gui_state קורא את שם האימג' ב-jq")
def test_the_cloner_state_passes_the_details_through_to_the_prompt(tmp_path):
    from test_cloner_gui import state_lines
    lines = state_lines(tmp_path, smart_request="12-2|2|fail|pending_sectors|WDC WD5000|WD-123")
    assert "smart_prompt=12-2|2|fail|pending_sectors|WDC WD5000|WD-123" in lines, lines


@bash_only
def test_the_console_ask_still_carries_the_reason_alone(tmp_path):
    """‏`attended.sh` קרא ארבעה שדות — המשתנה האחרון היה בולע את
    ‏`reason|model|serial` ושולח אותו לקונסולה כסיבה."""
    from test_smart_ask_agent import JQ_STUB, prelude, request
    box = tmp_path
    (box / "run").mkdir(); (box / "gui").mkdir(); (box / "bin").mkdir()
    if shutil.which("jq") is None:
        (box / "jqstub.py").write_text(JQ_STUB, encoding="utf-8", newline="\n")
        (box / "bin" / "jq").write_text(f'#!/bin/sh\nexec python "{posix(box / "jqstub.py")}" "$@"\n', newline="\n")
    (box / "gui" / "smart-request").write_text("3-2|2|fail|pending_sectors|WDC WD5000|WD-123\n", newline="\n")
    out = sh(prelude(box, None) + 'attended_hello "Disk 2: SMART fail"')
    assert out.returncode == 0, out.stderr
    assert request(box)["ask"] == {"nonce": "3-2", "port": 2, "verdict": "fail", "reason": "pending_sectors"}


# --- הסוכן: dsmart= במסכי הקליטה והשחזור ----------------------------------------

STATION = {"known": True, "role": "build", "task": None, "allowed_images": [],
           "disks": [{"dev": "sda", "model": "Samsung SSD", "size_bytes": 500107862016, "has_data": True,
                      "removable": False},
                     {"dev": "sdb", "model": "USB stick", "size_bytes": 16000000000, "has_data": True,
                      "removable": True}]}

#: smart_probe / smart_field / disk_serial מוזרקים: הקריאה של smartctl נבדקת
#: ב-test_smart; כאן נבדק מה נכתב לגואי. כל probe נרשם, כדי לראות שהמטמון
#: מונע probe חוזר בכל שנייה.
SMART_STUBS = (
    'smart_probe() { echo "$1" >> "$RUN_DIR/probed"; echo fail; }; '
    'smart_field() { echo pending_sectors; }; '
    'smart_ro_stale() { return 1; }; '   # #1742: the read-only redraw, tested in test_readonly_red_1742
    'disk_serial() { printf "S5Y2|NX0R"; }; '
    'disk_port() { echo 1; }; '
    'gui_get() { echo "[]" > "$2"; }; '
)


def _gui_state(tmp_path: Path, mode: str, runs: int = 1) -> list[str]:
    run = tmp_path / "run"; run.mkdir(parents=True, exist_ok=True)
    gui = tmp_path / "gui"; gui.mkdir(parents=True, exist_ok=True)
    (run / "station.json").write_text(json.dumps(STATION), newline="\n")
    (gui / "mode").write_text(mode + "\n", newline="\n")
    script = (
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
        f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
        f'. {posix(AGENT)}/lib/gui/buildmenuitems.sh; . {posix(AGENT)}/lib/gui/buildmenu.sh; '
        f'. {posix(AGENT)}/lib/gui/guistate.sh; . {posix(AGENT)}/lib/gui/askgui.sh; . {posix(AGENT)}/lib/gui/syspartgui.sh; '   # #1896: syspart_records
        + SMART_STUBS
        + 'disk_slot_records() { :; }; '   # #1495's drawer numbering, tested in test_drawer_map_gui_1495
        + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
        + " ".join(["gui_state || exit 9;"] * runs)
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
@pytest.mark.parametrize("mode", ["restore", "capture"])
def test_the_capture_and_restore_screens_get_the_smart_of_the_own_disks(tmp_path, mode):
    lines = _gui_state(tmp_path, mode, runs=3)
    dsmart = [l for l in lines if l.startswith("dsmart=")]
    assert dsmart == ["dsmart=sda|fail|pending_sectors|S5Y2 NX0R|1"], lines   # not the removable sdb
    # one smartctl per disk, not one per second (the line is kept per disk)
    assert (tmp_path / "run" / "probed").read_text().split() == ["sda"]


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_the_menu_does_not_probe_smart(tmp_path):
    lines = _gui_state(tmp_path, "menu")
    assert not any(l.startswith("dsmart=") for l in lines), lines
    assert not (tmp_path / "run" / "probed").exists()


# --- הגואי: דרייבר שמזין אירועים ל-handle() של main.c ------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define main station_gui_main
#include "main.c"
#undef main
/* The same driver compiles against the code before the window (the negative
 * control reverts every production file): there is no window to report. */
#ifdef IMAGESRV_SMARTDLG_H
#define DRV_SDLG_KIND(a) ((a).sdlg.kind)
#define DRV_SDLG_GO(a) ((a).sdlg.go)
#else
#define DRV_SDLG_KIND(a) 0
#define DRV_SDLG_GO(a) 0
#endif
static void drv_draw(App *a) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_route(a); app_draw(a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_destroy(s);
}
static void drv_load(App *a, const char *path) {
    FILE *f = fopen(path, "r"); if (!f) { perror(path); exit(2); }
    state_parse(&a->st, f); fclose(f); after_reload(a);
}
static int drv_hit(const char *n) {
    static const struct { const char *n; int id; } T[] = {
        {"SMART_RESCUE", HIT_SMART_RESCUE}, {"SMART_SKIP", HIT_SMART_SKIP},
#ifdef IMAGESRV_SMARTDLG_H
        {"SDLG_SKIP", HIT_SDLG_SKIP}, {"SDLG_GO", HIT_SDLG_GO},
#endif
        {"ROOM_WAKE", HIT_ROOM_WAKE}, {"ROOM_START", HIT_ROOM_START},
        {"RESTORE_START", HIT_RESTORE_START}, {"CONFIRM_OK", HIT_CONFIRM_OK}, {"START", HIT_START},
        {"LC_NEXT", HIT_LC_NEXT}, {"LC_GO", HIT_LC_GO} };
    for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) if (!strcmp(T[i].n, n)) return T[i].id;
    int i, r; if (sscanf(n, "ROLE%d.%d", &i, &r) == 2) return HIT_LC_ROLE_BASE + i * 3 + r;
    if (sscanf(n, "MACHINE%d", &i) == 1) return HIT_ROOM_MACHINE_BASE + i;   /* 17/10: a cloner's name */
    return -1;
}
int main(int argc, char **argv) {
    static App a; memset(&a, 0, sizeof a);
    a.theme = &THEME_LIGHT; a.default_theme = a.theme; a.focus = HIT_USER; a.menu_focus = -1; a.chosen_disk = -1;
    a.signed_in = 1; a.admin = 1; snprintf(a.signed_user, sizeof a.signed_user, "admin");
    snprintf(a.mac, sizeof a.mac, "3C:52:82:A1:00:1F");
    int i = 1;
    for (; i < argc && strncmp(argv[i], "state:", 6); i++) {          /* the App before the first state */
        const char *v = argv[i];
        if (!strcmp(v, "mode:restore")) a.mode = MODE_RESTORE;
        else if (!strcmp(v, "mode:capture")) a.mode = MODE_CAPTURE;
        else if (!strcmp(v, "mode:room")) a.mode = MODE_ROOM;
        else if (!strcmp(v, "mode:cloner")) a.force_cloner = 1;
        else if (!strncmp(v, "image:", 6)) a.restore_image_sel = atoi(v + 6);
        else if (!strncmp(v, "disk:", 5)) a.chosen_disk = atoi(v + 5);
        else if (!strncmp(v, "name:", 5)) snprintf(a.name, sizeof a.name, "%s", v + 5);
        else if (!strncmp(v, "folder:", 7)) a.folder_sel = atoi(v + 7);
    }
    for (; i < argc; i++) {
        Event e; memset(&e, 0, sizeof e); const char *v = argv[i];
        if (!strncmp(v, "state:", 6)) { drv_load(&a, v + 6); drv_draw(&a); printf("@ reload\n"); fflush(stdout); continue; }
        if (!strncmp(v, "key:", 4)) {
            const char *k = v + 4; e.type = UIEV_KEY;
            e.key = !strcmp(k, "LEFT") ? KEYSYM_LEFT : !strcmp(k, "RIGHT") ? KEYSYM_RIGHT : !strcmp(k, "UP") ? KEYSYM_UP
                  : !strcmp(k, "DOWN") ? KEYSYM_DOWN : !strcmp(k, "TAB") ? KEYSYM_TAB : !strcmp(k, "ENTER") ? KEYSYM_ENTER : KEYSYM_ESC;
        } else if (!strncmp(v, "char:", 5)) { e.type = UIEV_CHAR; e.ch = v[5]; e.he = v[5]; }
        else if (!strncmp(v, "hit:", 4)) {
            int id = drv_hit(v + 4), found = 0;
            for (int h = a.nhits - 1; h >= 0 && !found; h--) if (a.hits[h].id == id) {
                e.type = UIEV_CLICK; e.x = a.hits[h].r.x + a.hits[h].r.w / 2; e.y = a.hits[h].r.y + a.hits[h].r.h / 2; found = 1; }
            if (!found) { printf("@ nohit %s\n", v + 4); fflush(stdout); continue; }
        }
        app_route(&a); handle(&a, &e, NULL, 1); drv_draw(&a);
        printf("@ screen=%d confirm=%d sdlg=%d go=%d lcconfirm=%d roles=", a.screen, a.confirm, DRV_SDLG_KIND(a), DRV_SDLG_GO(a), a.lcu.confirm);
        for (int d = 0; d < a.st.lc.ndisks; d++) printf("%s:%d,", a.st.lc.disks[d].dev, lc_role_of(&a, a.st.lc.disks[d].dev));
        printf("\n"); fflush(stdout);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("sdlgdrv")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def drive(driver: Path, tmp_path: Path, setup: list[str], state: str, *events: str) -> list[str]:
    path = tmp_path / "state.txt"
    path.write_text(state, encoding="utf-8", newline="\n")
    extra = []
    for ev in events:
        if ev.startswith("state="):
            p = tmp_path / f"s{len(extra)}.txt"; p.write_text(ev[6:], encoding="utf-8", newline="\n"); ev = f"state:{posix(p)}"
        extra.append(ev)
    env_ = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(driver), *setup, f"state:{posix(path)}", *extra], capture_output=True, text=True,
                         encoding="utf-8", timeout=120, env=env_, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return [l for l in run.stdout.splitlines() if l]


def records(lines: list[str]) -> list[str]:
    """The token lines the GUI sent (not '@' driver lines, not key=value details)."""
    return [l for l in lines if not l.startswith("@") and "=" not in l.split("|")[0]]


def states(lines: list[str]) -> list[str]:
    return [l for l in lines if l.startswith("@ screen")]


# --- מחשב השיכפול: שאלת ה-SMART ----------------------------------------------------

CLONER = "cloner=Office 2024\nsmart_prompt=7-2|2|fail|pending_sectors|WDC WD5000AAKX|WD-WCC2EVC71234\n"
#: #1732: only an orange disk is ever written through the window -- the mechanism is tested on it
CLONER_ORANGE = CLONER.replace("|fail|pending_sectors|", "|failed_last|cable|")


@native_gui
def test_cloner_enter_on_write_anyway_opens_the_window_and_writes_nothing(driver, tmp_path):
    out = drive(driver, tmp_path, ["mode:cloner"], CLONER, "key:ENTER")
    assert records(out) == [], out                                        # the old code sent smart-rescue|7-2 here
    assert "sdlg=1 go=0" in states(out)[-1], out                          # SDLG_CLONER, the focus on "דלג"


@native_gui
def test_cloner_skip_in_the_window_writes_nothing(driver, tmp_path):
    enter = drive(driver, tmp_path, ["mode:cloner"], CLONER, "key:ENTER", "key:ENTER")
    assert records(enter) == ["smart-skip|7-2"], enter                    # Enter on the default = "דלג"
    esc = drive(driver, tmp_path, ["mode:cloner"], CLONER, "key:ENTER", "key:ESC")
    assert records(esc) == ["smart-skip|7-2"], esc
    click = drive(driver, tmp_path, ["mode:cloner"], CLONER, "hit:SMART_RESCUE", "hit:SDLG_SKIP")
    assert records(click) == ["smart-skip|7-2"], click


@native_gui
def test_cloner_only_the_red_button_writes(driver, tmp_path):
    keys = drive(driver, tmp_path, ["mode:cloner"], CLONER_ORANGE, "key:ENTER", "key:TAB", "key:ENTER")
    assert records(keys) == ["smart-rescue|7-2"], keys
    click = drive(driver, tmp_path, ["mode:cloner"], CLONER_ORANGE, "hit:SMART_RESCUE", "hit:SDLG_GO")
    assert records(click) == ["smart-rescue|7-2"], click
    # the window owns the keys while open: S and R reach nothing behind it
    shield = drive(driver, tmp_path, ["mode:cloner"], CLONER, "key:ENTER", "char:S", "char:R")
    assert records(shield) == [], shield


@native_gui
def test_cloner_window_of_a_request_that_is_gone_answers_nothing(driver, tmp_path):
    new = CLONER.replace("7-2|", "8-2|")
    out = drive(driver, tmp_path, ["mode:cloner"], CLONER, "key:ENTER", f"state={new}", "key:TAB", "key:ENTER")
    assert "smart-rescue|7-2" not in records(out) and "smart-rescue|8-2" not in records(out), out


@native_gui
def test_cloner_red_disk_enter_opens_the_window_and_esc_skips(driver, tmp_path):
    """אדום (SMART fail, ‏CLONER): Enter פותח את החלון (לא כותב), ו-Esc בו הוא
    דלג. ‏#1732: החלון הזה אינו מציע כתיבה כלל — ראו הטסט שאחריו."""
    out = drive(driver, tmp_path, ["mode:cloner"], CLONER, "key:ENTER", "key:ESC")
    assert records(out) == ["smart-skip|7-2"], out


@native_gui
def test_cloner_orange_retry_writes_only_through_the_window(driver, tmp_path):
    """‏#1580: נכשל בשיכפול הקודם = כתום, "נסה שוב…" — Enter פותח את החלון,
    והכפתור האדום שבו (Tab, Enter) שולח rescue."""
    orange = CLONER.replace("|fail|pending_sectors|", "|failed_last|cable|")
    out = drive(driver, tmp_path, ["mode:cloner"], orange, "key:ENTER")
    assert records(out) == [] and "sdlg=1" in states(out)[-1], out
    out = drive(driver, tmp_path, ["mode:cloner"], orange, "key:ENTER", "key:TAB", "key:ENTER")
    assert records(out) == ["smart-rescue|7-2"], out


# --- מחשב הבנייה: שחזור לדיסק שלו, וקליטה ממנו ------------------------------------

BUILD = ("task=none\nmachine_name=BUILD-01\nimage=img_1|Office 2024|Office\nfolder=Office\n"
         "disk=sda|Samsung SSD 870 EVO|500107862016|1|0\n")
WARNED = BUILD + "dsmart=sda|fail|pending_sectors|S5Y2NX0R123456|1\n"     # red (SMART fail)
ORANGE = BUILD + "dsmart=sda|warn|crc|S5Y2NX0R123456|1\n"                  # #1732: the only colour written
CLEAN = BUILD + "dsmart=sda|ok|ok|S5Y2NX0R123456|1\n"
RESTORE = ["mode:restore", "image:1"]
CAPTURE = ["mode:capture", "disk:0", "name:Office 2024", "folder:1"]


def _starts(lines: list[str], token: str) -> int:
    return sum(1 for r in records(lines) if r == token)


@native_gui
def test_restore_onto_a_warned_disk_opens_the_window_after_the_erase_question(driver, tmp_path):
    out = drive(driver, tmp_path, RESTORE, ORANGE, "key:ENTER", "key:ENTER")
    assert _starts(out, "restore-start") == 0, out                        # the old code restored here
    assert "confirm=0 sdlg=2 go=0" in states(out)[-1], out                # SDLG_RESTORE, on "דלג"
    skip = drive(driver, tmp_path, RESTORE, ORANGE, "key:ENTER", "key:ENTER", "key:ENTER")
    assert _starts(skip, "restore-start") == 0 and "sdlg=0" in states(skip)[-1], skip
    go = drive(driver, tmp_path, RESTORE, ORANGE, "key:ENTER", "key:ENTER", "key:TAB", "key:ENTER")
    assert _starts(go, "restore-start") == 1, go
    click = drive(driver, tmp_path, RESTORE, ORANGE, "hit:RESTORE_START", "hit:CONFIRM_OK", "hit:SDLG_GO")
    assert _starts(click, "restore-start") == 1, click


@native_gui
def test_restore_onto_a_clean_disk_asks_only_the_erase_question(driver, tmp_path):
    """שומר מפני תיקון-יתר: דיסק בלי אזהרה (או לא-נבדק) אינו מקבל חלון."""
    for state in (CLEAN, BUILD):
        out = drive(driver, tmp_path, RESTORE, state, "key:ENTER", "key:ENTER")
        assert _starts(out, "restore-start") == 1, out


@native_gui
def test_capture_from_a_warned_disk_opens_the_window_and_red_captures(driver, tmp_path):
    out = drive(driver, tmp_path, CAPTURE, WARNED, "key:ENTER")
    assert _starts(out, "capture-start") == 0 and "sdlg=3" in states(out)[-1], out   # SDLG_CAPTURE
    skip = drive(driver, tmp_path, CAPTURE, WARNED, "key:ENTER", "key:ESC")
    assert _starts(skip, "capture-start") == 0, skip
    go = drive(driver, tmp_path, CAPTURE, WARNED, "hit:START", "hit:SDLG_GO")
    assert _starts(go, "capture-start") == 1 and "dev=sda" in go, go
    clean = drive(driver, tmp_path, CAPTURE, CLEAN, "key:ENTER")
    assert _starts(clean, "capture-start") == 1, clean


# --- שיכפול מקומי: יעד ומקור עם אזהרה ------------------------------------------------

LC = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
      "lc_disk=1|sda|Samsung|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
      "lc_disk=2|sdb|WDC|S2|500107862016|fail|health_failed|Ubuntu|2|38000000000|500107862016|1|\n"
      "lc_disk=3|sdc|Crucial|S3|480103981056|ok|ok|Windows|4|148600000000|480103981056|1|\n"
      "lc_src=sdc|none\nlc_fit=sda|fits|480103981056|\nlc_fit=sdb|fits|480103981056|\n")


LC_ORANGE = LC.replace("|fail|health_failed|Ubuntu|", "|warn|crc|Ubuntu|")   # #1732: orange is written through the window


@native_gui
def test_local_clone_a_warned_target_only_through_the_window(driver, tmp_path):
    out = drive(driver, tmp_path, ["mode:cloner"], LC_ORANGE, "hit:ROLE1.2")
    assert "sdb:0," in states(out)[-1] and "sdlg=4" in states(out)[-1], out   # still "דלג", SDLG_LC_TARGET
    skip = drive(driver, tmp_path, ["mode:cloner"], LC_ORANGE, "hit:ROLE1.2", "key:ENTER")
    assert "sdb:0," in states(skip)[-1] and "sdlg=0" in states(skip)[-1], skip
    go = drive(driver, tmp_path, ["mode:cloner"], LC_ORANGE, "hit:ROLE1.2", "hit:SDLG_GO")
    assert "sdb:2," in states(go)[-1], go
    clean = drive(driver, tmp_path, ["mode:cloner"], LC_ORANGE, "hit:ROLE0.0", "hit:ROLE0.2")   # sda: skip, target again
    assert "sda:2," in states(clean)[-1] and "sdlg=0" in states(clean)[-1], clean


@native_gui
def test_local_clone_a_warned_source_passes_the_window_once(driver, tmp_path):
    warned_src = LC.replace("|480103981056|ok|ok|Windows|4|", "|480103981056|fail|pending_sectors|Windows|4|")
    out = drive(driver, tmp_path, ["mode:cloner"], warned_src, "hit:LC_NEXT")
    assert "lcconfirm=0" in states(out)[-1] and "sdlg=5" in states(out)[-1], out    # SDLG_LC_SOURCE
    skip = drive(driver, tmp_path, ["mode:cloner"], warned_src, "hit:LC_NEXT", "key:ESC")
    assert "lcconfirm=0" in states(skip)[-1] and "sdlg=0" in states(skip)[-1], skip
    go = drive(driver, tmp_path, ["mode:cloner"], warned_src, "hit:LC_NEXT", "key:TAB", "key:ENTER", "hit:LC_GO")
    assert any("lcconfirm=1" in s for s in states(go)), go
    assert any(r.startswith("lc-start|sdc|sda|") for r in records(go)), go
    clean = drive(driver, tmp_path, ["mode:cloner"], LC, "hit:LC_NEXT")
    assert "lcconfirm=1" in states(clean)[-1] and "sdlg=0" in states(clean)[-1], clean


@native_gui
def test_local_clone_a_warned_old_source_is_not_swapped_into_a_target(driver, tmp_path):
    warned_src = LC.replace("|480103981056|ok|ok|Windows|4|", "|480103981056|fail|pending_sectors|Windows|4|")
    out = drive(driver, tmp_path, ["mode:cloner"], warned_src, "char:1")     # sda becomes the source
    assert "sda:1," in states(out)[-1] and "sdc:0," in states(out)[-1], out   # the old source: "דלג", not a target


# --- #1732: דיסק אדום (SMART נכשל) לעולם אינו יעד -------------------------------


@native_gui
def test_1732_cloner_red_window_offers_no_write(driver, tmp_path):
    """הבקרה השלילית: על הקוד הישן Tab+Enter בחלון שלח smart-rescue, והלחיצה
    על הכפתור האדום מצאה אותו."""
    keys = drive(driver, tmp_path, ["mode:cloner"], CLONER, "key:ENTER", "key:TAB", "key:ENTER")
    assert records(keys) == ["smart-skip|7-2"], keys
    click = drive(driver, tmp_path, ["mode:cloner"], CLONER, "hit:SMART_RESCUE", "hit:SDLG_GO")
    assert "@ nohit SDLG_GO" in click and records(click) == [], click


@native_gui
def test_1732_restore_onto_a_red_disk_never_starts(driver, tmp_path):
    keys = drive(driver, tmp_path, RESTORE, WARNED, "key:ENTER", "key:ENTER", "key:TAB", "key:ENTER")
    assert _starts(keys, "restore-start") == 0, keys
    click = drive(driver, tmp_path, RESTORE, WARNED, "hit:RESTORE_START", "hit:CONFIRM_OK", "hit:SDLG_GO")
    assert _starts(click, "restore-start") == 0 and "@ nohit SDLG_GO" in click, click


@native_gui
def test_1732_local_clone_a_red_drawer_is_never_a_target(driver, tmp_path):
    """sdb אדום: "יעד" אפור — אין לחיצה, אין חלון, והחצים מדלגים עליו."""
    out = drive(driver, tmp_path, ["mode:cloner"], LC, "hit:ROLE1.2")
    assert "@ nohit ROLE1.2" in out, out
    assert all("sdb:2," not in s for s in states(out)), out
    keys = drive(driver, tmp_path, ["mode:cloner"], LC, "key:UP", "key:RIGHT")   # sdb's bay: דלג -> יעד? (the old code opened the window)
    assert all("sdb:2," not in s for s in states(keys)) and all("sdlg=4" not in s for s in states(keys)), keys
    # a red disk may still be the SOURCE (reading from it is allowed)
    src = drive(driver, tmp_path, ["mode:cloner"], LC, "hit:ROLE1.1")   # ROLE<i>.<LCR_*>: 0 skip, 1 source, 2 target
    assert "sdb:1," in states(src)[-1], src


# --- חדר: W/N בלי סבב (נדב 16/10) ----------------------------------------------------

ROOM = "task=none\nroom_label=מחשבי שיכפול\n"
C_OFF, C_ON = "3c:52:82:a1:00:21", "3c:52:82:a1:00:22"
CLONERS = (f"machine=1|{C_OFF}|0|0|0||-1|0|\nmachine_drawers=0|2\n"
           f"machine=2|{C_ON}|1|0|1||-1|0|\nmachine_drawers=1|2\n"
           "room_drawer=1|1|S1|M|256060514304|passed|1|0||||ok\n")
ROUND = ROOM + CLONERS + "round=Office 2024|1|1|0|3|3|0|-1|-1|-1\n"


def cloners_sent(lines: list[str]) -> list[str]:
    return [l[len("cloner="):] for l in lines if l.startswith("cloner=")]


@native_gui
def test_room_n_sends_nothing_without_a_round_and_wake_with_no_pick_says_so(driver, tmp_path):
    """Nadav 17/10 (over 16/10): "הער" is no longer grey -- with nothing picked it
    takes the click and says so, and sends nothing; N still needs a round."""
    out = drive(driver, tmp_path, ["mode:room"], ROOM + CLONERS, "char:W", "char:w", "char:N", "char:n", "hit:ROOM_WAKE")
    assert records(out) == [], out
    assert "@ nohit ROOM_WAKE" not in out, out                         # the button takes the click now


@native_gui
def test_room_wake_click_sends_exactly_the_picked_cloners(driver, tmp_path):
    """Metal 17/10: the click sent no POST. Pick the OFF cloner by its name (it has
    no drawer yet), click "הער": one room-wake carrying only its MAC."""
    out = drive(driver, tmp_path, ["mode:room"], ROOM + CLONERS, "hit:MACHINE0", "hit:ROOM_WAKE")
    assert records(out) == ["room-wake"], out
    assert cloners_sent(out) == [C_OFF], out
    both = drive(driver, tmp_path, ["mode:room"], ROOM + CLONERS, "hit:MACHINE0", "hit:MACHINE1", "char:W")
    assert records(both) == ["room-wake"] and cloners_sent(both) == [f"{C_OFF},{C_ON}"], both


@native_gui
def test_room_w_and_n_work_with_a_round(driver, tmp_path):
    out = drive(driver, tmp_path, ["mode:room"], ROUND, "char:W", "char:N")
    assert records(out) == ["room-wake", "room-start"], out
    assert cloners_sent(out) == [f"{C_OFF},{C_ON}"], out              # a live round: its own cloners


# --- ציור: שלושת הכרטיסים בשתי ערכות הנושא ------------------------------------------


@native_gui
def test_the_window_is_drawn_with_the_red_button_in_both_themes(tmp_path):
    binary = _build_gui(tmp_path)
    env_ = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "card"), "--mac", "3C:52:82:A1:00:1F"],
                         capture_output=True, text=True, timeout=300, env=env_, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr                              # render_png's own routing check
    red = (0xE1, 0x22, 0x00)

    def reds(name):
        w, h, ch, px = _png_rgb(tmp_path / f"card-{name}-light.png")
        return sum(1 for i in range(0, w * h * ch, ch * 3) if all(abs(px[i + k] - red[k]) <= 30 for k in range(3)))

    # #1732: a write onto a red disk is refused -- the window is drawn (in red: its edge, its
    # stripe, the "!"), with no red button; reading from one (the capture, "pick") keeps
    # "קלוט ממנו בכל זאת". Testrunner 18/10: the plain restore card has more red than the refusal
    # over it (the window dims what is under it), so "more red than plain" is no measure there.
    for card, plain in (("cloner-smart", "cloner"), ("restore-smart", "restore")):
        assert reds(card) > 0, card
        assert (tmp_path / f"card-{card}-light.png").read_bytes() != (tmp_path / f"card-{plain}-light.png").read_bytes(), card
        assert (tmp_path / f"card-{card}-dark.png").exists()
    assert reds("pick-smart") > reds("pick") + 300
    assert reds("pick-smart") > reds("restore-smart"), "the capture keeps its red button, the restore has none"
