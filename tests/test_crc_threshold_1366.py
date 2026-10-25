"""#1366 (Nadav 13/10) -- the absolute CRC threshold.

#432 warns on a CRC *rise* since a disk's previous round. On 13/10, on metal,
disks with 667 and 1,752 CRC errors entered a first wave and nothing was
marked; the 667 one failed with fsync errno 5 (cause=cable). Over
``crc_warn_threshold`` (a console setting, admin only, default 500 since 21/10; 0 = off:
measured on metal the counters did not rise during that wave, they are history)
a drawer is orange: "כבל או מגירה חשודים — N שגיאות" -- in the room, on the
machines page, in the "start anyway" question, and to the agent in hello.
"""

from __future__ import annotations

import pytest
from conftest import room_slots
from test_room_precheck_432_65 import _open, disk, drawers_hello, journal, machine
from test_server_room import CLONER1, CLONER2, room, room_server  # noqa: F401 — fixture

TEXT = "כבל או מגירה חשודים — {} שגיאות"


@pytest.mark.parametrize("raw, want", [
    ("500", 500), (0, 0), ("0", 0), (1_000_000, 1_000_000),
    ("0500", None), ("-1", None), (-1, None), ("abc", None), ("", None), (True, None),
    (1_000_001, None), (5.5, None), (None, None),
])
def test_parse_takes_a_whole_number_in_range_only(raw, want):
    from server import crcwarn   # in the test: the rest of the module runs without it (negative control)
    assert crcwarn.parse(raw) == want


def test_suspect_has_three_states():
    from server import crcwarn
    assert crcwarn.suspect(667, 500) is True
    assert crcwarn.suspect(500, 500) is False            # "מעל" -- over, not at
    assert crcwarn.suspect(55242, 0) is False            # 0 = the warning is off
    assert crcwarn.suspect(None, 500) is None            # not read: unknown, not fine


def test_hello_carries_the_threshold_and_the_setting_changes_it(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert drawers_hello(anon, CLONER1, [disk("S1", 1)])["crc_warn_threshold"] == 500     # Nadav 21/10 (#1921)
    assert admin.get("/api/console/settings").json()["crc_warn_threshold"] == "500"  # in force, not "unset"
    assert admin.post("/api/console/settings", json={"crc_warn_threshold": 900}).status_code == 200
    assert drawers_hello(anon, CLONER1, [disk("S1", 1)])["crc_warn_threshold"] == 900
    assert admin.get("/api/console/settings").json()["crc_warn_threshold"] == "900"


@pytest.mark.parametrize("bad", ["-5", "abc", "0500", 1_000_001, True, ""])
def test_a_bad_value_is_refused_before_anything_is_written(room_server, bad):
    admin, ctx = room_server["admin"], room_server["ctx"]
    r = admin.post("/api/console/settings",
                   json={"session_wait_seconds": "77", "crc_warn_threshold": bad})
    assert r.status_code == 400 and "CRC" in r.text, r.text
    stored = {k: v for k, v in ctx.conn.execute("SELECT key, value FROM settings")}
    assert "crc_warn_threshold" not in stored and stored.get("session_wait_seconds") != "77"


def test_only_an_admin_sets_it(room_server):
    deploy = room_server["deploy"]
    assert deploy.post("/api/console/settings", json={"crc_warn_threshold": 9}).status_code == 403


def _turn_on(admin, limit=500):
    assert admin.post("/api/console/settings", json={"crc_warn_threshold": limit}).status_code == 200


def test_each_drawer_says_whether_its_crc_is_over_the_threshold(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    _turn_on(admin)
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=667), disk("S2", 2, crc=137),
                                  disk("S3", 3, crc=None), disk("S4", 4, crc=1752)])
    got = {d["serial"]: d["crc_suspect"] for d in machine(room(admin), CLONER1)["drawer_list"]}
    assert got == {"S1": True, "S2": False, "S3": None, "S4": True}
    # the machines page carries the same flag, from the same hello
    m = next(x for x in admin.get("/api/console/machines").json() if x["mac"] == CLONER1)
    assert {d["serial"]: d["crc_suspect"] for d in m["disks"]} == got


def test_a_first_wave_warns_before_start_on_an_absolute_count(room_server):
    """The 13/10 case, with the threshold turned on: no previous round, so no rise -- and still orange."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _turn_on(room_server["admin"])
    _open(deploy, target=4, slots=room_slots(ctx.conn, {CLONER1: ["S1", "S2"], CLONER2: ["S3"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=667), disk("S2", 2, crc=137)])
    drawers_hello(anon, CLONER2, [disk("S3", 1, crc=1752, link=1.5)])
    warn = {(w["mac"], w["port"]): w["text_he"] for w in room(deploy)["round"]["precheck"]}
    assert set(warn) == {(CLONER1, 1), (CLONER2, 1)}, warn
    assert warn[(CLONER1, 1)].endswith(TEXT.format(667)), warn
    assert TEXT.format(1752) in warn[(CLONER2, 1)] and "1.5 מתוך 6.0" in warn[(CLONER2, 1)]
    r = deploy.post("/api/console/room/start")          # a warning, not a block
    assert r.status_code == 200 and r.json()["warned"] == 2, r.text
    (detail,) = journal(ctx, "room_precheck_warned")
    assert f"{CLONER1}:1 crc=667" in detail, detail


def test_the_default_is_500_since_21_10():
    """Nadav 21/10 (#1921) replaced the 0 of 13/10: 500, as on USB -- a drawer/cable note."""
    from server import crcwarn
    assert crcwarn.DEFAULT == 500


def test_a_new_server_notes_a_drawer_over_500_with_no_setting(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=667), disk("S2", 2, crc=137)])
    got = {d["serial"]: d["crc_suspect"] for d in machine(room(admin), CLONER1)["drawer_list"]}
    assert got == {"S1": True, "S2": False}


def test_zero_is_off(room_server):
    """Nadav 13/10, measured on metal: the counters did not rise during the wave
    (667 / 1,752 / 137 before and after) -- history, not the cause. 0 still turns the note off."""
    admin, deploy, anon = room_server["admin"], room_server["deploy"], room_server["anon"]
    _turn_on(admin, "0")
    _open(deploy, target=4, slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=55242)])
    assert room(deploy)["round"]["precheck"] == []
    assert machine(room(admin), CLONER1)["drawer_list"][0]["crc_suspect"] is False
