"""‏#1758 — הקצב הלומד בוחר לפי goodput, לא לפי אחוז האבדן.

ברזל, 18/10, משכפל 2, משיכה לשתי מגירות, אימג' של 19.5GB (מה-Issue; הזמנים
הם send_start→send_done מהיומן):

    482m · ‏375ש' · אבדן 1.47% · ‏rcvbuf 198,228     ← המהיר ביותר
    361m · ‏476ש' · אבדן 0.41% · ‏rcvbuf 54,915      (down)
    270m · ‏624ש' · אבדן 0.02% · ‏rcvbuf 3,309       (down)
    370m · ‏469ש' · —                               (up)

הכלל הקודם ירד ×0.75 על כל אבדן מעל 0.1%, ולכן הוריד את הגל המהיר ביותר.
goodput = הבייטים שהמגירות כתבו בפועל, לכל מגירה, חלקי משך הזרם בשעון.

הטסטים כאן ממלאים את `wave_rates` **רק** דרך הפונקציות של הייצור
(`record_stream`, ‏`record_wave`) על החלטה שנשמרה — כך שבבקרה השלילית, על
הקוד הישן, הם רצים ונכשלים על ההחלטה עצמה, לא על עמודה חסרה.
"""

from __future__ import annotations

import json
import time

import pytest

from server import db
from server.rounds import drive_rates, nic_speed

SENT = 19_500_000_000                 # ‏19.5GB — גודל האימג' מה-Issue
BLOCKS = -(-SENT // drive_rates.UDPCAST_BLOCK)
CLONER2 = "78:ac:c0:9b:11:c2"
OTHER = "aa:bb:cc:00:00:32"
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives(session: str, drawers: int = 2, mac: str = CLONER2) -> list[dict]:
    return [{"serial": f"{session}-{mac[-2:]}-{k}", "model": "Samsung SSD 870", "mac": mac,
             "port": k + 1} for k in range(drawers)]


def _finish(conn, session, drives, *, loss, wall, lost=0, ended="send_done", rcvbuf=0,
            dropped=0, timeouts=0):
    """סוף גל, כפי שהשרת רושם אותו: המונה של השולח (`record_stream`) ומה
    שכל מגירה כתבה (`record_wave`). ‏`lost` — כמה מגירות של כל מכונה נכשלו.
    ‏`dropped` / ‏`timeouts` (‏#1761) — שורות `Dropping client` / ‏`Timeout
    notAnswered` ביומן השולח; ‏None = היומן לא נקרא."""
    drive_rates.record_stream(conn, session, {
        "sent_bytes": SENT, "loss_blocks": None if loss is None else round(BLOCKS * loss),
        "stream_bps": None, "stream_s": None, "wall_s": wall, "ended": ended,
        "dropped_clients": dropped, "timeouts": timeouts})
    members = {}
    for d in drives:
        members.setdefault(d["mac"], [])
    for mac, targets in members.items():
        mine = [d for d in drives if d["mac"] == mac]
        for k, d in enumerate(mine):
            failed = k < lost
            targets.append({"dev": f"sd{k}", "serial": d["serial"],
                            "state": "failed" if failed else "done",
                            "bytes_written": SENT // 4 if failed else SENT,
                            "bytes_total": SENT})
    drive_rates.record_wave(conn, session, [
        {"mac": mac, "targets_json": json.dumps(targets),
         "udp_drops_json": json.dumps({"rcvbuf": rcvbuf, "inerrors": rcvbuf})}
        for mac, targets in members.items()])


def _seed(conn, rate, *, loss, wall, rcvbuf=0, drawers=2, lost=0, ended="send_done",
          mac=CLONER2, dropped=0, timeouts=0):
    """גל שהיה — בקצב `rate` שנשמר כפי שהוא (ההחלטה של אז), ואז סופו."""
    session = f"ses_{next(_seq):04d}"
    drives = _drives(session, drawers, mac)
    roster = [{"mac": d["mac"], "serial": d["serial"], "model": d["model"]} for d in drives]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                 (session, json.dumps({"max_bitrate": rate, "basis": "up", "roster": roster}),
                  "2026-10-18T10:00:00+00:00"))
    conn.commit()
    _finish(conn, session, drives, loss=loss, wall=wall, lost=lost, ended=ended,
            rcvbuf=rcvbuf, dropped=dropped, timeouts=timeouts)
    return session


