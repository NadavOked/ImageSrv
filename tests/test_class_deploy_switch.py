"""‏#880: מתג "הפצה לכיתות ממחשב הבנייה" — הצד של ה-GUI.

‏v1 "מהדורת שיכפול": הכרטיס "הפצה לכיתות" מוצג רק כשהשרת אמר ב-hello
‏`class_deploy_enabled: true`. הצד של השרת נבדק ב-`test_server_api.py`
ו-`test_station.py`, תפריט הטקסט ב-`test_build_menu.py`; כאן שני
החוליות שביניהם:

* ‏`gui_state` (‏`agent/lib/gui/guistate.sh`) כותב `menu_class=0|1` לקובץ
  המצב — **תמיד**, גם כשכבוי, כדי שהיעדר רשומה לא יקרא כ"דלוק".
  ‏#1073: ב-v1 הערך הוא 0 **גם כשהשרת אמר true** — כיתות = v2, והשער
  `classrooms` (hello/state, #1081) כבוי; המסלול של v2 נבדק עם הדגל
  דלוק ב-hello, כדי שהדלקתו תחזיר הכול.
* ‏#1073: `gui_state` כותב גם `machine_name=` — השם הרשום של המכונה
  (‏`.name` מ-`/api/v1/agent/state`), האישור המוקלד של מסך השחזור.
* ‏`state.c` מפרסר את הרשומה ו-`screens.c` מסתיר את הכרטיס — נמדד
  בפיקסלים של `--png` (על המעבדה; בווינדוס אין מהדר).

**בקרה שלילית** (אומתה 16/09): על main ‏`gui_state` אינו כותב
‏`menu_class` כלל, והכרטיס מצויר תמיד — שני הטסטים נופלים על התנהגות.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_cloner_gui import (BASH, GUI, _build_gui, _count_color, _pkgconfig,
                             _png_rgb, posix, sh)

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

#: ‏gui_state קורא את station.json מ-`http_get` — כאן הוא מוחלף בקובץ,
#: כי מה שנבדק הוא הרשומה שנכתבת, לא התעבורה.
STATION = {"known": True, "role": "build", "disks": [], "task": None,
           "allowed_images": []}

PRELUDE = (
    f'. {posix(AGENT)}/lib/core/common.sh; '
    f'. {posix(AGENT)}/lib/core/jsonq.sh; '
    f'. {posix(AGENT)}/lib/gui/buildmenuitems.sh; . {posix(AGENT)}/lib/gui/buildmenu.sh; '
    f'. {posix(AGENT)}/lib/gui/guistate.sh; . {posix(AGENT)}/lib/gui/askgui.sh; '
    'disk_slot_records() { :; }; '   # #1495: disk numbering is test_drawer_map_gui_1495's
)


def state_records(tmp_path: Path, hello: dict | None, *, env: str = "",
                  station: dict = STATION, hello_state: str | None = None,
                  shell_pre: str = "") -> list[str]:
    run = tmp_path / "run"; run.mkdir(parents=True)
    gui = tmp_path / "gui"; gui.mkdir(parents=True)
    (run / "station.json").write_text(json.dumps(station), newline="\n")
    if hello is not None:
        (run / "response.json").write_text(json.dumps(hello), newline="\n")
    if hello_state is not None:
        (run / "hello.state").write_text(hello_state + "\n", newline="\n")
    (gui / "mode").write_text("menu\n", newline="\n")
    script = (
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
        f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1 {env}; '
        + PRELUDE
        + shell_pre
        + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
        + 'gui_state'
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text().splitlines()


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_carries_the_switch_from_the_last_hello(tmp_path):
    """‏(v2, הדגל דלוק) hello אמר true → `menu_class=1`; אמר false →
    `menu_class=0`. זה המסלול ש-v2 מדליק — נשמר עובד."""
    on = state_records(tmp_path / "on",
                       {"class_deploy_enabled": True, "classrooms": True})
    assert "menu_class=1" in on, on
    off = state_records(tmp_path / "off",
                        {"class_deploy_enabled": False, "classrooms": True})
    assert "menu_class=0" in off, off


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_v1_pins_the_class_card_off_whatever_the_server_says(tmp_path):
    """‏#1081: בלי `classrooms: true` (ברירת המחדל = v1) הכרטיס כבוי גם
    כשה-hello אמר `class_deploy_enabled` true — כיתות = v2. **בקרה
    שלילית:** בלי בדיקת `.classrooms` ב-`class_deploy_on` נכתב
    `menu_class=1` והטסט נופל."""
    on = state_records(tmp_path / "on", {"class_deploy_enabled": True})
    assert "menu_class=0" in on, on
    assert "menu_class=1" not in on


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_writes_the_registered_machine_name(tmp_path):
    """‏#1073: `machine_name=` הוא `.name` מהמצב — האישור המוקלד של מסך
    השחזור. ‏null (מכונה בלי שם) → הרשומה נכתבת **ריקה**, לא מושמטת."""
    named = state_records(tmp_path / "named", None,
                          station={**STATION, "name": "BUILD-01"})
    assert "machine_name=BUILD-01" in named, named
    unnamed = state_records(tmp_path / "unnamed", None,
                            station={**STATION, "name": None})
    assert "machine_name=" in unnamed, unnamed


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_writes_off_when_the_hello_has_no_field_or_no_file(tmp_path):
    """שדה חסר, וגם קובץ hello שאינו קיים — `menu_class=0` **נכתב**, לא
    מושמט: ה-C מפרסר היעדר כ-0 ממילא, אבל "לא ידענו" אינו "דלוק"."""
    absent = state_records(tmp_path / "absent", {"role": "build"})
    assert "menu_class=0" in absent, absent
    no_file = state_records(tmp_path / "nofile", None)
    assert "menu_class=0" in no_file, no_file


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_distinguishes_no_hello_from_a_completed_http_answer(tmp_path):
    """#1154 negative control: the writer previously emitted neither key."""
    never = state_records(tmp_path / "never", None, shell_pre='date() { echo 100; }; ')
    assert "hello_age=" in never and "hello_rc=" in never
    assert "hello_age=0" not in never and "hello_rc=0" not in never

    answered = state_records(tmp_path / "answered", None, hello_state="90|204",
                             shell_pre='date() { echo 100; }; ')
    assert "hello_age=10" in answered
    assert "hello_rc=204" in answered


