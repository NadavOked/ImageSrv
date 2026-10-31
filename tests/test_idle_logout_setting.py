"""נדב 18/10: ניתוק אוטומטי בחוסר פעילות — מתג, ו"אחרי N דקות" כשהוא פעיל.

‏`console_idle_seconds` נשאר המקור, בשניות (‏#1243): ברירת מחדל 600 = פעיל, 10 דקות;
‏0 = כבוי. ה-API מקבל ומחזיר שניות, והקונסולה מציגה ושולחת דקות × 60 — דקות שלמות
1–1440, ולכן השרת מקבל 0 או כפולה של 60 בין 60 ל-86400, ושום דבר אחר. כל מי
שמחזיר `idle_seconds` (‏/login, ‏/login/mfa, ‏/me) מעביר את ה-0 כמו שהוא, והטיימר של
הקונסולה אינו רץ עליו (console_idle.test.cjs). ערך שמור (300 ב-TA) אינו משתנה בעדכון.

בקרה שלילית: ‏server/console/console_api.py ו-server/console/journal_he.py מוחזרים יחד — הסירובים
ו"אחרי 5 דקות" ביומן נופלים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.console import console_api
from server import db
from server.app import create_console_app
from server.console.journal_he import JournalTranslator


def _post(server, value):
    return server["admin"].post("/api/console/settings", json={"console_idle_seconds": value})


def test_a_fresh_install_logs_out_after_ten_minutes(server):
    assert db.DEFAULT_SETTINGS["console_idle_seconds"] == "600"
    assert server["admin"].get("/api/console/settings").json()["console_idle_seconds"] == "600"
    assert server["admin"].get("/api/console/me").json()["idle_seconds"] == 600


def test_zero_turns_it_off_everywhere_the_value_is_handed_out(server):
    assert _post(server, "0").status_code == 200
    assert server["admin"].get("/api/console/me").json()["idle_seconds"] == 0
    assert server["admin"].post("/api/console/users", json={
        "username": "ofer", "password": "Aa12345!", "role": "admin"}).status_code == 200
    login = TestClient(create_console_app(server["app"].state.runtime)).post(
        "/api/console/login", json={"username": "ofer", "password": "Aa12345!"})
    assert login.json()["idle_seconds"] == 0, login.text


@pytest.mark.parametrize("minutes", [1, 5, 1440])
def test_whole_minutes_are_kept(server, minutes):
    assert _post(server, str(minutes * 60)).status_code == 200
    assert server["admin"].get("/api/console/me").json()["idle_seconds"] == minutes * 60


@pytest.mark.parametrize("bad", ["30", "61", "90", str(1441 * 60), "-60", "abc", "", True, None])
def test_anything_else_is_refused_in_hebrew_and_nothing_is_written(server, bad):
    before = server["admin"].get("/api/console/settings").json()["console_idle_seconds"]
    r = _post(server, bad)
    assert r.status_code == 400 and r.json()["detail"] == console_api.IDLE_REFUSAL_HE
    assert "דקות" in r.json()["detail"]
    assert server["admin"].get("/api/console/settings").json()["console_idle_seconds"] == before


def test_a_kept_value_is_not_changed_by_an_update(tmp_path):
    """TA שומר 300 — "מופעל, 5 דקות". עלייה מחדש של ה-DB לא נוגעת בו."""
    path = tmp_path / "imagesrv.db"
    conn = db.connect(path)
    db.set_setting(conn, "console_idle_seconds", "300")
    conn.close()
    again = db.connect(path)
    assert db.get_setting(again, "console_idle_seconds") == "300"
    again.close()


@pytest.mark.parametrize("value, text", [
    ("300", "ניתוק אוטומטי בחוסר פעילות: אחרי 5 דקות"),
    ("60", "ניתוק אוטומטי בחוסר פעילות: אחרי דקה אחת"),
    ("0", "ניתוק אוטומטי בחוסר פעילות: כבוי"),
])
def test_the_journal_says_minutes(server, value, text):
    _label, said = JournalTranslator(server["ctx"].conn, server["ctx"].library).translate(
        "setting_change", f"console_idle_seconds={value}")
    assert said == text
    assert "שניות" not in said
