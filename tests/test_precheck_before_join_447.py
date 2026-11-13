"""#447 (ברזל 07/11, room_e35abf2c) — האזהרה לפני "התחל" מופיעה **לפני** שהמשכפל מצטרף.

על ברזל: משכפל 2 הוער ב-WoL וחיכה (`awake=true`), ואז נפתח סבב עם יעד יחיד. `round.precheck`
היה `[]` בכל הדגימות עד היציאה, והאזהרה נרשמה רק ביומן. השורש: `warnings()` דילג על מכונה
שלא הצטרפה, והגל יוצא לבד באותו דופק שבו היא מצטרפת — בסבב עם יעד יחיד אין אף רגע שבו הגל
פתוח **וגם** המכונה הצטרפה.

הכרעת נדב 07/11 (אפשרות א'): בסבב עם בחירה, האזהרה מחושבת גם למכונה שנבחרה ועוד לא הצטרפה
— מרגע הבחירה. **היציאה האוטומטית לא משתנה** (#432 בתוקף): אזהרה, לא חסימה.

#2848 (נדב 11/11): "איטי בבדיקת הכתיבה הקודמת" כבר אינו אזהרה (רק הסבב הנוכחי קובע), ולכן הסימן
כאן הוא "איטי מהרגיל לדגם" (`model_slow`, #1845) — אותו כלל בחירה, סימן שעדיין מוזהר.
"""

from __future__ import annotations

from server.learn import models as learn_models
from server.rounds import room_precheck

from test_server_room import (CLONER1, CLONER2, cloner_hello, room,  # noqa: F401
                              room_body, room_server)
from test_precheck_writecheck_447 import _journal


def _x_slow_for_its_model(room_server) -> None:
    """שלושה דיסקים אחרים מאותו דגם ("Drawer SSD" של cloner_hello) ב-400 MB/s, ו-X ב-100 — איטי לדגם."""
    conn = room_server["ctx"].conn
    rows = [("ses_0", s, 400) for s in ("A", "B", "C")] + [("ses_0", "X", 100)]
    for i, (ses, serial, mbps) in enumerate(rows):
        conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES (?,?,?,?,?,?)",
                     (ses, serial, "Drawer SSD", CLONER2, mbps, f"2026-11-01T10:00:0{i}+00:00"))
    conn.commit()


def test_single_target_warns_while_the_cloner_waits_before_joining(room_server):
    """יעד יחיד (X), המשכפל ער ועוד לא הצטרף — האזהרה ב-`/room` כבר עכשיו."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _x_slow_for_its_model(room_server)
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 1, {CLONER1: ["X"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["X", "Y"], joining=False)     # ער, ממתין — לא הצטרף
    view = room(deploy)
    r, (m,) = view["round"], [x for x in view["machines"] if x["mac"] == CLONER1]
    assert r["wave_state"] == "open" and m["joined"] is False and m["awake"] is True, (r, m)
    warn = r["precheck"]
    assert [(w["serial"], w["model_slow"]) for w in warn] == [("X", True)], warn
    assert "איטי מהרגיל לדגם" in warn[0]["text_he"], warn

    # היציאה האוטומטית לא השתנתה (#432): מצטרף — הגל יוצא לבד, והיומן זוכר את האזהרה
    cloner_hello(anon, CLONER1, ["X", "Y"])
    assert room(deploy)["round"]["wave_state"] == "running"
    (detail,) = _journal(ctx, "room_precheck_warned")
    assert f"{CLONER1}:1 learned=slow_model" in detail, detail


def test_a_cloner_that_was_never_selected_does_not_warn(room_server):
    """רק מי שנבחר לסבב: X על shich-1 איטי לדגם, אבל הסבב בחר רק את shich-2 — אין אזהרה על X."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    _x_slow_for_its_model(room_server)
    cloner_hello(anon, CLONER2, ["V"], joining=False)
    assert deploy.post("/api/console/room", json=room_body(
        room_server, 1, {CLONER2: ["V"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["X", "Y"], joining=False)
    r = room(deploy)["round"]
    assert r["wave_state"] == "open", r
    assert r["precheck"] == [], r["precheck"]


SLOW_MODEL = {"mbps": 100, "median": 400, "samples": 3, "slow": True, "text_he": "איטי מהרגיל לדגם"}


def test_only_the_selected_slot_of_a_waiting_cloner_warns():
    """מכונה שלא הצטרפה, בסבב עם בחירה: רק המגירה שנבחרה, ורק כשהיא טרייה."""
    drawers = [
        {"port": 1, "serial": "X", "fresh": True, "selected": True, "model_speed": SLOW_MODEL},
        {"port": 2, "serial": "Y", "fresh": True, "selected": False, "model_speed": SLOW_MODEL},
        {"port": 3, "serial": "Z", "fresh": False, "selected": True, "model_speed": SLOW_MODEL},
    ]
    waiting = {"mac": CLONER1, "name": "01", "joined": False, "drawer_list": drawers}
    assert [w["serial"] for w in room_precheck.warnings([waiting], selection=True)] == ["X"]


def test_a_round_without_selection_still_waits_for_the_join():
    """סבב ישן בלי בחירה (roster None): אין דרך לדעת מי ייכתב — רק מי שהצטרף, כמו קודם."""
    d = {"port": 1, "serial": "X", "fresh": True, "selected": False, "model_speed": SLOW_MODEL}
    waiting = {"mac": CLONER1, "name": "01", "joined": False, "drawer_list": [d]}
    assert room_precheck.warnings([waiting], selection=False) == []
    assert len(room_precheck.warnings([{**waiting, "joined": True}], selection=False)) == 1


def test_a_past_rounds_slow_write_is_no_warning():
    """#2848 (נדב 11/11): `last_write_check` של #447 (רשומה של שרת ישן) אינו סימן — אין אזהרה."""
    d = {"port": 1, "serial": "X", "fresh": True, "selected": True,
         "last_write_check": {"mbps": 21, "slow": True, "text_he": "בבדיקת הכתיבה הקודמת נמדד איטי"}}
    assert room_precheck.warnings([{"mac": CLONER1, "name": "01", "joined": True, "drawer_list": [d]}],
                                  selection=True) == []
    assert not hasattr(learn_models, "last_check"), "the past-round check is gone, not left unused"
