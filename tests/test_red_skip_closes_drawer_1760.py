"""#1760 (metal D6b, rc6 c86f389f, cloner 2, 18/10) -- a drawer skipped as red.

`blockdev --setro` on one of two drawers before the round. Wave 1 was right:
the agent skipped it as red ("readonly -- never written") and wrote the other.
Then three things went wrong, one test group each:

1. **The room** counted the red drawer as one still to write: it opened wave 2
   for it and woke the cloner (`room_rewake n=1/6`). Red is never a target
   (#1732) -- it closes its place in the round, skipped, not written: no wave,
   no wake, and the round ends 1/2 when nothing else is left.
2. **The memory**: the skip was not remembered with its reason. After the
   reboot (`setro` gone) the disk came back orange (`failed_last|unclassified`,
   an EPERM record from before #1742 classified `readonly`) and
   send_held_smart held the whole stream on a question for 30 minutes. Now a
   red read-only skip is an open `disk_failures` row `cause=readonly` by
   serial, and an `unclassified` row whose own evidence is EPERM/EROFS is read
   as `readonly`.
3. **The end screen** said "השיכפול הושלם — הדיסקים נכתבו" with one of two
   drawers skipped: the count now counts the red skip -- `partial 1/2`.

Negative control (each group fails on the code before the fix): `git stash`
of server/rounds/room.py, server/disks/disk_failures.py, server/disks/disk_events.py, server/db.py
and agent/imagesrv-agent together -- all of them (#439).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from server.disks import ata_cause, disk_failures
from server.db import now_iso
from test_final_report import (  # noqa: F401 -- `reports` is a fixture
    IMAGE, SESSION, native_tools, reports, run_path, url_of,
)
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1, heartbeat, report, room, room_body, room_server,
)


def _hello(client, serials: list[str], red: str | None = None) -> dict:
    """A cloner hello with a drawer per serial; `red` = that serial says smart fail/readonly."""
    body = hello_body(CLONER1)
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "port": i + 1, "size_bytes": 256060514304,
         "model": "Drawer SSD", "serial": s, "removable": False, "scheme": "gpt",
         "has_data": False,
         **({"smart": "fail", "smart_reason": "readonly"} if s == red else {"smart": "ok"})}
        for i, s in enumerate(serials)
    ]
    response = client.post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _disk_event(client, session: str, disk: str, serial: str, verdict: str,
                reason: str, decision: str) -> None:
    response = client.post("/api/v1/agent/disk-event", json={
        "session_id": session, "mac": CLONER1, "disk": disk, "port": 2, "serial": serial,
        "smart": {"verdict": verdict, "reason": reason, "realloc": 0, "pending": 0,
                  "uncorrectable": 0, "crc": 0},
        "write_state": "skipped", "decision": decision})
    assert response.status_code == 200 and response.json().get("ok"), response.text


def _events(ctx, name: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (name,))]


def _wave1(room_server, red_hello: bool) -> tuple[str, dict]:
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = _hello(anon, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"
    if red_hello:                         # setro after the probe: the next hello says red
        _hello(anon, ["S1", "S2"], red="S2")
    return wave, opened.json()


# --- 1. the room: a red skip closes its drawer ---------------------------------------


@pytest.mark.parametrize("evidence", ["disk_event", "hello"])
def test_a_drawer_skipped_as_red_closes_the_round_without_a_second_wave(room_server, evidence):
    """D6b: 1 written + 1 red = the round's 2. Not `room_wave wave=2`, not `room_rewake`.
    The evidence that it was red: the wave's disk_event (fail/skip), or -- that event
    lost (best effort) -- the hello that says the disk is red."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave, opened = _wave1(room_server, red_hello=evidence == "hello")
    if evidence == "disk_event":
        _disk_event(anon, wave, "sdb", "S2", "fail", "readonly", "skip")
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "skipped"}, top="partial")
    heartbeat(room_server)

    assert room(deploy)["round"] is None, room(deploy)["round"]       # the round ended
    row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()
    assert row["state"] == "closed" and row["written_drives"] == 1
    assert json.loads(row["skipped_serials"]) == ["S2"]
    assert _events(ctx, "room_done") == [f'{opened["id"]} written=1/2 skipped_red=1']
    assert _events(ctx, "room_wave") == [] and _events(ctx, "room_rewake") == []


