"""‏#1269 — דרייברים לפי דגם מחשב, לכל דיסק יעד בסבב החדר (השרת).

המתג "להוסיף דרייברים" כבוי = הסבב לא משתנה בכלום: אין שדה, אין שאלה
מהסוכן. דלוק = המפעיל בוחר **דגם** מתוך מה שיש בספרייה (`drivers.models`,
מכללי הדגם של המניפסטים — לא ממציאים דגם), לכל דיסק יעד בנפרד. השרת
מאמת בפתיחה, שומר ברמת ה-round, מוסר לכל מכונה את החריצים שלה
(`session.driver_ports`), ועונה לסוכן לכל חריץ אילו חבילות להניח. דגם
שנבחר ושהחבילות שלו נמחקו מאז = הסוכן מדווח `failed` בגלוי (עיקרון 1:
השחזור ממשיך בלעדיהן).
"""

from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("fastapi")

from server.library import drivers                                        # noqa: E402
from test_server_room import (CLONER1, CLONER2, cloner_hello,     # noqa: E402,F401
                              room, room_server)

M70Q = {"vendor": "LENOVO", "model": "ThinkCentre M70q Gen 3"}
G6 = {"vendor": "HP", "model": "HP EliteDesk 800 G6"}


def _pkg(name: str, rules: list[dict], files: dict[str, bytes] | None = None) -> dict:
    files = files or {f"{name}.inf": b"[Version]\n"}
    return {"schema": 1, "name": name, "description": "", "match": rules,
            "files": [{"path": p, "sha256": hashlib.sha256(b).hexdigest()}
                      for p, b in files.items()]}


def _install(room_server, manifest: dict, files: dict[str, bytes] | None = None) -> None:
    """חבילה מאומתת ישר לספרייה — הייבוא עצמו נבדק ב-test_drivers.py."""
    root = room_server["ctx"].drivers.root / manifest["name"]
    root.mkdir(parents=True)
    files = files or {f["path"]: b"[Version]\n" for f in manifest["files"]}
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


# --- הדגמים שבספרייה — פונקציות טהורות -------------------------------------


def test_models_come_only_from_model_rules_deduplicated_and_sorted():
    packages = [
        _pkg("m70q-nic", [{"vendor": " lenovo ", "model": "thinkcentre m70q gen 3"},
                          {"pci_any": ["8086:15fa"]}]),
        # התצוגה מהחבילה הראשונה לפי שם — כאן, לא מהכתיב המקוצץ של השנייה.
        _pkg("m70q-chipset", [{"vendor": "LENOVO", "model": "ThinkCentre M70q Gen 3"}]),
        _pkg("g6-all", [G6]),
        _pkg("pci-only", [{"pci": ["8086:15bc:020000"]}]),
    ]
    assert drivers.models(packages) == [
        {"vendor": "HP", "model": "HP EliteDesk 800 G6", "packages": ["g6-all"]},
        {"vendor": "LENOVO", "model": "ThinkCentre M70q Gen 3",
         "packages": ["m70q-chipset", "m70q-nic"]},
    ]
    assert drivers.models([]) == []


def test_for_model_is_the_model_rule_only_never_pci():
    packages = [_pkg("b-nic", [M70Q]), _pkg("a-chip", [M70Q]),
                _pkg("pci-only", [{"pci_any": ["8086:15fa"]}]), _pkg("g6", [G6])]
    assert drivers.for_model("lenovo", " ThinkCentre M70q Gen 3 ", packages) == ["a-chip", "b-nic"]
    assert drivers.for_model("Dell", "OptiPlex 7090", packages) == []


# --- רשימת הדגמים למסך ------------------------------------------------------


def test_the_room_lists_the_models_the_library_has_for_the_operator(room_server):
    _install(room_server, _pkg("m70q-nic", [M70Q]))
    _install(room_server, _pkg("pci-only", [{"pci": ["8086:15bc:020000"]}]))
    answer = room_server["deploy"].get("/api/console/room/driver-models")
    assert answer.status_code == 200
    assert answer.json() == [{**M70Q, "packages": ["m70q-nic"]}]
    assert room_server["anon"].get("/api/console/room/driver-models").status_code == 401


# --- פתיחת סבב עם דרייברים ---------------------------------------------------


def _open(client, slots, driver_models=None, **extra):
    body = {"image_id": "img_7f3a91", "target_drives": 4, "target_slots": slots, **extra}
    if driver_models is not None:
        body["driver_models"] = driver_models
    return client.post("/api/console/room", json=body)


def _two_cloners(room_server):
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    _install(room_server, _pkg("m70q-nic", [M70Q]))
    _install(room_server, _pkg("g6-all", [G6]))


