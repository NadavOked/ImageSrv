"""#1874 -- the clone stick's default mapping is by the real SATA number (Nadav 07/11).

No saved list: drawer k = sata:k, as the cloner -- SATA 0 = drawer 1, SATA 1 = 2, SATA 2 = 3 --
up to the last SATA with a disk, an empty one between disks is its drawer too; the NVMe with a
disk follow (#2001's rule). It was "every connection with a disk, in order": a PC with disks on
SATA 0 and SATA 2 got drawers 1 and 2, and SATA 2 was never the source. A saved list still wins.

The machine is test_usb_map_1962's: ata1-4, a disk on ata1 (sda) and ata3 (sdb), an NVMe at
0000:03:00.0, the stick on sdz.
"""

from __future__ import annotations

import json

from test_usb_map_1962 import MODEL, stick

#: usb_answer on the stick's settings, with the disks <disks> (an sh function body for list_disks) wired by <ports>
ANSWER = ('{redo}usb_settings_load; usb_answer; jq -c .drawer_slots "$RUN_DIR/response.json"; echo @@; '
          'disk_drawer sda 1; echo; disk_drawer sdb {sdb}; echo')


def live(tmp_path, redo: str = "", sdb: int = 3, settings: dict | None = None) -> tuple[list, list[str]]:
    out, _usb = stick(tmp_path, [], then=ANSWER.format(redo=redo, sdb=sdb), settings=settings)
    tail = out.stdout.split("@@", 1)[1].split("@@")
    assert len(tail) == 2, out.stdout + out.stderr
    return json.loads(tail[0]), tail[1].strip().split("\n")


def test_disks_on_sata_0_and_sata_2_are_drawers_1_and_3(tmp_path):
    slots, (d1, d3) = live(tmp_path)
    assert slots == ["sata:1", "sata:2", "sata:3", "nvme:0000:03:00.0"], slots   # the empty SATA 1 is drawer 2; the NVMe after the last SATA
    assert (d1, d3) == ("1|SATA 0", "3|SATA 2"), (d1, d3)


def test_disks_on_sata_0_and_sata_1_only_have_no_drawer_3(tmp_path):
    redo = ('list_disks() { echo sda sdb sdz; }; disk_port() { case "$1" in sda) echo 1 ;; sdb) echo 2 ;; esac; }; '
            'disk_pci_json() { return 0; }; ')
    slots, (d1, d2) = live(tmp_path, redo, sdb=2)
    assert slots == ["sata:1", "sata:2"], slots
    assert (d1, d2) == ("1|SATA 0", "2|SATA 1"), (d1, d2)


def test_the_nvme_comes_after_the_last_sata_with_a_disk(tmp_path):
    redo = ('list_disks() { echo sdb nvme0n1 sdz; }; disk_pci_json() { [ "$1" = nvme0n1 ] && printf \',"pci":"0000:03:00.0"\'; return 0; }; ')
    slots, _ = live(tmp_path, redo)
    assert slots == ["sata:1", "sata:2", "sata:3", "nvme:0000:03:00.0"], slots   # the last SATA with a disk is SATA 2


def test_a_saved_list_still_wins_over_the_default(tmp_path):
    slots, (d1, d3) = live(tmp_path, settings={"drawer_slots": {MODEL: ["sata:3", "sata:1"]}})
    assert slots == ["sata:3", "sata:1"], slots
    assert (d1, d3) == ("2|SATA 0", "1|SATA 2"), (d1, d3)


def test_with_no_disk_there_is_no_default_list(tmp_path):
    redo = 'list_disks() { echo sdz; }; '
    out, _usb = stick(tmp_path, [], then=redo + 'usb_settings_load; usb_answer; jq -c ".drawer_slots // \\"none\\"" "$RUN_DIR/response.json"')
    assert out.stdout.split("@@", 1)[1].strip() == '"none"', out.stdout + out.stderr
