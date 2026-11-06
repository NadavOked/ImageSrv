"""#1912 / #683: מקלדת או עכבר שמתעוררים אחרי `input_open` נתפסים ב-`input_rescan`.

‏#683: מחשב בנייה עלה למסך טקסט כי ‏`input_open` סרק את `/dev/input` פעם אחת,
שנייה-שתיים אחרי האתחול, לפני שמקלדת ה-USB הופיעה — והגואי יצא. התיקון:
הגואי לא יוצא בלי קלט, ו-`input_rescan` (פעם בשנייה) תופס התקן מאוחר.

כאן בודקים את הצד של `input.c` — שהתקן שנוצר **אחרי** הפתיחה אכן נתפס, ושהוא
מספק אירועים: מקלדת (תו) ועכבר יחסי (תזוזה). הצד של `main.c` (שהלולאה קוראת
ל-`input_rescan` ושאין יציאה בלי קלט) אינו נבדק כאן; הוא דורש תצוגה אמיתית.

איך הבדיקה אינה תלויה במכונה: הדרייבר כולל את `input.c` ישירות, קורא
ל-`input_open` האמיתי, ואז **משכיח** את ההתקנים שכבר היו שם (סוגר ומאפס
`ndev` ו-`scan_done`). רק אחרי שההתקן המזויף נוצר הוא מקבל "go" ומתחיל לקרוא
ל-`input_rescan`. מכונה עם מקלדת ועכבר פיזיים אינה משנה את התוצאה: ההתקן
המזויף והישנים נראים באותה סריקה ראשונה.

בקרה שלילית (ראו גוף ה-PR): ‏`input_rescan` שחוזר מיד (התנהגות לפני #683) —
ההתקן לא נתפס, שני המקרים נופלים על "never grabbed". ‏gcc + ‏`/dev/uinput`
כתיב = מעבדה בלבד.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

import evdev_lab
from test_native_gui_input import LAB, INPUT_C

KEY_A, KEY_Z = 30, 44

DRIVER = r'''
#include "%(input_c)s"
#include <stdio.h>
#include <stdlib.h>

/* input_open as production calls it, then forget whatever the machine
 * already had: this test is about a device that was NOT there at open. */
int main(void) {
    Input *in = input_open(64, 32);
    if (!in) return 2;
    for (int i = 0; i < in->ndev; i++) close(in->dev[i].fd);
    in->ndev = 0; in->scan_done = 0;
    printf("opened\n"); fflush(stdout);
    char line[16];
    if (!fgets(line, sizeof line, stdin)) return 3;       /* "go": the late device exists now */
    int reported = 0;
    for (int ms = 0; ms < 8000; ms += 50) {
        input_rescan(in);
        for (; reported < in->ndev; reported++) printf("grabbed %%s\n", in->dev[reported].path);
        fflush(stdout);
        struct pollfd fds[MAX_DEV];
        int n = input_fill_pollfds(in, fds, MAX_DEV);
        if (poll(fds, n, 50) > 0) input_pump(in, fds, n);
        Event e;
        while (input_next(in, &e)) {
            if (e.type == UIEV_CHAR) printf("CHAR %%d\n", e.ch);
            else if (e.type == UIEV_MOVE) printf("MOVE %%.1f %%.1f\n", e.x, e.y);
            else continue;
            fflush(stdout);
            return 0;
        }
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("rescan-drv")
    source = d / "rescan_drv.c"
    source.write_text(DRIVER % {"input_c": INPUT_C.as_posix()})
    binary = d / "rescan_drv"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-std=c11", "-D_GNU_SOURCE", "-o", str(binary), str(source)],
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _wait_for(path: Path, needle: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if needle in path.read_text():
            return True
        time.sleep(0.05)
    return False


def _late_device(driver: Path, tmp_path: Path, make_device, events) -> list[str]:
    """מריץ את הדרייבר, יוצר את ההתקן רק אחרי "opened", ומחזיר את שורות הפלט."""
    out_path = tmp_path / "out.txt"
    with open(out_path, "wb") as out:
        proc = subprocess.Popen([str(driver)], stdin=subprocess.PIPE, stdout=out,
                                stderr=subprocess.DEVNULL, text=False)
        try:
            assert _wait_for(out_path, "opened", 5), "הדרייבר לא סיים input_open"
            with make_device() as dev:
                proc.stdin.write(b"go\n")
                proc.stdin.flush()
                grabbed = _wait_for(out_path, f"grabbed {dev.node}", 6)
                assert grabbed, (f"התקן שנוצר אחרי input_open ({dev.node}) לא נתפס אי פעם — "
                                 f"input_rescan לא רץ או לא מצא אותו:\n{out_path.read_text()}")
                dev.emit(*events)
                proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    return out_path.read_text().splitlines()


@pytest.mark.uinput
@LAB
def test_a_keyboard_that_appears_after_open_is_grabbed_and_types(driver, tmp_path):
    lines = _late_device(
        driver, tmp_path,
        lambda: evdev_lab.UinputDevice("ic683-late-keyboard", keys=(KEY_A, KEY_Z)),
        [(evdev_lab.EV_KEY, KEY_A, 1)])
    assert "CHAR 97" in lines, lines                       # 'a'


@pytest.mark.uinput
@LAB
def test_a_mouse_that_appears_after_open_is_grabbed_and_moves_the_pointer(driver, tmp_path):
    lines = _late_device(
        driver, tmp_path,
        lambda: evdev_lab.UinputDevice("ic683-late-mouse", rel=True),
        [(evdev_lab.EV_REL, evdev_lab.REL_X, 2)])
    # 64x32 screen, pointer starts at the centre; REL 2 * PTR_GAIN(3) = 6 px right
    assert "MOVE 38.0 16.0" in lines, lines
