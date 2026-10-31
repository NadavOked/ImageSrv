"""‏#704 — שמות החריצים בגואי הנייטיב: מסך מחשב השיכפול וגריד החדר של מחשב הבנייה.

השרת (‏`test_slot_names_704.py`) מחזיר את השמות בשני מקומות:

* ‏`hello` → ‏`slot_names` (‏`{"sata:1": "דיסק 1"}`). ‏`slotnames.sh` כותב אותם
  ל-`$RUN_DIR/slot_names`, ו-`cloner_gui_state` מוסיף `cslot_name=<port>|<name>`
  לקובץ המצב של מחשב השיכפול.
* ‏`GET /room` → ‏`drawer_list[].slot_name` + ‏`machines[].slot_names`. ‏`gui_state`
  (מחשב הבנייה) כותב שדה 11 ב-`room_drawer=` ורשומת `room_slot_name=` לכל
  חריץ SATA בעל שם — גם ריק.

‏`state.c` קורא את שלושתם, והמסכים מציירים את השם במקום "דיסק N"; בלי שם
התצוגה כמו היום. אין מהדר בווינדוס: המקור נבדק כאן, הרינדור (‏`--png`) על
ה-Testrunner — ‏`requires_native`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_class_deploy_switch import PRELUDE as STATE_PRELUDE, STATION
from test_cloner_gui import (AGENT, BASH, GUI, PRELUDE, _build_gui, _pkgconfig, _png,
                             _png_rgb, env, posix, target)

SRC = GUI / "src"
JQ = requires_native(("bash", BASH), "jq", why="slotnames.sh ו-gui_state קוראים JSON ב-jq")
GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
MAC = "a0:48:1c:8a:18:40"


def sh(script: str) -> subprocess.CompletedProcess:
    """כמו `test_cloner_gui.sh`, בפענוח UTF-8: השמות עבריים, וברירת המחדל של
    ווינדוס (cp1252) שוברת את קריאת הפלט."""
    return subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                          capture_output=True, encoding="utf-8", errors="replace",
                          cwd=str(AGENT.parent), stdin=subprocess.DEVNULL, timeout=60)


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# --- slotnames.sh: תשובת ה-hello → קובץ → רשומות --------------------------------


def _slotnames(tmp_path: Path, responses: list[object]) -> tuple[str, str]:
    """‏slot_names_refresh על כל תשובה ברצף (כמו פעימות hello); מחזיר
    (הרשומות אחרי הריצה האחרונה, הקובץ). אובייקט = JSON, מחרוזת = תוכן גולמי."""
    run = tmp_path / "run"; run.mkdir()
    steps = []
    for i, body in enumerate(responses):
        f = tmp_path / f"r{i}.json"
        f.write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
                     encoding="utf-8", newline="\n")
        steps.append(f'cp {posix(f)!r} "$RUN_DIR/response.json"; slot_names_refresh || :')
    out = sh(f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/hw/slotnames.sh; '
             + "; ".join(steps) + "; slot_names_records")
    assert out.returncode == 0, out.stderr
    kept = run / "slot_names"
    return out.stdout, (kept.read_text(encoding="utf-8") if kept.exists() else "")


@JQ
def test_hello_names_become_cloner_records_by_port(tmp_path):
    out, _ = _slotnames(tmp_path, [{"slot_names": {
        "sata:1": "דיסק 1", "sata:3": "a|b\nc", "serial:NV-1": "דיסק מיוחד"}}])
    assert out.splitlines() == ["cslot_name=1|דיסק 1", "cslot_name=3|a b c"], \
        "SATA keys only (the cloner lays out cards by port); | and newline folded"


@JQ
def test_an_answer_without_names_or_unreadable_keeps_the_last_file(tmp_path):
    """תשובה בלי השדה (‏poll, שרת ישן) או JSON שבור אינם "אין שמות" (עיקרון 5)."""
    out, _ = _slotnames(tmp_path, [{"slot_names": {"sata:2": "האמצעית"}},
                                   {"schema": 1}, "{broken"])
    assert out.splitlines() == ["cslot_name=2|האמצעית"]


@JQ
def test_an_empty_object_clears_the_names(tmp_path):
    out, kept = _slotnames(tmp_path, [{"slot_names": {"sata:2": "האמצעית"}}, {"slot_names": {}}])
    assert out == "" and kept == "ok\n"


def test_no_file_means_no_records(tmp_path):
    out = sh(f'export RUN_DIR={posix(tmp_path)!r}; . {posix(AGENT)}/lib/hw/slotnames.sh; slot_names_records')
    assert out.returncode == 0 and out.stdout == ""


@JQ
def test_the_cloner_state_file_carries_the_names(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text(
        json.dumps({"slot_names": {"sata:2": "האמצעית"}}, ensure_ascii=False),
        encoding="utf-8", newline="\n")
    target(run, "sdb", "waiting", 100, 0, "")
    out = sh(env(run, gui) + PRELUDE
             + 'disk_port() { case "$1" in sdb) echo 2;; esac; }; cloner_gui_state')
    assert out.returncode == 0, out.stderr
    assert "cslot_name=2|האמצעית" in (gui / "state").read_text(encoding="utf-8").splitlines()


def test_the_kiosk_load_chain_sources_slotnames():
    """‏#834: lib שחסר ב-`gui_libs` הוא `not found` פעם בשנייה על הברזל."""
    bridge = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    chain = bridge[bridge.index("gui_libs() {"):bridge.index("\n}", bridge.index("gui_libs() {"))]
    assert '. "$LIB_DIR/slotnames.sh"' in chain
    assert chain.index("slotnames.sh") < chain.index("clonergui.sh")