def test_a_red_skip_with_more_to_write_does_not_wait_for_it(room_server):
    """Target 3 over two drawers: the round goes on for a swapped drawer, and the red
    one is neither fresh nor a reason to wake the cloner -- remaining is 1, not 2."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 3, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = _hello(anon, ["S1", "S2"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    _disk_event(anon, wave, "sdb", "S2", "fail", "readonly", "skip")
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "skipped"}, top="partial")
    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["written_drives"] == 1 and view["skipped_drives"] == 1
    assert view["remaining_drives"] == 1 and view["wave_number"] == 2
    # the same drawers, setro gone after the reboot: S2 is closed -- the cloner does not join
    assert _hello(anon, ["S1", "S2"])["session"] is None


def test_the_console_drawer_list_shows_the_red_skip_closed_not_fresh(room_server):
    """The console reads the same truth as the room: skipped_serials. A red-skipped
    drawer is `fresh: false` + `skipped_red: true` ("skipped, not written") -- not
    `fresh: true` ("still to be written"), and not plain `false` ("written")."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 3, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = _hello(anon, ["S1", "S2"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    _disk_event(anon, wave, "sdb", "S2", "fail", "readonly", "skip")
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "skipped"}, top="partial")
    heartbeat(room_server)
    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    drawers = {d["serial"]: d for d in machine["drawer_list"]}
    assert (drawers["S2"]["fresh"], drawers["S2"]["skipped_red"]) == (False, True), drawers["S2"]
    assert (drawers["S1"]["fresh"], drawers["S1"]["skipped_red"]) == (False, False), drawers["S1"]
    assert machine["fresh_drawers"] == 0


def test_an_orange_skip_by_the_operator_still_waits_for_the_next_wave(room_server):
    """Guard against over-fixing: "דלג" on an orange drawer is the person's choice for
    this wave, not "never a target" -- the drawer stays fresh, as before #1760."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave, opened = _wave1(room_server, red_hello=False)
    _disk_event(anon, wave, "sdb", "S2", "failed_last", "cable", "skip")
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "skipped"}, top="partial")
    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view is not None and view["written_drives"] == 1 and view["skipped_drives"] == 0
    assert view["remaining_drives"] == 1 and view["wave_number"] == 2


# --- 1b. metal D6c: a wave with a red drawer starts with the healthy one ---------------


def _open_two(room_server) -> dict:
    opened = room_server["deploy"].post(
        "/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    return opened.json()


def test_a_wave_with_one_red_and_one_healthy_drawer_starts_at_once(room_server):
    """D6c (d6afd4ac, room_3afb859d, 44+ min): `setro` on drawer 3, round on 2+3. The
    cloner joined with `smart: fail/readonly` on one of them; the wave waited for two
    fresh drawers forever -- a red one is not fresh (#1760) and the start condition
    still counted it as awaited. Now the wave goes with the healthy one at once; the
    agent skips the red one, and the round closes 1/2."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = _open_two(room_server)
    answer = _hello(anon, ["S1", "S2"], red="S2")
    assert answer["session"] is not None                  # joins for its healthy drawer
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running", room(deploy)["round"]
    wave = answer["session"]["id"]
    _disk_event(anon, wave, "sdb", "S2", "fail", "readonly", "skip")
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "skipped"}, top="partial")
    heartbeat(room_server)
    assert room(deploy)["round"] is None
    assert _events(ctx, "room_done") == [f'{opened["id"]} written=1/2 skipped_red=1']


def test_a_wave_where_every_drawer_is_red_fails_by_name_not_open_forever(room_server):
    """Both selected drawers red: nothing to write. The round fails visibly with the
    reason -- not a wave left `open` with the cloner waiting on it."""
    from server.rounds import room as room_module               # noqa: PLC0415
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = _open_two(room_server)
    body = hello_body(CLONER1)
    body["disks"] = [
        {"dev": dev, "port": port, "size_bytes": 256060514304, "model": "Drawer SSD",
         "serial": serial, "removable": False, "scheme": "gpt", "has_data": False,
         "smart": "fail", "smart_reason": "readonly"}
        for dev, port, serial in (("sda", 1, "S1"), ("sdb", 2, "S2"))]
    # nothing fresh: the cloner does not join -- the round must not wait for it forever
    assert anon.post("/api/v1/agent/hello", json=body).json()["session"] is None
    heartbeat(room_server)
    row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()
    assert row["state"] == "failed", dict(row)
    assert row["failed_reason"] == room_module.NOTHING_TO_WRITE_REASON
    assert any("אין מגירות לכתוב" in d for d in _events(ctx, "room_failed"))


