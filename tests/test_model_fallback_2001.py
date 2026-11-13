"""#2001 — מפתח הדגם כשאין ``product_name`` שימושי (ממצא 22/10, HP i5-4590).

הסכנה: מיפוי מגירות שגוי כותב לדיסק הלא נכון. לכן מפתח דגם ריק או גנרי
(``To Be Filled By O.E.M.``) לעולם לא מאחד מכונות שונות; הנפילה היחידה היא ל-
``board_name`` יחד עם ``sys_vendor`` — ורק מה שהסוכן באמת שולח (``DMI_FIELDS``).
"""

from __future__ import annotations

import pytest

from server.disks import model_slots
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401 — fixture
from conftest import hello_body
from test_slot_map_2001 import _disk, _machine, _put

CLONER3 = "aa:bb:cc:00:00:23"
OEM = "To Be Filled By O.E.M."


def _dmi(vendor=None, product=None, board=None, version=None):
    return {"sys_vendor": vendor, "product_name": product, "product_version": version,
            "board_name": board}


@pytest.mark.parametrize("dmi,expected", [
    (_dmi("HP", "HP Compaq 8100 Elite SFF PC", "304Bh"), "HP Compaq 8100 Elite SFF PC"),
    (_dmi("Hewlett-Packard", None, "18E7"), "Hewlett-Packard (board 18E7)"),
    (_dmi("Hewlett-Packard", "", "18E7"), "Hewlett-Packard (board 18E7)"),
    (_dmi("ASUS", OEM, "H81M-K"), "ASUS (board H81M-K)"),
    (_dmi("ASUS", "  to be filled by oem ", "H81M-K"), "ASUS (board H81M-K)"),
    (_dmi("ASUS", "System Product Name", "PRIME B250"), "ASUS (board PRIME B250)"),
    (_dmi("Dell Inc.", "Default string", "0X1234"), "Dell Inc. (board 0X1234)"),
    # אין מקור שימושי -> אין דגם
    (_dmi("Hewlett-Packard", None, None), None),
    (_dmi(None, None, "18E7"), None),                  # מספר לוח בלי יצרן אינו מזהה
    (_dmi(OEM, None, "18E7"), None),                   # יצרן גנרי = בלי יצרן
    (_dmi("ASUS", OEM, OEM), None),
    (_dmi("Default string", "Default string", "Default string"), None),
    (_dmi("ASUS", "0", "0000"), None),
    (_dmi(), None),
    ("not a dict", None),
    (None, None),
])
def test_model_key(dmi, expected):
    assert model_slots.model_key(dmi) == expected


def _hello(client, mac, dmi, disks=None):
    body = hello_body(mac)
    body["disks"] = disks or [_disk("sda", mac[-2:] + "A", 1), _disk("sdb", mac[-2:] + "B", 2)]
    body["schema"] = 2
    body["inventory"] = {"dmi": dmi, "pci": [], "tpm": None}
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _add(admin, mac, name):
    assert admin.post("/api/console/machines", json={
        "mac": mac, "name": name, "group_id": "grp_CLONERS"}).status_code == 200


def test_machines_with_the_generic_product_name_are_not_one_model(room_server):
    """שלוש מכונות עם ``To Be Filled By O.E.M.`` — שני לוחות שונים ואחת בלי לוח.
    שמירה "לכל מחשבי הדגם" של אחת לא נוגעת באחרות (לפני התיקון: מפתח אחד לכולן)."""
    admin = room_server["admin"]
    _add(admin, CLONER3, "shich-3")
    _hello(admin, CLONER1, _dmi("ASUS", OEM, "H81M-K"))
    _hello(admin, CLONER2, _dmi("ASUS", OEM, "PRIME B250"))
    _hello(admin, CLONER3, _dmi("ASUS", OEM, None))
    models = {mac: _machine(admin, mac)["slot_model"] for mac in (CLONER1, CLONER2, CLONER3)}
    assert models == {CLONER1: "ASUS (board H81M-K)", CLONER2: "ASUS (board PRIME B250)",
                      CLONER3: None}
    r = _put(admin, CLONER1, "model", ["sata:2"], {"sata:2": "שם"})
    assert r.status_code == 200, r.text
    assert r.json()["machines"] == [CLONER1]
    assert _machine(admin, CLONER1)["slot_map_source"] == "model"
    for mac in (CLONER2, CLONER3):
        m = _machine(admin, mac)
        assert m["slot_map_source"] == "default" and m["slot_names"] == {}, mac


