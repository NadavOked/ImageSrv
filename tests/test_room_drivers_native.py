"""#1269 שלב 2 — הגואי הנייטיב: "להוסיף דרייברים" במסך החדר והפצה ישירה.

הצד של ה-C, לפי המוקאפ (`docs/design/apple-mockups/native-full.html`,
drvBlock/dtag/dmap): מתג כבוי כברירת מחדל; רשימת הדגמים מקובץ המצב
(`driver_models=` / `driver_model=`, החוזה ב-`test_room_drivers_gui_contract.py`);
שני מצבים — "כל הדיסקים שנבחרו" (דגם אחד) או "לבחור דיסקים", שבו לחיצה על
תגית של דיסק מחילה את "דגם לסימון" ולחיצה נוספת — "בלי"; שורת סיכום; הרשומה
`drivers=` בפתיחת הסבב; ותוצאת הנחת הדרייברים לכל מגירה בסבב החי.

**אין "אוטומטי"**: החומרה שזוהתה היא של המשכפל, לא של המכונה שתקבל את
הדיסק — אין לזה בסיס. רק דגם מפורש או "בלי".

בווינדוס אין מהדר: טסטי המקור רצים כאן, טסטי הרינדור (`--png`) על
ה-Testrunner/מעבדה (`requires_native`).

**בקרה שלילית:** על הבסיס אין `driver_model` בפרסר, אין `drivers=` ברשומה,
אין כרטיס `room-drivers` ואין קובץ `screens_drivers.c` — כל הטסטים כאן נופלים.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_cloner_gui import (GUI, _build_gui, _count_color, _pkgconfig, _png_rgb,
                             _routing_error, _token, posix)

SRC = GUI / "src"
MAC = "a0:48:1c:8a:18:40"


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def code(text: str) -> str:
    """בלי הערות C — הערה יכולה לצטט את הקוד הישן."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def body(text: str, name: str) -> str:
    # a parameter may hold one level of parentheses: char (*buf)[200]
    m = re.search(r"\n(?:static )?[\w][\w \*]*\b" + re.escape(name)
                  + r"\((?:[^;{()]|\([^()]*\))*\)\s*\{", text)
    assert m, f"{name}() is not defined here"
    start = m.end(); depth = 1; i = start
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start:i]


# --- אין "אוטומטי" -----------------------------------------------------------


def test_there_is_no_automatic_model_anywhere_in_the_drivers_code():
    drivers = code(_src("screens_drivers.c"))
    for word in ("אוטומטי", "אוטו", "לפי החומרה"):
        assert word not in drivers, word
    # הרשימה שבבורר היא רשימת הספרייה בלבד — אין שורה מומצאת לפניה.
    options = body(drivers, "room_drv_options")
    assert "a->st.driver_models[i]" in options
    assert options.count("opts[n++]") == 1, "one source of rows: the library's models"


# --- הפרסר -------------------------------------------------------------------


def test_the_parser_reads_the_models_and_says_unread_by_default():
    state = code(_src("state.c"))
    parse = body(state, "state_parse")
    assert "s->driver_models_read = -1;" in parse, "absent = not read, never 'no models'"
    assert 'KEY("driver_models")' in parse and 'KEY("driver_model")' in parse
    # שדות 9–10 של room_drawer: מצב ופירוט; מצב לא מוכר אינו מוצג כאילו הונח.
    # #704: שדה 11 (שם החריץ) נוסף אחריהם, ו-#432 שדה 12 (מצב הקישור) — עדיין אופציונלי מ-8.
    # #1366: a 13th field, the CRC count; #1495: 14-15, the drawer and the connection
    # #1386: 18-20 = dev, cancel text, done; #1331: 21-23 = drivers.boot; T8: 24-26 = drivers.linux; #1742: 27 = smart reason
    assert "int nf = split(val, f, 27);" in parse and "if (nf < 8) continue;" in parse
    assert 'strcmp(f[8], "staged")' in parse and 'strcmp(f[8], "failed")' in parse


# --- הרשומה שנפלטת ------------------------------------------------------------


def test_opening_emits_drivers_only_when_the_switch_is_on():
    main = code(_src("main.c"))
    lib = body(main, "open_round")
    direct = body(main, "open_direct")
    for fn in (lib, direct):
        assert 'emit_kv("drivers", drivers)' in fn
        assert "room_drv_active(a)" in fn
    # בסבב מהספרייה הבדיקה של המתג קודמת לרשומה. #1275: slots= נשלח בכל בחירה,
    # עם דרייברים או בלי (test_room_target_slots_1275.py) — רק drivers= תלוי במתג.
    assert lib.index("room_drv_active(a)") < lib.index('emit_kv("slots", slots)')


def test_the_switch_starts_off_on_every_entry_to_the_room_screens():
    main = code(_src("main.c"))
    choose = body(main, "choose_card")
    for case in ("case HIT_ROOM:", "case HIT_DIRECT:"):
        line = next(ln for ln in choose.splitlines() if case in ln)
        assert "room_drv_reset(a);" in line, line
    assert "a->drv_on = 0;" in body(code(_src("screens_drivers.c")), "room_drv_reset")


def test_a_tag_click_applies_the_model_and_a_second_click_is_none():
    click = body(code(_src("screens_drivers.c")), "room_drv_tag_click")
    assert "drv_same(" in click and 'drv_map_set(a, m->mac, port, "", "")' in click
    assert "drv_map_set(a, m->mac, port, a->drv_vendor, a->drv_model)" in click


