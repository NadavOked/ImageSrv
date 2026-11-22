"""#649 — ארגז הכלים בגואי הנייטיבי לפי מוקאפ 14 המאושר (נדב 31/10), נבדק על הבינארי.

שני סוגי ראיה, שניהם על קוד אמיתי (מעבדה בלבד — אין מהדר בווינדוס):

* **`handle()` האמיתי של main.c** על מצב מקובץ, כמו `test_gui_lc_badsect_2911.py`:
  ציור אחד (המסך ומפת הלחיצות), אירוע, ציור — ומה שהמסך הפך להיות ומה שנשלח לגשר.
  כך נבדקים הכללים של הכניסה (נדב 24/10 + מוקאפ 14 עמוד 1): "כלים F3" בשלושה
  מקומות — משכפל ממתין, מחשב בנייה אחרי הכניסה, דיסק-און-קי — כבוי בזמן כתיבה,
  לא בתחנת תלמיד ולא לפני כניסה, ו-F3/Esc בתוך הארגז חוזרים למסך שממנו נכנסו.
* **`--png` עם `IMAGESRV_GUI_TEXTLOG`**: מה שכל כרטיס חדש *אומר* (הקטגוריות, הספירות,
  המצב הריק של #3032, הפסק של rc 2, שורת היעד).

החוזה מול הסוכן (`$GUI_DIR/tools`, `tool-parts`, `tool-run|<id>|<arg>|<confirm>`) — כפי
שהמתאם נתן אותו לשני הצדדים.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_shrink_gui import GUI_TOOLS

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui"
GUI_SRC_DIR = GUI / "src"

#: handle() האמיתי: argv[1] = קובץ המצב, argv[2] = מחובר (0/1), argv[3..] = פעולות.
#: אחרי כל פעולה: ציור מחדש ושורת "@" עם המסך והארגז. מה שהגואי שולח לגשר — באותו stdout.
DRIVER = r'''
#define main imagesrv_gui_main
#include "%(main_c)s"
#undef main
#ifdef TOOLBOX_649
#define TQ(a) ((a)->tool_q)
#else
#define TQ(a) ""
#endif
static void draw(App *a) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_destroy(s);
}
static int has_hit(const App *a, int id) { for (int i = 0; i < a->nhits; i++) if (a->hits[i].id == id) return 1; return 0; }
static void report(const App *a) {
    printf("@ tools=%%d menu=%%d standby=%%d cloner=%%d local=%%d login=%%d class=%%d view=%%d confirm=%%d logout=%%d btn=%%d q=[%%s] sel=%%d\n",
           a->screen == SCREEN_TOOLS, a->screen == SCREEN_MENU, a->screen == SCREEN_STANDBY, a->screen == SCREEN_CLONER,
           a->screen == SCREEN_LOCAL, a->screen == SCREEN_LOGIN, a->screen == SCREEN_CLASS, (int)a->tools_view,
           a->confirm, a->logout_confirming, has_hit(a, HIT_TOOLS), TQ(a), a->tool_sel);
    fflush(stdout);
}
static int click(App *a, int id, Event *e) {
    for (int i = a->nhits - 1; i >= 0; i--) if (a->hits[i].id == id) {
        double sc = a->scale > 0 ? a->scale : 1;
        e->type = UIEV_CLICK; e->x = (a->hits[i].r.x + a->hits[i].r.w / 2) * sc; e->y = (a->hits[i].r.y + a->hits[i].r.h / 2) * sc;
        return 1;
    }
    return 0;
}
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App a; memset(&a, 0, sizeof a);
    a.theme = &THEME_LIGHT; a.focus = HIT_USER; a.menu_focus = -1; a.chosen_disk = -1;
    a.st.pct = -1; a.st.hello_age = -1; a.st.hello_rc = -1;
    a.tool_sel = -1; a.tool_disk_sel = -1; a.tool_rc = -1;
    snprintf(a.mac, sizeof a.mac, "3C:52:82:A1:00:1F");   /* without it after_reload says "חסר מזהה מכונה" */
    if (atoi(argv[2])) { a.signed_in = 1; a.admin = 1; snprintf(a.signed_user, sizeof a.signed_user, "admin"); }
    StateFile sf; memset(&sf, 0, sizeof sf); snprintf(sf.path, sizeof sf.path, "%%s", argv[1]);
    if (state_poll(&sf, &a.st) < 0) return 3;
    snprintf(a.tools_dir, sizeof a.tools_dir, "%%s", argv[1]);
    char *slash = strrchr(a.tools_dir, '/'); if (slash) *slash = 0;
    after_reload(&a); tools_poll(&a);
    draw(&a); report(&a);
    for (int k = 3; k < argc; k++) {
        const char *act = argv[k];
        Event e; memset(&e, 0, sizeof e); e.type = UIEV_KEY;
        if (!strcmp(act, "f3")) e.key = KEYSYM_F3;
        else if (!strcmp(act, "f4")) e.key = KEYSYM_F4;
        else if (!strcmp(act, "esc")) e.key = KEYSYM_ESC;
        else if (!strcmp(act, "enter")) e.key = KEYSYM_ENTER;
        else if (!strcmp(act, "up")) e.key = KEYSYM_UP;
        else if (!strcmp(act, "down")) e.key = KEYSYM_DOWN;
        else if (!strcmp(act, "left")) e.key = KEYSYM_LEFT;
        else if (!strcmp(act, "right")) e.key = KEYSYM_RIGHT;
#ifdef TOOLBOX_649
        else if (!strcmp(act, "pgdn")) e.key = KEYSYM_PGDN;
        else if (!strcmp(act, "pgup")) e.key = KEYSYM_PGUP;
#endif
        else if (!strcmp(act, "bs")) e.key = KEYSYM_BACKSPACE;
        else if (!strcmp(act, "tab")) e.key = KEYSYM_TAB;
        else if (!strncmp(act, "c:", 2)) { e.type = UIEV_CHAR; e.ch = e.he = (unsigned char)act[2]; }
        else if (!strncmp(act, "state:", 6)) {   /* the agent rewrote the state file: the next one */
            StateFile s2; memset(&s2, 0, sizeof s2); snprintf(s2.path, sizeof s2.path, "%%s/%%s", a.tools_dir, act + 6);
            if (state_poll(&s2, &a.st) < 0) return 3;
            after_reload(&a); draw(&a); report(&a); continue;
        }
        else if (!strncmp(act, "result:", 7)) {   /* the bridge answers: id|rc|path|seq, the output in path */
            char p[600]; snprintf(p, sizeof p, "%%s/tool-result", a.tools_dir);
            FILE *f = fopen(p, "w"); if (!f) return 5; fprintf(f, "%%s\n", act + 7); fclose(f);
            tools_poll(&a); draw(&a); report(&a); continue;
        }
        else if (!strncmp(act, "click:", 6)) {
            const char *n = act + 6; int id = -1;
            if (!strcmp(n, "tools")) id = HIT_TOOLS;
            else if (!strcmp(n, "run")) id = HIT_TOOL_RUN;
            else if (!strcmp(n, "ok")) id = HIT_CONFIRM_OK;
            else if (!strcmp(n, "cancel")) id = HIT_CONFIRM_CANCEL;
            else if (!strncmp(n, "disk", 4)) id = HIT_TOOL_DISK_BASE + atoi(n + 4);
#ifdef TOOLBOX_649
            else if (!strcmp(n, "clear")) id = HIT_TOOL_CLEAR;
            else if (!strncmp(n, "tile", 4)) id = HIT_TOOL_CAT_BASE + atoi(n + 4);
            else if (!strncmp(n, "part", 4)) id = HIT_TOOL_PART_BASE + atoi(n + 4);
#endif
            if (id < 0 || !click(&a, id, &e)) { printf("nohit %%s\n", n); fflush(stdout); continue; }
        } else { printf("nokey %%s\n", act); fflush(stdout); continue; }
        handle(&a, &e, NULL, 0);
        draw(&a); report(&a);
    }
    return 0;
}
'''

#: $GUI_DIR/tools כפי שהחוזה מגדיר: machine= ואז id|domain|title|risk|args|what (השדה השישי רשות).
TOOLS = """machine=BUILD-01
smart-log|disk|לוג שגיאות SMART|ro||מציג שגיאות והיסטוריית בדיקה עצמית
badblocks|disk|סריקת בלוקים פגומים|ro|disk:על איזה דיסק?|קורא את כל הדיסק ומאתרת בלוקים רעים, בלי לכתוב.
fsck-esp|boot|תיקון מחיצת האתחול|rw|part:איזו מחיצה?|בודק ומתקן מחיצת FAT של האתחול
blkdiscard|wipe|מחיקת SSD מהירה|destroy|disk:איזה דיסק למחוק?
ping|net|פינג ומסלול|ro|כתובת או שם מחשב|בודק אם כתובת עונה
cpu-info|hw|פרטי מעבד|ro||יצרן, דגם, ליבות ופגיעויות
mystery|quantum|כלי בתחום לא מוכר|ro||בדיקה
"""
PARTS = "sda1|sda|104857600|vfat|EFI\nsda3|sda|200000000000|ntfs|Windows\n"
DISKS = ("disk=sda|Samsung SSD 870 EVO 500GB|500107862016|1|0\n"
         "disk=sdb|WDC WD5000AAKX|500107862016|1|0\n")

BUILD = "menu_tools=1\nmachine_name=BUILD-01\n" + DISKS
STANDBY = "screen=cloner\nmenu_tools=1\n"
WRITING = ("screen=cloner\nmenu_tools=1\ncloner=office365 v4\n"
           "drawer=0|sda|writing|1000000000|19500000000||||1|SATA 0\n")
USB = ("screen=cloner\nmenu_tools=1\nlc_screen=local\nlc_usb=1\nlc_inv=done\nlc_phase=idle\n"
       "lc_disk=1|sda|A|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
       "lc_disk=3|sdc|C|S3|480103981056|ok|ok|Windows|4|148600000000|480103981056|1|\n")
CLASS = "screen=class\nmenu_tools=1\n"


@pytest.fixture(scope="module")
def drv(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("tools-drv")
    (d / "drv.c").write_text(DRIVER % {"main_c": (GUI_SRC_DIR / "main.c").as_posix()}, encoding="utf-8")
    srcs = subprocess.run(["make", "-s", "-C", str(GUI), "print-gui-src"],
                          capture_output=True, text=True, timeout=60, check=True, stdin=subprocess.DEVNULL).stdout.split()
    others = [str(GUI / s) for s in srcs if s != "src/main.c"]
    pk = subprocess.run(["pkg-config", "--cflags", "--libs", "pangocairo", "cairo", "libdrm"],
                        capture_output=True, text=True, timeout=60, check=True, stdin=subprocess.DEVNULL).stdout.split()
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run([cc, "-O1", "-std=c11", "-D_GNU_SOURCE", f"-I{GUI_SRC_DIR}", "-o", str(d / "drv"),
                            str(d / "drv.c"), *others, *pk, "-lm"],
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr[-3000:]
    return d / "drv"


def _run(drv: Path, tmp_path: Path, state: str, *actions: str, signed: bool = False,
         tools: str | None = TOOLS, parts: str | None = PARTS, textlog: Path | None = None) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").write_text(state, encoding="utf-8", newline="\n")
    if tools is not None:
        (tmp_path / "tools").write_text(tools, encoding="utf-8", newline="\n")
    if parts is not None:
        (tmp_path / "tool-parts").write_text(parts, encoding="utf-8", newline="\n")
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    if textlog is not None:
        env["IMAGESRV_GUI_TEXTLOG"] = str(textlog)
    r = subprocess.run([str(drv), str(tmp_path / "state"), "1" if signed else "0", *actions],
                       capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.splitlines()


def _states(out: list[str]) -> list[dict[str, str]]:
    rows = []
    for line in out:
        if line.startswith("@ "):
            q = line[line.index("q=[") + 3:line.index("]", line.index("q=["))]
            rest = line[2:].replace(f"q=[{q}]", "")
            row = dict(kv.split("=", 1) for kv in rest.split())
            row["q"] = q
            rows.append(row)
    return rows


def _emitted(out: list[str]) -> list[str]:
    return [line for line in out if line and not line.startswith("@ ") and line != "touch"]


# --- הכניסה: שלושה מקומות, כבוי בזמן כתיבה ---------------------------------------------


@GUI_TOOLS
@pytest.mark.parametrize("name,state,signed,home", [
    ("standby", STANDBY, False, "standby"),   # משכפל ממתין לסבב
    ("build", BUILD, True, "menu"),           # מחשב בנייה אחרי הכניסה
    ("usb", USB, False, "local"),             # דיסק-און-קי, שיכפול מקומי
])
def test_f3_opens_the_toolbox_in_the_three_places_and_f3_or_esc_go_back(drv, tmp_path, name, state, signed, home):
    out = _run(drv, tmp_path / name, state, "f3", "f3", "f3", "esc", signed=signed)
    s = _states(out)
    assert s[0][home] == "1" and s[0]["btn"] == "1", s[0]               # the button is on the bar
    assert s[1]["tools"] == "1", s[1]                                    # F3 opens
    assert s[2][home] == "1" and s[2]["tools"] == "0", s[2]             # F3 inside leaves (31/10)
    assert s[3]["tools"] == "1", s[3]
    assert s[4][home] == "1" and s[4]["tools"] == "0", s[4]             # Esc on the home leaves too
    assert s[4]["logout"] == "0", s[4]                                   # not the menu's logout question
    assert _emitted(out).count("tool-list") == 2, out                    # every entry asks the bridge for the list


@GUI_TOOLS
def test_the_header_button_opens_it_too(drv, tmp_path):
    out = _run(drv, tmp_path, STANDBY, "click:tools", "click:tools")
    assert "nohit tools" not in out, out                                 # the bar has a live "כלים"
    s = _states(out)
    assert s[1]["tools"] == "1" and s[2]["standby"] == "1", s


@GUI_TOOLS
@pytest.mark.parametrize("name,state,signed,home", [
    ("writing", WRITING, False, "cloner"),    # בזמן כתיבה: כבוי, F3 לא עושה כלום
    ("login", BUILD, False, "login"),         # מחשב בנייה לפני הכניסה
    ("class", CLASS, True, "class"),          # תחנת תלמיד (גם עם מישהו מחובר)
])
def test_f3_does_nothing_while_writing_before_login_and_on_a_student_station(drv, tmp_path, name, state, signed, home):
    out = _run(drv, tmp_path / name, state, "f3", "click:tools", signed=signed)
    s = _states(out)
    assert s[0][home] == "1", s[0]
    assert all(r["tools"] == "0" for r in s), s
    assert s[0]["btn"] == "0", s[0]                                       # no live button to click
    assert "tool-list" not in _emitted(out), out


@GUI_TOOLS
def test_without_menu_tools_there_is_no_way_in(drv, tmp_path):
    for name, state, signed in (("standby", STANDBY, False), ("build", BUILD, True), ("usb", USB, False)):
        st = state.replace("menu_tools=1", "menu_tools=0")
        out = _run(drv, tmp_path / name, st, "f3", signed=signed)
        s = _states(out)
        assert s[0]["btn"] == "0" and s[1]["tools"] == "0", (name, s)
        assert "tool-list" not in _emitted(out), (name, out)


@GUI_TOOLS
def test_a_round_that_starts_while_the_toolbox_is_open_closes_it(drv, tmp_path):
    """The cloner joins a round while the IT person is in the toolbox: the write
    takes the screen, the toolbox does not hide it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "writing").write_text(WRITING, encoding="utf-8", newline="\n")
    out = _run(drv, tmp_path, STANDBY, "f3", "state:writing", "f3")
    s = _states(out)
    assert s[1]["tools"] == "1", s
    assert s[2]["cloner"] == "1" and s[2]["tools"] == "0", s      # the write has the screen
    assert s[3]["tools"] == "0", s                                 # and F3 is off during it


