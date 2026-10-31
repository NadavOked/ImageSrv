"""ה-flows המלאים דרך HTTP — בדיוק מה שהסוכן והדפדפן יעשו.

הלקוח כאן מדבר את ממשק 2 מילה במילה (גוף hello מלא), ומאמת את ממשק 3
בתשובה. תפריט ה-GRUB נבדק דרך אותו שרת — לוודא שה-resolver המוזרק
באמת מחובר, ושתפריט לא מצרף אף אחד לסבב.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom


def hello(server, mac, **kwargs):
    response = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac, **kwargs))
    assert response.status_code == 200
    return response.json()


# --- hello -------------------------------------------------------------------


def test_hello_carries_registered_name_and_server_identity(server):
    ids = setup_classroom(server)
    answer = hello(server, ids["mac1"])
    assert answer["name"]
    assert answer["server_name"]
    assert "server_version" in answer

    unknown = hello(server, "de:ad:be:ef:00:01")
    assert unknown["known"] is False and unknown["name"] is None


def test_unknown_mac_gets_nothing_and_raises_an_alert(server):
    answer = hello(server, "aa:aa:aa:aa:aa:aa")
    assert answer.pop("name") is None
    assert answer.pop("server_name")
    assert "server_version" in answer
    answer.pop("server_version")
    # #1447: the zone (null where the server has no zoneinfo) and the clock
    assert "tz" in answer
    answer.pop("tz")
    assert isinstance(answer.pop("now"), int)
    assert answer == {
        "schema": 1, "known": False, "role": "unknown", "group": None,
        "task": None, "session": None, "allowed_images": [],
        "ui": {"language": "he", "require_login": True},
        "classrooms": False,
        "tools": False,
    }
    events = [row["event"] for row in server["admin"].get("/api/console/journal").json()]
    assert "unknown_mac" in events


def test_known_machine_without_a_session_boots_locally(server):
    ids = setup_classroom(server)
    answer = hello(server, ids["mac1"])
    assert answer["known"] is True
    assert answer["role"] == "classroom"
    assert answer["group"] == {"id": "grp_LAB1", "label": "כיתה LAB1", "suffix": "05"}
    assert answer["task"] is None and answer["session"] is None
    assert "drawer_count" not in answer


def test_cloner_hello_carries_its_configured_drawer_count(server):
    admin = server["admin"]
    # the cloner group is a fixed group that already exists (409 on create)
    assert admin.post("/api/console/machines", json={
        "mac": "b4:2e:99:07:1a:c1", "name": "Cloner1", "group_id": "grp_CLONERS",
    }).status_code == 200
    assert admin.put("/api/console/machines/b4:2e:99:07:1a:c1", json={
        "drawer_count": 2,
    }).status_code == 200

    assert hello(server, "b4:2e:99:07:1a:c1")["drawer_count"] == 2


def test_cloner_registration_defaults_drawers_from_last_hello(server):
    mac = "b4:2e:99:07:1a:c2"
    body = hello_body(mac)
    first = {**body["disks"][0], "port": 1}
    body["disks"] = [first, {**first, "dev": "sdb", "serial": "S2", "port": 2}]
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200

    response = server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "Cloner2", "group_id": "grp_CLONERS",
    })
    assert response.status_code == 200
    assert hello(server, mac)["drawer_count"] == 2


def test_explicit_cloner_drawer_count_wins_over_last_hello(server):
    mac = "b4:2e:99:07:1a:c3"
    body = hello_body(mac)
    first = {**body["disks"][0], "port": 1}
    body["disks"] = [first, {**first, "dev": "sdb", "serial": "S2", "port": 2}]
    server["anon"].post("/api/v1/agent/hello", json=body)

    response = server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": "Cloner3", "group_id": "grp_CLONERS", "drawer_count": 4,
    })
    assert response.status_code == 200
    assert hello(server, mac)["drawer_count"] == 4


def test_hello_identifies_a_machine_by_any_reported_mac(server):
    """#524: מכונה רשומה תחת X שעולה על כרטיס Y עדיין מזוהה.

    ה-MAC הראשי הוא כרטיס האתחול, והוא אינו הרשום; `all_macs` מכיל
    את הרשום. לפני התיקון התשובה הייתה `known: false`.
    """
    ids = setup_classroom(server)
    registered = ids["mac1"]
    boot = "de:ad:be:ef:00:01"
    body = hello_body(boot)
    body["all_macs"] = [boot, registered]
    answer = server["anon"].post("/api/v1/agent/hello", json=body).json()
    assert answer["known"] is True


def test_hello_with_only_unregistered_macs_stays_unknown(server):
    """בקרה שלילית: `all_macs` של זרים אינו הופך מכונה למוכרת."""
    setup_classroom(server)
    body = hello_body("de:ad:be:ef:00:01")
    body["all_macs"] = ["de:ad:be:ef:00:01", "de:ad:be:ef:00:02"]
    answer = server["anon"].post("/api/v1/agent/hello", json=body).json()
    assert answer["known"] is False


def test_allowed_images_respect_the_reported_disk(server):
    # #1863: by the real size, not the family -- both fixtures lay out ~255.4 GB
    # (MANIFEST_500 is "family 500" only), so both fit a 256 GB disk; 240 GB holds neither.
    setup_classroom(server)
    small = hello(server, "b4:2e:99:07:1a:c4", disk_bytes=240057409536)
    big = hello(server, "b4:2e:99:07:1a:c4", disk_bytes=256060514304)
    assert small["allowed_images"] == []
    assert big["allowed_images"] == ["img_2c8e04", "img_7f3a91"]


def test_malformed_hello_is_an_orderly_error(server):
    response = server["anon"].post("/api/v1/agent/hello", json={"schema": 1})
    assert response.status_code == 400
    assert response.json()["code"] == "bad_mac"


# --- מחזור סבב שלם דרך ה-API -------------------------------------------------


def open_session(server, expected=2):
    ids = setup_classroom(server, expected)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": expected},
    )
    assert response.status_code == 200
    ids["session"] = response.json()["id"]
    return ids


def test_the_full_classroom_round(server):
    ids = open_session(server, expected=2)

    # מכונה ראשונה מצטרפת דרך hello — בלי endpoint נפרד.
    first = hello(server, ids["mac1"])
    assert first["session"]["state"] == "open"
    assert first["session"]["joined"] == 1
    assert first["ui"]["require_login"] is False   # סבב פתוח = בלי סיסמאות

    # השנייה משלימה את המספר המוצהר; ה-hello הבא כבר רואה running.
    hello(server, ids["mac2"])
    running = hello(server, ids["mac1"])
    assert running["session"]["state"] == "running"
    assert running["session"]["image_id"] == "img_7f3a91"
    assert running["session"]["starts_in_seconds"] == 0

    # דיווח סיום — והסבב לא מוצע לאותה מכונה שוב (אין לולאת שחזור).
    report = {
        "session_id": ids["session"], "mac": ids["mac1"], "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 57982058496,
                     "bytes_total": 57982058496, "state": "done"}],
    }
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    after = hello(server, ids["mac1"])
    assert after["session"] is None
    # אבל השנייה עדיין בפנים.
    assert hello(server, ids["mac2"])["session"]["state"] == "running"


def test_late_machine_waits_for_the_next_round(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])                       # מצטרף ומתחיל (expected=1)
    late = hello(server, ids["mac2"])
    assert late["session"] is None                   # מאחר → דיסק מקומי


def test_hello_cannot_join_a_wave_that_started_after_its_read(server, monkeypatch):
    """בקרה שלילית #1126: בקוד שלפני התיקון הקריאה ואז ההכנסה יוצרות
    חבר, אף שה-hook מעביר את הסבב ל-``running`` בדיוק ביניהן."""
    from server.rounds.sessions import SessionStore             # noqa: PLC0415

    ids = open_session(server, expected=2)
    original = SessionStore.record_hello

    def start_between_read_and_insert(self, session, mac):
        self.conn.execute(
            "UPDATE sessions SET state = 'running' WHERE id = ?", (session["id"],)
        )
        self.conn.commit()
        return original(self, session, mac)

    monkeypatch.setattr(SessionStore, "record_hello", start_between_read_and_insert)
    answer = hello(server, ids["mac1"])
    member = server["ctx"].conn.execute(
        "SELECT 1 FROM session_members WHERE session_id = ? AND mac = ?",
        (ids["session"], ids["mac1"]),
    ).fetchone()
    assert answer["session"] is None
    assert member is None


def test_done_report_is_idempotent_without_a_second_journal_row(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])
    hello(server, ids["mac1"])
    payload = {
        "session_id": ids["session"], "mac": ids["mac1"], "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 100,
                     "bytes_total": 100, "state": "done"}],
    }
    assert server["anon"].post("/api/v1/agent/progress", json=payload).json()["ok"]
    conn = server["ctx"].conn
    before = conn.execute(
        "SELECT state, done, targets_json, updated_at FROM session_members"
        " WHERE session_id = ? AND mac = ?", (ids["session"], ids["mac1"]),
    ).fetchone()
    journal_before = conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'client_done'"
    ).fetchone()["n"]

    duplicate = dict(payload)
    duplicate["targets"] = []
    assert server["anon"].post(
        "/api/v1/agent/progress", json=duplicate
    ).json()["ok"]
    after = conn.execute(
        "SELECT state, done, targets_json, updated_at FROM session_members"
        " WHERE session_id = ? AND mac = ?", (ids["session"], ids["mac1"]),
    ).fetchone()
    journal_after = conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'client_done'"
    ).fetchone()["n"]
    assert dict(after) == dict(before)
    assert journal_after == journal_before


def test_done_then_immediate_reboot_does_not_receive_the_same_round(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])
    hello(server, ids["mac1"])
    report = {
        "session_id": ids["session"], "mac": ids["mac1"], "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 100,
                     "bytes_total": 100, "state": "done"}],
    }
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    reboot = hello(server, ids["mac1"])
    assert reboot["session"] is None and reboot["task"] is None


def test_failed_target_reaches_the_journal(server):
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    report = {
        "session_id": ids["session"], "mac": ids["mac1"], "state": "failed",
        "targets": [{"dev": "sda", "bytes_written": 4194304,
                     "bytes_total": 57982058496, "state": "failed",
                     "error": "I/O error at sector 8419328"}],
    }
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    journal = server["admin"].get("/api/console/journal").json()
    failures = [row for row in journal if row["event"] == "client_failed"]
    assert failures and "I/O error" in failures[0]["text"]
    # התרגום: תווית בעברית, וה-MAC נפתר לשם + קבוצה.
    assert failures[0]["label"] == "כתיבה נכשלה במחשב"
    assert "05" in failures[0]["text"] and "LAB1" in failures[0]["text"]

    members = server["admin"].get("/api/console/overview").json()["session"]["members"]
    assert members[0]["state"] == "failed"


def test_the_console_shows_names_not_identifiers(server):
    """מי שמסתכל על הקונסולה מחפש "LAB1-05", לא b4:2e:99:07:1a:c4."""
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    view = server["admin"].get("/api/console/overview").json()["session"]

    assert view["group_label"] == "כיתה LAB1"
    assert view["image_name"] == "Office 2024 Standard"
    assert view["single"] is False

    member = view["members"][0]
    assert member["name"] == "05"
    assert member["hostname"] == "LAB1-05"       # השם שייכתב למחשב בסיום
    assert member["mac"] == ids["mac1"]          # ה-MAC נשאר, לטכנאי


def test_a_one_machine_round_is_marked_as_a_single_station(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])
    view = server["admin"].get("/api/console/overview").json()["session"]
    assert view["single"] is True


def test_an_unregistered_member_falls_back_to_its_mac(server):
    """מכונה שנמחקה מהטבלה באמצע סבב לא מפילה את התצוגה."""
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    server["admin"].delete(f"/api/console/machines/{ids['mac1']}")
    member = server["admin"].get("/api/console/overview").json()["session"]["members"][0]
    assert member["name"] is None and member["hostname"] is None
    assert member["mac"] == ids["mac1"]


def test_edit_machine_rejects_an_unknown_field(server):
    """#406: PUT עם שדה לא מוכר (suffix במקום name) חייב 400 — ולא
    {"ok": true} קבוע שלא שינה כלום. עיקרון 5: הצלחה לפי ראיה חיובית."""
    admin = server["admin"]
    ids = setup_classroom(server)
    before = {m["mac"]: m["suffix"]
              for m in admin.get("/api/console/machines").json()}
    resp = admin.put(
        f"/api/console/machines/{ids['mac1']}", json={"suffix": "HP1"}
    )
    assert resp.status_code == 400, resp.text
    after = {m["mac"]: m["suffix"]
             for m in admin.get("/api/console/machines").json()}
    assert after == before


def _disks_of(server, mac):
    rows = server["admin"].get("/api/console/machines").json()
    return next(m for m in rows if m["mac"] == mac)["disks"]


def test_console_machine_list_distinguishes_never_reported_from_empty(server):
    """#417: null (מעולם לא דיווחה) ≠ [] (דיווחה אפס כוננים) — שני
    ממצאים שונים, ומסך המכונה בקונסולה חייב להבחין ביניהם ולא לקפל."""
    ids = setup_classroom(server)
    mac1, mac2 = ids["mac1"], ids["mac2"]

    # לפני כל hello — המכונה מעולם לא דיברה עם השרת.
    assert _disks_of(server, mac1) is None

    # דיווחה בפועל אפס כוננים.
    body = hello_body(mac2)
    body["disks"] = []
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    assert _disks_of(server, mac2) == []

    # ומכונה שדיווחה כונן אמיתי מקבלת את הרשימה.
    hello(server, mac1)
    disks = _disks_of(server, mac1)
    assert disks and disks[0]["dev"] == "sda"


def test_malformed_disks_do_not_crash_and_do_not_record_empty_inventory(server):
    """בקרה שלילית מתוך הגדרת הגמור של #417: hello עם `disks` פגום
    (לא רשימה) לא מפיל את הבקשה, ואסור שהוא ירשום מלאי ריק בשקט —
    ‏`[]` הוא ממצא ("דיווחה אפס"), לא "לא ידענו מה היא דיווחה"."""
    ids = setup_classroom(server)
    mac = ids["mac1"]
    body = hello_body(mac)
    body["disks"] = "not-a-list"
    response = server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200
    assert _disks_of(server, mac) is None


def test_progress_from_a_nonmember_is_rejected(server):
    ids = open_session(server)
    report = {"session_id": ids["session"], "mac": "aa:aa:aa:aa:aa:aa",
              "state": "writing", "targets": []}
    response = server["anon"].post("/api/v1/agent/progress", json=report)
    assert response.status_code == 400
    assert response.json()["code"] == "not_member"


def test_manual_start_and_close(server):
    ids = open_session(server, expected=30)
    hello(server, ids["mac1"])
    assert server["deploy"].post(
        f"/api/console/sessions/{ids['session']}/start"
    ).status_code == 200
    assert hello(server, ids["mac1"])["session"]["state"] == "running"
    assert server["deploy"].post(
        f"/api/console/sessions/{ids['session']}/close",
        json={"confirm_name": "Office 2024 Standard"},
    ).status_code == 200
    assert hello(server, ids["mac1"])["session"] is None


def test_manual_start_with_no_members_is_refused_and_round_stays_open(server):
    """‏#843: "התחל" לפני שמישהו הצטרף אינו מתקבל בשקט.

    ‏`record_hello` מצרף רק לסבב `open`, ולכן סבב שהותחל עם 0 חברים
    הוא גל שאיש אינו יכול להצטרף אליו: ה-sender ממתין 180ש', רושם
    `send_failed`, והסבב נשאר `running` ריק. נמדד במעבדה 15/09
    (`room_e6951214`/`ses_58f96d6d`). הגבול הוא ה-API — 409, הסבב
    נשאר `open`, ו-hello שמגיע אחר כך עדיין מצטרף.
    """
    ids = open_session(server, expected=30)
    response = server["deploy"].post(
        f"/api/console/sessions/{ids['session']}/start"
    )
    assert response.status_code == 409, response.text
    assert "אין מכונות בסבב" in response.json()["detail"]
    answer = hello(server, ids["mac1"])
    assert answer["session"]["id"] == ids["session"]
    assert answer["session"]["state"] == "open"
    # עכשיו יש חבר — אותה לחיצה מתקבלת.
    assert server["deploy"].post(
        f"/api/console/sessions/{ids['session']}/start"
    ).status_code == 200
    assert hello(server, ids["mac1"])["session"]["state"] == "running"


# --- מניפסטים וקבצים ---------------------------------------------------------


def test_manifest_endpoint_hides_internals(server):
    manifest = server["anon"].get("/api/v1/images/img_7f3a91/manifest").json()
    assert manifest["id"] == "img_7f3a91"
    assert "_dir" not in manifest
    missing = server["anon"].get("/api/v1/images/img_none/manifest")
    assert missing.status_code == 404 and missing.json()["code"] == "no_image"


def test_partition_files_are_whitelisted(server):
    good = server["anon"].get("/api/v1/images/img_7f3a91/files/p1.esp.pcl.zst")
    assert good.status_code == 200
    assert good.content == b"compressed-partition-bytes"
    for bad_name in ("manifest.json", "..%2Fmanifest.json", "secret.txt"):
        assert server["anon"].get(
            f"/api/v1/images/img_7f3a91/files/{bad_name}"
        ).status_code == 404


# --- תפריט ה-GRUB מחובר לאותו מוח --------------------------------------------


def test_boot_menu_is_wired_to_the_resolver(server):
    ids = open_session(server)
    response = server["anon"].get(f"/boot/menu?mac={ids['mac1']}")
    assert response.status_code == 200
    assert "chain_local" in response.text
    response.text.encode("ascii")                    # הפלט חייב להישאר ASCII

    # תפריט שואל — הוא לא מצרף לסבב. ההצטרפות היא רק ב-hello.
    overview = server["admin"].get("/api/console/overview").json()
    assert overview["session"]["joined"] == 0


def test_extra_cmdline_reaches_the_boot_menu(tmp_path, images_root, clock):
    """תוספות מפעיל לשורת הקרנל (קונסולה טורית) עוברות כהגדרה — לא
    בעריכת קוד על השרת, שהולידה fork חי (#18).

    ‏`imagesrv.debug` יוצא מן הכלל: מעטפת הניפוי של הסוכן אינה מוגשת
    מ-PXE לעולם. עד #1266 רק מתג ה-SSH בקונסולה הכניס אותו; המתג יצא
    מהמוצר, והדגל נשאר מוסר גם כשהמפעיל מעביר אותו כאן."""
    from fastapi.testclient import TestClient

    from server.console import users
    from server.app import create_app

    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock,
        extra_cmdline=("console=ttyS0,115200", "imagesrv.debug=1"),
    )
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    setup_classroom({"admin": admin})

    text = TestClient(app).get("/boot/menu?mac=b4:2e:99:07:1a:c4").text
    assert "console=ttyS0,115200" in text
    assert "imagesrv.debug" not in text


def test_boot_menu_never_errors_on_garbage(server):
    for query in ("", "?mac=", "?mac=zz:zz", "?mac=%00"):
        response = server["anon"].get(f"/boot/menu{query}")
        assert response.status_code == 200
        assert "chain_local" in response.text


# --- הרשאות ------------------------------------------------------------------


def test_anonymous_gets_401_everywhere(server):
    for path in ("/api/console/overview", "/api/console/images", "/api/console/journal"):
        assert server["anon"].get(path).status_code == 401


def test_deploy_user_is_fenced_in(server):
    """סעיף 11 + ‏#1073: לבחור אימג' ולהפיץ — כן, **ממחשב הבנייה** (allowlist
    הקיוסק, שקודם לקונסולה ב-`create_app`). לנהל את המערכת — לא, וגם לא
    לראות אותה: `/overview` הוא קונסולה בלבד → 403 `deploy_no_console`."""
    deploy = server["deploy"]
    assert deploy.get("/api/console/images").status_code == 200
    assert deploy.get("/api/console/overview").status_code == 403
    assert deploy.get("/api/console/users").status_code == 403
    assert deploy.get("/api/console/journal").status_code == 403
    assert deploy.get("/api/console/settings").status_code == 403
    assert deploy.get("/api/console/machines.csv").status_code == 403
    assert deploy.post(
        "/api/console/groups", json={"id": "x", "label": "x", "role": "classroom"}
    ).status_code == 403
    assert deploy.post(
        "/api/console/machines/import", json={"group_id": "x", "text": ""}
    ).status_code == 403


def test_wrong_password_fails_and_is_journaled(server):
    response = server["anon"].post(
        "/api/console/login", json={"username": "noc", "password": "nope"}
    )
    assert response.status_code == 401
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert "login_failed" in events


def test_idle_timeout_reaches_every_signed_in_user(server):
    """זמן הניתוק מוחזר ב-`/me` ובתשובת הכניסה. (עד #1073 נבדק כאן על
    deploy; מאז אין לו `/me` — הקונסולה סגורה בפניו — ולכן על מנהל שני.)"""
    from fastapi.testclient import TestClient                  # noqa: PLC0415
    from server.console import users                                    # noqa: PLC0415
    users.create(server["ctx"].conn, "noc2", "admin-pass-456", "admin",
                 by="test", is_builtin=True, check_policy=False)
    second = TestClient(server["app"])
    assert second.post("/api/console/login",
                       json={"username": "noc2", "password": "admin-pass-456"}
                       ).json()["idle_seconds"] == 600
    assert second.get("/api/console/me").json()["idle_seconds"] == 600
    assert server["admin"].post(
        "/api/console/settings", json={"console_idle_seconds": "900"}
    ).status_code == 200
    assert second.get("/api/console/me").json()["idle_seconds"] == 900
    # וגם בתשובת הכניסה עצמה, כדי שהשעון יתחיל נכון מהרגע הראשון.
    fresh = server["anon"].post(
        "/api/console/login",
        json={"username": "noc2", "password": "admin-pass-456"},
    ).json()
    assert fresh["idle_seconds"] == 900


def test_agent_login_checks_the_console_users(server):
    """סעיף 15: ההרשאה יושבת בשרת — מסך התחנה מאמת מול אותם משתמשים."""
    ok = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "deploy-pass-1",
        "mac": "b4:2e:99:07:1a:c4"})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "role": "deploy"}

    bad = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "wrong", "mac": "b4:2e:99:07:1a:c4"})
    assert bad.status_code == 401
    assert bad.json()["code"] == "bad_login"

    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert "agent_login" in events and "agent_login_failed" in events


# --- #880: מתג "הפצה לכיתות ממחשב הבנייה" — v1 מהדורת שיכפול --------------


def register_build_machine(server, mac="b4:2e:99:07:1a:aa") -> str:
    """מחשב בנייה בקבוצה הקבועה `grp_BUILD` — היחיד שמקבל את התפריט."""
    assert server["admin"].post(
        "/api/console/machines",
        json={"mac": mac, "name": "BUILD-PC", "group_id": "grp_BUILD"},
    ).status_code == 200
    return mac


def test_class_deploy_is_off_by_default_and_the_console_can_switch_it(server):
    """ברירת המחדל של v1 היא כבוי — במכוון, גם על שרת שכבר פרוס. ‏GET
    מחזיר אותה, ‏POST (מחרוזת או JSON bool) הופך אותה. **בקרה שלילית:**
    על main ההגדרה אינה ב-`WRITE_SETTINGS` — ‏GET לא מחזיר אותה ו-POST
    נופל ב-400 "הגדרה לא מוכרת"."""
    admin = server["admin"]
    assert admin.get("/api/console/settings").json()["class_deploy_enabled"] == "false"
    assert admin.post("/api/console/settings",
                      json={"class_deploy_enabled": "true"}).status_code == 200
    assert admin.get("/api/console/settings").json()["class_deploy_enabled"] == "true"
    # ‏JSON bool אינו נשמר כ-"True" שנקרא ככבוי — עיקרון 5.
    assert admin.post("/api/console/settings",
                      json={"class_deploy_enabled": False}).status_code == 200
    assert admin.get("/api/console/settings").json()["class_deploy_enabled"] == "false"
    assert admin.post("/api/console/settings",
                      json={"class_deploy_enabled": True}).status_code == 200
    assert admin.get("/api/console/settings").json()["class_deploy_enabled"] == "true"
    rows = admin.get("/api/console/journal", params={"event": "setting_change"}).json()
    assert any(r["text"] == "הפצה לכיתות ממחשב הבנייה: פעיל" for r in rows), rows


def test_the_build_machine_hello_carries_the_class_deploy_switch(server):
    """‏hello למחשב הבנייה נושא `class_deploy_enabled` (bool) — הסוכן
    מסיר לפיו את "הפצה לכיתות" מהתפריט. מכונת כיתה אינה מקבלת את השדה
    (אין לה תפריט). **בקרה שלילית:** על main השדה אינו קיים."""
    mac = register_build_machine(server)
    assert hello(server, mac)["classrooms"] is False
    assert hello(server, mac)["class_deploy_enabled"] is False
    assert server["admin"].post("/api/console/settings",
                                json={"class_deploy_enabled": "true"}).status_code == 200
    assert hello(server, mac)["class_deploy_enabled"] is True
    setup_classroom(server)
    assert "class_deploy_enabled" not in hello(server, "b4:2e:99:07:1a:c4")


def test_the_console_settings_screen_wires_the_class_deploy_switch():
    """המתג במסך ההגדרות: נקרא מ-GET, נשלח ב-POST כמחרוזת "true"/"false"
    כמו שאר ההגדרות, וה-`?v=` הוקפץ (JS ישן מול API חדש — gotcha ידוע).
    בדיקת תוכן — אין דפדפן בחבילה."""
    import re   # noqa: PLC0415
    from pathlib import Path   # noqa: PLC0415
    static = Path(__file__).resolve().parent.parent / "server" / "static"
    js = (static / "console.js").read_text(encoding="utf-8")
    # ‏#954 גל 6: מסך ההגדרות הוא טבלת שורות (`SETTING_ROWS`); המתג הוא שורה
    # מסוג switch, הקריאה/כתיבה משותפות לכל המתגים ("true"/"false" כמחרוזת).
    # ‏#1685 (cc2ece3f, עמוד ההגדרות לפי המוקאפ): המתג עבר לכרטיס "deploy",
    # ומתג נשמר מיד בלחיצה (`settingsSwitch`) — לא בטופס השמירה של הכרטיס.
    assert '{ key: "class_deploy_enabled", card: "deploy", label:' in js
    rows = js[js.index("const SETTING_ROWS"):js.index("];", js.index("const SETTING_ROWS"))]
    row = next(line for line in rows.splitlines() if 'key: "class_deploy_enabled"' in line)
    assert 'type: "switch"' in row
    assert 'if (r && r.type === "switch") return r.defaultOn ? v !== "false" : v === "true";' in js
    switch = js[js.index("function settingsSwitch("):]
    switch = switch[:switch.index("\n}\n")]
    assert 'await post("/settings", { [key]: want ? "true" : "false" });' in switch
    page = (static / "index.html").read_text(encoding="utf-8")
    versions = {tuple(int(x) for x in v.split(".")) for v in re.findall(r"\?v=([0-9.]+)", page)}
    assert versions and min(versions) >= (4, 9), versions


def test_recovery_login_toggle(server):
    """ברירת המחדל הבטוחה: recovery דורש כניסה. הדגמה יכולה לכבות."""
    setup_classroom(server)
    assert hello(server, "b4:2e:99:07:1a:c4")["ui"]["require_login"] is True
    assert server["admin"].post(
        "/api/console/settings", json={"recovery_require_login": "false"}
    ).status_code == 200
    assert hello(server, "b4:2e:99:07:1a:c4")["ui"]["require_login"] is False


# --- סינון וחיפוש ביומן (#115) -------------------------------------------


def test_journal_events_list_is_hebrew_labels(server):
    events = server["admin"].get("/api/console/journal/events").json()
    assert {"event": "login_failed", "label": "ניסיון כניסה כושל"} in events
    # ממוין לפי התווית — כדי שהתפריט יהיה קריא, לא לפי סדר יצירה במילון.
    labels = [e["label"] for e in events]
    assert labels == sorted(labels)


def test_journal_filter_by_event_type(server):
    server["anon"].post("/api/console/login", json={"username": "x", "password": "no"})
    server["admin"].post("/api/console/login", json={"username": "admin", "password": "wrong"})
    rows = server["admin"].get("/api/console/journal", params={"event": "login_failed"}).json()
    assert rows and all(r["event"] == "login_failed" for r in rows)


def test_journal_filter_by_date_range_excludes_out_of_range_rows(server):
    setup_classroom(server)
    hello(server, "b4:2e:99:07:1a:c4")  # אין סבב — לא כותב ליומן, רק ה-group_create/machine_add למעלה
    all_rows = server["admin"].get("/api/console/journal").json()
    assert all_rows, "צריך לפחות שורה אחת כדי שהבדיקה תהיה משמעותית"
    newest_ts = max(r["ts"] for r in all_rows)
    # "עד" לפני השורה החדשה ביותר — היא לא אמורה לחזור.
    before = server["admin"].get(
        "/api/console/journal", params={"to": "2000-01-01T00:00:00"}
    ).json()
    assert before == []
    after = server["admin"].get(
        "/api/console/journal", params={"from": newest_ts}
    ).json()
    assert any(r["ts"] == newest_ts for r in after)


def test_journal_machine_filter_matches_the_display_name_not_the_raw_id(server, classrooms_on):
    """המלכודת ב-#115: מפעיל מחפש את מה שהוא רואה על המסך (שם הכיתה),
    לא את המזהה הגולמי (grp_...) שכתוב בפועל בשורת ה-DB."""
    admin = server["admin"]
    assert admin.post(
        "/api/console/groups",
        json={"id": "grp_9f2e", "label": "מבנה מדעים", "role": "classroom"},
    ).status_code == 200

    # ודאות שזה באמת מבחן על המלכודת: השם המוצג לא מופיע בתוך המזהה הגולמי.
    assert "מבנה מדעים" not in "grp_9f2e"

    by_label = admin.get(
        "/api/console/journal", params={"machine": "מבנה מדעים"}
    ).json()
    assert any(r["event"] == "group_create" and "מבנה מדעים" in r["text"] for r in by_label)

    by_raw_id = admin.get(
        "/api/console/journal", params={"machine": "grp_9f2e"}
    ).json()
    assert any(r["event"] == "group_create" for r in by_raw_id)


def test_journal_free_text_search_matches_translated_text(server):
    setup_classroom(server)
    rows = server["admin"].get("/api/console/journal", params={"q": "כיתה LAB1"}).json()
    assert any(r["event"] == "group_create" for r in rows)
    empty = server["admin"].get(
        "/api/console/journal", params={"q": "מחרוזת שלא קיימת באמת"}
    ).json()
    assert empty == []


def test_journal_filters_require_admin(server):
    assert server["deploy"].get(
        "/api/console/journal", params={"event": "login"}
    ).status_code == 403
    assert server["deploy"].get("/api/console/journal/events").status_code == 403


# --- עצירת סבב כיתה: תפקיד והקלדת שם (עיקרון 7, #581) ------------------------


def test_close_without_typing_the_image_name_does_not_stop_the_class_round(server):
    """‏#581: ‏POST ריק עצר סבב כיתה חי — בלי גוף ובלי שום אימות בשרת.

    זו אותה חולשה שנסגרה לחדר השיכפולים ב-#533, שנשארה פתוחה לסבב
    הכיתה. עיקרון 7 נוקב ב"עצירת סבב" במפורש, והאכיפה היחידה ישבה
    ב-`classes.js` — טקסט קבוע ("עצור") במסך, כלומר בדיוק השכבה שאסור
    לסמוך עליה.

    מה שמוקלד הוא **שם האימג' שהסבב משדר** — הכותרת שהמסך כבר מציג
    ("משדר: ...").
    """
    ids = open_session(server, expected=30)
    hello(server, ids["mac1"])
    deploy = server["deploy"]
    path = f"/api/console/sessions/{ids['session']}/close"

    # ‏1. גוף ריק — זו בדיוק הקריאה שסגרה סבב לפני #581.
    assert deploy.post(path).status_code == 400
    assert hello(server, ids["mac1"])["session"] is not None, "סבב נסגר בלי אישור"

    # ‏2. הטקסט שהמסך אכף לבדו אינו האישור
    assert deploy.post(path, json={"confirm_name": "עצור"}).status_code == 400
    assert hello(server, ids["mac1"])["session"] is not None, "סבב נסגר על שם שגוי"

    # ‏3. השם המדויק — וזה עוצר
    stopped = deploy.post(path, json={"confirm_name": "Office 2024 Standard"})
    assert stopped.status_code == 200 and stopped.json()["ok"] is True
    assert hello(server, ids["mac1"])["session"] is None


def test_a_class_round_whose_image_was_deleted_can_still_be_stopped(server):
    """מה שמוקלד הוא מה שהמסך מציג — גם כשהמניפסט כבר איננו.

    ‏`session_view` נופל חזרה ל-`image_id` כשהאימג' נמחק מהספרייה תוך
    כדי סבב, ולכן גם האימות חייב ליפול לשם — מאותה פונקציה. בלי זה
    עצירת חירום של שידור חי הייתה בלתי אפשרית, כלומר תיקון שגרוע
    מהבאג.
    """
    ids = open_session(server, expected=30)
    hello(server, ids["mac1"])
    admin, deploy = server["admin"], server["deploy"]
    # #784: the console refuses to delete an image a live round uses.
    assert admin.post("/api/console/images/img_7f3a91/delete",
                      json={"confirm_name": "Office 2024 Standard"},
                      ).status_code == 409
    # The manifest can still vanish under a live round (disk, another
    # tool); stopping it must survive that, so remove it underneath.
    assert server["ctx"].library.delete("img_7f3a91")

    view = admin.get("/api/console/overview").json()["session"]
    assert view["image_name"] == "img_7f3a91"
    path = f"/api/console/sessions/{ids['session']}/close"
    assert deploy.post(
        path, json={"confirm_name": "Office 2024 Standard"}).status_code == 400
    assert deploy.post(path, json={"confirm_name": "img_7f3a91"}).status_code == 200
    assert hello(server, ids["mac1"])["session"] is None


def test_a_role_that_is_not_on_the_list_cannot_drive_a_class_round(server):
    """הבקרה השלילית של #581 — אותה בדיקה שנעשתה לחדר ב-#152 ולתחנה ב-#94.

    לפני התיקון ``start`` ו-``close`` היו ``Depends(current_user)``
    בלבד: הסבב **נפתח** מאחורי ``ROUND_OPENER_ROLES`` ונסגר בלעדיה.
    התפקיד ``auditor`` אינו קיים היום, ולכן זו סכימה של מחר: השאלה
    אינה מי מורשה עכשיו אלא האם הקוד **שואל**.
    """
    from fastapi.testclient import TestClient                  # noqa: PLC0415
    from test_station import add_user_with_role                # noqa: PLC0415

    ids = open_session(server, expected=30)
    hello(server, ids["mac1"])
    add_user_with_role(server, "auditor", "audit-pass-12", "auditor")
    client = TestClient(server["app"])
    assert client.post("/api/console/login", json={
        "username": "auditor", "password": "audit-pass-12"}).status_code == 200

    # קריאה מותרת — היא אינה הרסנית
    assert client.get("/api/console/overview").status_code == 200

    assert client.post(
        f"/api/console/sessions/{ids['session']}/start").status_code == 403
    assert client.post(
        f"/api/console/sessions/{ids['session']}/close",
        json={"confirm_name": "Office 2024 Standard"}).status_code == 403
    assert hello(server, ids["mac1"])["session"]["state"] == "open"


def test_a_role_that_is_not_on_the_list_cannot_open_a_class_round(server):
    """הבקרה השלילית של #592 — פתיחת הסבב עצמה, לא רק start/close.

    ‏#581 סגר את ``start``/``close`` מאחורי ``round_operator``, אבל
    ‏``POST /api/console/sessions`` — **פתיחת** הסבב מהקונסולה — נשארה
    ‏``current_user`` בלבד: כל חשבון מחובר יכול היה לפתוח שידור חי
    לכיתה. אותה סכימת-מחר כמו #581: ``auditor`` אינו ברשימה, והשאלה
    אינה מי מורשה עכשיו אלא האם הקוד **שואל** על התפקיד.
    """
    from fastapi.testclient import TestClient                  # noqa: PLC0415
    from test_station import add_user_with_role                # noqa: PLC0415

    ids = setup_classroom(server, expected=30)
    add_user_with_role(server, "auditor", "audit-pass-12", "auditor")
    client = TestClient(server["app"])
    assert client.post("/api/console/login", json={
        "username": "auditor", "password": "audit-pass-12"}).status_code == 200

    # קריאה מותרת — היא אינה הרסנית
    assert client.get("/api/console/overview").status_code == 200

    # פתיחת סבב היא פעולת מפעיל — auditor חייב לקבל 403, לא 200
    assert client.post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 30}).status_code == 403
