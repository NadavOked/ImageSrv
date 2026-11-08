"""#1843 -- the cloner's own screen says when the wave is expected to end (mockup 07/11, Nadav approved).

The approved proposal: the hour ("צפוי להסתיים ב-HH:MM") above "כ-N דק' נותרו", both stay; with no history
the screen says "אין עדיין הערכת סיום", never an invented number. The hour is the *wall clock* of the
cloner, which the agent sets from the server's `now` on every hello (#1447) -- so when the server's own
clock is not proven synced (#2144) no hour is drawn at all.

Three layers, each against the real thing:

* the agent (busybox-style sh, `clonergui.sh`): the hello's `session.forecast` becomes one `forecast=` record;
* the state parser and the cloner screen (`state.c`, `screens_cloner.c`, `lcbay.c`): every text it draws,
  through the real sources, with the clock fixed;
* a `--png` render at the mockup's 960x540: the hour is in the action colour (pixels), and only when it
  should be.

The server's half (the hello answer) is `test_hello_eta_1843.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import (BASH, GUI, PORTS, PRELUDE, _build_gui, _count_color, _pkgconfig, _png,
                             _png_rgb, env, posix, sh, target)

G = 1_000_000_000
#: 2100-01-01 10:52:00 UTC: far enough ahead to be "not over" whenever the test runs; the parser's TZ is UTC.
HISTORY_END = 4102483920


# --- the agent: session.forecast -> forecast= -----------------------------------------------------------


def _forecast_lines(tmp_path: Path, hello: str) -> list[str]:
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text(hello, newline="\n")
    target(run, "sda", "writing", 100, 40, "5")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    return [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("forecast=")]


def _hello(forecast: str | None) -> str:
    return '{"schema":1,"session":{"id":"ses_1","state":"running"' + (
        f',"forecast":{forecast}' if forecast is not None else "") + "}}"


@pytest.mark.parametrize("forecast,record", [
    # history, the clock proven: the hour, as an epoch
    ('{"ends_at":1762512720,"why":null,"clock":true}', "forecast=1|1762512720|"),
    # no history: said, and no hour
    ('{"ends_at":null,"why":"no_history","clock":true}', "forecast=1||no_history"),
    # an estimate the clock does not back: clock 0 and no hour
    ('{"ends_at":null,"why":"clock","clock":false}', "forecast=0||"),
    ('{"ends_at":null,"why":"no_history","clock":false}', "forecast=0||no_history"),
    # a reason that is neither "history" nor "clock" says nothing new
    ('{"ends_at":null,"why":"error","clock":true}', "forecast=1||"),
    # an hour that is not a number is no hour (never `"soon"` on the way to the screen)
    ('{"ends_at":"soon","why":null,"clock":true}', "forecast=1||"),
    # a server older than the field's `clock` has not proven anything
    ('{"ends_at":1762512720,"why":null}', "forecast=0|1762512720|"),
])
@requires_native("jq", ("bash", BASH), why="json_get reads the hello with jq")
def test_the_hello_forecast_becomes_one_record(tmp_path, forecast, record):
    assert _forecast_lines(tmp_path, _hello(forecast)) == [record]


@pytest.mark.parametrize("hello", [
    _hello(None),                                              # an older server: no field
    _hello('"later"'),                                         # not an object
    _hello("null"),
    '{"schema":1}',                                            # no session at all
    '{"schema":1,"session":null}',
])
@requires_native("jq", ("bash", BASH), why="json_get reads the hello with jq")
def test_nothing_said_is_no_record(tmp_path, hello):
    assert _forecast_lines(tmp_path, hello) == []


# --- the screen: real parser, real cloner screen, every text it drew -----------------------------------

DRIVER = r'''
#include <stdio.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
Text __real_text_make_ltr(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px);
Text __wrap_text_make_ltr(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make_ltr(cr, font, px, weight, utf8, width_px);
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1; a.fixed_clock = 1;   /* 12:45, as --png */
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f);
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_destroy(s);
    return 0;
}
'''

_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("forecast1843")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make,--wrap=text_make_ltr -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _state(rate: int, eta: int, forecast: str | None, moved_ago: int | None = None) -> str:
    """The mockup's wave: two drawers of one machine writing 47% of 85 GB (rate/eta -1 = not measured)."""
    import time
    now = int(time.time())
    drawers = "".join(
        f"drawer={port}|{dev}|writing|{int(0.47 * 85 * G)}|{int(85 * G)}||{rate}|{eta}|{port}|SATA {port - 1}\n"
        for port, dev in ((1, "sda"), (2, "sdb")))
    return ("cloner=classroom-win11 v7\n" + drawers
            + (f"drawer_moved=sda|{now - moved_ago}\n" if moved_ago is not None else "")
            + (f"forecast={forecast}\n" if forecast is not None else "")
            + f"updated={now}\n")