def _next(conn, drawers=2, mac=CLONER2, ceiling=None):
    return drive_rates.choose(conn, _drives("next", drawers, mac), ceiling)


def _bits(rate: str) -> int:
    return drive_rates.parse_bitrate(rate)


# --- 1. הסדרה מהברזל -----------------------------------------------------------


def test_the_fastest_wave_of_the_metal_series_is_not_lowered(conn):
    """482m, ‏1.47% אבדן, ‏375ש' — הזרם רץ ב-86% מהקצב שלו. האבדן כבר בתוך
    הזמן, ואין סיבה לרדת: עולים. הכלל הקודם: ‏`down` ל-361m — ‏476ש'."""
    _seed(conn, "482m", loss=0.0147, wall=375, rcvbuf=198_228)
    got = _next(conn)
    assert _bits(got["max_bitrate"]) >= _bits("482m"), got
    assert got["max_bitrate"] == "582m" and got["basis"] == "climb"
    assert got["previous"]["rule"] == "goodput"
    assert got["previous"]["best"]["max_bitrate"] == "482m"
    assert got["previous"]["goodput_bps"] == SENT // 375


def test_the_whole_metal_series_climbs_from_482_not_from_270(conn):
    """כל ארבעת הגלים בזיכרון: השיא הוא 482m (‏52MB/s למגירה) — לא 270m עם
    הכי מעט אבדן (‏31MB/s). ממנו עולים; אין מעליו קצב שנוסה."""
    _seed(conn, "482m", loss=0.0147, wall=375, rcvbuf=198_228)
    _seed(conn, "361m", loss=0.0041, wall=476, rcvbuf=54_915)
    _seed(conn, "270m", loss=0.0002, wall=624, rcvbuf=3_309)
    _seed(conn, "370m", loss=None, wall=469)          # האבדן לא נמסר ב-Issue
    got = _next(conn)
    assert _bits(got["max_bitrate"]) > _bits("482m"), got
    assert got["max_bitrate"] == "582m" and got["basis"] == "climb"
    assert got["previous"]["best"]["max_bitrate"] == "482m"


def test_the_metal_wave_at_270_alone_climbs_too(conn):
    """270m, ‏0.02%, ‏624ש' — ‏92.7% מהקצב: הקצב הוא שהגביל. עולים (כמו קודם)."""
    _seed(conn, "270m", loss=0.0002, wall=624, rcvbuf=3_309)
    got = _next(conn)
    assert got["max_bitrate"] == "370m" and got["basis"] == "climb"


def test_the_journal_line_says_what_the_peak_is(conn):
    _seed(conn, "482m", loss=0.0147, wall=375, rcvbuf=198_228)
    drives = _drives("ses_log")
    assert drive_rates.decide(conn, "ses_log", lambda: drives, None) == "582m"
    detail = conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                          " ORDER BY id DESC").fetchone()["detail"]
    assert "basis=climb(best=482m:52.0MB/s,eff=86%,goodput=52.0MB/s" in detail, detail
    assert "loss=1.47%,rcvbuf=198228)" in detail, detail


# --- 2. חיפוש השיא: עולה עד נקודה, ואז נשאר בה ----------------------------------


def _run(conn, model, waves, drawers=2):
    """גלים אמיתיים ברצף: השרת בוחר (`decide`), ‏`model(bits)` אומר מה קרה —
    ‏(goodput של הזרם בבייטים לשנייה, אבדן, מגירות שאבדו, איך השולח סיים)."""
    rates = []
    for n in range(waves):
        session = f"run_{n:02d}"
        drives = _drives(session, drawers)
        rate = drive_rates.decide(conn, session, lambda: drives, None)
        stream_bps, loss, lost, ended = model(_bits(rate))
        _finish(conn, session, drives, loss=loss, lost=lost, ended=ended,
                wall=SENT / stream_bps if ended == "send_done" else None)
        rates.append(rate)
    return rates


