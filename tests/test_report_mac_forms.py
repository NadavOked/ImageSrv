"""‏#108 — דיווח התקדמות בכל אחת מצורות ה-MAC של סעיף 6.

‏`hello` ו-`pulls` מנרמלים את ה-MAC לפני שהם נוגעים ב-DB, והסכימה
מצהירה שהעמודה קנונית (‏`db.py`: "קנוני: lowercase עם נקודתיים").
דיווח ההתקדמות השווה גולמי — ולכן מכונה שהצטרפה כ-`b4:2e:...`
ומדווחת כ-`B4-2E-...` נענתה "אינה חברה", נרשמה ביומן כמדווחת
מחוץ לסבב, והגל לא נסגר כי שורתה נשארה `waiting`.

הבדיקות כאן הן משני הצדדים של עיקרון 5: מכונה שכן חברה חייבת
להיקלט בכל צורה, ומחרוזת שאינה MAC כלל חייבת לקבל תשובה **אחרת**
מ"אינה חברה" — אלה שני מצבים, לא אחד.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom
from server.core.tasks import TOKEN_HEADER
from test_capture import task_token

CANONICAL = "b4:2e:99:07:1a:c4"
#: שלוש הווריאציות שסעיף 6 והתיעוד של `normalize_mac` מבטיחים.
FORMS = ["B4:2E:99:07:1A:C4", "b4-2e-99-07-1a-c4", "b42e99071ac4"]


def hello(server, mac):
    response = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert response.status_code == 200
    return response.json()


def open_session(server, expected=1):
    ids = setup_classroom(server, expected)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": expected},
    )
    assert response.status_code == 200
    ids["session"] = response.json()["id"]
    return ids


def progress(server, mac, session_id, state="writing", written=4194304):
    return server["anon"].post("/api/v1/agent/progress", json={
        "session_id": session_id, "mac": mac, "state": state,
        "targets": [{"dev": "sda", "bytes_written": written,
                     "bytes_total": 57982058496, "state": state}],
    })


def members(server):
    return server["admin"].get(
        "/api/console/overview").json()["session"]["members"]


def journal_events(server):
    return [row["event"] for row in
            server["admin"].get("/api/console/journal").json()]


def journal_details(server, event):
    return [row["detail"] for row in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = ?", (event,))]


# --- הצד החיובי: אותה מכונה, צורה אחרת --------------------------------------


def test_a_member_reporting_in_dashes_is_recognized_and_the_round_closes(server):
    """הבקרה השלילית של #108: הצטרפה קנונית, מדווחת במקפים ורישיות."""
    ids = open_session(server, expected=1)
    assert hello(server, CANONICAL)["session"]["joined"] == 1   # הצטרפה קנונית

    response = progress(server, "B4-2E-99-07-1A-C4", ids["session"],
                        state="done", written=57982058496)
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # ראיה חיובית שהשורה הנכונה עודכנה, ולא ששום דבר לא התפוצץ.
    member = members(server)[0]
    assert member["mac"] == CANONICAL
    assert member["state"] == "done" and member["done"] is True
    assert member["bytes_written"] == 57982058496

    # ומכאן הגל נסגר: הסבב לא מוצע שוב לאותה מכונה (אין לולאת שחזור).
    assert hello(server, CANONICAL)["session"] is None
    # ומכונה לגיטימית לא נרשמה כמדווחת מחוץ לסבב.
    assert "report_from_nonmember" not in journal_events(server)


@pytest.mark.parametrize("form", FORMS)
def test_every_form_of_section_six_reaches_the_same_row(server, form):
    ids = open_session(server, expected=1)
    hello(server, CANONICAL)

    assert progress(server, form, ids["session"]).json() == {"ok": True}
    assert len(members(server)) == 1               # לא נוצרה שורה שנייה
    assert members(server)[0]["bytes_written"] == 4194304


