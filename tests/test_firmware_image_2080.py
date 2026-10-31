"""#2080 (mockup 06, Nadav 24/10) -- the firmware image of a machine, and "changed since the previous power-on".

Every hello carries boot mode, the SATA controller mode (ahci/ide/raid), the BIOS version and the disk
of each SATA connection.  The server keeps a snapshot when it changes and compares to the previous
power-on (or to the image "this is the right setting" saved).  A field the agent did not report is NOT a
change (an old agent must not raise warnings), and a disk that moved is "moved", not "gone and new".
"""

from __future__ import annotations

import pytest

from conftest import hello_body
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401 -- fixture

pytest.importorskip("fastapi")


def _disk(serial, port, model="Samsung SSD 870"):
    return {"dev": f"sd{chr(96 + port)}", "size_bytes": 500107862016, "model": model, "serial": serial,
            "removable": False, "scheme": "gpt", "has_data": False, "port": port, "link_gbps": 3.0}


def _hello(room_server, mac=CLONER1, *, mode="__", disks=None, bios="__", boot="uefi", product="HP Compaq 8100 Elite CMT"):
    body = hello_body(mac)
    body["firmware"] = boot
    if mode != "__":
        body["sata_mode"] = mode
    if bios != "__":
        body["bios"] = bios
    if product:
        body["inventory"] = {"dmi": {"product_name": product}, "pci": [], "tpm": None}
    body["disks"] = [] if disks is None else disks
    r = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text


def _fw(room_server, mac=CLONER1):
    r = room_server["admin"].get(f"/api/console/machines/{mac}/firmware")
    assert r.status_code == 200, r.text
    return r.json()


def _count(room_server, mac=CLONER1):
    return next(m for m in room_server["admin"].get("/api/console/machines").json() if m["mac"] == mac)["fw_changes"]


def test_a_controller_mode_change_is_a_warning_with_was_and_now(room_server):
    _hello(room_server, mode="ahci", bios={"version": "786H1 v01.13", "date": "2012-03-01"})
    assert _count(room_server) == 0, "the first image has nothing to compare to"
    _hello(room_server, mode="ide", bios={"version": "786H1 v01.13", "date": "2012-03-01"})
    fw = _fw(room_server)
    assert [(c["field"], c["was"], c["now"]) for c in fw["changes"]] == [("sata_mode", "ahci", "ide")]
    assert fw["changes"][0]["text"] == "מצב בקר הדיסקים השתנה: AHCI ← IDE"
    assert _count(room_server) == 1 and fw["current"]["bios_version"] == "786H1 v01.13"
    detail = [r["detail"] for r in room_server["ctx"].conn.execute("SELECT detail FROM journal WHERE event = 'fw_changed'")]
    assert len(detail) == 1 and "AHCI ← IDE" in detail[0]
    _hello(room_server, mode="ide")                    # the same again: no second journal line, no new snapshot
    assert room_server["ctx"].conn.execute("SELECT COUNT(*) FROM fw_snapshots").fetchone()[0] == 2


def test_a_field_the_agent_did_not_report_is_not_a_change(room_server):
    _hello(room_server, mode="ahci")
    _hello(room_server)                                # an old agent: no sata_mode at all
    _hello(room_server, mode="banana")                 # garbage is ignored too
    fw = _fw(room_server)
    assert fw["changes"] == [] and fw["current"]["sata_mode"] == "ahci", "kept the last reported value"
    assert _count(room_server) == 0


def test_a_moved_disk_is_moved_and_a_vanished_disk_is_gone(room_server):
    _hello(room_server, mode="ahci", disks=[_disk("A", 1), _disk("T", 2, "TOSHIBA DT01ACA050")])
    _hello(room_server, mode="ahci", disks=[_disk("A", 1), _disk("T", 3, "TOSHIBA DT01ACA050")])    # SATA1 -> SATA2
    fw = _fw(room_server)
    assert [(c["kind"], c["serial"], c["from"], c["to"]) for c in fw["changes"]] == [("moved", "T", 2, 3)]
    assert fw["changes"][0]["text"] == "דיסק T עבר מ-SATA 1 ל-SATA 2"
    rows = {r["connection"]: r for r in fw["slots"]}
    assert rows["SATA 2"]["note"] == "עבר מ-SATA 1" and rows["SATA 1"]["changed"] and rows["SATA 1"]["was"]["serial"] == "T"
    assert fw["gap_below_populated"] is True, "SATA 1 empty while SATA 2 is populated"
    _hello(room_server, mode="ahci", disks=[_disk("A", 1)])                                          # pulled out
    assert [(c["kind"], c["serial"]) for c in _fw(room_server)["changes"]] == [("gone", "T")]


def test_a_hello_without_a_disk_list_does_not_empty_the_connections(room_server):
    _hello(room_server, mode="ahci", disks=[_disk("A", 1)])
    body = hello_body(CLONER1); body.pop("disks", None)   # no "disks" key at all
    body["firmware"] = "uefi"
    assert room_server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    assert _fw(room_server)["current"]["slots"]["1"]["serial"] == "A"
    assert _count(room_server) == 0


def test_this_is_the_right_setting_saves_the_baseline_and_clears_the_warning(room_server):
    admin = room_server["admin"]
    _hello(room_server, mode="ahci")
    _hello(room_server, mode="ide")
    assert _count(room_server) == 1
    assert admin.post(f"/api/console/machines/{CLONER1}/firmware/accept").status_code == 200
    assert _count(room_server) == 0 and _fw(room_server)["ref_kind"] == "baseline"
    _hello(room_server, mode="raid")                    # compared to the SAVED image (ide), not to the previous hello
    assert [(c["was"], c["now"]) for c in _fw(room_server)["changes"]] == [("ide", "raid")]
    assert "fw_accepted" in [r["event"] for r in room_server["ctx"].conn.execute("SELECT event FROM journal")]


def test_compare_to_the_model_lists_the_peers_and_what_differs(room_server):
    _hello(room_server, CLONER1, mode="ide", boot="uefi")
    _hello(room_server, CLONER2, mode="ahci", boot="uefi")
    peers = _fw(room_server)["peers"]
    assert [(p["mac"], p["differs"]) for p in peers] == [(CLONER2, ["sata_mode"])]
    _hello(room_server, CLONER2, mode="ahci", boot="uefi", product="Other model")
    assert _fw(room_server)["peers"] == []


def test_admin_only_and_unknown_machines(room_server):
    deploy, admin = room_server["deploy"], room_server["admin"]
    assert deploy.get(f"/api/console/machines/{CLONER1}/firmware").status_code == 403
    assert deploy.post(f"/api/console/machines/{CLONER1}/firmware/accept").status_code == 403
    assert admin.get("/api/console/machines/aa:bb:cc:dd:ee:ff/firmware").status_code == 404
    assert admin.post(f"/api/console/machines/{CLONER1}/firmware/accept").status_code == 409   # never reported: nothing to save
