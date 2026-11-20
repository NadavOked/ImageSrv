"""#3092 (owner 15/11, option a) -- the console sends what it DREW in each chosen slot and the server refuses
when its stored inventory of this moment says otherwise.

#2458 keeps, for each chosen slot, the serial and size of the stored inventory at the moment the open-round
POST arrives (`target_slots[].shown`). That is not necessarily what the browser showed: a drawer swapped
after the console's last refresh, whose hello arrived before the POST, was recorded as "shown" and written
in wave 1. Now the console adds ``displayed`` (``[{port, serial, size_bytes}]``) to each ``target_slots``
item, and `_validate_target_slots` refuses ("המלאי ... השתנה מאז שהוצג, בחרו שוב") when serial or size
differ. No ``displayed`` = a path with no browser (the cloner's screen, a direct deploy): as before.

Negative control: server/rounds/room.py and server/static/console.js (with index.html) back to the base
together -- see the PR.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from conftest import room_slots
from console_src import console_js
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1,
    room_server,
)

SIZE = 256060514304


def _body(room_server, serials=("S1", "S2"), *, displayed="drawn"):
    """An open-round body for CLONER1 chosen as the console drew it from the stored inventory."""
    slots = room_slots(room_server["ctx"].conn, {CLONER1: list(serials)})
    if displayed == "drawn":
        slots[0]["displayed"] = [{"port": i + 1, "serial": s, "size_bytes": SIZE} for i, s in enumerate(serials)]
    elif displayed is not None:
        slots[0]["displayed"] = displayed
    return {"image_id": "img_7f3a91", "target_drives": len(serials), "target_slots": slots}


def _post(room_server, body):
    return room_server["deploy"].post("/api/console/room", json=body)


def _no_round(room_server):
    assert room_server["deploy"].get("/api/console/room").json().get("round") is None


def test_what_the_console_drew_is_what_the_server_has(room_server):
    opened = _post(room_server, _body(room_server))
    assert opened.status_code == 200, opened.text
    slots = room_server["deploy"].get("/api/console/room").json()["round"]["target_slots"]
    assert slots[0]["shown"] == [{"port": 1, "serial": "S1", "size_bytes": SIZE},
                                 {"port": 2, "serial": "S2", "size_bytes": SIZE}]
    assert "displayed" not in slots[0], "what the console sent is checked, not stored: `shown` is the record"


def test_a_drawer_swapped_after_the_console_drew_it_is_refused_and_no_round_opens(room_server):
    """The Issue's scenario: the console drew S1 in slot 1; a hello re-reported slot 1 as S9 before the POST."""
    body = _body(room_server)
    room_slots(room_server["ctx"].conn, {CLONER1: ["S9", "S2"]})       # the hello that landed in between
    refused = _post(room_server, body)
    assert refused.status_code == 400, refused.text
    assert "השתנה מאז שהוצג" in refused.json()["detail"] and "בחרו שוב" in refused.json()["detail"]
    assert "חריץ 1" in refused.json()["detail"] and CLONER1 in refused.json()["detail"]
    _no_round(room_server)


def test_a_size_that_changed_since_it_was_drawn_is_refused_too(room_server):
    body = _body(room_server)
    room_slots(room_server["ctx"].conn, {CLONER1: ["S1", "S2"]}, size_bytes=SIZE * 2)
    refused = _post(room_server, body)
    assert refused.status_code == 400 and "השתנה מאז שהוצג" in refused.json()["detail"], refused.text
    _no_round(room_server)


def test_choosing_again_after_the_refusal_opens_the_round(room_server):
    body = _body(room_server)
    room_slots(room_server["ctx"].conn, {CLONER1: ["S9", "S2"]})
    assert _post(room_server, body).status_code == 400
    opened = _post(room_server, _body(room_server, ("S9", "S2")))
    assert opened.status_code == 200, opened.text
    slots = room_server["deploy"].get("/api/console/room").json()["round"]["target_slots"]
    assert slots[0]["shown"][0]["serial"] == "S9"


def test_a_path_with_no_browser_sends_nothing_and_opens_as_before(room_server):
    """The cloner's own screen and a direct deploy build `target_slots` on the server: no `displayed`."""
    opened = _post(room_server, _body(room_server, displayed=None))
    assert opened.status_code == 200, opened.text


@pytest.mark.parametrize("displayed", [
    "junk",                                                                     # not a list
    [["S1"]],                                                                   # not objects
    [{"port": 1, "serial": "S1", "size_bytes": SIZE}],                         # slot 2 chosen, not drawn
    [{"port": 1, "serial": "S1", "size_bytes": SIZE}, {"port": 2, "serial": "S2", "size_bytes": SIZE},
     {"port": 3, "serial": "S3", "size_bytes": SIZE}],                         # a slot that was not chosen
    [{"port": 1, "serial": "S1", "size_bytes": SIZE}, {"port": 1, "serial": "S1", "size_bytes": SIZE}],   # twice
    [{"port": True, "serial": "S1", "size_bytes": SIZE}, {"port": 2, "serial": "S2", "size_bytes": SIZE}],
    [{"serial": "S1", "size_bytes": SIZE}, {"port": 2, "serial": "S2", "size_bytes": SIZE}],            # no port
    [{"port": 1, "serial": "S1", "size_bytes": True}, {"port": 2, "serial": "S2", "size_bytes": SIZE}],  # bool size
])
def test_a_displayed_that_is_not_the_contract_is_refused_not_ignored(room_server, displayed):
    refused = _post(room_server, _body(room_server, displayed=displayed))
    assert refused.status_code == 400, refused.text
    _no_round(room_server)


def test_the_console_sends_displayed_with_every_chosen_slot():
    """The submit of the open-round window builds `displayed` from the card's own disk and sends it in
    `target_slots`; an unmeasured size goes as null (the server's `size_bytes` of such a slot is None)."""
    js = console_js()
    body = re.search(r"async function openRoomRoundSubmit\(\).*?^\}", js, re.M | re.S).group(0)
    assert "displayed: drawn.get(mac)" in body and "target_slots" in body
    assert "serial: x.d.serial" in body and "Number.isInteger(x.d.size_bytes) && x.d.size_bytes > 0 ? x.d.size_bytes : null" in body
    reopen = re.search(r"async function reopenRoom\(\).*?^\}", js, re.M | re.S).group(0)
    assert "displayed" not in reopen, "reopening sends the stored selection: nothing was drawn"