@GUI_TOOLS
def test_the_build_menu_has_no_tools_button_of_its_own_any_more(drv, tmp_path):
    """mockup 14 page 1: "כלים" is in the top bar only -- Tab walks the cards and
    "התנתק", and never lands on a second "כלים"."""
    # 4 cards (admin) + "התנתק" = 5 stops; the old sixth stop was "כלים" -- six Tabs land on it
    out = _run(drv, tmp_path, BUILD, *(["tab"] * 6), "enter", signed=True)
    assert "tool-list" not in _emitted(out), out


# --- המסך הראשי, קטגוריה, חיפוש ----------------------------------------------------------


@GUI_TOOLS
def test_keyboard_home_category_and_back(drv, tmp_path):
    # tiles in table order of the categories present: disk, wipe, net, boot, hw, other
    out = _run(drv, tmp_path, BUILD, "f3", "enter", "esc", "left", "enter", "esc", "esc", signed=True)
    s = _states(out)
    assert s[2]["view"] == "1", s[2]                       # Enter opens the focused category (TOOLS_CAT)
    assert s[3]["view"] == "0" and s[3]["tools"] == "1", s[3]   # Esc: back to the home, not out
    assert s[5]["view"] == "1", s[5]                       # Left = the next tile (RTL), opened
    assert s[7]["menu"] == "1", s[7]                       # Esc on the home leaves


