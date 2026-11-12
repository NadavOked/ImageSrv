"""#1366 (Nadav 13/10) -- the absolute CRC threshold, and its end (#2846, Nadav 11/11).

#1366 made a drawer orange over ``crc_warn_threshold``: "כבל או מגירה חשודים — N
שגיאות". On 11/11 at work three disks said it with 42,355 / 2,375 / 59,165 -- and
the counters did not move in 3h10m of streaming, nor when the disks moved to another
machine. Nadav: an absolute count is the disk's history, not a state. Only a RISE is
said (``crc_rise`` since the disk's previous round, ``crc_delta`` during the write),
as the cable or the drawer. The setting itself still exists (hello, settings, the
learning summary) and its removal is its own Issue; these tests keep it honest:
it is stored and validated, and it no longer marks anything.
"""

from __future__ import annotations

import pytest
from conftest import room_slots
from test_room_precheck_432_65 import _open, disk, drawers_hello, journal, machine
from test_server_room import CLONER1, CLONER2, room, room_server  # noqa: F401 — fixture

TEXT = "כבל או מגירה חשודים — {} שגיאות"   # #1366's words -- must appear nowhere now


@pytest.mark.parametrize("raw, want", [
    ("500", 500), (0, 0), ("0", 0), (1_000_000, 1_000_000),
    ("0500", None), ("-1", None), (-1, None), ("abc", None), ("", None), (True, None),
    (1_000_001, None), (5.5, None), (None, None),
])
def test_parse_takes_a_whole_number_in_range_only(raw, want):
    from server.disks import crcwarn   # in the test: the rest of the module runs without it (negative control)
    assert crcwarn.parse(raw) == want


def test_the_absolute_check_is_gone():
    """#2846: nothing left to call -- the flag cannot come back by one forgotten caller."""
    from server.disks import crcwarn
    assert not hasattr(crcwarn, "suspect")


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


def test_no_drawer_carries_an_absolute_crc_flag(room_server):
    """#2846: the work disks' counts, over any threshold -- no ``crc_suspect`` in the room
    or on the machines page; the count itself stays, as information."""
    admin, anon = room_server["admin"], room_server["anon"]
    _turn_on(admin)
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=42355), disk("S2", 2, crc=2375),
                                  disk("S3", 3, crc=None), disk("S4", 4, crc=59165)])
    drawers = machine(room(admin), CLONER1)["drawer_list"]
    assert [d for d in drawers if "crc_suspect" in d] == [], drawers
    assert {d["serial"]: d["crc"] for d in drawers} == {"S1": 42355, "S2": 2375, "S3": None, "S4": 59165}
    m = next(x for x in admin.get("/api/console/machines").json() if x["mac"] == CLONER1)
    assert [d for d in m["disks"] if "crc_suspect" in d] == [], m["disks"]


def test_a_first_wave_does_not_warn_on_an_absolute_count(room_server):
    """The 11/11 case: a first wave, no previous round -- a high count alone says nothing;
    a slow link on the same drawer still does."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _turn_on(room_server["admin"])
    _open(deploy, target=4, slots=room_slots(ctx.conn, {CLONER1: ["S1", "S2"], CLONER2: ["S3"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=42355), disk("S2", 2, crc=2375)])
    drawers_hello(anon, CLONER2, [disk("S3", 1, crc=59165, link=1.5)])
    warn = {(w["mac"], w["port"]): w for w in room(deploy)["round"]["precheck"]}
    assert set(warn) == {(CLONER2, 1)}, warn
    assert "1.5 מתוך 6.0" in warn[(CLONER2, 1)]["text_he"]
    assert TEXT.format(59165) not in warn[(CLONER2, 1)]["text_he"] and "crc_suspect" not in warn[(CLONER2, 1)]
    r = deploy.post("/api/console/room/start")
    assert r.status_code == 200 and r.json()["warned"] == 1, r.text
    (detail,) = journal(ctx, "room_precheck_warned")
    assert "crc=" not in detail, detail


def test_the_default_is_500_since_21_10():
    """The stored setting is unchanged by #2846 (its removal is its own Issue)."""
    from server.disks import crcwarn
    assert crcwarn.DEFAULT == 500