def test_goodput_that_keeps_rising_climbs_to_the_ceiling(conn):
    """הזרם רץ ב-90% מהקצב בכל קצב, עם 1% אבדן — כל צעד מקצר את הגל. עד הגג
    (900m), ולא יורדים בדרך."""
    rates = _run(conn, lambda bits: (0.9 * bits / 8, 0.01, 0, "send_done"), 6)
    assert rates == ["600m", "700m", "800m", "900m", "900m", "900m"]


def _peaked(bits: int):
    """‏goodput עולה עם הקצב עד 650m, ומעליו האבדן אוכל יותר ממה שהקצב נותן."""
    top = 650_000_000
    stream = 0.93 * min(bits, top) / 8 - 0.5 * max(0, bits - top) / 8
    return stream, 0.004 if bits <= top else 0.015, 0, "send_done"


def test_the_climb_finds_the_peak_and_stays_on_it(conn):
    """600 → 700 (מהיר יותר) → 800 (איטי) → 750 (איטי, באמצע) → 725 (איטי) →
    ‏700, ונשארים: הפער לקצב האיטי הקרוב (25m) קטן מ-2×REFINE_STEP."""
    rates = _run(conn, _peaked, 9)
    assert rates == ["600m", "700m", "800m", "750m", "725m",
                     "700m", "700m", "700m", "700m"]
    got = _next(conn)
    assert got["basis"] == "peak" and got["max_bitrate"] == "700m"


def test_a_peak_is_probed_again_once_the_slower_rates_leave_the_window(conn):
    """הקצבים האיטיים שמעל השיא יוצאים מהחלון אחרי GOODPUT_WINDOW גלים —
    ובודקים שוב (המגירות והאימג' משתנים), וחוזרים לשיא. עשרה גלים בשיא בין
    בדיקה לבדיקה."""
    rates = _run(conn, _peaked, 19)
    assert rates[5:15] == ["700m"] * 10
    assert rates[15:] == ["800m", "750m", "725m", "700m"]


# --- 3. אבדן — הגנה, לא מטרה ---------------------------------------------------


def test_a_drawer_lost_with_loss_above_the_peak_stops_the_search_below_it(conn):
    """700m איבד מגירה עם 3% אבדן — מגירה לכתוב מחדש היא גל שלם. חוזרים ל-600m
    ולא מחפשים באמצע (650m) עד שהנקודה יוצאת מהחלון."""
    def model(bits):
        if bits >= 700_000_000:
            return 0.85 * bits / 8, 0.03, 1, "send_done"
        return 0.92 * bits / 8, 0.003, 0, "send_done"
    assert _run(conn, model, 4) == ["600m", "700m", "600m", "600m"]
    got = _next(conn)
    assert got["basis"] == "peak" and got["max_bitrate"] == "600m"


def test_a_sender_that_failed_with_loss_is_a_point_of_zero(conn):
    """השולח נכשל ב-700m עם 2.5% שידורים חוזרים (המנגנון של 11/09: מקלט שהופל)
    — ‏goodput אפס, ולא חוזרים לשם."""
    def model(bits):
        if bits >= 700_000_000:
            return 0.0, 0.025, 2, "send_failed"
        return 0.92 * bits / 8, 0.003, 0, "send_done"
    assert _run(conn, model, 4) == ["600m", "700m", "600m", "600m"]


