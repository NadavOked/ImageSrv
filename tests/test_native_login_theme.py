"""#1499 (Nadav 16/10): the native GUI's theme -- light by default, the
signed-in user's saved theme after login, a switch in the corner of every
screen (click or F2), saved only while somebody is signed in.

Three layers, because the C compiles only on Linux (the lab / CI):

* the wiring, read from the source -- runs everywhere;
* `theme.c`'s two pure helpers, compiled with nothing but `cc` and driven
  with the answers the bridge prints (`test_gui_user_theme.py` is that side);
* the whole GUI rendered with `--png`: the switch is drawn in the header's
  corner on every card that has a header.

Negative control (on the code before #1499): `theme_saved` /
`theme_login_answer` do not exist (the compiled test fails to build), the
default is `&THEME_DARK`, nothing draws HIT_THEME (the header corner is the
bare header colour), and do_login never touches `a->theme`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import BASH
from test_cloner_gui import GUI, _build_gui, _pkgconfig, _png_rgb, posix

SRC = GUI / "src"
MAIN = (SRC / "main.c").read_text(encoding="utf-8")
SCREENS = (SRC / "screens.c").read_text(encoding="utf-8")


def _body(source: str, signature: str) -> str:
    start = source.index(signature)
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        depth += (source[j] == "{") - (source[j] == "}")
        if depth == 0:
            return source[i:j + 1]
    raise AssertionError(signature)


# ---- the wiring ---------------------------------------------------------------

def test_the_default_is_light_and_is_remembered():
    main = _body(MAIN, "int main(int argc, char **argv)")
    assert "a.theme = &THEME_LIGHT;" in main
    assert "a.theme = &THEME_DARK;" not in main
    # after the options: --theme dark still starts dark, and that is the default
    assert main.index("a.default_theme = a.theme;") > main.index('"--theme"')


def test_login_applies_the_saved_theme_and_logout_returns_to_the_default():
    login = _body(MAIN, "static void do_login(")
    assert "a->theme = theme_saved(saved, a->default_theme);" in login
    assert login.index("theme_saved") > login.index("if (!ok)"), "a refused login changes nothing"
    assert "a->theme = a->default_theme;" in _body(MAIN, "static void sign_out(")


def test_a_switch_is_saved_only_while_signed_in():
    toggle = _body(MAIN, "static void toggle_theme(")
    assert re.search(r'if \(a->signed_in\) emit1\(a->theme == &THEME_DARK \? "theme-dark" : "theme-light"\);',
                     toggle), toggle


def test_the_switch_is_reachable_on_every_screen_before_any_other_handler():
    handle = _body(MAIN, "static int handle(")
    at = handle.index("toggle_theme(a);")
    assert "KEYSYM_F2" in handle[:at] and "HIT_THEME" in handle[:at]
    for later in ("SCREEN_LCBOOT", "shrink_modal", "ask_modal", "logout_modal", "confirm_modal"):
        assert handle.index(later) > at, f"{later} would swallow the switch"


def test_the_switch_is_drawn_last_in_the_corner():
    draw = _body(SCREENS, "static void app_draw_logical(")
    assert draw.rstrip("}").rstrip().endswith("draw_theme_toggle(a,cr,W,head_h);   /* #1499: last -- over every screen and dialog, and its hit wins */")
    toggle = _body(SCREENS, "static void draw_theme_toggle(")
    assert "hit_add(a,b,HIT_THEME);" in toggle
    assert '"F2"' in toggle


# ---- theme.c, compiled --------------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include "theme.h"
static const char *id(const Theme *t) { return t ? t->id : "null"; }
int main(void) {
    const char *saved[] = { "light", "dark", "auto", "", "blue", "Light", NULL };
    for (int i = 0; saved[i]; i++) printf("saved %s -> %s\n", saved[i], id(theme_saved(saved[i], &THEME_LIGHT)));
    printf("saved (null) dark-default -> %s\n", id(theme_saved(NULL, &THEME_DARK)));
    printf("saved auto dark-default -> %s\n", id(theme_saved("auto", &THEME_DARK)));
    const char *answers[] = { "admin\ndark\n", "deploy\r\nlight\r\n", "admin\n", "admin", "", "admin\n\n", "admin\ndark\nadvanced\n", NULL };
    for (int i = 0; answers[i]; i++) {
        char role[8], t[8];
        theme_login_answer(answers[i], role, sizeof role, t, sizeof t);
        printf("answer %d -> [%s] [%s]\n", i, role, t);
    }
    char role[4], t[4];   /* longer than the buffers: cut, terminated */
    theme_login_answer("administrator\ndarkness\n", role, sizeof role, t, sizeof t);
    printf("cut -> [%s] [%s]\n", role, t);
    return 0;
}
'''


@requires_native(("cc", shutil.which("cc") or shutil.which("gcc")), posix=True,
                 why="theme.c is compiled on Linux (the lab / CI)")
def test_theme_helpers(tmp_path):
    (tmp_path / "driver.c").write_text(DRIVER, encoding="utf-8")
    binary = tmp_path / "driver"
    build = subprocess.run(
        [shutil.which("cc") or shutil.which("gcc"), "-Wall", "-Wextra", "-std=c11",
         "-I", str(SRC), "-o", str(binary), str(tmp_path / "driver.c"), str(SRC / "theme.c")],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert build.returncode == 0, build.stderr
    out = subprocess.run([str(binary)], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=30).stdout.splitlines()
    assert out == [
        "saved light -> light", "saved dark -> dark", "saved auto -> light", "saved  -> light",
        "saved blue -> light", "saved Light -> light",
        "saved (null) dark-default -> dark", "saved auto dark-default -> dark",
        "answer 0 -> [admin] [dark]", "answer 1 -> [deploy] [light]", "answer 2 -> [admin] []",
        "answer 3 -> [admin] []", "answer 4 -> [] []", "answer 5 -> [admin] []",
        "answer 6 -> [admin] [dark]",   # #3025: the third line (the view) changes nothing
        "cut -> [adm] [dar]",
    ], out


# ---- the whole GUI, rendered ---------------------------------------------------

@requires_native(
    ("bash", BASH),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui is built on Linux only",
)
def test_every_card_with_a_header_has_the_switch_in_its_corner(tmp_path):
    binary = _build_gui(tmp_path)
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "card"), "--size", "1280x800",
                          "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
                         capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    cards = sorted(p for p in tmp_path.glob("card-*.png") if "-login-" not in p.name)
    assert len(cards) >= 20, cards
    missing = []
    for png in cards:
        w, _h, ch, px = _png_rgb(png)            # 1280x800: scale 1, logical = pixels
        def at(x, y):
            i = (y * w + x) * ch
            return px[i:i + 3]
        # (45,10): the switch's top outline (24..80 x 10..38 at 1280 wide, a 1px
        # stroke on row 10), clear of its rounded corners. Before #1499 the clock
        # sat at x=24 with its digits below row 15: row 10 was bare header.
        edge, header = at(45, 10), at(640, 2)
        if not all(e - h > 15 for e, h in zip(edge, header)):
            missing.append(f"{png.name}: {tuple(edge)} vs header {tuple(header)}")
    assert not missing, "\n".join(missing)
