"""#2848 (Nadav 11/11): the server enforces the same rule as the build machine and the cloner, end to end
over HTTP -- "there is no difference between the three". A red disk is refused as a TARGET and a yellow one
as a SOURCE, with the rule's own sentence; and "slow" belongs to the round it was measured in: round 1
slow (red), round 2 measured again and fine -- green, and picked as a target.

The sentences are written out here on purpose, not imported from ``server/disks/disk_rule.py``: with the
fix reverted this file still runs and fails on behaviour (the negative control), and
``test_disk_rule_parity_2848.py::test_the_server_file_says_the_rules_sentences`` holds them equal.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body  # noqa: E402
from test_server_room import CLONER1, room, room_body, room_server  # noqa: E402,F401

REASON_HE = {
    "smart_fail": "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת",
    "link_slow": "קישור SATA איטי — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו",
    "sata_issue": "בעיית SATA (הקישור התאפס או המגירה התנתקה) — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו",
    "write_slow": "נפסל כאיטי בבדיקת הכתיבה בסבב הזה — לא כותבים עליו בסבב הזה; אפשר להעתיק ממנו",
    "failed_last": "נכשל בשיכפול הקודם — לא מעתיקים ממנו; אפשר לכתוב עליו",
    "write_incomplete": "כתיבה לא הושלמה — לא מעתיקים ממנו; אפשר לכתוב עליו",
}

def _hello(room_server, mac: str, disks: list[dict]) -> None:
    body = hello_body(mac)
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1, "smart": "ok", **d}
                     for i, d in enumerate(disks)]
    r = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text


def _open(room_server, serials: list[str]):
    body = room_body(room_server, len(serials), {CLONER1: serials})
    return room_server["deploy"].post("/api/console/room", json=body)


@pytest.mark.parametrize("bad, reason", [
    ({"smart": "fail"}, "smart_fail"),
    ({"link_gbps": 1.5, "link_max_gbps": 6.0}, "link_slow"),
    ({"sata_issues": ["drawer_gone"]}, "sata_issue"),
])
def test_a_red_target_is_refused_with_the_rules_sentence(room_server, bad, reason):
    room_body(room_server, 2, {CLONER1: ["X", "Y"]})           # registers the drawers (#1410)
    _hello(room_server, CLONER1, [{"serial": "X", **bad}, {"serial": "Y"}])
    body = {"image_id": "img_7f3a91", "target_drives": 2,
            "target_slots": [{"mac": CLONER1, "ports": [1, 2]}]}
    r = room_server["deploy"].post("/api/console/room", json=body)
    assert r.status_code == 400, r.text
    assert REASON_HE[reason] in r.json()["detail"], r.text
    assert room(room_server["deploy"])["round"] is None


@pytest.mark.parametrize("yellow", [{"write_mark": "incomplete"}, {"smart": "failed_last"}])
def test_a_yellow_disk_is_a_target(room_server, yellow):
    room_body(room_server, 1, {CLONER1: ["X"]})
    _hello(room_server, CLONER1, [{"serial": "X", **yellow}])
    r = room_server["deploy"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 1, "target_slots": [{"mac": CLONER1, "ports": [1]}]})
    assert r.status_code == 200, r.text
    (d,) = room(room_server["deploy"])["machines"][0]["drawer_list"]
    assert (d["rule"]["color"], d["rule"]["target"], d["rule"]["source"]) == ("yellow", True, False), d["rule"]


def test_a_yellow_build_disk_is_never_the_source(room_server):
    from test_direct_server import BUILD, _open_direct, _register_build
    _register_build(room_server)
    _hello(room_server, BUILD, [{"serial": "B-sda", "write_mark": "incomplete"}])
    room_body(room_server, 1, {CLONER1: ["X"]})
    r = _open_direct(room_server["deploy"], [{"mac": CLONER1, "ports": [1]}])
    assert r.status_code == 400, r.text
    assert REASON_HE["write_incomplete"] in r.json()["detail"], r.text


def test_a_yellow_disk_is_never_captured(room_server):
    from test_direct_server import BUILD, _register_build
    _register_build(room_server)
    _hello(room_server, BUILD, [{"serial": "B-sda", "write_mark": "incomplete"}])
    r = room_server["admin"].post("/api/console/tasks/capture", json={
        "mac": BUILD, "disk": "sda", "name": "Windows 11 Base", "folder": "Office"})
    assert r.status_code == 409, r.text
    assert REASON_HE["write_incomplete"] in r.json()["detail"], r.text


SLOW_X = {"dev": "sda", "bytes_written": 0, "bytes_total": 100, "state": "failed",
          "error": "איטי — 33 MB/s, מתחת לסף 50 MB/s",
          "write_preflight": {"state": "slow", "reason": "איטי — 33 MB/s, מתחת לסף 50 MB/s", "mbps": 33}}
DONE_Y = {"dev": "sdb", "bytes_written": 100, "bytes_total": 100, "state": "done",
          "write_preflight": {"state": "ok", "reason": "", "mbps": 180}}


def test_slow_is_red_in_its_own_round_and_green_in_the_next(room_server):
    """Nadav 11/11: round 1 -- X slow, so red. Round 2 -- measured again: SMART ok, so green, and a target."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert _open(room_server, ["X", "Y"]).status_code == 200
    _hello(room_server, CLONER1, [{"serial": "X"}, {"serial": "Y"}])
    wave = room(deploy)["round"]["wave_session_id"]
    if room(deploy)["round"]["wave_state"] == "open":
        assert deploy.post("/api/console/room/start").status_code == 200
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing", "targets": [SLOW_X, {**DONE_Y, "state": "writing"}]
    }).status_code == 200
    x = {d["serial"]: d for d in room(deploy)["machines"][0]["drawer_list"]}["X"]
    assert x["write_slow"] is True
    assert (x["rule"]["color"], x["rule"]["target"], x["rule"]["target_reason"]) == ("red", False, "write_slow")
    assert anon.post("/api/v1/agent/progress", json={      # the wave ends: X failed slow, Y written
        "session_id": wave, "mac": CLONER1, "state": "partial", "targets": [SLOW_X, DONE_Y]}).status_code == 200
    _hello(room_server, CLONER1, [{"serial": "X"}, {"serial": "Y"}])

    assert deploy.post("/api/console/room/close", json={"confirm_name": "Office 2024 Standard"}).json()["ok"]
    _hello(room_server, CLONER1, [{"serial": "X"}, {"serial": "Y"}])
    assert room(deploy)["round"] is None

    r = _open(room_server, ["X"])                     # round 2: X picked as a target -- accepted
    assert r.status_code == 200, r.text
    _hello(room_server, CLONER1, [{"serial": "X"}, {"serial": "Y"}])
    x = {d["serial"]: d for d in room(deploy)["machines"][0]["drawer_list"]}["X"]
    assert "last_write_check" not in x                 # #2848 (Nadav 11/11): no mark of the past round at all
    assert x["write_slow"] is False
    assert room(deploy)["round"]["precheck"] == []     # ...and no warning before "התחל" either
    assert (x["rule"]["color"], x["rule"]["target"], x["rule"]["reason"]) == ("green", True, None), x["rule"]
