"""מדיניות קצב הגל — הכרעת נדב 17/10 (על #447/#1471, נתונים מ-#1571).

נמדד: מחשב שיכפול 2 (i5-650) ירד סבב אחרי סבב, ‏600 → 450 → 337 → 252,
עם ‏rexmits ‏228044 · ‏126026 · ‏1338 · ‏13426. העלייה דרשה `loss == 0` **בדיוק**,
ולכן אף פעם לא עלתה; רק הרצפה (150m) עצרה.

ההכרעה:

1. **עלייה** כשהשידורים החוזרים ≤ 0.1% מהבלוקים **וגם** כל מקלט דיווח
   ‏`RcvbufErrors`, והסכום באותו תקציב (כיול 17/10 מול ses_54a923dd — "אפס
   בדיוק" לא קורה על ברזל). מקלט שלא דיווח ("לא נקרא") — לא עולים (עיקרון 5).
2. **זיכרון לכל MAC ולפי מספר המגירות** שלו בגל; בלי נתון לאותו מספר — הקרוב,
   בצורה שמרנית.
3. ההורדה ×0.75 מעל הסף נשארת כמו שהיא.
4. שורת היומן אומרת על מה: ‏`basis=up(loss=0.04%,rcvbuf=0)`.

והצד של הסוכן: הסכום על מחיצות המולטיקאסט עולה בדיווח ההתקדמות (`udp_drops`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.rounds import drive_rates
from server.agent import reports

SENT = 10 ** 10                       # ‏10GB בגל
BLOCKS = -(-SENT // drive_rates.UDPCAST_BLOCK)
BUDGET = int(BLOCKS * drive_rates.LOSS_DOWN_RATIO)   # 0.1% — לשידורים חוזרים ול-rcvbuf
MAC_A = "aa:bb:cc:00:00:31"           # מחשב שיכפול 2 (i5-650)
MAC_B = "aa:bb:cc:00:00:32"
UNREAD = object()                     # המקלט לא דיווח `udp_drops`


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives(session: str, drawers: dict[str, int]) -> list[dict]:
    """מגירות טריות בכל גל (בחדר מחליפים מגירות) — ‏`drawers`: ‏mac → כמה."""
    return [{"serial": f"{session}-{mac[-2:]}-{k}", "model": "EVO 870", "mac": mac,
             "port": k + 1}
            for mac, count in drawers.items() for k in range(count)]


def _wave(conn, session, loss_ratio=0.0005, rcvbuf=0, drawers=None):
    """גל שלם שנגמר: השרת בחר ויצא, כל כונן עמד בקצב, השולח ספר, ובסוף הגל
    (`record_wave`) כל מקלט דיווח את ה-`udp_drops` שלו. מחזיר את הקצב שיצא."""
    drawers = drawers or {MAC_A: 1}
    drives = _drives(session, drawers)
    rate = drive_rates.decide(conn, session, lambda: drives, None)
    cap = drive_rates.parse_bitrate(rate) // 8
    for d in drives:
        conn.execute(
            "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
            " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
            (d["serial"], d["model"], session, cap, cap, cap * 600, 600,
             "2026-10-17T10:00:00+00:00"))
    conn.commit()
    drive_rates.record_stream(conn, session, {
        "sent_bytes": SENT, "loss_blocks": int(BLOCKS * loss_ratio),
        "stream_bps": int(cap * 0.95), "stream_s": 600})
    drive_rates.record_wave(conn, session, [
        {"mac": mac, "targets_json": "[]",
         "udp_drops_json": None if rcvbuf is UNREAD
         else json.dumps({"rcvbuf": rcvbuf, "inerrors": 0})}
        for mac in drawers])
    return rate


def _next(conn, drawers=None):
    return drive_rates.choose(conn, _drives("next", drawers or {MAC_A: 1}), None)


# --- 1. עלייה על "כמעט נקי" -------------------------------------------------


def test_the_measured_spiral_turns_back_up(conn):
    """הספירלה שנמדדה: גל ב-600m עם 1.7% שידורים חוזרים יורד ל-450m (×0.75,
    נשאר). ואז גלים עם 0.05% ומקלט שלא זרק כלום — **עולים**, ולא יורדים.
    עד ההכרעה: 600 → 450 → 450 → 450 (‏`held` לנצח)."""
    assert _wave(conn, "ses_0", loss_ratio=0.017) == "600m"
    seen = [_wave(conn, f"ses_{n}") for n in range(1, 5)]
    assert seen == ["450m", "550m", "650m", "750m"]
    got = _next(conn)
    assert got["max_bitrate"] == "850m"
    assert got["basis"] == "up"
    assert got["previous"]["rcvbuf"] == 0


def test_a_little_loss_with_a_clean_receiver_raises(conn):
    _wave(conn, "ses_1", loss_ratio=0.0005)
    got = _next(conn)
    assert got["max_bitrate"] == "700m" and got["basis"] == "up"


def test_loss_exactly_at_the_threshold_still_raises(conn):
    """‏≤ 0.1% — הסף עצמו עוד עולה; מעליו יורד (הסף של ההורדה לא זז)."""
    _wave(conn, "ses_1", loss_ratio=0.001)
    assert _next(conn)["basis"] == "up"


def test_a_receiver_that_dropped_more_than_the_budget_holds_the_rate(conn):
    """0.05% אבל הקרנל של המקלט זרק מעל 0.1% מהבלוקים — האובדן נולד במקבל,
    לא ברשת. לא עולים."""
    _wave(conn, "ses_1", rcvbuf=BUDGET + 1)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "held"
    assert got["previous"]["rcvbuf"] == BUDGET + 1


@pytest.mark.parametrize("rcvbuf", [12, BUDGET])
def test_a_few_receiver_drops_within_the_budget_still_raise(conn, rcvbuf):
    """הכיול של 17/10: "‏rcvbuf == 0 בדיוק" לא מתקיים על מכונה אמיתית — אותו
    תקציב כמו השידורים החוזרים, 0.1%, כולל הסף עצמו."""
    _wave(conn, "ses_1", rcvbuf=rcvbuf)
    got = _next(conn)
    assert got["max_bitrate"] == "700m" and got["basis"] == "up"


def test_the_measured_wave_ses_54a923dd_raises(conn):
    """הנתונים האמיתיים (מחשב 2, ‏17/10): ‏rcvbuf=189, ‏loss_blocks=187, זרם של
    640 שניות ב-30MB/s — כ-0.0014% מהבלוקים. הגל רץ ב-252m (תחתית הספירלה,
    ‏31.5MB/s — הזרם ב-95% ממנו). לפי "rcvbuf == 0" הוא לא היה עולה לעולם."""
    roster = [{"mac": MAC_A, "serial": "R1", "model": "EVO 870"}]
    sent = 30_000_000 * 640
    conn.execute(
        "INSERT INTO wave_rates (session_id, decision, at, stream, drops)"
        " VALUES (?,?,?,?,?)",
        ("ses_54a923dd", json.dumps({"max_bitrate": "252m", "roster": roster}),
         "2026-10-17T11:00:00+00:00",
         json.dumps({"sent_bytes": sent, "loss_blocks": 187,
                     "stream_bps": 30_000_000, "stream_s": 640}),
         json.dumps({MAC_A: {"rcvbuf": 189, "inerrors": 189}})))
    cap = 252_000_000 // 8
    conn.execute(
        "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
        " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
        ("R1", "EVO 870", "ses_54a923dd", 30_000_000, cap, sent, 640, "t"))
    conn.commit()
    got = _next(conn)
    assert got["max_bitrate"] == "352m"
    assert got["basis"] == "up"
    assert got["previous"]["rcvbuf"] == 189


@pytest.mark.parametrize("loss_ratio", [0.0005, 0.0])
def test_an_unread_receiver_does_not_raise(conn, loss_ratio):
    """עיקרון 5: מקלט שלא דיווח `udp_drops` אינו "מקלט נקי" — גם בלי שידור
    חוזר אחד. נשארים במקום."""
    _wave(conn, "ses_1", loss_ratio=loss_ratio, rcvbuf=UNREAD)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "unmeasured"
    assert got["previous"]["rcvbuf"] is None


def test_a_receiver_that_said_not_read_does_not_raise(conn):
    _wave(conn, "ses_1")
    drive_rates.record_wave(conn, "ses_1", [{
        "mac": MAC_A, "targets_json": "[]",
        "udp_drops_json": json.dumps({"rcvbuf": None, "inerrors": None})}])
    assert _next(conn)["basis"] == "unmeasured"


def test_one_unread_receiver_of_two_is_unread(conn):
    """שני מקלטים בגל, אחד נקי ואחד שלא דיווח — הגל כולו "לא נקרא"."""
    drives = _drives("ses_1", {MAC_A: 1, MAC_B: 1})
    rate = drive_rates.decide(conn, "ses_1", lambda: drives, None)
    cap = drive_rates.parse_bitrate(rate) // 8
    for d in drives:
        conn.execute(
            "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
            " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
            (d["serial"], d["model"], "ses_1", cap, cap, cap * 600, 600, "t"))
    conn.commit()
    drive_rates.record_stream(conn, "ses_1", {"sent_bytes": SENT, "loss_blocks": 0,
                                              "stream_bps": int(cap * 0.95),
                                              "stream_s": 600})
    drive_rates.record_wave(conn, "ses_1", [
        {"mac": MAC_A, "targets_json": "[]",
         "udp_drops_json": json.dumps({"rcvbuf": 0, "inerrors": 0})}])
    got = _next(conn, {MAC_A: 1, MAC_B: 1})
    assert got["max_bitrate"] == "600m" and got["basis"] == "unmeasured"


def test_one_percent_loss_lowers_even_with_a_clean_receiver(conn):
    """3. ההגנה לא זזה: מעל 0.1% — ‏×0.75, מה שהמקלט לא יגיד."""
    _wave(conn, "ses_1", loss_ratio=0.01, rcvbuf=0)
    got = _next(conn)
    assert got["max_bitrate"] == "450m" and got["basis"] == "down"


# --- rc2 על ברזל: ses_54a923dd כפי שהוא ב-DB של שרת המעבדה ------------------

CLONER2 = "78:ac:c0:9b:11:c2"
REAL_ROSTER = [{"mac": CLONER2, "serial": "naa.5002538d0049da64", "model": "SAMSUNG MZ7LN256"},
               {"mac": CLONER2, "serial": "naa.5002538f52a13403", "model": "Samsung SSD 870"}]
OFFLINE = ("מחיצה 3: הקרנל דחה כתיבה לכונן: device offline error, dev sdb, sector 5963736"
           " op 0x1:(WRITE) flags 0x100000 phys_seg 1 prio class 2")


def _real_wave(conn, sdb_state="failed"):
    """‏ses_54a923dd משוחזר מה-DB של שרת המעבדה (17/10, ‏90ea1585): ‏wave_rates
    (‏decision/stream) כפי שנשמרו, ו-`record_wave` על ה-targets_json וה-udp_drops
    של החבר כפי שדווחו — sda הושלמה, sdb נפלה בדקה הראשונה (device offline)."""
    conn.execute(
        "INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?,?,?,?)",
        ("ses_54a923dd", json.dumps({"max_bitrate": "252m", "roster": REAL_ROSTER}),
         "2026-10-17T12:23:24+00:00",
         json.dumps({"sent_bytes": 19528278182, "loss_blocks": 187,
                     "stream_bps": 30378495, "stream_s": 640.8})))
    conn.commit()
    targets = [
        {"dev": "sda", "state": "done", "serial": "naa.5002538d0049da64",
         "bytes_written": 19528278983, "bytes_total": 19528278983,
         "first_moved_at": "2026-10-17T12:23:52+00:00",
         "moved_at": "2026-10-17T12:34:49+00:00"},
        {"dev": "sdb", "state": sdb_state, "serial": None, "bytes_written": 1401332136,
         "bytes_total": 19528278983, "first_moved_at": "2026-10-17T12:23:52+00:00",
         "moved_at": "2026-10-17T12:24:46+00:00", "error": OFFLINE}]
    assert drive_rates.record_wave(conn, "ses_54a923dd", [{
        "mac": CLONER2, "targets_json": json.dumps(targets),
        "udp_drops_json": json.dumps({"rcvbuf": 189, "inerrors": 189})}]) == 1


def _real_next(conn):
    return drive_rates.choose(conn, [{**d, "port": n + 1} for n, d in
                                     enumerate(REAL_ROSTER)], None)


def test_rc2_ses_54a923dd_with_a_drawer_that_went_offline_raises(conn):
    """על ברזל יצא `held` (‏ses_80844e82): ‏sdb נפלה ב-device offline ולא השאירה
    דגימה, ו"כל כונן עמד בקצב" חסם. מגירה **שנכשלה** אינה ראיה על הקצב —
    הזרם (96%), השידורים החוזרים (0.0014%), ה-rcvbuf ו-sda הוכיחו אותו."""
    _real_wave(conn)
    got = _real_next(conn)
    assert got["max_bitrate"] == "352m"
    assert got["basis"] == "up"
    assert got["previous"]["rcvbuf"] == 189


def test_a_drawer_with_no_sample_that_did_not_fail_still_holds(conn):
    """השומר: מגירה בלי דגימה שלא דווחה כנכשלת (גל קצר, דיווח שלא הגיע) — אין
    ראיה, כמו קודם."""
    _real_wave(conn, sdb_state="writing")
    got = _real_next(conn)
    assert got["max_bitrate"] == "252m" and got["basis"] == "held"


def test_a_machine_whose_every_drawer_failed_does_not_raise(conn):
    _wave(conn, "ses_1", drawers={MAC_A: 2})
    conn.execute("DELETE FROM drive_rates WHERE session_id = 'ses_1'")
    conn.commit()
    drive_rates.record_wave(conn, "ses_1", [{
        "mac": MAC_A, "targets_json": json.dumps([{"dev": "sda", "state": "failed"},
                                                  {"dev": "sdb", "state": "failed"}]),
        "udp_drops_json": json.dumps({"rcvbuf": 0, "inerrors": 0})}])
    got = _next(conn, {MAC_A: 2})
    assert got["max_bitrate"] == "600m" and got["basis"] == "held"


# --- 2. זיכרון לפי מספר המגירות ---------------------------------------------


def test_three_drawers_do_not_punish_a_wave_of_one(conn):
    """המכונה למדה 550m עם מגירה אחת; גל של שלוש מגירות על אותה מכונה ירד
    אחר כך. הגל הבא של מגירה אחת ממשיך מהזיכרון **של מגירה אחת**."""
    _wave(conn, "ses_1", drawers={MAC_A: 1}, loss_ratio=0.017)       # 600 → 450
    _wave(conn, "ses_2", drawers={MAC_A: 1})                          # 450 → 550
    assert _wave(conn, "ses_3", drawers={MAC_A: 3},                   # הקרוב: 1 —
                 loss_ratio=0.017) == "285m"                          # 450 × 0.75^log2(3) (#1842)
    one = _next(conn, {MAC_A: 1})
    assert one["max_bitrate"] == "550m" and one["basis"] == "up"
    assert one["previous"]["session_id"] == "ses_2"
    assert one["previous"]["drawers"] == 1
    three = _next(conn, {MAC_A: 3})
    assert three["max_bitrate"] == "213m" and three["basis"] == "down"
    assert three["previous"]["session_id"] == "ses_3"
    assert three["previous"]["drawers"] == 3


def test_without_the_same_count_more_drawers_are_the_safe_side(conn):
    """אין גל של 2 מגירות. ‏1 ו-3 במרחק שווה — ‏3 (יותר עומס) קובע: הקצב שעמד
    בשלוש מגירות בטוח לשתיים; זה שעמד באחת — לא בהכרח."""
    _wave(conn, "ses_1", drawers={MAC_A: 3}, loss_ratio=0.017)       # 3: 600 → 450
    _wave(conn, "ses_2", drawers={MAC_A: 1})                          # 1: 450 → 550
    got = _next(conn, {MAC_A: 2})
    assert got["max_bitrate"] == "450m"
    assert got["previous"]["session_id"] == "ses_1"
    assert got["previous"]["drawers"] == 3


def test_a_clean_wave_of_fewer_drawers_does_not_raise_more(conn):
    """רק גל נקי של מגירה אחת בזיכרון, והגל הבא של שלוש: לא עולים ממנו —
    הקצב שהוא רץ בו × 0.75 לכל הכפלה (‏#1842, נדב 20/10): 600 × 0.75^log2(3)."""
    _wave(conn, "ses_1", drawers={MAC_A: 1})
    got = _next(conn, {MAC_A: 3})
    assert got["max_bitrate"] == "380m" and got["basis"] == "drawers"
    assert got["previous"]["drawers"] == 1


def test_a_clean_wave_of_more_drawers_raises_fewer(conn):
    _wave(conn, "ses_1", drawers={MAC_A: 3})
    got = _next(conn, {MAC_A: 1})
    assert got["max_bitrate"] == "700m" and got["basis"] == "up"


def test_a_lowering_from_fewer_drawers_still_lowers(conn):
    """‏#1842: ירידה ×0.75 (‏450m) גבוהה מ-600m × 0.75^log2(3) (‏380m) — הנמוך."""
    _wave(conn, "ses_1", drawers={MAC_A: 1}, loss_ratio=0.017)
    got = _next(conn, {MAC_A: 3})
    assert got["max_bitrate"] == "380m" and got["basis"] == "drawers"


# --- 4. היומן -----------------------------------------------------------------


def _journal(conn) -> list[str]:
    return [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'wave_rate' ORDER BY id")]


def test_the_journal_says_what_the_basis_rests_on(conn):
    _wave(conn, "ses_1", loss_ratio=0.0004)
    _wave(conn, "ses_2", rcvbuf=BUDGET + 1)
    _wave(conn, "ses_3", rcvbuf=UNREAD)
    lines = _journal(conn)
    assert lines[0].startswith("ses_1 600m basis=start ")
    assert lines[1].startswith("ses_2 700m basis=up(loss=0.04%,rcvbuf=0) ")
    assert "mac=" + MAC_A + " drawers=1/1" in lines[1]
    assert lines[2].startswith(f"ses_3 700m basis=held(loss=0.05%,rcvbuf={BUDGET + 1}) ")
    drive_rates.decide(conn, "ses_4", lambda: _drives("ses_4", {MAC_A: 3}), None)
    assert _journal(conn)[3].startswith(                 # 700 × 0.75^log2(3) (#1842)
        "ses_4 443m basis=drawers(loss=0.05%,rcvbuf=None) ")
    assert "drawers=1/3" in _journal(conn)[3]


# --- השרת: מהדיווח אל הזיכרון ------------------------------------------------


def _member(conn, session="ses_r", mac=MAC_A):
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES (?, 'grp_CLONERS', 'img', 'ROOM', 1, 300, 'running', 'nadav', 't',"
        " 0.0, 'multicast')", (session,))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES (?, ?, 'writing', 't')", (session, mac))
    conn.commit()


def _report(conn, **extra):
    return reports.ingest(conn, {"session_id": "ses_r", "mac": MAC_A,
                                 "state": "writing", "targets": [], **extra})


def _stored(conn):
    raw = conn.execute("SELECT udp_drops_json FROM session_members WHERE mac = ?",
                       (MAC_A,)).fetchone()["udp_drops_json"]
    return json.loads(raw) if raw else None


def test_the_report_keeps_the_receivers_drops(conn):
    _member(conn)
    assert _report(conn)["ok"] is True
    assert _stored(conn) is None                         # עוד לא שודרה מחיצה
    _report(conn, udp_drops={"rcvbuf": 0, "inerrors": 3})
    assert _stored(conn) == {"rcvbuf": 0, "inerrors": 3}
    _report(conn)                                        # דיווח בלי השדה — נשאר
    assert _stored(conn) == {"rcvbuf": 0, "inerrors": 3}
    _report(conn, udp_drops={"rcvbuf": None, "inerrors": None})
    assert _stored(conn) == {"rcvbuf": None, "inerrors": None}


@pytest.mark.parametrize("bad", [{"rcvbuf": -1, "inerrors": 0}, {"rcvbuf": "0"},
                                 {"rcvbuf": True}, "0 0", [0, 0]])
def test_a_malformed_value_is_not_read_and_does_not_leave_an_old_zero(conn, bad):
    """דיווח סופי פגום אחרי מחיצה נקייה: האפס הקודם **לא** נשאר — "לא נקרא"."""
    _member(conn)
    _report(conn, udp_drops={"rcvbuf": 0, "inerrors": 0})
    _report(conn, udp_drops=bad)
    assert _stored(conn) == {"rcvbuf": None, "inerrors": None}


def test_the_end_of_the_wave_copies_each_receivers_drops(conn):
    drive_rates.decide(conn, "ses_1", lambda: _drives("ses_1", {MAC_A: 1, MAC_B: 2}),
                       None)
    drive_rates.record_wave(conn, "ses_1", [
        {"mac": MAC_A, "targets_json": "[]",
         "udp_drops_json": json.dumps({"rcvbuf": 4, "inerrors": 0})},
        {"mac": "ff:ff:ff:ff:ff:ff", "targets_json": "[]",       # לא ברשימת הגל
         "udp_drops_json": json.dumps({"rcvbuf": 0, "inerrors": 0})}])
    row = conn.execute("SELECT drops FROM wave_rates WHERE session_id = 'ses_1'").fetchone()
    assert json.loads(row["drops"]) == {
        MAC_A: {"rcvbuf": 4, "inerrors": 0, "failed_drawers": 0}, MAC_B: None}


# --- הסוכן: הסכום עולה בדיווח -----------------------------------------------

from native import requires_native  # noqa: E402
from test_agent import AGENT, BASH, posix, sh  # noqa: E402

needs_bash = requires_native(("bash", BASH))


def _agent(run: Path, body: str) -> str:
    run.mkdir(parents=True, exist_ok=True)
    return sh(f"export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/core/common.sh 2>/dev/null; "
              f". {posix(AGENT)}/lib/core/waits.sh; . {posix(AGENT)}/lib/core/progress.sh; {body}")


@needs_bash
def test_the_agent_sums_the_partitions_and_reports_them(tmp_path):
    run = tmp_path / "run"
    out = _agent(run, "udp_drops_reset; udp_drops_add 3 1; udp_drops_add 0 4; "
                      "build_progress ses_1 aa:bb:cc:00:00:31")
    assert json.loads(out)["udp_drops"] == {"rcvbuf": 3, "inerrors": 5}


@needs_bash
def test_one_unread_partition_makes_the_sum_unread_for_good(tmp_path):
    run = tmp_path / "run"
    out = _agent(run, "udp_drops_reset; udp_drops_add 0 0; udp_drops_add null null; "
                      "udp_drops_add 0 0; build_progress ses_1 aa:bb:cc:00:00:31")
    assert json.loads(out)["udp_drops"] == {"rcvbuf": None, "inerrors": None}


@needs_bash
def test_the_drivers_outcome_does_not_overwrite_the_drops(tmp_path):
    """ברזל, ses_54a923dd: מחשב שיש לו גם `drivers.json` — השורה של הדרייברים
    דרסה את `udp_drops`, והשרת (‏COALESCE) נשאר עם ערך ישן."""
    run = tmp_path / "run"
    run.mkdir(parents=True)
    (run / "drivers.json").write_text('{"state":"skipped","packages":[]}\n',
                                      newline="\n")
    out = json.loads(_agent(run, "udp_drops_reset; udp_drops_add 189 189; "
                                 "build_progress ses_1 aa:bb:cc:00:00:31"))
    assert out["udp_drops"] == {"rcvbuf": 189, "inerrors": 189}
    assert out["drivers"] == {"state": "skipped", "packages": []}


@needs_bash
def test_before_any_partition_there_is_no_field_and_reset_clears_it(tmp_path):
    run = tmp_path / "run"
    out = _agent(run, "udp_drops_add 9 9; udp_drops_reset; "
                      "build_progress ses_1 aa:bb:cc:00:00:31")
    assert "udp_drops" not in json.loads(out)


@needs_bash
def test_a_real_multicast_stream_reaches_the_report(tmp_path):
    """המסלול האמיתי של המגירות (#1571): ההפרש סביב הזרם נכנס לסכום."""
    from test_udp_drops_1571 import _stream, snmp                # noqa: PLC0415
    _stream(tmp_path, "multicast", snmp(100, 110), snmp(137, 150))
    run = tmp_path / "box" / "run"
    assert json.loads((run / "udp_drops.json").read_text()) == {"rcvbuf": 37,
                                                                "inerrors": 40}


@needs_bash
def test_a_unicast_stream_adds_nothing(tmp_path):
    from test_udp_drops_1571 import _stream, snmp                # noqa: PLC0415
    _stream(tmp_path, "unicast", snmp(100, 110), snmp(137, 150))
    assert not (tmp_path / "box" / "run" / "udp_drops.json").exists()