SLOTS = [{"mac": CLONER1, "ports": [1, 2]}, {"mac": CLONER2, "ports": [1]}]


def test_a_round_without_drivers_is_unchanged(room_server):
    _two_cloners(room_server)
    assert _open(room_server["deploy"], SLOTS).status_code == 200
    assert room(room_server["deploy"])["round"]["driver_models"] is None
    session = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])["session"]
    assert session["driver_ports"] is None


def test_the_choice_is_stored_per_disk_and_each_machine_gets_only_its_ports(room_server):
    _two_cloners(room_server)
    chosen = [{"mac": CLONER1, "port": 2, **M70Q},
              {"mac": CLONER2.upper(), "port": 1, "vendor": "hp", "model": "hp elitedesk 800 g6"}]
    assert _open(room_server["deploy"], SLOTS, chosen).status_code == 200
    # השרת שומר את הצורה הקנונית מהספרייה, לא את מה שהוקלד.
    assert room(room_server["deploy"])["round"]["driver_models"] == [
        {"mac": CLONER1, "port": 2, **M70Q}, {"mac": CLONER2, "port": 1, **G6}]
    anon = room_server["anon"]
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["driver_ports"] == [2]
    assert cloner_hello(anon, CLONER2, ["S3", "S4"])["session"]["driver_ports"] == [1]
    events = room_server["admin"].get("/api/console/journal").json()
    opened = next(e for e in events if e["event"] == "room_open")
    assert "drivers=2" in opened["text"]


@pytest.mark.parametrize("chosen, fragment", [
    ([{"mac": CLONER1, "port": 1, "vendor": "Dell", "model": "OptiPlex 7090"}], "אין בשרת"),
    ([{"mac": CLONER2, "port": 2, **M70Q}], "אינו בין דיסקי היעד"),
    ([{"mac": CLONER1, "port": 1, **M70Q}, {"mac": CLONER1, "port": 1, **G6}], "פעמיים"),
    ([{"mac": CLONER1, "port": "1", **M70Q}], "אינה תקינה"),
    ([{"mac": "nonsense", "port": 1, **M70Q}], "אינה תקינה"),
    ("M70q", "אינה תקינה"),
])
def test_a_bad_choice_is_refused_by_name_and_no_round_opens(room_server, chosen, fragment):
    _two_cloners(room_server)
    answer = _open(room_server["deploy"], SLOTS, chosen)
    assert answer.status_code == 400
    assert fragment in answer.json()["detail"]
    assert room(room_server["deploy"])["round"] is None


def test_drivers_per_disk_need_a_disk_selection(room_server):
    """מאז #1410 הבחירה עצמה חובה, ולכן הסירוב מגיע ממנה — לפני הדרייברים."""
    _two_cloners(room_server)
    answer = room_server["deploy"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 4,
        "driver_models": [{"mac": CLONER1, "port": 1, **M70Q}]})
    assert answer.status_code == 400
    assert answer.json()["detail"] == "יש לבחור לפחות דיסק יעד אחד"
    assert room(room_server["deploy"])["round"] is None


def test_the_direct_round_takes_the_same_choice(room_server):
    from test_direct_server import BUILD, _register_build   # noqa: PLC0415
    _two_cloners(room_server)
    _register_build(room_server)
    answer = room_server["admin"].post("/api/console/room", json={
        "source": {"kind": "build_disk", "mac": BUILD, "disk": "sda"},
        "target_slots": [{"mac": CLONER1, "ports": [1, 2]}],
        "driver_models": [{"mac": CLONER1, "port": 1, **G6}]})
    assert answer.status_code == 200, answer.json()
    assert room(room_server["admin"])["round"]["driver_models"] == [
        {"mac": CLONER1, "port": 1, **G6}]


def test_an_old_database_gets_the_column_and_keeps_it_through_the_state_rebuild(tmp_path):
    """‏DB מלפני #1269 **וגם** עם CHECK ישן: ה-ALTER מוסיף את העמודה, ובניית
    הטבלה מחדש (‏`_rebuild_state_tables`, שרצה אחריו) אינה מאבדת אותה."""
    import sqlite3                                                # noqa: PLC0415

    from server import db as db_module                           # noqa: PLC0415
    legacy = db_module.SCHEMA.replace(
        "    driver_models_json TEXT,               -- דגם דרייברים לכל דיסק יעד (#1269);"
        " NULL=בלי דרייברים\n", "",
    ).replace("('active', 'closing', 'failed', 'closed')", "('active', 'closed')")
    assert "driver_models_json" not in legacy
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(legacy)
    raw.close()
    conn = db_module.connect(path)
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(room_rounds)")}
    assert "driver_models_json" in columns
    assert "'closing'" in conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'room_rounds'").fetchone()["sql"]


