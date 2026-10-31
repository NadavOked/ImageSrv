"""סבב חדר השיכפולים (Issue #9, אפיון סעיף 29) — מקצה לקצה מול ה-API.

הגלים נבדקים כמו שהם קורים בחדר: מכונות מדווחות מגירות ב-hello,
גל יוצא כשהמוכנות מכסה את היתרה, מגירה נספרת לפי serial פעם אחת,
ומכונה שלא הוחלפו לה המגירות לא מצטרפת לגל הבא.
"""

from __future__ import annotations

from pathlib import Path
import json

import pytest
from conftest import Clock, hello_body, write_image, MANIFEST_256, room_slots

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

CLONER1 = "aa:bb:cc:00:00:21"
CLONER2 = "aa:bb:cc:00:00:22"


@pytest.fixture()
def room_server(tmp_path: Path, monkeypatch):
    """שרת עם WoL מזויף ומנוע שידור מזויף, ושני מחשבי שיכפול רשומים."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                       # noqa: PLC0415

    from server.rounds import sender as sender_module
    from server.console import users
    from server.app import create_app

    # ה-runner המזויף הוא הראיה שהשידור עצמו בשליטת הטסט. גם בדיקת
    # הפורטים חייבת להיות ראיה מזויפת חיובית: ב-Windows אין /proc/net/udp,
    # ובלי זה #1126 הופך בצדק את כשל הבדיקה ל-send_failed ומפסיק את החדר.
    monkeypatch.setattr(sender_module, "port_holders", lambda _port: [])

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    woken: list[bytes] = []
    recorder = Recorder()
    app = create_app(
        tmp_path / "data", images, "http://10.44.12.10:8080",
        now_fn=Clock(), sender_runner=recorder,
        wol_send=woken.append,
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)

    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    for mac, name in ((CLONER1, "shich-1"), (CLONER2, "shich-2")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": "grp_CLONERS",
        }).status_code == 200
    yield {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
           "anon": TestClient(app), "woken": woken, "recorder": recorder}
    ctx.sender.stop()


def room_body(room_server, target: int, drawers: dict[str, list[str]]) -> dict:
    """גוף פתיחת סבב עם בחירה מפורשת (#1410) של כל המגירות ב-`drawers`."""
    return {"image_id": "img_7f3a91", "target_drives": target,
            "target_slots": room_slots(room_server["ctx"].conn, drawers)}


def cloner_hello(client, mac: str, serials: list[str], *, ports: bool = True,
                 joining: bool | None = None) -> dict:
    """hello של מחשב שיכפול: מגירה לכל serial.

    ‏`ports=False` מדמה סוכן ישן (או VM עם SCSI) שאינו מדווח חריץ —
    השדה פשוט חסר, וזה חייב להמשיך לעבוד (עיקרון 1).

    ‏`joining=None` הוא סוכן שאינו שולח את השדה בכלל — ברירת המחדל
    ההיסטורית, שחייבת להישאר "מצטרף".
    """
    body = hello_body(mac)
    if joining is not None:
        body["joining"] = joining
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304,
         "model": "Drawer SSD", "serial": serial, "removable": False,
         "scheme": "gpt", "has_data": False,
         **({"port": i + 1} if ports else {})}
        for i, serial in enumerate(serials)
    ]
    response = client.post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200
    return response.json()


def report(client, session_id: str, mac: str, per_drive: dict[str, str],
           top: str | None = None) -> None:
    """דיווח סופי של מכונה: מצב לכל מגירה (dev → done/failed).

    ברירת המחדל של `top` היא מה ש**סוכן ישן** שולח — `done` על כל מכונה
    שמגירה אחת שלה שרדה (#67). היא נשארת כאן בכוונה: זה חוזה שהשרת
    חייב להמשיך לכבד. סוכן מעודכן שולח `partial` במפורש.
    """
    states = list(per_drive.values())
    if top is None:
        top = "failed" if all(s == "failed" for s in states) else "done"
    response = client.post("/api/v1/agent/progress", json={
        "session_id": session_id, "mac": mac, "state": top,
        "targets": [
            {"dev": dev, "bytes_written": 100 if state == "done" else 4,
             "bytes_total": 100, "state": state,
             **({"error": "I/O error"} if state == "failed" else {})}
            for dev, state in per_drive.items()
        ],
    })
    assert response.status_code == 200


def room(client) -> dict:
    response = client.get("/api/console/room")
    assert response.status_code == 200
    return response.json()


def heartbeat(room_server):
    """Explicit agent event for tests that used GET as their clock (#446)."""
    ctx = room_server["ctx"]
    row = ctx.conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?",
                           (CLONER1,)).fetchone()
    if row is None:
        return
    body = hello_body(CLONER1)
    body.update(disks=json.loads(row["disks_json"]), joining=False)
    response = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text


