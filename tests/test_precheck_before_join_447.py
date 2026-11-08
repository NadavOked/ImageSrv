"""‏#447 (ברזל 07/11, ‏room_e35abf2c) — האזהרה לפני "התחל" מופיעה **לפני** שהמשכפל מצטרף.

על ברזל: משכפל 2 הוער ב-WoL וחיכה (‏`awake=true`), ואז נפתח סבב עם יעד יחיד — מגירה 1,
‏APPLE HDD שנפסל אמש "איטי — 8 MB/s". ‏`round.precheck` היה `[]` בכל הדגימות עד היציאה,
והאזהרה נרשמה רק ביומן (‏`room_precheck_warned ... learned=slow_write`). השורש: ‏`warnings()`
דילג על מכונה שלא הצטרפה, והגל יוצא לבד באותו דופק שבו היא מצטרפת (‏`ready >= remaining`)
— בסבב עם יעד יחיד אין אף רגע שבו הגל פתוח **וגם** המכונה הצטרפה.

הכרעת נדב 07/11 (אפשרות א'): בסבב עם בחירה, האזהרה מחושבת גם למכונה שנבחרה ועוד לא הצטרפה
— מרגע הבחירה. **היציאה האוטומטית לא משתנה** (#432 בתוקף): אזהרה, לא חסימה.
"""

from __future__ import annotations

from server.rounds import room_precheck

from test_server_room import (CLONER1, CLONER2, cloner_hello, heartbeat, room,  # noqa: F401
                              room_body, room_server)
from test_precheck_writecheck_447 import DONE_Y, SLOW_X, _journal, _wave

SLOW_TEXT = "בבדיקת הכתיבה הקודמת נמדד איטי — 33 MB/s, מתחת לסף 50 MB/s"


def _slow_x_round_one(room_server) -> None:
    """סבב 1: X נפסל איטי (33 MB/s), Y נכתב. הסבב נסגר."""
    deploy = room_server["deploy"]
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["X", "Y"]})).status_code == 200
    _wave(room_server, ["X", "Y"], [SLOW_X, DONE_Y])
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "Office 2024 Standard"}).json()["ok"] is True
    heartbeat(room_server)
    assert room(deploy)["round"] is None


def test_single_target_warns_while_the_cloner_waits_before_joining(room_server):
    """התרחיש מהברזל: יעד יחיד (X), המשכפל ער ועוד לא הצטרף — האזהרה ב-`/room` כבר עכשיו."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _slow_x_round_one(room_server)
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 1, {CLONER1: ["X"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["X", "Y"], joining=False)     # ער, ממתין — לא הצטרף
    view = room(deploy)
    r, (m,) = view["round"], [x for x in view["machines"] if x["mac"] == CLONER1]
    assert r["wave_state"] == "open" and m["joined"] is False and m["awake"] is True, (r, m)
    warn = r["precheck"]
    assert [(w["serial"], w["write_slow"]) for w in warn] == [("X", True)], warn
    assert SLOW_TEXT in warn[0]["text_he"], warn

    # היציאה האוטומטית לא השתנתה (#432): מצטרף — הגל יוצא לבד, והיומן זוכר את האזהרה
    cloner_hello(anon, CLONER1, ["X", "Y"])
    assert room(deploy)["round"]["wave_state"] == "running"
    (detail,) = _journal(ctx, "room_precheck_warned")
    assert f"{CLONER1}:1 learned=slow_write" in detail, detail


def test_a_cloner_that_was_never_selected_does_not_warn(room_server):
    """רק מי שנבחר לסבב: X על shich-1 איטי, אבל הסבב בחר רק את shich-2 — אין אזהרה על X."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    _slow_x_round_one(room_server)
    cloner_hello(anon, CLONER2, ["V"], joining=False)
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 1, {CLONER2: ["V"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["X", "Y"], joining=False)
    r = room(deploy)["round"]
    assert r["wave_state"] == "open", r
    assert r["precheck"] == [], r["precheck"]


def test_only_the_selected_slot_of_a_waiting_cloner_warns():
    """מכונה שלא הצטרפה, בסבב עם בחירה: רק המגירה שנבחרה, ורק כשהיא טרייה."""
    drawers = [
        {"port": 1, "serial": "X", "fresh": True, "selected": True,
         "last_write_check": {"mbps": 21, "slow": True, "text_he": "איטי X"}},
        {"port": 2, "serial": "Y", "fresh": True, "selected": False,
         "last_write_check": {"mbps": 21, "slow": True, "text_he": "איטי Y"}},
        {"port": 3, "serial": "Z", "fresh": False, "selected": True,
         "last_write_check": {"mbps": 21, "slow": True, "text_he": "איטי Z"}},
    ]
    waiting = {"mac": CLONER1, "name": "01", "joined": False, "drawer_list": drawers}
    assert [w["serial"] for w in room_precheck.warnings([waiting], selection=True)] == ["X"]


def test_a_round_without_selection_still_waits_for_the_join():
    """סבב ישן בלי בחירה (‏roster ‏None): אין דרך לדעת מי ייכתב — רק מי שהצטרף, כמו קודם."""
    d = {"port": 1, "serial": "X", "fresh": True, "selected": False,
         "last_write_check": {"mbps": 21, "slow": True, "text_he": "איטי X"}}
    waiting = {"mac": CLONER1, "name": "01", "joined": False, "drawer_list": [d]}
    assert room_precheck.warnings([waiting], selection=False) == []
    assert len(room_precheck.warnings([{**waiting, "joined": True}], selection=False)) == 1
