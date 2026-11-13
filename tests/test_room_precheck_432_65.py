"""#432 + #65 — מה שמסך החדר אומר לפני שגל יוצא לדרך.

#432: כל מגירה נושאת את מהירות הקישור, את המקסימום של הפורט ואת עליית
מונה ה-CRC מאז הסבב הקודם שלה; גל פתוח מציג **אזהרה כתומה** על מגירה איטית
או שה-CRC שלה עלה — אזהרה ולא חסימה, וגל שיצא בכל זאת נרשם ביומן. קישור
שלא נקרא הוא `unknown` — לעולם לא `ok` (עיקרון 5).

#65: גל שמחכה ליותר מגירות ממה שהצטרפו אומר **מי** חסר ולמה, ו"כבה אותו"
מכבה מכונה אחת כזו — דרך הסוכן (`power_action`), רק בגל פתוח.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import hello_body, room_slots
from test_server_room import CLONER1, CLONER2, room, room_server  # noqa: F401 — fixture


def drawers_hello(client, mac: str, disks: list[dict], joining: bool | None = None) -> dict:
    """hello של משכפל; כל דיסק: serial, port ושדות #432 כפי שהסוכן שולח."""
    body = hello_body(mac)
    if joining is not None:
        body["joining"] = joining
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
         "removable": False, "scheme": "gpt", "has_data": False, **d}
        for i, d in enumerate(disks)
    ]
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def disk(serial: str, port: int, link=6.0, top=6.0, crc=None) -> dict:
    return {"serial": serial, "port": port, "link_gbps": link, "link_max_gbps": top, "crc": crc}


def machine(view: dict, mac: str) -> dict:
    return next(m for m in view["machines"] if m["mac"] == mac)


def journal(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ?", (event,))]


# --- #432: השדות של כל מגירה --------------------------------------------------


def test_each_drawer_carries_its_link_and_a_state(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2, link=1.5),
                                  disk("S3", 3, link=None), {"serial": "S4", "port": 4}])
    got = {d["serial"]: (d["link_gbps"], d["link_max_gbps"], d["link_state"])
           for d in machine(room(admin), CLONER1)["drawer_list"]}
    assert got == {"S1": (6.0, 6.0, "ok"), "S2": (1.5, 6.0, "slow"),
                   "S3": (None, 6.0, "unknown"),
                   "S4": (None, None, "unknown")}          # סוכן ישן: לא "תקין"


@pytest.mark.parametrize("bad", [True, "6.0", 0, -3])
def test_a_malformed_speed_is_unknown_not_a_number(room_server, bad):
    admin, anon = room_server["admin"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1, link=bad)])
    d = machine(room(admin), CLONER1)["drawer_list"][0]
    assert d["link_gbps"] is None and d["link_state"] == "unknown"


def _disk_event(ctx, serial: str, crc: int, verdict="ok", reason="ok", session="ses_old"):
    ctx.conn.execute(
        "INSERT INTO disk_events (session_id, mac, disk, port, serial, verdict, reason,"
        " crc, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (session, CLONER1, "sda", 1, serial, verdict, reason, crc,
         datetime.now(timezone.utc).isoformat()))
    ctx.conn.commit()


def test_crc_rise_is_measured_against_the_disks_last_round(room_server):
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    _disk_event(ctx, "S1", 100)
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=112), disk("S2", 2, crc=40)])
    got = {d["serial"]: (d["crc"], d["crc_rise"]) for d in machine(room(admin), CLONER1)["drawer_list"]}
    assert got == {"S1": (112, 12), "S2": (40, None)}      # S2: אין סבב קודם — לא נמדד, לא 0


@pytest.mark.parametrize("verdict, reason", [("unchecked", "open_failed"),
                                             ("unchecked", "no_smartctl"),
                                             ("failed_last", "cable")])
def test_a_baseline_smartctl_never_read_is_no_baseline(room_server, verdict, reason):
    """disk_events שומר 0 ל-CRC שלא נקרא. 0 כזה אינו בסיס: אחרת כל דיסק עם
    היסטוריה של 137 שגיאות היה "עולה ב-137" בסבב הראשון."""
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    _disk_event(ctx, "S1", 0, verdict=verdict, reason=reason)
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=137)])
    assert machine(room(admin), CLONER1)["drawer_list"][0]["crc_rise"] is None


