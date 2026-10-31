"""‏#1331 — מוקאפ הדרייברים, גרסה 2 (אושר ע"י נדב 17/10): שורת הדרייברים מתחת
לפס של כל דיסק, במסך "שיכפול במקביל" של מחשב השיכפול ובמסך ההפצה של מחשב
הבנייה — באותה שורת יעד של השיכפול המקומי (‏`lcbay.c`).

שלוש החוליות:
* ‏**מחשב השיכפול** כתב את התוצאה בעצמו, ל-`targets/<dev>/drivers.json`
  (‏`stage_drawer_drivers`). ‏`cloner_gui_state` מצרף אותה לרשומת `drawer=`
  כשדות 13–17, אחרי שדות הביטול 11–12 של #1386 (‏`drvgui.sh`); בלי קובץ — הרשומה **זהה** למה שהייתה (השלב לא
  רץ, לעולם לא "הונחו"); קובץ ש-jq אינו קורא — התמונה אינה מתפרסמת (עיקרון 5).
* ‏**מחשב הבנייה** מקבל את `drawer_list[].drivers` מהשרת; ‏`gui_state` מוסיף
  ל-`room_drawer=` את `drivers.boot` (‏state/service/error) בשדות 21–23, אחרי שדות הביטול 18–20 של #1386.
* ‏**הגואי**: אדום (קצה, פס צד, נורית) רק לדרייבר אחסון שלא הוגדר לעלייה —
  הדיסק עלול לא לעלות; כתום (נורית) ל"לא הונחו" — המחשב יעלה בלעדיהם (#856).

**בקרה שלילית:** על הבסיס (‏integration/v0760-rc1) ‏`drvgui.sh` אינו קיים,
‏`room_drawer=` נגמר בשדה 17, ושורת היעד אינה יודעת על דרייברים — הטסטים
נופלים על התנהגות.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_cloner_gui import (AGENT, BASH, PORTS, PRELUDE as CLONER_PRELUDE, _build_gui, _count_color,
                             _pkgconfig, _png_rgb, _token, env, posix, sh, target)
from test_room_drivers_gui_contract import _drawer, _state
from test_room_precheck_native_432_65 import _render

import shutil

JQ = requires_native(("bash", BASH), "jq", why="drvgui.sh ו-gui_state קוראים JSON ב-jq")
_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

FAILED_BOOT = {"state": "failed", "service": "stornvme", "error": "באימג' אין את השירות stornvme"}


# --- מחשב הבנייה: room_drawer= שדות 21-23 ---------------------------------------


@JQ
def test_room_drawer_carries_the_boot_storage_outcome(tmp_path):
    room = {
        "round": {"image_name": "office365", "wave_number": 2, "wave_state": "closed",
                  "written_drives": 3, "target_drives": 3, "ready_drives": 0,
                  "remaining_drives": 0, "elapsed_s": 842, "rate_bps": None, "eta_s": None},
        "machines": [{
            "name": "2", "mac": "78:ac:c0:9b:11:c2", "awake": True, "joined": True,
            "fresh_drawers": 3, "state": "done", "drawer_count": 3,
            "drawer_list": [
                _drawer(1, "S1", {"state": "staged", "packages": ["hp-800g6-win11"], "boot": FAILED_BOOT}),
                _drawer(2, "S2", {"state": "staged", "packages": ["a", "b"],
                                  "boot": {"state": "enabled", "service": "storahci"}}),
                _drawer(3, "S3", {"state": "failed", "packages": [], "error": "לא הצלחנו לעגן את מחיצת Windows"}),
            ]}]}
    rows = {r[1]: r for r in (line.split("=", 1)[1].split("|")
                              for line in _state(tmp_path, room, []) if line.startswith("room_drawer="))}
    assert len(rows["1"]) == 23, rows["1"]
    assert rows["1"][8:10] == ["staged", "hp-800g6-win11"], "fields 9-10 keep their meaning"
    assert rows["1"][17:20] == ["sda", "", "0"], "#1386's cancel fields stay 18-20"
    assert rows["1"][20:23] == ["failed", "stornvme", "באימג' אין את השירות stornvme"]
    assert rows["2"][20:23] == ["enabled", "storahci", ""]
    assert rows["3"][20:23] == ["", "", ""], "no boot object = not reported, never enabled"


# --- מחשב השיכפול: drawer= שדות 13-17 ---------------------------------------------


PRELUDE = CLONER_PRELUDE + f'. {posix(AGENT)}/lib/gui/drvgui.sh; '


def _cloner_lines(tmp_path: Path, drivers: dict[str, str]) -> tuple[int, list[str], str]:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"office365"}', newline="\n")
    for dev in ("sda", "sdb", "sdc"):
        target(run, dev, "done", 100, 100, "")
        # #1657: target_bytes sums bytes.<N> (the partition counters), not bytes.raw -- without this
        # the record said 0 written for a done drawer and the sdc assertion below could not hold
        (run / "targets" / dev / "bytes.0").write_text("100\n", newline="\n")
        if dev in drivers:
            (run / "targets" / dev / "drivers.json").write_text(drivers[dev], encoding="utf-8", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    state = gui / "state"
    lines = state.read_text(encoding="utf-8").splitlines() if state.exists() else []
    return out.returncode, [line for line in lines if line.startswith("drawer=")], out.stderr


@JQ
def test_the_cloner_drawer_record_carries_its_own_drivers(tmp_path):
    rc, lines, err = _cloner_lines(tmp_path, {
        "sda": json.dumps({"state": "staged", "packages": ["hp-800g6-win11", "realtek-nic"],
                           "boot": {"state": "enabled", "service": "stornvme"}}, ensure_ascii=False),
        "sdb": json.dumps({"state": "failed", "packages": [], "error": "לא הצלחנו | לעגן"}, ensure_ascii=False),
    })
    assert rc == 0, err
    by_dev = {line.split("|")[1]: line for line in lines}
    assert by_dev["sda"].endswith("|1|SATA 0|||staged|hp-800g6-win11, realtek-nic|enabled|stornvme|"), by_dev["sda"]
    assert by_dev["sdb"].endswith("|2|SATA 1|||failed|לא הצלחנו   לעגן|||"), "a | inside the error cannot split the record"
    assert by_dev["sdc"] == "drawer=3|sdc|done|100|100||-1|-1|3|SATA 2||", \
        "no drivers.json = the step did not run: the record is the twelve fields it was (#1386: 11-12)"


@JQ
def test_a_drivers_file_jq_cannot_read_is_not_published_as_a_snapshot(tmp_path):
    rc, lines, _err = _cloner_lines(tmp_path, {"sda": "{not json"})
    assert rc != 0 and lines == [], "an unreadable outcome is not 'did not run' (principle 5)"


def test_the_kiosk_and_the_load_gate_source_drvgui():
    bridge = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    gui_libs = bridge[bridge.index("gui_libs() {"):bridge.index("gui_main() {")]
    assert '. "$LIB_DIR/drvgui.sh"' in gui_libs, "the kiosk runs cloner_gui_state"
    assert '. "$LIB_DIR/drvgui.sh"' in (AGENT / "imagesrv-agent").read_text(encoding="utf-8")


# --- הגואי: השורה, הצבעים, הגובה ----------------------------------------------------

G = 1_000_000_000
CDISKS = ("cdisk=1|sda|Samsung SSD 870 EVO|500107862016|ok||6.0|6.0||1|SATA 0\n"
          "cdisk=2|sdb|WDC WD5000AAKX|500107862016|ok||6.0|6.0||2|SATA 1\n"
          "cdisk=3|sdc|Crucial MX500|500107862016|ok||6.0|6.0||3|SATA 2\n")


def _done(port: int, dev: str, drv: str = "") -> str:
    return f"drawer={port}|{dev}|done|{int(19.5 * G)}|{int(19.5 * G)}||-1|-1|{port}|SATA {port - 1}||{drv}\n"   # #1386: 11-12 = cancel, cancelled_at


CLONER_PLAIN = _done(1, "sda") + _done(2, "sdb") + _done(3, "sdc")
CLONER_DRV = (_done(1, "sda", "|staged|hp-800g6-win11, realtek-nic|enabled|stornvme|")
              + _done(2, "sdb", "|failed|לא הצלחנו לעגן את מחיצת Windows|||")
              + _done(3, "sdc", "|staged|hp-800g6-win11|failed|stornvme|באימג' אין את השירות stornvme"))
CLONER_ALL_OK = (_done(1, "sda", "|staged|a|enabled|stornvme|") + _done(2, "sdb", "|staged|a|enabled|stornvme|")
                 + _done(3, "sdc", "|staged|a|enabled|stornvme|"))


def _cloner_png(binary, tmp_path: Path, name: str, drawers: str, theme: str = "light") -> Path:
    _render(binary, "cloner=office365\n" + CDISKS + drawers + "message=x|y\n", tmp_path / name)
    return tmp_path / f"{name}-cloner-{theme}.png"


def _count(path: Path, token: str, theme: str = "light", tol: int = 6) -> int:
    _w, _h, ch, px = _png_rgb(path)
    return _count_color(px, ch, _token(token, theme), tol=tol)


@_GUI
def test_the_cloner_rows_say_what_happened_to_the_drivers(tmp_path):
    binary = _build_gui(tmp_path)
    plain = _cloner_png(binary, tmp_path, "plain", CLONER_PLAIN)
    drv = _cloner_png(binary, tmp_path, "drv", CLONER_DRV)
    ok = _cloner_png(binary, tmp_path, "ok", CLONER_ALL_OK)
    # the disk that may not boot: a red edge, a red strip and a red LED -- on a
    # done row, which had no red at all
    assert _count(plain, "--clr-danger") < 100
    assert _count(drv, "--clr-danger") > _count(plain, "--clr-danger") + 500
    # not placed: an orange LED and pill (the restore is done -- not red)
    assert _count(drv, "--clr-warning") > _count(plain, "--clr-warning") + 40   # the orange LED
    # all placed and boot-start: no red and no orange, only the grey/ink line
    assert _count(ok, "--clr-danger") < 100 and _count(ok, "--clr-warning") < 40
    # dark too: the same red
    drv_dark = tmp_path / "drv-cloner-dark.png"
    if drv_dark.exists():
        assert _count(drv_dark, "--clr-danger", "dark") > _count(tmp_path / "plain-cloner-dark.png", "--clr-danger", "dark") + 500


BUILD = ("room_label=מחשבי שיכפול\n"
         "machine=2|78:ac:c0:9b:11:c2|1|1|0|done|100|1|\nmachine_drawers=0|3\n"
         "room_drawer=0|2|S2|Samsung SSD 860 EVO|250059350016|passed|0|1|{d2}||ok||||100|done|sdb||0{b2}\n"
         "room_drawer=0|3|S3|Crucial MX500|500107862016|passed|0|1|{d3}||ok||||100|done|sdc||0{b3}\n"
         "round=office365|2|0|2|2|0|0|842|-1|-1|closed\nmessage=x|y\n")


@_GUI
def test_the_build_screen_rows_say_what_happened_to_the_drivers(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, BUILD.format(d2="|", d3="|", b2="", b3=""), tmp_path / "plain")   # d = fields 9-10
    _render(binary, BUILD.format(d2="staged|hp-800g6-win11", b2="|failed|stornvme|באימג' אין את השירות stornvme",
                                 d3="failed|לא הצלחנו לעגן את מחיצת Windows", b3="|||"), tmp_path / "drv")
    _render(binary, BUILD.format(d2="staged|hp-800g6-win11", b2="|bogus|stornvme|x",
                                 d3="staged|hp-800g6-win11", b3="|||"), tmp_path / "bogus")
    plain, drv, bogus = (tmp_path / f"{n}-room-live-light.png" for n in ("plain", "drv", "bogus"))
    assert _count(drv, "--clr-danger") > _count(plain, "--clr-danger") + 500
    assert _count(drv, "--clr-warning") > _count(plain, "--clr-warning") + 40   # the orange LED
    # a boot word the agent never writes is not "failed" -- never red
    assert _count(bogus, "--clr-danger") < _count(plain, "--clr-danger") + 100