def test_round_wakes_room_and_autostarts_when_drives_cover_target(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]

    # משתמש הפצה פותח — זו בדיוק ההרשאה שלו (אפיון סעיף 11).
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 4, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    assert opened.status_code == 200
    assert len(room_server["woken"]) == 2          # WoL לכל החדר עם הפתיחה

    # שתי מכונות עולות עם שתי מגירות כל אחת — 4 מוכנות מול יעד 4.
    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert answer["session"]["state"] == "open"
    cloner_hello(anon, CLONER2, ["S3", "S4"])

    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["ready_drives"] in (0, 4)          # לפני/אחרי tick של ה-GET
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"


def test_room_start_with_no_members_is_refused_and_wave_stays_open(room_server):
    """‏#843: "התחל עכשיו" על גל שאיש עוד לא הצטרף אליו — 409, לא 200.

    זה בדיוק מה שקרה במעבדה 15/09: `room_open` ו-`session_start_manual`
    באותה שנייה, ‏180ש' אחר כך `send_failed` ("אף מחשב לא הצטרף"), והגל
    נשאר `running` בלי חברים — המשכפלים קיבלו `session: null` לנצח.
    הגל חייב להישאר `open` כדי שה-hello הבא יצטרף, ו-udp-sender אסור
    שיצא לדרך.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 4, {CLONER1: ["S1", "S2"]}),
                       ).status_code == 200
    refused = deploy.post("/api/console/room/start")
    assert refused.status_code == 409, refused.text
    assert "אין מכונות בסבב" in refused.json()["detail"]
    assert room(deploy)["round"]["wave_state"] == "open"
    assert ctx.sender.status() is None            # השידור לא יצא לדרך

    # המכונה שמגיעה אחרי הלחיצה השגויה עדיין מצטרפת — ואז ההתחלה מתקבלת.
    joined = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert joined["state"] == "open"
    assert deploy.post("/api/console/room/start").status_code == 200
    assert room(deploy)["round"]["wave_state"] == "running"


def test_sender_failure_stops_room_until_operator_opens_it_again(room_server):
    """#1126: send_failed מסיים גם את הגל וגם את החדר כ-`failed`.

    `failed` הוא מצב סופי מפורש עם סיבה, לא `running` ולא `active`; דופק
    אינו פותח גל נוסף, ורק פתיחה מפורשת של מפעיל יוצרת חדר `active` חדש.
    """
    from test_sender import wait_for                         # noqa: PLC0415

    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    room_server["recorder"].code = 3
    opened = deploy.post(
        "/api/console/room",
        json=room_body(room_server, 2, {CLONER1: ["S1"]}),
    ).json()
    wave = cloner_hello(anon, CLONER1, ["S1"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    assert wait_for(lambda: ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = ?", (opened["id"],)
    ).fetchone()["state"] == "failed")
    failed = room(deploy)["round"]
    assert failed["state"] == "failed" and failed["failed_reason"]
    assert ctx.conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (wave,)
    ).fetchone()["state"] == "failed"

    sessions_before = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM sessions"
    ).fetchone()["n"]
    heartbeat(room_server)
    heartbeat(room_server)
    assert ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM sessions"
    ).fetchone()["n"] == sessions_before

    room_server["recorder"].code = 0
    reopened = deploy.post(
        "/api/console/room",
        json=room_body(room_server, 2, {CLONER1: ["S1"]}),
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["id"] != opened["id"]
    assert room(deploy)["round"]["state"] == "active"
    assert ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = ?", (opened["id"],)
    ).fetchone()["state"] == "failed"


def test_tally_requires_the_serial_that_was_present_at_done(room_server):
    from server.rounds import room as room_module                  # noqa: PLC0415

    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = deploy.post(
        "/api/console/room",
        json=room_body(room_server, 2, {CLONER1: ["S1"]}),
    ).json()
    wave = cloner_hello(anon, CLONER1, ["S1"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    report(anon, wave, CLONER1, {"sda": "done"})

    round_row = ctx.conn.execute(
        "SELECT * FROM room_rounds WHERE id = ?", (opened["id"],)
    ).fetchone()
    members = ctx.store.members(wave)
    targets = json.loads(members[0]["targets_json"])
    assert targets[0]["serial"] == "S1"
    assert room_module._tally(ctx.conn, round_row, members)[0] == 1

    cloner_hello(anon, CLONER1, ["S9"], joining=False)
    assert room_module._tally(ctx.conn, round_row, members)[0] == 0
    assert room_module._tally(ctx.conn, round_row, members)[0] == 0
    changed = ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_drawer_changed'"
    ).fetchall()
    assert len(changed) == 1
    assert "shich-1" in changed[0]["detail"]
    assert "serial=S1 current=S9" in changed[0]["detail"]


def test_close_marks_closing_before_sender_stop_so_tick_opens_no_wave(
    room_server, monkeypatch,
):
    from server.rounds import room as room_module                  # noqa: PLC0415

    deploy, ctx = room_server["deploy"], room_server["ctx"]
    opened = deploy.post(
        "/api/console/room",
        json=room_body(room_server, 2, {CLONER1: ["S1"]}),
    ).json()
    before = ctx.conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
    observed = []

    def stop_during_close(*_args):
        observed.append(ctx.conn.execute(
            "SELECT state FROM room_rounds WHERE id = ?", (opened["id"],)
        ).fetchone()["state"])
        room_module.tick(ctx.conn, ctx.store)
        return None

    monkeypatch.setattr(ctx.sender, "stop", stop_during_close)
    closed = deploy.post(
        "/api/console/room/close",
        json={"confirm_name": "Office 2024 Standard"},
    )
    assert closed.status_code == 200, closed.text
    assert observed and set(observed) == {"closing"}
    assert ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM sessions"
    ).fetchone()["n"] == before
    assert ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = ?", (opened["id"],)
    ).fetchone()["state"] == "closed"


def test_waves_accumulate_by_serial_until_target(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 6, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}),
                       ).status_code == 200
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])

    # 4 מוכנות מול יתרה 6 — הגל לא יוצא לבד; "התחל עכשיו" כן.
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "open"
    assert deploy.post("/api/console/room/start").status_code == 200

    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done"})
    report(anon, wave1, CLONER2, {"sda": "done", "sdb": "done"})

    # הגל הסתיים: 4 נכתבו, גל שני נפתח מעצמו וממתין למגירות מוחלפות.
    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["written_drives"] == 4
    assert view["wave_number"] == 2
    assert view["wave_state"] == "open"

    # אותן מגירות — המכונה לא מצטרפת שוב (אחרת נספור אותן פעמיים).
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"] is None

    # מגירות מוחלפות: 2 טריות מכסות את היתרה — הגל יוצא לבד.
    answer = cloner_hello(anon, CLONER1, ["S5", "S6"])
    wave2 = answer["session"]["id"]
    assert wave2 != wave1
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"

    report(anon, wave2, CLONER1, {"sda": "done", "sdb": "done"})
    heartbeat(room_server)
    assert room(deploy)["round"] is None           # היעד הושג — הסבב נסגר

    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "room_open" in events and "room_wave" in events and "room_done" in events


def test_failed_drawer_is_not_counted_and_retries_next_wave(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]

    deploy.post("/api/console/room",
                json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"

    # מגירה אחת נכשלה — נספרת רק המוצלחת, בגלוי (עיקרון 4).
    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "failed"})
    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["written_drives"] == 1 and view["wave_state"] == "open"

    # אותה מכונה, אותן מגירות: S2 הכושלת עדיין טרייה — מצטרפים וכותבים שוב.
    wave2 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"
    report(anon, wave2, CLONER1, {"sdb": "done"})
    heartbeat(room_server)
    assert room(deploy)["round"] is None


def test_a_machine_that_lost_one_drawer_is_partial_and_not_done(room_server):
    """‏#67: המצב השלישי, לכל אורך הדרך — מהדיווח ועד המסך.

    ‏`partial` הוא **סופי**: הגל מסתיים ונפתח הבא, אחרת מכונה שאיבדה
    מגירה הייתה תולה את החדר כולו. הוא **אינו** הצלחה מלאה: הדגל `done`
    של חבר הסבב לא נדלק, המסך מקבל את המילה, והמגירה שנכשלה לא נספרת.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    deploy.post("/api/console/room",
                json=room_body(room_server, 6, {CLONER1: ["S1", "S2", "S3"], CLONER2: ["S4", "S5"]}))
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S4", "S5"])
    assert deploy.post("/api/console/room/start").status_code == 200

    # המכונה הראשונה איבדה מגירה אחת מתוך שלוש; השנייה עוד כותבת.
    report(anon, wave1, CLONER1,
           {"sda": "done", "sdb": "failed", "sdc": "done"}, top="partial")

    heartbeat(room_server)
    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert machine["state"] == "partial", "המסך לא קיבל את המצב השלישי"
    assert "sdb" in (machine["error"] or "")

    member = ctx.conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (wave1, CLONER1),
    ).fetchone()
    assert member["state"] == "partial"
    assert member["done"] == 0, "הושלם חלקית נספר כהצלחה מלאה"

    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "client_partial" in events

    # וכשהשנייה מסיימת, הגל נסגר: `partial` הוא סיום, לא המתנה. בלי זה
    # מכונה שאיבדה מגירה הייתה תולה את החדר כולו.
    report(anon, wave1, CLONER2, {"sda": "done", "sdb": "done"})
    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["written_drives"] == 4              # ‏S2 הכושלת לא נספרה
    assert view["wave_number"] == 2

    # והמגירה שנכשלה נשארת טרייה: הגל הבא כותב אותה שוב.
    assert cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"] is not None


