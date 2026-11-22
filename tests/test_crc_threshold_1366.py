"""#2861 הסרת ההגדרה."""

from __future__ import annotations

from conftest import room_slots
from test_room_precheck_432_65 import _open, disk, drawers_hello, journal, machine
from test_server_room import CLONER1, CLONER2, room, room_server  # noqa: F401 — fixture


def test_hello_does_not_carry_the_threshold(room_server):
    anon = room_server["anon"]
    assert "crc_warn_threshold" not in drawers_hello(anon, CLONER1, [disk("S1", 1)])


def test_settings_do_not_list_or_accept_it(room_server):
    admin = room_server["admin"]
    assert "crc_warn_threshold" not in admin.get("/api/console/settings").json()
    r = admin.post("/api/console/settings", json={"crc_warn_threshold": 900})
    assert r.status_code == 400 and "הגדרה לא מוכרת" in r.text


def test_a_stale_row_in_the_db_is_ignored(room_server):
    from server import db
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    db.set_setting(ctx.conn, "crc_warn_threshold", "900")
    assert "crc_warn_threshold" not in drawers_hello(anon, CLONER1, [disk("S1", 1)])
    got = admin.get("/api/console/settings")
    assert got.status_code == 200 and "crc_warn_threshold" not in got.json()


def test_the_module_keeps_only_the_old_journal_text():
    from server.disks import crcwarn
    assert not hasattr(crcwarn, "threshold")
    assert not hasattr(crcwarn, "parse")
    assert not hasattr(crcwarn, "SETTING")
    assert hasattr(crcwarn, "text_he")
    assert crcwarn.text_he(812) == "כבל או מגירה חשודים — 812 שגיאות"


def test_the_absolute_check_is_gone():
    """#2846: nothing left to call -- the flag cannot come back by one forgotten caller."""
    from server.disks import crcwarn
    assert not hasattr(crcwarn, "suspect")


def test_no_drawer_carries_an_absolute_crc_flag(room_server):
    """#2846: the work disks' counts, over any threshold -- no ``crc_suspect`` in the room
    or on the machines page; the count itself stays, as information."""
    admin, anon = room_server["admin"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=42355), disk("S2", 2, crc=2375),
                                  disk("S3", 3, crc=None), disk("S4", 4, crc=59165)])
    drawers = machine(room(admin), CLONER1)["drawer_list"]
    assert [d for d in drawers if "crc_suspect" in d] == [], drawers
    assert {d["serial"]: d["crc"] for d in drawers} == {"S1": 42355, "S2": 2375, "S3": None, "S4": 59165}
    m = next(x for x in admin.get("/api/console/machines").json() if x["mac"] == CLONER1)
    assert [d for d in m["disks"] if "crc_suspect" in d] == [], m["disks"]


def test_a_first_wave_does_not_warn_on_an_absolute_count(room_server):
    """The 11/11 case: a first wave, no previous round -- a high count alone says nothing; and since #3591 (Nadav 17/11)
    neither does a link speed (1.5 of 6.0) on the same drawer."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _open(deploy, target=4, slots=room_slots(ctx.conn, {CLONER1: ["S1", "S2"], CLONER2: ["S3"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=42355), disk("S2", 2, crc=2375)])
    drawers_hello(anon, CLONER2, [disk("S3", 1, crc=59165, link=1.5)])
    warn = {(w["mac"], w["port"]): w for w in room(deploy)["round"]["precheck"]}
    assert warn == {}, warn
    r = deploy.post("/api/console/room/start")
    assert r.status_code == 200 and r.json()["warned"] == 0, r.text
    assert journal(ctx, "room_precheck_warned") == []