@GUI_TOOLS
def test_typing_a_letter_opens_the_search_with_it_and_esc_clears_it(drv, tmp_path):
    out = _run(drv, tmp_path, BUILD, "f3", "c:N", "c:V", "bs", "esc", "c:/", "esc", "esc", signed=True)
    s = _states(out)
    assert s[2]["q"] == "N" and s[3]["q"] == "NV" and s[4]["q"] == "N", s
    assert s[5]["q"] == "" and s[5]["tools"] == "1", s[5]       # Esc clears the search, stays in
    assert s[6]["q"] == "" and s[6]["tools"] == "1", s[6]       # '/' opens it empty
    assert s[8]["menu"] == "1", s[8]


@GUI_TOOLS
def test_a_read_only_tool_without_input_runs_at_once(drv, tmp_path):
    # hw is the 5th tile (disk, wipe, net, boot, hw): four Lefts
    out = _run(drv, tmp_path, BUILD, "f3", "left", "left", "left", "left", "enter", "enter", signed=True)
    assert "tool-run|cpu-info||" in _emitted(out), out
    assert _states(out)[-1]["view"] == "3", out                 # TOOLS_OUTPUT


@GUI_TOOLS
def test_an_unknown_domain_is_shown_under_other_not_dropped(drv, tmp_path):
    out = _run(drv, tmp_path, BUILD, "f3", "click:tile8", "enter", signed=True)   # 8 = "other" in the table
    assert "tool-run|mystery||" in _emitted(out), out