def texts(driver: Path, tmp_path: Path, state: str, writing: bool = True) -> list[str]:
    p = tmp_path / "state.txt"
    p.write_text(state, encoding="utf-8", newline="\n")
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    got = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
    assert any("מעתיק" in t for t in got) == writing, got   # the cloner's round screen, not another view
    return got


AT = "צפוי להסתיים ב-"
BY_RATE, BY_HISTORY = "לפי הקצב בפועל", "לפי היסטוריה · הקצב עוד לא נמדד"
NO_HISTORY, UNMEASURED = "אין עדיין הערכת סיום (אין היסטוריה)", "הזמן שנותר טרם נמדד"


@_GUI
def test_the_measured_rate_gives_the_hour_over_the_minutes_left(driver, tmp_path):
    """Mockup 1: 47%, 55 MB/s, 14 minutes left -> "צפוי להסתיים ב-<now + 14 min>" over "כ-14 דק' נותרו", both kept."""
    got = texts(driver, tmp_path, _state(55_000_000, 840, "1|" + str(HISTORY_END) + "|"))
    assert AT in got and "12:59" in got, got          # 12:45 (the fixed clock) + 840 s
    assert BY_RATE in got and "כ-14 דק' נותרו" in got, got
    assert BY_HISTORY not in got and UNMEASURED not in got, got


@_GUI
def test_history_alone_gives_the_hour_until_the_rate_is_measured(driver, tmp_path):
    """Mockup 2: 3%, the speed not measured yet -> the history's hour, and what it rests on."""
    got = texts(driver, tmp_path, _state(-1, -1, "1|" + str(HISTORY_END) + "|"))
    assert AT in got and "10:52" in got and BY_HISTORY in got, got
    assert UNMEASURED not in got and BY_RATE not in got, got   # the history line replaces "not measured yet"


@_GUI
def test_no_history_says_so_and_draws_no_hour(driver, tmp_path):
    """Mockup 3: "הזמן שנותר טרם נמדד" and, under it, why there is no estimate."""
    got = texts(driver, tmp_path, _state(-1, -1, "1||no_history"))
    assert UNMEASURED in got and NO_HISTORY in got, got
    assert AT not in got, got


@_GUI
def test_a_clock_not_proven_synced_draws_no_hour_but_keeps_the_minutes(driver, tmp_path):
    """#2144: a wrong hour is worse than none. The measured minutes are the screen as it was."""
    for forecast in ("0|" + str(HISTORY_END) + "|", "0||"):
        got = texts(driver, tmp_path, _state(55_000_000, 840, forecast))
        assert AT not in got and BY_RATE not in got and "12:59" not in got, got
        assert "כ-14 דק' נותרו" in got, got
        got = texts(driver, tmp_path, _state(-1, -1, forecast))
        assert AT not in got and "10:52" not in got and BY_HISTORY not in got, got
        assert UNMEASURED in got, got


@_GUI
def test_a_history_that_says_there_is_none_does_not_hide_the_unproven_clock_case(driver, tmp_path):
    """"אין היסטוריה" needs no clock: it is said even when the clock is not proven."""
    got = texts(driver, tmp_path, _state(-1, -1, "0||no_history"))
    assert NO_HISTORY in got and AT not in got, got