# --- #432: האזהרה לפני "התחל" -------------------------------------------------


def _open(client, target=2, slots=None):
    body = {"image_id": "img_7f3a91", "target_drives": target}
    if slots is not None:
        body["target_slots"] = slots
    r = client.post("/api/console/room", json=body)
    assert r.status_code == 200, r.text


def test_an_open_wave_warns_on_a_slow_drawer_and_not_on_the_others(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    # #1703: target 5 -- four drawers do not cover it, the wave stays open
    _open(deploy, target=5, slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2, link=1.5)])
    drawers_hello(anon, CLONER2, [disk("S3", 1, link=None), disk("S4", 2, link=3.0, top=3.0)])
    warn = room(deploy)["round"]["precheck"]
    assert [(w["mac"], w["port"]) for w in warn] == [(CLONER1, 2)], warn
    assert "shich-1" in warn[0]["text_he"] and "1.5 מתוך 6.0" in warn[0]["text_he"]


def test_a_crc_rise_warns_too(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _disk_event(ctx, "S1", 100)
    _open(deploy, target=4, slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1, crc=112)])
    (w,) = room(deploy)["round"]["precheck"]
    assert w["crc_rise"] == 12 and "מונה CRC עלה ב-12" in w["text_he"]


def test_no_round_and_a_clean_wave_have_no_warning(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1, link=1.5)])
    assert room(deploy)["round"] is None
    _open(deploy, target=4, slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    assert room(deploy)["round"]["precheck"] == []


def test_a_drawer_outside_the_selection_does_not_warn(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2, link=1.5)])
    _open(deploy, target=1, slots=[{"mac": CLONER1, "ports": [1]}])
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2, link=1.5)])
    assert room(deploy)["round"]["precheck"] == []


def test_start_is_a_warning_not_a_block_and_the_journal_keeps_it(room_server):
    """ה-Issue: "סבב שהתחיל בכל זאת חייב לרשום שהאזהרה הייתה שם"."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _open(deploy, target=4, slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1", "S2"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2, link=1.5)])
    r = deploy.post("/api/console/room/start")
    assert r.status_code == 200 and r.json()["warned"] == 1, r.text
    assert room(deploy)["round"]["wave_state"] == "running"
    (detail,) = journal(ctx, "room_precheck_warned")
    assert f"{CLONER1}:2 link=1.5/6.0" in detail, detail


def test_a_clean_start_writes_no_warning_line(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _open(deploy, target=4, slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    assert deploy.post("/api/console/room/start").json()["warned"] == 0
    assert journal(ctx, "room_precheck_warned") == []


def test_a_wave_that_starts_by_itself_keeps_the_warning_too(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _open(deploy, target=1, slots=room_slots(room_server["ctx"].conn, {CLONER1: ["S1"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1, link=3.0)])
    drawers_hello(anon, CLONER1, [disk("S1", 1, link=3.0)])   # tick: 1 מוכנה מול יתרה 1
    assert room(deploy)["round"]["wave_state"] == "running"
    assert any("link=3.0/6.0" in d for d in journal(ctx, "room_precheck_warned"))


# --- #65: מי חסר ------------------------------------------------------------


def _asleep(ctx, mac: str) -> None:
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    ctx.conn.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ?", (old, mac))
    ctx.conn.commit()


def _slots_round(room_server):
    """שלוש מגירות נבחרו: שתיים ב-shich-1, אחת ב-shich-2. shich-1 מצטרף עם
    מגירה 1 בלבד (2 נשלפה), shich-2 כבוי."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2)])
    drawers_hello(anon, CLONER2, [disk("S3", 1)])
    _open(deploy, target=3, slots=[{"mac": CLONER1, "ports": [1, 2]}, {"mac": CLONER2, "ports": [1]}])
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    _asleep(ctx, CLONER2)
    return deploy, anon, ctx


def test_the_missing_machines_are_named_with_the_reason(room_server):
    deploy, _anon, _ctx = _slots_round(room_server)
    r = room(deploy)["round"]
    assert r["ready_drives"] == 1
    got = {m["mac"]: (m["reason"], m["ports"], m["can_poweroff"]) for m in r["missing"]}
    assert got == {CLONER1: ("drawers", [2], True), CLONER2: ("off", [1], False)}, r["missing"]
    texts = " | ".join(m["text_he"] for m in r["missing"])
    assert "shich-1: אין דיסק חדש בדיסק 2" in texts and "shich-2 כבוי" in texts