def test_a_machine_with_no_usable_model_saves_only_for_itself(room_server):
    admin = room_server["admin"]
    for mac, dmi in ((CLONER1, _dmi("Default string", "Default string", "Default string")),
                     (CLONER2, _dmi("Hewlett-Packard", None, None))):
        _hello(admin, mac, dmi)
        assert _machine(admin, mac)["slot_model"] is None
        r = _put(admin, mac, "model", ["sata:1"])
        assert r.status_code == 409 and "דגם" in r.json()["detail"], r.text
        assert _put(admin, mac, "machine", ["sata:1"], {"sata:1": "א"}).status_code == 200
    assert room_server["ctx"].conn.execute("SELECT COUNT(*) FROM model_slot_maps").fetchone()[0] == 0


def test_without_product_name_the_board_groups_the_same_machines(room_server):
    """HP i5-4590 מהשטח: ``product_name`` לא נקרא, אבל יצרן+לוח כן — שתי מכונות מאותו
    לוח חולקות מיפוי, ומכונה מלוח אחר לא."""
    admin = room_server["admin"]
    _add(admin, CLONER3, "shich-3")
    _hello(admin, CLONER1, _dmi("Hewlett-Packard", None, "18E7"))
    _hello(admin, CLONER2, _dmi("Hewlett-Packard", None, "18E7"))
    _hello(admin, CLONER3, _dmi("Hewlett-Packard", None, "2AB1"))
    r = _put(admin, CLONER1, "model", ["sata:2", "sata:1"], {"sata:2": "עליונה"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "Hewlett-Packard (board 18E7)"
    assert sorted(r.json()["machines"]) == sorted([CLONER1, CLONER2])
    assert _machine(admin, CLONER2)["slot_names"] == {"sata:2": "עליונה"}
    assert _machine(admin, CLONER3)["slot_map_source"] == "default"


def test_product_name_still_wins_over_the_board(room_server):
    admin = room_server["admin"]
    _hello(admin, CLONER1, _dmi("HP", "HP Compaq 8100 Elite SFF PC", "304Bh"))
    _hello(admin, CLONER2, _dmi("HP", "HP Compaq 8100 Elite SFF PC", "OTHER"))
    r = _put(admin, CLONER1, "model", ["sata:1"])
    assert r.json()["model"] == "HP Compaq 8100 Elite SFF PC"
    assert sorted(r.json()["machines"]) == sorted([CLONER1, CLONER2])


def test_a_map_stored_under_a_generic_name_no_longer_applies(room_server):
    """מיפוי שנשמר בעבר תחת ``To Be Filled By O.E.M.`` (לכל המכונות שדיווחו כך) לא חל
    על אף מכונה — עיקרון 1: ברירת המחדל, לא מיפוי של מי שלא מוכר."""
    admin, ctx = room_server["admin"], room_server["ctx"]
    _hello(admin, CLONER1, _dmi("ASUS", OEM, "H81M-K"))
    ctx.conn.execute("INSERT INTO model_slot_maps (model, slots_json, names_json, set_at, set_by)"
                     " VALUES (?, '[\"sata:2\"]', '{}', 'x', 'old')", (OEM,))
    ctx.conn.commit()
    m = _machine(admin, CLONER1)
    assert m["slot_map_source"] == "default" and m["model_slot_map"] is None
