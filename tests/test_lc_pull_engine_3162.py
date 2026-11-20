"""#3162: איזה מנוע שידור מקבל את הגל של "שחזור אימג' מהשרת" במשכפל (lc-pull), ומה לא מגיע אליו אף פעם.

lc-pull פותח סבב חדר של מכונה אחת (`POST /api/v1/agent/cloner-round`, `server/rounds/cloner_round.py`), וזה
אותו `open_round` של הקונסולה: הגל נרשם בקידומת `ROOM`, ולכן `chunk_wave` (`server/app.py`) בוחר בו לפי המתג
"מנוע שידור" -- ולא לפי מי שפתח. הטסטים כאן הם הצד של השרת, בלי ברזל ובלי chunkcast;
`test_lc_pull_chunk_3162.py` מריץ את אותו מסלול מקצה לקצה עם שרת אמיתי.

- הגל של הסבב שנפתח ממסך המשכפל הוא חדר של מכונה אחת, ו-`chunk_wave` עונה עליו לפי המתג **בלבד**: `chunk` --
  כן; `udpcast`, חסר, או ערך שאינו מוכר -- לא (ברירת המחדל היא udpcast, עיקרון 1).
- **אימג' בפורמט 1** (בלי `image_format: 2`) אינו מופץ עוד (הכרעת נדב 12/11) -- באף מתג: אינו מוצע במסך
  המשכפל, והפתיחה שלו מסורבת בשם, ולא נפתח סבב. "ההתנהגות של היום" היא הסירוב הזה, ולא נפילה שקטה לכותב ישן.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, hello_body, write_image
from server.db import set_setting
from server.rounds import room
from test_cloner_round_server import (IMG_256, MAC1, ask, cloner, no_round, now,  # noqa: F401 -- `now` is autouse there
                                      register)
from test_machine_identity import identity_server  # noqa: F401

LEGACY = {k: v for k, v in MANIFEST_256.items() if k != "image_format"} | {"id": "img_old_fmt1", "name": "format one"}


@pytest.mark.parametrize("setting,on_chunk", [("chunk", True), ("udpcast", False), (None, False), ("nonsense", False)],
                         ids=["chunk", "udpcast", "unset", "unknown"])
def test_the_wave_of_a_round_opened_from_the_cloner_goes_by_the_switch_alone(identity_server, setting, on_chunk):
    srv = identity_server
    ctx = srv["ctx"]
    if setting is not None:
        set_setting(ctx.conn, "stream_engine", setting)
    cloner(srv)
    assert ask(srv, ports=[1]).status_code == 200
    wave = room.active_round(ctx.conn)["wave_session_id"]
    row = ctx.conn.execute("SELECT prefix, kind, expected_clients FROM sessions WHERE id = ?", (wave,)).fetchone()
    # one machine, a room wave: what chunk_wave is asked about, and what the multicast engine has always taken
    assert (row["prefix"], row["kind"], row["expected_clients"]) == ("ROOM", "multicast", 1), dict(row)
    assert ctx.chunk.wants(wave) is on_chunk


@pytest.mark.parametrize("setting", ["chunk", "udpcast"])
def test_a_format_one_image_is_not_offered_and_not_opened_whatever_the_switch(identity_server, setting):
    srv = identity_server
    ctx = srv["ctx"]
    set_setting(ctx.conn, "stream_engine", setting)
    write_image(ctx.library.root, LEGACY)
    register(srv, MAC1, "grp_CLONERS", "1")
    body = hello_body(MAC1)
    body["disks"] = [{"dev": "sda", "size_bytes": 256_060_514_304, "port": 1, "model": "Drawer SSD",
                      "serial": "S1", "removable": False, "scheme": "gpt", "has_data": False}]
    assert srv["station"].post("/api/v1/agent/hello", json=body).status_code == 200
    state = srv["station"].get("/api/v1/agent/state", params={"mac": MAC1}).json()
    offered = [i["id"] for i in state["allowed_images"]]
    assert LEGACY["id"] not in offered, offered
    everywhere = ctx.library.allowed_for_disks(None)                           # what the route itself asks: the list is not just empty
    assert IMG_256 in everywhere and LEGACY["id"] not in everywhere, everywhere
    r = ask(srv, image_id=LEGACY["id"], ports=[1])
    assert r.status_code == 403 and r.json()["code"] == "image_not_allowed", r.text
    assert no_round(srv)
