"""‏#1386 "בטל דיסק זה" באמצע גל — צד השרת.

נדב 13/10: לעצור רק דיסק איטי או בעייתי כדי שהשאר ימשיכו. מה שנבדק:

- ‏`POST /api/console/room/drawer/cancel` ‏`{mac, dev}` — רק למפעיל סבבים
  (‏admin/deploy, ה-RBAC של `room_operator`), רק בגל שיצא לדרך, רק למגירה
  שהדיווח האחרון שלה `writing`. בקשה שנייה — 409 עם מי שביטל.
- הבקשה נמסרת לסוכן בתשובת דיווח ההתקדמות (`cancel: [{dev, who_he}]`).
- "בוטל על ידי ‹משתמש›" נרשם ביומן כשהסוכן מדווח `cancelled` — לא לפני.
- בוטל אינו נכשל: אין `disk_failures`, ובסבב הבא הדיסק ירוק. בסבב הזה הוא סגור (#1936).
- ביטול ממסך מחשב השיכפול (בלי בקשה בשרת) נרשם כ"ממסך מחשב השיכפול".
"""

from __future__ import annotations

from conftest import room_slots
from test_kiosk_app import kiosk_env  # noqa: F401 — fixture
from test_server_room import (CLONER1, CLONER2, cloner_hello, room, room_body,  # noqa: F401
                              room_server)

CANCEL = "/api/console/room/drawer/cancel"


def _progress(client, wave: str, mac: str, states: dict[str, str], top: str = "writing"):
    r = client.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": mac, "state": top,
        "targets": [{"dev": dev, "bytes_written": 100 if st == "done" else 40,
                     "bytes_total": 100, "state": st} for dev, st in states.items()],
    })
    assert r.status_code == 200, r.text
    return r.json()


def _running(room_server, drawers=None):
    deploy, anon = room_server["deploy"], room_server["anon"]
    drawers = drawers or {CLONER1: ["S1", "S2"]}
    assert deploy.post("/api/console/room", json=room_body(room_server, 4, drawers)).status_code == 200
    wave = None
    for mac, serials in drawers.items():
        wave = cloner_hello(anon, mac, serials)["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    return wave


def _journal(ctx, event):
    return [(r["detail"], r["user"]) for r in ctx.conn.execute(
        "SELECT detail, user FROM journal WHERE event = ? ORDER BY id", (event,))]


def _drawer(view, mac, dev):
    m = next(m for m in view["machines"] if m["mac"] == mac)
    return next(d for d in m["drawer_list"] if d["dev"] == dev)


def test_cancel_reaches_the_agent_and_is_journaled_only_on_its_evidence(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _running(room_server)
    assert "cancel" not in _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})

    r = deploy.post(CANCEL, json={"mac": CLONER1.upper(), "dev": "sdb"})
    assert r.status_code == 200, r.text
    assert _journal(ctx, "room_drawer_cancel_requested")[-1][1] == "labtech"
    assert _journal(ctx, "room_drawer_cancelled") == [], "a request is not evidence"

    # the next report's answer carries it -- only sdb, with who asked
    reply = _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    # (the combined test app answers on the agent side first -- "build"; the split
    # apps, console vs kiosk, are the last test)
    assert [c["dev"] for c in reply["cancel"]] == ["sdb"] and "labtech" in reply["cancel"][0]["who_he"]
    pending = _drawer(room(deploy), CLONER1, "sdb")["cancel"]
    assert pending["done"] is False and pending["by"] == "labtech"
    assert _drawer(room(deploy), CLONER1, "sda")["cancel"] is None

    # the agent reports it: now "בוטל על ידי labtech", once
    reply = _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "cancelled"})
    assert "cancel" not in reply, "a confirmed cancel is not sent again"
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "cancelled"})
    done = _journal(ctx, "room_drawer_cancelled")
    assert len(done) == 1 and "בוטל" in done[0][0] and "labtech" in done[0][0] and done[0][1] == "labtech"
    view = _drawer(room(deploy), CLONER1, "sdb")
    assert view["state"] == "cancelled" and view["cancel"]["done"] is True
    assert view["cancel"]["text_he"].startswith("בוטל ") and "labtech" in view["cancel"]["text_he"]