@GUI_TOOLS
def test_a_destructive_tool_asks_in_the_red_dialog_and_sends_the_machine_name(drv, tmp_path):
    out = _run(drv, tmp_path, BUILD, "f3", "left", "enter", "enter", "click:disk1", "click:run", "click:ok", signed=True)
    s = _states(out)
    assert s[4]["view"] == "2", s[4]                            # the run view (pick + what happens)
    assert s[6]["confirm"] != "0", s[6]                         # "הרץ…" opens the #1270 dialog -- nothing sent yet
    assert not any(e.startswith("tool-run|") for e in _emitted(out)[:-1]), out
    assert _emitted(out)[-1] == "tool-run|blkdiscard|/dev/sdb|BUILD-01", out


@GUI_TOOLS
def test_the_dialog_cancel_sends_nothing(drv, tmp_path):
    out = _run(drv, tmp_path, BUILD, "f3", "left", "enter", "enter", "click:disk0", "click:run", "click:cancel", signed=True)
    assert not any(e.startswith("tool-run|") for e in _emitted(out)), out


@GUI_TOOLS
def test_a_partition_tool_sends_the_partition(drv, tmp_path):
    # boot is the 4th tile
    out = _run(drv, tmp_path, BUILD, "f3", "left", "left", "left", "enter", "enter", "click:part0", "click:run", "click:ok",
               signed=True)
    assert "tool-run|fsck-esp|/dev/sda1|BUILD-01" in _emitted(out), out


