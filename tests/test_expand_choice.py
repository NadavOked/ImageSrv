"""#59: בחירת המחיצה להרחבה מהקונסולה, בפתיחת סבב כיתה או סבב חדר.

שלוש שכבות, ושלושתן נבדקות:

* ``imagefit.validate_expand_choice`` — הנרמול והאימות מול המניפסט,
  לפני שהסבב נפתח בכלל.
* השרת — הבחירה נשמרת על ה-session (או על ה-room_round, ומשם על **כל**
  גליו) ומגיעה ל-hello כ-``session.expand_partition``; כיבוי או בחירה
  ידנית נרשמים ביומן.
* סוכן ישן, או קורא ששכח לעדכן (pulls.py, station.py) — ``None`` נשמר
  כ-``NULL`` ונקרא חזרה כ-``"auto"``, בדיוק ההתנהגות מלפני ה-Issue הזה.

הבקרה השלילית של השכבה הרביעית — ``_expand_candidate`` בסוכן עצמו,
כולל "סוכן ישן שלא הציב EXPAND_OVERRIDE" — יושבת ב-``test_agent.py``
לצד שאר בדיקות ``expand_last``, לא כאן: שם כבר קיים ה-harness
(‏`run_expand`) שמריץ POSIX sh אמיתי מול sgdisk מזויף.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, hello_body, room_slots, setup_classroom

from server.library.imagefit import validate_expand_choice
from test_server_room import (CLONER1, CLONER2, cloner_hello, heartbeat,
                              report, room_server)  # noqa: F401


# --- הנרמול והאימות (ללא שרת) -------------------------------------------------


def test_a_missing_choice_is_auto():
    """‏None — השדה לא נשלח בכלל — הוא בדיוק "auto", כדי ש-pulls.py
    ו-station.py, ששניהם לא עודכנו, ימשיכו לקבל את הבחירה האוטומטית
    בלי לדעת שהשדה קיים (עיקרון 1)."""
    assert validate_expand_choice(MANIFEST_256, None) == "auto"


def test_auto_is_accepted_explicitly():
    assert validate_expand_choice(MANIFEST_256, "auto") == "auto"


def test_none_disables_expansion():
    assert validate_expand_choice(MANIFEST_256, "none") == "none"


def test_the_system_partition_is_accepted_manually():
    assert validate_expand_choice(MANIFEST_256, 3) == "3"
    assert validate_expand_choice(MANIFEST_256, "3") == "3"


def test_a_non_system_partition_is_refused():
    """‏#59 שוקל בדיוק מה ש-`expandable_candidate` שוקל — windows/linux
    בלבד. מחיצת ה-ESP קיימת במניפסט, אבל אינה מועמדת."""
    with pytest.raises(ValueError, match="windows/linux"):
        validate_expand_choice(MANIFEST_256, 1)


def test_an_index_that_does_not_exist_is_refused():
    with pytest.raises(ValueError):
        validate_expand_choice(MANIFEST_256, 99)


def test_garbage_is_refused_instead_of_silently_falling_back():
    """הדפוס שחזר בפרויקט הזה שבע פעמים: קלט שאי אפשר לקרוא **נדחה**,
    ולא נופל בשקט ל"auto" — אחרת בחירה שגויה נראית כמו שקדמה לה."""
    with pytest.raises(ValueError):
        validate_expand_choice(MANIFEST_256, "banana")


# --- סבב כיתה: מהקונסולה ועד ה-hello ------------------------------------------


def _open_classroom(server, **extra):
    ids = setup_classroom(server)
    body = {"group_id": ids["group"], "image_id": "img_7f3a91",
            "prefix": "LAB1", "expected_clients": 2, **extra}
    return server["admin"].post("/api/console/sessions", json=body), ids


def _hello(server, mac):
    return server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()


def test_a_round_opened_with_no_choice_reads_back_as_auto(server):
    """ברירת המחדל: מי שלא שלח את השדה בכלל (בדיוק כמו לפני #59)."""
    opened, ids = _open_classroom(server)
    assert opened.status_code == 200
    answer = _hello(server, ids["mac1"])
    assert answer["session"]["expand_partition"] == "auto"


def test_disabling_expansion_reaches_the_agent_and_the_journal(server):
    opened, ids = _open_classroom(server, expand_partition="none")
    assert opened.status_code == 200
    answer = _hello(server, ids["mac1"])
    assert answer["session"]["expand_partition"] == "none"

    rows = server["admin"].get("/api/console/journal").json()
    opened_row = next(r for r in rows if r["event"] == "session_open")
    assert "הרחבה כבויה" in opened_row["text"]


def test_a_manual_choice_reaches_the_agent_and_the_journal(server):
    opened, ids = _open_classroom(server, expand_partition="3")
    assert opened.status_code == 200
    answer = _hello(server, ids["mac1"])
    assert answer["session"]["expand_partition"] == "3"

    rows = server["admin"].get("/api/console/journal").json()
    opened_row = next(r for r in rows if r["event"] == "session_open")
    assert "הרחבת מחיצה 3" in opened_row["text"]


def test_a_default_round_open_does_not_mention_expansion_in_the_journal(server):
    """"auto" הוא ברירת המחדל השקטה — סבב שלא נגע בהרחבה לא נראה
    ביומן כמו החלטה שמישהו קיבל (בניגוד לשני הטסטים שמעליו)."""
    opened, _ids = _open_classroom(server)
    assert opened.status_code == 200
    rows = server["admin"].get("/api/console/journal").json()
    opened_row = next(r for r in rows if r["event"] == "session_open")
    assert "הרחבה" not in opened_row["text"]


def test_opening_with_an_invalid_choice_is_refused_before_anything_opens(server):
    ids = setup_classroom(server)
    opened = server["admin"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91",
        "prefix": "LAB1", "expected_clients": 2, "expand_partition": "1",
    })
    assert opened.status_code == 400
    # לא נפתח סבב בכלל — לא רק שהשדה נדחה.
    answer = _hello(server, ids["mac1"])
    assert answer["session"] is None