def test_a_failed_sender_without_loss_is_not_evidence_about_the_rate(conn):
    """נכשל בלי שידורים חוזרים (אף מקלט לא הצטרף, קובץ חסר) — לא נקודה; הקצב
    נקבע מהגלים שלפניו."""
    _seed(conn, "600m", loss=0.003, wall=SENT / (0.92 * 600e6 / 8))
    _seed(conn, "700m", loss=0.0, wall=None, ended="send_failed")
    got = _next(conn)
    assert got["max_bitrate"] == "700m" and got["basis"] == "climb"
    assert got["previous"]["best"]["max_bitrate"] == "600m"


def test_loss_beyond_the_measured_range_holds_the_peak(conn):
    """הזרם ב-90% מהקצב, אבל 2.5% שידורים חוזרים — מעל כל גל שהסתיים על ברזל
    (‏1.70%). לא עולים לשטח שלא נמדד; גם לא יורדים."""
    _seed(conn, "600m", loss=0.025, wall=SENT / (0.9 * 600e6 / 8))
    got = _next(conn)
    assert got["max_bitrate"] == "600m" and got["basis"] == "held"


def test_a_stream_that_did_not_run_at_its_rate_goes_back(conn):
    """הזרם ב-50% מהקצב (משהו אחר מגביל) ואין מתחתיו קצב שנוסה — ‏×0.75."""
    _seed(conn, "600m", loss=0.0005, wall=SENT / (0.5 * 600e6 / 8))
    got = _next(conn)
    assert got["max_bitrate"] == "450m" and got["basis"] == "back"


def test_a_higher_rate_within_the_noise_is_not_the_peak(conn):
    """‏700m היה מהיר רק ב-2% מ-600m — בתוך הרעש (3%). השיא נשאר 600m."""
    _seed(conn, "600m", loss=0.001, wall=SENT / 70e6)
    _seed(conn, "700m", loss=0.004, wall=SENT / 71.4e6)
    got = _next(conn)
    assert got["max_bitrate"] == "650m" and got["basis"] == "climb"
    assert got["previous"]["best"]["max_bitrate"] == "600m"


def test_an_unreported_machine_is_not_a_slow_one(conn):
    """עיקרון 5: מכונה שלא דיווחה בסוף הגל — אין נקודה, ולכן הכלל הקודם."""
    session = _seed(conn, "482m", loss=0.0147, wall=375)
    drive_rates.record_wave(conn, session, [])
    got = _next(conn)
    assert got["previous"].get("rule") is None
    assert got["basis"] == "down"


# --- 3א. ‏#1761: האות הישיר מהשולח, וזיכרון של קצב לא-בטוח ---------------------
#
# ‏udpcast (‏senddata.c:930-967): מקלט מנותק רק כשפקיעת ההמתנה ל-REQACK חוזרת
# יותר מ-retries-until-drop פעמים — `Dropping client #N because of timeout`;
# וכל פקיעה מדפיסה `Timeout notAnswered=...`. אבדן rcvbuf אינו האות.


def _fast(rate_bits: float, eff: float = 0.92) -> float:
    """‏`wall` של גל שהזרם שלו רץ ב-`eff` מהקצב."""
    return SENT / (eff * rate_bits / 8)


def test_a_dropped_client_makes_the_rate_unsafe(conn):
    """‏700m היה מהיר יותר מ-600m, בלי מגירה שאבדה ובאבדן נמוך — אבל udp-sender
    ניתק בו מקלט. לא עולים אליו ולא מעליו. הכלל של #1758 לבדו היה רואה
    ב-700m שיא בטוח ועולה ל-800m."""
    _seed(conn, "600m", loss=0.003, wall=_fast(600e6))
    _seed(conn, "700m", loss=0.003, wall=_fast(700e6), dropped=1)
    got = _next(conn)
    assert got["max_bitrate"] == "600m" and got["basis"] == "peak", got
    assert got["previous"]["best"]["max_bitrate"] == "600m"
    rows = [dict(r) for r in conn.execute("SELECT mac, drawers, rate_bps FROM unsafe_rates")]
    assert rows == [{"mac": CLONER2, "drawers": 2, "rate_bps": 700_000_000}]


