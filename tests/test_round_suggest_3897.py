"""#3897 (נדב 20/11) -- הצעת השרת לפתיחת סבב: משכפלים פנויים לפי המספר ולפי כמות הדיסקים.

כל הבדיקות הן מול ה-API (`GET /room/suggest`, ואחר כך `POST /room` עם מה שההצעה החזירה), כדי שהבקרה השלילית
תיפול על התנהגות: מי נכנס להצעה, באיזה סדר, מי מדולג ולמה, ושההצעה היא בדיוק מה שפתיחת הסבב מקבלת.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body  # noqa: E402
from server.brain import slowdisk  # noqa: E402
from server.disks import slow_write  # noqa: E402
from server.rounds import room as room_mod  # noqa: E402
from test_server_room import room, room_server  # noqa: E402,F401


def mac(n: int) -> str:
    return f"aa:bb:cc:00:03:{n:02d}"


def add(rs, n: int, serials=("a", "b", "c"), red=()) -> None:
    """משכפל מספר n עם מגירה לכל serial (הפורט = המקום ברשימה). `red` = serials ש-SMART שלהם נכשל."""
    admin = rs["admin"]
    assert admin.post("/api/console/machines", json={"mac": mac(n), "name": str(n), "group_id": "grp_CLONERS"}).status_code == 200
    assert admin.put(f"/api/console/machines/{mac(n)}", json={"drawer_count": 3}).status_code == 200
    body = hello_body(mac(n))
    body["disks"] = [{"dev": f"sd{chr(97 + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "serial": f"M{n}{s}", "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1,
                      **({"smart": "fail", "smart_reason": "health_failed"} if s in red else {})}
                     for i, s in enumerate(serials)]
    assert rs["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def suggest(rs, n: int, who="deploy"):
    r = rs[who].get(f"/api/console/room/suggest?target_drives={n}")
    assert r.status_code == 200, r.text
    out = r.json()
    # ה-fixture רושם מראש שני משכפלים בלי מספר (shich-1/2); הם נשארים ברשימה אחרונים וריקים, והטסטים בודקים את המשכפלים הממוספרים.
    out["machines"] = [m for m in out["machines"] if m["number"] is not None]
    return out


def picked(s) -> dict[int, list[int]]:
    names = {m["mac"]: m["number"] for m in s["machines"]}
    return {names[i["mac"]]: i["ports"] for i in s["selected"]}


def open_with(rs, target: int, slots):
    return rs["deploy"].post("/api/console/room", json={"image_id": "img_7f3a91", "target_drives": target, "target_slots": slots})


def test_18_disks_three_free_cloners_and_one_busy_proposes_only_the_free_ones_in_order_as_a_line(room_server):
    rs = room_server
    for n in (3, 1, 2, 4):          # נרשמו בסדר מבולבל: ההצעה לפי המספר, לא לפי הרישום
        add(rs, n)
    assert open_with(rs, 3, [{"mac": mac(4), "ports": [1, 2, 3]}]).status_code == 200   # משכפל 4 בסבב
    s = suggest(rs, 18)
    assert picked(s) == {1: [1, 2, 3], 2: [1, 2, 3], 3: [1, 2, 3]}
    assert [m["number"] for m in s["machines"]] == [1, 2, 3, 4]
    assert [(m["number"], m["state"]) for m in s["machines"] if m["state"] == "busy"] == [(4, "busy")]
    assert (s["selected_count"], s["shortfall"], s["line"], s["waves"]) == (9, 9, True, 2)
    assert s["machines"][3]["busy_in"], "המפעיל צריך לראות באיזה סבב המשכפל תפוס"


def test_a_red_drawer_is_never_proposed_and_the_next_cloner_fills_in(room_server):
    rs = room_server
    add(rs, 1)
    add(rs, 2, red=("c",))
    add(rs, 3)
    s = suggest(rs, 7)
    assert picked(s) == {1: [1, 2, 3], 2: [1, 2], 3: [1, 2]}
    red = [d for m in s["machines"] for d in m["drawers"] if d["state"] == "red"]
    assert [(d["port"], bool(d["reason_he"])) for d in red] == [(3, True)]
    assert (s["shortfall"], s["line"]) == (0, False)


def test_fewer_disks_than_drawers_stops_mid_cloner_and_is_a_regular_round(room_server):
    rs = room_server
    for n in (1, 2, 3):
        add(rs, n)
    s = suggest(rs, 4)
    assert picked(s) == {1: [1, 2, 3], 2: [1]}
    assert [d["state"] for d in s["machines"][1]["drawers"]] == ["selected", "spare", "spare"]
    assert (s["selected_count"], s["shortfall"], s["line"], s["waves"]) == (4, 0, False, 1)
    assert not [m for m in s["machines"] if m["number"] == 3 and any(d["state"] == "selected" for d in m["drawers"])]


def test_an_asleep_cloner_is_proposed_but_marked_asleep(room_server):
    rs = room_server
    add(rs, 1)
    add(rs, 2)
    rs["ctx"].conn.execute("UPDATE net_devices SET last_seen = '2020-01-01T00:00:00+00:00' WHERE mac = ?", (mac(2),))
    rs["ctx"].conn.commit()
    s = suggest(rs, 6)
    assert picked(s) == {1: [1, 2, 3], 2: [1, 2, 3]}
    assert {m["number"]: m["awake"] for m in s["machines"]} == {1: True, 2: False}


def test_a_cloner_copying_locally_is_busy(room_server):
    rs = room_server
    add(rs, 1)
    add(rs, 2)
    rs["ctx"].conn.execute("UPDATE net_devices SET local_clone = 'copying' WHERE mac = ?", (mac(1),))
    rs["ctx"].conn.commit()
    s = suggest(rs, 3)
    assert picked(s) == {2: [1, 2, 3]}
    assert [m["state"] for m in s["machines"]] == ["busy", "free"]


def test_no_free_cloner_proposes_nothing_and_a_bad_count_is_refused(room_server):
    rs = room_server
    add(rs, 1)
    assert open_with(rs, 3, [{"mac": mac(1), "ports": [1, 2, 3]}]).status_code == 200
    s = suggest(rs, 5)
    assert (s["selected"], s["selected_count"], s["shortfall"], s["line"], s["waves"]) == ([], 0, 5, False, 0)
    assert rs["deploy"].get("/api/console/room/suggest?target_drives=0").status_code == 400


def test_what_the_suggestion_returns_is_accepted_by_open_round_and_opens_a_production_line(room_server):
    rs = room_server
    for n in (1, 2, 3):
        add(rs, n)
    s = suggest(rs, 12)
    assert (s["selected_count"], s["line"]) == (9, True)
    assert open_with(rs, 12, s["selected"]).status_code == 200
    assert room(rs["deploy"])["round"]["production_line"] is not None


# ---- ברזל 21/11 (#3897): מגירה שהמשכפל מציג אדום או שנפסלה כאיטית בסבב קודם אינה מוצעת, וכל החלטה נושאת סיבה ----------------

def _cloner_says_slow(rs, n: int, serial: str, mbps: int = 46) -> None:
    """מה שה-hello של משכפל n אומר על דיסק שכתב לאט: B62 רושם `RED`, והמסך של המשכפל מציג אותו אדום (הסיבה: "בגל הקודם...")."""
    now = {"serial": serial, "state": "slow", "mbps": mbps, "first_mbps": mbps, "reason": f"איטי — {mbps} MB/s, מתחת לסף 74 MB/s"}
    out = slowdisk.decide(rs["ctx"].conn, mac(n), [now], [{"serial": serial}])
    assert [d["verdict"] for d in out] == ["RED"]


def _journal_slow(rs, n: int, dev: str, serial: str, mbps: int = 19) -> None:
    """שורת `disk_write_slow` כמו שהסבב כותב אותה כשבדיקת הכתיבה פוסלת יעד."""
    target = {"dev": dev, "serial": serial, "write_preflight": {"state": "slow", "mbps": mbps, "reason": f"איטי — {mbps} MB/s"}}
    slow_write.journal_new(rs["ctx"].conn, "ses_r177", mac(n), None, [target])


def _rule_target(rs, n: int, port: int) -> bool:
    drawers = room_mod.drawer_list(rs["ctx"].conn, mac(n), set())
    return next(d for d in drawers if d["port"] == port)["rule"]["target"]


def test_a_disk_the_cloner_shows_red_for_a_slow_wave_is_not_proposed_and_says_why(room_server):
    rs = room_server
    add(rs, 1)
    add(rs, 2)
    _cloner_says_slow(rs, 2, "M2b")
    assert _rule_target(rs, 2, 2) is True, "הפער של #3897: הכלל של השרת (`rule.target`) רואה את הדיסק הזה כשר, והמשכפל מציג אותו אדום"
    s = suggest(rs, 5)
    assert picked(s) == {1: [1, 2, 3], 2: [1, 3]}
    avoided = [d for m in s["machines"] for d in m["drawers"] if d["state"] == "avoid"]
    assert [(d["port"], d["serial"]) for d in avoided] == [(2, "M2b")]
    assert "46MB/s" in avoided[0]["reason_he"] or "46 MB/s" in avoided[0]["reason_he"]
    assert "ידנית" in avoided[0]["reason_he"], "המוח מציע ולא פוסל: הסיבה אומרת שאפשר לבחור ידנית"
    assert s["shortfall"] == 0 and s["selected_count"] == 5


def test_a_disk_that_was_pronounced_slow_twice_in_the_journal_is_not_proposed(room_server):
    rs = room_server
    add(rs, 1)
    add(rs, 2)
    _journal_slow(rs, 1, "sdc", "M1c", 19)
    _journal_slow(rs, 1, "sdc", "M1c", 14)
    assert _rule_target(rs, 1, 3) is True
    s = suggest(rs, 4)
    assert picked(s) == {1: [1, 2], 2: [1, 2]}
    avoided = [d for m in s["machines"] for d in m["drawers"] if d["state"] == "avoid"]
    assert [(d["port"], d["serial"]) for d in avoided] == [(3, "M1c")]
    assert "14MB/s" in avoided[0]["reason_he"] and "סבב קודם" in avoided[0]["reason_he"]


def test_only_the_disks_that_were_slow_are_skipped_the_rest_of_the_same_cloner_are_proposed(room_server):
    rs = room_server
    add(rs, 1)
    add(rs, 2)
    _journal_slow(rs, 2, "sda", "M2a")
    _cloner_says_slow(rs, 2, "M2b")
    s = suggest(rs, 4)
    assert picked(s) == {1: [1, 2, 3], 2: [3]}, "נפסלו שני דיסקים של משכפל 2 בשני המקורות; M2c נשאר מוצע, ומשכפל 1 לא נפגע"
    assert [d["state"] for d in s["machines"][1]["drawers"]] == ["avoid", "avoid", "selected"]


def test_the_avoided_drawer_can_still_be_chosen_by_hand(room_server):
    rs = room_server
    add(rs, 1)
    _cloner_says_slow(rs, 1, "M1b")
    s = suggest(rs, 3)
    assert picked(s) == {1: [1, 3]}
    assert open_with(rs, 3, [{"mac": mac(1), "ports": [1, 2, 3]}]).status_code == 200, "ההצעה אינה חוסמת: המפעיל בוחר את המגירה בעצמו"


def test_every_decision_carries_a_reason(room_server):
    rs = room_server
    add(rs, 1, serials=("a", "b", "c"), red=("c",))
    add(rs, 2)
    add(rs, 3)
    _cloner_says_slow(rs, 2, "M2a")
    assert open_with(rs, 1, [{"mac": mac(3), "ports": [1]}]).status_code == 200      # משכפל 3 בסבב: busy
    s = suggest(rs, 4)
    states = {d["state"] for m in s["machines"] for d in m["drawers"]}
    assert {"selected", "red", "avoid"} <= states
    for m in s["machines"]:
        assert m["reason_he"], f"משכפל {m['number']} ({m['state']}) בלי סיבה"
        for d in m["drawers"]:
            assert d["reason_he"], f"מגירה {d['port']} ({d['state']}) של משכפל {m['number']} בלי סיבה"
