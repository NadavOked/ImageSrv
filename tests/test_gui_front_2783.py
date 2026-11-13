"""#2783 / #2743 (ברזל 08-09/11, HP-BUILD-2): קליטה שנייה אחרי "הקליטה נכשלה".

מה נצפה: הקליטה הראשונה נכשלה, המכונה עמדה במסך הכישלון, והקונסולה הזמינה
קליטה חדשה. במוניטור: חלון "הקליטה תכווץ מחיצות" **מעל** "הקליטה נכשלה",
ולחיצה על "כווץ וקלוט" או Enter לא עשו דבר. על המסך הפיזי: הבאנר באנגלית של
`do_task` ("Capturing ... Reading used blocks only").

שלושה שורשים שנמדדו/נקראו, וטסט לכל אחד:

1. **fail.state נשאר אחרי שה-hold שחרר** (`hold.sh hold_new_task`, #1575): הקיוסק
   ממשיך לצייר את מסך הכישלון (`gui_hello_state` מצרף את fail.state), ושאלת
   הכיווץ של הקליטה החדשה מצוירת מעליו.
2. **מסך הכישלון לוקח את הקלט לפני השאלה שמעליו** (`main.c handle`): נמדד על
   ה-Testrunner בגואי האמיתי (uinput, 09/11) -- לחיצה על "כווץ וקלוט" ו-'1' לא
   הוציאו `shrink-continue`, ו-Enter השאיר `fail.action=retry` שאף hold לא קורא.
3. **הבאנר של `do_task` נכתב ל-tty גם כשגואי שלנו חי** -- הכרעת נדב 17/10: אין
   מסך טקסט כשמסך שלנו פתוח (כמו `log()`).

בקרה שלילית: כל טסט נבדק נופל על main שלפני התיקון (ראו גוף ה-PR).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_agent import BASH, posix, sh
from test_shrink_gui import GUI_TOOLS

pytestmark = requires_native(("bash", BASH), "jq")

REPO = Path(__file__).resolve().parent.parent
GUI_SRC_DIR = REPO / "native-gui" / "src"


# --- 1. ה-hold שמשחרר מסיים את מסך הכישלון ------------------------------------------


def _task(tid: str) -> dict:
    return {"schema": 1, "known": True, "role": "build",
            "task": {"id": tid, "type": "capture", "disk": "sda", "token": "t" * 48}}


def _yielding_hold(tmp_path: Path, answers: dict[str, dict]) -> tuple[str, Path, Path]:
    """`hold_watch` האמיתי אחרי קליטה שנכשלה (HOLD_YIELD_TASK), עם מסך כישלון
    שכתוב ב-fail.state ולחיצה שהגואי השאיר ב-fail.action לפני שה-hold קרא אותה."""
    run, ans, gui = tmp_path / "run", tmp_path / "ans", tmp_path / "run" / "gui"
    gui.mkdir(parents=True); ans.mkdir()
    for name, body in answers.items():
        (ans / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    (run / "fail.state").write_text("fail=bad|1|הקליטה נכשלה|קליטת אימג׳ חדש|build\n", encoding="utf-8")
    libs = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in ("common", "jsonq", "hold", "attended"))
    out = sh(
        f"export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} ANS={posix(ans)!r} HOLD_BEAT_S=0; {libs} "
        "build_hello() { echo '{\"schema\":1}'; }; "
        # the GUI's press lands between two beats -- after the hold's own look at it
        'http_post_json() { n=$(cat "$ANS/n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$ANS/n"; '
        '  [ "$n" -gt 6 ] && exit 7; [ "$n" = 2 ] && echo retry > "$GUI_DIR/fail.action"; '
        '  f="$ANS/$n.json"; [ -f "$f" ] || f="$ANS/default.json"; cat "$f"; }; '
        'hold_action() { return 1; }; '   # the press is not taken in the hold: the yield is what is tested
        '( HOLD_PROMPT="FAILED: capture did not complete"; HOLD_YIELD_TASK=tsk_5845; '
        'hold_watch hold_beat; echo "returned beats=$(cat "$ANS/n")" ); echo "sub=$?"'
    )
    return out, run, gui


def test_the_yield_ends_the_failure_screen_and_drops_its_unread_press(tmp_path):
    """הליבה של #2743: אחרי שהשרת נתן משימה חדשה, מסך הכישלון אינו נשאר מתחת
    לשאלה הבאה, ולחיצה שלו (fail.action) אינה מחכה ל-hold הבא כדי לאתחל."""
    out, run, gui = _yielding_hold(tmp_path, {"1": _task("tsk_5845"), "2": _task("tsk_5845"),
                                              "3": _task("tsk_d1f1"), "default": _task("tsk_d1f1")})
    assert "returned beats=3" in out, out
    assert not (run / "fail.state").exists(), "מסך הכישלון נשאר בקיוסק אחרי שה-hold שחרר"
    assert not (gui / "fail.action").exists(), "לחיצה ממסך הכישלון נשארה לקליטה הבאה"


def test_a_hold_that_keeps_holding_keeps_its_screen(tmp_path):
    """הצד השני: אותה משימה שנכשלה -- המסך נשאר (רק שחרור מוחק אותו)."""
    out, run, _gui = _yielding_hold(tmp_path, {"default": _task("tsk_5845")})
    assert "sub=7" in out and "returned" not in out, out
    assert (run / "fail.state").exists(), "מסך הכישלון נמחק בלי שה-hold שחרר"


# --- 3. אין באנר טקסט מתחת לגואי חי --------------------------------------------------


def _do_task(tmp_path: Path, gui_alive: bool) -> str:
    """`do_task` האמיתי עד הכישלון (הקליטה עצמה מזויפת על גבול המערכת)."""
    run = tmp_path / "run"; (run / "targets").mkdir(parents=True)
    resp = run / "response.json"
    resp.write_text(json.dumps({"task": {"id": "tsk_d1f1", "type": "capture", "disk": "sda",
                                         "name": "HP-BUILD-2-r17b-1109", "token": "t" * 48}}),
                    encoding="utf-8")
    keeper = None
    if gui_alive:
        keeper = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
        (run / "gui.pid").write_text(f"{keeper.pid}\n", encoding="utf-8")
    libs = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in ("common", "jsonq", "sysinfo", "progress", "failmark", "ui", "task"))   # #2272: progress calls sysinfo and failmark
    try:
        return sh(
            f"export RUN_DIR={posix(run)!r} RESP={posix(resp)!r} IMAGESRV_TEST=1 MAC=aa:bb:cc:dd:ee:ff SERVER=http://x; {libs} "
            'progress_loop() { :; }; gui_parent() { return 0; }; '
            'capture_disk() { return 1; }; report_final() { return 0; }; '
            'ui_error_hold() { echo "HOLD $1" >> "$RUN_DIR/trace"; }; '
            "do_task; echo \"rc=$?\""
        )
    finally:
        if keeper:
            keeper.kill(); keeper.wait()


def test_the_capture_banner_stays_off_a_live_gui(tmp_path):
    out = _do_task(tmp_path, gui_alive=True)
    assert "rc=1" in out, out
    assert "Capturing" not in out and "Reading used blocks" not in out, out


def test_without_a_gui_the_banner_is_still_the_screen(tmp_path):
    """לא "שקט תמיד": בלי מסך שלנו (לפני שהגואי עלה) השורות נשארות."""
    out = _do_task(tmp_path, gui_alive=False)
    assert "Capturing HP-BUILD-2-r17b-1109" in out and "Reading used blocks only" in out, out


def test_the_hold_text_stays_off_a_live_gui(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    keeper = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
    (run / "gui.pid").write_text(f"{keeper.pid}\n", encoding="utf-8")
    try:
        out = sh(f"export RUN_DIR={posix(run)!r} IMAGESRV_TEST=1; . {posix(flat_lib())}/common.sh; "
                 f". {posix(flat_lib())}/ui.sh; ui_error_hold 'capture did not complete' :")
    finally:
        keeper.kill(); keeper.wait()
    assert "TEST-HOLD" in out, out
    assert "FAILED:" not in out and "contact IT" not in out, out


# --- 2. שאלה שמצוירת מעל מסך הכישלון לוקחת את הקלט ---------------------------------

#: handle() האמיתי של main.c: מצב מקובץ, ציור אחד (המסך ומפת הלחיצות), ואירוע אחד.
DRIVER = r'''
#define main imagesrv_gui_main
#include "%(main_c)s"
#undef main
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    App a; memset(&a, 0, sizeof a);
    a.theme = &THEME_LIGHT; a.focus = HIT_USER; a.menu_focus = -1; a.chosen_disk = -1;
    a.st.pct = -1; a.st.hello_age = -1; a.st.hello_rc = -1;
    a.signed_in = 1; a.admin = 1; snprintf(a.signed_user, sizeof a.signed_user, "admin");
    StateFile sf; memset(&sf, 0, sizeof sf); snprintf(sf.path, sizeof sf.path, "%%s", argv[1]);
    if (state_poll(&sf, &a.st) < 0) return 3;
    snprintf(a.tools_dir, sizeof a.tools_dir, "%%s", argv[1]);
    char *slash = strrchr(a.tools_dir, '/'); if (slash) *slash = 0;
    after_reload(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    Event e; memset(&e, 0, sizeof e);
    if (!strcmp(argv[2], "1")) { e.type = UIEV_CHAR; e.ch = e.he = '1'; }
    else if (!strcmp(argv[2], "enter")) { e.type = UIEV_KEY; e.key = KEYSYM_ENTER; }
    else {
        int f = -1;
        for (int i = 0; i < a.nhits; i++) if (a.hits[i].id == HIT_SHRINK_CONTINUE) f = i;
        if (f < 0) { printf("nohit\n"); return 4; }
        double sc = a.scale > 0 ? a.scale : 1;
        e.type = UIEV_CLICK; e.x = (a.hits[f].r.x + a.hits[f].r.w / 2) * sc; e.y = (a.hits[f].r.y + a.hits[f].r.h / 2) * sc;
    }
    printf("screen=%%d failure=%%d\n", a.screen, a.screen == SCREEN_FAILURE);
    handle(&a, &e, NULL, 0);
    fflush(stdout);
    return 0;
}
'''

QUESTION = ("task=running\ntask_name=HP-BUILD-2-r17b-1109\ntask_disk=/dev/sda\npct=-1\n"
            "shrink_prompt=7-0|1\nshrink_partition=3|499000000000|146000000000\n")
FAILURE = ("fail=bad|1|הקליטה נכשלה|קליטת אימג׳ חדש|build\nfail_why=x\n"
           "fail_btn=retry|נסה שוב\nfail_btn=off|כבה את המחשב\n")


@pytest.fixture(scope="module")
def handle_driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("handle-drv")
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


def _press(drv: Path, tmp_path: Path, state: str, action: str) -> tuple[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").write_text(state, encoding="utf-8")
    env = dict(os.environ)
    if (REPO / "native-gui" / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(REPO / "native-gui" / "fonts.conf")
    r = subprocess.run([str(drv), str(tmp_path / "state"), action], capture_output=True, text=True,
                       timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout, tmp_path / "fail.action"


@GUI_TOOLS
@pytest.mark.parametrize("action", ["click", "1"])
def test_the_shrink_answer_over_the_failure_screen_reaches_the_question(handle_driver, tmp_path, action):
    out, fail_action = _press(handle_driver, tmp_path / action, QUESTION + FAILURE, action)
    assert "failure=1" in out, out                                  # the screen of 09/11: failure, dialog over it
    assert "shrink-continue|7-0" in out.splitlines(), out
    assert not fail_action.exists(), fail_action.read_text()


@GUI_TOOLS
def test_enter_under_the_question_is_not_a_failure_button(handle_driver, tmp_path):
    """Enter אינו עונה לשאלת הכיווץ ("כווץ" אינו ברירת מחדל, #928) -- וגם אינו
    "נסה שוב" של המסך שמתחת, שה-hold הבא היה מבצע (אתחול)."""
    out, fail_action = _press(handle_driver, tmp_path, QUESTION + FAILURE, "enter")
    assert "failure=1" in out, out
    assert not fail_action.exists(), "Enter מתחת לשאלה הפך ל-fail.action"


@GUI_TOOLS
def test_the_failure_screen_alone_keeps_its_buttons(handle_driver, tmp_path):
    """בלי שאלה מעליו, Enter הוא עדיין הכפתור הראשון (#2068) -- התיקון לא לקח לו אותו."""
    _out, fail_action = _press(handle_driver, tmp_path, "task=failed\n" + FAILURE, "enter")
    assert fail_action.exists() and fail_action.read_text().strip() == "retry"
