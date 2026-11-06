"""#1412: the native installer draws "ISO מעבדה — לא למכללה" above every step.

The bridge's inventory says ``lab_overlay=lab|clean|unchecked`` (the medium's
``imagesrv-iso.json``). ``install.c`` keeps it in ``InstallState.lab``; zero is
"not checked", so a bridge that says nothing -- an older one, a broken read --
is never "clean" (principle 5). ``screens_install.c`` draws a red strip for
``lab`` and an orange one for anything not checked, and moves the page down by
its height; a clean ISO has no strip.

The C is built and run on Linux (the testrunner); the source contracts run
everywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_native_installer_gui import NATIVE as NATIVE_GUI, _build, _colors

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
GUI = REPO / "native-gui"


def _c(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# --- the source contracts (every machine) ---------------------------------------

def test_not_checked_is_the_zero_state_and_unknown_words_fall_back_to_it():
    state = _c("install_state.h")
    assert "enum { INSTALL_LAB_UNCHECKED = 0, INSTALL_LAB_CLEAN, INSTALL_LAB_YES };" in state
    control = _c("install.c")
    assert ('else if(!strcmp(line,"lab_overlay"))s->lab=!strcmp(eq,"lab")?INSTALL_LAB_YES:'
            '!strcmp(eq,"clean")?INSTALL_LAB_CLEAN:INSTALL_LAB_UNCHECKED;') in control


def test_the_strip_is_drawn_unless_clean_and_moves_the_page_down():
    frame = _c("screens_install.c")
    banner = frame[frame.index("static double lab_banner("):frame.index("InstallLayout install_frame(")]
    assert "if(lab==INSTALL_LAB_CLEAN)return 0;" in banner
    assert "t->danger_fill" in banner and "t->warning_bg" in banner
    assert "ISO מעבדה — לא למכללה" in frame and "לא נבדק אם זה ISO מעבדה" in frame
    assert "{cx,105+top,col,H-185-top}" in frame


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

/* argv: BRIDGE [AGAIN] -- print the state after the inventory; with AGAIN, after
 * a second inventory too (a re-read must not keep the first answer) */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    if (install_start(a, &b, 0)) { printf("start-failed\n"); return 3; }
    printf("lab=%%d\n", a->install.lab);
    if (argc > 2) { char text[256]; snprintf(text, sizeof text, "%%s", argv[2]); parse_lines(&a->install, text, "inventory");
                    printf("again=%%d\n", a->install.lab); }
    printf("unchecked=%%d\nclean=%%d\nyes=%%d\n", INSTALL_LAB_UNCHECKED, INSTALL_LAB_CLEAN, INSTALL_LAB_YES);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-lab")
    source = tmp / "install_lab.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_lab"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _drive(driver: Path, line: str, *again: str) -> dict:
    bridge_cmd = ('f(){ cat > /dev/null; case "$1" in '
                  "inventory) printf 'disk=/dev/sda|model=X|size=1|bus=sata|removable=0|has=empty|iso=0\\n'; "
                  '[ -z "$LAB_LINE" ] || printf "%s\\n" "$LAB_LINE" ;; '
                  "progress) printf 'state=ready\\n' ;; esac; }; f")
    proc = subprocess.run([str(driver), bridge_cmd, *again], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL, env={**os.environ, "LAB_LINE": line})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return dict(row.split("=", 1) for row in proc.stdout.splitlines())


@NATIVE
@pytest.mark.parametrize("line,expected", [
    ("lab_overlay=lab", "yes"),
    ("lab_overlay=clean", "clean"),
    ("lab_overlay=unchecked", "unchecked"),
    ("lab_overlay=true", "unchecked"),     # not the bridge's word
    ("lab_overlay=", "unchecked"),
    ("", "unchecked"),                     # an older bridge: no line at all
])
def test_the_inventory_line_sets_the_state_and_anything_else_is_not_checked(driver, line, expected):
    state = _drive(driver, line)
    assert state["lab"] == state[expected]


@NATIVE
def test_a_second_inventory_without_the_line_forgets_the_first_answer(driver):
    state = _drive(driver, "lab_overlay=clean", "disk=/dev/sda|model=X|size=1|bus=sata|removable=0|has=empty|iso=0\n")
    assert state["lab"] == state["clean"] and state["again"] == state["unchecked"]


# --- the drawing (Linux, pango/cairo) -----------------------------------------------

LIGHT = {"danger_fill": (0xE1, 0x22, 0x00), "warning_bg": (0xFF, 0xF2, 0xE3)}
DARK = {"danger_fill": (0xC2, 0x44, 0x44), "warning_bg": (0x33, 0x29, 0x1A)}


@NATIVE_GUI
def test_the_strip_is_red_for_a_lab_iso_orange_when_not_checked_and_absent_when_clean(tmp_path):
    """1280x1024 renders at scale 1.25: the content area is 0..967 px wide (the
    rail is to its right). Rows 0..20 px are the strip's own, above the title
    (y=28 logical = 35 px) -- on a clean ISO they are the page background."""
    binary = _build(tmp_path)
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    prefix = tmp_path / "r"
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png", str(prefix),
                           "--size", "1280x1024"], capture_output=True, text=True, timeout=120,
                          env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    def top(name: str, theme: str) -> set:
        return _colors(tmp_path / f"r-{name}-{theme}.png", (0, 0, 900, 20))[2]

    for theme, tokens in (("light", LIGHT), ("dark", DARK)):
        red, orange = bytes(tokens["danger_fill"]), bytes(tokens["warning_bg"])
        for name in ("install-0c-disk-lab", "install-4b-admin-lab", "install-5d-summary-lab"):
            assert red in top(name, theme), (name, theme)
        assert orange in top("install-0d-disk-lab-unknown", theme), theme
        clean = top("install-0-disk", theme)
        assert red not in clean and orange not in clean, theme
