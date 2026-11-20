"""#3209: in a direct send, a drawer that was pulled is grey "pulled", not red "failed".

At the cloner (#3270) a drawer pulled mid-write is a state of the process: grey, "נשלף · ממתין להחזרה". The build
machine's direct send draws the SAME cards from the server's room (``directgui.sh``, #2849), and the server dropped
the one thing that tells the two apart: the agent (#3288, ``drawer_back_json``) sends ``pulled: true`` next to a
``failed`` target the round still waits for, and ``drawer_list`` had no field for it -- so the build machine drew every
pulled drawer red, in the words of a lost write.

* ``drawer_list[].pulled`` is true on evidence only: the target is ``failed``, the agent said ``pulled`` as a real
  ``true``, **and the machine is still ``writing``**. ``state`` stays ``failed`` (the server, the console and the
  decisions read it);
* once the machine is past the write (``verifying`` and on, or finished) nobody waits for the drawer: it is
  failed, by its own reason -- the third requirement, "a drawer that did not come back by the round's end fails";
* ``directgui.sh`` turns ``failed`` + ``pulled`` into the word ``pulled`` of the cloner's own ``drawer=`` record.
"""

from __future__ import annotations

import shutil

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.rounds import room
from test_agent import BASH
from test_build_direct_drawers_2849 import STATION, records
from test_server_room import CLONER1, cloner_hello, report, room as room_view, room_body, room_server  # noqa: F401


def _row(state: str) -> dict:
    return {"state": state}


# --- the server: on evidence only ---------------------------------------------------------------------------------

def test_pulled_is_true_only_for_a_failed_target_the_agent_called_pulled_while_the_machine_writes():
    ok = {"dev": "sdc", "state": "failed", "pulled": True}
    assert room._pulled(ok, _row("writing")) is True
    for target, member, why in (
        ({**ok, "pulled": False}, _row("writing"), "the agent said it is not"),
        ({"dev": "sdc", "state": "failed"}, _row("writing"), "a failure of its own: no word from the agent"),
        ({**ok, "pulled": "true"}, _row("writing"), "a string is not the agent's word"),
        ({**ok, "pulled": 1}, _row("writing"), "1 is not true"),
        ({**ok, "state": "writing"}, _row("writing"), "it is still writing: not a failure at all"),
        ({**ok, "state": "done"}, _row("writing"), "it completed"),
        (ok, _row("verifying"), "past the write nobody waits for it"),
        (ok, _row("partial"), "the machine finished"),
        (ok, _row("failed"), "the machine failed"),
        (ok, None, "no machine in the round: nobody to ask"),
    ):
        assert room._pulled(target, member) is False, why


# --- the room, from a real report --------------------------------------------------------------------------------

def _post(anon, wave: str, top: str, targets: list[dict]) -> None:
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": top, "targets": targets}).status_code == 200


def _drawers(deploy) -> dict:
    machine = next(m for m in room_view(deploy)["machines"] if m["mac"] == CLONER1)
    return {d["dev"]: d for d in machine["drawer_list"]}


def _targets(pulled: bool | None) -> list[dict]:
    pulled_one = {"dev": "sdc", "bytes_written": 4, "bytes_total": 100, "state": "failed",
                  "error": "הכונן נעלם מהמערכת (נשלף או נותק)"}
    if pulled is not None:
        pulled_one["pulled"] = pulled
    return [{"dev": "sda", "bytes_written": 50, "bytes_total": 100, "state": "writing"},
            {"dev": "sdb", "bytes_written": 100, "bytes_total": 100, "state": "failed", "error": "I/O error"},
            pulled_one]


def test_a_pulled_drawer_is_pulled_in_the_room_while_the_round_waits_for_it_and_failed_after(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    deploy.post("/api/console/room", json=room_body(room_server, 3, {CLONER1: ["S1", "S2", "S3"]}))
    wave = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]

    _post(anon, wave, "writing", _targets(True))
    got = _drawers(deploy)
    assert got["sdc"]["state"] == "failed" and got["sdc"]["pulled"] is True      # state is untouched
    assert got["sdb"]["state"] == "failed" and got["sdb"]["pulled"] is False     # a write error of its own
    assert got["sda"]["pulled"] is False                                         # writing

    # the agent past the write: no `pulled` any more -- and even a stale one is not believed
    _post(anon, wave, "verifying", _targets(None))
    assert _drawers(deploy)["sdc"]["pulled"] is False
    _post(anon, wave, "verifying", _targets(True))
    assert _drawers(deploy)["sdc"]["pulled"] is False

    # the round's end, with the drawer still out: failed, by its name
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "failed", "sdc": "failed"})
    got = _drawers(deploy)
    assert got["sdc"]["state"] == "failed" and got["sdc"]["pulled"] is False


@requires_native(("bash", BASH), ("jq", shutil.which("jq")), why="directgui.sh קורא ל-jq")
def test_the_build_machine_draws_what_the_server_said_through_the_whole_chain(room_server, tmp_path):
    """The room as the console API returns it -> ``directgui.sh`` -> the words of the cloner's own records."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    deploy.post("/api/console/room", json=room_body(room_server, 3, {CLONER1: ["S1", "S2", "S3"]}))
    wave = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]
    _post(anon, wave, "writing", _targets(True))

    def words(sub: str) -> dict[str, str]:
        (tmp_path / sub).mkdir()
        out = records(tmp_path / sub, STATION, room=room_view(deploy))
        return {l.split("|")[1]: l.split("|")[2] for l in out if l.startswith("drawer=")}

    assert words("waiting") == {"sda": "writing", "sdb": "failed", "sdc": "pulled"}
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "failed", "sdc": "failed"})
    assert words("ended") == {"sda": "done", "sdb": "failed", "sdc": "failed"}


# --- the kiosk: the word -----------------------------------------------------------------------------------------

@requires_native(("bash", BASH), ("jq", shutil.which("jq")), why="directgui.sh קורא ל-jq")
@pytest.mark.parametrize("drawer, word", [
    ({"state": "failed", "pulled": True}, "pulled"),
    ({"state": "failed", "pulled": False}, "failed"),
    ({"state": "failed"}, "failed"),                       # an older server: no field, red as before
    ({"state": "writing", "pulled": True}, "writing"),     # the word is a failure's only
    ({"state": "done", "pulled": True}, "done"),
    ({"state": "failed", "pulled": "true"}, "failed"),
])
def test_only_a_failed_drawer_the_server_calls_pulled_gets_the_word_pulled(tmp_path, drawer, word):
    room_json = {"round": {"image_name": "x"}, "machines": [
        {"name": "1", "mac": "64:31:50:42:b1:9c", "drawer_list": [{"dev": "sdc", "port": 1, **drawer}]}]}
    line = [l for l in records(tmp_path, STATION, room=room_json) if l.startswith("drawer=")][0]
    assert line.split("|")[2] == word
