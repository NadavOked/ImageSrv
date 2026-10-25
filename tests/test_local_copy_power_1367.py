"""#1367 (Nadav 14/10) -- a power request over a local copy, the server's half.

The hello says a local clone is writing (`local_clone: "copying"`); the
console reads it as `local_copy` -- on the machines list, on the room's
machines and on the wave's missing ones -- and warns before "כבה אותו" /
"אתחל". A request the operator confirmed over that warning goes with
`confirm_local_copy: true` and reaches the agent as `power_confirmed`; only
such a request powers off a machine in the middle of a copy. Without it the
answer is what it was (the agent drops it during a copy, logged).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import hello_body

pytest.importorskip("fastapi")

from test_machine_reboot import CLONER as RB_CLONER  # noqa: E402
from test_machine_reboot import server  # noqa: E402,F401 -- fixture
from test_room_precheck_432_65 import _open, disk, drawers_hello  # noqa: E402
from test_server_room import CLONER1, CLONER2, room, room_server  # noqa: E402,F401 -- fixture

#: test_machine_reboot's fixture creates a classroom group (v1.2 flow)
pytestmark = pytest.mark.usefixtures("classrooms_on")


def _copying(client, mac: str, copying: bool = True, **extra) -> dict:
    body = hello_body(mac)
    body["joining"] = False
    if copying:
        body["local_clone"] = "copying"
    body.update(extra)
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _journal(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute("SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def _machine(client, mac: str) -> dict:
    r = client.get("/api/console/machines")
    assert r.status_code == 200, r.text
    return next(m for m in r.json() if m["mac"] == mac)


def _asleep(ctx, mac: str) -> None:
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    ctx.conn.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ?", (old, mac))
    ctx.conn.commit()


# --- what the console sees --------------------------------------------------------

def test_the_machine_list_says_a_local_copy_is_writing_and_only_while_it_is(server):
    _copying(server["anon"], RB_CLONER)
    assert _machine(server["admin"], RB_CLONER)["local_copy"] is True
    _copying(server["anon"], RB_CLONER, copying=False)          # a hello without the field clears it
    assert _machine(server["admin"], RB_CLONER)["local_copy"] is False


@pytest.mark.parametrize("value", ["COPYING", "idle", True, 1, ""])
def test_anything_but_copying_is_not_a_local_copy(server, value):
    _copying(server["anon"], RB_CLONER, copying=False, local_clone=value)
    assert _machine(server["admin"], RB_CLONER)["local_copy"] is False


def test_a_silent_machine_is_not_said_to_be_copying(server):
    """No fresh hello = no evidence it is writing now (and nobody to warn about)."""
    _copying(server["anon"], RB_CLONER)
    _asleep(server["ctx"], RB_CLONER)
    assert _machine(server["admin"], RB_CLONER)["local_copy"] is False


def test_the_wave_names_a_missing_machine_that_is_copying(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    drawers_hello(anon, CLONER2, [disk("S3", 1)])
    _open(deploy, target=2, slots=[{"mac": CLONER1, "ports": [1]}, {"mac": CLONER2, "ports": [1]}])
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    _copying(anon, CLONER2)
    view = room(deploy)
    (m,) = view["round"]["missing"]
    assert (m["mac"], m["reason"], m["can_poweroff"], m["local_copy"]) == (CLONER2, "not_joined", True, True)
    by_mac = {x["mac"]: x for x in view["machines"]}
    assert by_mac[CLONER2]["local_copy"] is True and by_mac[CLONER1]["local_copy"] is False


# --- the confirmation, on its way to the agent -------------------------------------

def _missing_round(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    drawers_hello(anon, CLONER2, [disk("S3", 1)])
    _open(deploy, target=2, slots=[{"mac": CLONER1, "ports": [1]}, {"mac": CLONER2, "ports": [1]}])
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    _copying(anon, CLONER2)
    return deploy, anon, room_server["ctx"]


def test_power_off_confirmed_over_the_local_copy_reaches_the_agent_confirmed(room_server):
    deploy, anon, ctx = _missing_round(room_server)
    r = deploy.post(f"/api/console/room/missing/{CLONER2}/poweroff",
                    json={"confirm_name": "shich-2", "confirm_local_copy": True})
    assert r.status_code == 200, r.text
    answer = _copying(anon, CLONER2)
    assert answer.get("power_action") == "poweroff" and answer.get("power_confirmed") is True, answer
    assert _journal(ctx, "room_missing_poweroff")[-1].endswith("local copy confirmed")
    assert _journal(ctx, "power_delivered")[-1] == f"{CLONER2} poweroff (confirmed over a local copy)"


@pytest.mark.parametrize("extra", [{}, {"confirm_local_copy": "true"}, {"confirm_local_copy": 1}])
def test_without_a_real_confirmation_the_request_is_what_it_was(room_server, extra):
    """An old console (no field) or a field that is not literally true: the
    agent gets the request unconfirmed -- and drops it during a copy."""
    deploy, anon, ctx = _missing_round(room_server)
    r = deploy.post(f"/api/console/room/missing/{CLONER2}/poweroff", json={"confirm_name": "shich-2", **extra})
    assert r.status_code == 200, r.text
    answer = _copying(anon, CLONER2)
    assert answer.get("power_action") == "poweroff" and "power_confirmed" not in answer, answer
    assert not _journal(ctx, "room_missing_poweroff")[-1].endswith("confirmed")


def test_reboot_confirmed_over_the_local_copy_reaches_the_agent_confirmed(server):
    _copying(server["anon"], RB_CLONER)
    r = server["admin"].post(f"/api/console/machines/{RB_CLONER}/reboot",
                             json={"confirm_name": "CLONER-1", "confirm_local_copy": True})
    assert r.status_code == 200, r.text
    answer = _copying(server["anon"], RB_CLONER)
    assert answer.get("power_action") == "reboot" and answer.get("power_confirmed") is True, answer
    assert "power_action" not in _copying(server["anon"], RB_CLONER)      # once


def test_a_plain_reboot_is_not_confirmed(server):
    r = server["admin"].post(f"/api/console/machines/{RB_CLONER}/reboot", json={"confirm_name": "CLONER-1"})
    assert r.status_code == 200, r.text
    answer = _copying(server["anon"], RB_CLONER, copying=False)
    assert answer.get("power_action") == "reboot" and "power_confirmed" not in answer, answer


def test_room_poweroff_confirmation_marks_every_request_confirmed(room_server):
    _copying(room_server["anon"], CLONER1)
    r = room_server["admin"].post("/api/console/room/poweroff",
                             json={"confirm_name": "מחשבי שיכפול", "confirm_local_copy": True})
    assert r.status_code == 200, r.text
    answer = _copying(room_server["anon"], CLONER1)
    assert answer.get("power_action") == "poweroff" and answer.get("power_confirmed") is True, answer


# --- the console's warning (the words are Nadav's, 14/10) --------------------------

def test_the_console_asks_over_a_local_copy_and_only_then_confirms():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    sheet_at = js.index("function localCopyPowerSheet(")
    body = js[sheet_at:js.index("\nfunction ", sheet_at + 10)]
    assert "המחשב באמצע שיכפול מקומי — " in body and "ישאיר את דיסק היעד שבור ולא שמיש" in body
    assert "danger: true" in body                                   # the red button (#1270)
    # the confirmation is sent from the two warning paths and nowhere else
    assert js.count("confirm_local_copy: true") == 2, js.count("confirm_local_copy: true")
    for fn, flag in (("function poweroffMissing(", "x.local_copy"), ("function rebootMachine(", "m.local_copy")):
        at = js.index(fn)
        part = js[at:js.index("\nfunction ", at + 10)]
        assert flag in part and part.index(flag) < part.index("confirm_local_copy: true"), fn
