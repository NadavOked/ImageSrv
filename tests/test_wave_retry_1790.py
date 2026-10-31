"""#1790 (metal D2, rc7nl, cloner 2, 19/10) -- a retry wave writes only what is still open.

D2, room_41d7feed: wave 1 failed drawer 2 (dm injection, "לא הצלחנו לכתוב את טבלת
המחיצות") and wrote drawer 3. Wave 2 (automatic rewake) rewrote **both** --
`drawer=3 done 19528278983` a second time: ~6 minutes and a full write on a good disk.

`session.target_ports` leaves out a slot only on positive evidence: the inventory of
this very hello puts a disk there whose serial this round already closed (written, or
skipped red #1760). A swapped disk (another serial), a disk without a serial, or a
hello without an inventory -- the slot stays and the drawer is written (principle 5).
The console card shows the drawer written in an earlier wave as "נכתב", not "—".

Negative control: server/rounds/room.py, server/agent/hello.py and server/static/console.js back
to the base, all of them (#439) -- see the PR.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.rounds import room
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1, cloner_hello, heartbeat, report, room as room_view, room_body, room_server,
)

REPO = Path(__file__).resolve().parent.parent


def _wave1_fails_drawer_2(room_server, *, ata_log: bool = False) -> tuple[str, str]:
    """Round of 2 over CLONER1 (S1 on port 1, S2 on port 2); wave 1 writes S1 and fails S2
    (`ata_log` -- with the kernel evidence, so #874 records it). Returns (round id, wave 2)."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    first = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert first["target_ports"] == [1, 2], "wave 1 writes every selected drawer"
    heartbeat(room_server)
    assert room_view(deploy)["round"]["wave_state"] == "running"
    targets = [
        {"dev": "sda", "bytes_written": 100, "bytes_total": 100, "state": "done"},
        {"dev": "sdb", "bytes_written": 4, "bytes_total": 100, "state": "failed",
         "error": "לא הצלחנו לכתוב את טבלת המחיצות",
         **({"ata_log": [], "serial": "S2", "port": 2} if ata_log else {})},
    ]
    response = anon.post("/api/v1/agent/progress", json={
        "session_id": first["id"], "mac": CLONER1, "state": "partial", "targets": targets})
    assert response.status_code == 200, response.text
    heartbeat(room_server)
    view = room_view(deploy)["round"]
    assert view["written_drives"] == 1 and view["wave_state"] == "open" and view["wave_number"] == 2
    return opened.json()["id"], view["wave_session_id"]


def _journal(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


# --- #1790: the retry wave ------------------------------------------------------------


def test_the_retry_wave_hands_the_cloner_only_the_drawer_that_failed(room_server):
    """D2: wave 2 gets port 2 only; port 1 (S1, written in wave 1) is not written again.
    The round still ends 2/2 on the one drawer the wave wrote."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    round_id, _ = _wave1_fails_drawer_2(room_server)

    second = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert second["target_ports"] == [2], second
    machine = next(m for m in room_view(deploy)["machines"] if m["mac"] == CLONER1)
    fresh = {d["port"]: d["fresh"] for d in machine["drawer_list"]}
    assert fresh == {1: False, 2: True}, "port 1 is shown written, port 2 still to write"

    heartbeat(room_server)
    report(anon, second["id"], CLONER1, {"sdb": "done"})
    heartbeat(room_server)
    assert room_view(deploy)["round"] is None
    assert _journal(ctx, "room_done") == [f"{round_id} written=2/2"]


def test_a_drawer_swapped_between_waves_is_a_new_disk_and_is_written(room_server):
    """The cloner rebooted with another disk in port 1 (S3): it is not S1 -- written."""
    anon = room_server["anon"]
    _wave1_fails_drawer_2(room_server)
    assert cloner_hello(anon, CLONER1, ["S3", "S2"])["session"]["target_ports"] == [1, 2]


def test_a_drawer_without_a_serial_is_written_in_every_wave(room_server):
    """Principle 5: no serial = no evidence it was written -- the slot stays."""
    anon = room_server["anon"]
    _wave1_fails_drawer_2(room_server)
    assert cloner_hello(anon, CLONER1, [None, "S2"])["session"]["target_ports"] == [1, 2]


def _round(written: list[str], skipped: list[str] | None = None) -> dict:
    return {"target_slots_json": json.dumps([{"mac": CLONER1, "ports": [1, 2, 3]}]),
            "written_serials": json.dumps(written),
            "skipped_serials": json.dumps(skipped or []),
            "cancelled_serials": "[]"}                     # #1936: the row has the column


def _disk(port: int, serial: str | None) -> dict:
    return {"dev": f"sd{'abc'[port - 1]}", "port": port, "serial": serial}


@pytest.mark.parametrize("disks,expected", [
    ([_disk(1, "S1"), _disk(2, "S2"), _disk(3, "S3")], [2, 3]),     # S1 written: out
    ([_disk(1, "S9"), _disk(2, "S2"), _disk(3, "S3")], [1, 2, 3]),  # swapped: in
    ([_disk(1, None), _disk(2, "S2"), _disk(3, "S3")], [1, 2, 3]),  # no serial: in
    ([_disk(1, "S1"), _disk(2, "S2"), _disk(3, "R1")], [2]),        # red skip closed (#1760)
    ([_disk(2, "S2"), _disk(3, "S3")], [1, 2, 3]),                  # empty slot: as before
    (None, [1, 2, 3]),                                               # no inventory: all
    ([_disk(1, "S1"), _disk(1, "S9"), _disk(2, "S2")], [1, 2, 3]),  # two disks claim port 1
])
def test_target_ports_leaves_out_a_slot_only_on_positive_evidence(disks, expected):
    assert room.target_ports(_round(["S1"], ["R1"]), CLONER1, disks) == expected


def test_an_old_round_without_a_selection_stays_null():
    assert room.target_ports({"target_slots_json": None}, CLONER1, [_disk(1, "S1")]) is None
    assert room.target_ports(None, CLONER1, [_disk(1, "S1")]) is None


NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_the_console_card_shows_a_drawer_written_in_an_earlier_wave_as_written(tmp_path):
    """Wave 2 has no target on port 1: the card said "—" (as for "no report"). It is
    written -- "נכתב", green, 100% -- while a red skip stays "דולג" and a fresh one "—"."""
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    pick = [re.search(r"^function linkSlow\(d\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^function slotClass\(s\) \{.*?^\}", js, re.M | re.S),
            re.search(r"^function bayEmphasis\(s\) \{.*?^\}", js, re.M | re.S),   # #2050: the row's emphasis class
            re.search(r"^function roundDskHtml\(m, s\) \{.*?^\}", js, re.M | re.S)]
    assert all(pick), "a function this test reads is gone from console.js"
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x), encodeId = (x) => x, roomOperator = () => false;
const slotLabel = (m, n) => `דיסק ${n}`, rowLabel = () => "?", diskTitle = () => "";
""" + "\n".join(m.group(0) for m in pick) + """
const cell = (disk) => roundDskHtml({ mac: "m" }, { n: 1, disk, live: disk, fail: null });
const text = (html) => html.match(/<b>(.*?)<\\/b>/)[1];
console.log(JSON.stringify({
  written: cell({ fresh: false, skipped_red: false, state: null, smart: "ok" }),
  red: text(cell({ fresh: false, skipped_red: true, state: null, smart: "fail" })),
  fresh: text(cell({ fresh: true, skipped_red: false, state: null, smart: "ok" })),
}));
""", encoding="utf-8", newline="\n")
    run = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert "<b>נכתב</b>" in out["written"] and 'class="bar ok"' in out["written"], out["written"]
    assert "--w:100%" in out["written"]
    assert (out["red"], out["fresh"]) == ("דולג", "—")