def test_an_old_agent_that_still_says_done_keeps_working(room_server):
    """תאימות לאחור (#67/#1126): סוכן ישן שולח `done` בלי קשירת serial.

    ‏`schema` לא עלה, ולכן הוא חייב להמשיך לעבוד בדיוק כמו קודם —
    השרת קושר serial כשהוא זמין, והיעדר החותמת החדשה אינו החלפת מגירה;
    הספירה היא ממילא יעד-יעד, והמגירה הכושלת עדיין אינה נספרת.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    deploy.post("/api/console/room",
                json=room_body(room_server, 4, {CLONER1: ["S1", "S2"]}))
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200

    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "failed"})   # top=done

    # מדמה targets_json שנשמר לפני #1126: אין בו חותמת serial חדשה. היעדר
    # השדה אינו ראיה שהמגירה הוחלפה; sda עדיין חייבת להיספר לפי המלאי החי.
    legacy_targets = json.loads(ctx.conn.execute(
        "SELECT targets_json FROM session_members WHERE session_id = ? AND mac = ?",
        (wave1, CLONER1),
    ).fetchone()["targets_json"])
    for target in legacy_targets:
        target.pop("serial", None)
    ctx.conn.execute(
        "UPDATE session_members SET targets_json = ? WHERE session_id = ? AND mac = ?",
        (json.dumps(legacy_targets), wave1, CLONER1),
    )
    ctx.conn.commit()

    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["written_drives"] == 1 and view["wave_number"] == 2
    member = ctx.conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (wave1, CLONER1),
    ).fetchone()
    assert (member["state"], member["done"]) == ("done", 1)


def test_the_drawer_slot_travels_from_hello_to_the_console(room_server):
    """‏#27: הקונסולה צריכה לדעת איזה חריץ לשלוף — לא באיזה סדר התגלה.

    ‏`port` מדווח ב-hello (ממשק 2), נשמר כמו שהוא, וחוזר במסך החדר
    יחד עם שם ההתקן ומצב הכתיבה של אותה מגירה.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]

    deploy.post("/api/console/room",
                json=room_body(room_server, 3, {CLONER1: ["S1", "S2", "S3"]}))
    wave = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]

    heartbeat(room_server)
    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert [(d["port"], d["dev"]) for d in machine["drawer_list"]] == [
        (1, "sda"), (2, "sdb"), (3, "sdc")]
    assert all(d["fresh"] for d in machine["drawer_list"])
    assert machine["drawers"] == 3 and machine["fresh_drawers"] == 3

    # תוך כדי הגל: המצב יושב על המגירה עצמה — "מגירה 3 (sdc) נכשלה",
    # ולא "אחת משלוש נכשלה, לך תמצא איזו".
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [
            {"dev": "sda", "bytes_written": 100, "bytes_total": 100, "state": "done"},
            {"dev": "sdb", "bytes_written": 50, "bytes_total": 100, "state": "writing"},
            {"dev": "sdc", "bytes_written": 4, "bytes_total": 100, "state": "failed",
             "error": "I/O error at sector 8419328"},
        ],
    }).status_code == 200
    by_port = {d["port"]: d for d in
               next(m for m in room(deploy)["machines"]
                    if m["mac"] == CLONER1)["drawer_list"]}
    assert by_port[3]["state"] == "failed" and "I/O error" in by_port[3]["error"]
    assert by_port[1]["state"] == "done" and by_port[2]["state"] == "writing"

    # ואחרי שהגל נסגר: המגירה שנכשלה נשארת הטרייה היחידה — לפי חריץ.
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done", "sdc": "failed"})
    heartbeat(room_server)
    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    fresh = [d["port"] for d in machine["drawer_list"] if d["fresh"]]
    assert fresh == [3] and machine["fresh_drawers"] == 1