def test_a_capture_task_report_in_dashes_updates_the_task(server):
    """אותו באג ב-`_ingest_task`: ‏`tasks.mac` קנוני גם הוא."""
    mac = "aa:bb:cc:00:00:10"
    assert server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "BUILD-PC", "group_id": "grp_BUILD",
    }).status_code == 200
    created = server["admin"].post("/api/console/tasks/capture", json={
        "mac": mac, "disk": "sda", "name": "Windows 11 Base",
        "description": "נקלט בבדיקה", "folder": "Office",
    }).json()

    response = server["anon"].post("/api/v1/agent/progress", json={
        "task_id": created["id"], "mac": "AA-BB-CC-00-00-10",
        "state": "capturing",
        "targets": [{"dev": "sda", "bytes_written": 4096,
                     "bytes_total": 100000, "state": "capturing"}],
    }, headers={TOKEN_HEADER: task_token(server, created["id"])})
    assert response.status_code == 200 and response.json() == {"ok": True}

    task = server["admin"].get("/api/console/tasks").json()[0]
    assert task["state"] == "running" and task["bytes_written"] == 4096
    assert "report_from_nonmember" not in journal_events(server)


# --- הצד השני של עיקרון 5: מה ש**באמת** אינו חבר, ומה שאינו MAC בכלל --------


@pytest.mark.parametrize("bad, code", [
    ("not-a-mac", "bad_mac"),          # תווים, לא MAC
    ("b4:2e:99:07:1a", "bad_mac"),     # חמש אוקטטות — קצר מדי
    ("  ", "bad_mac"),                 # רווחים
    (17, "bad_mac"),                   # לא מחרוזת בכלל
    ("", "bad_report"),                # השדה חסר — זו כבר שגיאת מבנה
])
def test_an_unreadable_mac_is_not_called_a_nonmember(server, bad, code):
    """"לא הצלחנו לקרוא את המזהה" אינו "המכונה אינה חברה"."""
    ids = open_session(server, expected=1)
    hello(server, CANONICAL)

    response = progress(server, bad, ids["session"])
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert response.json()["code"] != "not_member"
    # ובוודאי לא נרשם ביומן כדיווח מחוץ לסבב — זו טענה שלא נבדקה.
    assert "report_from_nonmember" not in journal_events(server)


def test_a_real_nonmember_is_still_journaled(server):
    """הנרמול לא מבטל את הרישום — מדווח זר הוא מידע אמיתי."""
    ids = open_session(server, expected=1)
    hello(server, CANONICAL)

    response = progress(server, "AA-AA-AA-AA-AA-AA", ids["session"])
    assert response.status_code == 400
    assert response.json()["code"] == "not_member"
    assert "report_from_nonmember" in journal_events(server)


def test_the_journal_records_both_the_canonical_and_the_sent_form(server):
    """מה שנרשם מאבחן: הצורה שמחפשים בטבלה, והצורה שהגיעה בפועל."""
    ids = open_session(server, expected=1)
    hello(server, CANONICAL)
    progress(server, "AA-AA-AA-AA-AA-AA", ids["session"])

    details = journal_details(server, "report_from_nonmember")
    assert len(details) == 1
    assert "aa:aa:aa:aa:aa:aa" in details[0]      # מה שנבדק מול ה-DB
    assert "AA-AA-AA-AA-AA-AA" in details[0]      # מה שהלקוח שלח
    assert ids["session"] in details[0]


def test_a_canonical_report_does_not_add_noise_to_the_journal(server):
    """כשהצורות זהות אין מה להוסיף — השורה נשארת כפי שהייתה."""
    ids = open_session(server, expected=1)
    hello(server, CANONICAL)
    progress(server, "aa:aa:aa:aa:aa:aa", ids["session"])

    details = journal_details(server, "report_from_nonmember")
    assert details == [f"aa:aa:aa:aa:aa:aa for {ids['session']}"]
