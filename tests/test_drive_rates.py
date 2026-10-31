"""‏#447 — תקרת קצב שנלמדת מהכוננים שבחדר, ולעולם לא מעל התקרה של היום.

הכרעת נדב 11/10: לבנות עכשיו, אבל בתקופה הראשונה **רק להוריד**. התקרה של
היום (‏`IMAGESRV_MAX_BITRATE`, ‏200m במעבדה) היא הגג; כונן שידוע שהוא איטי
מוריד את הגל אליו, כונן שאינו מוכר אינו מוריד דבר (לא מנחשים).

שלוש מלכודות שהטסטים כאן נועדו לתפוס:

* **ראצ'ט.** במולטיקאסט כל כונן כותב בקצב הזרם, והזרם מוגבל בתקרה. כונן
  שעמד בתקרה "עשה" 24MB/s רק כי זה מה שנשלח אליו — ולמידה תמימה (קצב ×
  מקדם ביטחון) הייתה מורידה את התקרה בכל גל, לנצח. לכן דגימה שעמדה בקצב
  הגל אינה מגבילה.
* **יציבות.** כונן איטי שהגל הורד אליו עומד כעת בקצב — וזה אינו סיבה
  להוריד שוב, וגם לא לקפוץ חזרה לתקרה בגל הבא.
* **עיקרון 5.** תקרה חסרה או לא-מספרית, דגימה בלי זמנים, גל בלי תקרה
  ידועה — כולם "לא נלמד", והגל יוצא בתקרה של היום.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.rounds import drive_rates
from server.agent import reports

MB = 1_000_000
CEILING = "200m"                      # 200Mbit/s = 25MB/s
CAP_200M = 25 * MB                    # בייטים לשנייה


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _sample(conn, serial, rate_bps, cap_bps=CAP_200M, model="EVO 870",
            session=None):
    n = conn.execute("SELECT COUNT(*) FROM drive_rates").fetchone()[0]
    conn.execute(
        "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
        " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
        (serial, model, session or f"ses_{n}", rate_bps, cap_bps,
         rate_bps * 1000, 1000, "2026-10-11T10:00:00+00:00"))
    conn.commit()


def _slow(conn, serial, rate_bps, **kw):
    """כונן שהגביל ב-`MIN_HELD_SAMPLES` גלים שונים — הסף להורדה."""
    for _ in range(2):                  # "≥2 גלים שונים" — הדרישה, לא הקבוע
        _sample(conn, serial, rate_bps, **kw)


def _drive(serial, model="EVO 870", mac="aa:bb:cc:00:00:21", port=1):
    return {"serial": serial, "model": model, "mac": mac, "port": port}


# --- התקרה כפי שהיא כתובה --------------------------------------------------


@pytest.mark.parametrize("raw, bits", [
    ("200m", 200_000_000), ("200M", 200_000_000), (" 150m ", 150_000_000),
    ("1g", 1_000_000_000), ("500k", 500_000), ("90000000", 90_000_000),
    ("2.5m", 2_500_000),
])
def test_the_ceiling_is_read_the_way_udpcast_reads_it(raw, bits):
    assert drive_rates.parse_bitrate(raw) == bits


@pytest.mark.parametrize("raw", [None, "", "fast", "0m", "-5m", "200x", "m"])
def test_a_ceiling_that_is_not_a_number_is_none_not_a_guess(raw):
    assert drive_rates.parse_bitrate(raw) is None


# --- הבחירה לפני הגל -------------------------------------------------------


def test_drives_with_no_history_leave_todays_cap(conn):
    got = drive_rates.choose(conn, [_drive("S1"), _drive("S2")], CEILING)
    assert got["max_bitrate"] == CEILING
    assert got["basis"] == "start"              # אין גל קודם: הפתיחה = התקרה
    assert got["by"] is None
    assert got["unknown_drives"] == 2


def test_a_known_slow_drive_lowers_the_wave_and_is_named(conn):
    """הבקרה השלילית של ה-Issue: כונן עם היסטוריה של 15MB/s מצטרף. היום
    הגל יוצא ב-200m והוא נזרק באמצע; אחרי — הגל יורד אליו, עם שוליים."""
    _slow(conn, "SLOW", 15 * MB)
    got = drive_rates.choose(conn, [_drive("FAST"), _drive("SLOW", port=2)], CEILING)
    assert got["basis"] == "learned"
    assert got["max_bitrate"] == "108m"          # 15MB/s × 8 × 0.9
    assert got["by"]["serial"] == "SLOW"
    assert got["by"]["model"] == "EVO 870"
    assert got["by"]["port"] == 2
    assert got["by"]["rate_bps"] == 15 * MB
    assert got["unknown_drives"] == 1            # FAST


def test_the_slowest_known_drive_decides(conn):
    _slow(conn, "A", 18 * MB)
    _slow(conn, "B", 12 * MB)
    got = drive_rates.choose(conn, [_drive("A"), _drive("B")], CEILING)
    assert got["by"]["serial"] == "B"
    assert got["max_bitrate"] == "86m"           # 12 × 8 × 0.9 = 86.4


def test_learning_never_goes_above_todays_cap(conn):
    """הגג של נדב: גם כונן שנמדד איטי ביחס לתקרה גבוהה יותר אינו מעלה
    את הגל מעל התקרה של היום."""
    _slow(conn, "S1", 30 * MB, cap_bps=125 * MB)     # הוגבל תחת 1g
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == CEILING
    assert got["basis"] == "start"              # אין גל קודם: הפתיחה = התקרה


def test_a_drive_that_kept_up_with_the_cap_does_not_lower_it(conn):
    """הראצ'ט: 23.9MB/s תחת 200m הוא הזרם, לא הכונן. בלי ההבחנה הזו כל
    גל היה מוריד את הבא ב-10%."""
    for _ in range(5):
        _sample(conn, "S1", 23_900_000)
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == CEILING
    assert got["basis"] == "start"              # אין גל קודם: הפתיחה = התקרה
    assert got["unknown_drives"] == 0            # מוכר — ופשוט עומד בקצב


def test_a_slow_drive_that_now_keeps_up_stays_where_it_was(conn):
    """גל אחד הוגבל ב-15MB/s תחת 200m; הגל הבא הורד ל-108m והכונן עמד
    בו. לא להוריד שוב (13MB/s × 0.9), ולא לקפוץ חזרה ל-200m."""
    _slow(conn, "S1", 15 * MB)
    _sample(conn, "S1", 13 * MB, cap_bps=13_500_000)
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == "108m"


def test_a_drive_that_once_kept_up_at_todays_cap_is_not_slow(conn):
    """כונן מהיר שישב פעם בגל של כונן איטי נמדד איטי — זו המלכודת השנייה
    ב-#447. גל אחד שבו עמד ב-200m מוכיח שהוא אינו המגביל."""
    _slow(conn, "S1", 15 * MB)
    _sample(conn, "S1", 24 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == CEILING


def test_only_the_recent_rounds_count(conn):
    _sample(conn, "S1", 10 * MB)                 # ישן
    for _ in range(drive_rates.WINDOW):
        _sample(conn, "S1", 24 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == CEILING


def test_the_memory_is_per_model_and_serial(conn):
    _slow(conn, "S1", 10 * MB, model="OTHER")
    got = drive_rates.choose(conn, [_drive("S1", model="EVO 870")], CEILING)
    assert got["max_bitrate"] == CEILING


def test_switching_learning_off_leaves_todays_cap(conn):
    _slow(conn, "S1", 10 * MB)
    db.set_setting(conn, drive_rates.SETTING, "false")
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == CEILING
    assert got["basis"] == "disabled"


def test_a_single_slow_wave_does_not_lower(conn):
    """סקירת אבטחה 11/10: דגימה אחת — כונן חם, גל חריג, או מכונה אחת
    שמדווחת שקר — אינה תכונה של כונן. רק גלים שונים מורידים."""
    _sample(conn, "S1", 2 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == CEILING
    assert got["basis"] == "start"              # אין גל קודם: הפתיחה = התקרה


def test_even_a_dying_drive_cannot_take_the_wave_below_the_floor(conn):
    """1MB/s × 8 × 0.9 = 7m. הרצפה היא רבע מהתקרה של היום — 50m — כדי
    שדגימות מורעלות או כונן גוסס לא ישתקו חדר שלם."""
    _slow(conn, "S1", 1 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], CEILING)
    assert got["max_bitrate"] == "50m"
    assert got["basis"] == "learned" and got["by"]["serial"] == "S1"


def test_learning_is_on_by_default(conn):
    assert db.get_setting(conn, drive_rates.SETTING) == "true"


@pytest.mark.parametrize("value", ["", "yes", "True "])
def test_a_garbled_switch_is_off_not_on(conn, value):
    """עיקרון 5: ערך שאינו "true" אינו רשות להוריד."""
    _slow(conn, "S1", 10 * MB)
    db.set_setting(conn, drive_rates.SETTING, value)
    assert drive_rates.choose(conn, [_drive("S1")], CEILING)["basis"] == "disabled"


def test_without_a_configured_cap_a_slow_drive_still_lowers_to_the_floor(conn):
    """קצב אוטומטי (הכרעת נדב 11/10): בלי `IMAGESRV_MAX_BITRATE` הגג 900m
    והפתיחה 600m — והכונן האיטי מוריד, עד הרצפה: רבע מהפתיחה (150m).
    עד ההכרעה: בלי תקרה לא היה `--max-bitrate` כלל, ולא נלמד דבר."""
    _slow(conn, "S1", 10 * MB)                   # 10 × 8 × 0.9 = 72m
    got = drive_rates.choose(conn, [_drive("S1")], None)
    assert got["max_bitrate"] == "150m"
    assert got["basis"] == "learned"
    assert got["ceiling"] == "900m"


def test_a_cap_that_is_not_a_number_passes_through_unlearned(conn):
    _slow(conn, "S1", 10 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], "fast")
    assert got["max_bitrate"] == "fast"          # בדיוק מה שהיה עובר היום
    assert got["basis"] == "bad_ceiling"


# --- ההחלטה נשמרת, ואינה מפילה גל ------------------------------------------


def test_the_decision_is_stored_and_journaled(conn):
    _slow(conn, "SLOW", 15 * MB)
    got = drive_rates.decide(conn, "ses_w1", lambda: [_drive("SLOW")], CEILING)
    assert got == "108m"
    stored = drive_rates.decision(conn, "ses_w1")
    assert stored["max_bitrate"] == "108m" and stored["by"]["serial"] == "SLOW"
    detail = conn.execute(
        "SELECT detail FROM journal WHERE event = 'wave_rate'").fetchone()[0]
    assert "108m" in detail and "SLOW" in detail


def test_a_broken_decision_is_todays_cap_and_said_out_loud(conn):
    def broken():
        raise RuntimeError("boom")
    assert drive_rates.decide(conn, "ses_w1", broken, CEILING) == CEILING
    events = [r[0] for r in conn.execute("SELECT event FROM journal")]
    assert "drive_rates_failed" in events


# --- הזיכרון: מה כל כונן באמת כתב -----------------------------------------


def _member(targets):
    return {"mac": "aa:bb:cc:00:00:21", "targets_json": json.dumps(targets)}


def _done(serial="S1", written=15_000 * MB, first="2026-10-11T10:00:00+00:00",
          last="2026-10-11T10:16:40+00:00", **extra):
    return {"dev": "sda", "state": "done", "serial": serial,
            "bytes_written": written, "first_moved_at": first, "moved_at": last,
            **extra}


def _with_disks(conn, model="EVO 870"):
    conn.execute(
        "INSERT INTO net_devices (mac, ip, first_seen, last_seen, disks_json)"
        " VALUES (?, ?, ?, ?, ?)",
        ("aa:bb:cc:00:00:21", "10.0.0.5", "x", "x",
         json.dumps([{"dev": "sda", "serial": "S1", "model": model}])))
    conn.commit()


def _decided(conn, session="ses_w1", ceiling=CEILING, roster=("S1",)):
    """הגל יצא — עם רשימת הכוננים שהשרת ראה לפני שיצא."""
    drive_rates.decide(conn, session, lambda: [_drive(s) for s in roster], ceiling)


def test_a_finished_drawer_leaves_its_rate(conn):
    _with_disks(conn)
    _decided(conn)
    assert drive_rates.record_wave(conn, "ses_w1", [_member([_done()])]) == 1
    row = conn.execute("SELECT * FROM drive_rates").fetchone()
    assert row["serial"] == "S1" and row["model"] == "EVO 870"
    assert row["rate_bps"] == 15 * MB            # 15GB / 1000s
    assert row["cap_bps"] == CAP_200M
    assert row["seconds"] == 1000


def test_recording_twice_is_one_sample(conn):
    _with_disks(conn)
    _decided(conn)
    drive_rates.record_wave(conn, "ses_w1", [_member([_done()])])
    drive_rates.record_wave(conn, "ses_w1", [_member([_done()])])
    assert conn.execute("SELECT COUNT(*) FROM drive_rates").fetchone()[0] == 1


@pytest.mark.parametrize("target", [
    _done(state="failed"),
    _done(serial=None),
    _done(first=None),
    _done(first="not a time"),
    _done(last="2026-10-11T10:00:30+00:00"),     # 30 שניות — רעש, לא קצב
    _done(written=100 * MB),                      # מתחת ל-1GB
])
def test_a_drawer_without_a_real_measurement_leaves_nothing(conn, target):
    _with_disks(conn)
    _decided(conn)
    assert drive_rates.record_wave(conn, "ses_w1", [_member([target])]) == 0
    assert conn.execute("SELECT COUNT(*) FROM drive_rates").fetchone()[0] == 0


def test_a_drive_that_was_not_in_the_wave_when_it_left_teaches_nothing(conn):
    """סקירת אבטחה 11/10: לומדים רק מכונן שהיה ברשימת הגל כשיצא (מכונה +
    סידורי) — לא מסידורי שמופיע רק בדיווח הסוף, ולא מאותו סידורי במכונה
    אחרת."""
    _with_disks(conn)
    _decided(conn)
    assert drive_rates.record_wave(conn, "ses_w1", [_member([_done(serial="S9")])]) == 0
    other = {"mac": "aa:bb:cc:00:00:99", "targets_json": json.dumps([_done()])}
    assert drive_rates.record_wave(conn, "ses_w1", [other]) == 0
    assert conn.execute("SELECT COUNT(*) FROM drive_rates").fetchone()[0] == 0


def test_the_model_comes_from_the_roster_not_the_end_of_the_wave(conn):
    _with_disks(conn, model="SWAPPED LATER")
    _decided(conn)
    drive_rates.record_wave(conn, "ses_w1", [_member([_done()])])
    assert conn.execute("SELECT model FROM drive_rates").fetchone()[0] == "EVO 870"


def test_a_wave_without_a_known_cap_teaches_nothing(conn):
    """בלי התקרה של הגל אי-אפשר לדעת אם הכונן הגביל או עמד בקצב."""
    _with_disks(conn)
    assert drive_rates.record_wave(conn, "ses_w1", [_member([_done()])]) == 0
    # תקרה לא-מספרית עוברת כמות שהיא — ואין ממנה קצב לסווג לפיו. (בלי
    # `IMAGESRV_MAX_BITRATE` יש מאז הקצב האוטומטי קצב ידוע: הפתיחה.)
    _decided(conn, session="ses_w2", ceiling="fast")
    assert drive_rates.record_wave(conn, "ses_w2", [_member([_done()])]) == 0


def test_a_broken_record_does_not_raise(conn):
    _decided(conn)
    assert drive_rates.record_wave(conn, "ses_w1", [{"mac": "x", "targets_json": "{"}]) == 0
    events = [r[0] for r in conn.execute("SELECT event FROM journal")]
    assert "drive_rates_failed" in events


# --- החותמת שממנה נגזר הזמן -----------------------------------------------


def _stamp(previous, targets):
    return reports.stamp_movement(
        json.dumps(previous) if previous is not None else None, targets)


def test_the_first_movement_is_stamped_once_and_kept():
    first = _stamp(None, [{"dev": "sda", "bytes_written": 0}])
    assert "first_moved_at" not in first[0]      # עוד לא זז
    moved = _stamp(first, [{"dev": "sda", "bytes_written": 10}])
    assert moved[0]["first_moved_at"]
    kept = _stamp([{**moved[0], "first_moved_at": "2026-10-11T10:00:00+00:00"}],
                  [{"dev": "sda", "bytes_written": 99}])
    assert kept[0]["first_moved_at"] == "2026-10-11T10:00:00+00:00"


def test_a_counter_that_restarted_restarts_the_first_movement():
    before = [{"dev": "sda", "bytes_written": 5000, "moved_at": "2026-10-11T10:05:00+00:00",
               "first_moved_at": "2026-10-11T10:00:00+00:00"}]
    after = _stamp(before, [{"dev": "sda", "bytes_written": 12}])
    assert after[0]["first_moved_at"] != "2026-10-11T10:00:00+00:00"