def test_the_crc_rise_of_the_round_reaches_the_drawer_and_absence_is_not_zero(room_server):
    """‏#872: הסוכן מדווח `crc_delta` ליעד שנמדד (199 לפני ואחרי הכתיבה),
    ומשמיט אותו ליעד שלא נמדד. במסך החדר: 5 על מגירה 1, ‏None על מגירה 2 —
    לא 0, כי "לא נמדד" ו"לא עלה" הם שני מצבים (עיקרון 5). בקרה שלילית: על
    main השדה אינו קיים ב-`drawer_list` כלל."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    deploy.post("/api/console/room",
                json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "done",
        "targets": [
            {"dev": "sda", "bytes_written": 100, "bytes_total": 100, "state": "done",
             "crc_delta": 5},
            {"dev": "sdb", "bytes_written": 100, "bytes_total": 100, "state": "done"},
        ],
    }).status_code == 200
    by_port = {d["port"]: d for d in
               next(m for m in room(deploy)["machines"]
                    if m["mac"] == CLONER1)["drawer_list"]}
    assert by_port[1]["crc_delta"] == 5
    assert by_port[2]["crc_delta"] is None


def test_an_agent_without_the_port_field_is_shown_and_not_written(room_server):
    """סוכן ישן: אין `port`, אין מספר חריץ. מאז #1410 הבחירה היא לפי חריץ
    ואין "כל הדיסקים", ולכן מגירה בלי חריץ אינה יעד — המכונה אינה מצטרפת
    לגל (עיקרון 1), אבל נשארת נראית בקונסולה לפי שם ההתקן."""
    deploy, anon = room_server["deploy"], room_server["anon"]

    assert deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    assert cloner_hello(anon, CLONER1, ["S1", "S2"], ports=False)["session"] is None

    heartbeat(room_server)
    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert [d["port"] for d in machine["drawer_list"]] == [None, None]
    assert [d["dev"] for d in machine["drawer_list"]] == ["sda", "sdb"]
    assert machine["drawers"] == 2 and machine["fresh_drawers"] == 2
    assert [d["selected"] for d in machine["drawer_list"]] == [False, False]
    assert room(deploy)["round"]["wave_state"] == "open"


def test_room_round_respects_the_single_session_invariant(room_server, classrooms_on):
    admin, deploy = room_server["admin"], room_server["deploy"]

    # סבב כיתה פעיל תופס את החריץ היחיד — סבב חדר נדחה, בגלוי.
    assert admin.post("/api/console/groups", json={
        "id": "grp_LAB9", "label": "כיתה 9", "role": "classroom",
    }).status_code == 200
    assert admin.post("/api/console/machines", json={
        "mac": "b4:2e:99:00:00:09", "name": "05", "group_id": "grp_LAB9",
    }).status_code == 200
    assert admin.post("/api/console/sessions", json={
        "group_id": "grp_LAB9", "image_id": "img_7f3a91",
        "prefix": "LAB9", "expected_clients": 1,
    }).status_code == 200

    denied = deploy.post("/api/console/room",
                         json=room_body(room_server, 2, {CLONER1: ["S1"]}))
    assert denied.status_code == 409


def test_close_round_mid_wave_and_bad_requests(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]

    assert deploy.post("/api/console/room/close").status_code == 409
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_7f3a91", "target_drives": 0},
                       ).status_code == 400
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_nope", "target_drives": 2},
                       ).status_code == 400

    deploy.post("/api/console/room",
                json=room_body(room_server, 8, {CLONER1: ["S1", "S2"]}))
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "Office 2024 Standard"},
                       ).json()["ok"] is True
    heartbeat(room_server)
    assert room(deploy)["round"] is None
    # החריץ התפנה — אפשר לפתוח סבב חדש.
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}),
                       ).status_code == 200


def test_wake_endpoint_sends_wol_to_the_whole_room(room_server):
    deploy, woken = room_server["deploy"], room_server["woken"]
    result = deploy.post("/api/console/room/wake")
    assert result.status_code == 200
    assert result.json()["sent"] == 2 and len(woken) == 2


def test_anonymous_cannot_touch_the_room(room_server):
    anon = room_server["anon"]
    assert anon.get("/api/console/room").status_code in (401, 403)
    assert anon.post("/api/console/room",
                     json={"image_id": "img_7f3a91", "target_drives": 2},
                     ).status_code in (401, 403)


def test_a_held_machine_beats_without_joining_the_wave(room_server):
    """מכונה שנעצרה על מסך שגיאה פועמת בלי להצטרף (#64).

    הדופק חייב לעשות בדיוק שני דברים: להחזיק אותה **נראית** בקונסולה,
    ולא לגרום לגל לצאת לדרך בהסתמך על מגירות שאיש לא יכתוב. לפני
    התיקון המכונה פשוט השתתקה, והקונסולה ציירה אותה כ"כבויה" בזמן
    שהיא ישבה דלוקה עם שגיאה על המסך.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 4, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]})).status_code == 200

    # ארבע מגירות טריות — די והותר ליעד — אבל כולן בדופק בלבד.
    cloner_hello(anon, CLONER1, ["S1", "S2"], joining=False)
    cloner_hello(anon, CLONER2, ["S3", "S4"], joining=False)

    heartbeat(room_server)
    view = room(deploy)
    assert view["round"]["wave_state"] == "open"     # לא יצא לדרך
    assert view["round"]["ready_drives"] == 0        # ולא נספרו כמוכנות
    assert [m["joined"] for m in view["machines"]] == [False, False]

    # ובכל זאת נראות: זה כל תפקידו של הדופק.
    assert all(m["awake"] for m in view["machines"])

    # אותן מכונות ב-hello רגיל — עכשיו הגל כן יוצא.
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"


