"""‏#1896 — סימון C: באימג' ישן ממסך מחשב הבנייה וממסך המשכפל (הכרעת נדב 22/10).

השרת מוסר ב-`/agent/state` את `unchecked_images` (אימג' ששתי מחיצות ה-windows שלו
שוות ואין סימון מהקליטה). המסך מציג אותו "לא נבדק", לחיצה פותחת בוחר C:
(`screens_syspart.c`), והבחירה הולכת לשרת:

* מחשב הבנייה — `syspart|<id>|<idx>` → נתיב הקונסולה עם הכניסה (`gui_syspart`);
* המשכפל — `lc-syspart|<id>|<idx>` → בקשה לסוכן, שמוסר אותה עם אסימון המסך
  (`lc_pull_syspart`), ורשימת האימג'ים נקראת שוב.

סירוב נאמר במסך במילים של השרת; אימג' "לא נבדק" לעולם אינו נבחר לשחזור.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _build_gui, _count_color, _pkgconfig, _png_rgb, _token, posix, sh
from test_cloner_pull_agent import DRAWERS, TRY, run

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

STATE = {"allowed_images": [{"id": "img_7f3a91", "name": "Office", "folder": "Office"}],
         "unchecked_images": [{"id": "img_5790e4", "name": "Vm | old", "folder": "ישן",
                               "candidates": [{"index": 1, "size_bytes": 223392075264, "used_bytes": 880222208},
                                              {"index": 2, "size_bytes": 223392075264, "used_bytes": None}]}]}

needs_jq = requires_native(("bash", BASH), ("jq", shutil.which("jq")))


@needs_jq
def test_the_records_carry_the_unchecked_image_and_its_candidates(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(STATE), encoding="utf-8")
    out = sh(f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/syspartgui.sh; "
             f"syspart_records {posix(tmp_path / 'state.json')!r}")
    assert out.stdout.splitlines() == ["image_unchecked=img_5790e4|Vm   old|ישן",
                                       "image_cand=img_5790e4|1|223392075264|880222208",
                                       "image_cand=img_5790e4|2|223392075264|"], out.stdout + out.stderr
    (tmp_path / "old.json").write_text(json.dumps({"allowed_images": []}), encoding="utf-8")
    old = sh(f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/syspartgui.sh; "
             f"syspart_records {posix(tmp_path / 'old.json')!r}; echo rc=$?")
    assert old.stdout.strip() == "rc=0"   # an older server: none, not a failure


# --- the build machine: the console route with the session ------------------------

def _build(tmp_path, code: str, answer: dict) -> tuple[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "run"; gui = run_dir / "gui"; gui.mkdir(parents=True)
    (tmp_path / "answer.json").write_text(json.dumps(answer), encoding="utf-8")
    out = sh(f"export RUN_DIR={posix(run_dir)!r} GUI_DIR={posix(gui)!r} LOG_FILE={posix(run_dir)!r}/log; "
             f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; . {posix(AGENT)}/lib/guibridge.sh; "
             f". {posix(AGENT)}/lib/syspartgui.sh; "
             f'console_post() {{ echo "$1 $(cat "$2")" > "$RUN_DIR/posted"; cp {posix(tmp_path / "answer.json")!r} "$3"; echo {code}; }}; '
             'gui_syspart "syspart|img_5790e4|2"; echo "rc=$?"; '
             'gui_syspart "syspart|../x|2"; echo "rc=$?"')
    return out.stdout, run_dir


@needs_jq
def test_the_build_machine_posts_the_choice_and_says_a_refusal(tmp_path):
    out, run_dir = _build(tmp_path / "ok", "200", {"ok": True, "index": 2})
    assert [l for l in out.splitlines() if l.startswith("rc=")] == ["rc=0", "rc=1"], out
    assert (run_dir / "posted").read_text().strip() == 'images/img_5790e4/system-partition {"index":2}'
    assert not (run_dir / "gui" / "error").exists()
    out, run_dir = _build(tmp_path / "no", "400", {"detail": "מחיצה 2 אינה אחת המחיצות לבחירה (1, 3)"})
    assert [l for l in out.splitlines() if l.startswith("rc=")][0] == "rc=1"
    assert "form_error=מחיצה 2 אינה אחת המחיצות לבחירה (1, 3)" in (run_dir / "gui" / "error").read_text()


# --- the cloner: the kiosk files it, the agent posts it with the screen nonce ------------

def test_the_cloner_screen_hands_a_mark_to_the_agent_only_on_the_pull_screen(tmp_path):
    out = run(tmp_path, DRAWERS + TRY + 'mkdir -p "$LC"; echo boot > "$LC/screen"; try "lc-syspart|img_5790e4|2"; '
              'echo pull > "$LC/screen"; try "lc-syspart|img_5790e4|2"; cat "$LC/syspart.req"; '
              'rm -f "$LC/syspart.req"; try "lc-syspart|img;x|2"; try "lc-syspart|img_5790e4|0"; try "lc-syspart|img_5790e4|"; '
              'test -f "$LC/syspart.req" && echo WRITTEN || echo NONE')
    assert out.stdout.splitlines() == [
        "refused lc-syspart|img_5790e4|2", "ok lc-syspart|img_5790e4|2", "img_5790e4|2",
        "refused lc-syspart|img;x|2", "refused lc-syspart|img_5790e4|0", "refused lc-syspart|img_5790e4|", "NONE"], out.stdout


def _agent_mark(tmp_path, code: str, answer: dict, nonce: bool = True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "answer.json").write_text(json.dumps(answer), encoding="utf-8")
    return run(tmp_path, 'mkdir -p "$LC"; ' + ('echo abc123 > "$LC/pull.nonce"; ' if nonce else '')
               + 'echo "img_5790e4|2" > "$LC/syspart.req"; '
               f'lc_pull_post() {{ echo "$1 $(cat "$2")" > "$LC/posted"; cp {posix(tmp_path / "answer.json")!r} "$3"; echo {code}; }}; '
               'lc_pull_images() { echo REREAD > "$LC/reread"; }; '
               'lc_pull_syspart; echo "rc=$?"; cat "$LC/posted" 2>/dev/null; cat "$LC/pull.error" 2>/dev/null; cat "$LC/reread" 2>/dev/null; '
               'test -f "$LC/syspart.req" && echo LEFT || echo TAKEN')


@needs_jq
def test_the_agent_posts_with_the_screen_nonce_and_reads_the_images_again(tmp_path):
    out = _agent_mark(tmp_path / "ok", "200", {"ok": True, "index": 2})
    lines = [l for l in out.stdout.splitlines() if not l.startswith("imagectl:")]   # log() also writes to stdout
    assert lines[0] == "rc=0" and "REREAD" in lines and lines[-1] == "TAKEN", out.stdout + out.stderr
    path, body = lines[1].split(" ", 1)
    assert path == "cloner-round/system-partition"
    assert json.loads(body) == {"mac": "aa:bb:cc:dd:ee:ff", "nonce": "abc123", "image_id": "img_5790e4", "index": 2}


@needs_jq
def test_a_refusal_is_said_on_the_screen_and_no_nonce_sends_nothing(tmp_path):
    out = _agent_mark(tmp_path / "no", "409", {"ok": False, "error": "מחיצת המערכת באימג' הזה כבר סומנה — שינוי מהקונסולה"})
    assert "מחיצת המערכת באימג' הזה כבר סומנה — שינוי מהקונסולה" in out.stdout and "REREAD" in out.stdout, out.stdout
    out = _agent_mark(tmp_path / "nn", "200", {"ok": True}, nonce=False)
    assert "rc=1" in out.stdout.splitlines() and "הסימון לא נשלח" in out.stdout, out.stdout
    assert "cloner-round" not in out.stdout, "nothing posted without the screen's nonce"


# --- the native picker (Testrunner: cc + pango/cairo) ---------------------------------

GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד")

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static void draw(App *a) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_destroy(s);
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = atoi(argv[2]); a.signed_in = 1;
    FILE *f = fopen(argv[1], "r"); state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    a.mode = MODE_RESTORE; app_route(&a); draw(&a);
    printf("@ n=%d unchecked=%d,%d ncand=%d\n", a.st.nimages, a.st.images[0].unchecked, a.st.nimages > 1 ? a.st.images[1].unchecked : -1,
           a.st.nimages > 1 ? a.st.images[1].ncand : -1);
    for (int i = 3; i < argc; i++) {
        Event e; memset(&e, 0, sizeof e); const char *v = argv[i];
        if (!strncmp(v, "pick:", 5)) { restore_pick(&a, atoi(v + 5) - 1); }
        else if (!strncmp(v, "char:", 5)) { e.type = UIEV_CHAR; e.ch = v[5]; syspart_modal(&a, &e); }
        else if (!strcmp(v, "esc")) { e.type = UIEV_KEY; e.key = KEYSYM_ESC; syspart_modal(&a, &e); }
        else if (!strcmp(v, "ready")) { printf("@ ready=%d err=%s\n", lc_pull_ready(&a), a.st.form_error); continue; }
        draw(&a);
        printf("@ open=%d sel=%d\n", a.sysp_open, a.restore_image_sel); fflush(stdout);
    }
    return 0;
}
'''

PULL = ("lc_screen=pull\nmachine_name=CLONER-02\n"
        "image=img_7f3a91|Office 2024|Office\n"
        "image_unchecked=img_5790e4|Vm|Office\n"
        "image_cand=img_5790e4|1|223392075264|880222208\n"
        "image_cand=img_5790e4|2|223392075264|203075014656\n"
        "cdisk=1|sdc|Crucial MX500|480103981056|passed||6.0|6.0|\n")


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("syspdrv")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    assert "src/screens_syspart.c" in srcs, srcs
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def drive(driver: Path, tmp_path: Path, cloner: int, *events: str) -> list[str]:
    path = tmp_path / "state.txt"
    path.write_text(PULL if cloner else PULL.replace("lc_screen=pull\n", ""), encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    out = subprocess.run([str(driver), posix(path), str(cloner), *events], capture_output=True, text=True,
                         encoding="utf-8", timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    return [l for l in out.stdout.splitlines() if l]


@GUI_BUILD
def test_an_unchecked_image_opens_the_picker_and_is_never_selected(driver, tmp_path):
    out = drive(driver, tmp_path, 1, "pick:2", "ready", "char:2", "pick:1")
    assert out[0] == "@ n=2 unchecked=0,1 ncand=2", out
    assert out[1] == "@ open=2 sel=0", out                    # the picker, not the restore choice
    assert out[2].startswith("@ ready=0"), out                # still nothing to restore
    assert "lc-syspart|img_5790e4|2" in out, out              # 2 = the second candidate
    assert out[out.index("lc-syspart|img_5790e4|2") + 1] == "@ open=0 sel=0", out
    assert out[-1] == "@ open=0 sel=1", out                   # a checked image is chosen as before


@GUI_BUILD
def test_the_build_machine_sends_its_own_token_and_esc_sends_nothing(driver, tmp_path):
    out = drive(driver, tmp_path, 0, "pick:2", "char:1")
    assert "syspart|img_5790e4|1" in out and not any(l.startswith("lc-") for l in out), out
    out = drive(driver, tmp_path, 0, "pick:2", "esc", "char:1")
    assert not any("syspart|" in l for l in out), out
    assert out[-1] == "@ open=0 sel=0", out


@GUI_BUILD
def test_the_picker_is_drawn_over_the_restore_screen(tmp_path):
    binary = _build_gui(tmp_path)
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    out = subprocess.run([str(binary), "--png", posix(tmp_path / "p"), "--size", "1280x800"],
                         capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    _w, _h, ch, plain = _png_rgb(tmp_path / "p-restore-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "p-restore-syspart-light.png")
    warn = _token("--clr-warning")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    assert (tmp_path / "p-restore-syspart-dark.png").exists()


@needs_jq
def test_the_cloner_pull_list_carries_the_unchecked_image(tmp_path):
    """the real lc_pull_images: the /agent/state answer becomes the screen's records."""
    (tmp_path / "state.json").write_text(json.dumps(STATE), encoding="utf-8")
    out = run(tmp_path, 'mkdir -p "$LC"; '
              f'http_get() {{ cat {posix(tmp_path / "state.json")!r}; }}; '
              'lc_pull_images; echo "rc=$?"; cat "$LC/pull.images"')
    lines = out.stdout.splitlines()
    assert "rc=0" in lines, out.stdout + out.stderr
    assert "image=img_7f3a91|Office|Office" in lines
    assert "image_unchecked=img_5790e4|Vm   old|ישן" in lines
    assert "image_cand=img_5790e4|2|223392075264|" in lines


@needs_jq
def test_the_build_restore_state_carries_the_unchecked_image(tmp_path):
    """the real gui_state in restore mode: image= and the #1896 records, from the same /state."""
    st = dict(STATE, known=True, disks=[], task=None, name="BUILD-01")
    (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
    run_dir = tmp_path / "run"; gui = run_dir / "gui"; gui.mkdir(parents=True)
    (gui / "mode").write_text("restore\n", newline="\n")
    out = sh(f"export RUN_DIR={posix(run_dir)!r} GUI_DIR={posix(gui)!r} LOG_FILE={posix(run_dir)!r}/log; "
             + "".join(f". {posix(AGENT)}/lib/{n}.sh; " for n in ("common", "jsonq", "guistate", "askgui", "syspartgui"))
             + f'http_get() {{ cat {posix(tmp_path / "state.json")!r}; }}; '
             'disk_slot_records() { :; }; class_deploy_on() { return 1; }; tools_on() { return 1; }; '
             'gui_disk_smart() { :; }; '
             'gui_state; echo "rc=$?"')
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    assert "image=img_7f3a91|Office|Office" in lines
    assert "image_unchecked=img_5790e4|Vm   old|ישן" in lines
    assert "image_cand=img_5790e4|1|223392075264|880222208" in lines
