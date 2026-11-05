"""לשונית הרשת, תתי-הלשוניות של המחשבים, ושינוי שם תיקייה.

הרשימה נבנית ממה שהשרת ראה בפועל — ולכן הבדיקות מדברות איתו כמו
שהסוכן מדבר (hello מלא), ולא מזריקות שורות ל-DB.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom
from fastapi.testclient import TestClient


def hello(server, mac, ip=None):
    """‏`ip` — הכתובת **שממנה** ה-hello נשלח (ה-peer של החיבור). מאז #2432 השרת
    רושם אותה, ולא את `ip` שבגוף; הגוף נושא כתובת אחרת כדי שהבדיקה תבחין."""
    body = hello_body(mac)
    client = server["anon"]
    if ip:
        body["ip"] = "10.99.99.99"
        client = TestClient(server["app"], client=(ip, 40000))
    return client.post("/api/v1/agent/hello", json=body).json()


# --- רשת ---------------------------------------------------------------------


def test_any_machine_that_talks_is_listed(server):
    """גם מכונה שאינה בטבלת ה-MAC — זה בדיוק הערך של הלשונית."""
    hello(server, "aa:bb:cc:11:22:33", ip="10.44.12.50")
    devices = server["admin"].get("/api/console/net").json()
    assert len(devices) == 1
    device = devices[0]
    assert device["mac"] == "aa:bb:cc:11:22:33"
    assert device["ip"] == "10.44.12.50"
    assert device["registered"] is False
    assert device["first_seen"] and device["last_seen"]


def test_registered_machines_show_their_name_and_group(server):
    ids = setup_classroom(server)
    hello(server, ids["mac1"], ip="10.44.12.51")
    device = server["admin"].get("/api/console/net").json()[0]
    assert device["registered"] is True
    assert device["name"] == "05"
    assert device["group_label"] == "כיתה LAB1"
    assert device["role"] == "classroom"


def test_description_survives_the_next_hello(server):
    """התיאור הוא של האדם; דיווח חוזר מעדכן IP בלבד."""
    hello(server, "aa:bb:cc:11:22:33", ip="10.44.12.50")
    admin = server["admin"]
    assert admin.put("/api/console/net/aa:bb:cc:11:22:33",
                     json={"description": "מדפסת מעבדה 2"}).status_code == 200
    hello(server, "aa:bb:cc:11:22:33", ip="10.44.12.99")
    device = admin.get("/api/console/net").json()[0]
    assert device["description"] == "מדפסת מעבדה 2"
    assert device["ip"] == "10.44.12.99"


def test_manual_add_normalizes_the_mac(server):
    r = server["admin"].post("/api/console/net",
                             json={"mac": "AA-BB-CC-11-22-44", "ip": "10.0.0.5",
                                   "description": "עמדה חדשה"})
    assert r.status_code == 200 and r.json()["mac"] == "aa:bb:cc:11:22:44"
    assert server["admin"].post("/api/console/net", json={"mac": "nope"}).status_code == 400


def test_forgetting_is_not_permanent(server):
    hello(server, "aa:bb:cc:11:22:33")
    admin = server["admin"]
    assert admin.delete("/api/console/net/aa:bb:cc:11:22:33").status_code == 200
    assert admin.get("/api/console/net").json() == []
    hello(server, "aa:bb:cc:11:22:33")
    assert len(admin.get("/api/console/net").json()) == 1


def test_net_writes_are_admin_only(server):
    deploy = server["deploy"]
    assert deploy.post("/api/console/net", json={"mac": "aa:bb:cc:11:22:55"}).status_code == 403
    assert deploy.put("/api/console/net/aa:bb:cc:11:22:55",
                      json={"description": "x"}).status_code == 403
    assert deploy.delete("/api/console/net/aa:bb:cc:11:22:55").status_code == 403


# --- הקבוצות הקבועות ---------------------------------------------------------


def test_the_two_fixed_groups_exist_from_the_start(server):
    groups = {g["id"]: g for g in server["admin"].get("/api/console/groups").json()}
    assert groups["grp_CLONERS"]["role"] == "cloner"
    assert groups["grp_BUILD"]["role"] == "build"


def test_fixed_groups_cannot_be_deleted(server):
    """חדר השיכפולים ומחשב הבנייה יחידים — מסירים מהם מכונות, לא מוחקים."""
    admin = server["admin"]
    assert admin.delete("/api/console/groups/grp_CLONERS").status_code == 400
    assert admin.delete("/api/console/groups/grp_BUILD").status_code == 400
    setup_classroom(server)
    # ‏#1016: כיתה עם מחשבים מסורבת (409); אחרי שהוסרו -- נמחקת.
    assert admin.delete("/api/console/groups/grp_LAB1").status_code == 409
    for m in admin.get("/api/console/machines?group=grp_LAB1").json():
        assert admin.delete(f"/api/console/machines/{m['mac']}").status_code == 200
    assert admin.delete("/api/console/groups/grp_LAB1").status_code == 200


def test_classroom_groups_can_be_renamed(server):
    admin = server["admin"]
    setup_classroom(server)
    assert admin.put("/api/console/groups/grp_LAB1",
                     json={"label": "כיתה LAB1 — סייבר"}).status_code == 200
    groups = {g["id"]: g for g in admin.get("/api/console/groups").json()}
    assert groups["grp_LAB1"]["label"] == "כיתה LAB1 — סייבר"
    assert admin.put("/api/console/groups/grp_LAB1", json={"label": " "}).status_code == 400


# --- שינוי שם תיקייה ---------------------------------------------------------


def test_renaming_a_folder_moves_its_images(server):
    admin = server["admin"]
    r = admin.put("/api/console/folders/Office", json={"name": "General Teaching"})
    assert r.status_code == 200 and r.json()["name"] == "General Teaching"

    folders = {f["name"]: f for f in admin.get("/api/console/folders").json()}
    assert "Office" not in folders
    assert folders["General Teaching"]["images"] == 2
    assert all(m["folder"] == "General Teaching"
               for m in admin.get("/api/console/images").json())


def test_renaming_onto_an_existing_folder_is_refused(server):
    admin = server["admin"]
    admin.post("/api/console/folders", json={"name": "Cyber"})
    assert admin.put("/api/console/folders/Office", json={"name": "Cyber"}).status_code == 409


def test_description_only_edit_keeps_the_name(server):
    admin = server["admin"]
    assert admin.put("/api/console/folders/Office",
                     json={"description": "תחנות הוראה כלליות"}).status_code == 200
    folders = {f["name"]: f for f in admin.get("/api/console/folders").json()}
    assert folders["Office"]["description"] == "תחנות הוראה כלליות"
    assert folders["Office"]["images"] == 2