def test_a_dropped_client_alone_goes_back(conn):
    """גל יחיד שבו נותק מקלט — ‏goodput אפס: ‏×0.75, גם כשהשולח סיים."""
    _seed(conn, "600m", loss=0.003, wall=_fast(600e6), dropped=2)
    got = _next(conn)
    assert got["max_bitrate"] == "450m" and got["basis"] == "back", got


def _per_gb(timeouts_per_gb: float) -> int:
    """כמה שורות `Timeout notAnswered` הן `timeouts_per_gb` על SENT (‏19.5GB)."""
    return round(timeouts_per_gb * SENT / 10 ** 9)


def test_many_timeouts_per_gb_at_the_peak_hold_the_climb(conn):
    """השיא רץ ב-92% מהקצב, אבדן נמוך — אבל udp-sender כתב בו `Timeout
    notAnswered` מעל TIMEOUTS_PER_GB_HELD (‏5.1/GB): נשארים, לא עולים."""
    timeouts = _per_gb(5.1)
    assert timeouts * 10 ** 9 / SENT > 5.0          # TIMEOUTS_PER_GB_HELD
    _seed(conn, "600m", loss=0.003, wall=_fast(600e6), timeouts=timeouts)
    got = _next(conn)
    assert got["max_bitrate"] == "600m" and got["basis"] == "held", got


def test_timeouts_per_gb_under_the_threshold_do_not_hold(conn):
    """‏4.9/GB — מתחת לסף (המקסימום שנמדד בלי ניתוק, ברזל 19/10: ‏3.4/GB). עולים."""
    timeouts = _per_gb(4.9)
    assert 0 < timeouts * 10 ** 9 / SENT < 5.0      # TIMEOUTS_PER_GB_HELD
    _seed(conn, "600m", loss=0.003, wall=_fast(600e6), timeouts=timeouts)
    got = _next(conn)
    assert got["max_bitrate"] == "700m" and got["basis"] == "climb", got


def test_one_timeout_at_277m_does_not_hold_two_drawers(conn):
    """ברזל 19/10, משכפל 2, שתי מגירות, 19.5GB: ‏277m → פקיעה אחת, וכל המגירות
    הצליחו. "פקיעה אחת עוצרת" היה נועל את שתי המגירות ב-277m."""
    _seed(conn, "277m", loss=0.0002, wall=_fast(277e6), timeouts=1)
    got = _next(conn)
    assert got["max_bitrate"] == "377m" and got["basis"] == "climb", got


def test_at_577m_on_metal_the_loss_is_what_holds(conn):
    """ברזל 19/10: ‏577m — ‏67 פקיעות (‏3.4/GB, מתחת לסף) אבל 3.6%–4.0% שידורים
    חוזרים: LOSS_CLIMB_MAX הוא שעוצר, כמו על הברזל."""
    _seed(conn, "577m", loss=0.038, wall=_fast(577e6), timeouts=67)
    got = _next(conn)
    assert got["max_bitrate"] == "577m" and got["basis"] == "held", got


def test_timeouts_below_the_peak_do_not_hold_it(conn):
    """הפקיעות היו ב-500m; השיא (600m) נקי — עולים ממנו."""
    _seed(conn, "500m", loss=0.003, wall=_fast(500e6), timeouts=4)
    _seed(conn, "600m", loss=0.003, wall=_fast(600e6))
    got = _next(conn)
    assert got["max_bitrate"] == "700m" and got["basis"] == "climb", got


def test_an_unread_sender_log_is_not_a_clean_one(conn):
    """עיקרון 5: יומן השולח לא נקרא — "לא נבדק", לא "אפס ניתוקים". אין טיפוס."""
    _seed(conn, "600m", loss=0.003, wall=_fast(600e6), dropped=None, timeouts=None)
    got = _next(conn)
    assert got["max_bitrate"] == "600m" and got["basis"] == "unmeasured", got


