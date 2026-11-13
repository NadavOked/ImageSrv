"""#2911: שאלת "סקטורים פגומים" (#2017) מעל מסך השיכפול המקומי לוקחת את הקלט.

מה נמצא בקריאת קוד (11/11, לא שוחזר על מכונה): השאלה מצוירת מעל כל מסך
(`screens.c draw_ask_dialog`), אבל ב-`main.c handle` המסכים `SCREEN_LCBOOT` /
`SCREEN_LOCAL` הולכים ל-`lc_handle` וחוזרים **לפני** `ask_modal`. לחיצה על
"עצור", '2', Esc או Enter לא הוציאו `ask-<answer>|<nonce>`, הסוכן
(`lcraw.sh lc_rescue_ask`) לא קיבל תשובה, ואחרי `LC_RESCUE_ASK_S` ההעתקה
המשיכה עם אפסים במקום הסקטורים שלא נקראו: הבחירה של המפעיל אבדה.

צד הסוכן נבדק ואינו השורש: `gui_ask_dispatch` מקבל `badsect` על
`GUI_SCREEN=cloner`, וגם השיכפול המקומי וגם הדיסק-און-קי רצים כ-cloner
(`guiparent.sh`, `usbclone.sh`). זה נבדק ב-`test_lc_raw_2017.py`. הכרטיס
`--png cloner-badsect` מצויר על `SCREEN_CLONER` (`png_drawer_ask`), ולא על
המסך שבו השאלה באמת עולה, ולכן הוא לא תפס את זה.

הטסט מריץ את `handle()` האמיתי של main.c על מצב מקובץ: ציור אחד (המסך ומפת
הלחיצות) ואירוע אחד, כמו `test_gui_front_2783.py` (#2743, אותו באג במסך הכישלון).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_shrink_gui import GUI_TOOLS

REPO = Path(__file__).resolve().parent.parent
GUI_SRC_DIR = REPO / "native-gui" / "src"

#: handle() האמיתי. argv[2]: '1' / '2' / esc / enter / primary / secondary.
DRIVER = r'''
#define main imagesrv_gui_main
#include "%(main_c)s"
#undef main
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    App a; memset(&a, 0, sizeof a);
    a.theme = &THEME_LIGHT; a.focus = HIT_USER; a.menu_focus = -1; a.chosen_disk = -1;
    a.st.pct = -1; a.st.hello_age = -1; a.st.hello_rc = -1;
    StateFile sf; memset(&sf, 0, sizeof sf); snprintf(sf.path, sizeof sf.path, "%%s", argv[1]);
    if (state_poll(&sf, &a.st) < 0) return 3;
    snprintf(a.tools_dir, sizeof a.tools_dir, "%%s", argv[1]);
    char *slash = strrchr(a.tools_dir, '/'); if (slash) *slash = 0;
    after_reload(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    Event e; memset(&e, 0, sizeof e);
    const char *act = argv[2];
    if (!strcmp(act, "1") || !strcmp(act, "2")) { e.type = UIEV_CHAR; e.ch = e.he = act[0]; }
    else if (!strcmp(act, "esc")) { e.type = UIEV_KEY; e.key = KEYSYM_ESC; }
    else if (!strcmp(act, "enter")) { e.type = UIEV_KEY; e.key = KEYSYM_ENTER; }
    else {
        int want = !strcmp(act, "primary") ? HIT_ASK_PRIMARY : HIT_ASK_SECONDARY, f = -1;
        for (int i = 0; i < a.nhits; i++) if (a.hits[i].id == want) f = i;
        if (f < 0) { printf("nohit\n"); return 4; }
        double sc = a.scale > 0 ? a.scale : 1;
        e.type = UIEV_CLICK; e.x = (a.hits[f].r.x + a.hits[f].r.w / 2) * sc; e.y = (a.hits[f].r.y + a.hits[f].r.h / 2) * sc;
    }
    printf("screen=%%d local=%%d ask=%%d\n", a.screen, a.screen == SCREEN_LOCAL, a.st.ask_pending);
    handle(&a, &e, NULL, 0);
    printf("lcstop=%%d\n", a.lcu.stop);
    fflush(stdout);
    return 0;
}
'''

#: מסך השיכפול המקומי באמצע ההעתקה, כפי ש-lcgui.sh כותב אותו (screen=cloner: GUI_SCREEN).
COPYING = ("screen=cloner\nlc_screen=local\nlc_inv=done\nlc_phase=copying\n"
           "lc_disk=1|sda|A|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
           "lc_disk=3|sdc|C|S3|480103981056|ok|ok|Windows|4|148600000000|480103981056|1|\n")
#: השאלה של lcraw.sh: המקור sdc, מחיצה 3, ממשיכים לבד ב-epoch הזה (askgui.sh, gui_ask_state).
BADSECT = "ask_prompt=9-0|badsect|1|sdc\nask_row=3|3|4102444800\n"


@pytest.fixture(scope="module")
def handle_driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lc-ask-drv")
    (d / "drv.c").write_text(DRIVER % {"main_c": (GUI_SRC_DIR / "main.c").as_posix()}, encoding="utf-8")
    srcs = subprocess.run(["make", "-s", "-C", str(REPO / "native-gui"), "print-gui-src"],
                          capture_output=True, text=True, timeout=60, check=True, stdin=subprocess.DEVNULL).stdout.split()
    others = [str(REPO / "native-gui" / s) for s in srcs if s != "src/main.c"]
    pk = subprocess.run(["pkg-config", "--cflags", "--libs", "pangocairo", "cairo", "libdrm"],
                        capture_output=True, text=True, timeout=60, check=True, stdin=subprocess.DEVNULL).stdout.split()
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run([cc, "-O1", "-std=c11", "-D_GNU_SOURCE", f"-I{GUI_SRC_DIR}", "-o", str(d / "drv"),
                            str(d / "drv.c"), *others, *pk, "-lm"],
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr[-3000:]
    return d / "drv"


def _press(drv: Path, tmp_path: Path, state: str, action: str) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ)
    if (REPO / "native-gui" / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(REPO / "native-gui" / "fonts.conf")
    r = subprocess.run([str(drv), str(tmp_path / "state"), action], capture_output=True, text=True,
                       timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.splitlines()


@GUI_TOOLS
@pytest.mark.parametrize("action,answer", [
    ("secondary", "stop"),     # the red "עצור — השיכפול ייכשל" of the issue
    ("2", "stop"),
    ("esc", "stop"),           # Esc = the safe answer (main.c ask_modal)
    ("primary", "continue"),
    ("1", "continue"),
    ("enter", "continue"),     # #2017: Enter is "המשך", the default (Nadav 22/10)
])
def test_the_bad_sector_answer_on_the_local_clone_screen_is_sent(handle_driver, tmp_path, action, answer):
    out = _press(handle_driver, tmp_path / action, COPYING + BADSECT, action)
    assert "local=1 ask=1" in out[0], out                  # the screen of the issue: the copy, the question over it
    assert f"ask-{answer}|9-0" in out, out
    assert "lc-stop" not in out and "lcstop=0" in out, out  # the copy screen behind it did not take the key too


@GUI_TOOLS
def test_without_the_question_the_copy_screen_keeps_its_keys(handle_driver, tmp_path):
    """הצד השני: בלי שאלה, Esc באמצע ההעתקה עדיין פותח את "לעצור את השיכפול?" של
    המסך עצמו (lc_state.c) -- התיקון לא לקח לו את המקלדת."""
    out = _press(handle_driver, tmp_path, COPYING, "esc")
    assert "local=1 ask=0" in out[0], out
    assert "lcstop=1" in out and not any(line.startswith("ask-") for line in out), out
