"""‏#949: ‏`native-gui/src/input.c` — התקן מוחלט ממופה 1:1 לפיקסלים, והסמן נשאר.

‏`monitor.c` מצהיר ABS_X/ABS_Y בטווח 0..w-1 / 0..h-1 (ראה
‏`test_monitor_native.py`), והצד השני של החוזה הוא כאן: ‏`read_dev` חייב
להחזיר בדיוק את הערך שנכתב — לא `value·w/max` שנתן 17.27 במקום 17 והצמיד
את הפיקסל האחרון מחוץ למסך. ושלושה סוגי התקן מובחנים בהתנהגות:

| התקן | מיפוי | `is_touch` (הסמן) |
|---|---|---|
| מוחלט בלי BTN_TOUCH (המוניטור, טאבלט) | ‏1:1 | ‏0 — הסמן מצויר |
| מסך מגע (‏BTN_TOUCH) | ‏max → הפיקסל האחרון | ‏1 — בלי סמן |
| עכבר יחסי (‏REL) | ‏Δ·PTR_GAIN מהמרכז | ‏0 |

הבדיקה עוקפת את `input_open` (שסורק ותופס את **כל** ‏`/dev/input`, כולל
המקלדת של המעבדה): הדרייבר כולל את `input.c` ישירות (‏`#include`) וקורא
ל-`probe` על הנתיב שהטסט יצר דרך uinput, ואז מריץ את לולאת הקריאה
האמיתית. ‏gcc + ‏`/dev/uinput` כתיב = מעבדה בלבד, כ-root.

בקרה שלילית (על `input.c` שלפני #949): ההתקן המוחלט מסווג כמגע —
‏`is_touch=1` ו-‏`x=17.27` — ושני הטסטים הראשונים נופלים; העכבר היחסי עובר
בשני המצבים, וזה השומר מפני "תיקון" שמשנה גם אותו.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import evdev_lab
from native import requires_native

REPO = Path(__file__).resolve().parent.parent
INPUT_C = REPO / "native-gui" / "src" / "input.c"

W, H = 64, 32

LAB = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("/dev/uinput (root)", evdev_lab.uinput_available()),
    posix=True,
    why="input.c נבנה ומורץ מול uinput במעבדה בלבד",
)

DRIVER = r'''
#include "%(input_c)s"
#include <stdio.h>
#include <stdlib.h>
#ifdef UIEV_MOVE_DOWN   /* #2778: the button held during a move (a drag); -1 = this input.c has no such field */
#define HELD(e) (e).down
#else
#define HELD(e) -1
#endif

/* argv: W H MS PATH -- probe PATH, then pump events for MS milliseconds
 * and print each one as "MOVE|CLICK x y is_touch". "ready" goes out first
 * so the test knows the device is grabbed before it emits. */
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    Input *in = calloc(1, sizeof *in);
    in->w = atoi(argv[1]); in->h = atoi(argv[2]);
    in->px = in->w / 2.0; in->py = in->h / 2.0;
    if (probe(in, argv[4]) != 0) { fprintf(stderr, "probe %%s failed\n", argv[4]); return 3; }
    printf("ready kinds=%%d\n", in->dev[0].kinds);
    fflush(stdout);
    int ms = atoi(argv[3]);
    struct pollfd fds[MAX_DEV];
    int elapsed = 0;
    while (elapsed < ms) {
        int n = input_fill_pollfds(in, fds, MAX_DEV);
        int r = poll(fds, n, 50);
        elapsed += 50;
        if (r > 0) input_pump(in, fds, n);
        Event e;
        while (input_next(in, &e)) {
            if (e.type == UIEV_MOVE || e.type == UIEV_CLICK)
                printf("%%s %%.3f %%.3f %%d %%d\n", e.type == UIEV_MOVE ? "MOVE" : "CLICK",
                       e.x, e.y, e.is_touch, HELD(e));
        }
    }
    fflush(stdout);
    input_close(in);
    return 0;
}
'''


def _build_driver(tmp_path: Path) -> Path:
    source = tmp_path / "input_drv.c"
    source.write_text(DRIVER % {"input_c": INPUT_C.as_posix()})
    binary = tmp_path / "input_drv"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-std=c11", "-D_GNU_SOURCE",
         "-o", str(binary), str(source)],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    return binary


def _run(binary: Path, device: evdev_lab.UinputDevice,
         frames: list[list[tuple[int, int, int]]], tmp_path: Path, held: bool = False) -> list[tuple]:
    """מריץ את הדרייבר על ההתקן, שולח את המסגרות אחרי `ready`, ומחזיר
    את האירועים שהגואי היה מקבל."""
    out_path = tmp_path / f"{device.name}.out"
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(
            [str(binary), str(W), str(H), "1500", str(device.node)],
            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while b"ready" not in out_path.read_bytes():
            assert proc.poll() is None, f"driver exited rc={proc.returncode}: {out_path.read_text()}"
            assert time.monotonic() < deadline, f"driver never became ready: {out_path.read_text()}"
            time.sleep(0.02)
        for frame in frames:
            device.emit(*frame)
            time.sleep(0.05)
        assert proc.wait(timeout=10) == 0
    lines = out_path.read_text().splitlines()
    assert lines and lines[0].startswith("ready"), lines
    events = []
    for line in lines[1:]:
        kind, x, y, touch, is_held = line.split()
        events.append((kind, float(x), float(y), int(touch)) + ((int(is_held),) if held else ()))
    return events


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """נבנה פעם אחת למודול; הסימון `LAB` על כל טסט מדלג/נופל לפני שמגיעים לכאן."""
    return _build_driver(tmp_path_factory.mktemp("input-drv"))


@pytest.mark.uinput
@LAB
def test_absolute_pointer_maps_one_to_one_and_keeps_the_cursor(driver, tmp_path):
    ABS_X, ABS_Y, EV_ABS, EV_KEY, BTN_LEFT = (
        evdev_lab.ABS_X, evdev_lab.ABS_Y, evdev_lab.EV_ABS, evdev_lab.EV_KEY, evdev_lab.BTN_LEFT)
    with evdev_lab.UinputDevice("ic949-abs-pointer", abs=(W - 1, H - 1)) as dev:
        events = _run(driver, dev, [
            [(EV_ABS, ABS_X, 17), (EV_ABS, ABS_Y, 9)],
            [(EV_ABS, ABS_X, W - 1), (EV_ABS, ABS_Y, H - 1)],
            [(EV_KEY, BTN_LEFT, 1)],
        ], tmp_path)
    assert events == [
        ("MOVE", 17.0, 9.0, 0),
        ("MOVE", float(W - 1), float(H - 1), 0),
        ("CLICK", float(W - 1), float(H - 1), 0),
    ], events


@pytest.mark.uinput
@LAB
def test_touchscreen_maps_its_range_onto_the_screen_without_a_cursor(driver, tmp_path):
    ABS_X, ABS_Y, EV_ABS, EV_KEY, BTN_TOUCH = (
        evdev_lab.ABS_X, evdev_lab.ABS_Y, evdev_lab.EV_ABS, evdev_lab.EV_KEY, evdev_lab.BTN_TOUCH)
    with evdev_lab.UinputDevice("ic949-touch", abs=(4095, 4095), touch=True) as dev:
        events = _run(driver, dev, [
            [(EV_ABS, ABS_X, 2048), (EV_ABS, ABS_Y, 1024)],
            [(EV_ABS, ABS_X, 4095), (EV_ABS, ABS_Y, 4095)],
            [(EV_KEY, BTN_TOUCH, 1)],
        ], tmp_path)
    # הטווח כולו נמתח על 0..w-1: אמצע → (2048/4095)·63, ‏max → הפיקסל האחרון
    # בדיוק (ולא w, שרק ה-clamp החזיר למסך); ‏is_touch=1: בלי סמן.
    assert [(k, t) for k, _x, _y, t in events] == [("MOVE", 1), ("MOVE", 1), ("CLICK", 1)], events
    assert events[0][1:3] == pytest.approx((2048 / 4095 * (W - 1), 1024 / 4095 * (H - 1)), abs=0.001)
    assert events[1][1:3] == (float(W - 1), float(H - 1)), events
    assert events[2][1:3] == (float(W - 1), float(H - 1)), events


@pytest.mark.uinput
@LAB
def test_relative_mouse_is_unchanged(driver, tmp_path):
    """השומר מפני תיקון-יתר: עכבר פיזי עדיין יחסי, עם PTR_GAIN, מהמרכז."""
    EV_REL, REL_X, REL_Y = evdev_lab.EV_REL, evdev_lab.REL_X, evdev_lab.REL_Y
    with evdev_lab.UinputDevice("ic949-mouse", rel=True) as dev:
        events = _run(driver, dev, [[(EV_REL, REL_X, 2), (EV_REL, REL_Y, -1)]], tmp_path)
    assert events == [("MOVE", W / 2 + 2 * 3, H / 2 - 1 * 3, 0)], events


@pytest.mark.uinput
@LAB
def test_a_move_says_whether_the_button_is_held(driver, tmp_path):
    """#2778: גרירת מקלדת המסך במתקין. תזוזה עם הכפתור לחוץ נושאת held=1, ואחרי שחרורו held=0.
    השחרור עצמו אינו אירוע: אף מסך שלא מסתכל על held לא רואה דבר חדש."""
    ABS_X, ABS_Y, EV_ABS, EV_KEY, BTN_LEFT = (
        evdev_lab.ABS_X, evdev_lab.ABS_Y, evdev_lab.EV_ABS, evdev_lab.EV_KEY, evdev_lab.BTN_LEFT)
    with evdev_lab.UinputDevice("ic2778-drag", abs=(W - 1, H - 1)) as dev:
        events = _run(driver, dev, [
            [(EV_ABS, ABS_X, 10), (EV_ABS, ABS_Y, 5)],
            [(EV_KEY, BTN_LEFT, 1)],
            [(EV_ABS, ABS_X, 20)],
            [(EV_KEY, BTN_LEFT, 0)],
            [(EV_ABS, ABS_X, 30)],
        ], tmp_path, held=True)
    assert events == [
        ("MOVE", 10.0, 5.0, 0, 0),
        ("CLICK", 10.0, 5.0, 0, 0),
        ("MOVE", 20.0, 5.0, 0, 1),
        ("MOVE", 30.0, 5.0, 0, 0),
    ], events
