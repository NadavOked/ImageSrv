"""‏#1331 T8 — שורת `drivers.linux` בגואי הנייטיב ובקונסולה.

`drivers.linux` (בדיקת ה-initramfs ביעד לינוקס, docs/interfaces.md סעיף 4/17)
נכנס ל-rc6 ב-#1688 ולא הוצג באף מסך. עכשיו הוא שורה מתחת לפס של המגירה, באותו
מקום ובאותו סגנון כמו שורת ה-Windows של #1710:

* **הקונסולה** — `linuxDriversText` / `driversTone` / `driversHtml` ב-`console.js`
  (טסטי ה-node ב-`linux_drivers_console_1331.test.cjs`).
* **הגואי** — `lc_bay_linux` ב-`lcbay.c`. השדות על החוט **אחרי** שדה 17 של #1710:
  `drawer=` שדות 18–20 (`drvgui.sh`), `room_drawer=` שדות 24–26 (`guistate.sh`),
  כל אחד רק כשיש אובייקט `linux`.
* **אותן מילים** — הטסט הראשון משווה את מחרוזות ה-C למחרוזות ה-JS, כדי ששני
  המסכים לא ינסחפו.

רק `generic` הוא ראיה שהדיסק יעלה על bus אחר (אפור); `hostonly` / `unsupported` /
`failed` כתום; `skipped` עם `unknown_bus` נאמר בגלוי.

**בקרה שלילית:** על rc6 אין `lc_bay_linux`, אין `drv_lstate` ואין `linuxDriversText`;
ה-jq לא מוסיף שדות — כל הטסטים כאן נופלים.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import (AGENT, BASH, PORTS, PRELUDE as CLONER_PRELUDE, _build_gui, _pkgconfig,
                             env, posix, sh, target)
from test_drivers_line_1331 import BUILD, CLONER_PLAIN, _cloner_png, _count, _done
from test_room_drivers_gui_contract import _drawer, _state
from test_room_precheck_native_432_65 import _render

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "native-gui" / "src"
JQ = requires_native(("bash", BASH), "jq", why="drvgui.sh ו-gui_state קוראים JSON ב-jq")
_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _norm(s: str) -> str:
    return re.sub(r"%s|\$\{[^}]*\}", "§", s)


def _fn(text: str, head: str) -> str:
    i = text.index(head)
    return text[i:text.index("\n}\n", i)]


# --- אותן מילים בשני המסכים --------------------------------------------------------


def test_the_gui_and_the_console_say_the_same_words():
    c = _fn((SRC / "lcbay.c").read_text(encoding="utf-8"), "void lc_bay_linux(")
    js = _fn((ROOT / "server/static/console.js").read_text(encoding="utf-8"), "function linuxDriversText(")
    c_words = {_norm(m) for m in re.findall(r'"(לינוקס:[^"]*)"', c)}
    js_words = {_norm(m) for m in re.findall(r'[`"](לינוקס:[^`"]*)[`"]', js)}
    assert len(c_words) == 6, c_words
    assert c_words == js_words, (c_words ^ js_words)
    assert "קובץ האתחול כולל רק את בקר הדיסק של המחשב המקורי — עלול לא לעלות כאן" in " ".join(c_words)


def test_only_generic_and_skipped_are_grey_the_rest_orange_and_never_red():
    c = _fn((SRC / "lcbay.c").read_text(encoding="utf-8"), "void lc_bay_linux(")
    assert "y->drv_tone = DRV_LWARN;" in c and "DRV_LGREY" in c
    assert re.search(r'!strcmp\(state, "generic"\) \|\| !strcmp\(state, "skipped"\)\) y->drv_tone = DRV_LGREY', c)
    assert "DRV_BAD" not in c and "pill" not in c, "no red, no pill: the restore is done and the disk may well boot"


def test_the_linux_tones_stay_out_of_the_windows_summary_and_get_the_tall_row():
    for name in ("screens_cloner.c", "screens_room.c"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert "dg.drv_tone <= DRV_BAD" in text, f"{name}: the 'placed / without' summary counts Windows tones only"
        assert "dv_line ? LC_BAY_DRV_H" in text, f"{name}: a Linux line needs the row with room for it"
        assert "lc_bay_linux(y, " in text, name


def test_the_parser_reads_linux_after_field_17_and_validates_the_word():
    s = (SRC / "state.c").read_text(encoding="utf-8")
    assert "drv_linux_fields(d->drv_lstate, d->drv_lmod, d->drv_ldet, f + 23)" in s   # room_drawer 24-26
    assert "drv_linux_fields(d->drv_lstate, d->drv_lmod, d->drv_ldet, f + 17)" in s   # drawer= 18-20
    assert "nf >= 26" in s and "split(val, f, 27)" in s and "split(val, f, 20)" in s   # #1742: 27 = smart reason
    body = _fn(s, "static void drv_linux_fields(")
    for word in ("generic", "hostonly", "unsupported", "failed", "skipped"):
        assert f'"{word}"' in body


# --- החוט: jq ---------------------------------------------------------------------


@JQ
def test_room_drawer_carries_linux_after_the_boot_fields(tmp_path):
    room = {
        "round": {"image_name": "debian", "wave_number": 1, "wave_state": "closed",
                  "written_drives": 3, "target_drives": 3, "ready_drives": 0,
                  "remaining_drives": 0, "elapsed_s": 9, "rate_bps": None, "eta_s": None},
        "machines": [{
            "name": "2", "mac": "78:ac:c0:9b:11:c2", "awake": True, "joined": True,
            "fresh_drawers": 3, "state": "done", "drawer_count": 3,
            "drawer_list": [
                _drawer(1, "S1", {"state": "skipped", "packages": [], "reason": "not_windows",
                                  "linux": {"state": "hostonly", "module": "ahci", "kver": "6.12"}}),
                _drawer(2, "S2", {"state": "skipped", "packages": [], "reason": "not_windows",
                                  "linux": {"state": "skipped", "reason": "unknown_bus"}}),
                _drawer(3, "S3", {"state": "staged", "packages": ["a"], "linux": "junk"}),
            ]}]}
    rows = {r[1]: r for r in (line.split("=", 1)[1].split("|")
                              for line in _state(tmp_path, room, []) if line.startswith("room_drawer="))}
    assert rows["1"][20:] == ["", "", "", "hostonly", "ahci", ""], rows["1"]
    assert rows["2"][23:] == ["skipped", "", "unknown_bus"], rows["2"]
    assert len(rows["3"]) == 23, "no linux object (or junk) = the record as it was: 23 fields"


def _cloner_lines(tmp_path: Path, drivers: dict[str, str]) -> list[str]:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"debian"}', newline="\n")
    for dev in ("sda", "sdb", "sdc"):
        target(run, dev, "done", 100, 100, "")
        if dev in drivers:
            (run / "targets" / dev / "drivers.json").write_text(drivers[dev], encoding="utf-8", newline="\n")
    out = sh(env(run, gui) + CLONER_PRELUDE + f'. {posix(AGENT)}/lib/gui/drvgui.sh; ' + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    return [x for x in lines if x.startswith("drawer=")]


@JQ
def test_the_cloner_drawer_carries_linux_as_fields_18_to_20(tmp_path):
    lines = _cloner_lines(tmp_path, {
        "sda": json.dumps({"state": "skipped", "packages": [], "reason": "not_windows",
                           "linux": {"state": "unsupported", "error": "Alpine | 3.20"}}),
        "sdb": json.dumps({"state": "skipped", "packages": [], "reason": "not_windows"}),
    })
    by_dev = {x.split("|")[1]: x.split("|") for x in lines}
    assert len(by_dev["sda"]) == 20 and by_dev["sda"][12:14] == ["skipped", "not_windows"], by_dev["sda"]
    assert by_dev["sda"][17:] == ["unsupported", "", "Alpine   3.20"], "a | in the error cannot split the record"
    assert len(by_dev["sdb"]) == 17, "no linux object = seventeen fields, as #1710 wrote them"
    assert len(by_dev["sdc"]) == 12, "no drivers.json = the step did not run"


# --- הגואי: צבעים ----------------------------------------------------------------


def _linux(state: str, mod: str = "", det: str = "") -> str:
    return f"|skipped|not_windows||||{state}|{mod}|{det}"   # fields 13-17, then 18-20


@_GUI
def test_the_cloner_rows_show_the_linux_line_orange_or_grey(tmp_path):
    binary = _build_gui(tmp_path)

    def png(name, drv):
        return _cloner_png(binary, tmp_path, name,
                           "".join(_done(i + 1, d, drv) for i, d in enumerate(("sda", "sdb", "sdc"))))

    plain = _cloner_png(binary, tmp_path, "plain", CLONER_PLAIN)
    host, gen = png("host", _linux("hostonly", "ahci")), png("gen", _linux("generic", "nvme"))
    assert _count(host, "--clr-warning") > _count(plain, "--clr-warning") + 40, "hostonly: the orange LED and line"
    assert _count(gen, "--clr-warning") < 40, "generic: grey -- positive evidence, nothing to warn"
    assert _count(host, "--clr-danger") < 100, "a Linux warning is never the red of an unbootable Windows disk"


@_GUI
def test_the_build_screen_shows_the_linux_line_orange(tmp_path):
    binary = _build_gui(tmp_path)
    b = "||||hostonly|ahci|"   # fields 21-23 empty (boot), 24-26 = linux
    _render(binary, BUILD.format(d2="|", d3="|", b2="", b3=""), tmp_path / "plain")
    _render(binary, BUILD.format(d2="skipped|not_windows", b2=b, d3="skipped|not_windows", b3=b), tmp_path / "host")
    plain, host = (tmp_path / f"{n}-room-live-light.png" for n in ("plain", "host"))
    assert _count(host, "--clr-warning") > _count(plain, "--clr-warning") + 40
    assert _count(host, "--clr-danger") < _count(plain, "--clr-danger") + 100
