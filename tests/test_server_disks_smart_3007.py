"""#3007: the health page says the SMART of the server's own disks.

The work server ran all of 12/11 on a disk whose SMART said FAILED (Samsung
MZ7TY256: 173 Wear_Leveling_Count VALUE 001 < THRESH 005, FAILING_NOW);
smartd's mail had nowhere to go and the console said nothing. Now one row per
physical disk under the system and the library, in three states by positive
evidence -- PASSED, FAILED (with the failing attribute by name), and "not
checked" with the reason -- plus this boot's ATA errors and a lowered SATA
link by disk name. smartctl's output is injected; nothing here reads a real disk.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.system import server_disks
from test_server_health import health_server  # noqa: F401 -- the fixture

LINUX = pytest.mark.skipif(not hasattr(os, "major"), reason="st_dev -> /sys/dev/block is Linux")

# smartctl -H -A -j, trimmed to what the verdict reads (the work server's disk, 12/11)
FAILED = {"model_name": "SAMSUNG MZ7TY256HDHP-000L7", "smart_status": {"passed": False},
          "ata_smart_attributes": {"table": [
              {"id": 5, "name": "Reallocated_Sector_Ct", "when_failed": ""},
              {"id": 173, "name": "Wear_Leveling_Count", "when_failed": "now"},
              {"id": 199, "name": "UDMA_CRC_Error_Count", "when_failed": ""}]}}
PASSED = {"model_name": "Samsung SSD 860 EVO 500GB", "smart_status": {"passed": True},
          "ata_smart_attributes": {"table": [{"id": 199, "name": "UDMA_CRC_Error_Count", "when_failed": ""}]}}
PAST = {"smart_status": {"passed": True},
        "ata_smart_attributes": {"table": [{"id": 190, "name": "Airflow_Temperature_Cel", "when_failed": "past"}]}}
VIRTUAL = {"model_name": "VMware Virtual disk", "smartctl": {"messages": [
    {"string": "SMART support is: Unavailable - device lacks SMART capability.", "severity": "information"}]}}
NVME_FAILED = {"model_name": "WD PC SN520", "smart_status": {"passed": False, "nvme": {"value": 4}}}


def run(data, rc=0):
    return rc, json.dumps(data), ""


@pytest.mark.parametrize(("got", "state", "words"), [
    (run(FAILED, 8), "bad", ["FAILED", "173 Wear_Leveling_Count", "להחליף את הדיסק"]),
    (run(NVME_FAILED, 8), "bad", ["FAILED"]),
    (run(PASSED), "ok", ["SMART: PASSED"]),
    (run(PAST, 32), "warn", ["נכשל בעבר", "190 Airflow_Temperature_Cel"]),
    (run(VIRTUAL, 4), "unknown", ["אין SMART", "VMware Virtual disk"]),
    (run(PASSED, 2), "unknown", ["לא הצליח לפתוח", "/dev/sda"]),
    ((None, "", "smartctl אינו מותקן בשרת"), "unknown", ["smartctl אינו מותקן", "לא נבדק"]),
    ((0, "smartctl 7.4 -- not json", ""), "unknown", ["לא נקרא", "לא נבדק"]),
])
def test_each_verdict_is_by_positive_evidence(got, state, words):
    st, detail = server_disks.smart_verdict(*got, "sda")
    assert st == state, detail
    for word in words:
        assert word in detail, detail


def test_a_cumulative_crc_count_alone_is_not_a_verdict():
    """#872: 214,655 CRC errors on the work server, but 199 never failed -- the row is SMART's word."""
    data = {"smart_status": {"passed": True}, "ata_smart_attributes": {"table": [
        {"id": 199, "name": "UDMA_CRC_Error_Count", "when_failed": "", "raw": {"value": 214655}}]}}
    assert server_disks.smart_verdict(*run(data), "sda") == ("ok", "SMART: PASSED")


# --- the disks under a path, through partitions, md and dm ---------------------------------------

def _sys(tmp_path: Path, target: str) -> Path:
    """A /sys with dev/block/<the real st_dev of tmp_path> -> ``target`` (under devices/)."""
    root = tmp_path / "sys"
    st = os.stat(tmp_path)
    link = root / "dev" / "block" / f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
    link.parent.mkdir(parents=True)
    dest = root / "devices" / target
    dest.mkdir(parents=True, exist_ok=True)
    link.symlink_to(dest)
    return root


def _partition(root: Path, disk: str, part: str, ata: str = "ata1") -> Path:
    path = root / "devices" / "pci0000:00" / ata / "host0" / "block" / disk / part
    path.mkdir(parents=True, exist_ok=True)
    (path / "partition").write_text("2\n")
    (root / "block").mkdir(exist_ok=True)
    if not (root / "block" / disk).exists():
        (root / "block" / disk).symlink_to(path.parent)
    return path


@LINUX
def test_a_partition_is_its_disk(tmp_path):
    root = _sys(tmp_path, "pci0000:00/ata1/host0/block/sda/sda2")
    _partition(root, "sda", "sda2")
    assert server_disks.disks_of(str(tmp_path), root) == ["sda"]
    assert server_disks.ata_port("sda", root) == "ata1"


@LINUX
def test_an_md_array_is_every_member_disk(tmp_path):
    root = _sys(tmp_path, "virtual/block/md0")
    slaves = root / "devices" / "virtual" / "block" / "md0" / "slaves"
    slaves.mkdir()
    for disk, ata in (("sda", "ata1"), ("sdb", "ata2")):
        (slaves / f"{disk}2").symlink_to(_partition(root, disk, f"{disk}2", ata))
    assert server_disks.disks_of(str(tmp_path), root) == ["sda", "sdb"]


