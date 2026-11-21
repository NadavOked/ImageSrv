"""#3173 (נדב 21/11, גרסת המכללה): ברירת המחדל של ההפצה היא מנוע החתיכות.

`chunksend.engine_of` הוא מקור האמת היחיד לשאלה "איזה מנוע" (hello, `chunk_wave`, משיכה, הפצה ישירה, הקונסולה).
ההגדרה `stream_engine` חסרה, או בערך שאינו מוכר, היא `chunk`; udpcast רק כשההגדרה היא בדיוק `udpcast` --
בחירה מפורשת בהגדרות, ו-udpcast לא נמחק. הקונסולה מציגה את הערך שבתוקף, לא את מה שנשמר.
"""

from __future__ import annotations

import pytest

from server import db
from server.rounds import chunksend


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "engine.db")
    yield c
    c.close()


@pytest.mark.parametrize("stored,engine", [
    (None, "chunk"),             # never set: a new server
    ("chunk", "chunk"),
    ("udpcast", "udpcast"),      # the explicit choice, the only way to udpcast
    ("something-else", "chunk"),  # an unknown value is the default
    ("", "chunk"),
    ("UDPCAST", "chunk"),        # exactly `udpcast`, not a look-alike
    (" udpcast", "chunk"),
], ids=["unset", "chunk", "udpcast", "unknown", "empty", "upper", "spaced"])
def test_the_engine_is_chunk_unless_udpcast_is_chosen_exactly(conn, stored, engine):
    if stored is not None:
        db.set_setting(conn, chunksend.SETTING, stored)
    assert db.get_setting(conn, chunksend.SETTING) == stored     # read back: the value under test is the stored one
    assert chunksend.engine_of(conn) == engine


def test_the_console_shows_chunk_for_a_server_that_never_chose(server):
    admin, ctx = server["admin"], server["ctx"]
    db.set_settings(ctx.conn, {}, remove=(chunksend.SETTING,))       # whatever the fixture chose: a server that never did
    assert db.get_setting(ctx.conn, chunksend.SETTING) is None
    assert admin.get("/api/console/settings").json()["stream_engine"] == "chunk"
    assert admin.post("/api/console/settings", json={"stream_engine": "udpcast"}).status_code == 200
    assert admin.get("/api/console/settings").json()["stream_engine"] == "udpcast"
    assert chunksend.engine_of(ctx.conn) == "udpcast"
