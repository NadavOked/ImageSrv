"""#3139 -- a drawer that comes back under a new device name (sda -> sdc) is the same disk: the server keys it by serial.

Metal and VM lab, 14/11: a drawer pulled mid-round and pushed back is completed in the background and comes
back under another kernel name. The record the pull opened stayed open under the old name, the round did not
count the drawer, and the screen said "2 of 2" next to "a disk has a problem". The agent half (`_db_rename`,
#3112) keeps the round's end by name; this is the server half -- the identity of a `done` target is the
serial, never the name:

* `clear_returned` closes the pull's record by (round, machine, serial), whatever name the drawer completed under;
* `stamp_done_serials` gives a `done` target that the round names `sdb` -- while the machine's inventory now
  holds that disk as `sdc` -- the serial the round bound to it (the pull's record), because that serial is
  in the inventory under another name;
* `room._tally` counts it: a recorded serial that is still in the machine's inventory is not a swapped drawer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from server import db
from server.disks import disk_failures, disk_sets
from test_disk_failures import HOTPLUG_LINES, PULLED_ERROR, failed_target
from test_server_room import CLONER1, cloner_hello, heartbeat, room_server   # noqa: F401 -- `room_server` is a fixture

MAC = "b4:2e:99:07:1a:c4"
SERIAL = "S3TWNE0JB04745"
SES = "ses_round_1"
PULL = failed_target(ata_log=HOTPLUG_LINES, error=PULLED_ERROR)


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "t.db")


def _open(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, dev, cause FROM disk_failures WHERE cleared_at IS NULL")]


def _journal(conn) -> list[str]:
    return [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'disk_failure_cleared' ORDER BY id")]


# --- the record: closed by serial, under any name --------------------------------------------------------------

def test_a_drawer_that_completed_under_a_new_name_closes_the_record_of_the_old_one(conn):
    row = disk_failures.record(conn, SES, MAC, PULL)                          # the pull, as sda
    assert row["dev"] == "sda" and _open(conn) != []
    assert disk_failures.clear_returned(conn, SES, MAC, "sdc", SERIAL) == [row["id"]]
    assert _open(conn) == []
    assert disk_failures.list_open(conn) == [] and disk_failures.open_for(conn, MAC, [{"serial": SERIAL}]) == []
    assert disk_sets.health(conn, [SERIAL])[SERIAL]["state"] != "orange"
    assert _journal(conn) == [f"#{row['id']} serial={SERIAL} נוקה אוטומטית: המגירה חזרה והושלמה בסבב {SES} (sda -> sdc)"]


def test_the_same_name_keeps_its_journal_line_as_it_was(conn):
    row = disk_failures.record(conn, SES, MAC, PULL)
    assert disk_failures.clear_returned(conn, SES, MAC, "sda", SERIAL) == [row["id"]]
    assert _journal(conn) == [f"#{row['id']} serial={SERIAL} נוקה אוטומטית: המגירה חזרה והושלמה בסבב {SES}"]


def test_another_disk_under_the_new_name_closes_nothing(conn):
    disk_failures.record(conn, SES, MAC, PULL)
    assert disk_failures.clear_returned(conn, SES, MAC, "sdc", "S-ANOTHER-DISK") == []
    assert len(_open(conn)) == 1 and _journal(conn) == []


def test_another_round_or_machine_is_not_touched_by_a_rename(conn):
    disk_failures.record(conn, "ses_earlier", MAC, PULL)
    disk_failures.record(conn, SES, "aa:bb:cc:00:00:99", PULL)
    assert disk_failures.clear_returned(conn, SES, MAC, "sdc", SERIAL) == []
    assert len(_open(conn)) == 2


# --- the round: the drawer is counted, under the name the round chose for it -------------------------------------

def _hello_after_the_return(server) -> None:
    """The machine's hello once the drawer is back: S1 still `sda`, S2 now `sdc` -- there is no `sdb`."""
    body = hello_body(CLONER1)
    body["joining"] = False
    body["disks"] = [{"dev": dev, "size_bytes": 256060514304, "model": "Drawer SSD", "serial": serial,
                      "removable": False, "scheme": "gpt", "has_data": False, "port": port}
                     for dev, serial, port in (("sda", "S1", 1), ("sdc", "S2", 2))]
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def _running(server) -> None:
    """Two drawers cover the target of 2, so the wave starts by itself (a manual start then is a 409 -- #3250);
    start it by hand only if it did not, and say why when the room refuses."""
    heartbeat(server)
    heartbeat(server)
    view = server["deploy"].get("/api/console/room")
    assert view.status_code == 200, view.text
    if view.json()["round"]["wave_state"] != "running":
        started = server["deploy"].post("/api/console/room/start")
        assert started.status_code == 200, started.text
    assert server["deploy"].get("/api/console/room").json()["round"]["wave_state"] == "running"