# --- מזהי לחיצה ---------------------------------------------------------------


def _enum_values(text: str) -> dict[str, int]:
    body_ = re.search(r"enum \{\s*HIT_NONE = 0,(.*?)\n\};", text, flags=re.S).group(1)
    values, n = {"HIT_NONE": 0}, 0
    for item in re.sub(r"/\*.*?\*/", "", body_, flags=re.S).split(","):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition("=")
        n = int(value) if value.strip() else n + 1
        values[name.strip()] = n
    return values


def test_new_hit_ids_stay_out_of_every_range():
    ui = _src("ui.h")
    v = _enum_values(ui)
    for name in ("HIT_ROOM_DRV_SWITCH", "HIT_ROOM_DRV_MODEL", "HIT_ROOM_DRV_ALL", "HIT_ROOM_DRV_PICK"):
        assert v[name] < v["HIT_DISK_BASE"], (name, v[name])
    sel = 32 * 8
    assert v["HIT_ROOM_DTAG_BASE"] >= v["HIT_INSTALL_NIC_BASE"] + 64
    assert re.search(r"#define MAX_ROOM_SELECTIONS \(MAX_MACHINES \* MAX_DRAWERS\)", ui)
    taken = [(v[k], v[k] + sel) for k in ("HIT_ROOM_DISK_BASE",)] + [(v["HIT_ROOM_MACHINE_BASE"], v["HIT_ROOM_MACHINE_BASE"] + 32)]
    for lo, hi in taken:
        assert not (lo <= v["HIT_ROOM_DTAG_BASE"] < hi)
    # 30 machines x 8 disks, and a tag on each in pick mode: the table must hold both.
    assert int(re.search(r"#define MAX_HITS\s+(\d+)", ui).group(1)) >= 2 * sel + 64


def test_the_room_drivers_card_is_rendered_and_built():
    main = _src("main.c")
    cards = re.search(r"PNG_CARDS\[\]\s*=\s*\{(.*?)\};", main, flags=re.S).group(1)
    assert '"room-drivers"' in cards
    assert "src/screens_drivers.c" in (GUI / "Makefile").read_text(encoding="utf-8")
    readme = (GUI / "README.md").read_text(encoding="utf-8")
    for needle in ("driver_models=", "driver_model=", "drivers=<mac>|<port>|<vendor>|<model>"):
        assert needle in readme, needle


# --- רינדור (Testrunner) -------------------------------------------------------

GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _render(binary: Path, state: Path, prefix: Path) -> None:
    env2 = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env2["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run(
        [str(binary), "--png", posix(prefix), "--size", "1280x800",
         "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2, stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)


def _room_state(tmp_path: Path, name: str, models: int, drv: str = "") -> Path:
    p = tmp_path / f"{name}.txt"
    lines = [
        f"machine=CLONE-01|{MAC}|1|1|2|done|100|1|",
        "machine_drawers=0|2",
        f"room_drawer=0|1|S1|Model A|256060514304|passed|1|1{drv}",
        "room_drawer=0|2|S2|Model B|256060514304|passed|1|1",
        f"driver_models={models}",
        *(["driver_model=HP|HP EliteDesk 800 G6",
           "driver_model=LENOVO|ThinkCentre M70q Gen 3"][:models]),
        "round=office365|1|0|0|2|0|2|-1|-1|-1",
        "message=x|y",
    ]
    p.write_text("\n".join(lines) + "\n", newline="\n")
    return p


@GUI_BUILD
def test_the_switch_is_drawn_on_only_when_the_library_has_models(tmp_path):
    """אותו מצב פעמיים, ההבדל היחיד הוא `driver_models=2` מול `=0`. בכרטיס
    `room-drivers` המתג דלוק — מסילה בצבע הפעולה, הבורר והמקטעים — רק כשיש
    דגמים; עם 0 הוא מושבת עם הסיבה, ואין בו צבע פעולה."""
    binary = _build_gui(tmp_path)
    _render(binary, _room_state(tmp_path, "two", 2), tmp_path / "two")
    _render(binary, _room_state(tmp_path, "none", 0), tmp_path / "none")
    action = _token("--clr-action")
    _w, _h, ch, on = _png_rgb(tmp_path / "two-room-drivers-light.png")
    _w, _h, ch2, off = _png_rgb(tmp_path / "none-room-drivers-light.png")
    a, b = _count_color(on, ch, action, tol=12), _count_color(off, ch2, action, tol=12)
    assert a - b > 300, f"the switch is not drawn on with models ({b} -> {a} px)"


@GUI_BUILD
def test_the_live_round_says_what_happened_to_the_drivers_of_each_drawer(tmp_path):
    """שדות 9–10 של `room_drawer=` — "נכשלו" בצבע האזהרה (השחזור עצמו הושלם:
    כתום, לא אדום), שורה לכל מגירה."""
    binary = _build_gui(tmp_path)
    _render(binary, _room_state(tmp_path, "plain", 2), tmp_path / "plain")
    _render(binary, _room_state(tmp_path, "failed", 2,
                                "|failed|the server has no driver package for the chosen model"),
            tmp_path / "failed")
    warn = _token("--clr-warning")
    _w, _h, ch, base = _png_rgb(tmp_path / "plain-room-live-light.png")
    _w, _h, ch2, failed = _png_rgb(tmp_path / "failed-room-live-light.png")
    before, after = _count_color(base, ch, warn), _count_color(failed, ch2, warn)
    assert after - before > 40, f"no drivers outcome line in the warning colour ({before} -> {after} px)"