# --- מה הסוכן שואל לכל חריץ -------------------------------------------------


def _wave(room_server, chosen):
    _two_cloners(room_server)
    assert _open(room_server["deploy"], SLOTS, chosen).status_code == 200
    return room(room_server["deploy"])["round"]["id"], room_server["ctx"].conn.execute(
        "SELECT wave_session_id FROM room_rounds").fetchone()["wave_session_id"]


def test_the_agent_gets_the_packages_of_the_model_chosen_for_that_port(room_server):
    _, wave = _wave(room_server, [{"mac": CLONER1, "port": 2, **M70Q}])
    anon = room_server["anon"]
    answer = anon.get(f"/api/v1/agent/drivers?mac={CLONER1}&session={wave}&port=2").json()
    assert answer == {"ok": True, "chosen": M70Q, "packages": [{
        "name": "m70q-nic", "by": "model",
        "files": [{"path": "m70q-nic.inf",
                   "sha256": hashlib.sha256(b"[Version]\n").hexdigest(),
                   "url": "/api/v1/agent/drivers/m70q-nic/files/m70q-nic.inf"}]}]}
    # חריץ שלא נבחר לו דגם — "לא נבחר", לא "אין התאמה".
    assert anon.get(f"/api/v1/agent/drivers?mac={CLONER1}&session={wave}&port=1").json() == {
        "ok": True, "chosen": None, "packages": []}


def test_a_chosen_model_whose_packages_were_deleted_answers_empty_not_missing(room_server):
    _, wave = _wave(room_server, [{"mac": CLONER1, "port": 2, **M70Q}])
    assert room_server["ctx"].drivers.delete("m70q-nic")
    answer = room_server["anon"].get(
        f"/api/v1/agent/drivers?mac={CLONER1}&session={wave}&port=2").json()
    assert answer == {"ok": True, "chosen": M70Q, "packages": []}


def test_a_session_that_is_not_a_room_wave_is_refused_by_name(room_server):
    _wave(room_server, [{"mac": CLONER1, "port": 2, **M70Q}])
    anon = room_server["anon"]
    gone = anon.get(f"/api/v1/agent/drivers?mac={CLONER1}&session=ses_nope&port=2")
    assert gone.status_code == 404 and gone.json()["code"] == "no_round"
    half = anon.get(f"/api/v1/agent/drivers?mac={CLONER1}&port=2")
    assert half.status_code == 400 and half.json()["code"] == "bad_query"


# --- התוצאה לכל מגירה בדיווח הסופי ------------------------------------------


def test_the_per_drawer_outcome_is_kept_shown_and_journaled_once(room_server):
    _, wave = _wave(room_server, [{"mac": CLONER1, "port": 2, **M70Q}])
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])            # מצטרף לגל
    body = {"session_id": wave, "mac": CLONER1, "state": "done", "targets": [
        {"dev": "sda", "bytes_written": 9, "bytes_total": 9, "state": "done"},
        {"dev": "sdb", "bytes_written": 9, "bytes_total": 9, "state": "done",
         "drivers": {"state": "failed", "packages": [],
                     "error": "the server has no driver package for the chosen model"}},
    ]}
    assert anon.post("/api/v1/agent/progress", json=body).json() == {"ok": True}
    machine = next(m for m in room(room_server["deploy"])["machines"] if m["mac"] == CLONER1)
    by_dev = {d["dev"]: d["drivers"] for d in machine["drawer_list"]}
    assert by_dev["sda"] is None, "חסר = השלב לא רץ למגירה הזו, לא no_match"
    assert by_dev["sdb"]["state"] == "failed" and "chosen model" in by_dev["sdb"]["error"]
    failed = [e for e in room_server["admin"].get("/api/console/journal").json()
              if e["event"] == "drivers_failed"]
    assert len(failed) == 1 and "sdb" in failed[0]["text"]


def test_a_malformed_per_drawer_outcome_is_dropped(room_server):
    _, wave = _wave(room_server, [{"mac": CLONER1, "port": 2, **M70Q}])
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    body = {"session_id": wave, "mac": CLONER1, "state": "writing", "targets": [
        {"dev": "sdb", "bytes_written": 1, "bytes_total": 9, "state": "writing",
         "drivers": {"state": "installed", "packages": []}}]}
    assert anon.post("/api/v1/agent/progress", json=body).json() == {"ok": True}
    row = room_server["ctx"].conn.execute(
        "SELECT targets_json FROM session_members WHERE mac = ?", (CLONER1,)).fetchone()
    assert "drivers" not in json.loads(row["targets_json"])[0]
