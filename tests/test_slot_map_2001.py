"""‏#2001 — חלון "מיפוי חיבורים": מכונה ← דגם ← ברירת מחדל.

הכרעות נדב (22/10, ההערה האחרונה ב-#2001):

1. השם מחליף את "דיסק N" בכל המסכים; שורה פעילה בלי שם = "דיסק N" לפי הסדר,
   והסדר הוא סדר החיבורים (בלי גרירה).
2. מתג "המחשב הזה / כל מחשבי הדגם" — הדגם הוא ``dmi.product_name`` מה-hello.
3. חיבור שהמפעיל סימן פעיל נשאר פעיל גם ריק.
4. הוספה ידנית: "NVMe n" בלי PCI — הכתובת נשלמת כשנראה כונן.

השמירה היא ``PUT /api/console/machines/{mac}/slot-map``; האפקט נבדק במה
שהמסכים קוראים: ``/machines``, ``/room`` וה-hello של המשכפל.
"""

from __future__ import annotations

from conftest import hello_body
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401 — fixture

MAP = "/api/console/machines/{}/slot-map"
HP = "HP Compaq 8100 Elite SFF PC"


def _disk(dev, serial, port=None, pci=None):
    d = {"dev": dev, "size_bytes": 256060514304, "model": "SSD", "serial": serial,
         "removable": False, "scheme": "gpt", "has_data": False}
    if port is not None:
        d["port"] = port
    if pci is not None:
        d["pci"] = pci
    return d


def _hello(client, mac, disks, model=HP):
    body = hello_body(mac)
    body["disks"] = disks
    if model is not None:
        body["schema"] = 2
        body["inventory"] = {"dmi": {"sys_vendor": "HP", "product_name": model,
                                     "product_version": None, "board_name": None},
                             "pci": [], "tpm": None}
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _machine(client, mac):
    return next(m for m in client.get("/api/console/machines").json() if m["mac"] == mac)


def _room(client, mac):
    return next(m for m in client.get("/api/console/room").json()["machines"] if m["mac"] == mac)


def _put(client, mac, scope, slots, names=None, **extra):
    body = {"scope": scope, "slots": slots, **extra}
    if names is not None:
        body["names"] = names
    return client.put(MAP.format(mac), json=body)


def _two_hp(admin):
    _hello(admin, CLONER1, [_disk("sda", "A1", 1), _disk("sdb", "A2", 2), _disk("sdc", "A4", 4)])
    _hello(admin, CLONER2, [_disk("sda", "B1", 1), _disk("sdb", "B2", 2)])


