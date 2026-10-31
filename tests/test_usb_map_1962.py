"""#1962 -- the drawer mapping on the clone stick, by machine model (Nadav 22/10).

The server's #1485 mapping (drawer k -> "sata:<ataN>" / "nvme:<PCI>") on a stick
with no server: chosen in the stick's settings screen, kept in settings.json on
IMGSRV_DATA under the machine's DMI product_name -- never a disk's serial -- and put
into response.json the way a server's hello carries it, so drawer_slots_refresh /
disk_drawer (slotnames.sh) number every screen by it. "החזר לברירת המחדל" removes
this model's list.

#2001 replaced the drawer-by-drawer editor with one window of every connection (name +
"פעיל / לא פעיל", tests/test_usb_map_window_2001.py); what stays here is the list by model.

The machine here: an "HP Compaq 8000 Elite SFF PC" with four SATA ports, disks on
ata1 (ST500) and ata3 (WD250), an NVMe at 0000:03:00.0, and the stick on sdz.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from test_cloner_gui import posix
from test_usb_clone import KIOSK, needs_jq, run
from test_usb_settings_1959_1960 import dialog_title

pytestmark = needs_jq

MODEL = "HP Compaq 8000 Elite SFF PC"
TO_MAP = ["KEY DOWN"] * 4 + ["KEY ENTER"]
TO_RESET = ["KEY DOWN"] * 7 + ["KEY ENTER", "KEY TAB", "KEY ENTER"]
SATA1_ON = ["KEY DOWN", "CHAR 20"]   # #2001: the window -- SATA 1 (empty, off by default) to "פעיל", F2 saves

STUBS = ('list_disks() { echo sda sdb nvme0n1 sdz; }; '
         'disk_port() { case "$1" in sda) echo 1 ;; sdb) echo 3 ;; esac; }; '
         'disk_pci_json() { [ "$1" = nvme0n1 ] && printf \',"pci":"0000:03:00.0"\'; return 0; }; '
         'echo sdz > "$RUN_DIR/usb-media.disks"; ')


def machine(tmp_path: Path, model: str | None = MODEL) -> Path:
    root = tmp_path / "sysroot"
    for n in (1, 2, 3, 4):
        (root / "sys" / "class" / "ata_port" / f"ata{n}").mkdir(parents=True, exist_ok=True)
    for dev, model_name, gb in (("sda", "ST500DM002", 500), ("sdb", "WDC WD2500", 250), ("nvme0n1", "Samsung 970", 256),
                                ("sdz", "SanDisk", 32)):
        d = root / "sys" / "block" / dev / "device"; d.mkdir(parents=True, exist_ok=True)
        (d / "model").write_text(model_name + "\n")
        (d.parent / "size").write_text(str(gb * 1000 * 1000 * 1000 // 512) + "\n")
    dmi = root / "sys" / "class" / "dmi" / "id"; dmi.mkdir(parents=True, exist_ok=True)
    if model is not None:
        (dmi / "product_name").write_text(model + "\n")
    elif (dmi / "product_name").exists():
        (dmi / "product_name").unlink()
    return root


def stick(tmp_path: Path, keys: list[str], then: str = "", model: str | None = MODEL, data: str = "mounted",
          settings: dict | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """The settings screen on this machine: usbset_open, the keys, the frame, then <then>."""
    usb = tmp_path / "usbdata"; usb.mkdir(exist_ok=True)
    if settings is not None:
        (usb / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    root = machine(tmp_path, model)
    script = (KIOSK + STUBS + f'echo {data} > "$RUN_DIR/usb.data"; USB_DATA_DIR={posix(usb)!r}; usbset_open; '
              + "".join(f'usbset_key {k!r}; ' for k in keys) + 'cat "$GUI_DIR/usbset.frame"; echo "@@"; ' + then)
    return run(tmp_path, script, IMAGESRV_MODE="usbclone", SYSROOT=posix(root)), usb


def saved(usb: Path) -> dict:
    return json.loads((usb / "settings.json").read_text(encoding="utf-8"))


def test_saved_by_model_in_force_and_every_screen_numbers_by_it(tmp_path):
    out, usb = stick(tmp_path, TO_MAP + SATA1_ON + ["KEY F2"],
                     then='cat "$RUN_DIR/response.json"; echo "@@"; cat "$RUN_DIR/drawer_slots"; echo "@@"; '
                          'disk_drawer sdb 3; echo; disk_drawer sda 1; echo; disk_drawer nvme0n1 0')
    frame, answer, slots, drawers = out.stdout.split("@@")
    want = ["sata:1", "sata:2", "sata:3", "nvme:0000:03:00.0"]
    assert saved(usb) == {"drawer_slots": {MODEL: want}}, out.stdout + out.stderr
    assert dialog_title(frame) is None                              # saved: the window closed
    assert json.loads(answer)["drawer_slots"] == want
    assert slots.split() == ["ok", "1|sata:1", "2|sata:2", "3|sata:3", "4|nvme:0000:03:00.0"]
    assert drawers.strip().split("\n") == ["3|SATA 2", "1|SATA 0", "4|NVMe 03:00.0"], drawers
    assert "ST500" not in json.dumps(saved(usb)) and "serial" not in json.dumps(saved(usb))   # nothing about a disk


def test_another_model_gets_the_default_not_this_models_mapping(tmp_path):
    stick(tmp_path, TO_MAP + SATA1_ON + ["KEY F2"])
    out, usb = stick(tmp_path, [], model="HP Compaq 8100 Elite SFF PC",
                     then='usb_settings_load; usb_answer; jq -c ".drawer_slots" "$RUN_DIR/response.json"; disk_drawer sdb 3')
    tail = out.stdout.split("@@")[1].split()
    # #2001: the default -- every connection with a disk, in order
    assert tail[0] == '["sata:1","sata:3","nvme:0000:03:00.0"]' and tail[1] == "2|SATA", out.stdout + out.stderr
    assert MODEL in saved(usb)["drawer_slots"]                      # still on the stick, for its own model


def test_reset_removes_this_models_mapping_and_keeps_another_models(tmp_path):
    other = {"HP Compaq 8100 Elite SFF PC": ["sata:2", "sata:1"]}
    out, usb = stick(tmp_path, TO_MAP + SATA1_ON + ["KEY F2"], settings={"drawer_slots": dict(other)})
    assert saved(usb)["drawer_slots"][MODEL][1] == "sata:2", out.stdout
    out, usb = stick(tmp_path, TO_RESET, then='jq -c ".drawer_slots" "$RUN_DIR/response.json"')
    assert saved(usb) == {"min_free_gb": 20, "readback_mode": "sample", "crc_warn_threshold": 500, "temp_warn_c": 60, "cloner_beep": True, "poweroff_s": 20,
                          "drawer_slots": other}, out.stdout + out.stderr
    assert out.stdout.split("@@")[1].split() == ['["sata:1","sata:3","nvme:0000:03:00.0"]']   # the default again


def test_reset_says_what_happens_to_the_mapping(tmp_path):
    out, _usb = stick(tmp_path, ["KEY DOWN"] * 7 + ["KEY ENTER"])
    assert "d.kv\tמיפוי חיבורים\tברירת המחדל — כל חיבור שיש בו דיסק, לפי הסדר\t0" in out.stdout, out.stdout


def test_a_mapping_not_valid_on_the_stick_is_refused_aloud_and_not_used(tmp_path):
    out, _usb = stick(tmp_path, [], settings={"drawer_slots": {MODEL: ["sata:1", "sata:1"]}},
                      then='usb_settings_load 2>/dev/null; echo "$USB_NOTICE"; usb_answer; jq -c ".drawer_slots" "$RUN_DIR/response.json"')
    notice, ds = out.stdout.split("@@")[1].strip().split("\n")
    assert f"drawer_slots[{MODEL}]" in notice and "נדחו" in notice, out.stdout
    assert ds == '["sata:1","sata:3","nvme:0000:03:00.0"]'          # the default, not the refused list


def test_without_a_model_nothing_is_saved_and_it_is_said(tmp_path):
    out, usb = stick(tmp_path, TO_MAP + SATA1_ON + ["KEY F2"], model=None)
    assert "d.alert\t<b>לא נשמר:</b> לא זוהה דגם המחשב (DMI) — אין לפי מה לשמור את המיפוי.\t" in out.stdout, out.stdout + out.stderr
    assert not (usb / "settings.json").exists()


def test_a_save_that_does_not_read_back_is_said(tmp_path):
    pre_mv = 'mv() { case "$2" in *settings.json) return 0 ;; esac; command mv "$@"; }; '
    usb = tmp_path / "usbdata"; usb.mkdir(exist_ok=True)
    root = machine(tmp_path)
    script = (KIOSK + STUBS + f'echo mounted > "$RUN_DIR/usb.data"; USB_DATA_DIR={posix(usb)!r}; usbset_open; '
              + "".join(f'usbset_key {k!r}; ' for k in TO_MAP + SATA1_ON) + pre_mv + 'usbset_key "KEY F2"; cat "$GUI_DIR/usbset.frame"')
    out = run(tmp_path, script, IMAGESRV_MODE="usbclone", SYSROOT=posix(root))
    assert "הקריאה החוזרת לא החזירה את אותו מיפוי" in out.stdout, out.stdout + out.stderr
    assert dialog_title(out.stdout) == "מיפוי חיבורים"