# --- gui_state (מחשב הבנייה): ‏GET /room → רשומות ------------------------------

ROOM = {"round": None, "machines": [{
    "name": "CLONE-01", "mac": MAC, "awake": True, "joined": False, "fresh_drawers": 2,
    "state": None, "drawer_count": 3,
    "slot_names": {"sata:1": "עליונה", "sata:3": "ריקה|x", "serial:NV-1": "דיסק מיוחד"},
    "drawer_list": [
        {"dev": "sda", "port": 1, "serial": "S1", "model": "M", "size_bytes": 1, "smart": "ok",
         "fresh": True, "selected": False, "slot_name": "עליונה"},
        {"dev": "nvme0n1", "port": None, "serial": "NV-1", "model": "M", "size_bytes": 1,
         "smart": "ok", "fresh": True, "selected": False, "slot_name": "דיסק מיוחד"},
        {"dev": "sdb", "port": 2, "serial": "S2", "model": "M", "size_bytes": 1, "smart": "ok",
         "fresh": True, "selected": False, "slot_name": None},
    ]}]}


def _room_records(tmp_path: Path, room: dict) -> list[str]:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "station.json").write_text(json.dumps(STATION), newline="\n")
    (run / "room.json").write_text(json.dumps(room, ensure_ascii=False), encoding="utf-8", newline="\n")
    (gui / "mode").write_text("direct\n", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
             f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
             + STATE_PRELUDE
             + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
             + f'gui_get() {{ case "$1" in room) cp {posix(run / "room.json")!r} "$2" ;; *) return 1 ;; esac; }}; '
             + 'gui_state')
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


@JQ
def test_room_drawer_carries_the_slot_name_as_field_eleven(tmp_path):
    lines = _room_records(tmp_path, ROOM)
    rows = [l.split("=", 1)[1].split("|") for l in lines if l.startswith("room_drawer=")]
    assert [(r[1], r[10]) for r in rows] == [("1", "עליונה"), ("0", "דיסק מיוחד"), ("2", "")], rows
    # #432: field 12 = link state, after the name; #1366: 13 = CRC; #1495: 14 = drawer, 15 = connection
    # 17/10: 16-17 = the drawer's own progress in the wave (pct, target state)
    assert all(len(r) == 23 for r in rows), rows   # #1386: 18-20 = dev, "בוטל על ידי …", reported; #1331: 21-23 = drivers.boot


@JQ
def test_room_slot_name_records_cover_sata_slots_even_empty(tmp_path):
    lines = _room_records(tmp_path, ROOM)
    assert [l for l in lines if l.startswith("room_slot_name=")] == [
        "room_slot_name=0|1|עליונה", "room_slot_name=0|3|ריקה x"], \
        "sata keys only (serial: rides on its drawer), | folded"


@JQ
def test_an_older_server_without_names_writes_empty_fields(tmp_path):
    room = json.loads(json.dumps(ROOM))
    m = room["machines"][0]
    del m["slot_names"]
    for d in m["drawer_list"]:
        del d["slot_name"]
    lines = _room_records(tmp_path, room)
    assert not [l for l in lines if l.startswith("room_slot_name=")]
    rows = [l.split("=", 1)[1].split("|") for l in lines if l.startswith("room_drawer=")]
    assert rows and all(r[10] == "" for r in rows), rows   # field 11, the name: empty, not made up


# --- state.c + המסכים: המקור (בלי מהדר בווינדוס) --------------------------------


def test_the_parser_reads_all_three_records():
    state_c = _src("state.c")
    assert 'KEY("room_slot_name")' in state_c and 'KEY("cslot_name")' in state_c
    # #432 added field 12 (link state) after the name; the floor is still 8
    assert "int nf = split(val, f, 27);" in state_c and "if (nf < 8) continue;" in state_c,         "room_drawer: fields 11-27 optional (an older agent sends 8-17; #1386: 18-20; #1331: 21-23; T8: 24-26; #1742: 27)"
    assert "m->slot_names[actual_port - 1]" in state_c
    assert re.search(r"fallback.*port == \(int\)port", state_c), \
        "a port-0 drawer drawn in a borrowed slot must not take that slot's name"