def test_an_agent_that_omits_joining_still_joins(room_server):
    """סוכן ישן אינו שולח `joining` — והיעדרו נשאר "מצטרף"."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200

    cloner_hello(anon, CLONER1, ["S1", "S2"], joining=None)
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"


def test_a_wave_closes_when_the_reports_come_in_another_mac_form(room_server):
    """‏#108: הגל נסגר גם כשהדיווח מגיע ברישיות ובמקפים.

    זו הבקרה השלילית של הבאג בצורתו החמורה ביותר: שורה שלא עודכנה
    נשארת `waiting`, ‏`room.tick` לא מוצא שכל החברים במצב סופי, והגל
    הבא לא נפתח — סבב שלם נעצר על מכונות שכתבו בהצלחה.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 6, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]})).status_code == 200

    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert deploy.post("/api/console/room/start").status_code == 200

    # אותן מכונות בדיוק — רק צורת ה-MAC בדיווח שונה מזו שב-hello.
    report(anon, wave1, CLONER1.upper().replace(":", "-"),
           {"sda": "done", "sdb": "done"})
    report(anon, wave1, CLONER2.replace(":", ""), {"sda": "done", "sdb": "done"})

    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["written_drives"] == 4        # ארבע מגירות נספרו
    assert view["wave_number"] == 2           # והגל הבא נפתח
    assert view["wave_state"] == "open"

    events = [r["event"] for r in room_server["ctx"].conn.execute(
        "SELECT event FROM journal")]
    assert "report_from_nonmember" not in events