def test_an_unread_sender_log_still_lets_the_rate_go_back(conn):
    """ירידה אינה צריכה את היומן: הזרם ב-50% מהקצב — ‏×0.75 כמו קודם."""
    _seed(conn, "600m", loss=0.003, wall=_fast(600e6, 0.5), dropped=None, timeouts=None)
    got = _next(conn)
    assert got["max_bitrate"] == "450m" and got["basis"] == "back", got


def test_an_unsafe_rate_is_remembered_after_it_leaves_the_window(conn):
    """700m איבד מגירה עם 3% אבדן. אחרי GOODPUT_WINDOW (10) גלים ב-600m הנקודה
    יוצאת מהחלון — והקצב **לא** חוזר ל-700m: הוא נזכר ב-`unsafe_rates`.
    בלי הזיכרון: הגל ה-13 עולה שוב ל-700m ומאבד מגירה שוב."""
    def model(bits):
        if bits >= 700_000_000:
            return 0.85 * bits / 8, 0.03, 1, "send_done"
        return 0.92 * bits / 8, 0.003, 0, "send_done"
    rates = _run(conn, model, 13)
    assert rates == ["600m", "700m"] + ["600m"] * 11, rates
    got = _next(conn)
    assert got["max_bitrate"] == "600m" and got["basis"] == "peak", got


def test_a_remembered_unsafe_rate_also_caps_more_drawers(conn):
    """קצב שהפיל מקלט עם שתי מגירות אינו בטוח עם שלוש (יותר עומס במקלט)."""
    _seed(conn, "700m", loss=0.003, wall=_fast(700e6), dropped=1, drawers=2)
    for _ in range(drive_rates.GOODPUT_WINDOW + 1):
        _seed(conn, "600m", loss=0.003, wall=_fast(600e6), drawers=3)
    got = _next(conn, drawers=3)
    assert got["max_bitrate"] == "600m" and got["basis"] == "peak", got


# --- 4. מה שאסור לשבור --------------------------------------------------------


def test_the_switch_still_turns_learning_off(conn):
    _seed(conn, "482m", loss=0.0147, wall=375)
    db.set_setting(conn, drive_rates.SETTING, "false")
    got = _next(conn)
    assert got["basis"] == "disabled" and got["max_bitrate"] == "600m"


def test_the_ceiling_still_stops_the_climb(conn):
    _seed(conn, "482m", loss=0.0147, wall=375)
    got = _next(conn, ceiling="500m")
    assert got["max_bitrate"] == "500m" and got["basis"] == "ceiling"


def test_a_slow_cloner_nic_still_caps_the_climb(conn):
    """גג מוגדר של 2000m, אבל מחשב השיכפול ב-1Gb/s — הגג 900m (90% ממנו), גם
    כשהשיא שנלמד (900m, הזרם ב-90% מהקצב) היה עולה ל-1000m."""
    _seed(conn, "900m", loss=0.005, wall=SENT / (0.9 * 900e6 / 8))
    nic_speed.set_manual(conn, CLONER2, 1000)
    got = _next(conn, ceiling="2000m")
    assert got["max_bitrate"] == "900m" and got["basis"] == "ceiling"
    assert got["cap_by"] == "cloner"


def test_from_fewer_drawers_the_climb_does_not_explore(conn):
    """נלמד עם מגירה אחת; עכשיו שתיים — לא +100m, אלא השיא שהוכח × 0.75 (‏#1842)."""
    _seed(conn, "482m", loss=0.0147, wall=375, drawers=1)
    got = _next(conn, drawers=2)
    assert got["max_bitrate"] == "361m" and got["basis"] == "drawers"


def test_the_minimum_over_machines_still_rules(conn):
    """שתי מכונות: אחת בשיא 482m (עולה ל-582m), השנייה הוכיחה רק 270m
    (עולה ל-370m) — הגל במינימום."""
    _seed(conn, "482m", loss=0.0147, wall=375)
    _seed(conn, "270m", loss=0.0002, wall=624, mac=OTHER)
    drives = _drives("next", 2, CLONER2) + _drives("next", 2, OTHER)
    got = drive_rates.choose(conn, drives, None)
    assert got["max_bitrate"] == "370m" and got["previous"]["mac"] == OTHER