def test_buffers_are_sized_for_the_longest_name():
    """כל gcc warning שובר את טסטי הרינדור (קיצוץ עברית): כל מאגר שמקבל שם
    גדול מ-SLOT_NAME_MAX בתוספת הטקסט שסביבו."""
    ui = _src("ui.h")
    assert re.search(r"#define SLOT_NAME_MAX 100\b", ui)
    assert "char slot_names[MAX_DRAWERS][SLOT_NAME_MAX];" in ui
    assert "char cslot_names[MAX_DRAWERS][SLOT_NAME_MAX];" in ui
    room = _src("screens_room.c")
    assert "named[SLOT_NAME_MAX + 128]" in room   # #1495: + "(לא ממופה)"
    # 17/10: the live rows (both screens) name the disk in LcBay.name
    assert "char name[SLOT_NAME_MAX + 64], sub[200];" in _src("lcui.h")
    cloner = _src("screens_cloner.c")
    assert "pdisk[SLOT_NAME_MAX + 16]" in cloner and "head[SLOT_NAME_MAX + 16 + 160]" in cloner
    standby = _src("screens_standby.c")   # #1304: the standby moved out of screens.c
    assert "name[SLOT_NAME_MAX + 16], sub[SLOT_NAME_MAX + 48]" in standby
    assert "label[SLOT_NAME_MAX + 16], msg[SLOT_NAME_MAX + 120]" in standby


def test_every_disk_label_on_the_cloner_screens_goes_through_the_name():
    for name in ("screens_cloner.c", "screens_standby.c"):   # #1304: the standby moved out of screens.c
        text = _src(name)
        assert "cloner_slot_label(" in text, name
        assert not re.search(r'"דיסק %d"', text), f"{name}: a bare 'דיסק %d' skips the name"
    rounds = _src("screens_rounds.c")
    body = rounds[rounds.index("void cloner_slot_label("):]
    assert '"דיסק %d"' in body.split("\n}")[0], "no name = exactly as before"


def test_the_room_grid_uses_the_name_and_falls_back_to_disk_n():
    room = _src("screens_room.c")
    assert "m->slot_names[port - 1]" in room
    # #1495: the live row is room_drawer_label + size; no name = "דיסק N" as before
    assert "room_drawer_label(m, d, y->name, sizeof y->name);" in room   # 17/10: the live row
    rounds = _src("screens_rounds.c")
    label = rounds[rounds.index("void room_drawer_label("):].split("\n}")[0]
    assert '"דיסק %d", drawer' in label


def test_the_readme_documents_the_records():
    readme = (GUI / "README.md").read_text(encoding="utf-8")
    for needle in ("room_slot_name=<machine-index>|<port>|<name>", "cslot_name=<port>|<name>",
                   "|<slot name>"):
        assert needle in readme, needle


# --- רינדור (Testrunner) -------------------------------------------------------


def _room_state(tmp_path: Path, name: str, extra: list[str], f11: str = "") -> Path:
    p = tmp_path / f"{name}.txt"
    lines = [
        f"machine=CLONE-01|{MAC}|1|1|2|writing|40|1|",
        "machine_drawers=0|3",
        f"room_drawer=0|1|S1|Model A|256060514304|passed|1|1|||{f11}",   # field 11 (index 10): after the two driver fields (#1269)
        "room_drawer=0|2|S2|Model B|256060514304|passed|1|0||",
        *extra,
        "round=office365|1|0|0|2|0|2|-1|-1|-1",
        "message=x|y",
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return p


def _pixels(path: Path) -> bytes:
    return _png_rgb(path)[3]


@GUI_BUILD
def test_room_screens_draw_the_name_and_nothing_changes_without_one(tmp_path):
    binary = _build_gui(tmp_path)
    _png(binary, tmp_path / "plain", _room_state(tmp_path, "plain", []))
    _png(binary, tmp_path / "empty", _room_state(tmp_path, "empty", ["room_slot_name=0|3|"]))
    _png(binary, tmp_path / "named", _room_state(tmp_path, "named", ["room_slot_name=0|3|ריקה"], "עליונה"))
    for card in ("room", "room-live"):
        plain = _pixels(tmp_path / f"plain-{card}-light.png")
        assert _pixels(tmp_path / f"empty-{card}-light.png") == plain, \
            f"{card}: an empty name must draw exactly as before"
        assert _pixels(tmp_path / f"named-{card}-light.png") != plain, f"{card}: the name is not drawn"


def _cloner_state(tmp_path: Path, name: str, extra: list[str]) -> Path:
    p = tmp_path / f"{name}.txt"
    lines = ["cloner=Office 2024",
             "drawer=1|sda|writing|40|100||-1|-1",
             "drawer=2|sdb|waiting|0|0||-1|-1",
             "message=x|y",          # every --png card must route (the message card needs one)
             *extra]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return p


@GUI_BUILD
def test_the_cloner_cards_draw_the_name(tmp_path):
    binary = _build_gui(tmp_path)
    _png(binary, tmp_path / "plain", _cloner_state(tmp_path, "plain", []))
    _png(binary, tmp_path / "named", _cloner_state(tmp_path, "named", ["cslot_name=2|האמצעית"]))
    _png(binary, tmp_path / "bad", _cloner_state(tmp_path, "bad", ["cslot_name=9|x", "cslot_name=2|"]))
    plain = _pixels(tmp_path / "plain-cloner-light.png")
    assert _pixels(tmp_path / "named-cloner-light.png") != plain
    assert _pixels(tmp_path / "bad-cloner-light.png") == plain, "out of range / empty = no name"
