"""#1554 — חדר המשכפלים הוא כל מחשבי השיכפול של השרת, בכל קבוצה.

הקונסולה מאפשרת קבוצה חדשה בתפקיד "משכפלים" (POST /groups, role=cloner),
אבל החדר נבנה על הקבוצה הקבועה `grp_CLONERS` בלבד: בשרת חיפה (17/10) שני
המשכפלים בקבוצה "חיפה — משכפלים" לא הופיעו בחדר, ולא יכלו להצטרף לסבב.
"""

from __future__ import annotations

from test_server_room import (CLONER1, CLONER2, cloner_hello, room, room_body,  # noqa: F401
                              room_server)

HAIFA = "aa:bb:cc:00:00:31"


def _haifa_cloner(admin) -> None:
    made = admin.post("/api/console/groups",
                      json={"id": "grp_HAIFA", "label": "חיפה — משכפלים", "role": "cloner"})
    assert made.status_code == 200, made.text
    assert admin.post("/api/console/machines", json={
        "mac": HAIFA, "name": "haifa-1", "group_id": "grp_HAIFA"}).status_code == 200


def test_the_room_shows_cloners_of_every_cloner_group(room_server):
    admin = room_server["admin"]
    _haifa_cloner(admin)
    macs = {m["mac"] for m in room(admin)["machines"]}
    assert macs == {CLONER1, CLONER2, HAIFA}


def test_a_cloner_in_another_cloner_group_is_woken_and_joins_the_round(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    _haifa_cloner(admin)
    opened = admin.post("/api/console/room",
                        json=room_body(room_server, 2, {CLONER1: ["S1"], HAIFA: ["H1"]}))
    assert opened.status_code == 200, opened.text
    assert len(room_server["woken"]) == 2          # #1568: "הער" מעיר רק את מה שנבחר — גם בחיפה

    answer = cloner_hello(anon, HAIFA, ["H1"])
    assert answer["session"] is not None and answer["session"]["prefix"] == "ROOM"
    assert answer["session"]["target_ports"] == [1]


def test_room_poweroff_reaches_cloners_of_every_cloner_group(room_server):
    admin = room_server["admin"]
    _haifa_cloner(admin)
    label = room(admin)["group_label"]
    r = admin.post("/api/console/room/poweroff", json={"confirm_name": label})
    assert r.status_code == 200, r.text
    assert r.json()["requested"] == 3


def test_a_classroom_or_build_machine_is_still_not_in_the_room(room_server):
    admin = room_server["admin"]
    assert admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:00:00:41", "name": "build-9", "group_id": "grp_BUILD"}).status_code == 200
    assert "aa:bb:cc:00:00:41" not in {m["mac"] for m in room(admin)["machines"]}