# --- סבב חדר: הבחירה חלה על כל הגלים, לא רק על הראשון -------------------------


def test_the_room_round_choice_survives_into_the_second_wave(room_server):
    """הבחירה נשמרת ברמת ה-round (לא רק על ה-session של הגל הראשון):
    כשגל שני נפתח מעצמו כי היעד לא הושג בראשון, הוא קורא אותה מחדש."""
    deploy, anon = room_server["deploy"], room_server["anon"]

    assert deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 6,
        "expand_partition": "none",
        "target_slots": room_slots(room_server["ctx"].conn, {
            CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}),
    }).status_code == 200
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert wave1["expand_partition"] == "none"
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)
    assert deploy.post("/api/console/room/start").status_code == 200

    report(anon, wave1["id"], CLONER1, {"sda": "done", "sdb": "done"})
    report(anon, wave1["id"], CLONER2, {"sda": "done", "sdb": "done"})
    heartbeat(room_server)                    # הגל נסגר, השני נפתח מעצמו

    wave2 = cloner_hello(anon, CLONER1, ["S5", "S6"])["session"]
    assert wave2["id"] != wave1["id"]
    assert wave2["expand_partition"] == "none", \
        "הבחירה לא שרדה לגל השני — היא נקראה רק בפתיחת הסבב"


def test_a_room_round_with_no_choice_reads_back_as_auto(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": room_slots(room_server["ctx"].conn, {CLONER1: ["S1", "S2"]}),
    }).status_code == 200
    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert answer["session"]["expand_partition"] == "auto"


def test_an_invalid_room_choice_is_refused_before_anything_wakes(room_server):
    deploy = room_server["deploy"]
    response = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "expand_partition": "1",           # ה-ESP, לא windows/linux
    })
    assert response.status_code == 400
    assert not room_server["woken"], "החדר לא אמור להתעורר על בקשה שנדחתה"
