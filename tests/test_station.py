"""פתיחת סבב מהתחנה + Wake-on-LAN — זרימה 13.3, הנקודה השנייה.

השליחה של חבילות הקסם מוזרקת, ולכן הבדיקות תופסות כל בייט שהיה יוצא
לרשת — כולל למי הוא לא יצא (המכונה שפתחה כבר דולקת).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.db import set_setting
from server.wol import magic_packet

from conftest import Clock, hello_body, setup_classroom, write_image
from server.tasks import TOKEN_HEADER
from test_capture import task_token
from conftest import MANIFEST_256, MANIFEST_500


# --- חבילת הקסם --------------------------------------------------------------


def test_the_magic_packet_is_shaped_right():
    packet = magic_packet("b4:2e:99:07:1a:c4")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("b42e99071ac4")
    assert packet[6:] == bytes.fromhex("b42e99071ac4") * 16


def test_a_bad_mac_raises_instead_of_waking_nothing():
    with pytest.raises(ValueError):
        magic_packet("not-a-mac")


# --- שרת עם WoL נתפס ---------------------------------------------------------


@pytest.fixture()
def station(tmp_path, images_root, clock):
    from fastapi.testclient import TestClient

    from server import users
    from server.app import create_app

    woken: list[bytes] = []
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, wol_send=woken.append)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy = TestClient(app)
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
            "anon": TestClient(app), "clock": clock, "woken": woken}


def open_body(mac, **overrides):
    body = {"username": "labtech", "password": "deploy-pass-1", "mac": mac,
            "group_id": "grp_LAB1", "image_id": "img_7f3a91"}
    body.update(overrides)
    return body


# --- רשימת הכיתות ------------------------------------------------------------


def test_the_station_gets_the_class_list(station):
    setup_classroom(station)
    station["admin"].post("/api/console/groups",
                          json={"id": "grp_CLONE", "label": "שיכפול", "role": "cloner"})
    classes = station["anon"].get("/api/v1/agent/groups").json()
    # רק כיתות — חדר השיכפולים אינו יעד לסבב מהתחנה.
    assert [c["id"] for c in classes] == ["grp_LAB1"]
    assert classes[0]["label"] == "כיתה LAB1"
    assert classes[0]["machines"] == 2


# --- פתיחה מהתחנה ------------------------------------------------------------


def test_opening_a_round_wakes_everyone_except_the_opener(station):
    ids = setup_classroom(station)
    response = station["anon"].post("/api/v1/agent/sessions",
                                    json=open_body(ids["mac1"]))
    assert response.status_code == 200
    opened = response.json()

    # ברירות המחדל מהשרת: קידומת ממזהה הקבוצה, מספר מטבלת המכונות.
    assert opened["prefix"] == "LAB1"
    assert opened["expected_clients"] == 2

    # ה-WoL: מכונה אחת הוערה (השנייה בכיתה), והפותחת לא.
    assert station["woken"] == [magic_packet(ids["mac2"])]

    # והפותחת מצטרפת דרך ה-hello הרגיל, כמו כל מכונה.
    answer = station["anon"].post("/api/v1/agent/hello",
                                  json=hello_body(ids["mac1"])).json()
    assert answer["session"]["id"] == opened["id"]
    assert answer["session"]["state"] == "open"

    journal = [r["event"] for r in station["admin"].get("/api/console/journal").json()]
    assert "wol_sent" in journal


def test_the_station_gets_the_machine_list_by_name(station):
    ids = setup_classroom(station)
    rows = station["anon"].get("/api/v1/agent/groups/grp_LAB1/machines").json()
    assert rows == [{"mac": ids["mac1"], "name": "05"},
                    {"mac": ids["mac2"], "name": "06"}]
    bad = station["anon"].get("/api/v1/agent/groups/grp_NOPE/machines")
    assert bad.status_code == 400


def test_a_round_for_chosen_machines_wakes_and_admits_only_them(station):
    """בחירת מחשבים: רק הנבחרים מוערים ומצטרפים; השאר — דיסק מקומי."""
    ids = setup_classroom(station)
    response = station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body(ids["mac1"], macs=[ids["mac1"]]),
    )
    assert response.status_code == 200
    assert response.json()["expected_clients"] == 1

    # הנבחר היחיד הוא גם הפותח — אף חבילת WoL לא יוצאת.
    assert station["woken"] == []

    joined = station["anon"].post("/api/v1/agent/hello",
                                  json=hello_body(ids["mac1"])).json()
    assert joined["session"] is not None

    # המכונה שלא נבחרה לא הוזמנה: אין סבב בתשובה — דיסק מקומי.
    skipped = station["anon"].post("/api/v1/agent/hello",
                                   json=hello_body(ids["mac2"])).json()
    assert skipped["session"] is None and skipped["task"] is None


def test_a_bad_machine_selection_is_refused(station):
    ids = setup_classroom(station)
    unregistered = station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body(ids["mac1"], macs=["aa:aa:aa:aa:aa:aa"]),
    )
    assert unregistered.status_code == 409

    malformed = station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body(ids["mac1"], macs=["not-a-mac"]),
    )
    assert malformed.status_code == 400

    empty = station["anon"].post(
        "/api/v1/agent/sessions", json=open_body(ids["mac1"], macs=[]),
    )
    assert empty.status_code == 400


def test_a_console_opened_round_wakes_the_class_too(station):
    ids = setup_classroom(station)
    station["deploy"].post("/api/console/sessions",
                           json={"group_id": ids["group"], "image_id": "img_7f3a91",
                                 "prefix": "LAB1", "expected_clients": 2})
    # מהקונסולה אין "מכונה פותחת" — כולן מוערות.
    assert sorted(station["woken"]) == sorted(
        [magic_packet(ids["mac1"]), magic_packet(ids["mac2"])])


def test_the_build_machine_opens_a_class_round_for_chosen_machines(station):
    """הפצה לכיתות מהקיוסק (Issue #9 המשך): אותו endpoint של הקונסולה,
    עם בחירת מחשבים וברירות מחדל — קידומת מהמזהה, צפי מהבחירה."""
    ids = setup_classroom(station)
    r = station["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91",
        "macs": [ids["mac2"]],
    })
    assert r.status_code == 200

    # רק הנבחר הוער, הצפי נגזר מהבחירה, והקידומת מהמזהה.
    assert station["woken"] == [magic_packet(ids["mac2"])]
    ctx = station["ctx"]
    session = ctx.conn.execute("SELECT * FROM sessions").fetchone()
    assert session["expected_clients"] == 1 and session["prefix"] == "LAB1"


# --- #880: v1 מהדורת שיכפול — סבב כיתה מהסוכן מסורב כשהמתג כבוי -----------


def test_a_class_round_from_the_agent_is_refused_while_the_switch_is_off(station):
    """‏`setup_classroom` מדליק את המתג (הבדיקות של זרימה 13.3 הן v2);
    כאן מכבים אותו חזרה — ברירת המחדל של v1 — ופתיחה מהסוכן נדחית
    ב-409 עם קוד וסיבה, נרשמת ביומן בעברית, ואינה מעירה איש. **בקרה
    שלילית:** על main הסבב נפתח (200) והכיתה מוערת — אומת 16/09."""
    ids = setup_classroom(station)
    # ישירות ב-DB ולא דרך ה-API: כך הבקרה השלילית נופלת על **הפתיחה**
    # (200 במקום 409) ולא על "הגדרה לא מוכרת" בשלב ההכנה.
    set_setting(station["ctx"].conn, "class_deploy_enabled", "false")

    response = station["anon"].post("/api/v1/agent/sessions",
                                    json=open_body(ids["mac1"]))

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "class_deploy_disabled"
    assert response.json()["error"] == "הפצה לכיתות כבויה (v1 מהדורת שיכפול)"
    assert station["woken"] == []
    assert station["ctx"].conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0
    rows = station["admin"].get("/api/console/journal").json()
    refused = next(r for r in rows if r["event"] == "class_deploy_refused")
    assert refused["label"] == "פתיחת סבב כיתה נדחתה — הפצה לכיתות כבויה"
    assert "labtech" in refused["text"]


def test_the_switch_off_refuses_before_the_password_is_checked(station):
    """‏#1282: עד כאן המתג נבדק **אחרי** הסיסמה — סיסמה שגויה קיבלה 401
    ונכונה 409. ב-v1 המתג כבוי תמיד, ולכן כל `POST /sessions` היה בודק
    סיסמאות בלי שום נתיב פתיחה מאחוריו: ‏409 מול 401 גילה מה נכון. עכשיו
    הסירוב קודם לסיסמה — שתי הסיסמאות מקבלות אותה תשובה, ואין ניסיון
    שנספר."""
    ids = setup_classroom(station)
    set_setting(station["ctx"].conn, "class_deploy_enabled", "false")
    wrong = station["anon"].post(
        "/api/v1/agent/sessions", json=open_body(ids["mac1"], password="wrong"))
    right = station["anon"].post("/api/v1/agent/sessions", json=open_body(ids["mac1"]))
    assert wrong.status_code == right.status_code == 409
    assert wrong.json() == right.json()
    assert station["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM login_attempts").fetchone()["n"] == 0


# --- #1282: אותו שער סיסמה כמו כל כניסה אחרת ----------------------------------


def test_round_open_passwords_go_through_the_login_guard(station, monkeypatch):
    """‏#1282: פתיחת סבב מהתחנה בדקה סיסמה בלי `login_guard` — בלי השהיה
    ובלי נעילה, בניגוד ל-`/agent/login` ולכניסה לקונסולה. אחרי 5 כשלונות
    גם הסיסמה הנכונה ממתינה."""
    from server import login_guard

    monkeypatch.setattr(login_guard, "now", lambda: 1_000_000.0)
    ids = setup_classroom(station)
    for _ in range(login_guard.DELAY_AFTER):
        bad = station["anon"].post(
            "/api/v1/agent/sessions", json=open_body(ids["mac1"], password="wrong"))
        assert bad.status_code == 401
    response = station["anon"].post("/api/v1/agent/sessions", json=open_body(ids["mac1"]))
    assert response.status_code == 429, response.text
    assert response.json()["code"] == "login_delayed"
    assert station["ctx"].conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0


def test_a_round_open_by_a_user_who_must_change_password_is_refused(station):
    """‏#1282: הקיוסק ו-`/agent/login` מסרבים למי שחייב להחליף סיסמה; פתיחת
    סבב קיבלה אותו — ובהתקנה טרייה זה admin/admin."""
    ids = setup_classroom(station)
    conn = station["ctx"].conn
    conn.execute("UPDATE users SET must_change_password = 1 WHERE username = 'labtech'")
    conn.commit()
    response = station["anon"].post("/api/v1/agent/sessions", json=open_body(ids["mac1"]))
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "password_change_required"
    assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0


# --- מי רשאי לפתוח סבב, ולא רק מי שהסיסמה שלו נכונה (#94) --------------------


def add_user_with_role(station, username: str, password: str, role: str) -> None:
    """מכניס משתמש בתפקיד שאינו admin/deploy.

    ‏`users.create` דוחה תפקיד כזה, וגם ה-CHECK בסכימה — ולכן זה עובר
    דרך `ignore_check_constraints`: הבדיקה מדמה **סכימה של מחר**, את
    היום שבו יתווסף תפקיד שלישי, וזו בדיוק השאלה שהקוד לא שאל.
    """
    from server import users
    conn = station["ctx"].conn
    conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        conn.execute(
            "INSERT INTO users (username, pw_hash, role, created_at)"
            " VALUES (?, ?, ?, '2026-08-29T00:00:00+03:00')",
            (username, users._hash(password, "aabbccddeeff00112233445566778899"), role),
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA ignore_check_constraints = OFF")


def test_a_role_that_is_not_on_the_list_cannot_open_a_round(station):
    """הבקרה השלילית של #94.

    לפני התיקון `role` נקרא מ-`users.verify` ולא נבדק, ולכן כל תפקיד
    שהסיסמה שלו נכונה פתח סבב שמוחק דיסקים בכיתה שלמה.
    """
    ids = setup_classroom(station)
    add_user_with_role(station, "auditor", "audit-pass-12", "auditor")

    r = station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body(ids["mac1"], username="auditor", password="audit-pass-12"),
    )
    assert r.status_code == 403
    assert r.json()["code"] == "role_not_allowed"
    assert station["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM sessions"
    ).fetchone()["n"] == 0
    assert station["woken"] == []


def test_the_refusal_reaches_the_journal_in_hebrew(station):
    """סירוב שקט הוא כשל אחר של אותו סוג."""
    ids = setup_classroom(station)
    add_user_with_role(station, "auditor", "audit-pass-12", "auditor")
    station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body(ids["mac1"], username="auditor", password="audit-pass-12"),
    )
    rows = station["admin"].get("/api/console/journal").json()
    refused = next(r for r in rows if r["event"] == "agent_role_refused")
    assert refused["label"] == "פתיחת סבב נדחתה — התפקיד אינו רשאי"
    assert "auditor" in refused["text"]


@pytest.mark.parametrize("who", [("labtech", "deploy-pass-1"), ("noc", "admin-pass-123")])
def test_both_real_roles_still_open_rounds(station, who):
    """הצד החיובי — התיקון לא צמצם את מי שאמור לפתוח."""
    ids = setup_classroom(station)
    r = station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body(ids["mac1"], username=who[0], password=who[1]),
    )
    assert r.status_code == 200


def test_a_wrong_password_is_still_a_401_and_not_a_403(station):
    """שני סירובים שונים נשארים שני קודים שונים: "הסיסמה שגויה" איננו
    "התפקיד אינו רשאי", וטכנאי שיקבל את השני יחפש את התקלה הנכונה."""
    ids = setup_classroom(station)
    r = station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body(ids["mac1"], password="wrong-password"),
    )
    assert r.status_code == 401
    assert r.json()["code"] == "bad_login"

    # מי שלא נבחר — דיסק מקומי.
    skipped = station["anon"].post("/api/v1/agent/hello",
                                   json=hello_body(ids["mac1"])).json()
    assert skipped["session"] is None


def test_wrong_credentials_do_not_open_or_wake(station):
    ids = setup_classroom(station)
    response = station["anon"].post(
        "/api/v1/agent/sessions", json=open_body(ids["mac1"], password="wrong"))
    assert response.status_code == 401
    assert station["woken"] == []
    assert station["admin"].get("/api/console/overview").json()["session"] is None


def test_only_a_classroom_can_be_a_target(station):
    ids = setup_classroom(station)
    station["admin"].post("/api/console/groups",
                          json={"id": "grp_CLONE", "label": "שיכפול", "role": "cloner"})
    response = station["anon"].post(
        "/api/v1/agent/sessions", json=open_body(ids["mac1"], group_id="grp_CLONE"))
    assert response.status_code == 400
    assert response.json()["code"] == "bad_group"


def test_only_a_classroom_can_be_a_target_from_the_console(station):
    """אותה בדיקה גם במסלול הקונסולה (#534) — זה המסלול שהמסך הגרפי
    קורא לו בפועל. סבב כיתה על מחשב הבנייה או על חדר השיכפולים → 400."""
    setup_classroom(station)
    station["admin"].post("/api/console/groups",
                          json={"id": "grp_BUILD", "label": "בנייה", "role": "build"})
    station["admin"].post("/api/console/groups",
                          json={"id": "grp_CLONE", "label": "שיכפול", "role": "cloner"})
    for gid in ("grp_BUILD", "grp_CLONE"):
        response = station["deploy"].post(
            "/api/console/sessions",
            json={"group_id": gid, "image_id": "img_7f3a91"})
        assert response.status_code == 400, gid
        assert "כיתה" in response.json()["detail"], gid


def test_an_empty_class_cannot_be_opened(station):
    setup_classroom(station)
    station["admin"].post("/api/console/groups",
                          json={"id": "grp_EMPTY", "label": "ריקה", "role": "classroom"})
    response = station["anon"].post(
        "/api/v1/agent/sessions",
        json=open_body("b4:2e:99:07:1a:c4", group_id="grp_EMPTY"))
    assert response.status_code == 400
    assert response.json()["code"] == "empty_group"


def test_the_one_active_round_rule_holds_from_the_station_too(station):
    ids = setup_classroom(station)
    assert station["anon"].post("/api/v1/agent/sessions",
                                json=open_body(ids["mac1"])).status_code == 200
    second = station["anon"].post("/api/v1/agent/sessions",
                                  json=open_body(ids["mac2"]))
    assert second.status_code == 409
    assert second.json()["code"] == "session_conflict"


# --- מסך מחשב הבנייה ---------------------------------------------------------


def test_the_station_state_feeds_the_build_screen(station):
    """מה שהעמוד הגרפי מציג: זהות, הדיסקים מה-hello האחרון, והמשימה."""
    station["admin"].post("/api/console/machines",
                          json={"mac": "aa:bb:cc:00:00:10", "name": "BUILD-PC",
                                "group_id": "grp_BUILD"})
    # ה-hello מדווח דיסקים — הם מה שממלא את "מה מותקן עכשיו".
    station["anon"].post("/api/v1/agent/hello",
                         json=hello_body("aa:bb:cc:00:00:10"))

    state = station["anon"].get(
        "/api/v1/agent/state?mac=AA-BB-CC-00-00-10").json()
    assert state["known"] is True
    assert state["role"] == "build"
    assert state["name"] == "BUILD-PC"
    assert state["disks"][0]["dev"] == "sda"
    assert state["disks"][0]["model"] == "Test SSD"
    assert state["task"] is None

    # אחרי הזמנת קליטה — המשימה מופיעה, עם ההתקדמות.
    created = station["admin"].post(
        "/api/console/tasks/capture",
        json={"mac": "aa:bb:cc:00:00:10", "disk": "sda", "name": "Base",
              "folder": "Office"}).json()
    station["anon"].post("/api/v1/agent/progress", json={
        "task_id": created["id"], "mac": "aa:bb:cc:00:00:10",
        "state": "capturing",
        "targets": [{"dev": "sda", "bytes_written": 512, "bytes_total": 2048,
                     "state": "capturing"}]},
        headers={TOKEN_HEADER: task_token(station, created["id"])})
    task = station["anon"].get(
        "/api/v1/agent/state?mac=aa:bb:cc:00:00:10").json()["task"]
    assert task["state"] == "running"
    assert task["bytes_written"] == 512


def test_reported_empty_disks_clear_the_build_screen(station):
    mac = "aa:bb:cc:00:00:10"
    assert station["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "BUILD-PC", "group_id": "grp_BUILD",
    }).status_code == 200
    disks = [
        {"dev": dev, "size_bytes": 256060514304, "model": f"Disk {dev}",
         "serial": f"S{index}", "removable": False, "scheme": "gpt",
         "has_data": True}
        for index, dev in enumerate(("sda", "sdb", "sdc"), start=1)
    ]
    body = hello_body(mac)
    body["disks"] = disks
    assert station["anon"].post(
        "/api/v1/agent/hello", json=body
    ).status_code == 200

    without_disks = hello_body(mac)
    without_disks.pop("disks")
    assert station["anon"].post(
        "/api/v1/agent/hello", json=without_disks
    ).status_code == 200
    stored = station["ctx"].conn.execute(
        "SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()["disks_json"]
    assert stored == json.dumps(disks)

    body["disks"] = []
    assert station["anon"].post(
        "/api/v1/agent/hello", json=body
    ).status_code == 200
    stored = station["ctx"].conn.execute(
        "SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()["disks_json"]
    assert stored == "[]"
    state = station["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
    assert state["disks"] == []


def test_station_state_for_a_stranger_is_polite(station):
    state = station["anon"].get("/api/v1/agent/state?mac=ff:ff:ff:ff:ff:01").json()
    assert state["known"] is False and state["disks"] == []
    assert station["anon"].get("/api/v1/agent/state").status_code == 400


def test_the_station_web_page_is_gone(station):
    """#1247: מסך התחנה בדפדפן הוסר; הגואי הנייטיב הוא המסך היחיד."""
    assert station["anon"].get("/console/station/").status_code == 404


def test_a_wake_failure_does_not_stop_the_rest(tmp_path, images_root, clock):
    """מוטב 29 ערות ואחת לא — כשל שליחה אחד לא מפיל את הפתיחה."""
    from server import users
    from server.app import create_app
    from fastapi.testclient import TestClient

    calls = {"n": 0}

    def flaky(packet):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("network is unhappy")

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, wol_send=flaky)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    server = {"admin": client, "deploy": client, "anon": TestClient(app)}
    setup_classroom(server)

    # ‏#997: `mac` חובה (זהות הפותח). פותח שאינו בכיתה — שתי המכונות מוערות.
    response = server["anon"].post("/api/v1/agent/sessions", json={
        "username": "noc", "password": "admin-pass-123", "mac": "aa:bb:cc:00:00:10",
        "group_id": "grp_LAB1", "image_id": "img_7f3a91"})
    assert response.status_code == 200
    assert calls["n"] == 2                      # שתיהן נוסו למרות שהראשונה נפלה


# --- תצוגת הסבב הפעיל בלי כניסה (#34) ----------------------------------------


def test_the_live_session_view_needs_no_login(station):
    """הקיוסק לא יכול להישען על cookie שמתפוגג: ההתנתקות האוטומטית של
    הקונסולה מחקה אותו באמצע סבב, והמסך קפא על מצב ישן (#34)."""
    ids = setup_classroom(station)
    assert station["anon"].get(
        "/api/v1/agent/sessions/active").json() == {"session": None}

    opened = station["anon"].post("/api/v1/agent/sessions",
                                  json=open_body(ids["mac1"])).json()

    view = station["anon"].get("/api/v1/agent/sessions/active").json()["session"]
    assert view["id"] == opened["id"]
    assert view["state"] == "open"
    assert view["group_role"] == "classroom"
    assert view["prefix"] == "LAB1"
    assert view["members"] == []          # עוד לא הצטרף איש — אבל השדה קיים


def test_the_live_session_view_carries_member_progress(station):
    ids = setup_classroom(station)
    opened = station["anon"].post("/api/v1/agent/sessions",
                                  json=open_body(ids["mac1"])).json()
    station["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    station["anon"].post("/api/v1/agent/progress", json={
        "session_id": opened["id"], "mac": ids["mac1"], "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 512, "bytes_total": 2048,
                     "state": "writing"}]})

    members = station["anon"].get(
        "/api/v1/agent/sessions/active").json()["session"]["members"]
    ours = [m for m in members if m["mac"] == ids["mac1"]]
    assert ours and ours[0]["bytes_written"] == 512
    assert ours[0]["bytes_total"] == 2048
