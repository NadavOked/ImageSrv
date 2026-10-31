"""#1857 (mockup 04, Nadav 24/10) -- disk sets: a name that follows the disk by serial.

"xx-12" is the sticker of ONE disk wherever it sits; two disks never get the same name; the
set is a display (nothing written to Windows); a disk that already left the cloner is still
listed (the inventory is filled from hello); and replacing moves the name to the new disk.
"""

from __future__ import annotations

import json

import pytest

from conftest import hello_body
from test_room_precheck_432_65 import disk, drawers_hello
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401 -- fixture

pytest.importorskip("fastapi")

SETS, DISKS = "/api/console/disk-sets", "/api/console/disks"


def _make(admin, name="קוריאים", prefix="xx", students=29, instructor=True):
    r = admin.post(SETS, json={"name": name, "prefix": prefix, "students": students, "instructor": instructor})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _event(ctx, serial, verdict, reason=None, session="ses_a", mac=CLONER1, dev="sda", port=1,
           at="2026-10-23T22:15:00+00:00"):
    ctx.conn.execute(
        "INSERT OR REPLACE INTO disk_events (session_id, mac, disk, port, serial, verdict, reason, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (session, mac, dev, port, serial, verdict, reason, at))
    ctx.conn.commit()


def test_a_set_is_created_with_a_name_and_a_prefix_only_once(room_server):
    admin = room_server["admin"]
    set_id = _make(admin)
    sets = admin.get(SETS).json()
    assert [(s["id"], s["name"], s["prefix"], s["capacity"]) for s in sets] == [(set_id, "קוריאים", "xx", 30)]
    assert sets[0]["plan"][:3] == ["INS", "1", "2"] and sets[0]["plan"][-1] == "29"
    for bad in ({"name": "", "prefix": "yy", "students": 5}, {"name": "ב", "prefix": "x-x", "students": 5},
                {"name": "ב", "prefix": "yy", "students": 201},
                {"name": "ב", "prefix": "yy", "students": 0, "instructor": False},
                {"name": "קוריאים", "prefix": "yy", "students": 5}, {"name": "ב", "prefix": "XX", "students": 5}):
        r = admin.post(SETS, json=bad)
        assert r.status_code in (400, 409), (bad, r.text)
    assert len(admin.get(SETS).json()) == 1


def test_only_an_admin_touches_sets(room_server):
    deploy = room_server["deploy"]
    for call in (lambda: deploy.get(SETS), lambda: deploy.get(DISKS), lambda: deploy.post(SETS, json={}),
                 lambda: deploy.get(SETS + "/rounds")):
        assert call().status_code == 403


def test_two_disks_never_get_the_same_name_and_nothing_is_written_on_a_conflict(room_server):
    admin = room_server["admin"]
    sid = _make(admin)
    ok = admin.post(f"{SETS}/{sid}/assign", json={"items": [{"serial": "S1", "suffix": "12"}]})
    assert ok.status_code == 200
    clash = admin.post(f"{SETS}/{sid}/assign",
                       json={"items": [{"serial": "S2", "suffix": "25"}, {"serial": "S3", "suffix": "12"}]})
    assert clash.status_code == 409 and "xx-12 כבר שייך לדיסק אחר" in clash.text
    twice = admin.post(f"{SETS}/{sid}/assign",
                       json={"items": [{"serial": "S2", "suffix": "7"}, {"serial": "S4", "suffix": "7"}]})
    assert twice.status_code == 409
    members = admin.get(f"{SETS}/{sid}/members").json()["disks"]
    assert [(d["serial"], d["label"]) for d in members] == [("S1", "xx-12")], "S2 must not be half-assigned"
    for bad in ("0", "007", "abc", "", "12345"):
        assert admin.post(f"{SETS}/{sid}/assign", json={"items": [{"serial": "S9", "suffix": bad}]}).status_code == 400


def test_a_disk_is_in_one_set_only(room_server):
    admin = room_server["admin"]
    a, b = _make(admin), _make(admin, "מעבדה 303", "L303", 23)
    assert admin.post(f"{SETS}/{a}/assign", json={"items": [{"serial": "S1", "suffix": "1"}]}).status_code == 200
    r = admin.post(f"{SETS}/{b}/assign", json={"items": [{"serial": "S1", "suffix": "7"}]})
    assert r.status_code == 409 and "קוריאים" in r.text
    assert admin.delete(f"{SETS}/members/S1").status_code == 200       # leaves the first set explicitly...
    assert admin.post(f"{SETS}/{b}/assign", json={"items": [{"serial": "S1", "suffix": "7"}]}).status_code == 200


def test_the_name_follows_the_serial_to_another_drawer_and_reaches_the_cloner(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    sid = _make(admin)
    assert admin.post(f"{SETS}/{sid}/assign", json={"items": [{"serial": "S7", "suffix": "7"}]}).status_code == 200
    first = drawers_hello(anon, CLONER1, [disk("S7", 1), disk("S8", 2)])
    assert first["disk_labels"] == {"S7": {"set": "קוריאים", "label": "xx-7", "text": "קוריאים · xx-7"}}
    moved = drawers_hello(anon, CLONER1, [disk("S8", 1), disk("S7", 4)])       # S7 now in drawer 4
    assert moved["disk_labels"]["S7"]["label"] == "xx-7" and "S8" not in moved["disk_labels"]
    assert drawers_hello(anon, CLONER2, [disk("S5", 1)])["disk_labels"] == {}, "no set: looks as it does today"


def test_health_comes_from_what_was_already_collected_and_unchecked_is_not_ok(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    sid = _make(admin)
    items = [{"serial": s, "suffix": n} for s, n in (("R", "12"), ("O", "5"), ("G", "1"), ("U", "24"))]
    assert admin.post(f"{SETS}/{sid}/assign", json={"items": items}).status_code == 200
    _event(ctx, "R", "fail", "pending", dev="sda", port=1)
    _event(ctx, "O", "warn", "uncorrectable", dev="sdb", port=2)
    _event(ctx, "G", "ok", None, dev="sdc", port=3)
    got = admin.get(f"{SETS}/{sid}/members").json()["disks"]
    assert [(d["label"], d["state"]) for d in got] == [
        ("xx-12", "red"), ("xx-5", "orange"), ("xx-24", "unchecked"), ("xx-1", "ok")]
    assert got[0]["reason"] == "pending" and got[3]["reason"] is None
    assert admin.get(SETS).json()[0]["counts"] == {"red": 1, "orange": 1, "unchecked": 1, "ok": 1}
    # an open disk failure turns an "ok" disk red; clearing it brings it back
    ctx.conn.execute("INSERT INTO disk_failures (session_id, mac, dev, serial, port, at, cause)"
                     " VALUES ('s', ?, 'sdc', 'G', 3, '2026-10-23T23:00:00+00:00', 'disk')", (CLONER1,))
    ctx.conn.commit()
    assert {d["serial"]: d["state"] for d in admin.get(f"{SETS}/{sid}/members").json()["disks"]}["G"] == "red"
    ctx.conn.execute("UPDATE disk_failures SET cleared_at = '2026-10-24T00:00:00+00:00'")
    ctx.conn.commit()
    assert {d["serial"]: d["state"] for d in admin.get(f"{SETS}/{sid}/members").json()["disks"]}["G"] == "ok"


def test_replace_moves_the_name_to_the_new_disk_and_keeps_the_old_one_in_history(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    sid = _make(admin)
    admin.post(f"{SETS}/{sid}/assign",
               json={"items": [{"serial": "OLD", "suffix": "12"}, {"serial": "OTHER", "suffix": "3"}]})
    r = admin.post(f"{SETS}/{sid}/replace", json={"suffix": "12", "new_serial": "NEW"})
    assert r.status_code == 200, r.text
    assert {d["serial"]: d["label"] for d in admin.get(f"{SETS}/{sid}/members").json()["disks"]} == {
        "NEW": "xx-12", "OTHER": "xx-3"}
    hist = ctx.conn.execute("SELECT serial, suffix, reason, replaced_by FROM disk_set_history").fetchall()
    assert [tuple(h) for h in hist] == [("OLD", "12", "replaced", "NEW")]
    assert admin.post(f"{SETS}/{sid}/replace", json={"suffix": "12", "new_serial": "OTHER"}).status_code == 409
    assert admin.post(f"{SETS}/{sid}/replace", json={"suffix": "20", "new_serial": "Z"}).status_code == 404
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert {"disk_set_created", "disk_set_assigned", "disk_set_replaced"} <= set(events)


def test_a_disk_that_left_the_cloner_is_still_listed_with_its_model(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    body = hello_body(CLONER1)
    body["disks"] = [{"dev": "sda", "size_bytes": 500107862016, "model": "WDC WD5000AAKX", "serial": "GONE",
                      "removable": False, "scheme": "gpt", "has_data": False, "port": 3}]
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    got = {d["serial"]: d for d in admin.get(DISKS).json()["disks"]}
    assert got["GONE"]["model"] == "WDC WD5000AAKX" and got["GONE"]["in_machine"] is True and got["GONE"]["port"] == 3
    body["disks"] = []                                                                  # pulled out
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    gone = {d["serial"]: d for d in admin.get(DISKS).json()["disks"]}["GONE"]
    assert gone["in_machine"] is False and gone["model"] == "WDC WD5000AAKX", "from the inventory, not the hello"


def test_a_round_lists_its_disks_by_drawer_with_the_write_result(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    sid = _make(admin)
    admin.post(f"{SETS}/{sid}/assign", json={"items": [{"serial": "S3", "suffix": "9"}]})
    ctx.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
        " created_at, last_join_at) VALUES ('ses_a', 'grp_CLONERS', 'img_x', 'p', 1, 1, 'closed', 'u',"
        " '2026-10-23T22:00:00+00:00', 0)")
    ctx.conn.execute(
        "INSERT INTO session_members (session_id, mac, state, done, updated_at, targets_json)"
        " VALUES ('ses_a', ?, 'done', 1, '2026-10-23T22:15:00+00:00', ?)",
        (CLONER1, json.dumps([{"dev": "sda", "state": "done"}, {"dev": "sdb", "state": "failed"},
                              {"dev": "sdc", "state": "done"}])))
    for dev, port, serial in (("sda", 1, "S1"), ("sdb", 2, "S2"), ("sdc", 3, "S3")):
        _event(ctx, serial, "ok", None, dev=dev, port=port)
    got = admin.get(SETS + "/rounds").json()
    assert len(got) == 1 and got[0]["image_id"] == "img_x"
    assert [(d["port"], d["serial"], d["written"], d["in_set"]) for d in got[0]["drawers"]] == [
        (1, "S1", True, None), (2, "S2", False, None), (3, "S3", True, "xx-9")]
