"""#1920 -- the stick's log under a hard schema, and which device is the stick.

Two things that must never be "close enough":

* a report is DATA ONLY -- an unknown key, a serial/SMART key at any depth, a wrong type is rejected
  whole, with the reason (Nadav 20/10: the stick keeps nothing about disks);
* a partition is the stick only on positive evidence from one ``lsblk`` tree (USB bus, FAT, the
  IMGSRV_USB sibling, nothing mounted). A label alone is not the stick (#2431): the Hyper-V disk that
  arrives as SCSI is refused by name, not mounted.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from server.storage import locations as sl
from server.usbstick import device, records

GOOD = {
    "mac": "", "source": {"dev": "sda", "port": 1},
    "targets": [{"dev": "sdb", "port": 2, "state": "done", "error": "", "ntfs_note": ""}],
    "shrunk": False, "source_restore": "", "stopped": False, "bytes": 85_000_000_000, "elapsed_s": 1500,
    "shrink_path": None, "snapshot_teardown": "", "info_s": 12, "resize_s": None, "relocations_mb": None,
    "copy_s": 1000, "verify_s": 400, "restore_s": None,
    "machine": {"vendor": "HP", "product": "HP Compaq 8000 Elite SFF PC"}, "controller": "ide|sda=ata_piix"}


def parse(name="clone-20261106-184400.json", **over):
    doc = {**GOOD, **over}
    return records.parse_report(name, json.dumps(doc).encode())


def rejected(**over):
    with pytest.raises(records._Bad) as exc:
        parse(**over)
    return str(exc.value)


# --- the schema ---------------------------------------------------------------------------


def test_a_good_report_becomes_a_record_about_the_machine_only():
    rec = parse()
    assert rec.recorded_at == "2026-11-06T18:44:00Z"
    assert rec.model == "HP Compaq 8000 Elite SFF PC"
    assert (rec.controller, rec.driver) == ("ide", "ata_piix")
    assert rec.copy_mbps == 85 and rec.verify_mbps == 212          # bytes / stage seconds / 1e6
    assert rec.result == "ok" and (rec.targets, rec.targets_failed) == (1, 0)
    assert not any("serial" in k or "smart" in k for k in rec.stored())


def test_the_record_id_is_its_content_not_the_file():
    assert parse().record_id == parse().record_id
    assert parse().record_id != parse(copy_s=500).record_id
    assert parse().record_id != parse(name="clone-20261106-184401.json").record_id


@pytest.mark.parametrize("where", ["top", "source", "target", "machine"])
def test_a_serial_at_any_depth_rejects_the_whole_record_by_name(where):
    doc = json.loads(json.dumps(GOOD))
    {"top": doc, "source": doc["source"], "target": doc["targets"][0], "machine": doc["machine"]}[where]["serial"] = "WD-X1"
    with pytest.raises(records._Bad) as exc:
        records.parse_report("clone-20261106-184400.json", json.dumps(doc).encode())
    assert str(exc.value) == "שדה serial אסור בסכמה"


@pytest.mark.parametrize("key", ["smart_attrs", "SMART", "disk_wwn", "wwid", "volume_uuid", "disk_guid", "SerialNumber"])
def test_smart_and_disk_identity_keys_are_named_forbidden(key):
    assert rejected(**{key: 1}) == f"שדה {key} אסור בסכמה"


def test_an_unknown_key_is_not_in_the_schema_and_a_weird_key_is_not_echoed():
    assert rejected(extra=1).startswith("שדה extra אינו בסכמה")
    assert "<script>" not in rejected(**{"<script>": 1})


@pytest.mark.parametrize("over,why", [
    ({"bytes": "85"}, "bytes"), ({"bytes": -1}, "bytes"), ({"bytes": True}, "bytes"), ({"copy_s": 1.5}, "copy_s"),
    ({"copy_s": 10 ** 12}, "copy_s"), ({"stopped": "no"}, "stopped"), ({"targets": []}, "targets"),
    ({"targets": [{"state": "running"}]}, "מצב יעד"), ({"targets": ["x"]}, "יעד"), ({"machine": "HP"}, "machine"),
    ({"shrink_path": "../etc"}, "shrink_path"), ({"controller": "ide|sda=../../x"}, "controller"),
    ({"controller": 5}, "controller"), ({"source": 3}, "source"),
])
def test_a_wrong_type_or_range_rejects_the_record(over, why):
    assert why in rejected(**over)


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b"null", b'{"bytes": NaN}', b"\xff\xfe", pytest.param(b"x" * 70000, id="too-big")])
def test_not_json_not_an_object_or_too_big_is_rejected(raw):
    with pytest.raises(records._Bad):
        records.parse_report("clone-20261106-184400.json", raw)


@pytest.mark.parametrize("name", ["clone-20261306-184400.json", "clone.json", "../clone-20261106-184400.json",
                                  "clone-20261106-184400.json.next", "report-20261106-184400.json"])
def test_the_file_name_is_matched_by_a_fixed_pattern(name):
    with pytest.raises(records._Bad):
        records.parse_report(name, json.dumps(GOOD).encode())


def test_free_text_from_the_stick_is_accepted_by_the_schema_and_never_kept():
    rec = parse(targets=[{"dev": "sdb", "port": 1, "state": "failed", "error": "מקום חסר ב-/dev/sdb", "ntfs_note": "x"}],
                source_restore="whatever the stick wrote")
    assert rec.result == "failed" and rec.targets_failed == 1
    assert "sdb" not in json.dumps(rec.stored(), ensure_ascii=False)


def test_stopped_wins_over_the_targets_and_a_machine_without_a_model_is_kept_without_one():
    assert parse(stopped=True).result == "stopped"
    assert parse(machine={"vendor": "ASUS", "product": "To Be Filled By O.E.M."}).model is None


@pytest.mark.parametrize("raw,want", [
    ("ide|sda=ata_piix", ("ide", "ata_piix")), ("other|sda=ahci", ("ahci", "ahci")),
    ("other|sda=ahci sdb=ahci", ("ahci", "ahci")), ("|sda=pata_via", ("ide", "pata_via")),
    ("other|nvme0n1=nvme", ("other", "nvme")), ("ide|sda=ahci sdb=ata_piix", ("ide", "ata_piix")),
    ("", (None, None)), ("|", (None, None)),
])
def test_the_controller_is_read_from_the_drivers_and_empty_is_not_ahci(raw, want):
    assert records.parse_controller(raw) == want


def test_reading_a_log_counts_what_it_skips_and_keeps_what_it_rejects(tmp_path: Path):
    rep = tmp_path / "reports"
    rep.mkdir()
    (rep / "clone-20261106-184400.json").write_text(json.dumps(GOOD))
    (rep / "clone-20261105-112000.json").write_text(json.dumps({**GOOD, "serial": "X"}))
    (rep / "clone-20261104-100000.json").write_text("{")
    (rep / "diag-20261106.txt").write_text("not a report")
    (rep / "clone-20261106-184400.json.next").write_text("{}")
    (rep / "sub").mkdir()
    got = records.read_reports(tmp_path)
    assert [r.recorded_at for r in got.records] == ["2026-11-06T18:44:00Z"]
    assert {r.name: r.reason for r in got.rejected} == {
        "clone-20261105-112000.json": "שדה serial אסור בסכמה", "clone-20261104-100000.json": "הקובץ אינו JSON תקין"}
    assert got.ignored == 3 and got.beyond_cap == 0


def test_no_reports_directory_is_an_empty_log_and_an_unreadable_one_is_an_error(tmp_path: Path, monkeypatch):
    assert records.read_reports(tmp_path).records == []
    (tmp_path / "reports").mkdir()
    def denied(path):
        raise PermissionError(13, "Permission denied")

    # only records' own view of os: patching the real os.scandir would break pytest's own cleanup
    monkeypatch.setattr(records, "os", types.SimpleNamespace(scandir=denied))
    with pytest.raises(records.ReadError):
        records.read_reports(tmp_path)


def test_more_reports_than_the_cap_are_counted_not_silently_dropped(tmp_path: Path, monkeypatch):
    rep = tmp_path / "reports"
    rep.mkdir()
    for n in range(5):
        (rep / f"clone-2026110{n + 1}-100000.json").write_text(json.dumps(GOOD))
    monkeypatch.setattr(records, "MAX_FILES", 3)
    got = records.read_reports(tmp_path)
    assert got.beyond_cap == 2 and len(got.records) + len(got.rejected) == 3


# --- which device is the stick ----------------------------------------------------------------


def disk(name="sdb", tran="usb", serial="0123456789AB", children=(), **kw):
    return {"name": name, "pkname": None, "path": f"/dev/{name}", "type": "disk", "size": 15_000_000_000, "model": "Cruzer",
            "vendor": "SanDisk", "serial": serial, "ro": False, "rm": True, "tran": tran, "fstype": None,
            "label": None, "uuid": None, "mountpoints": [None], "children": list(children), **kw}


def part(name, label, fstype="vfat", uuid="7C2E-41AB", mount=None, **kw):
    return {"name": name, "pkname": None, "path": f"/dev/{name}", "type": "part", "size": 128_000_000, "model": None,
            "vendor": None, "serial": None, "ro": False, "rm": True, "tran": None, "fstype": fstype, "label": label,
            "uuid": uuid, "mountpoints": [mount], **kw}


def stick_disk(name="sdb", **kw):
    return disk(name, children=[part(f"{name}1", "IMGSRV_USB", uuid="1111-2222"), part(f"{name}2", "IMGSRV_DATA")], **kw)


def internal(name="sda"):
    return disk(name, tran="sata", serial="WD-INTERNAL", rm=False, children=[
        part(f"{name}1", "Windows", fstype="ntfs", uuid="ABCDEF0123456789", mount="/"),
    ])


def tree(*disks):
    return {"blockdevices": list(disks)}


def test_a_usb_disk_with_both_partitions_is_the_stick_and_is_named_by_its_serial_not_by_sdx():
    got = device.classify(tree(internal(), stick_disk()))
    assert got.state == device.CONNECTED
    assert got.stick.device_id == "usb-0123456789AB" and got.stick.has_serial
    assert got.stick.partition == "/dev/sdb2" and got.stick.label == "IMGSRV_DATA"
    renamed = device.classify(tree(internal("sdc"), stick_disk("sdz")))
    assert renamed.stick.device_id == got.stick.device_id and renamed.stick.partition == "/dev/sdz2"


def test_a_stick_without_a_serial_is_named_by_the_data_partitions_uuid():
    got = device.classify(tree(stick_disk(serial="")))
    assert got.stick.device_id == "usb-7C2E-41AB" and not got.stick.has_serial


def test_the_by_id_link_is_carried_when_there_is_one():
    got = device.classify(tree(stick_disk()), {"/dev/sdb2": "/dev/disk/by-id/usb-SanDisk_Cruzer_0123456789AB-0:0-part2"})
    assert got.stick.by_id.endswith("-part2")


def test_both_lsblk_shapes_are_read_the_flat_one_too():
    d = stick_disk()
    flat = {"blockdevices": [{**d, "children": None}] + [{**c, "pkname": "sdb"} for c in d["children"]]}
    assert device.classify(flat).state == device.CONNECTED


def test_nothing_with_the_label_is_none_and_an_unreadable_tree_is_not_none():
    assert device.classify(tree(internal())).state == device.NONE
    assert device.classify({"blockdevices": "x"}).state == device.UNREAD
    assert device.classify([]).state == device.UNREAD


@pytest.mark.parametrize("tran", ["sata", "sas", "scsi", "", None])
def test_the_label_on_a_disk_that_is_not_on_the_usb_bus_is_refused_by_name(tran):
    """Hyper-V disk passthrough, or an internal drawer someone labelled IMGSRV_DATA (#2431)."""
    got = device.classify(tree(stick_disk(tran=tran)))
    assert got.state == device.UNSAFE and got.stick is None
    assert "אינו על אפיק USB" in got.reason and "/dev/sdb2" in got.reason


def test_no_boot_partition_next_to_it_means_it_is_not_ours():
    d = disk("sdb", children=[part("sdb1", "DATA"), part("sdb2", "IMGSRV_DATA")])
    got = device.classify(tree(d))
    assert got.state == device.UNSAFE and "IMGSRV_USB" in got.reason


def test_a_mounted_stick_is_left_alone():
    d = disk("sdb", children=[part("sdb1", "IMGSRV_USB"), part("sdb2", "IMGSRV_DATA", mount="/run/media/x")])
    got = device.classify(tree(d))
    assert got.state == device.UNSAFE and "/run/media/x" in got.reason


@pytest.mark.parametrize("what,kw", [("fs", {"fstype": "ext4"}), ("ro", {"ro": True})])
def test_not_fat_or_read_only_is_refused(what, kw):
    d = stick_disk()
    d["children"][1].update(kw)
    assert device.classify(tree(d)).state == device.UNSAFE


def test_two_sticks_are_ambiguous_not_a_pick():
    got = device.classify(tree(stick_disk("sdb", serial="AAA"), stick_disk("sdc", serial="BBB")))
    assert got.state == device.AMBIGUOUS and got.stick is None and "2" in got.reason


def test_an_unsafe_candidate_beside_a_real_stick_does_not_hide_the_stick_but_is_said():
    got = device.classify(tree(stick_disk(tran="sas", name="sda"), stick_disk("sdb")))
    assert got.state == device.CONNECTED and got.stick.partition == "/dev/sdb2"
    assert len(got.notes) == 1 and "/dev/sda2" in got.notes[0]


def test_scan_says_unread_when_lsblk_cannot_run_or_is_not_json():
    boom = lambda cmd, timeout=0: sl.HookResult(sl.UNCHECKED, "lsblk לא נמצא במערכת")
    assert device.scan(run=boom, by_id=dict).state == device.UNREAD
    junk = lambda cmd, timeout=0: sl.HookResult(sl.OK, payload={"stdout": "not json"})
    assert device.scan(run=junk, by_id=dict).state == device.UNREAD
    ok = lambda cmd, timeout=0: sl.HookResult(sl.OK, payload={"stdout": json.dumps(tree(stick_disk()))})
    assert device.scan(run=ok, by_id=dict).state == device.CONNECTED


def test_lsblk_is_asked_for_names_and_the_columns_the_decision_needs():
    for col in ("NAME", "PKNAME", "TRAN", "LABEL", "FSTYPE", "SERIAL", "MOUNTPOINTS", "RO"):
        assert col in device.LSBLK_COLUMNS.split(",")


# --- opening it ----------------------------------------------------------------------------------


class Calls:
    def __init__(self, scan_result, mount_ok=True, umount_ok=True):
        self.scan_result, self.log = scan_result, []
        self.mount_ok, self.umount_ok = mount_ok, umount_ok

    def scan(self):
        return self.scan_result

    def mount(self, fstype, source, target, opts=""):
        self.log.append(("mount", fstype, source, target, opts))
        return sl.HookResult(sl.OK if self.mount_ok else sl.FAILED, "" if self.mount_ok else "boom")

    def umount(self, target):
        self.log.append(("umount", target))
        return sl.HookResult(sl.OK if self.umount_ok else sl.FAILED, "" if self.umount_ok else "busy")


@pytest.fixture()
def not_mounted(monkeypatch):
    monkeypatch.setattr(device.sl, "is_mounted", lambda target: False)


def connected(serial="0123456789AB"):
    return device.classify(tree(stick_disk(serial=serial)))


def test_opening_read_only_mounts_ro_nosuid_nodev_noexec_and_unmounts(not_mounted):
    stick = connected().stick
    c = Calls(connected())
    with device.open_stick(stick, scanner=c.scan, mount=c.mount, umount=c.umount) as root:
        assert Path(root) == Path(device.MOUNT_POINT)
    assert c.log[0][:2] == ("mount", "vfat") and c.log[0][2] == "/dev/sdb2"
    opts = c.log[0][4].split(",")
    assert opts[0] == "ro" and {"nosuid", "nodev", "noexec"} <= set(opts)
    assert c.log[-1] == ("umount", device.MOUNT_POINT)


def test_opening_for_writing_is_rw_and_sync(not_mounted):
    stick = connected().stick
    c = Calls(connected())
    with device.open_stick(stick, write=True, scanner=c.scan, mount=c.mount, umount=c.umount):
        pass
    assert c.log[0][4].split(",")[:2] == ["rw", "sync"]


def test_the_stick_is_unmounted_even_when_the_body_raises(not_mounted):
    stick = connected().stick
    c = Calls(connected())
    with pytest.raises(RuntimeError):
        with device.open_stick(stick, scanner=c.scan, mount=c.mount, umount=c.umount):
            raise RuntimeError("boom")
    assert c.log[-1][0] == "umount"


def test_a_replaced_stick_is_not_opened(not_mounted):
    shown = connected("AAA").stick
    c = Calls(connected("BBB"))
    with pytest.raises(device.StickError, match="הוחלף"):
        with device.open_stick(shown, scanner=c.scan, mount=c.mount, umount=c.umount):
            pass
    assert c.log == []


@pytest.mark.parametrize("now", [device.Scan(device.NONE), device.Scan(device.UNSAFE, reason="unsafe-why"),
                                 device.Scan(device.UNREAD, reason="unread-why")])
def test_a_stick_that_is_no_longer_safe_is_not_mounted(not_mounted, now):
    c = Calls(now)
    with pytest.raises(device.StickError):
        with device.open_stick(connected().stick, write=True, scanner=c.scan, mount=c.mount, umount=c.umount):
            pass
    assert c.log == []


def test_a_busy_mount_point_is_not_used(monkeypatch):
    monkeypatch.setattr(device.sl, "is_mounted", lambda target: True)
    c = Calls(connected())
    with pytest.raises(device.StickError, match="תפוסה"):
        with device.open_stick(connected().stick, scanner=c.scan, mount=c.mount, umount=c.umount):
            pass
    assert c.log == []


def test_a_failed_mount_and_a_failed_unmount_are_both_said(not_mounted):
    stick = connected().stick
    c = Calls(connected(), mount_ok=False)
    with pytest.raises(device.StickError, match="העיגון נכשל"):
        with device.open_stick(stick, scanner=c.scan, mount=c.mount, umount=c.umount):
            pass
    c = Calls(connected(), umount_ok=False)
    with pytest.raises(device.StickError, match="נשאר מעוגן"):
        with device.open_stick(stick, scanner=c.scan, mount=c.mount, umount=c.umount):
            pass