# --- הצד הגרפי: הכרטיס נעלם מהתפריט --------------------------------------------


def _menu_ink(tmp_path: Path, state_text: str) -> int:
    """מרנדר את כל הכרטיסים ממצב נתון ומחזיר את מספר פיקסלי הדיו
    (‏`theme.light.ink`) בכרטיס התפריט — הטקסט של הכרטיסים המוצגים."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    binary = _build_gui(tmp_path)
    state = tmp_path / "state.txt"
    state.write_text(state_text + "message=x|y\n", newline="\n")
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", "1000x760",
         "--mac", "3C:52:82:A1:00:21", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, run.stderr
    w, h, ch, px = _png_rgb(tmp_path / "card-menu-light.png")
    return _count_color(px, ch, (0x31, 0x31, 0x31))     # theme.light.ink


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_the_class_card_is_drawn_only_when_menu_class_is_on(tmp_path):
    """‏`menu_class=1` מצייר ארבעה כרטיסים, `menu_class=0` שלושה, ורשומה
    חסרה שווה ל-0. נמדד בדיו: הכותרת "הפצה לכיתות" ותת-הכותרת שלה הן
    מאות פיקסלים כהים שנעלמים עם הכרטיס."""
    shown = _menu_ink(tmp_path / "on", "menu_class=1\n")
    hidden = _menu_ink(tmp_path / "off", "menu_class=0\n")
    absent = _menu_ink(tmp_path / "absent", "")
    assert shown - hidden > 200, (shown, hidden)
    assert absent == hidden, (absent, hidden)