@_GUI
def test_a_standing_stream_promises_no_end(driver, tmp_path):
    """#2055: the bytes stood for 3 minutes -- "waiting for the stream", and neither an hour nor "no history"."""
    for forecast in ("1|" + str(HISTORY_END) + "|", "1||no_history"):
        got = texts(driver, tmp_path, _state(0, -1, forecast, moved_ago=180))
        assert "ממתין לזרם מהשרת" in got, got
        assert AT not in got and NO_HISTORY not in got and BY_HISTORY not in got, got


@_GUI
def test_a_history_hour_already_past_is_not_drawn(driver, tmp_path):
    got = texts(driver, tmp_path, _state(-1, -1, "1|1000000000|"))      # 2001
    assert AT not in got and BY_HISTORY not in got, got
    assert UNMEASURED in got, got


@_GUI
@pytest.mark.parametrize("forecast", [None, "1|", "2||", "1|x|", "x", "1|" + str(HISTORY_END), "1||later", "-1||"])
def test_an_old_server_or_a_broken_record_is_the_screen_as_it_was(driver, tmp_path, forecast):
    """No `forecast=`, or one that is not `<0|1>|<epoch>|<why>`: not said (principle 5), nothing new drawn."""
    got = texts(driver, tmp_path, _state(55_000_000, 840, forecast))
    assert AT not in got and BY_RATE not in got, got
    assert "כ-14 דק' נותרו" in got, got
    got = texts(driver, tmp_path, _state(-1, -1, forecast))
    assert AT not in got and NO_HISTORY not in got and UNMEASURED in got, got


@_GUI
def test_the_end_of_the_round_has_no_forecast(driver, tmp_path):
    """The hour is the write's own line: a finished drawer's screen does not carry it."""
    done = ("cloner=classroom-win11 v7\n"
            + "".join(f"drawer={p}|{d}|done|{85 * G}|{85 * G}||-1|-1|{p}|SATA {p - 1}\n" for p, d in ((1, "sda"), (2, "sdb")))
            + "forecast=1|" + str(HISTORY_END) + "|\nupdated=1\n")
    got = texts(driver, tmp_path, done, writing=False)
    assert AT not in got and BY_HISTORY not in got, got


# --- pixels: the hour is in the action colour, at the mockup's 960x540 ----------------------------------


def _hero_action_pixels(tmp_path: Path, binary: Path, state: str, name: str) -> int:
    import time
    from test_cloner_gui import _token
    p = tmp_path / f"{name}.txt"
    p.write_text(state + "disk_probe=drives\nmessage=x|y\n", encoding="utf-8", newline="\n")
    ahead = time.time() + 3600      # a state file older than 10 s draws the "agent is not updating" toast (#2099)
    os.utime(p, (ahead, ahead))
    prefix = tmp_path / name
    _png(binary, prefix, p, size="960x540")
    w, h, ch, px = _png_rgb(Path(f"{prefix}-cloner-light.png"))
    action = _token("--clr-action-fg")
    # the hero: the top third of the body, at the start (the number's) side -- the left of an RTL screen
    rows = bytearray()
    for y in range(40, int(h * 0.46)):
        rows += px[(y * w) * ch:(y * w + w // 2) * ch]
    return _count_color(bytes(rows), ch, action, tol=12)


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH), why="native-gui נבנה על המעבדה בלבד")
def test_the_hour_is_drawn_in_the_action_colour_and_only_when_it_should_be(tmp_path):
    binary = _build_gui(tmp_path)
    shown = _hero_action_pixels(tmp_path, binary, _state(55_000_000, 840, "1|" + str(HISTORY_END) + "|"), "shown")
    unproven = _hero_action_pixels(tmp_path, binary, _state(55_000_000, 840, "0||"), "unproven")
    old = _hero_action_pixels(tmp_path, binary, _state(55_000_000, 840, None), "old")
    assert shown > 150, shown                  # "12:59" at 24px mono semibold: hundreds of pixels
    assert unproven == old, (unproven, old)    # the clock not proven draws what an old server's screen does
    assert shown > unproven + 150, (shown, unproven)