def test_a_cancelled_drawer_is_not_a_failure_and_is_closed_in_this_round(room_server):
    """Not a failure: no `disk_failures`, no red memory, not counted as written. And
    (#1936, FI O2) not written again in a later wave of the same round -- the cancel is
    final for the round; the *next round* writes it as a normal green disk."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _running(room_server)
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert deploy.post(CANCEL, json={"mac": CLONER1, "dev": "sdb"}).status_code == 200
    _progress(anon, wave, CLONER1, {"sda": "done", "sdb": "cancelled"}, top="done")
    assert ctx.conn.execute("SELECT COUNT(*) FROM disk_failures").fetchone()[0] == 0
    # the next hello: no red memory for S2 (failed_last), and S2 is fresh -- green
    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert not answer.get("disk_failures")
    rnd = ctx.conn.execute("SELECT written_serials FROM room_rounds").fetchone()
    assert "S2" not in rnd["written_serials"]
    sdb = _drawer(room(deploy), CLONER1, "sdb")
    assert (sdb["fresh"], sdb["cancelled_round"]) == (False, True), sdb


def test_refusals_say_why(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post(CANCEL, json={"mac": CLONER1, "dev": "sdb"}).status_code == 409   # no round
    assert deploy.post("/api/console/room", json=room_body(room_server, 4, {CLONER1: ["S1", "S2"]})).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    r = deploy.post(CANCEL, json={"mac": CLONER1, "dev": "sdb"})
    assert r.status_code == 409 and "לא יצא" in r.json()["detail"]          # wave still open
    assert deploy.post("/api/console/room/start").status_code == 200
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "done"})
    r = deploy.post(CANCEL, json={"mac": CLONER1, "dev": "sdb"})
    assert r.status_code == 409 and "אינו כותב" in r.json()["detail"]        # done: nothing to cancel
    r = deploy.post(CANCEL, json={"mac": CLONER2, "dev": "sda"})
    assert r.status_code == 409 and "אינו בגל" in r.json()["detail"]
    for bad in ({"mac": CLONER1}, {"dev": "sda"}, {"mac": "zz", "dev": "sda"},
                {"mac": CLONER1, "dev": "../sda"}, ["sda"]):
        assert deploy.post(CANCEL, json=bad).status_code == 400, bad
    assert deploy.post(CANCEL, content=b"not json").status_code == 400
    assert deploy.post(CANCEL, json={"mac": CLONER1, "dev": "sda"}).status_code == 200
    second = room_server["admin"].post(CANCEL, json={"mac": CLONER1, "dev": "sda"})
    assert second.status_code == 409 and "labtech" in second.json()["detail"]


def test_only_a_round_operator_signed_in_may_cancel(room_server, monkeypatch):
    from server.rounds import room as room_module                  # noqa: PLC0415
    anon = room_server["anon"]
    wave = _running(room_server)
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert anon.post(CANCEL, json={"mac": CLONER1, "dev": "sda"}).status_code in (401, 403)
    assert room_module.ROOM_OPERATOR_ROLES == ("admin", "deploy")
    # a role outside the allow-list is refused by room_operator, like /wake (#94)
    monkeypatch.setattr(room_module, "ROOM_OPERATOR_ROLES", ("admin",))
    r = room_server["deploy"].post(CANCEL, json={"mac": CLONER1, "dev": "sda"})
    assert r.status_code == 403
    assert room_server["ctx"].conn.execute("SELECT COUNT(*) FROM drawer_cancels").fetchone()[0] == 0


def test_a_cancel_from_the_cloner_screen_is_recorded_as_such(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    wave = _running(room_server)
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "cancelled"})   # no request: the machine's own key
    row = ctx.conn.execute("SELECT * FROM drawer_cancels").fetchone()
    assert row["source"] == "cloner" and row["requested_by"] is None and row["done_at"]
    assert "ממסך מחשב השיכפול" in _journal(ctx, "room_drawer_cancelled")[0][0]
    assert _drawer(room(deploy), CLONER1, "sdb")["cancel"]["text_he"].startswith("בוטל ממסך מחשב השיכפול")


def test_console_and_build_machine_are_told_apart(kiosk_env, monkeypatch):  # noqa: F811
    """The split apps of production: the console (:8081) records "על ידי", the
    kiosk of the build machine (:8080) "ממחשב הבנייה" -- same RBAC, same route."""
    from fastapi.testclient import TestClient                # noqa: PLC0415
    from server.rounds import sender as sender_module               # noqa: PLC0415
    monkeypatch.setattr(sender_module, "port_holders", lambda _port: [])   # as room_server: no /proc/net/udp here
    rt = kiosk_env["rt"]
    mac = "aa:bb:cc:00:00:20"
    kiosk = kiosk_env["kiosk"]
    console = TestClient(kiosk_env["console_app"])
    # deploy signs in on the kiosk; the console refuses deploy (#1073) -- the admin there
    assert kiosk.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    assert console.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    image = kiosk.get("/api/console/images").json()
    image = (image.get("images") if isinstance(image, dict) else image)[0]["id"]
    body = {"image_id": image, "target_drives": 2, "target_slots": room_slots(rt.conn, {mac: ["S1", "S2"]})}
    opened = kiosk.post("/api/console/room", json=body)
    assert opened.status_code == 200, opened.text
    joined = cloner_hello(kiosk_env["anon"], mac, ["S1", "S2"])["session"]
    assert joined["state"] == "running"   # #1703: the hello that covers the target starts the wave
    wave = joined["id"]
    _progress(kiosk_env["anon"], wave, mac, {"sda": "writing", "sdb": "writing"})
    r = kiosk.post(CANCEL, json={"mac": mac, "dev": "sda"})
    assert r.status_code == 200, r.text
    assert console.post(CANCEL, json={"mac": mac, "dev": "sdb"}).status_code == 200
    rows = {r["dev"]: r for r in rt.conn.execute("SELECT * FROM drawer_cancels")}
    assert rows["sda"]["source"] == "build" and rows["sdb"]["source"] == "console"
    assert rows["sda"]["requested_by"] == "labtech" and rows["sdb"]["requested_by"] == "noc"
    reply = _progress(kiosk_env["anon"], wave, mac, {"sda": "writing", "sdb": "writing"})
    assert reply["cancel"] == [{"dev": "sda", "who_he": "ממחשב הבנייה (labtech)"},
                               {"dev": "sdb", "who_he": "על ידי noc"}]