@LINUX
def test_a_device_that_is_not_in_sys_is_not_found_not_guessed(tmp_path):
    assert server_disks.disks_of(str(tmp_path), tmp_path / "empty-sys") is None
    rows = server_disks.disk_rows({"מערכת": str(tmp_path)}, sys_root=tmp_path / "empty-sys",
                                  smartctl=lambda d: pytest.fail("no disk, no smartctl"), kernel=lambda: "")
    assert [(r["id"], r["state"]) for r in rows] == [("server_disk:מערכת", "unknown")]
    assert "לא נקבע על איזה דיסק" in rows[0]["detail"]


# --- the rows -------------------------------------------------------------------------------------

KERNEL_1112 = """ata1.00: exception Emask 0x10 SAct 0x0 SErr 0x4050002 action 0x6 frozen
ata1.00: irq_stat 0x08000000, interface fatal error
ata1: SError: { UnrecovData CommWake Handshk }
ata1: hard resetting link
ata1: SATA link up 6.0 Gbps (SStatus 133 SControl 300)
ata1: limiting SATA link speed to 3.0 Gbps
ata2: SATA link up 6.0 Gbps (SStatus 133 SControl 300)
"""


@LINUX
def test_one_row_per_disk_with_its_roles_and_this_boots_ata_errors(tmp_path, caplog):
    root = _sys(tmp_path, "pci0000:00/ata1/host0/block/sda/sda2")
    _partition(root, "sda", "sda2")
    seen = []

    def smartctl(disk):
        seen.append(disk)
        return run(PASSED)
    with caplog.at_level(logging.WARNING, logger="imagesrv.server_disks"):
        rows = server_disks.disk_rows({"מערכת": str(tmp_path), "ספרייה": str(tmp_path)}, sys_root=root,
                                      smartctl=smartctl, kernel=lambda: KERNEL_1112)
    assert seen == ["sda"], "one disk under both roles is read once"
    assert len(rows) == 1 and rows[0]["id"] == "server_disk:sda"
    assert rows[0]["label"] == "דיסק השרת sda (מערכת, ספרייה)"
    assert rows[0]["state"] == "warn", "SMART PASSED, but the link dropped this boot"
    assert "שגיאות ATA ב-ata1" in rows[0]["detail"] and "3.0 Gbps" in rows[0]["detail"], rows[0]["detail"]
    assert "server_disk_ata disk=sda port=ata1" in caplog.text and "link=3.0 Gbps" in caplog.text


@LINUX
def test_a_failed_disk_stays_red_and_an_unread_kernel_log_is_said(tmp_path):
    root = _sys(tmp_path, "pci0000:00/ata1/host0/block/sda/sda2")
    _partition(root, "sda", "sda2")
    rows = server_disks.disk_rows({"מערכת": str(tmp_path)}, sys_root=root,
                                  smartctl=lambda d: run(FAILED, 8), kernel=lambda: None)
    assert rows[0]["state"] == "bad"
    assert "173 Wear_Leveling_Count" in rows[0]["detail"]
    assert "יומן הקרנל לא נקרא" in rows[0]["detail"]


@LINUX
def test_another_ports_errors_are_not_this_disks(tmp_path):
    root = _sys(tmp_path, "pci0000:00/ata2/host1/block/sdb/sdb1")
    _partition(root, "sdb", "sdb1", "ata2")
    rows = server_disks.disk_rows({"ספרייה": str(tmp_path)}, sys_root=root,
                                  smartctl=lambda d: run(PASSED), kernel=lambda: KERNEL_1112)
    assert (rows[0]["state"], rows[0]["detail"]) == ("ok", "SMART: PASSED")


def test_the_hook_reads_smartctl_at_most_once_per_ttl():
    calls, now = [], [0.0]

    def fake(paths, **kw):
        calls.append(1)
        return [{"id": "server_disk:sda", "state": "ok"}]
    cached = server_disks.Cached({"מערכת": "/"}, ttl=300, clock=lambda: now[0])
    original, server_disks.disk_rows = server_disks.disk_rows, fake
    try:
        cached(); now[0] = 299; cached()
        assert len(calls) == 1
        now[0] = 300; cached()
        assert len(calls) == 2
    finally:
        server_disks.disk_rows = original


# --- wiring ---------------------------------------------------------------------------------------

def test_main_wires_the_system_and_the_library():
    from server import main
    runtime = SimpleNamespace(health_hooks={}, ctx=SimpleNamespace(library=SimpleNamespace(root=Path("/srv/x"))))
    main._wire_server_disks(runtime)
    hook = runtime.health_hooks["server_disks"]
    assert isinstance(hook, server_disks.Cached) and hook.paths == {"מערכת": "/", "ספרייה": "/srv/x"}


def test_the_health_page_carries_the_rows_only_when_wired(health_server):
    from server.system import health
    from test_server_health import SS_DNSMASQ
    assert health.default_hooks()["server_disks"] is None, "tests and direct runs read no real disk"
    base = {**health.default_hooks(),
            "ss": lambda: SS_DNSMASQ, "unit_active": lambda n: "active",
            "http_get": lambda u: 200, "http_size": lambda u: (200, 31_000_000),
            "interfaces": lambda: [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
            "tftp_root": lambda: health_server["tftp"], "udp_sender_pids": lambda: []}
    ctx = health_server["admin"].app.state.ctx
    row = {"id": "server_disk:sda", "label": "דיסק השרת sda (מערכת)", "state": "bad", "detail": "SMART: FAILED"}
    got = {r["id"]: r for r in health.collect(ctx, {**base, "server_disks": lambda: [row]}, "http://x")}
    assert got["server_disk:sda"] == row
    got = {r["id"] for r in health.collect(ctx, base, "http://x")}
    assert not any(i.startswith("server_disk") for i in got)
