"""#2001 -- the clone stick's connections in one window, as the approved mockup (Nadav 22/10).

Nadav on ccd257f6: #1962's mapping editor (add a drawer, then pick its connection) is not
comfortable. The mockup he approved -- docs/design/console-redesign/mockups/slot-map-2001.html
?surface=usb -- is one window with every connection the machine shows (each SATA port, an
empty one too, a CD/DVD drive as a row that is never a drawer, each NVMe controller): the
connection, what is on it now, a name, a switch "פעיל". His decisions (the issue, 22/10):
1. the name replaces "דיסק N" on every screen; an active row with no name is "דיסק N" by order;
3. the default: a connection with a disk is active, an empty one is not -- and with no saved
   mapping that is what is in force, numbered in order;
4. "הוסף חיבור" is a type and a number only, no PCI address (an NVMe gets its address when a
   disk is seen there);
5. F2 saves, Enter ends a name, F5 is the default, Tab / Space, Alt+Shift for Hebrew.
Saved by machine model (DMI product_name), never a disk's serial: drawer_slots[model] = the
active rows in order, slot_names[model] = {key: name} (#704's rules).

The machine: an "HP Compaq 8000 Elite SFF PC" with six SATA ports -- disks on ata1 (ST500) and
ata3 (WD250), a DVD drive on ata6 -- NVMe at 0000:03:00.0 and 0000:04:00.0, the stick on sdz.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from test_cloner_gui import posix
from test_usb_clone import KIOSK, needs_jq, run
from test_usb_settings_1959_1960 import DRIVER, _compile, dialog_title, gui, hit, native_gui, tokens

pytestmark = needs_jq

MODEL = "HP Compaq 8000 Elite SFF PC"
TO_MAP = ["KEY DOWN"] * 4 + ["KEY ENTER"]
TO_RESET = ["KEY DOWN"] * 6 + ["KEY ENTER", "KEY TAB", "KEY ENTER"]
NV3, NV4 = "nvme:0000:03:00.0", "nvme:0000:04:00.0"
DEFAULT = ["sata:1", "sata:3", NV3, NV4]

STUBS = ('list_disks() { echo sda sdb nvme0n1 nvme1n1 sdz; }; '
         'disk_port() { case "$1" in sda) echo 1 ;; sdb) echo 3 ;; esac; }; '
         'port_from_path() { case "$1" in *sr0*) echo 6 ;; esac; }; '
         'disk_pci_json() { case "$1" in nvme0n1) printf \',"pci":"0000:03:00.0"\' ;; '
         'nvme1n1) printf \',"pci":"0000:04:00.0"\' ;; nvme2n1) printf \',"pci":"0000:05:00.0"\' ;; esac; return 0; }; '
         'echo sdz > "$RUN_DIR/usb-media.disks"; ')
THIRD_NVME = 'list_disks() { echo sda sdb nvme0n1 nvme1n1 nvme2n1 sdz; }; '


def chars(text: str) -> list[str]:
    """What the GUI sends for <text>: CHAR <code point, hex> -- a Hebrew letter as its SI-1452 code point."""
    return [f"CHAR {ord(c):x}" for c in text]


def machine(tmp_path: Path, model: str | None = MODEL) -> Path:
    root = tmp_path / "sysroot"
    for n in range(1, 7):
        (root / "sys" / "class" / "ata_port" / f"ata{n}").mkdir(parents=True, exist_ok=True)
    for dev, model_name, gb in (("sda", "ST500DM002", 500), ("sdb", "WDC WD2500", 250), ("nvme0n1", "Samsung 970", 256),
                                ("nvme1n1", "WD SN550", 1000), ("nvme2n1", "KINGSTON NV2", 2000), ("sdz", "SanDisk", 32)):
        d = root / "sys" / "block" / dev / "device"; d.mkdir(parents=True, exist_ok=True)
        (d / "model").write_text(model_name + "\n")
        (d.parent / "size").write_text(str(gb * 1000 * 1000 * 1000 // 512) + "\n")
    (root / "sys" / "block" / "sr0" / "device").mkdir(parents=True, exist_ok=True)
    (root / "sys" / "block" / "sr0" / "device" / "model").write_text("DVD-RAM GH60L\n")
    dmi = root / "sys" / "class" / "dmi" / "id"; dmi.mkdir(parents=True, exist_ok=True)
    if model is not None:
        (dmi / "product_name").write_text(model + "\n")
    elif (dmi / "product_name").exists():
        (dmi / "product_name").unlink()
    return root


def stick(tmp_path: Path, keys: list[str], then: str = "", model: str | None = MODEL, data: str = "mounted",
          settings: dict | None = None, pre: str = "", stubs: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    """The settings screen on this machine: usbset_open, the keys, the frame, then <then>."""
    usb = tmp_path / "usbdata"; usb.mkdir(exist_ok=True)
    if settings is not None:
        (usb / "settings.json").write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    root = machine(tmp_path, model)
    script = (KIOSK + STUBS + stubs + f'echo {data} > "$RUN_DIR/usb.data"; USB_DATA_DIR={posix(usb)!r}; usbset_open; '
              + "".join(f'usbset_key {k!r}; ' for k in keys) + pre + 'cat "$GUI_DIR/usbset.frame"; echo "@@"; ' + then)
    return run(tmp_path, script, IMAGECTL_MODE="usbclone", SYSROOT=posix(root)), usb


def recs(frame: str, kind: str) -> list[list[str]]:
    return [r[1:] for r in (line.split("\t") for line in frame.splitlines()) if r[0] == kind]


def conns(frame: str) -> list[list[str]]:
    """Each d.conn: [label, pci, model, size, tag, name, placeholder, focus, active, kind]."""
    return recs(frame, "d.conn")


def table(frame: str) -> list[tuple[str, str, str]]:
    """(label, name, active) per row."""
    return [(c[0], c[5], c[8]) for c in conns(frame)]


def focus(frame: str) -> list[tuple[str, str]]:
    return [(c[0], c[7]) for c in conns(frame) if c[7] != "0"]


def saved(usb: Path) -> dict:
    return json.loads((usb / "settings.json").read_text(encoding="utf-8"))


def frame_of(out: subprocess.CompletedProcess) -> str:
    return out.stdout.split("@@")[0]


# --- the window -----------------------------------------------------------------------------------

def test_the_window_shows_every_connection_the_empty_ones_and_the_dvd_too(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP)
    frame = frame_of(out)
    assert dialog_title(frame) == "מיפוי חיבורים", out.stdout + out.stderr
    assert "dlg\tmap\t0\tמיפוי חיבורים\t⁨HP Compaq 8000 Elite SFF PC⁩ · נשמר על הדיסק-און-קי לדגם הזה" in frame
    assert conns(frame) == [
        ["SATA 0", "", "ST500DM002", "500GB", "", "", "דיסק 1", "2", "1", "disk"],
        ["SATA 1", "", "ריק", "", "", "", "", "0", "0", "empty"],
        ["SATA 2", "", "WDC WD2500", "250GB", "", "", "דיסק 2", "0", "1", "disk"],
        ["SATA 3", "", "ריק", "", "", "", "", "0", "0", "empty"],
        ["SATA 4", "", "ריק", "", "", "", "", "0", "0", "empty"],
        ["SATA 5", "", "כונן CD/DVD · ⁨DVD-RAM GH60L⁩", "", "", "", "", "0", "0", "optical"],
        ["NVMe 1", "03:00.0", "Samsung 970", "256GB", "", "", "דיסק 3", "0", "1", "disk"],
        ["NVMe 2", "04:00.0", "WD SN550", "1TB", "", "", "דיסק 4", "0", "1", "disk"]], frame
    assert "SanDisk" not in frame                                   # the stick is no connection
    assert recs(frame, "d.alert")[0][0].startswith("<b>אין מיפוי שמור</b> — זו ברירת המחדל")   # the mockup's "empty"
    assert recs(frame, "d.addlink") == [["+ הוסף חיבור", "לחיבור שהמחשב לא מציג"]]
    assert recs(frame, "d.foot") == [["4 פעילים · ברירת המחדל", ""]]
    assert recs(frame, "d.btn") == [["ביטול", "Esc", "plain", "0"], ["שמירה", "F2", "pri", "0"]]   # no F5: nothing saved
    keys = [k[0] for k in recs(frame, "key")]
    assert keys == ["↑ ↓", "Tab", "רווח", "+", "Alt+Shift", "F2"], keys


def test_a_saved_list_and_names_are_what_the_window_opens_with(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP, settings={"drawer_slots": {MODEL: ["sata:3", "sata:5"]},
                                                  "slot_names": {MODEL: {"sata:5": "מגירה עליונה"}}})
    frame = frame_of(out)
    assert table(frame) == [("SATA 0", "", "0"), ("SATA 1", "", "0"), ("SATA 2", "", "1"), ("SATA 3", "", "0"),
                            ("SATA 4", "מגירה עליונה", "1"), ("SATA 5", "", "0"), ("NVMe 1", "", "0"),
                            ("NVMe 2", "", "0")], out.stdout + out.stderr
    assert conns(frame)[2][6] == "דיסק 1"                           # the first active one is drawer 1
    assert focus(frame) == [("SATA 0", "2")]
    assert not recs(frame, "d.alert")                               # saved: no "אין מיפוי שמור"
    assert ["החזר לברירת המחדל", "F5", "ghost", "0"] in recs(frame, "d.btn")


def test_names_with_a_space_and_in_hebrew_saved_by_model_and_in_force(tmp_path):
    keys = TO_MAP + chars("Top 1") + ["KEY ENTER", "KEY DOWN", "CHAR 20"] + chars("מגירה תחתונה") + ["KEY F2"]
    out, usb = stick(tmp_path, keys, then='jq -c ".drawer_slots" "$RUN_DIR/response.json"; echo "@@"; '
                                          'cat "$RUN_DIR/drawer_names"; echo "@@"; '
                                          'printf "%s;" "$(usb_drawer_name sda 1)" "$(usb_drawer_name sdb 3)" '
                                          '"$(usb_drawer_name nvme0n1 0)"')
    frame, ds, names, each = out.stdout.split("@@")
    want = ["sata:1", "sata:2", "sata:3", NV3, NV4]
    assert saved(usb) == {"drawer_slots": {MODEL: want},
                          "slot_names": {MODEL: {"sata:1": "Top 1", "sata:2": "מגירה תחתונה"}}}, out.stdout + out.stderr
    assert json.loads(ds) == want
    assert names.split("\n")[1:3] == ["sata:1|Top 1", "sata:2|מגירה תחתונה"], names
    assert each.strip() == "Top 1;;;", each                        # by the disk's connection
    assert dialog_title(frame) is None                              # saved: the window closed


def test_space_on_a_row_flips_its_switch_and_the_drawers_close_up(tmp_path):
    out, usb = stick(tmp_path, TO_MAP + ["CHAR 20", "KEY F2"], then='disk_drawer sdb 3')
    assert saved(usb)["drawer_slots"][MODEL] == ["sata:3", NV3, NV4], out.stdout + out.stderr
    assert out.stdout.split("@@")[1].strip() == "1|SATA 2", out.stdout   # WD250 on SATA 2 is drawer 1 now
    assert "slot_names" not in saved(usb)                           # no name given, none kept
    other = tmp_path / "another"; other.mkdir()                     # a stick with nothing saved
    out, _usb = stick(other, TO_MAP + ["KEY DOWN", "KEY LEFT", "KEY DOWN", "KEY RIGHT"])
    assert [a for _, _, a in table(frame_of(out))][:3] == ["1", "1", "0"]   # Left/Right too


def test_tab_is_name_or_switch_and_enter_ends_the_name(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + ["KEY TAB"])
    assert focus(frame_of(out)) == [("SATA 0", "1")]                # the name
    out, _usb = stick(tmp_path, TO_MAP + ["KEY TAB", "KEY TAB"])
    assert focus(frame_of(out)) == [("SATA 0", "2")]                # the switch
    out, _usb = stick(tmp_path, TO_MAP + ["KEY DOWN", "KEY TAB"])
    assert focus(frame_of(out)) == [("SATA 1", "2")]                # off: no name to go to
    out, _usb = stick(tmp_path, TO_MAP + chars("A"))
    assert focus(frame_of(out)) == [("SATA 0", "1")] and conns(frame_of(out))[0][5] == "A"   # a letter starts the name
    out, _usb = stick(tmp_path, TO_MAP + chars("A") + ["KEY ENTER", "CHAR 20"])
    assert focus(frame_of(out)) == [("SATA 0", "2")] and table(frame_of(out))[0] == ("SATA 0", "A", "0")


def test_up_and_down_pass_over_the_dvd_drive(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + ["KEY DOWN"] * 5)
    assert focus(frame_of(out)) == [("NVMe 1", "2")], frame_of(out)
    out, _usb = stick(tmp_path, TO_MAP + ["KEY DOWN"] * 5 + ["KEY UP"])
    assert focus(frame_of(out)) == [("SATA 4", "2")]


def test_a_dvd_drive_is_never_a_drawer(tmp_path):
    out, usb = stick(tmp_path, TO_MAP + ["KEY DOWN"] * 4 + ["CHAR 20", "KEY F2"],
                     settings={"drawer_slots": {MODEL: ["sata:6", "sata:1"]}})
    assert "sata:6" not in saved(usb)["drawer_slots"][MODEL], out.stdout + out.stderr


def test_a_letter_on_a_connection_that_is_off_says_so(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + ["KEY DOWN"] + chars("a"))
    frame = frame_of(out)
    assert recs(frame, "d.alert")[0][0] == "החיבור לא פעיל — הפעל אותו (רווח) כדי לתת לו שם", frame
    assert "sb\twarn\tערך לא תקין" in frame


def test_backspace_takes_a_whole_hebrew_letter(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + chars("אב") + ["KEY BACKSPACE"])
    assert conns(frame_of(out))[0][5] == "א", out.stdout + out.stderr
    out, _usb = stick(tmp_path, TO_MAP + chars("a") + ["KEY BACKSPACE", "KEY BACKSPACE"])
    assert conns(frame_of(out))[0][5] == "", out.stdout + out.stderr


def test_a_character_a_name_cannot_have_is_refused_aloud(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + chars("a|<"))
    frame = frame_of(out)
    assert conns(frame)[0][5] == "a"
    assert recs(frame, "d.alert")[0][0] == "בשם: אותיות, ספרות, רווח ו־- . # ( ) / בלבד", frame


def test_a_name_stops_at_24_characters(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + chars("א" * 25))
    frame = frame_of(out)
    assert conns(frame)[0][5] == "א" * 24
    assert recs(frame, "d.alert")[0][0] == "עד 24 תווים בשם", frame


def test_spaces_at_the_ends_of_a_name_are_not_saved(tmp_path):
    out, usb = stick(tmp_path, TO_MAP + chars("A b ") + ["KEY ENTER", "KEY DOWN", "KEY DOWN", "KEY TAB"] + chars("  ") + ["KEY F2"])
    assert saved(usb)["slot_names"] == {MODEL: {"sata:1": "A b"}}, out.stdout + out.stderr


def test_the_footer_counts_the_changes_not_saved(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + ["CHAR 20"])
    assert recs(frame_of(out), "d.foot") == [["3 פעילים", "· שינוי אחד לא נשמר"]]
    assert recs(frame_of(out), "d.alert")[0][1] == "info"             # until saved: the rows stay put under the mouse
    out, _usb = stick(tmp_path, TO_MAP + ["CHAR 20", "KEY DOWN", "KEY DOWN"] + chars("x"))
    assert recs(frame_of(out), "d.foot") == [["3 פעילים", "· 2 שינויים לא נשמרו"]]


# --- "הוסף חיבור": a type and a number, no PCI address (decision 4) --------------------------------

def test_add_a_sata_port_the_machine_does_not_show(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + ["CHAR 2b"])
    frame = frame_of(out)
    assert recs(frame, "d.add")[0][:4] == ["SATA", "6", "", "1"], frame    # the next port, the number focused
    assert not recs(frame, "d.addlink")
    assert [k[0] for k in recs(frame, "key")] == ["Tab", "Enter", "Esc"]
    out, usb = stick(tmp_path, TO_MAP + ["CHAR 2b", "KEY TAB"] + chars("כרטיס") + ["KEY ENTER"])
    row = conns(frame_of(out))[6]
    assert row == ["SATA 6", "", "ריק", "", "נוסף ידנית · לא נמצא במחשב", "כרטיס", "דיסק 3", "2", "1", "manual"], row
    out, usb = stick(tmp_path, TO_MAP + ["CHAR 2b", "KEY TAB"] + chars("כרטיס") + ["KEY ENTER", "KEY F2"])
    assert saved(usb) == {"drawer_slots": {MODEL: ["sata:1", "sata:3", "sata:7", NV3, NV4]},
                          "slot_names": {MODEL: {"sata:7": "כרטיס"}}}, out.stdout + out.stderr
    out, _usb = stick(tmp_path, TO_MAP)                                    # the next time: a saved row not found
    assert conns(frame_of(out))[6][:6] == ["SATA 6", "", "ריק", "", "לא נמצא במחשב", "כרטיס"]


def test_add_an_nvme_by_number_and_it_takes_its_address_when_a_disk_is_seen(tmp_path):
    to_nvme = ["CHAR 2b", "KEY BACKTAB", "CHAR 20"]                        # the type field, NVMe
    out, _usb = stick(tmp_path, TO_MAP + to_nvme)
    assert recs(frame_of(out), "d.add")[0][:4] == ["NVMe", "3", "", "0"]
    out, usb = stick(tmp_path, TO_MAP + to_nvme + ["KEY ENTER", "KEY F2"])
    assert saved(usb)["drawer_slots"][MODEL] == ["sata:1", "sata:3", NV3, NV4, "nvme#3"], out.stdout + out.stderr
    out, _usb = stick(tmp_path, TO_MAP, then='usb_settings_load; usb_answer; jq -c .drawer_slots "$RUN_DIR/response.json"')
    assert conns(frame_of(out))[8][:2] == ["NVMe 3", ""]                   # no address: none shown
    assert json.loads(out.stdout.split("@@")[1]) == ["sata:1", "sata:3", NV3, NV4, "nvme#3"]
    out, _usb = stick(tmp_path, TO_MAP, stubs=THIRD_NVME,
                      then='usb_settings_load; usb_answer; jq -c .drawer_slots "$RUN_DIR/response.json"')
    assert conns(frame_of(out))[8][:4] == ["NVMe 3", "05:00.0", "KINGSTON NV2", "2TB"], frame_of(out)
    assert json.loads(out.stdout.split("@@")[1]) == ["sata:1", "sata:3", NV3, NV4, "nvme:0000:05:00.0"]


def test_add_refuses_a_connection_already_there(tmp_path):
    out, _usb = stick(tmp_path, TO_MAP + ["CHAR 2b", "KEY BACKSPACE", "CHAR 30", "KEY ENTER"])
    assert recs(frame_of(out), "d.alert")[0][0] == "SATA 0 כבר ברשימה", frame_of(out)
    out, _usb = stick(tmp_path, TO_MAP + ["CHAR 2b", "KEY BACKTAB", "CHAR 20", "KEY TAB", "KEY BACKSPACE", "CHAR 32",
                                          "KEY ENTER"])
    assert recs(frame_of(out), "d.alert")[0][0] == "NVMe 2 כבר נמצא במחשב", frame_of(out)
    out, _usb = stick(tmp_path, TO_MAP + ["CHAR 2b", "KEY ESC"])
    assert not recs(frame_of(out), "d.add") and dialog_title(frame_of(out)) == "מיפוי חיבורים"   # Esc: the form only


def test_nothing_active_or_more_than_eight_is_not_saved_and_said(tmp_path):
    off = ["CHAR 20", "KEY DOWN", "KEY DOWN", "CHAR 20", "KEY DOWN", "KEY DOWN", "KEY DOWN", "CHAR 20", "KEY DOWN", "CHAR 20"]
    out, usb = stick(tmp_path, TO_MAP + off + ["KEY F2"])
    assert all(a == "0" for _, _, a in table(frame_of(out))), frame_of(out)
    assert recs(frame_of(out), "d.alert")[0][0] == "<b>לא נשמר:</b> אין אף חיבור פעיל — צריך לפחות אחד."
    assert "sb\tbad\tלא נשמר" in frame_of(out)                      # the mockup's red dot
    assert not (usb / "settings.json").exists()
    on = ["KEY DOWN", "CHAR 20", "KEY DOWN", "KEY DOWN", "CHAR 20", "KEY DOWN", "CHAR 20"]
    add = ["CHAR 2b", "KEY ENTER", "CHAR 2b", "KEY ENTER"]
    out, usb = stick(tmp_path, TO_MAP + on + add + ["KEY F2"])
    assert sum(a == "1" for _, _, a in table(frame_of(out))) == 9, frame_of(out)
    assert recs(frame_of(out), "d.alert")[0][0] == "<b>לא נשמר:</b> 9 חיבורים פעילים — עד 8."
    assert not (usb / "settings.json").exists()


def test_f5_is_the_default_and_saving_it_removes_this_models_mapping(tmp_path):
    other = {"drawer_slots": {"HP 8100": ["sata:2"]}}
    mine = {"drawer_slots": {MODEL: ["sata:3"], **other["drawer_slots"]}, "slot_names": {MODEL: {"sata:3": "x"}}}
    out, _usb = stick(tmp_path, TO_MAP + ["KEY F5"], settings=mine)
    assert table(frame_of(out)) == [("SATA 0", "", "1"), ("SATA 1", "", "0"), ("SATA 2", "", "1"), ("SATA 3", "", "0"),
                                    ("SATA 4", "", "0"), ("SATA 5", "", "0"), ("NVMe 1", "", "1"), ("NVMe 2", "", "1")]
    out, usb = stick(tmp_path, TO_MAP + ["KEY F5", "KEY F2"], settings=mine,
                     then='jq -c .drawer_slots "$RUN_DIR/response.json"')
    assert saved(usb) == other, out.stdout + out.stderr             # the default is no list of its own
    assert json.loads(out.stdout.split("@@")[1]) == DEFAULT


def test_with_no_saved_list_the_default_is_in_force_and_follows_the_disks(tmp_path):
    script = ('usb_default_follow; jq -c .drawer_slots "$RUN_DIR/response.json"; '
              'list_disks() { echo sdb sdz; }; usb_default_follow; jq -c .drawer_slots "$RUN_DIR/response.json"; '
              'disk_drawer sdb 3')
    out, _usb = stick(tmp_path, [], then=script)
    first, second, drawer = out.stdout.split("@@")[1].strip().split("\n")
    assert (json.loads(first), json.loads(second), drawer) == (DEFAULT, ["sata:3"], "1|SATA 2"), out.stdout + out.stderr


def test_reset_removes_this_models_names_too_and_keeps_another_models(tmp_path):
    other = {"drawer_slots": {"HP 8100": ["sata:2"]}, "slot_names": {"HP 8100": {"sata:2": "x"}}}
    stick(tmp_path, TO_MAP + chars("Top") + ["KEY F2"], settings=dict(other))
    out, usb = stick(tmp_path, TO_RESET, then='ls "$RUN_DIR/drawer_names" 2>/dev/null || echo none')
    assert saved(usb) == {"min_free_gb": 20, "readback_mode": "sample", "crc_warn_threshold": 500, "temp_warn_c": 60, "cloner_beep": True,
                          **other}, out.stdout + out.stderr
    assert out.stdout.split("@@")[1].split() == ["none"]


def test_names_not_valid_on_the_stick_are_refused_aloud_and_not_used(tmp_path):
    out, _usb = stick(tmp_path, [], settings={"drawer_slots": {MODEL: ["sata:1"]}, "slot_names": {MODEL: {"sata:1": "a|b"}}},
                      then='usb_settings_load 2>/dev/null; echo "$USB_NOTICE"; usb_answer; '
                           'ls "$RUN_DIR/drawer_names" 2>/dev/null || echo none; cat "$RUN_DIR/drawer_slots"')
    notice, rest = out.stdout.split("@@")[1].strip().split("\n", 1)
    assert f"slot_names[{MODEL}]" in notice and "נדחו" in notice, out.stdout
    assert rest.split("\n") == ["none", "ok", "1|sata:1"]          # the mapping itself stands


def test_the_local_clone_screen_gets_the_name_of_each_drawer(tmp_path):
    lines = "".join(f'printf "%s\\n" "{p}|{d}|M|S{p}|1000|ok|ok|Windows|1|1|1000|1|" > "$LC/inv/{d}.line"; '
                    for p, d in ((1, "sda"), (3, "sdb")))
    out, _usb = stick(tmp_path, TO_MAP + chars("עליונה") + ["KEY F2"],
                      then=f'mkdir -p "$LC/inv"; echo local > "$LC/screen"; {lines} lc_gui_state; grep ^lc_disk= "$GUI_DIR/state"')
    disks = out.stdout.split("@@")[1].strip().split("\n")
    assert [(d.split("|")[1], d.split("|")[13], d.split("|")[17]) for d in disks] == [
        ("sda", "1", "עליונה"), ("sdb", "2", "")], out.stdout + out.stderr


# --- the mouse, the keyboard language and the name on the clone screen (native-gui) ---------------

DRIVER_2001 = DRIVER.replace(
    'int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }',
    'int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }\n'
    '__attribute__((weak)) int app_usbset_window(const App *a) { (void)a; return -1; }   /* -1: no such function */\n'
    'static void emit_state(App *a, const char *path) { FILE *f = fopen(path, "r"); if (!f) return;\n'
    '    memset(&a->st.lc, 0, sizeof a->st.lc); state_parse(&a->st, f); fclose(f); lc_after_reload(a); }'
).replace(
    '        } else if (!strncmp(v, "key:", 4)) {',
    '        } else if (!strncmp(v, "char:", 5)) {   /* char:<us>:<he> */\n'
    '            Event e; memset(&e, 0, sizeof e); e.type = UIEV_CHAR; sscanf(v + 5, "%x:%x", &e.ch, &e.he);\n'
    '            app_route(&a); lc_handle(&a, &e); draw(&a);\n'
    '        } else if (!strcmp(v, "layout")) {\n'
    '            Event e; memset(&e, 0, sizeof e); e.type = UIEV_KEY; e.key = KEYSYM_LAYOUT;\n'
    '            app_route(&a); lc_handle(&a, &e); draw(&a);\n'
    '        } else if (!strncmp(v, "state:", 6)) {\n'
    '            emit_state(&a, v + 6); app_route(&a);\n'
    '        } else if (!strcmp(v, "window")) {\n'
    '            printf("window %d\\n", app_usbset_window(&a));\n'
    '        } else if (!strcmp(v, "labels")) {\n'
    '            for (int k = 0; k < a.st.lc.ndisks; k++) { char lb[160]; lc_disk_label(&a.st.lc.disks[k], lb, sizeof lb);\n'
    '                printf("label %s %s\\n", a.st.lc.disks[k].dev, lb); }\n'
    '        } else if (!strncmp(v, "key:", 4)) {')


class _Drivers2001:
    def __init__(self, d: Path) -> None:
        self.d, self.built = d, None
        (d / "driver.c").write_text(DRIVER_2001, newline="\n")

    def __call__(self) -> Path:
        if self.built is None:
            self.built = _compile(self.d, "hits2001", "-DWITH_HITS")
        return self.built


@pytest.fixture(scope="module")
def drv(tmp_path_factory) -> _Drivers2001:
    return _Drivers2001(tmp_path_factory.mktemp("usbmapdrv"))


def hits_named(driver: Path, gui_dir: Path) -> list[str]:
    return [line.split(" ", 5)[5] for line in gui(driver, gui_dir, "hits") if line.startswith("hit ")]


def press(driver: Path, gui_dir: Path, what: str) -> list[str]:
    return tokens(gui(driver, gui_dir, "click:%d,%d" % hit(driver, gui_dir, what)))


@native_gui
def test_a_click_on_a_name_or_a_switch_is_the_keys_to_it(drv, tmp_path):
    _out, _usb = stick(tmp_path, TO_MAP)
    gui_dir = tmp_path / "run" / "gui"
    names = hits_named(drv(), gui_dir)
    for c in ("SATA 0", "SATA 4", "NVMe 1", "NVMe 2"):
        assert f"active:{c}" in names, names
    assert "active:SATA 5" not in names and "name:SATA 1" not in names   # the DVD drive; a row that is off has no name
    assert press(drv(), gui_dir, "name:SATA 2") == ["KEY DOWN", "KEY DOWN", "KEY TAB"]
    assert press(drv(), gui_dir, "active:NVMe 1") == ["KEY DOWN"] * 5 + ["KEY LEFT"]   # past the DVD drive
    assert press(drv(), gui_dir, "addlink") == ["CHAR 2b"]
    assert press(drv(), gui_dir, "btn:שמירה") == ["KEY F2"] and press(drv(), gui_dir, "btn:ביטול") == ["KEY ESC"]
    keys = press(drv(), gui_dir, "active:SATA 3")
    assert keys == ["KEY DOWN", "KEY DOWN", "KEY DOWN", "KEY LEFT"]
    _out, usb = stick(tmp_path, TO_MAP + keys + ["KEY F2"])
    assert "sata:4" in saved(usb)["drawer_slots"][MODEL]


@native_gui
def test_a_click_while_a_name_is_typed(drv, tmp_path):
    _out, _usb = stick(tmp_path, TO_MAP + chars("A"))
    gui_dir = tmp_path / "run" / "gui"
    assert press(drv(), gui_dir, "active:SATA 0") == ["KEY TAB", "KEY LEFT"]
    assert press(drv(), gui_dir, "name:SATA 2") == ["KEY DOWN", "KEY DOWN"]


@native_gui
def test_the_add_form_by_mouse(drv, tmp_path):
    _out, _usb = stick(tmp_path, TO_MAP + ["CHAR 2b"])
    gui_dir = tmp_path / "run" / "gui"
    assert press(drv(), gui_dir, "add:NVMe") == ["KEY TAB", "KEY TAB", "KEY LEFT"]
    assert press(drv(), gui_dir, "add:name") == ["KEY TAB"]
    assert press(drv(), gui_dir, "add:go") == ["KEY ENTER"] and press(drv(), gui_dir, "add:cancel") == ["KEY ESC"]


@native_gui
def test_alt_shift_types_hebrew_into_the_name_and_the_chip_says_so(drv, tmp_path):
    _out, _usb = stick(tmp_path, TO_MAP + chars("a"))
    gui_dir = tmp_path / "run" / "gui"
    assert "lang" in hits_named(drv(), gui_dir)                      # the chip in the field, to click
    sent = tokens(gui(drv(), gui_dir, "char:74:5d0", "layout", "char:74:5d0", "char:31:31",
                      "click:%d,%d" % hit(drv(), gui_dir, "lang"), "char:74:5d0"))
    assert sent == ["CHAR 74", "CHAR 5d0", "CHAR 31", "CHAR 74"], sent   # the chip's click switched back
    out, _usb = stick(tmp_path, TO_MAP + chars("a") + sent)
    assert conns(frame_of(out))[0][5] == "atא1t"


@native_gui
def test_f2_belongs_to_the_window_only_while_it_is_open(drv, tmp_path):
    _out, _usb = stick(tmp_path, TO_MAP)
    assert "window 1" in gui(drv(), tmp_path / "run" / "gui", "window")
    _out, _usb = stick(tmp_path, TO_MAP + ["KEY ESC"])
    assert "window 0" in gui(drv(), tmp_path / "run" / "gui", "window")


@native_gui
def test_the_clone_screen_says_the_name_instead_of_disk_n(drv, tmp_path):
    state = tmp_path / "state"
    state.write_text("lc_screen=local\nlc_usb=1\nlc_inv=done\nlc_phase=idle\n"
                     "lc_disk=1|sda|M|S1|1000|ok|ok|Windows|1|1|1000|1||1|SATA 0|||מגירה עליונה\n"
                     "lc_disk=3|sdb|M|S3|1000|ok|ok|Windows|1|1|1000|1||2|SATA 2||\n", encoding="utf-8")
    gui_dir = tmp_path / "g"; gui_dir.mkdir()
    lines = gui(drv(), gui_dir, f"state:{posix(state)}", "labels")
    assert [line for line in lines if line.startswith("label ")] == ["label sda מגירה עליונה", "label sdb דיסק 2"], lines


@native_gui
def test_the_lc_disk_parser_has_room_for_every_field(tmp_path):
    """lc_split writes <max> pointers: lc_disk's 18 (#2001) into f[16] was out of bounds -- f[16] itself
    already since #1921. gcc sees it at -O2 (-Warray-bounds); the GUI is built at -O2."""
    from test_cloner_gui import BASH, GUI
    out = subprocess.run([BASH, "-c", 'cc -O2 -Wall -Werror=array-bounds -std=c11 -D_GNU_SOURCE -I"$1/src" '
                          '$(pkg-config --cflags pangocairo cairo libdrm) -c "$1/src/lc_state.c" -o "$2"',
                          "_", posix(GUI), posix(tmp_path / "lc_state.o")],
                         capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