@GUI_TOOLS
def test_a_missing_partition_list_runs_nothing(drv, tmp_path):
    out = _run(drv, tmp_path, BUILD, "f3", "left", "left", "left", "enter", "enter", "click:part0", "click:run", "click:ok",
               signed=True, parts=None)
    assert not any(e.startswith("tool-run|") for e in _emitted(out)), out


@GUI_TOOLS
def test_the_result_comes_back_and_rc2_is_its_own_verdict(drv, tmp_path):
    (tmp_path / "out.txt").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "out.txt").write_text("לא הצלחנו לקרוא את המעבד\n", encoding="utf-8")
    out = _run(drv, tmp_path, BUILD, "f3", "left", "left", "left", "left", "enter", "enter",
               f"result:cpu-info|2|{(tmp_path / 'out.txt').as_posix()}|1", signed=True)
    assert _states(out)[-1]["view"] == "3", out


@GUI_TOOLS
def test_the_agents_save_line_is_drawn_as_the_save_box_not_as_raw_output(drv, tmp_path):
    """The two halves of #649 meet here. agent/lib/tools/tooldest.sh prints, as the first
    line of a tool that saves files, "יעד השמירה: <איפה> · <נתיב> · פנוי: N MB"
    (interfaces.md §23). The GUI draws that line as the save box of mockup 14 page 4 --
    where, the path, the free space -- and does not also print it as a row of raw output."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    out_txt = tmp_path / "out.txt"
    out_txt.write_text("יעד השמירה: בדיסק-און-קי שהמחשב עלה ממנו · /run/imagesrv/usbdata/recovered/scalpel/2026-11-12_0912"
                       " · פנוי: 20 MB\nנספרו ביעד: 3 קבצים\n", encoding="utf-8")
    log = tmp_path / "text.log"
    one = "machine=BUILD-01\ncpu-info|hw|פרטי מעבד|ro||יצרן, דגם, ליבות ופגיעויות\n"
    out = _run(drv, tmp_path, BUILD, "f3", "enter", "enter", f"result:cpu-info|0|{out_txt.as_posix()}|1",
               signed=True, tools=one, textlog=log)
    assert _states(out)[-1]["view"] == "3", out
    said = log.read_text(encoding="utf-8").splitlines()
    assert "נספרו ביעד: 3 קבצים" in said, said[-40:]                    # the output view was drawn
    assert not any(s.startswith("יעד השמירה: ") for s in said), said[-40:]   # not a row of raw output
    assert "נשמר בדיסק-און-קי שהמחשב עלה ממנו" in said, said[-40:]
    assert "/run/imagesrv/usbdata/recovered/scalpel/2026-11-12_0912" in said, said[-40:]
    assert any("פנוי" in s and "20 MB" in s for s in said), said[-40:]


@GUI_TOOLS
def test_on_the_usb_stick_the_toolbox_keeps_its_keys_from_the_local_clone(drv, tmp_path):
    """F4 ("הגדרות") belongs to the local clone screen; inside the toolbox it does
    not open the stick's settings under it, and Esc goes back to the source screen."""
    out = _run(drv, tmp_path, USB, "f3", "f4", "esc")
    s = _states(out)
    assert s[1]["tools"] == "1" and s[2]["tools"] == "1", s
    assert s[3]["local"] == "1", s
    keys = out[next(i for i, line in enumerate(out) if line.startswith("@ ")) + 1:]   # after the first draw (lc-measure is the load's)
    assert not any(e.startswith("lc-") for e in _emitted(keys)), out