def _progress(server, wave: str, state: str, targets: list[dict]) -> None:
    assert server["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": state, "targets": targets}).status_code == 200


def test_a_pulled_drawer_back_under_a_new_name_is_counted_and_leaves_no_open_record(room_server):   # noqa: F811
    from server.rounds import room as room_module                                            # noqa: PLC0415

    ctx, deploy = room_server["ctx"], room_server["deploy"]
    from conftest import room_slots
    opened = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": room_slots(ctx.conn, {CLONER1: ["S1", "S2"]})}).json()
    wave = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]["id"]
    _running(room_server)

    done = {"bytes_written": 100, "bytes_total": 100, "state": "done"}
    pulled = failed_target(dev="sdb", serial="S2", port=2, ata_log=HOTPLUG_LINES, error=PULLED_ERROR)
    _progress(room_server, wave, "writing", [{"dev": "sda", **done}, pulled])
    assert [r["dev"] for r in _open(ctx.conn)] == ["sdb"], "the pull opened a record under the old name"

    _hello_after_the_return(room_server)
    # the post-#3112 agent reports the drawer by the name the round chose for it, not by the one it has now
    _progress(room_server, wave, "done", [{"dev": "sda", **done}, {"dev": "sdb", **done}])

    targets = {t["dev"]: t.get("serial") for t in json.loads(ctx.store.members(wave)[0]["targets_json"])}
    assert targets == {"sda": "S1", "sdb": "S2"}, "the round's name for the drawer is bound to its serial"
    assert _open(ctx.conn) == [], "no record is left under the old name: nothing says 'a disk has a problem'"
    assert disk_failures.open_for(ctx.conn, CLONER1, [{"serial": "S2"}]) == []
    round_row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()
    assert room_module._tally(ctx.conn, round_row, ctx.store.members(wave))[0] == 2
    assert ctx.conn.execute("SELECT COUNT(*) AS n FROM journal WHERE event = 'room_drawer_unidentified'"
                            ).fetchone()["n"] == 0


def test_a_disk_that_is_not_in_the_inventory_any_more_is_still_not_the_drawer_that_was_pulled(room_server):   # noqa: F811
    """Guard: the evidence is the serial being in the machine's inventory under another name. S2 gone from the
    machine altogether is not "back" -- the target stays unidentified and is not counted (principle 5)."""
    from server.rounds import room as room_module                                            # noqa: PLC0415

    ctx, deploy = room_server["ctx"], room_server["deploy"]
    from conftest import room_slots
    opened = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": room_slots(ctx.conn, {CLONER1: ["S1", "S2"]})}).json()
    wave = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]["id"]
    _running(room_server)

    done = {"bytes_written": 100, "bytes_total": 100, "state": "done"}
    pulled = failed_target(dev="sdb", serial="S2", port=2, ata_log=HOTPLUG_LINES, error=PULLED_ERROR)
    _progress(room_server, wave, "writing", [{"dev": "sda", **done}, pulled])
    cloner_hello(room_server["anon"], CLONER1, ["S1"], joining=False)                   # S2 is not back
    _progress(room_server, wave, "done", [{"dev": "sda", **done}, {"dev": "sdb", **done}])

    targets = {t["dev"]: t.get("serial") for t in json.loads(ctx.store.members(wave)[0]["targets_json"])}
    assert targets.get("sdb") is None
    round_row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()
    assert room_module._tally(ctx.conn, round_row, ctx.store.members(wave))[0] == 1
