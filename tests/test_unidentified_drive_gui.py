"""‏#1235 (המשך #105): כונן בלי serial בגואי הנייטיב של מחשב הבנייה.

‏`GET /room` מחזיר לכונן כזה `fresh: null` — הסבב סופר לפי serial ואינו
יכול לדעת אם הוא נכתב. מסך הטקסט והקיוסק כבר אומרים "לא ניתן לזהות";
כאן שתי החוליות של הגואי הנייטיב:

* ‏`gui_state` (‏`agent/lib/gui/guistate.sh`) כותב בשדה ה-fresh של
  ‏`room_drawer=` את הערך ‏`-1` עבור `null` — ולא `0`, שהוא "נכתב".
* ‏`state.c` מפרסר את `-1` ו-`screens_room.c` כותב "לא ניתן לזהות את
  הכונן בחריץ N" בצבע האזהרה, במסך ההכנה ובמסך הסבב החי — נמדד
  בפיקסלים של `--png` (על המעבדה וב-CI; בווינדוס אין מהדר).

**בקרה שלילית:** על main ‏`gui_state` כותב `0` ל-`fresh: null`, והגואי
אינו מצייר שום שורת אזהרה — שני הטסטים נופלים על התנהגות.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_class_deploy_switch import PRELUDE, STATION
from test_cloner_gui import (BASH, GUI, _build_gui, _count_color, _pkgconfig,
                             _png_rgb, _routing_error, _token, posix, sh)

MAC = "3C:52:82:A1:00:21"

ROOM = {"round": None, "machines": [{
    "name": "CLONE-01", "mac": MAC, "awake": True, "joined": False,
    "fresh_drawers": 1, "state": None, "drawer_count": 3,
    "drawer_list": [
        {"dev": "sda", "port": 1, "serial": "SER-A", "model": "M", "size_bytes": 1,
         "smart": "ok", "fresh": True, "selected": True},
        {"dev": "sdb", "port": 2, "serial": None, "model": "M", "size_bytes": 1,
         "smart": "ok", "fresh": None, "selected": False},
        {"dev": "sdc", "port": 3, "serial": "SER-C", "model": "M", "size_bytes": 1,
         "smart": "ok", "fresh": False, "selected": False},
    ]}]}


def room_drawer_fresh(tmp_path: Path) -> dict[str, str]:
    """‏gui_state במצב `direct` (מסך החדר בלי בחירת אימג') מול room.json
    קבוע; מחזיר port → שדה ה-fresh של כל רשומת `room_drawer=`."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "station.json").write_text(json.dumps(STATION), newline="\n")
    (run / "room.json").write_text(json.dumps(ROOM), newline="\n")
    (gui / "mode").write_text("direct\n", newline="\n")
    script = (
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
        f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
        + PRELUDE
        + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
        + f'gui_get() {{ cp {posix(run / "room.json")!r} "$2"; }}; '
        + 'gui_state'
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    rows = [line.split("=", 1)[1].split("|")
            for line in (gui / "state").read_text().splitlines()
            if line.startswith("room_drawer=")]
    assert len(rows) == 3, rows
    return {r[1]: r[6] for r in rows}


@requires_native(("bash", BASH), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_writes_minus_one_for_a_drive_with_no_serial(tmp_path):
    fresh = room_drawer_fresh(tmp_path)
    assert fresh["2"] == "-1", "fresh:null must not collapse to 0 -- 0 is 'written'"
    assert fresh["1"] == "1"
    assert fresh["3"] == "0"


def _state(tmp_path: Path, fresh: str) -> Path:
    p = tmp_path / f"room-{fresh}.txt"
    p.write_text(
        f"machine=CLONE-01|{MAC}|1|1|1|writing|-1|0|\n"
        "machine_drawers=0|3\n"
        "room_drawer=0|1|SER-A|Model A|256060514304|passed|1|1\n"
        f"room_drawer=0|2||Model B|500107862016|passed|{fresh}|0\n"
        "round=img|1|0|0|2|0|2|-1|-1|-1\n"
        # --png renders every card; "message" needs msg_title or it falls to the menu.
        "message=x|y\n",
        newline="\n",
    )
    return p


def _render(binary: Path, state: Path, prefix: Path) -> None:
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(prefix), "--size", "1000x760",
         "--mac", MAC, "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_room_screens_say_cannot_identify_for_a_drive_with_no_serial(tmp_path):
    """אותו מצב פעמיים, ההבדל היחיד הוא שדה ה-fresh של חריץ 2: ‏`1` מול
    ‏`-1`. כל המגירות `passed` (ירוק), כך שבמסך ההכנה אין צבע אזהרה
    כלל; במסך החי יש התראת גל כתומה זהה בשני הרינדורים. השורה "לא ניתן
    לזהות את הכונן בחריץ 2" מוסיפה פיקסלים בצבע `--clr-warning` בשניהם."""
    binary = _build_gui(tmp_path)
    _render(binary, _state(tmp_path, "1"), tmp_path / "ok")
    _render(binary, _state(tmp_path, "-1"), tmp_path / "unid")
    warn = _token("--clr-warning")
    for card in ("room", "room-live"):
        _w, _h, ch, base = _png_rgb(tmp_path / f"ok-{card}-light.png")
        _w, _h, ch2, unid = _png_rgb(tmp_path / f"unid-{card}-light.png")
        before, after = _count_color(base, ch, warn), _count_color(unid, ch2, warn)
        assert after - before > 40, (
            f"{card}: no 'cannot identify' line in the warning colour "
            f"({before} -> {after} px)")