# --- מי רשאי להפעיל את החדר, ולא רק מי שמחובר (#152) -------------------------


def test_a_role_that_is_not_on_the_list_cannot_drive_the_room(room_server):
    """הבקרה השלילית של #152 — אותה בדיקה שנעשתה לתחנה ב-#94.

    לפני התיקון ארבע נקודות הקצה של החדר היו ``Depends(current_user)``
    בלבד: כל חשבון מחובר יכול היה להעיר את החדר ולשדר על המגירות —
    הפעולה שדורסת כל כונן מחובר.

    התפקיד ``auditor`` אינו קיים היום, ולכן זו סכימה של מחר: השאלה
    אינה מי מורשה עכשיו אלא האם הקוד **שואל**.
    """
    from test_station import add_user_with_role                # noqa: PLC0415

    add_user_with_role(room_server, "auditor", "audit-pass-12", "auditor")
    client = TestClient(room_server["app"])
    assert client.post("/api/console/login", json={
        "username": "auditor", "password": "audit-pass-12"}).status_code == 200

    # קריאה מותרת — היא אינה הרסנית
    assert client.get("/api/console/room").status_code == 200

    # וארבע הפעולות שכן משנות מצב
    assert client.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2}).status_code == 403
    assert client.post("/api/console/room/start").status_code == 403
    assert client.post("/api/console/room/wake").status_code == 403
    assert client.post("/api/console/room/close").status_code == 403

    # ולא נשלח WoL לאף מכונה בדרך
    assert room_server["woken"] == []


# --- עצירת סבב מאחורי הקלדת שם (עיקרון 7, #533) ------------------------------


