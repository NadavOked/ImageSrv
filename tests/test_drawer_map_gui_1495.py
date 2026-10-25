"""‏#1495 — המסכים על המכונה ממספרים דיסקים לפי מיפוי החיבורים (`drawer_slots`).

‏#1485 הכניס לכל משכפל ומחשב בנייה מיפוי "מגירה → חיבור" (‏`sata:<ataN>` /
‏`nvme:<PCI>`; ‏דיסק k = המקום ה-k ברשימה). הקונסולה מציגה "דיסק N" לפיו, אבל
הגואי הנייטיב המשיך למספר לפי ה-port — ועל מכונה שחווטה אחרת המסך על המכונה
אמר מספר אחר מהקונסולה: בדיוק הכשל של #553/#567 (טכנאי שולף כונן תקין).

החלוקה: הסוכן (‏`slotnames.sh`) מחשב לכל דיסק "<drawer>|<connection>" —
‏0 = החיבור אינו במיפוי, והמסך אומר "SATA n (לא ממופה)", לעולם לא מספר
מנוחש — וה-C רק מציג. ‏`port` נשאר הזהות על החוט (‏target_ports, ‏SMART).

איפה זה רץ: הפונקציות של `slotnames.sh` נבדקות כאן בכל מקום שיש bash (גם
בווינדוס). השרשור לקובצי המצב (‏`cloner_gui_state` / ‏`lc_gui_state` /
‏`gui_state`) קורא JSON ב-jq — ‏Testrunner/CI. הקומפילציה והרינדור של
‏native-gui — ‏gcc+pango, ‏Testrunner/CI בלבד.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import hello_body
from native import requires_native
from test_cloner_gui import (AGENT, BASH, GUI, PRELUDE, _build_gui, _pkgconfig, _png,
                             _png_rgb, env, posix, target)
from test_server_room import CLONER1, room_server  # noqa: F401
from test_slot_names_704_gui import sh

pytestmark = requires_native(("bash", BASH))
JQ = requires_native(("bash", BASH), "jq", why="the GUI state writers read the hello/room JSON with jq")
GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui is built on Linux only",
)
SRC = GUI / "src"

# readlink -f of a disk's `device`: an NVMe controller path holds colons,
# which a Windows test tree cannot hold -- the link is stubbed, the parse is not.
READLINK = ('readlink() { case "$2" in '
            '*/nvme0n1/*) echo /sys/devices/pci0000:00/0000:00:1d.0/0000:03:00.0/nvme/nvme0 ;; '
            '*/nvme1n1/*) echo /sys/devices/pci0000:00/0000:00:1c.0/0000:04:00.0/nvme/nvme1 ;; '
            '*/sr0/*) echo /sys/devices/pci0000:00/0000:00:17.0/ata5/host4/target4/4:0:0:0 ;; '
            '*) echo /sys/devices/virtual/block/x ;; esac; }; ')
LIBS = "".join(f". {posix(AGENT)}/lib/{n}.sh; " for n in ("common", "sysinfo", "disklink", "slotnames"))
# drawer 1 = SATA 2 (ata3), drawer 2 = SATA 0 (ata1), drawer 3 = an NVMe controller
MAPPING = "ok\n1|sata:3\n2|sata:1\n3|nvme:0000:03:00.0\n"


def _run(tmp_path: Path, script: str, mapping: str | None = MAPPING,
         names: str | None = None, sysroot: Path | None = None) -> str:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    if mapping is not None:
        (run / "drawer_slots").write_text(mapping, encoding="utf-8", newline="\n")
    if names is not None:
        (run / "slot_names").write_text(names, encoding="utf-8", newline="\n")
    root = sysroot or tmp_path / "root"
    root.mkdir(exist_ok=True)
    out = sh(f'export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r}; {LIBS}{READLINK}{script}')
    assert out.returncode == 0, out.stderr
    return out.stdout


# --- disk_drawer: the number the operator sees -------------------------------------


def test_a_disk_is_numbered_by_its_connection_in_the_mapping_not_its_port(tmp_path):
    """sata:3 is drawer 1 here: the disk on ATA port 3 is "דיסק 1", not 3."""
    out = _run(tmp_path, 'for a in "sdc 3" "sda 1" "nvme0n1 0"; do set -- $a; '
                         'disk_drawer "$1" "$2"; echo; done')
    assert out.splitlines() == ["1|SATA 2", "2|SATA 0", "3|NVMe 03:00.0"]


def test_a_connection_not_in_the_mapping_is_zero_never_a_guessed_number(tmp_path):
    """ATA port 2 and a second NVMe controller are not in the mapping: drawer 0
    ("SATA 1 (לא ממופה)" on screen) -- not 2, not the next free number. A disk
    with neither a port nor an NVMe address has no connection to name."""
    out = _run(tmp_path, 'for a in "sdb 2" "nvme1n1 0" "sdx 0"; do set -- $a; '
                         'disk_drawer "$1" "$2"; echo; done')
    assert out.splitlines() == ["0|SATA 1", "0|NVMe 04:00.0", "0|"]


def test_without_a_mapping_file_the_convention_is_drawer_equals_port(tmp_path):
    """No `$RUN_DIR/drawer_slots` (an older server, no hello answered yet): the
    declared convention drawer k = sata:k -- the console's fallback too. An
    NVMe disk is not in the convention (sata:k only)."""
    out = _run(tmp_path, 'for a in "sdc 3" "sda 1" "nvme0n1 0"; do set -- $a; '
                         'disk_drawer "$1" "$2"; echo; done', mapping=None)
    assert out.splitlines() == ["3|SATA 2", "1|SATA 0", "0|NVMe 03:00.0"]


# --- the records for the cloner screen -------------------------------------------------


def test_every_drawer_of_the_mapping_becomes_a_record_with_its_slot_name(tmp_path):
    """Empty drawers too: the standby cage draws drawer 1..N of the mapping.
    The slot name (#704, by SATA port) travels with its drawer."""
    out = _run(tmp_path, "drawer_slots_records", names="ok\n3|למעלה\n1|a b\n")
    assert out.splitlines() == ["cdrawer_slot=1|SATA 2|למעלה", "cdrawer_slot=2|SATA 0|a b",
                                "cdrawer_slot=3|NVMe 03:00.0|"]


def test_no_mapping_file_means_no_drawer_records(tmp_path):
    assert _run(tmp_path, "drawer_slots_records", mapping=None) == ""


def _optical_root(tmp_path: Path, sr_mod: bool = True) -> Path:
    root = tmp_path / "root"
    (root / "sys/block/sr0/device").mkdir(parents=True)
    (root / "sys/block/sr0/device/model").write_text("  HL-DT|DVDRAM  \n", encoding="utf-8")
    if sr_mod:
        (root / "sys/module/sr_mod").mkdir(parents=True)
    return root


def test_a_cd_dvd_drive_is_a_record_of_its_own_by_its_connection(tmp_path):
    """sr0 on ATA port 5 (SATA 4) is not in the mapping: drawer 0. The model's
    `|` would split the record -- folded."""
    out = _run(tmp_path, "drawer_slots_records", sysroot=_optical_root(tmp_path))
    assert out.splitlines()[-1] == "coptical=0|SATA 4|HL-DT DVDRAM"


def test_a_cd_dvd_drive_on_a_mapped_connection_carries_that_drawer(tmp_path):
    out = _run(tmp_path, "drawer_slots_records", mapping="ok\n1|sata:5\n",
               sysroot=_optical_root(tmp_path))
    assert out.splitlines() == ["cdrawer_slot=1|SATA 4|", "coptical=1|SATA 4|HL-DT DVDRAM"]


def test_without_sr_mod_no_optical_record_is_made_up(tmp_path):
    """No sr_mod = no /sys/block/sr* to trust (disklink.sh optical_json): no
    record, not an empty drawer that happens to hold a drive."""
    out = _run(tmp_path, "drawer_slots_records", sysroot=_optical_root(tmp_path, sr_mod=False))
    assert "coptical=" not in out


# --- the build machine's own disks (the capture list) ------------------------------------


def test_the_build_machine_gets_a_disk_slot_record_per_local_disk(tmp_path):
    out = _run(tmp_path, 'drawer_slots_refresh() { return 1; }; list_disks() { echo sda sdc nvme0n1; }; '
                         'disk_port() { case "$1" in sda) echo 1;; sdc) echo 3;; esac; }; '
                         "disk_slot_records")
    assert out.splitlines() == ["disk_slot=sda|2|SATA 0", "disk_slot=sdc|1|SATA 2",
                                "disk_slot=nvme0n1|3|NVMe 03:00.0"]


# --- the state files (jq: Testrunner/CI) ----------------------------------------------------


PORTS = 'disk_port() { case "$1" in sda) echo 1;; sdb) echo 2;; sdc) echo 3;; *) ;; esac; }; '


@JQ
def test_the_cloner_state_numbers_the_round_drawers_by_the_mapping(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "response.json").write_text(json.dumps({"drawer_slots": ["sata:3", "sata:1"]}),
                                       encoding="utf-8", newline="\n")
    target(run, "sda", "writing", 100, 40, "5")
    target(run, "sdb", "waiting", 100, 0, "")
    target(run, "sdc", "waiting", 100, 0, "")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    assert "drawer=1|sda|writing|45|100||-1|-1|2|SATA 0||" in lines
    assert "drawer=2|sdb|waiting|0|100||-1|-1|0|SATA 1||" in lines, "port 2 is not mapped"
    assert "drawer=3|sdc|waiting|0|100||-1|-1|1|SATA 2||" in lines
    assert [l for l in lines if l.startswith("cdrawer_slot=")] == [
        "cdrawer_slot=1|SATA 2|", "cdrawer_slot=2|SATA 0|"]


@JQ
def test_the_idle_inventory_is_sorted_by_drawer_with_the_unmapped_last(tmp_path):
    """sda on port 1 is drawer 2, sdc on port 3 is drawer 1, sdb on port 2 is
    not mapped: the records come 1, 2, then the unmapped one."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    root = tmp_path / "root"
    (run / "response.json").write_text(json.dumps({"drawer_slots": ["sata:3", "sata:1"]}),
                                       encoding="utf-8", newline="\n")
    for dev in ("sda", "sdb", "sdc"):
        (root / "sys/block" / dev / "device").mkdir(parents=True)
        (root / "sys/block" / dev / "size").write_text("1000\n", newline="\n")
    out = sh(env(run, gui) + f"export SYSROOT={posix(root)!r}; " + PRELUDE + PORTS
             + "list_disks() { echo sda sdb sdc; }; smart_hello_field() { echo ok; }; "
             + "disk_failure_cause() { return 1; }; disk_failures_refresh() { :; }; cloner_gui_state")
    assert out.returncode == 0, out.stderr
    cdisk = [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("cdisk=")]
    assert [(l.split("|")[1], l.split("|")[9], l.split("|")[10]) for l in cdisk] == [   # #1798: field 12 (the mark) follows
        ("sdc", "1", "SATA 2"), ("sda", "2", "SATA 0"), ("sdb", "0", "SATA 1")], cdisk


@JQ
def test_the_room_records_carry_the_drawer_and_every_mapped_slot(tmp_path):
    """GET /room: drawer_list[].drawer (null = not mapped -> 0; an older server
    without the field -> empty) and the connection; room_slot= per drawer of
    machines[].drawer_slots, with its name."""
    room = {"group_label": "חדר", "round": None, "machines": [{
        "name": "C1", "mac": "aa:bb:cc:dd:ee:01", "awake": True, "joined": False,
        "fresh_drawers": 2, "state": None, "drawer_count": 3,
        "drawer_slots": ["sata:3", "sata:1", "nvme:0000:03:00.0"],
        "slot_names": {"sata:3": "למעלה"},
        "drawer_list": [
            {"dev": "sda", "port": 1, "pci": None, "drawer": 2, "serial": "S1", "model": "M",
             "size_bytes": 1, "fresh": True, "selected": False},
            {"dev": "sdb", "port": 2, "pci": None, "drawer": None, "serial": "S2", "model": "M",
             "size_bytes": 1, "fresh": True, "selected": False},
            {"dev": "nvme0n1", "port": None, "pci": "0000:03:00.0", "drawer": 3, "serial": "S3",
             "model": "M", "size_bytes": 1, "fresh": True, "selected": False},
            {"dev": "sdc", "port": 3, "serial": "S4", "model": "M", "size_bytes": 1,
             "fresh": True, "selected": False}]}]}
    gui = tmp_path / "gui"; gui.mkdir()
    (gui / "room.json").write_text(json.dumps(room, ensure_ascii=False), encoding="utf-8")
    jq_prog = (AGENT / "lib/guistate.sh").read_text(encoding="utf-8")
    start = jq_prog.index("            jq -r '\n              def clean:")
    end = jq_prog.index("\"$GUI_DIR/room.json\"", start)
    out = sh(f'export GUI_DIR={posix(gui)!r}; ' + jq_prog[start:end] + '"$GUI_DIR/room.json"')
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    drawers = [l.split("|") for l in lines if l.startswith("room_drawer=")]
    assert [(d[1], d[13], d[14]) for d in drawers] == [
        ("1", "2", "SATA 0"), ("2", "0", "SATA 1"), ("0", "3", "NVMe 03:00.0"), ("3", "", "SATA 2")]
    assert [l for l in lines if l.startswith("room_slot=")] == [
        "room_slot=0|1|SATA 2|למעלה", "room_slot=0|2|SATA 0|", "room_slot=0|3|NVMe 03:00.0|"]


@JQ
def test_the_build_machine_state_carries_its_disks_by_the_mapping(tmp_path):
    """gui_state (the build machine's kiosk) writes disk_slot= from the hello's
    drawer_slots: the capture list says "דיסק 1" for the disk on ATA port 3.
    The real slotnames.sh replaces the PRELUDE's stub, as gui_libs loads it."""
    from test_class_deploy_switch import state_records
    pre = (LIBS + "list_disks() { echo sda sdc; }; "
           'disk_port() { case "$1" in sda) echo 1;; sdc) echo 3;; esac; }; ')
    lines = state_records(tmp_path, {"drawer_slots": ["sata:3", "sata:1"]}, shell_pre=pre)
    assert [l for l in lines if l.startswith("disk_slot=")] == [
        "disk_slot=sda|2|SATA 0", "disk_slot=sdc|1|SATA 2"], lines


@JQ
def test_the_local_clone_disks_carry_the_drawer_and_sort_by_it(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    lc = tmp_path / "lc"; (lc / "inv").mkdir(parents=True)
    (lc / "screen").write_text("local\n", newline="\n")
    (run / "response.json").write_text(json.dumps({"drawer_slots": ["sata:3", "sata:1"]}),
                                       encoding="utf-8", newline="\n")
    for port, dev in ((1, "sda"), (2, "sdb"), (3, "sdc")):
        (lc / "inv" / f"{dev}.line").write_text(
            f"{port}|{dev}|M|S{port}|1000|ok|ok|Windows|1|1|1000|1|\n", encoding="utf-8", newline="\n")
    out = sh(env(run, gui) + f"export LC={posix(lc)!r}; " + PRELUDE
             + f". {posix(AGENT)}/lib/lcgui.sh; lc_gui_state")
    assert out.returncode == 0, out.stderr
    disks = [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("lc_disk=")]
    assert [(d.split("|")[1], d.split("|")[13], d.split("|")[14]) for d in disks] == [
        ("sdc", "1", "SATA 2"), ("sda", "2", "SATA 0"), ("sdb", "0", "SATA 1")], disks


# --- the server: GET /room must let an NVMe drawer be found in the mapping -------------


def test_room_numbers_a_mapped_nvme_drawer(room_server):
    """drawer_list had no `pci`, so drawer_of() never found an NVMe drawer: it
    was "not mapped" on the room grid (native and console) even when mapped as
    drawer 2. The grid on the build machine reads `drawer` from here."""
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.put(f"/api/console/machines/{CLONER1}",
                     json={"drawer_slots": ["sata:1", "nvme:0000:03:00.0"]}).status_code == 200
    body = hello_body(CLONER1)
    body["disks"] = [
        {"dev": "sda", "size_bytes": 1000, "model": "A", "serial": "S1", "removable": False,
         "scheme": "gpt", "has_data": False, "port": 1, "pci": None},
        {"dev": "nvme0n1", "size_bytes": 1000, "model": "N", "serial": "N1", "removable": False,
         "scheme": "gpt", "has_data": False, "port": None, "pci": "0000:03:00.0"},
        {"dev": "nvme1n1", "size_bytes": 1000, "model": "N", "serial": "N2", "removable": False,
         "scheme": "gpt", "has_data": False, "port": None, "pci": "0000:04:00.0"},
    ]
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    machine = next(m for m in admin.get("/api/console/room").json()["machines"] if m["mac"] == CLONER1)
    by_dev = {d["dev"]: (d["drawer"], d.get("pci")) for d in machine["drawer_list"]}
    assert by_dev == {"sda": (1, None), "nvme0n1": (2, "0000:03:00.0"), "nvme1n1": (None, "0000:04:00.0")}


# --- native-gui: the source (everywhere) ----------------------------------------------------


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_the_parser_reads_every_new_record():
    state_c = _src("state.c")
    for key in ("cdrawer_slot", "coptical", "disk_slot", "room_slot"):
        assert f'KEY("{key}")' in state_c, key
    assert "lc_disk" in _src("lc_state.c") and "conn" in _src("localclone.h")


def test_the_screens_say_unmapped_and_cd_dvd_in_the_consoles_words():
    rounds = _src("screens_rounds.c")
    assert "(לא ממופה)" in rounds, "the console's rowLabel: '<connection> (לא ממופה)'"
    assert "כונן CD/DVD" in _src("screens_standby.c")


def test_no_screen_numbers_a_disk_by_its_port_any_more():
    """Every 'דיסק N' / number box that printed d->port now goes through the
    drawer. A port is still printed only as the connection ('SATA port-1')
    when no connection text was sent (an older agent)."""
    import re
    offenders = []
    for path in sorted(SRC.glob("*.c")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'"[^"\n]*דיסק %d[^"\n]*"[^;\n]*->port\b', text):
            offenders.append(f"{path.name}: {m.group(0)}")
        for m in re.finditer(r'snprintf\(num, sizeof num, "%d", [^;]*->port[^;]*\);', text):
            offenders.append(f"{path.name}: {m.group(0)}")
    assert not offenders, offenders


def test_room_hit_ids_are_drawer_indexes_not_ports():
    """Ports go up to 32 now (sata:12 may be drawer 1): `mi * MAX_DRAWERS +
    (port - 1)` would land in the next machine's ids."""
    for name in ("screens_room.c", "screens_drivers.c"):
        assert "MAX_DRAWERS + (port - 1)" not in _src(name), name
    assert "encoded % MAX_DRAWERS + 1" not in _src("main.c")


def test_the_readme_documents_the_records():
    readme = (GUI / "README.md").read_text(encoding="utf-8")
    for needle in ("disk_slot=<dev>|<drawer>|<conn>", "room_slot=<machine-index>|<drawer>|<conn>|<name>",
                   "cdrawer_slot=<drawer>|<conn>|<name>", "coptical=<drawer>|<conn>|<model>",
                   "|<rate_bps>|<eta_s>|<drawer>|<conn>", "|<crc>|<drawer>|<conn>"):
        assert needle in readme, needle


# --- native-gui: what is drawn (gcc + pango: Testrunner/CI) -----------------------------------


def _state(tmp_path: Path, name: str, lines: list[str]) -> Path:
    p = tmp_path / f"{name}.txt"
    p.write_text("\n".join([*lines, "message=x|y"]) + "\n", encoding="utf-8", newline="\n")
    return p


def _same(tmp_path: Path, a: str, b: str, card: str) -> bool:
    return _png_rgb(tmp_path / f"{a}-{card}-light.png")[3] == _png_rgb(tmp_path / f"{b}-{card}-light.png")[3]


DISK = "500107862016|passed||6.0|6.0|"


@GUI_BUILD
def test_the_cloner_screens_place_a_disk_by_its_drawer_not_its_port(tmp_path):
    """The equivalence is the test: a machine whose drawer 1 is on ATA port 3
    must draw exactly what a conventionally wired machine with that disk on
    port 1 draws -- same cards, same cage. An old screen (by port) puts
    'Model X' in bay 3 and differs."""
    binary = _build_gui(tmp_path)
    by_port = ["cloner=Office 2024", "drawer=1|sda|writing|40|100||-1|-1", "drawer=2|sdb|waiting|0|0||-1|-1",
               f"cdisk=1|sda|Model X|{DISK}", f"cdisk=2|sdb|Model Y|{DISK}"]
    mapped = ["cloner=Office 2024", "cdrawer_slot=1|SATA 0|", "cdrawer_slot=2|SATA 1|", "cdrawer_slot=3|SATA 2|",
              "drawer=5|sdb|waiting|0|0||-1|-1|2|SATA 1", "drawer=3|sda|writing|40|100||-1|-1|1|SATA 0",
              f"cdisk=5|sdb|Model Y|{DISK}|2|SATA 1", f"cdisk=3|sda|Model X|{DISK}|1|SATA 0"]
    _png(binary, tmp_path / "port", _state(tmp_path, "port", by_port))
    _png(binary, tmp_path / "map", _state(tmp_path, "map", mapped))
    assert _same(tmp_path, "port", "map", "cloner"), "the round cards follow the drawer"
    assert _same(tmp_path, "port", "map", "standby"), "the cage follows the drawer"


@GUI_BUILD
def test_an_unmapped_disk_and_a_cd_dvd_drive_are_drawn_not_hidden(tmp_path):
    binary = _build_gui(tmp_path)
    bays = ["cdrawer_slot=1|SATA 0|", "cdrawer_slot=2|SATA 1|", f"cdisk=1|sda|Model X|{DISK}|1|SATA 0"]
    _png(binary, tmp_path / "base", _state(tmp_path, "base", bays))
    _png(binary, tmp_path / "loose", _state(tmp_path, "loose", [*bays, f"cdisk=4|sdd|Model Z|{DISK}|0|SATA 3"]))
    _png(binary, tmp_path / "dvd", _state(tmp_path, "dvd", [*bays, "coptical=2|SATA 1|HL-DT-ST DVDRAM"]))
    assert not _same(tmp_path, "base", "loose", "standby"), "a disk not in the mapping is a row of its own"
    assert not _same(tmp_path, "base", "dvd", "standby"), "drawer 2 holds a CD/DVD drive, not 'ריק'"


@GUI_BUILD
def test_the_room_grid_places_a_drawer_by_its_number_not_its_port(tmp_path):
    """drawer 1 of the machine is on ATA port 5 (> drawer_count): the old
    parser dropped it (`port > drawer_count`), the grid now draws it in row 1
    exactly as a conventionally wired drawer on port 1."""
    binary = _build_gui(tmp_path)
    head = ["machine=CLONE-01|a0:48:1c:8a:18:40|1|1|2|writing|40|1|", "machine_drawers=0|3"]
    tail = ["round=office365|1|0|0|2|0|2|-1|-1|-1"]
    by_port = [*head, "room_drawer=0|1|S1|Model A|256060514304|passed|1|1",
               "room_drawer=0|2|S2|Model B|256060514304|passed|1|0", *tail]
    mapped = [*head, "room_slot=0|1|SATA 0|", "room_slot=0|2|SATA 1|", "room_slot=0|3|SATA 2|",
              "room_drawer=0|7|S2|Model B|256060514304|passed|1|0||||||2|SATA 1",
              "room_drawer=0|5|S1|Model A|256060514304|passed|1|1||||||1|SATA 0", *tail]
    _png(binary, tmp_path / "port", _state(tmp_path, "port", by_port))
    _png(binary, tmp_path / "map", _state(tmp_path, "map", mapped))
    for card in ("room", "room-live"):
        assert _same(tmp_path, "port", "map", card), card


try:   # the local clone's driver (feeds real events to lc_handle) -- Linux only
    from test_native_local_clone import drive, driver  # noqa: F401
except Exception:   # pragma: no cover - the module skips itself without gcc
    drive = driver = None


@GUI_BUILD
def test_the_local_clone_source_is_drawer_three_by_the_mapping(driver, tmp_path):
    """'drawer 3 is the source' (#1322) is drawer 3 of the mapping: here sda on
    ATA port 1. The old screen took port 3 (sdc). The digit key picks by
    drawer too: '1' is sdb."""
    disks = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
             "lc_disk=2|sdb|B|S2|500107862016|ok|ok|Windows|3|1|500107862016|1||1|SATA 1\n"
             "lc_disk=3|sdc|C|S3|500107862016|ok|ok|Windows|3|1|500107862016|1||2|SATA 2\n"
             "lc_disk=1|sda|A|S1|500107862016|ok|ok|Windows|3|1|500107862016|1||3|SATA 0\n")
    out = drive(driver, tmp_path, disks, "key:DOWN")
    assert out[0] == "lc-measure|sda", out
    out = drive(driver, tmp_path, disks, "char:1")
    assert "lc-measure|sdb" in out, out