def test_record_wave_stores_what_each_machine_delivered(conn):
    session = _seed(conn, "482m", loss=0.0147, wall=375, lost=1)
    row = dict(conn.execute("SELECT * FROM wave_rates WHERE session_id = ?",
                            (session,)).fetchone())
    assert json.loads(row.get("delivered") or "null") == {
        CLONER2: {"drawers": 2, "done": 1, "written_bytes": SENT}}


# --- 5. השולח: משך הזרם בשעון ---------------------------------------------------

from test_sender_stream_stats import current_log, engine_env, line, write_log  # noqa: E402,F401
from test_sender import wait_for  # noqa: E402
from server.rounds import sender as sender_module  # noqa: E402


def test_the_wall_time_runs_from_the_first_byte_to_send_done(engine_env, monkeypatch):
    """‏`wall_s` — מהבייט הראשון של מחיצה 1 ועד `send_done`, כולל המעבר בין
    המחיצות; בלי ההמתנה למקבלים לפני הבייט הראשון."""
    monkeypatch.setattr(sender_module, "PACE_POLL", 0.05)
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(0, 0))
    time.sleep(0.6)                                  # ממתין למקבלים — לא נספר
    write_log(current_log(recorder), "Starting transfer: f\n" + line(1_000_000, 0))
    began = time.monotonic()
    time.sleep(0.4)
    recorder.processes[0].release()
    assert wait_for(lambda: len(recorder.processes) == 2)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(2_000_000, 0))
    time.sleep(0.3)
    recorder.processes[1].release()
    assert wait_for(lambda: engine.status()["state"] == "done")
    spanned = time.monotonic() - began
    got = engine.wave_outcome("ses_1")
    assert got.get("wall_s") is not None, got
    assert 0.6 <= got["wall_s"] <= spanned + 0.2, (got, spanned)


def test_the_wave_outcome_counts_drops_and_timeouts_over_the_partitions(engine_env):
    """‏#1761: השורות של udp-sender (‏senddata.c) נספרות בכל המחיצות, מהקובץ
    כולו — לא מהזנב, שאותו ממלאות שורות ההתקדמות."""
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    timeout = "Timeout notAnswered=[1] notReady=[] nrAns=1 nrRead=1 nrPart=2 avg=3104\n"
    write_log(current_log(recorder), "Starting transfer: f\n" + timeout * 2
              + line(5_000, 7) * 200)
    recorder.processes[0].release()
    assert wait_for(lambda: len(recorder.processes) == 2)
    write_log(current_log(recorder), "Starting transfer: f\n" + timeout
              + "Dropping client #1 because of timeout\n" + line(9_000, 0))
    got = engine.wave_outcome("ses_1")
    assert (got.get("dropped_clients"), got.get("timeouts")) == (1, 3), got


def test_a_clean_sender_log_counts_zero(engine_env):
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(5_000, 0))
    got = engine.wave_outcome("ses_1")
    assert (got.get("dropped_clients"), got.get("timeouts")) == (0, 0), got


def test_an_unreadable_sender_log_is_unchecked_not_clean(engine_env):
    """עיקרון 5: לוג של מחיצה שלא נקרא — שני המונים None, לא אפס."""
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    current_log(recorder).unlink(missing_ok=True)
    got = engine.wave_outcome("ses_1")
    assert "dropped_clients" in got and got["dropped_clients"] is None, got
    assert "timeouts" in got and got["timeouts"] is None, got


def test_a_wave_that_did_not_reach_send_done_has_no_wall_time(engine_env):
    engine, recorder = engine_env
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: len(recorder.processes) == 1)
    write_log(current_log(recorder), "Starting transfer: f\n" + line(5_000, 0))
    got = engine.wave_outcome("ses_1")
    assert got["sent_bytes"] == 5_000 and "wall_s" in got and got["wall_s"] is None, got
