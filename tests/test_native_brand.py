"""#1302: the ImageSrv logo (option K, "bit by bit", approved by Nadav 12/10)
on the native GUI -- the build/cloner screens and the graphical installer.

native-gui/src/brand.c draws it with Cairo from the approved SVGs, which are
kept in docs/brand/. The source tests bind brand.c's numbers and colours to
those files (they run anywhere); the render test builds the GUI and looks for
the logo's own colours where each surface draws it (Testrunner only).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import os
from pathlib import Path

from native import requires_native
from test_cloner_gui import _build_gui, _pkgconfig, _png_rgb, posix

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
# absent = every source test fails by itself (not one collection error for the file)
BRAND_C = (SRC / "brand.c").read_text(encoding="utf-8") if (SRC / "brand.c").exists() else ""
SVG = REPO / "docs" / "brand"


def _svg(name: str) -> str:
    return (SVG / name).read_text(encoding="utf-8")


def _hex(name: str) -> str:
    found = re.search(r"\b" + name + r" = HEX\(0x([0-9A-Fa-f]{6})\)", BRAND_C)
    assert found, f"{name} is not in brand.c"
    return "#" + found[1].upper()


def _body(src: str, name: str) -> str:
    start = src.index(name + "(")
    return src[start:src.index("\n}\n", start)]


def test_the_colours_are_the_approved_lockups():
    for name, suffix in (("logo-lockup.svg", ""), ("logo-lockup-dark.svg", "_DARK")):
        svg = _svg(name)
        symbol = re.search(r'<g fill="(#[0-9A-F]{6})"><rect', svg)[1]
        image, srv = re.findall(r'stroke="(#[0-9A-F]{6})"/>', svg)
        tag = re.search(r'<text [^>]*fill="(#[0-9A-F]{6})"', svg)[1]
        assert _hex("BRAND_BLUE" + suffix) == symbol == srv, name
        assert _hex("BRAND_INK" + suffix) == image, name
        assert _hex("BRAND_TAG" + suffix) == tag, name


def test_the_pattern_and_its_copy_are_logo_svg():
    rects = [(float(x), float(y)) for x, y in re.findall(r'<rect x="([\d.]+)" y="([\d.]+)"', _svg("logo.svg"))]
    xs, ys = sorted({x for x, _ in rects}), sorted({y for _, y in rects})
    left, right = xs[:3], xs[3:]
    lit = lambda cols: [[int((cols[c], y) in rects) for c in range(3)] for y in ys]
    assert lit(left) == lit(right), "the second pattern is an exact copy of the first"
    rows = re.search(r"PATTERN\[3\]\[3\] = \{ (.*?) \};", BRAND_C)[1]
    assert [[int(v) for v in r.split(",")] for r in re.findall(r"\{([\d,]+)\}", rows)] == lit(left)
    # the PNGs' gaps: 3px cells / 1px gaps / 4px between the copies (symbol-white-32.png),
    # 6 / 2 / 8 (symbol-white-64.png) -- logo.svg's 6 / 2 / 8 units
    assert (xs[1] - xs[0] - 6, xs[3] - xs[2] - 6) == (2.0, 8.0)
    assert "int g = (int)lround(cell / 3.0);" in BRAND_C
    assert "return (int)lround(cell * 4.0 / 3.0);" in BRAND_C


def test_the_lockup_geometry_is_read_from_the_svgs():
    compact, full = _svg("logo-lockup-compact.svg"), _svg("logo-lockup.svg")
    assert 'transform="translate(94 2.80) scale(0.92)"' in compact and 'stroke-width="5.4"' in compact
    symbol_w = max(float(x) + 9 for x in re.findall(r'<rect x="([\d.]+)" y=', compact))
    assert f"#define WORD_GAP {94 - symbol_w:.1f}" in BRAND_C
    assert "#define WORD_SCALE .92" in BRAND_C and "#define WORD_Y 2.8 " in BRAND_C
    assert "cairo_set_line_width(cr, 5.4);" in BRAND_C
    top = lambda svg: min(float(y) for y in re.findall(r'<rect x="[\d.]+" y="([\d.]+)"', svg))
    assert f"#define SYMBOL_Y {top(compact)} " in BRAND_C
    assert f"#define SYMBOL_Y_TAG {top(full)} " in BRAND_C
    text = re.search(r'<text x="([\d.]+)" y="([\d.]+)"[^>]*font-size="(\d+)" font-weight="(\d+)"', full)
    assert f"#define TAG_BASELINE {float(text[2])}" in BRAND_C
    assert f"right - k * (291.5 - {text[1]})" in BRAND_C
    assert f'text_make(cr, FONT_SANS, {text[3]} * k, {text[4]}, "שרת אימג\'ים", 0, DIR_RTL)' in BRAND_C


def test_the_word_is_the_svgs_strokes():
    """Every coordinate of a line or curve in the SVG paths is in brand.c's
    paths (an S's reflected control point and an arc's centre are worked out
    there, so arcs are compared as centres and radius instead)."""
    image_d, srv_d = re.findall(r'<path d="([^"]+)"', _svg("logo-lockup-compact.svg"))
    numbers = lambda body: {float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", body)}

    def plain(d: str) -> set[float]:
        out: set[float] = set()
        for cmd, args in re.findall(r"([MVHCSLA])([^MVHCSLA]*)", d):
            if cmd != "A":
                out |= numbers(args)
        return out

    image_c, srv_c = numbers(_body(BRAND_C, "static void path_image")), numbers(_body(BRAND_C, "static void path_srv"))
    assert plain(image_d) - image_c == set()
    assert plain(srv_d) - srv_c == set()
    assert "cairo_arc(cr, 64, 49, 11, 0, 2 * M_PI)" in BRAND_C      # a: A11 11 .. 53 49 / 75 49
    assert "cairo_arc(cr, 95, 49, 11, 0, 2 * M_PI)" in BRAND_C      # g: 84 49 / 106 49
    assert "cairo_arc_negative(cr, 126, 49, 11, 0, atan2(56.1 - 49, 134.4 - 126))" in BRAND_C   # e: sweep 0


def test_every_surface_draws_the_new_logo_and_the_old_mark_is_gone():
    screens = (SRC / "screens.c").read_text(encoding="utf-8")
    install = (SRC / "screens_install.c").read_text(encoding="utf-8")
    assert "right=draw_brand_lockup_r(cr,right,(N_HEADER_H-brand_symbol_height(5))/2,5,1,0)-22;" in screens  # header
    assert "draw_brand_lockup_r(cr,W-64,56,6,t==&THEME_DARK,1);" in screens                                  # login
    # #1304: mockup A has no logo tile on the cloner's standby (the header carries the logo)
    assert "draw_brand_tile" not in (SRC / "screens_standby.c").read_text(encoding="utf-8")
    assert "draw_brand_lockup_r(cr,l.rail.x+l.rail.w-28,28+(30-brand_symbol_height(5))/2,5,1,0);" in install  # installer
    makefile = (REPO / "native-gui" / "Makefile").read_text(encoding="utf-8")
    assert "src/brand.c" in makefile and "src/brand.h" in makefile
    for path in SRC.glob("*.[ch]"):
        assert "draw_brand_mark" not in path.read_text(encoding="utf-8"), path.name


def test_the_gui_never_shows_the_old_product_name():
    """User-facing strings say ImageSrv, and since #1699 so do the internal
    names: no "ImageCtl" literal is left in the GUI at all."""
    shown = []
    for path in sorted(SRC.glob("*.c")):
        code = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        for line in code.splitlines():
            for literal in re.findall(r'"(?:[^"\\]|\\.)*"', line):
                if "ImageCtl" in literal:
                    shown.append(f"{path.name}: {literal}")
    assert shown == []
    assert '{"packages", "מתקין את חבילות ImageSrv"}' in (SRC / "install.c").read_text(encoding="utf-8")
    # the exemption holds only while the two names are the same device (#1475)
    gui = re.search(r'#define MONITOR_UINPUT_NAME (".*?")', (SRC / "input.c").read_text(encoding="utf-8"))
    agent = re.search(r'#define UINPUT_NAME (".*?")', (SRC.parents[1] / "agent" / "monitor.c").read_text(encoding="utf-8"))
    assert gui and agent and gui.group(1) == agent.group(1), (gui, agent)


_NEEDS_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


_DECODED: dict[Path, tuple] = {}


def _count(path: Path, box: tuple[int, int, int, int], rgb: str, tol: int = 3) -> int:
    if path not in _DECODED:
        _DECODED[path] = _png_rgb(path)
    w, _h, ch, px = _DECODED[path]
    x0, y0, x1, y1 = box
    want = [int(rgb[i:i + 2], 16) for i in (1, 3, 5)]
    n = 0
    for y in range(y0, y1):
        row = y * w * ch
        for x in range(x0, x1):
            i = row + x * ch
            if all(abs(px[i + c] - want[c]) <= tol for c in range(3)):
                n += 1
    return n


@_NEEDS_GUI
def test_the_logo_is_drawn_on_every_surface(tmp_path):
    """1280x800 is scale 1, so the symbol's squares sit on whole pixels and
    their inside is exactly the logo's colour: 12 squares of 5x5 on the
    header and the installer's rail, 12 of 6x6 on the login (#1304 took the
    standby's tile away -- mockup A). The old mark used only theme colours -- none of these appear where
    it was (the negative control: this test fails on the old code)."""
    binary = _build_gui(tmp_path)
    env = dict(os.environ, FONTCONFIG_FILE=str(REPO / "native-gui" / "fonts.conf"))
    for args in (["--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"], ["--screen", "install", "--demo"]):
        run = subprocess.run([str(binary), *args, "--png", posix(tmp_path / "c"), "--size", "1280x800"],
                             capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
        assert run.returncode == 0, run.stderr
    for theme in ("light", "dark"):
        header = (1280 - 24 - 175, 0, 1280, 48)                    # the header's right end
        assert _count(tmp_path / f"c-menu-{theme}.png", header, "#3FA4DA") >= 12 * 25 + 40, theme
        assert _count(tmp_path / f"c-menu-{theme}.png", header, "#F2F5F7") >= 40, theme   # "Image"
        rail = (1030, 0, 1280, 70)
        assert _count(tmp_path / f"c-install-0-disk-{theme}.png", rail, "#3FA4DA") >= 12 * 25 + 40, theme
        login = (860, 0, 1280, 110)
        blue, ink = ("#0079B8", "#1D2730") if theme == "light" else ("#3FA4DA", "#F2F5F7")
        assert _count(tmp_path / f"c-login-{theme}.png", login, blue) >= 12 * 36 + 60, theme
        assert _count(tmp_path / f"c-login-{theme}.png", login, ink) >= 60, theme