# --- 2. the memory: a read-only skip is remembered with its reason -------------------


def test_a_read_only_skip_is_remembered_as_readonly_by_serial(room_server):
    anon, ctx = room_server["anon"], room_server["ctx"]
    wave, _opened = _wave1(room_server, red_hello=False)
    _disk_event(anon, wave, "sdb", "S2", "fail", "readonly", "skip")
    _disk_event(anon, wave, "sdb", "S2", "fail", "readonly", "skip")     # once, not twice
    rows = ctx.conn.execute("SELECT * FROM disk_failures").fetchall()
    assert [(r["serial"], r["cause"], r["port"]) for r in rows] == [("S2", "readonly", None)]
    # the next hello -- the reboot, setro gone -- hands it back as readonly: the agent
    # (smart.sh disk_failure_readonly) makes the disk red, not orange
    marks = _hello(anon, ["S1", "S2"])["disk_failures"]
    assert [(m["serial"], m["cause"]) for m in marks] == [("S2", "readonly")]
    # by serial only: another disk in the same slot is not it
    assert _hello(anon, ["S1", "S9"])["disk_failures"] == []


def test_a_smart_failed_skip_is_not_recorded_as_readonly(room_server):
    """SMART fail is read again from the disk itself on every probe; only read-only,
    which a reboot hides, needs the server's memory."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    wave, _opened = _wave1(room_server, red_hello=False)
    _disk_event(anon, wave, "sdb", "S2", "fail", "health_failed", "skip")
    assert ctx.conn.execute("SELECT COUNT(*) AS n FROM disk_failures").fetchone()["n"] == 0


def test_an_old_unclassified_eperm_record_reads_as_readonly(room_server):
    """D6 left a row from before #1742 classified readonly: `unclassified`, with
    `dd: … Operation not permitted` as its evidence. Read again by that evidence."""
    anon, ctx = room_server["anon"], room_server["ctx"]
    error = "dd: error writing '/dev/sdb': Operation not permitted"
    assert ata_cause.classify([], error) == "readonly"
    ctx.conn.execute(
        "INSERT INTO disk_failures (session_id, mac, dev, serial, port, ata_port, at,"
        " cause, error, ata_log) VALUES ('ses_old', ?, 'sdb', 'S2', 2, NULL, ?,"
        " 'unclassified', ?, '[]')", (CLONER1, now_iso(), error))
    ctx.conn.commit()
    marks = _hello(anon, ["S1", "S2"])["disk_failures"]
    assert [(m["serial"], m["cause"]) for m in marks] == [("S2", "readonly")]
    assert [r["cause"] for r in disk_failures.list_open(ctx.conn)] == ["readonly"]


# --- 3. the end screen: a skipped drawer is not "the drives were written" -------------

RED_SKIP = ('smart_gate() { target_set sdc skipped "הדיסק במצב קריאה בלבד"; echo sdb; return 0; }; '
            'run_restore_drawers() { target_set sdb done; echo done > "$RUN_DIR/state"; return 0; }; '
            'end_countdown() { echo "COUNTDOWN $1 $3/$4" >> "$RUN_DIR/trace"; exit 0; }; '
            'finish_and_stop() { echo "POWEROFF-AT-ONCE" >> "$RUN_DIR/trace"; exit 0; }; ')


@native_tools
def test_a_round_with_a_red_skip_ends_as_partial_one_of_two(tmp_path, reports):
    """The count is over the round's drawers, the red skip included: `partial 1/2`
    ("השיכפול הסתיים: 1 מתוך 2 דיסקים נכתבו"), not `drawers 1/1` ("הדיסקים נכתבו")."""
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                      stubs=RED_SKIP, env="IMAGESRV_TEST=0 D_ROLE=cloner")
    assert "COUNTDOWN partial 1/2" in result["trace"], result["trace"]
