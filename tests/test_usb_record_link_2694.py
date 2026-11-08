"""#2694 -- the link speed (SATA 1.5 / 3 / 6 Gb/s) in the clone report, as the server reads it.

The stick writes ``link_gbps`` into ``reports/clone-*.json`` (the slowest link of the clone's disks, null
= it could not read one). Here: the schema accepts it, rejects anything that is not one of the three
speeds, and -- the part that is easy to break -- a record the server already took keeps its id.
"""

from __future__ import annotations

import json

import pytest

from server.usbstick import records

NAME = "clone-20261106-184400.json"
GOOD = {
    "mac": "", "source": {"dev": "sda", "port": 1},
    "targets": [{"dev": "sdb", "port": 2, "state": "done", "error": "", "ntfs_note": ""}],
    "shrunk": False, "source_restore": "", "stopped": False, "bytes": 85_000_000_000, "elapsed_s": 1500,
    "shrink_path": None, "snapshot_teardown": "", "info_s": 12, "resize_s": None, "relocations_mb": None,
    "copy_s": 1000, "verify_s": 400, "restore_s": None,
    "machine": {"vendor": "HP", "product": "HP Compaq 8000 Elite SFF PC"}, "controller": "ide|sda=ata_piix"}
#: record_id of GOOD as parse_report computed it on origin/main (b250b3cf), before the link existed:
#: measured by running that version of records.py on GOOD, not derived from the code under test.
ID_BEFORE_THE_LINK = "cfddcd111965a4e4ff73b0e0e51a2bb6"


def parse(**over) -> records.Record:
    return records.parse_report(NAME, json.dumps({**GOOD, **over}).encode())


@pytest.mark.parametrize("raw,want", [(1.5, 1.5), (3, 3.0), (3.0, 3.0), (6, 6.0), (6.0, 6.0), (None, None)])
def test_the_three_speeds_and_null_are_accepted(raw, want):
    rec = parse(link_gbps=raw)
    assert rec.link_gbps == want and rec.stored()["link_gbps"] == want


def test_a_report_from_before_the_field_has_no_link_not_a_zero():
    assert parse().link_gbps is None


@pytest.mark.parametrize("raw", [0, 2, 12, -3.0, 7.5, 3.1, "3", "3.0", True, False, [3], {"gbps": 3}])
def test_anything_that_is_not_one_of_the_three_speeds_rejects_the_whole_record(raw):
    with pytest.raises(records._Bad, match="link_gbps"):
        parse(link_gbps=raw)


def test_a_record_the_server_already_took_keeps_its_id():
    # reports written before #2694 have no link: their ids are in usb_records, and a changed id would take them twice
    assert parse().record_id == ID_BEFORE_THE_LINK
    assert parse(link_gbps=None).record_id == ID_BEFORE_THE_LINK


def test_a_link_is_part_of_the_id_when_there_is_one():
    ids = {parse(link_gbps=v).record_id for v in (1.5, 3, 6)}
    assert len(ids) == 3 and ID_BEFORE_THE_LINK not in ids
    assert parse(link_gbps=3).record_id == parse(link_gbps=3.0).record_id      # 3 and 3.0 are the same speed


def test_the_key_passes_the_serial_and_smart_guard_of_the_schema():
    # link_gbps is a machine fact; the guard on key names (serial, smart, wwn ...) must not mistake it for a disk's identity
    assert not any(part in "link_gbps" for part in records.FORBIDDEN_FRAGMENTS)
    assert "link_gbps" in records.TOP_KEYS
