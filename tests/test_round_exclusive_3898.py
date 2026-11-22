"""#3898 (שלב 1) -- משכפל נמצא בסבב אחד בלבד, והסבב השני כבר אינו נחסם בגלל הראשון.

**מה שהפרוסה הזו עושה:** הבדיקה `active_round(...) is not None` ב-`open_round` הוחלפה בבלעדיות משכפל (`round_cloners`).
`room.PARALLEL_ROUNDS` כבוי עד שלב 3 (חריץ השידור והמנועים עדיין יחידים), ולכן כדי לפתוח סבב שני כאן הגל של הראשון נסגר
ידנית: סבב פעיל בין גלים הוא מצב חוקי, והחריץ שלו פנוי. כבוי = ההתנהגות כמו לפני כן.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_round_suggest_3897 import add, mac, open_with, suggest  # noqa: E402
from test_server_room import room_server  # noqa: E402,F401

import threading  # noqa: E402
import time  # noqa: E402

from server.rounds import room, round_cloners  # noqa: E402

P3 = [1, 2, 3]


def slots(n: int) -> list[dict]:
    return [{"mac": mac(n), "ports": P3}]


def between_waves(rs, round_id_of_mac_n: int) -> None:
    """הגל של הסבב שמשכפל n בו נסגר: הסבב פעיל, חריץ השידור פנוי."""
    row = room.round_of_mac(rs["ctx"].conn, mac(round_id_of_mac_n))
    rs["ctx"].conn.execute("UPDATE sessions SET state = 'closed' WHERE id = ?", (row["wave_session_id"],))
    rs["ctx"].conn.commit()


def holders(rs, *numbers: int) -> dict[int, str]:
    """n -> מזהה הסבב הפעיל שמשכפל n בו (רק אלה שתפוסים)."""
    found = round_cloners.busy(rs["ctx"].conn, {mac(n) for n in numbers})
    return {n: found[mac(n)]["id"] for n in numbers if mac(n) in found}


def test_by_default_a_second_round_is_still_refused_with_the_old_message(room_server):
    rs = room_server
    for n in (1, 2):
        add(rs, n)
    assert open_with(rs, 3, slots(1)).status_code == 200
    between_waves(rs, 1)
    refused = open_with(rs, 3, slots(2))
    assert refused.status_code == 409 and "כבר יש סבב חדר פעיל" in refused.json()["detail"], refused.text


def test_two_rounds_on_different_cloners_open_and_each_holds_its_own(room_server, monkeypatch):
    rs = room_server
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", True)
    for n in (1, 2):
        add(rs, n)
    assert open_with(rs, 3, slots(1)).status_code == 200
    between_waves(rs, 1)
    assert open_with(rs, 3, slots(2)).status_code == 200
    a, b = room.round_of_mac(rs["ctx"].conn, mac(1)), room.round_of_mac(rs["ctx"].conn, mac(2))
    assert a["id"] != b["id"]
    assert holders(rs, 1, 2, 3) == {1: a["id"], 2: b["id"]}
    assert [r["id"] for r in room.active_rounds(rs["ctx"].conn)] == [a["id"], b["id"]]


def test_a_third_round_cannot_take_a_cloner_that_is_in_another_round_and_says_which(room_server, monkeypatch):
    rs = room_server
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", True)
    for n in (1, 2, 3):
        add(rs, n)
    assert open_with(rs, 3, slots(1)).status_code == 200
    between_waves(rs, 1)
    before = rs["ctx"].conn.execute("SELECT COUNT(*) AS n FROM room_rounds").fetchone()["n"]
    refused = open_with(rs, 6, slots(1) + slots(3))
    assert refused.status_code == 409, refused.text
    assert "משכפל-1 כבר בסבב אחר" in refused.json()["detail"], refused.text
    assert rs["ctx"].conn.execute("SELECT COUNT(*) AS n FROM room_rounds").fetchone()["n"] == before
    assert holders(rs, 3) == {}, "a refused round holds nothing"


def test_openers_go_one_at_a_time_so_the_second_sees_the_first(room_server, monkeypatch):
    rs = room_server
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", True)
    add(rs, 1)
    done = {}

    def second():
        done["response"] = open_with(rs, 3, slots(1))

    with room._OPEN_LOCK:                       # another opener is in the middle of its open
        worker = threading.Thread(target=second)
        worker.start()
        time.sleep(0.4)
        assert worker.is_alive() and "response" not in done, "an opener must wait for the one before it"
    worker.join(10)
    assert done["response"].status_code == 200, done["response"].text


def test_a_cloner_is_free_again_when_its_round_ends(room_server, monkeypatch):
    rs = room_server
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", True)
    for n in (1, 2):
        add(rs, n)
    assert open_with(rs, 3, slots(1)).status_code == 200
    a = room.round_of_mac(rs["ctx"].conn, mac(1))["id"]
    rs["ctx"].conn.execute("UPDATE room_rounds SET state = 'closed' WHERE id = ?", (a,))
    rs["ctx"].conn.execute("UPDATE sessions SET state = 'closed' WHERE id IN (SELECT wave_session_id FROM room_rounds WHERE id = ?)", (a,))
    rs["ctx"].conn.commit()
    assert holders(rs, 1) == {}
    assert open_with(rs, 3, slots(1)).status_code == 200
    assert holders(rs, 1)[1] != a


def test_the_suggestion_marks_the_cloners_of_every_active_round_busy(room_server, monkeypatch):
    rs = room_server
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", True)
    for n in (1, 2, 3):
        add(rs, n)
    assert open_with(rs, 3, slots(1)).status_code == 200
    between_waves(rs, 1)
    assert open_with(rs, 3, slots(2)).status_code == 200
    s = suggest(rs, 3)
    assert {m["number"]: m["state"] for m in s["machines"]} == {1: "busy", 2: "busy", 3: "free"}
    assert [i["mac"] for i in s["selected"]] == [mac(3)]