def test_close_without_typing_the_image_name_does_not_stop_the_round(room_server):
    """‏#533: ‏POST ריק עצר סבב פעיל — בלי גוף, בלי אישור, בלי בדיקה.

    עצירת סבב היא פעולה הרסנית לכל דבר: היא הורגת את המשדר באמצע
    כתיבה, וכל מגירה שהייתה תחת הזרם נשארת חצי-כתובה. עיקרון 7 מציב
    אותה באותה שורה עם מחיקת אימג' — "מאחורי הקלדת שם" — ומחיקת אימג'
    באמת מאמתת (`console_library.delete_image`). כאן לא היה בשרת שום
    אימות: האכיפה היחידה ישבה ב-`room.js`, והמסך הוא בדיוק השכבה
    שאסור לסמוך עליה.

    מה שמוקלד הוא **שם האימג' שהסבב משדר** — הכותרת שהמסך כבר מציג.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]

    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 8, {CLONER1: ["S1", "S2"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])

    # ‏1. גוף ריק — זו בדיוק הקריאה שסגרה סבב לפני #533.
    assert deploy.post("/api/console/room/close").status_code == 400
    assert room(deploy)["round"] is not None, "סבב נסגר בלי שום אישור"

    # ‏2. גוף עם טקסט שאינו שם האימג'
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "עצור"}).status_code == 400
    assert room(deploy)["round"] is not None, "סבב נסגר על שם שגוי"

    # ‏3. השם המדויק — וזה עוצר
    stopped = deploy.post("/api/console/room/close",
                          json={"confirm_name": "Office 2024 Standard"})
    assert stopped.status_code == 200 and stopped.json()["ok"] is True
    heartbeat(room_server)
    assert room(deploy)["round"] is None


def test_a_round_whose_image_was_deleted_can_still_be_stopped(room_server):
    """מה שמוקלד הוא מה שהמסך מציג — גם כשהמניפסט כבר איננו.

    ‏`status_view` נופל חזרה ל-`image_id` כשהאימג' נמחק מהספרייה תוך
    כדי סבב, ולכן גם האימות חייב ליפול לשם. אחרת עצירת חירום של
    שידור חי הייתה בלתי אפשרית — כלומר תיקון שגרוע מהבאג.
    """
    admin, deploy, anon = (room_server["admin"], room_server["deploy"],
                           room_server["anon"])

    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 8, {CLONER1: ["S1", "S2"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    # #784: the console refuses to delete an image a live round uses.
    assert admin.post("/api/console/images/img_7f3a91/delete",
                      json={"confirm_name": "Office 2024 Standard"},
                      ).status_code == 409
    # The manifest can still vanish under a live round (disk, another
    # tool); stopping it must survive that, so remove it underneath.
    assert room_server["ctx"].library.delete("img_7f3a91")

    assert room(deploy)["round"]["image_name"] == "img_7f3a91"
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "Office 2024 Standard"},
                       ).status_code == 400
    assert deploy.post("/api/console/room/close",
                       json={"confirm_name": "img_7f3a91"}).status_code == 200
    assert room(deploy)["round"] is None


# --- #695: בחירת דיסק יעד — נתיב הכתיבה הבטוח -------------------------------

def _open_with_slots(client, slots, target=4, image="img_7f3a91"):
    return client.post("/api/console/room", json={
        "image_id": image, "target_drives": target, "target_slots": slots})


def test_target_slots_rejects_a_disconnected_port(room_server):
    """בחירת פורט שאינו מחובר כרגע נדחית — לעולם לא פותחים סבב שיכתוב
    לדיסק שאינו שם (עיקרון 4/5)."""
    admin, anon = room_server["admin"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])      # פורטים 1,2 מחוברים
    r = _open_with_slots(admin, [{"mac": CLONER1, "ports": [3]}])
    assert r.status_code == 400
    assert "מחובר" in r.json()["detail"]


def test_target_slots_rejects_out_of_range_duplicate_and_unknown(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert _open_with_slots(admin, [{"mac": CLONER1, "ports": [9]}]).status_code == 400
    assert _open_with_slots(admin, [{"mac": CLONER1, "ports": [1, 1]}]).status_code == 400
    assert _open_with_slots(
        admin, [{"mac": "aa:bb:cc:00:00:99", "ports": [1]}]).status_code == 400
    assert _open_with_slots(admin, []).status_code == 400   # אין fallback לכל-הדיסקים


def test_room_without_explicit_target_slots_is_rejected_without_a_row(room_server):
    response = room_server["deploy"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 4})
    assert response.status_code == 400
    assert response.json()["detail"] == "יש לבחור לפחות דיסק יעד אחד"
    assert room_server["ctx"].conn.execute(
        "SELECT COUNT(*) FROM room_rounds").fetchone()[0] == 0


def test_target_slots_stored_and_hello_returns_only_this_machines_ports(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert _open_with_slots(admin, [
        {"mac": CLONER1, "ports": [1]}, {"mac": CLONER2, "ports": [2]},
    ]).status_code == 200
    # כל מכונה מקבלת רק את הפורטים שלה.
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["target_ports"] == [1]
    assert cloner_hello(anon, CLONER2, ["S3", "S4"])["session"]["target_ports"] == [2]


def test_drawer_list_marks_selected_and_filters_fresh(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])   # 3 פורטים (מאכלס דיסקים)
    assert _open_with_slots(admin, [{"mac": CLONER1, "ports": [2]}]).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])   # מצטרף לגל הפעיל
    machine = next(m for m in room(admin)["machines"] if m["mac"] == CLONER1)
    sel = {d["port"]: d["selected"] for d in machine["drawer_list"]}
    assert sel == {1: False, 2: True, 3: False}       # רק פורט 2 נבחר
    assert sum(1 for d in machine["drawer_list"] if d["selected"]) == 1
    # ‏fresh = נוכח פיזית (3); הבחירה נפרדת ומשפיעה על הספירה לגל.
    assert machine["fresh_drawers"] == 3
    assert room(admin)["round"]["ready_drives"] == 1  # רק הנבחר נספר ליעד


def test_drawer_count_setting_persists_and_bounds(room_server):
    admin = room_server["admin"]
    assert admin.put(f"/api/console/machines/{CLONER1}",
                     json={"drawer_count": 5}).status_code == 200
    row = next(m for m in admin.get("/api/console/machines").json()
               if m["mac"] == CLONER1)
    assert row["drawer_count"] == 5
    assert admin.put(f"/api/console/machines/{CLONER1}",
                     json={"drawer_count": 9}).status_code == 400
    assert admin.put(f"/api/console/machines/{CLONER1}",
                     json={"drawer_count": 0}).status_code == 400


# --- #774: חותמת last_seen עתידית אינה "ער" ---------------------------------


def test_a_future_last_seen_is_not_awake():
    """**#774.** `last_seen` בעתיד (סטיית שעון / DB מיובא) נותן גיל
    שלילי, שבלי חסם תחתון מקיים `<= AWAKE_SECONDS` — והמכונה נראתה
    "ערה" לכל משך הסטייה. חותמת עתידית אינה ראיה שהמכונה חיה עכשיו."""
    from datetime import datetime, timedelta, timezone         # noqa: PLC0415

    from server.rounds.room import AWAKE_SECONDS, _is_awake            # noqa: PLC0415

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert _is_awake(future) is False

    # גם סטייה קטנה מעבר לעכשיו — החסם התחתון הוא 0, לא "קרוב מספיק".
    slightly_future = (
        datetime.now(timezone.utc) + timedelta(seconds=AWAKE_SECONDS + 5)
    ).isoformat()
    assert _is_awake(slightly_future) is False


def test_a_recent_last_seen_is_still_awake():
    """רדיוס הפגיעה: חותמת עדכנית בעבר הקרוב עדיין "ערה"."""
    from datetime import datetime, timedelta, timezone         # noqa: PLC0415

    from server.rounds.room import _is_awake                          # noqa: PLC0415

    recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert _is_awake(recent) is True


# --- #449: מכונה שכותבת בפועל אינה "ישנה" ------------------------------------


def test_fresh_progress_keeps_a_machine_awake_without_hello():
    """**#449.** ``last_seen`` (hello) ישן, אבל דיווח progress טרי.

    בזמן שחזור הסוכן חדל לפעום ומדווח progress בלבד. ‏`_is_awake` חייב
    לקבל את שתי הראיות: חותמת progress עדכנית לצד hello ישן = ערה."""
    from datetime import datetime, timedelta, timezone         # noqa: PLC0415

    from server.rounds.room import _is_awake                          # noqa: PLC0415

    stale = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    fresh = (datetime.now(timezone.utc) - timedelta(seconds=4)).isoformat()
    assert _is_awake(stale, fresh) is True
    # שתיהן ישנות — עדיין ישנה.
    assert _is_awake(stale, stale) is False


def test_a_writing_machine_reporting_progress_is_not_shown_asleep(room_server):
    """**#449.** מכונה כותבת ששולחת progress ולא hello נראתה ``awake=False``.

    ‏`awake` נשען על `net_devices.last_seen` (hello) בחלון 30 שניות, אבל
    השחזור רץ סינכרונית והסוכן מדווח **progress** בלבד. אחרי 30 שניות
    של כתיבה מכונה בריאה לחלוטין קיבלה את אותו סימון כמו מכונה שנפלה או
    איבדה רשת (עיקרון 5). הטסט נופל אם כותבת נראית זהה לנעלמת."""
    from datetime import datetime, timedelta, timezone         # noqa: PLC0415

    deploy, anon, ctx = (room_server["deploy"], room_server["anon"],
                         room_server["ctx"])

    deploy.post("/api/console/room",
                json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]

    # המכונה כותבת ומדווחת progress — updated_at של החבר נדחף לעכשיו.
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [
            {"dev": "sda", "bytes_written": 50, "bytes_total": 100,
             "state": "writing"},
            {"dev": "sdb", "bytes_written": 30, "bytes_total": 100,
             "state": "writing"},
        ],
    }).status_code == 200

    # ...אבל ה-hello האחרון ישן: הסוכן חדל לפעום כל משך הכתיבה.
    stale = (datetime.now(timezone.utc) - timedelta(minutes=3)
             ).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ?",
                     (stale, CLONER1))
    ctx.conn.commit()

    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert machine["state"] == "writing"          # אכן כותבת
    assert machine["awake"] is True               # #449: כותבת ⇒ ערה


def test_989_new_serials_rejoin_the_open_round_and_same_serials_do_not_until_the_target(room_server):
    """‏#989 סעיף 3: סבב 7 על שתי מכונות (2+3 מגירות). הגל הראשון כותב 5; מכונה שחזרה
    עם אותם דיסקים לא נכתבת שנית (‏`fresh_serials`, #695); מכונה שחזרה עם serials חדשים
    מצטרפת לאותו סבב ל**גל הבא**, נכתבת עד 7, והסבב נסגר."""
    deploy, anon = room_server["deploy"], room_server["anon"]

    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 7, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4", "S5"]})).status_code == 200
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4", "S5"])
    heartbeat(room_server)
    first = room(deploy)["round"]
    assert first["wave_state"] == "open"        # 5 מוכנים מול 7 — לא יוצא לבד
    assert deploy.post("/api/console/room/start").status_code == 200
    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done"})
    report(anon, wave1, CLONER2, {"sda": "done", "sdb": "done", "sdc": "done"})

    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["id"] == first["id"]
    assert (view["written_drives"], view["remaining_drives"], view["wave_number"]) == (5, 2, 2)
    assert view["wave_state"] == "open" and view["ready_drives"] == 0      # הדיסקים הישנים אינם "מוכנים"

    # אותם דיסקים — לא מצטרפים ולא נכתבים שנית, ושום מוכן לא נוסף
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"] is None
    assert cloner_hello(anon, CLONER2, ["S3", "S4", "S5"])["session"] is None
    heartbeat(room_server)
    view = room(deploy)["round"]
    assert view["written_drives"] == 5 and view["ready_drives"] == 0 and view["wave_state"] == "open"

    # דיסקים חדשים (serials חדשים) — מצטרפים לאותו סבב, שני מוכנים מכסים את היתרה
    wave2 = cloner_hello(anon, CLONER1, ["S6", "S7"])["session"]["id"]
    assert wave2 != wave1
    view = room(deploy)["round"]
    assert view["id"] == first["id"] and view["ready_drives"] == 2 and view["wave_number"] == 2
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"
    report(anon, wave2, CLONER1, {"sda": "done", "sdb": "done"})
    heartbeat(room_server)
    assert room(deploy)["round"] is None                          # 7 מתוך 7 — נסגר
    row = room_server["ctx"].conn.execute(
        "SELECT state, written_drives, wave_number FROM room_rounds").fetchone()
    assert (row["state"], row["written_drives"], row["wave_number"]) == ("closed", 7, 2)
