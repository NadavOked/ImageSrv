"""#3064 / #3181: המוח של המצטרף -- קבלה והצטרפות של דיסקים באמצע סבב, במצב צל.

`server/brain/joiner.py` (ההחלטות, טהורות) ו-`server/brain/joinwatch.py` (האיסוף והרישום). מה נבדק:

- כל החלטה (B1, B2, B4, B17, B18, B21, B25, B41, B44, B45, B46): הפסק לפי הראיה, ו**"לא נמדד" = ``UNDECIDED``**,
  לעולם לא פסק שקט (עיקרון 5).
- הספים (X1, X17) הם נקודת התחלה, והמוח מכייל אותם ממדידות -- גם אחרי אתחול של השרת (המדידות ב-``R4 FILLED``).
- דרך ה-API: דיסק חדש באמצע פס ייצור נכנס לתור (V15) ונשפט (B1, B25), מגירה שחזרה מקבלת B2/B4/B18/B46,
  ציר הזמן (R4) נקרא מהקונסולה -- **והסוכן לא שומע דבר** (צל). במתג ``off`` לא נרשם כלום.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import pytest

from server.brain import joiner as J

# --- הספים -----------------------------------------------------------------------------------------------------


def test_a_threshold_is_its_start_value_until_three_measurements():
    assert J.x1([]) == J.Threshold(50.0, False, 0)
    assert J.x1([40, 40]).value == 50.0, "שתי מדידות אינן בסיס"
    learned = J.x1([40, 30, 50])
    assert (learned.value, learned.learned, learned.samples) == (80.0, True, 3), "פעמיים החציון"
    assert J.x1([400, 400, 400]).value == J.X1_CEIL_S, "לעולם לא מעל התקרה"
    assert J.x1([1, 1, 1]).value == J.X1_FLOOR_S
    assert J.x1([0, -3, None, True, "x"]).samples == 0, "מדידה שאינה מספר חיובי אינה מדידה"
    assert J.x17([]).value == pytest.approx(2071 / 42)
    assert J.x17([10, 20, 30]).value == 20.0


# --- B1, B25 ----------------------------------------------------------------------------------------------------


def _accept(**kw):
    facts = {"serial": "N1", "smart": "ok", "writecheck": "ok", "other_round": False, "post": J.POST_OCCUPIED,
             "actual": None}
    return J.accept(**{**facts, **kw})


def test_a_new_disk_is_accepted_only_on_positive_evidence():
    assert _accept().verdict == J.ACCEPT
    assert _accept(serial=None).verdict == J.UNDECIDED
    for kw, word in (({"smart": "fail"}, "SMART"), ({"writecheck": "failed"}, "כתיבה"),
                     ({"other_round": "ses_x"}, "ses_x")):
        call = _accept(**kw)
        assert call.verdict == J.REFUSE and word in call.reason, kw
    for kw in ({"smart": None}, {"writecheck": None}, {"writecheck": "unchecked"}, {"other_round": None}):
        assert _accept(**kw).verdict == J.UNDECIDED, f"{kw}: לא נמדד אינו 'מתקבל'"
    assert _accept(smart="fail", writecheck=None).verdict == J.REFUSE, "ראיה שלילית גוברת על חוסר"
    slow = _accept(writecheck="slow")
    assert slow.verdict == J.ACCEPT and "#3295" in slow.reason, "איטי מתקבל ומשלים אחר כך"
    assert "B25" in _accept(post=J.POST_EMPTY).reason


def test_the_slot_at_post_and_the_safe_pull():
    assert J.slot_at_post(2, frozenset({1, 2})).verdict == J.POST_OCCUPIED
    assert J.slot_at_post(3, frozenset({1, 2})).verdict == J.POST_EMPTY
    assert J.slot_at_post(3, None).verdict == J.UNDECIDED
    assert J.slot_at_post(None, frozenset({1})).verdict == J.UNDECIDED
    assert J.safe_pull(2, True).verdict == J.SAFE
    assert J.safe_pull(2, False).verdict == J.UNSAFE


# --- V15, B44 ---------------------------------------------------------------------------------------------------


def test_the_waiting_queue_is_first_come_and_says_who_gets_no_name():
    waiting = [("N3", 30.0, "aa"), ("N1", 10.0, "bb"), ("N2", 10.0, "aa")]
    call = J.order(waiting, 2)
    assert call.evidence[0] == "queue=N2,N1,N3", "לפי הזמן, ובתיקו לפי המשכפל"
    assert "בלי שם: N3" in call.reason
    assert "בלי שם" not in J.order(waiting, 3).reason
    assert J.order(waiting, None).verdict == J.UNDECIDED


# --- B2, B45, B4 -----------------------------------------------------------------------------------------------


def test_join_the_live_pass_only_with_a_tail_worth_it():
    x1 = J.x1([])
    assert J.join_now(True, 120.0, x1).verdict == J.LIVE
    assert J.join_now(True, 20.0, x1).verdict == J.NEXT_PART
    assert J.join_now(False, None, x1).verdict == J.NEXT_PART
    assert J.join_now(None, 120.0, x1).verdict == J.UNDECIDED
    assert J.join_now(True, None, x1).verdict == J.UNDECIDED, "זנב שלא נמדד אינו 'שווה'"
    assert J.join_now(True, 60.0, J.x1([40, 40, 40])).verdict == J.NEXT_PART, "X1 שנלמד (80) משנה את הפסק"


def test_a_short_partition_holds_the_joiner_for_the_next_one():
    x1 = J.x1([])
    assert J.short_part(8.0, x1).verdict == J.NEXT_PART
    assert J.short_part(600.0, x1) is None
    assert J.short_part(None, x1).verdict == J.UNDECIDED
    assert J.pass_s(18254, 100 * J.MIB) == pytest.approx(18254 * 4 / 100)
    assert J.pass_s(None, 100) is None and J.pass_s(10, None) is None


def test_a_big_gap_on_one_cloner_streams_then_fetches():
    assert J.big_gap(10, 64, 1) is None
    assert J.big_gap(900, 64, 1).verdict == J.STREAM_THEN_HTTP
    assert J.big_gap(900, 64, 2).verdict == J.REPASS_CANDIDATE


# --- B17, B18, B46 --------------------------------------------------------------------------------------------


def test_a_catchup_runs_in_the_background_or_waits_for_the_end():
    rate = J.x17([10, 10, 10])
    assert J.catchup(100, rate, 30.0).verdict == J.IN_TIME
    assert J.catchup(100, rate, 30.0).rule == "B17"
    late = J.catchup(1000, rate, 30.0)
    assert (late.rule, late.verdict) == ("B18", J.DEFER_TO_END) and "לא מחכה" in late.reason
    assert J.catchup(100, rate, None).verdict == J.UNDECIDED


def test_a_sibling_completing_shares_or_waits_by_the_budget():
    from server.brain import fill                                           # noqa: PLC0415

    assert J.sibling([], fill.CEILING_BPS, fill.CEILING_BPS) is None
    assert J.sibling([30], fill.CEILING_BPS, fill.CEILING_BPS).verdict == J.PARALLEL
    assert J.sibling([30], fill.CEILING_BPS // 2, fill.CEILING_BPS).verdict == J.SERIAL
    assert J.sibling([30], None, fill.CEILING_BPS).verdict == J.UNDECIDED


# --- B21, B41, R5 ---------------------------------------------------------------------------------------------


def test_a_drawer_that_misses_a_lot_twice_goes_to_http():
    assert J.bad_pass(0.5, [0.01, 0.02])
    assert not J.bad_pass(0.03, [0.01, 0.02]), "פחות מהרצפה אינו 'הרבה'"
    assert not J.bad_pass(0.3, [0.2, 0.2]), "כמו השכנות: הרשת, לא המגירה"
    assert J.bad_pass(0.1, []), "בלי שכנות: הרצפה"
    assert J.http_after(0, 3) is None
    assert J.http_after(1, 1).verdict == J.STAY
    assert J.http_after(J.HTTP_AFTER_PASSES, 3).verdict == J.HTTP


def test_a_late_joiner_is_exempt_from_the_slow_rule():
    assert J.late_slow(None) is None
    call = J.late_slow("הדיסק לא עמד בקצב")
    assert (call.rule, call.verdict) == ("B41", J.EXEMPT)


def test_the_completing_drawer_text():
    assert J.completing_text(2, 1393, 2322) == "משלים מגירה 2: חסרות 1,393 חתיכות, 40% כבר עליה"
    assert J.completing_text(None, 5, None) == "משלים מגירה (מספר לא ידוע): חסרות 5 חתיכות"


# --- הלמידה שורדת אתחול -----------------------------------------------------------------------------------------


def test_what_was_measured_calibrates_the_next_server(tmp_path):
    from server import db                                                   # noqa: PLC0415
    from server.brain import joinwatch, memory                              # noqa: PLC0415

    conn = db.connect(tmp_path / "learn.db")
    for n in range(3):
        memory.record(conn, mode="shadow", rule="R4", action="STAGE", verdict="FILLED", reason="x",
                      evidence=("missing0=900", "join_latency_s=40.0", "fill_cps=20.0"), round_id=f"room_{n}",
                      entity=("disk", f"S{n}"))
    learned = joinwatch.Watch()._learned(conn, joinwatch._Session())
    assert J.x1(learned["X1"]).value == 80.0
    assert J.x17(learned["X17"]).value == 20.0


# --- האיסוף: הזנב, ו-B21 לפי מעבר -------------------------------------------------------------------------------

MAC = "aa:00:00:00:00:01"


def _watch(tmp_path, name="w.db"):
    from server import db                                                   # noqa: PLC0415
    from server.brain import joinwatch                                      # noqa: PLC0415

    clock = [1000.0]
    conn = db.connect(tmp_path / name)
    w = joinwatch.Watch(clock=lambda: clock[0])
    row = {"id": "room_1", "wave_session_id": "ses_1"}

    def report(targets, engine):
        w.observe(conn, row, MAC, {"session_id": "ses_1", "targets": targets}, mode="shadow", engine=engine,
                  stream_Bps=100 * J.MIB, budget_bps=None, floor_mbps=None)
    return conn, clock, report


def _verdicts(conn, rule):
    from server.brain import memory                                         # noqa: PLC0415

    return [(d["entity_key"], d["verdict"]) for d in reversed(memory.decisions(conn, rule=rule, limit=50))]


def _d(serial, port, **extra):
    return {"dev": f"sd{chr(96 + port)}", "serial": serial, "port": port, "state": "writing", **extra}


def test_the_tail_is_measured_only_from_a_pass_the_server_saw_start(tmp_path):
    on_air = {"partition": 3, "round": 1, "state": "sending"}
    conn, clock, report = _watch(tmp_path)
    report([_d("S1", 1)], None)
    report([_d("S1", 1)], on_air)                                           # המעבר מתחיל ב-1000
    clock[0] = 1010.0
    report([_d("S1", 1, rejoin={"part": 3, "missing": 30, "chunks": 18254})], on_air)
    assert _verdicts(conn, "B2") == [("S1", J.LIVE)], "730 ש' מעבר, עברו 10: נשאר הרבה"
    conn2, _clock, report2 = _watch(tmp_path, "w2.db")
    report2([_d("S1", 1)], on_air)                                          # השרת עלה באמצע המעבר
    report2([_d("S1", 1, rejoin={"part": 3, "missing": 30, "chunks": 18254})], on_air)
    assert _verdicts(conn2, "B2") == [("S1", J.UNDECIDED)], "מעבר שלא נראה מתחיל: הזנב לא נמדד"


def test_a_drawer_that_misses_a_lot_in_two_passes_is_told_http(tmp_path):
    conn, _clock, report = _watch(tmp_path)
    conn.execute("INSERT INTO chunk_ledger (serial, size_bytes, part, round_id, mac, image_id, index_xxh64, chunks,"
                 " ranges_json, updated_at) VALUES ('S2', 1, 3, 'ses_1', ?, 'img', 'x', 1000, '[]', 'now')", (MAC,))
    conn.commit()
    for rnd in (1, 2):
        engine = {"partition": 3, "round": rnd, "state": "sending"}
        report([_d("S1", 1, skipped=400), _d("S2", 2, skipped=5), _d("S3", 3, skipped=10)], engine)
    report([_d("S1", 1), _d("S2", 2), _d("S3", 3)], {"partition": 3, "round": 3, "state": "sending"})
    assert _verdicts(conn, "B21") == [("S1", J.STAY), ("S1", J.HTTP)], "רק המגירה שחסר לה פי כמה מהשכנות"


# --- דרך ה-API ---------------------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from test_prodline_3181 import CLONER1, FIRST, _nine, _open, _progress, _queue, _t, _wave  # noqa: E402
from test_server_room import hello_body, room, room_server  # noqa: E402,F401

BOOT = "5b1f6f0e-3c0a-4b52-9d4e-0d6a2f1c7e88"
#: מה שהמוח של המצטרף רושם -- ואף אחד מהם אינו מגיע לסוכן.
RULES = ("V15", "B1", "B2", "B4", "B17", "B18", "B21", "B25", "B41", "B44", "B45", "B46", "R4")


def _rows(rs, rule=None, key=None):
    sql, params = "SELECT * FROM brain_decisions WHERE 1", []
    if rule:
        sql, params = sql + " AND rule = ?", [*params, rule]
    if key:
        sql, params = sql + " AND entity_key = ?", [*params, key]
    return rs["ctx"].conn.execute(sql + " ORDER BY id", params).fetchall()


def _booted(rs, mac, serials):
    body = hello_body(mac)
    body["boot_id"] = BOOT
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "serial": s, "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1}
                     for i, s in enumerate(serials)]
    assert rs["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def _line(rs):
    _nine(rs)
    _booted(rs, CLONER1, ["A1", "A2", "A3", "A4", "A5"])
    assert _open(rs["deploy"], 11, {"names": FIRST, "queue": _queue(2)}).status_code == 200
    wave = _wave(rs)
    drawers = list(enumerate(["A1", "A2", "A3", "A4", "A5"], start=1))
    _progress(rs, wave, CLONER1, [_t(f"sd{chr(96 + p)}", s, p, "waiting") for p, s in drawers])   # המלאי של POST
    _progress(rs, wave, CLONER1, [_t(f"sd{chr(96 + p)}", s, p, "writing") for p, s in drawers])
    return wave


def test_a_new_disk_mid_round_is_queued_and_judged_and_the_agent_hears_nothing(room_server):
    rs = room_server
    wave = _line(rs)
    got = _progress(rs, wave, CLONER1, [_t("sda", "A1", 1, "done", detached=True, removed=True),
                                        _t("sdf", "N1", 1, "waiting"), _t("sdg", "N6", 6, "waiting")])
    assert not {"join", "joiner", "brain"} & set(got), "בצל הסוכן לא שומע"
    queued = _rows(rs, "V15", "N1")
    assert [(r["mode"], r["verdict"]) for r in queued] == [("shadow", "WAITING")]
    assert {r["verdict"] for r in _rows(rs, "B25", "N1")} == {"POST_OCCUPIED"}
    assert {r["verdict"] for r in _rows(rs, "B25", "N6")} == {"POST_EMPTY"}
    first = _rows(rs, "B1", "N1")
    assert [r["verdict"] for r in first] == ["UNDECIDED"] and "SMART" in first[0]["reason"], "אין SMART = לא הוכרע"
    assert [r["verdict"] for r in _rows(rs, "B25", "A1")] == ["SAFE"], "נותק לפני שהוצא"
    order = _rows(rs, "B44")
    assert order and '"queue=N1,N6"' in order[-1]["evidence_json"]
    # SMART תקין ובדיקת כתיבה: מתקבל. אותו פסק בדיווח הבא אינו שורה נוספת
    rs["ctx"].conn.execute("INSERT INTO disk_events (session_id, mac, disk, port, serial, verdict, updated_at)"
                           " VALUES (?, ?, 'sdf', 1, 'N1', 'ok', '2026-11-15T10:00:00+00:00')", (wave, CLONER1))
    rs["ctx"].conn.commit()
    for _ in range(2):
        _progress(rs, wave, CLONER1, [_t("sdf", "N1", 1, "waiting", write_preflight={"state": "ok", "mbps": 400})])
    assert [r["verdict"] for r in _rows(rs, "B1", "N1")] == ["UNDECIDED", "ACCEPT"]
    assert room(rs["deploy"])["round"]["production_line"]["disks"], "פס הייצור עצמו לא השתנה"


def test_a_returning_drawer_is_judged_and_its_timeline_is_read(room_server):
    rs = room_server
    wave = _line(rs)
    rejoin = {"part": 3, "missing": 900, "chunks": 18254, "away_s": 90}
    got = _progress(rs, wave, CLONER1, [_t("sda", "A1", 1, "writing", rejoin=rejoin),
                                        _t("sdb", "A2", 2, "writing", rejoin={"part": 3, "missing": 40}),
                                        _t("sdc", "A3", 3, "done")])
    assert not {"join", "joiner", "brain"} & set(got)
    assert [r["verdict"] for r in _rows(rs, "B4", "A1")] == ["STREAM_THEN_HTTP"], "משכפל אחד חסר הרבה"
    assert [r["verdict"] for r in _rows(rs, "B2", "A1")] == ["UNDECIDED"], "אין גל חתיכות באוויר: לא ידוע"
    assert '"scope=CURRENT"' in _rows(rs, "B2", "A1")[0]["evidence_json"]
    assert [r["verdict"] for r in _rows(rs, "B18", "A1")] == ["UNDECIDED"]
    assert [r["verdict"] for r in _rows(rs, "B46", "A1")] == ["PARALLEL"], "השכנה משלימה, התקציב שלם"
    _progress(rs, wave, CLONER1, [_t("sda", "A1", 1, "writing", rejoin={**rejoin, "missing": 400}),
                                  _t("sdb", "A2", 2, "writing")])
    _progress(rs, wave, CLONER1, [_t("sda", "A1", 1, "verifying"), _t("sdb", "A2", 2, "writing")])
    _progress(rs, wave, CLONER1, [_t("sda", "A1", 1, "done"), _t("sdb", "A2", 2, "writing")])
    filled = _rows(rs, "R4", "A1")
    assert [r["verdict"] for r in filled] == ["FILLED", "READBACK", "DONE"]
    assert '"join_latency_s=' in filled[0]["evidence_json"], "המדידה של X1 נשמרת"
    line = rs["admin"].get("/api/console/brain/timeline/A1")
    assert line.status_code == 200
    events = line.json()["events"]
    rules = [e["rule"] for e in events if e["source"] == "brain"]
    assert rules[-1] == "R4" and rules.index("B2") < rules.index("B4") < rules.index("R4")
    assert "K5" in rules, "the completion source (K5) is part of the disk timeline"
    assert line.json()["round_id"] == room(rs["deploy"])["round"]["id"]
    assert rs["anon"].get("/api/console/brain/timeline/A1").status_code in (401, 403)


def test_off_computes_nothing(room_server):
    from server.db import set_setting                                       # noqa: PLC0415

    rs = room_server
    set_setting(rs["ctx"].conn, "brain_veto_mode", "off")
    wave = _line(rs)
    _progress(rs, wave, CLONER1, [_t("sda", "A1", 1, "writing", rejoin={"part": 3, "missing": 900}),
                                  _t("sdf", "N1", 6, "waiting")])
    assert [r for r in _rows(rs) if r["rule"] in RULES] == []


def test_the_waiting_queue_reads_back_in_order(room_server):
    rs = room_server
    wave = _line(rs)
    _progress(rs, wave, CLONER1, [_t("sdf", "N1", 6, "waiting")])
    _progress(rs, wave, CLONER1, [_t("sdf", "N1", 6, "waiting"), _t("sdg", "N2", 7, "waiting")])
    assert [w["serial"] for w in rs["ctx"].join.queue(wave)] == ["N1", "N2"]
    _progress(rs, wave, CLONER1, [_t("sdf", "N1", 6, "writing"), _t("sdg", "N2", 7, "waiting")])
    assert [w["serial"] for w in rs["ctx"].join.queue(wave)] == ["N2"], "מי שהתחיל לכתוב יצא מהתור"