# --- מה שהכרטיסים אומרים (--png + textlog) -------------------------------------------------


def _said(tmp_path: Path, card: str, state: str = "", tools: str | None = None, extra: dict | None = None,
          size: str = "1280x800") -> list[str]:
    from test_cloner_gui import _build_gui, posix
    binary = _build_gui(tmp_path)
    st = tmp_path / "state.txt"
    st.write_text(state + "message=x|y\n", encoding="utf-8", newline="\n")
    if tools is not None:
        (tmp_path / "tools").write_text(tools, encoding="utf-8", newline="\n")
    for name, text in (extra or {}).items():
        (tmp_path / name).write_text(text, encoding="utf-8", newline="\n")
    log = tmp_path / "text.log"
    env = dict(os.environ, IMAGESRV_GUI_TEXTLOG=str(log))
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "c"), "--size", size, "--mac", "3C:52:82:A1:00:1F",
                          "--ip", "10.10.10.31", "--state", posix(st)], capture_output=True, text=True, timeout=180,
                         env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr[-2000:]
    lines = log.read_text(encoding="utf-8").splitlines()
    if f"@@ {card}-light" not in lines:
        return []                                            # the card does not exist: nothing is said
    start = lines.index(f"@@ {card}-light")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("@@ ")), len(lines))
    return lines[start + 1:end]