def test_an_awake_machine_that_did_not_join_is_not_joined(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    drawers_hello(anon, CLONER2, [disk("S3", 1)])
    _open(deploy, target=2, slots=[{"mac": CLONER1, "ports": [1]}, {"mac": CLONER2, "ports": [1]}])
    drawers_hello(anon, CLONER1, [disk("S1", 1)])
    drawers_hello(anon, CLONER2, [disk("S3", 1)], joining=False)    # דופק בלבד — לא מצטרף
    (m,) = room(deploy)["round"]["missing"]
    assert (m["mac"], m["reason"], m["can_poweroff"]) == (CLONER2, "not_joined", True)


def test_nothing_is_missing_when_the_ready_drawers_cover_the_wave(room_server):
    deploy, anon, _ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2)])
    _open(deploy, target=5, slots=[{"mac": CLONER1, "ports": [1, 2]}])
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2)])
    r = room(deploy)["round"]
    assert r["missing"] == [], r["missing"]        # גל של 2 מתוך יעד 5 — לא חסר בו דבר


def test_a_selected_cloner_that_never_said_hello_is_listed_as_off(room_server):
    """#1410: אין עוד סבב בלי בחירה — משכפל שנבחר ולא הגיע מופיע כחסר."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    _open(deploy, target=4, slots=room_slots(ctx.conn, {
        CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    drawers_hello(anon, CLONER1, [disk("S1", 1), disk("S2", 2)])
    (m,) = room(deploy)["round"]["missing"]
    assert (m["mac"], m["reason"]) == (CLONER2, "off")


# --- #65: "כבה אותו" ---------------------------------------------------------


def _poweroff(client, mac, name):
    return client.post(f"/api/console/room/missing/{mac}/poweroff", json={"confirm_name": name})


def test_power_off_a_missing_machine_rides_its_next_hello_and_leaves_the_wave(room_server):
    deploy, anon, ctx = _slots_round(room_server)
    r = _poweroff(deploy, CLONER1, "shich-1")
    assert r.status_code == 200 and r.json()["requested"] == 1, r.text
    assert any(CLONER1 in d for d in journal(ctx, "room_missing_poweroff"))
    answer = drawers_hello(anon, CLONER1, [disk("S1", 1)])
    assert answer.get("power_action") == "poweroff"
    # הדיסק שדיווחה לפני שכבתה אינו נספר עוד — "התחל בכל זאת" לא יכלול מכונה כבויה.
    assert room(deploy)["round"]["ready_drives"] == 0
    wave = ctx.conn.execute(
        "SELECT wave_session_id FROM room_rounds WHERE state = 'active'").fetchone()[0]
    assert not ctx.store.is_member(wave, CLONER1)


def test_power_off_is_refused_for_a_machine_that_is_not_missing_or_cannot_hear(room_server):
    deploy, anon, ctx = _slots_round(room_server)
    assert _poweroff(deploy, CLONER2, "shich-2").status_code == 409     # כבוי — אין למי לשלוח
    assert _poweroff(deploy, "aa:bb:cc:00:00:99", "x").status_code == 409
    assert _poweroff(deploy, CLONER1, "shich-2").status_code == 400     # שם אחר
    assert _poweroff(deploy, "not-a-mac", "x").status_code == 400
    assert _poweroff(room_server["anon"], CLONER1, "shich-1").status_code == 401
    assert ctx.conn.execute("SELECT COUNT(*) FROM power_requests").fetchone()[0] == 0


def test_power_off_is_refused_once_the_wave_runs(room_server):
    deploy, anon, ctx = _slots_round(room_server)
    assert deploy.post("/api/console/room/start").status_code == 200   # "התחל בכל זאת"
    r = _poweroff(deploy, CLONER1, "shich-1")
    assert r.status_code == 409 and "יצא לדרך" in r.json()["detail"], r.text   # הסיבה הנכונה, לא "אינו חסר"
    assert ctx.conn.execute("SELECT COUNT(*) FROM power_requests").fetchone()[0] == 0


def test_power_off_with_no_round_is_refused(room_server):
    assert _poweroff(room_server["deploy"], CLONER1, "shich-1").status_code == 409

