"""#3181 (נדב 14/11) — פס ייצור בשרת, והמוח של "כמה חסר" (K5).

פס ייצור = סבב שמבקשים בו יותר דיסקים ממספר המגירות במשכפלים. אין מתג: אותו `POST /room`, והשרת מכריע.
כאן: המעבר בגבול (9 מגירות ו-9 דיסקים, 9 ו-10), השמות בתור, המונה "נכתבו X מתוך Y", "אפשר להוציא" רק אחרי
ניתוק בטוח, "הכנס דיסק" עם השם הבא, דיסק חדש שמקבל אותו, ההחלטה לפי כמה חסר, ותפקיד deploy.

ההתנהגות נבדקת דרך ה-API (פתיחה, hello, progress, GET /room), כדי שהבקרה השלילית תיפול על התנהגות.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_server_room import CLONER1, CLONER2, cloner_hello, room, room_server  # noqa: E402,F401

A = ["A1", "A2", "A3", "A4", "A5"]
B = ["B1", "B2", "B3", "B4"]
SLOTS = [{"mac": CLONER1, "ports": [1, 2, 3, 4, 5]}, {"mac": CLONER2, "ports": [1, 2, 3, 4]}]
FIRST = [{"mac": CLONER1, "port": p, "name": f"LAB-0{p}"} for p in range(1, 6)] + \
        [{"mac": CLONER2, "port": p, "name": f"LAB-0{p + 5}"} for p in range(1, 5)]


def _nine(rs) -> None:
    """שני משכפלים, 5 ו-4 מגירות = 9, ודיסק בכל מגירה."""
    admin, anon = rs["admin"], rs["anon"]
    assert admin.put(f"/api/console/machines/{CLONER1}", json={"drawer_count": 5}).status_code == 200
    assert admin.put(f"/api/console/machines/{CLONER2}", json={"drawer_count": 4}).status_code == 200
    cloner_hello(anon, CLONER1, A)
    cloner_hello(anon, CLONER2, B)


def _open(client, target, hostnames=None):
    body = {"image_id": "img_7f3a91", "target_drives": target, "target_slots": SLOTS}
    if hostnames is not None:
        body["hostnames"] = hostnames
    return client.post("/api/console/room", json=body)


def _queue(n):
    return [f"LAB-{10 + i}" for i in range(n)]


def _wave(rs) -> str:
    """שני המשכפלים מצטרפים לגל הפתוח (hello), ומזהה הגל."""
    cloner_hello(rs["anon"], CLONER1, A)
    cloner_hello(rs["anon"], CLONER2, B)
    return room(rs["deploy"])["round"]["wave_session_id"]


def _t(dev, serial, port, state="writing", **extra):
    return {"dev": dev, "serial": serial, "port": port, "state": state,
            "bytes_written": 100 if state == "done" else 10, "bytes_total": 100, **extra}


def _progress(rs, wave, mac, targets):
    answer = rs["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": mac, "state": "writing", "targets": targets})
    assert answer.status_code == 200, answer.text
    return answer.json()


# --- המעבר האוטומטי, בגבול -------------------------------------------------------------------------------


def test_nine_drawers_and_nine_disks_is_a_regular_round(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 9).status_code == 200
    assert room(room_server["deploy"])["round"]["production_line"] is None
    session = cloner_hello(room_server["anon"], CLONER1, A)["session"]
    assert session["production_line"] is None
    wave = room(room_server["deploy"])["round"]["wave_session_id"]
    assert "production_line" not in _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1)])


def test_nine_drawers_and_ten_disks_is_a_production_line_by_itself(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 10).status_code == 200
    line = room(room_server["deploy"])["round"]["production_line"]
    assert line is not None
    assert (line["drawers"], line["target"], line["written"], line["remaining"]) == (9, 10, 0, 10)
    assert len(line["disks"]) == 10
    events = room_server["admin"].get("/api/console/journal").json()
    opened = next(e for e in events if e["event"] == "room_open")
    assert "line=drawers:9" in opened["text"]
    session = cloner_hello(room_server["anon"], CLONER1, A)["session"]
    assert session["production_line"]["target"] == 10


# --- השמות: שם לכל דיסק שביקשו -------------------------------------------------------------------------------


def test_a_production_line_takes_a_name_for_every_disk_asked(room_server):
    _nine(room_server)
    answer = _open(room_server["deploy"], 11, {"names": FIRST, "queue": _queue(2)})
    assert answer.status_code == 200, answer.json()
    line = room(room_server["deploy"])["round"]["production_line"]
    assert [d["name"] for d in line["disks"]] == [n["name"] for n in FIRST] + ["LAB-10", "LAB-11"]
    assert line["next_name"] == "LAB-10" and line["queue_left"] == 2


@pytest.mark.parametrize("hostnames, fragment", [
    ({"names": FIRST}, "שם לכל דיסק שביקשו"),                                  # בלי תור: "גל אחד" כבר אינו הסיבה
    ({"names": FIRST, "queue": _queue(1)}, "ונשלחו 10"),                       # 11 ביקשו
    ({"names": FIRST, "queue": ["LAB-10", "LAB-01"]}, "פעמיים"),
    ({"names": FIRST, "queue": ["LAB-10", "LAB 11"]}, "אותיות אנגליות"),
])
def test_a_production_line_refuses_names_that_do_not_cover_the_round(room_server, hostnames, fragment):
    _nine(room_server)
    answer = _open(room_server["deploy"], 11, hostnames)
    assert answer.status_code == 400
    assert fragment in answer.json()["detail"], answer.json()
    assert room(room_server["deploy"])["round"] is None


def test_a_queue_in_a_regular_round_is_refused(room_server):
    _nine(room_server)
    answer = _open(room_server["deploy"], 9, {"names": FIRST, "queue": ["LAB-10"]})
    assert answer.status_code == 400
    assert "רק לפס ייצור" in answer.json()["detail"]


# --- המונה, "אפשר להוציא", "הכנס דיסק", ודיסק חדש -----------------------------------------------------------------


def test_done_is_counted_but_only_a_safe_detach_says_it_can_come_out(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 11, {"names": FIRST, "queue": _queue(2)}).status_code == 200
    wave = _wave(room_server)
    got = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done"), _t("sdb", "A2", 2)])
    assert got["production_line"]["written"] == 1 and got["production_line"]["target"] == 11
    assert got["production_line"]["removable"] == [], "נכתב אינו 'אפשר להוציא'"
    got = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done", detached=True), _t("sdb", "A2", 2)])
    assert got["production_line"]["removable"] == [1]
    assert got["production_line"]["written"] == 1, "אותו דיסק אינו נספר פעמיים"
    line = room(room_server["deploy"])["round"]["production_line"]
    assert next(d for d in line["disks"] if d["serial"] == "A1")["state"] == "removable"
    assert line["written"] == 1 and line["remaining"] == 10


def test_an_emptied_drawer_waits_with_the_next_name_and_the_new_disk_gets_it(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 11, {"names": FIRST, "queue": _queue(2)}).status_code == 200
    wave = _wave(room_server)
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done", detached=True)])
    got = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done", detached=True, removed=True)])
    assert got["production_line"]["insert"] == {"1": "LAB-10"}, "המגירה הריקה שומרת את השם הבא"
    assert got["production_line"]["next_name"] == "LAB-11"
    got = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done", removed=True),
                                                  _t("sdf", "N1", 1, "writing")])
    assert got["production_line"]["names"]["N1"] == "LAB-10"
    assert got["production_line"]["insert"] == {}
    assert got["production_line"]["written"] == 1
    # מגירה שהתרוקנה במשכפל השני מקבלת את הבא אחריו, לא את אותו שם
    _progress(room_server, wave, CLONER2, [_t("sda", "B1", 1, "done", detached=True)])
    got = _progress(room_server, wave, CLONER2, [_t("sda", "B1", 1, "done", removed=True)])
    assert got["production_line"]["insert"] == {"1": "LAB-11"}
    assert got["production_line"]["written"] == 2


def test_a_failed_disk_keeps_its_name_and_never_takes_one(room_server):
    """B22 (נדב 15/11, כמו במוקאפ של #2786): השם נקשר לדיסק כשהוא נכנס, ודיסק שנכשל שומר אותו -- המדבקה."""
    _nine(room_server)
    assert _open(room_server["deploy"], 10, {"names": FIRST, "queue": _queue(1)}).status_code == 200
    wave = _wave(room_server)
    got = _progress(room_server, wave, CLONER1, [_t("sdb", "A2", 2, "failed", error="I/O error")])
    assert got["production_line"]["next_name"] == "LAB-10", "השם של הנכשל אינו חוזר לתור"
    assert "A2" not in got["production_line"]["names"]
    disks = room(room_server["deploy"])["round"]["production_line"]["disks"]
    assert {"name": "LAB-02", "serial": "A2", "state": "failed"}.items() <= next(
        d for d in disks if d["serial"] == "A2").items()
    # אותו דיסק שחוזר לכתיבה ממשיך עם אותו שם
    got = _progress(room_server, wave, CLONER1, [_t("sdb", "A2", 2, "writing")])
    assert got["production_line"]["names"]["A2"] == "LAB-02"
    # דיסק נכשל שלא היה בסבב אינו לוקח שם
    got = _progress(room_server, wave, CLONER1, [_t("sdg", "X9", 2, "failed", error="I/O error")])
    assert "X9" not in got["production_line"]["names"] and got["production_line"]["refused"] == []


def test_a_disk_in_place_of_a_failed_one_takes_the_next_name_and_then_none(room_server):
    """B22: הנכשל שומר את שמו, ולכן הדיסק שבא במקומו לוקח את הבא בתור. נגמר התור -- נכתב בלי שם, בגלוי,
    ולא נדחה: אחרת סבב שנכשל בו דיסק לא היה מגיע ליעד."""
    _nine(room_server)
    assert _open(room_server["deploy"], 10, {"names": FIRST, "queue": _queue(1)}).status_code == 200
    wave = _wave(room_server)
    _progress(room_server, wave, CLONER2, [_t("sdf", "Z1", 1, "writing")])          # LAB-10, האחרון בתור
    _progress(room_server, wave, CLONER1, [_t("sdb", "A2", 2, "failed", error="I/O error")])
    got = _progress(room_server, wave, CLONER1, [_t("sdb", "A2", 2, "failed", error="I/O error"),
                                                  _t("sdh", "N2", 2, "writing")])
    assert got["production_line"]["refused"] == [], "מקום שהנכשל השאיר ביעד אינו 'הסבב קיבל הכול'"
    assert "N2" not in got["production_line"]["names"], "אין שם פנוי: לא לוקחים את LAB-02 של הנכשל"
    disks = room(room_server["deploy"])["round"]["production_line"]["disks"]
    assert next(d for d in disks if d["serial"] == "N2")["name"] is None
    assert next(d for d in disks if d["serial"] == "A2")["name"] == "LAB-02"
    events = room_server["admin"].get("/api/console/journal").json()
    assert any(e["event"] == "prodline_unnamed" for e in events)
    # ואין מקום שני: דיסק נוסף נדחה כמו קודם
    got = _progress(room_server, wave, CLONER1, [_t("sdh", "N2", 2, "writing"), _t("sdi", "N3", 3, "writing")])
    assert got["production_line"]["refused"] == ["N3"]


def test_a_disk_beyond_what_was_asked_is_refused_by_name(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 10, {"names": FIRST, "queue": _queue(1)}).status_code == 200
    wave = _wave(room_server)
    got = _progress(room_server, wave, CLONER2, [_t("sdf", "Z1", 1, "writing")])
    assert got["production_line"]["names"]["Z1"] == "LAB-10"
    got = _progress(room_server, wave, CLONER2, [_t("sdf", "Z1", 1, "writing"), _t("sdg", "Z2", 2, "writing")])
    assert got["production_line"]["refused"] == ["Z2"]
    events = room_server["admin"].get("/api/console/journal").json()
    assert any(e["event"] == "prodline_full" for e in events)


def test_a_disk_that_came_out_is_still_counted_at_the_end_of_the_wave(room_server):
    from server.rounds import room as room_module                         # noqa: PLC0415

    _nine(room_server)
    assert _open(room_server["deploy"], 10).status_code == 200
    wave = _wave(room_server)
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done", detached=True)])
    _progress(room_server, wave, CLONER1, [_t("sdb", "A2", 2)])     # A1 כבר אינו בדיווח
    ctx = room_server["ctx"]
    round_row = ctx.conn.execute("SELECT * FROM room_rounds WHERE state = 'active'").fetchone()
    total, written, _skipped, _cancelled = room_module._tally(ctx.conn, round_row, ctx.store.members(wave))
    assert "A1" in written and total == 1
    assert room(room_server["deploy"])["round"]["production_line"]["written"] == 1


def test_a_report_without_serial_or_port_is_not_a_new_disk(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 10, {"names": FIRST, "queue": _queue(1)}).status_code == 200
    wave = _wave(room_server)
    got = _progress(room_server, wave, CLONER1, [{"dev": "sdz", "state": "writing",
                                                   "bytes_written": 1, "bytes_total": 100}])
    assert got["production_line"]["names"].keys() <= set(A) and got["production_line"]["next_name"] == "LAB-10"


# --- תפקיד deploy --------------------------------------------------------------------------------------------


def test_deploy_opens_a_production_line_and_sees_it_and_an_anonymous_client_does_not(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 10).status_code == 200
    assert room(room_server["deploy"])["round"]["production_line"]["target"] == 10
    assert room_server["anon"].get("/api/console/room").status_code in (401, 403)
    assert _open(room_server["anon"], 10).status_code in (401, 403)


# --- המוח: ההחלטה לפי כמה חסר (K5) ----------------------------------------------------------------------------


def test_the_decision_follows_how_much_is_missing():
    from server.brain import fill                                           # noqa: PLC0415

    budget = fill.CEILING_BPS
    few = fill.decide(fill.Need(fill.FEW_MAX_CHUNKS), ["sdb", "sdc"], 0, 9, budget)
    assert (few.verdict, few.source, few.neighbors, few.per_neighbor_bps) == ("FEW", "NEIGHBOR", ("sdb",), budget)
    many = fill.decide(fill.Need(fill.FEW_MAX_CHUNKS + 1), ["sdb", "sdc"], 1, 9, budget)
    assert (many.verdict, many.source, many.neighbors) == ("MANY", "NEIGHBORS", ("sdb", "sdc"))
    assert many.per_neighbor_bps == budget // 2, "התקציב מתחלק בין השכנות"
    assert fill.decide(fill.Need(5), [], 0, 9, budget).source == "HTTP"
    assert fill.decide(fill.Need(500), [], 0, 9, budget).source == "HTTP"
    assert fill.decide(fill.Need(0), ["sdb"], 0, 9, budget).source == "NONE"
    # הרבה מגירות: לפחות 2, ולפחות מחצית מהסבב
    assert fill.decide(fill.Need(500), ["sdb"], 5, 9, budget).verdict == "REPASS"
    assert fill.decide(fill.Need(500), ["sdb"], 4, 9, budget).verdict == "MANY"
    assert fill.decide(fill.Need(500), ["sdb"], 1, 2, budget).verdict == "MANY"
    assert fill.decide(fill.Need(500), ["sdb"], 2, 2, budget).verdict == "REPASS"
    assert fill.decide(fill.Need(10), ["sdb"], 9, 9, budget).verdict == "FEW", "מעט חסר אינו מעבר נוסף"


def test_the_fill_budget_is_100mib_per_cloner_and_drops_on_rcvbuf_or_skipped():
    from server.brain import fill                                           # noqa: PLC0415

    assert fill.CEILING_BPS == 100 * 1024 * 1024
    assert fill.step(None, 0, 0)[0] == fill.CEILING_BPS, "לעולם לא מעל התקרה"
    assert fill.step(fill.CEILING_BPS, 3, 0)[:2] == (fill.CEILING_BPS // 2, "CUT")
    assert fill.step(fill.CEILING_BPS, 0, 7)[:2] == (fill.CEILING_BPS // 2, "CUT")
    assert fill.step(fill.CEILING_BPS // 2, None, None)[:2] == (fill.CEILING_BPS // 2, "HOLD"), "לא נמדד אינו נקי"
    assert fill.step(fill.CEILING_BPS // 2, 0, None)[1] == "RAISE"
    assert fill.step(fill.FLOOR_BPS, 1, 0)[0] == fill.FLOOR_BPS


def test_the_brain_decides_in_shadow_and_the_agent_hears_nothing(room_server):
    _nine(room_server)
    assert _open(room_server["deploy"], 10).status_code == 200
    wave = _wave(room_server)
    got = _progress(room_server, wave, CLONER1, [
        _t("sda", "A1", 1, "writing", rejoin={"part": 3, "missing": 30, "chunks": 18254, "away_s": 2}),
        _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    assert "fill" not in got, "בצל הסוכן לא שומע"
    rows = room_server["ctx"].conn.execute(
        "SELECT * FROM brain_decisions WHERE rule = 'K5' AND action = 'FILL_SOURCE'").fetchall()
    assert len(rows) == 1
    assert (rows[0]["mode"], rows[0]["verdict"], rows[0]["entity_key"]) == ("shadow", "FEW", "A1")
    assert '"source=NEIGHBOR"' in rows[0]["evidence_json"]
    line = room(room_server["deploy"])["round"]["production_line"]
    assert any(d["rule"] == "K5" for d in line["decisions"]), "החלון 'החלטות המוח' רואה אותה"


def test_in_enforce_the_plan_goes_back_to_the_agent(room_server):
    from server.db import set_setting                                       # noqa: PLC0415

    _nine(room_server)
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "enforce")
    assert _open(room_server["deploy"], 10).status_code == 200
    wave = _wave(room_server)
    got = _progress(room_server, wave, CLONER1, [
        _t("sda", "A1", 1, "writing", rejoin={"part": 3, "missing": 900}),
        _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    plan = got["fill"]["A1"]
    assert (plan["verdict"], plan["source"], plan["neighbors"]) == ("MANY", "NEIGHBORS", ["sdb", "sdc"])
    assert plan["per_neighbor_bps"] == plan["budget_bps"] // 2
