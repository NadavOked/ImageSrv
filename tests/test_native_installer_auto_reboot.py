"""‏#1250: מסך "ההתקנה הושלמה" מפעיל מחדש לבד אחרי ספירה לאחור, כמו ESXi.

לפני התיקון המסך חיכה ללחיצה בלי הגבלת זמן ובלי ספירה. החוזה שנבדק כאן:

* פקיעת הספירה מפעילה את **אותו** מסלול של הכפתור ("eject" ואז
  ‏"reboot" דרך הגשר) — **פעם אחת**, גם אם ה-reboot חזר בלי לאתחל.
* "בטל" (לחיצה) ו-Esc עוצרים את הספירה; הכפתור "הפעל מחדש" ממשיך לעבוד.
* ‏Enter / "הפעל מחדש" מפעילים מיד, והטיימר לא יורה שוב אחריו.
* ‏eject שנכשל נרשם ב-stderr ואינו עוצר את ההפעלה מחדש (עיקרון 5).
* ‏`--png` מצייר את כרטיס הספירה במסך הסיום.

השעון מוזרק: ‏`reboot_tick(a, b, now)` מקבל את הזמן כארגומנט, והדרייבר
כולל את `install.c` ישירות (‏`#include`) כמו `test_native_installer_keyboard.py`.
הגשר הוא `cat; echo >>LOG` — כל פעולה שהמתקין שלח נרשמת.
‏cc + כותרות cairo = לינוקס בלבד; בווינדוס זה דילוג, לא מעבר.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_native_installer_gui import _build, _size_and_ink, NATIVE as NATIVE_GUI

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("cairo", _pkgconfig("cairo")),
    posix=True, why="install.c נבנה ומורץ על לינוקס בלבד",
)

DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

static int g_hit;
int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return g_hit; }

static void show(const App *a, const char *tok) {
    const InstallState *s = &a->install;
    printf("%%s view=%%d armed=%%d left=%%d poll=%%d\n", tok, s->view,
           s->reboot_at > 0, s->reboot_left, install_polling(a));
}

static App app;

/* argv: BRIDGE-CMD TOKEN... -- the demo state on the done screen, then one
 * line of state after every token. arm@T / tick@T take the clock as T. */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    install_demo(a, INSTALL_DONE);
    show(a, "start");
    for (int i = 2; i < argc; i++) {
        const char *t = argv[i];
        Event e = { 0 };
        if (!strncmp(t, "arm@", 4)) { arm_reboot(&a->install, atof(t + 4)); show(a, t); continue; }
        if (!strncmp(t, "tick@", 5)) { reboot_tick(a, &b, atof(t + 5)); show(a, t); continue; }
        if (!strcmp(t, "click-cancel") || !strcmp(t, "click-restart")) {
            g_hit = !strcmp(t, "click-cancel") ? HIT_INSTALL_CANCEL_REBOOT : HIT_INSTALL_NEXT;
            e.type = UIEV_CLICK;
        } else {
            e.type = UIEV_KEY;
            e.key = !strcmp(t, "ENTER") ? KEYSYM_ENTER : !strcmp(t, "ESC") ? KEYSYM_ESC : 0;
            if (!e.key) { fprintf(stderr, "unknown token %%s\n", t); return 2; }
        }
        install_handle(a, &b, &e);
        show(a, t);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """נבנה פעם אחת למודול; הסימון `NATIVE` מדלג/נופל לפני שמגיעים לכאן."""
    tmp = tmp_path_factory.mktemp("install-reboot")
    source = tmp / "install_reboot.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_reboot"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _run(driver: Path, tmp_path: Path, *tokens: str, fail_eject: bool = False):
    """מריץ את הרצף ומחזיר (מצב אחרי כל צעד, פעולות הגשר, stderr)."""
    log = tmp_path / "bridge.log"
    log.write_text("")
    # cat קורא את ה-JSON שהגואי כותב, כדי שהגואי לא יכתוב לצינור שנסגר
    # (SIGPIPE) כשהגשר יוצא לפני שהכתיבה הגיעה — כאן יש שתי קריאות ברצף.
    bridge = f'cat >/dev/null; echo >>"{log}"'
    if fail_eject:  # הגשר רושם כל פעולה, ו-eject יוצא 1
        bridge = f'f(){{ cat >/dev/null; echo "$1" >>"{log}"; [ "$1" != eject ]; }}; f'
    proc = subprocess.run([str(driver), bridge, *tokens], capture_output=True, text=True,
                          timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    states = []
    for line in proc.stdout.splitlines():
        tok, *pairs = line.split()
        state = dict(pair.split("=", 1) for pair in pairs)
        state["key"] = tok
        states.append(state)
    assert [s["key"] for s in states] == ["start", *tokens], proc.stdout
    return states, log.read_text().split(), proc.stderr


def _col(states: list[dict], name: str) -> list[str]:
    return [s[name] for s in states]


@NATIVE
def test_countdown_runs_out_and_restarts_through_the_button_path_once(driver, tmp_path):
    """‏reboot שחזר (הגשר כאן לא מאתחל) לא נשלח שוב בכל tick."""
    states, bridge, _ = _run(driver, tmp_path, "arm@1000", "tick@1000.2", "tick@1030",
                             "tick@1089.5", "tick@1090", "tick@1091", "tick@5000")
    assert _col(states, "left") == ["90", "90", "90", "60", "1", "0", "0", "0"]
    assert _col(states, "armed") == ["1", "1", "1", "1", "1", "0", "0", "0"]
    assert _col(states, "poll") == ["1", "1", "1", "1", "1", "0", "0", "0"], \
        "הלולאה מתעוררת כל שנייה רק בזמן הספירה"
    assert bridge == ["eject", "reboot"], "פקיעה = מסלול הכפתור, פעם אחת"


@NATIVE
def test_escape_stops_the_countdown(driver, tmp_path):
    states, bridge, _ = _run(driver, tmp_path, "arm@1000", "tick@1010", "ESC",
                             "tick@1090", "tick@9999")
    assert _col(states, "armed") == ["1", "1", "1", "0", "0", "0"]
    assert bridge == [], "Esc ביטל — שום reboot לא נשלח"


@NATIVE
def test_cancel_button_stops_the_countdown_and_restart_still_works(driver, tmp_path):
    """אם "בטל" לא עצר, ה-tick ב-1090 היה שולח reboot, והלחיצה עוד אחד."""
    states, bridge, _ = _run(driver, tmp_path, "arm@1000", "click-cancel", "tick@1090",
                             "tick@9999", "click-restart")
    assert _col(states, "armed") == ["1", "1", "0", "0", "0", "0"]
    assert bridge == ["eject", "reboot"]


@NATIVE
def test_enter_restarts_at_once_and_the_timer_does_not_fire_after_it(driver, tmp_path):
    states, bridge, _ = _run(driver, tmp_path, "arm@1000", "tick@1001", "ENTER",
                             "tick@1090", "tick@2000")
    assert _col(states, "armed") == ["1", "1", "1", "0", "0", "0"]
    assert bridge == ["eject", "reboot"]


@NATIVE
def test_failed_eject_is_logged_and_the_restart_still_goes_out(driver, tmp_path):
    """עיקרון 5: eject שנכשל נרשם — לא נבלע, ולא עוצר את ההפעלה מחדש."""
    _, bridge, stderr = _run(driver, tmp_path, "arm@1000", "tick@1090", fail_eject=True)
    assert bridge == ["eject", "reboot"]
    assert "native-gui: eject:" in stderr and "eject" in stderr.split("native-gui: eject:", 1)[1]


@NATIVE_GUI
def test_done_card_draws_the_countdown(tmp_path) -> None:
    """ב-1280x800 (קנה מידה 1; 1280x1024 מוגדל פי 1.25 מאז מוקאפ 10/10): העמודה
    האחת (760 ממורכזת) מתחילה ב-(135,105),
    הכרטיס נגמר ב-479 (‏#2144: שורת "שעון השרת" הוסיפה לו 32), ושורת הספירה
    ב-495–553; הטקסט נגמר ב-875 ו"בטל" ב-145. לפני #1250 האזור הזה היה רקע
    בלבד — צבע אחד."""
    binary = _build(tmp_path)
    prefix = tmp_path / "reboot"
    env = dict(os.environ)
    if (REPO / "native-gui" / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(REPO / "native-gui" / "fonts.conf")
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                           str(prefix), "--size", "1280x800"], capture_output=True,
                          text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    for theme in ("light", "dark"):
        path = tmp_path / f"reboot-install-6-done-{theme}.png"
        _, _, text = _size_and_ink(path, (700, 502, 870, 547))
        assert text >= 8, f"{theme}: no countdown text in the done card ({text} colors)"
        _, _, cancel = _size_and_ink(path, (150, 510, 190, 540))
        assert cancel > 1, f"{theme}: no Cancel button next to the countdown"


def test_countdown_contract_in_the_source() -> None:
    """רץ גם בווינדוס: קבוע אחד, טקסט הספירה, ועצירה לפני ה-reboot."""
    state_h = (SRC / "install_state.h").read_text(encoding="utf-8")
    control = (SRC / "install.c").read_text(encoding="utf-8")
    final = (SRC / "screens_install_final.c").read_text(encoding="utf-8")
    assert re.search(r"^#define INSTALL_AUTO_REBOOT_S 90$", state_h, re.M)
    assert "s->reboot_at=now+INSTALL_AUTO_REBOOT_S;s->reboot_left=INSTALL_AUTO_REBOOT_S;" in control
    assert '"הפעלה מחדש אוטומטית בעוד %d שניות",s->reboot_left' in final
    assert "HIT_INSTALL_CANCEL_REBOOT" in final and "HIT_INSTALL_CANCEL_REBOOT)cancel_reboot(s)" in control
    assert "if(s->view==INSTALL_DONE)cancel_reboot(s);else back(a);" in control, "Esc עוצר"
    restart = next(line for line in control.splitlines() if "static void reboot_or_report" in line)
    assert restart.index("cancel_reboot(s)") < restart.index('call(a,b,"eject"') < restart.index('call(a,b,"reboot"')
    assert "אין אתחול אוטומטי" not in final, "מסך הסיכום לא מבטיח עוד שאין אתחול אוטומטי"
    assert "הסר את מדיית ההתקנה" in final