@GUI_TOOLS
def test_the_home_counts_what_arrived_and_hides_empty_categories(tmp_path):
    said = _said(tmp_path, "tools", tools=TOOLS)
    text = "\n".join(said)
    for name in ("דיסק", "מחיקת דיסקים", "רשת", "אתחול", "חומרה", "אחר"):
        assert name in said, (name, text)
    for absent in ("Windows", "שחזור קבצים", "אבטחה", "בדיקות משולבות"):   # no tool arrived for them
        assert absent not in said, (absent, text)
    assert any("2 כלים" in s for s in said), text          # disk: two tools in the file
    assert any("1 הרסני" in s for s in said), text          # the wipe tile's mix


@GUI_TOOLS
def test_the_empty_search_says_why_and_offers_to_clear(tmp_path):
    said = _said(tmp_path, "tools-search-empty")
    text = "\n".join(said)
    assert any(s.startswith("אין כלי שמתאים ל") for s in said), text
    assert "נקה חיפוש" in said, text                        # #3032: an action -> a button


@GUI_TOOLS
def test_the_category_pages_by_eight(tmp_path):
    said = _said(tmp_path, "tools-cat")
    text = "\n".join(said)
    assert "1–8 מתוך 16" in said, text
    assert "עוד 8 למטה · PgDn" in said, text


@GUI_TOOLS
def test_rc2_is_not_checked_never_green_and_the_save_line_is_drawn(tmp_path):
    said = _said(tmp_path, "tools-output")
    text = "\n".join(said)
    assert any("לא הצלחנו לבדוק" in s for s in said), text
    assert not any(s.startswith("dest=") for s in said), text   # the record is drawn as the line, not printed
    assert any("בשרת" in s for s in said), text


@GUI_TOOLS
def test_a_missing_partition_file_is_said_not_shown_as_no_partitions(tmp_path):
    said = _said(tmp_path, "tools-confirm-part", tools=TOOLS)
    text = "\n".join(said)
    assert any(s.startswith("לא הצלחנו לקרוא את המחיצות") for s in said), text   # principle 5: orange, said
    assert "אין מחיצות" not in text, text


@GUI_TOOLS
def test_the_new_cards_route_and_render_at_960x540_too(tmp_path):
    said = _said(tmp_path, "tools", size="960x540")
    assert "כלים" in said and "דיסק" in said, said