def test_a_model_map_names_every_machine_of_the_model(room_server):
    admin = room_server["admin"]
    _two_hp(admin)
    r = _put(admin, CLONER1, "model", ["sata:4", "sata:1", "sata:2"],
             {"sata:1": "מגירה תחתונה", "sata:4": "מגירה עליונה"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == HP and sorted(r.json()["machines"]) == sorted([CLONER1, CLONER2])
    for mac in (CLONER1, CLONER2):
        m = _machine(admin, mac)
        assert m["slot_model"] == HP and m["slot_map_source"] == "model"
        # סדר החיבורים, לא סדר השליחה
        assert m["drawer_slots"] == ["sata:1", "sata:2", "sata:4"]
        assert m["slot_names"] == {"sata:1": "מגירה תחתונה", "sata:4": "מגירה עליונה"}
    # המסכים: החדר, וה-hello של המשכפל השני — שמעולם לא נשמר לו דבר בעצמו
    drawers = {d["port"]: d for d in _room(admin, CLONER1)["drawer_list"]}
    assert drawers[1]["slot_name"] == "מגירה תחתונה" and drawers[4]["slot_name"] == "מגירה עליונה"
    assert drawers[4]["drawer"] == 3
    answer = _hello(admin, CLONER2, [_disk("sda", "B1", 1)])
    assert answer["slot_names"] == {"sata:1": "מגירה תחתונה", "sata:4": "מגירה עליונה"}
    assert answer["drawer_slots"] == ["sata:1", "sata:2", "sata:4"]


def test_the_machine_beats_the_model_and_its_reset_falls_back_to_the_model(room_server):
    admin = room_server["admin"]
    _two_hp(admin)
    assert _put(admin, CLONER1, "model", ["sata:1", "sata:2"], {"sata:1": "דגם"}).status_code == 200
    assert _put(admin, CLONER2, "machine", ["sata:2"], {"sata:2": "רק שלי"}).status_code == 200
    m2 = _machine(admin, CLONER2)
    assert m2["slot_map_source"] == "machine"
    assert m2["drawer_slots"] == ["sata:2"] and m2["slot_names"] == {"sata:2": "רק שלי"}
    assert m2["model_slot_map"]["names"] == {"sata:1": "דגם"}   # החלון מציג גם את הדגם
    assert _machine(admin, CLONER1)["slot_names"] == {"sata:1": "דגם"}   # השני לא נגע
    assert _hello(admin, CLONER2, [_disk("sdb", "B2", 2)])["slot_names"] == {"sata:2": "רק שלי"}
    # "החזר לברירת המחדל" של המחשב — חוזר לדגם, לא ל"דיסק N"
    assert _put(admin, CLONER2, "machine", None).status_code == 200
    m2 = _machine(admin, CLONER2)
    assert m2["slot_map_source"] == "model" and m2["slot_names"] == {"sata:1": "דגם"}


def test_saving_for_the_model_asks_before_it_replaces_the_machines_own_maps(room_server):
    """נדב 22/10: שום דבר לא נמחק בשקט. מחשב מהדגם עם מיפוי משלו → ‏409 עם הרשימה,
    ושום דבר לא נכתב; רק ``replace_machine_maps: true`` מחליף."""
    admin, ctx = room_server["admin"], room_server["ctx"]
    _two_hp(admin)
    assert _put(admin, CLONER2, "machine", ["sata:2"], {"sata:2": "ישן"}).status_code == 200
    r = _put(admin, CLONER1, "model", ["sata:1"], {"sata:1": "חדש"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "machine_maps" and r.json()["machines"] == [CLONER2]
    assert "ל-1 מחשבים מהדגם יש מיפוי משלהם" in r.json()["message_he"]
    assert ctx.conn.execute("SELECT COUNT(*) FROM model_slot_maps").fetchone()[0] == 0
    assert _machine(admin, CLONER2)["slot_names"] == {"sata:2": "ישן"}     # לא נגע
    assert _put(admin, CLONER1, "model", ["sata:1"], {"sata:1": "חדש"},
                replace_machine_maps="yes").status_code == 400
    r = _put(admin, CLONER1, "model", ["sata:1"], {"sata:1": "חדש"}, replace_machine_maps=True)
    assert r.status_code == 200, r.text
    m2 = _machine(admin, CLONER2)
    assert m2["slot_map_source"] == "model"
    assert m2["drawer_slots"] == ["sata:1"] and m2["slot_names"] == {"sata:1": "חדש"}


def test_model_reset_returns_every_machine_to_the_convention(room_server):
    admin = room_server["admin"]
    _two_hp(admin)
    _put(admin, CLONER1, "model", ["sata:2"], {"sata:2": "x"})
    assert _put(admin, CLONER1, "model", None).status_code == 200
    m = _machine(admin, CLONER1)
    assert m["slot_map_source"] == "default" and m["slot_names"] == {}
    assert m["drawer_slots"] == ["sata:1"]   # drawer_count=1 מהשמירה — "מגירה k = sata:k"


def test_a_machine_without_a_model_can_only_save_for_itself(room_server):
    admin = room_server["admin"]
    _hello(admin, CLONER1, [_disk("sda", "A1", 1)], model=None)
    r = _put(admin, CLONER1, "model", ["sata:1"])
    assert r.status_code == 409 and "DMI" in r.json()["detail"]
    assert _machine(admin, CLONER1)["slot_map_source"] == "default"
    assert _put(admin, CLONER1, "machine", ["sata:1"], {"sata:1": "א"}).status_code == 200


def test_duplicate_names_are_refused_before_any_write(room_server):
    admin = room_server["admin"]
    _two_hp(admin)
    r = _put(admin, CLONER1, "model", ["sata:1", "sata:2"],
             {"sata:1": "מגירה עליונה", "sata:2": "מגירה עליונה"})
    assert r.status_code == 400 and "אותו שם" in r.json()["detail"]
    # שם ששווה ל"דיסק N" של שורה אחרת — אותה עמימות
    r = _put(admin, CLONER1, "machine", ["sata:1", "sata:2"], {"sata:2": "דיסק 1"})
    assert r.status_code == 400 and "דיסק 1" in r.json()["detail"]
    assert _machine(admin, CLONER1)["slot_map_source"] == "default"
    assert room_server["ctx"].conn.execute("SELECT COUNT(*) FROM model_slot_maps").fetchone()[0] == 0


def test_bad_bodies_and_roles(room_server):
    admin, deploy = room_server["admin"], room_server["deploy"]
    _two_hp(admin)
    assert _put(admin, CLONER1, "room", ["sata:1"]).status_code == 400
    assert _put(admin, CLONER1, "machine", []).status_code == 400
    assert _put(admin, CLONER1, "machine", ["sata:1"], {"sata:3": "לא פעיל"}).status_code == 400
    assert _put(admin, CLONER1, "machine", ["nvme:02:00.0"]).status_code == 400
    assert _put(admin, CLONER1, "machine", None, {"sata:1": "x"}).status_code == 400
    assert _put(deploy, CLONER1, "machine", ["sata:1"]).status_code == 403
    assert _put(admin, "aa:bb:cc:dd:ee:ff", "machine", ["sata:1"]).status_code == 404


def test_an_nvme_added_by_number_gets_its_pci_when_a_drive_shows_up(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    _hello(admin, CLONER1, [_disk("sda", "A1", 1)])
    r = _put(admin, CLONER1, "model", ["sata:1", "nvme#1"], {"nvme#1": "דיסק מהיר"})
    assert r.status_code == 200, r.text
    assert _machine(admin, CLONER1)["drawer_slots"] == ["sata:1", "nvme#1"]
    answer = _hello(admin, CLONER1, [_disk("sda", "A1", 1),
                                     _disk("nvme0n1", "NV1", pci="0000:03:00.0")])
    assert answer["drawer_slots"] == ["sata:1", "nvme:0000:03:00.0"]
    assert answer["slot_names"] == {"nvme:0000:03:00.0": "דיסק מהיר"}
    row = ctx.conn.execute("SELECT slots_json FROM model_slot_maps").fetchone()
    assert "nvme#" not in row["slots_json"]
    nv = next(d for d in _room(admin, CLONER1)["drawer_list"] if d["dev"] == "nvme0n1")
    assert nv["slot_name"] == "דיסק מהיר" and nv["drawer"] == 2


def test_the_journal_says_what_was_saved(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    _two_hp(admin)
    _put(admin, CLONER1, "model", ["sata:1"], {"sata:1": "תחתונה"})
    detail = ctx.conn.execute("SELECT detail FROM journal WHERE event = 'machine_slot_map'"
                              " ORDER BY id DESC").fetchone()["detail"]
    assert f"model={HP} machines=2" in detail and 'sata:1="תחתונה"' in detail


def test_model_save_without_own_maps_does_not_ask(room_server):
    admin = room_server["admin"]
    _two_hp(admin)
    assert _put(admin, CLONER1, "model", ["sata:1"]).status_code == 200
    # הדגם עצמו כבר שמור — שמירה חוזרת אינה "מיפוי אישי"
    assert _put(admin, CLONER2, "model", ["sata:1", "sata:2"]).status_code == 200


def test_the_default_is_sata_0_disk_1_sata_1_disk_2(room_server):
    """נדב 22/10 (חיווט המשכפל, sata-wiring-convention): בלי מיפוי שמור SATA 0 =
    דיסק 1, ‏SATA 1 = דיסק 2… ‏port הוא ataN מ-sysfs (‏ata1 = ‏SATA 0), ולכן ‏sata:k
    הוא דיסק k — בלי הזזה באחד."""
    admin = room_server["admin"]
    _hello(admin, CLONER1, [_disk("sda", "A1", 1), _disk("sdb", "A2", 2), _disk("sdc", "A3", 3)])
    m = _machine(admin, CLONER1)
    assert m["slot_map_source"] == "default" and m["drawer_count"] == 3
    assert m["drawer_slots"] == ["sata:1", "sata:2", "sata:3"]
    drawers = {d["port"]: d["drawer"] for d in _room(admin, CLONER1)["drawer_list"]}
    assert drawers == {1: 1, 2: 2, 3: 3}
    assert _hello(admin, CLONER1, [_disk("sda", "A1", 1)])["drawer_slots"] == ["sata:1", "sata:2", "sata:3"]
    # מיפוי שנשמר גובר
    _hello(admin, CLONER1, [_disk("sda", "A1", 1), _disk("sdb", "A2", 2), _disk("sdc", "A3", 3)])
    assert _put(admin, CLONER1, "machine", ["sata:2", "sata:3"]).status_code == 200
    drawers = {d["port"]: d["drawer"] for d in _room(admin, CLONER1)["drawer_list"]}
    assert drawers == {1: None, 2: 1, 3: 2}
