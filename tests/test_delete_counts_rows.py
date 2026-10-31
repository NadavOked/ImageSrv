"""‏DELETE מדווח האם באמת נמחקה שורה, ולא מחזיר ok עיוור (#515, #522).

עיקרון 5 — ראיה חיובית: מחיקה שלא תאמה שורה היא 404, לא 200; ‏MAC
שמתנרמל ל-None הוא 400; והיומן נכתב **רק** אחרי פעולה שקרתה. כל
הבדיקות כאן נופלות על הקוד הישן (שהחזיר ok עיוור) ועוברות על המתוקן.

‏`POST /users` נבדק מהצד השני: ‏OperationalError (דיסק מלא / DB נעול)
חייב לעלות כ-500 עם הסיבה האמיתית, ולא להתחפש ל-409 «שם תפוס».
"""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom


def events(server) -> list[str]:
    return [row["event"] for row in server["admin"].get("/api/console/journal").json()]


# --- #515: DELETE /net/{mac} -------------------------------------------------


def test_forget_unknown_device_is_404_and_not_journaled(server):
    """‏MAC תקין שאינו בטבלה — 404, והיומן אינו מתעד הסרה שלא קרתה."""
    admin = server["admin"]
    assert admin.delete("/api/console/net/aa:bb:cc:dd:ee:ff").status_code == 404
    assert "net_forget" not in events(server)


def test_forget_invalid_mac_is_400(server):
    """‏MAC שמתנרמל ל-None היה נכנס כ-WHERE mac = NULL ומחזיר ok עיוור."""
    admin = server["admin"]
    assert admin.delete("/api/console/net/not-a-mac").status_code == 400
    assert "net_forget" not in events(server)


# --- #522: DELETE /groups/{id} ----------------------------------------------


def test_delete_unknown_group_is_404_and_not_journaled(server):
    admin = server["admin"]
    assert admin.delete("/api/console/groups/grp_GHOST").status_code == 404
    assert "group_delete" not in events(server)


def test_delete_existing_group_still_works(server):
    admin = server["admin"]
    setup_classroom(server)
    # ‏#1016: כיתה עם מחשבים אינה נמחקת (409, לא 500) -- מסירים אותם קודם.
    for m in admin.get("/api/console/machines?group=grp_LAB1").json():
        assert admin.delete(f"/api/console/machines/{m['mac']}").status_code == 200
    assert admin.delete("/api/console/groups/grp_LAB1").status_code == 200
    assert "group_delete" in events(server)


# --- #522: DELETE /machines/{mac} -------------------------------------------


def test_delete_unknown_machine_is_404_and_not_journaled(server):
    admin = server["admin"]
    assert admin.delete("/api/console/machines/aa:bb:cc:dd:ee:ff").status_code == 404
    assert "machine_delete" not in events(server)


def test_delete_machine_invalid_mac_is_400(server):
    admin = server["admin"]
    assert admin.delete("/api/console/machines/not-a-mac").status_code == 400
    assert "machine_delete" not in events(server)


def test_delete_existing_machine_still_works(server):
    admin = server["admin"]
    ids = setup_classroom(server)
    assert admin.delete(f"/api/console/machines/{ids['mac1']}").status_code == 200
    assert "machine_delete" in events(server)


# --- #522: POST /users — except Exception לא ממציא סיבה ----------------------


def test_create_user_operational_error_surfaces_not_409(server, monkeypatch):
    """כשל אמיתי ביצירה (דיסק מלא / DB נעול) חייב לעלות כ-500 עם הסיבה,
    ולא להתחפש ל-409 «שם תפוס» ששולח את המפעיל לחפש חשבון שלא נוצר."""
    from server.console import users

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(users, "create", boom)
    # ‏TestClient מוגדר להרים חריגת שרת לא-מטופלת (500 בייצור). הקוד
    # הישן בלע אותה ל-409; המתוקן נותן לה לעלות.
    with pytest.raises(sqlite3.OperationalError):
        server["admin"].post(
            "/api/console/users",
            json={"username": "newguy", "password": "password123", "role": "deploy"},
        )


def test_create_duplicate_user_is_still_409(server):
    """הצורה הנכונה שנשמרה: התנגשות UNIQUE אמיתית עדיין 409."""
    admin = server["admin"]
    body = {"username": "dupe", "password": "Password123!", "role": "deploy"}
    assert admin.post("/api/console/users", json=body).status_code == 200
    assert admin.post("/api/console/users", json=body).status_code == 409
