"""קצב מולטיקאסט אוטומטי לגמרי — הכרעת נדב 11/10: "אני לא נוגע בזה,
והשרת לבד מחליט".

על גבי #447 (`server/rounds/drive_rates.py`, שעד כאן רק הוריד מתחת לתקרה):

* **פתיחה ב-600m** — נמדד נקי, re-xmits=0 (מ.19, 06/09). `IMAGESRV_MAX_BITRATE`,
  כשהוגדר, הוא הגג; כשלא — הגג הוא 900m.
* **למעלה** — רק על ראיה חיובית לגל נקי: אפס שידורים חוזרים **וגם** כל
  כונן בגל עמד בקצב. מדידה שחסרה = נשאר במקום (עיקרון 5).
* **למטה** — שידורים חוזרים מעל סף קטן (-25%), או כונן איטי מוכר (#447).
* **רצפה** — רבע מקצב הפתיחה; **גג** — לעולם לא מעליו.
* **זיכרון** — לכל מחשב שיכפול (MAC) הגל האחרון שלו הוא נקודת המוצא, והגל
  יוצא מהמינימום על המכונות; מכונה שלא נראתה — פתיחה (#1471, נדב 16/10).

#3173 (נדב 21/11): udpcast נמחק. "למעלה" ו"למטה" על המונים של udp-sender יצאו איתו, והטסטים שלהם נמחקו; הגלים כאן
הם גלי מנוע החתיכות, והגל הבא יורש את הקצב שהגל הקודם של המכונה יצא בו (`unmeasured`). הנלמד (עלייה, קיצוץ) הוא של
`chunk_rate.adjust` ונבדק ב-`test_chunk_rate_3163`. נשארו כאן הפתיחה, הגג, הרצפה, הכוננים האיטיים (#447), הזיכרון
לכל מכונה (#1471), מחשב הבנייה (#715) והמתג.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.rounds import drive_rates

MB = 1_000_000
SENT = 10 ** 10                     # 10GB בגל — 6.87 מיליון בלוקים של 1456
CLEAN = 0
FEW = 10                            # מעל אפס, מתחת לסף — "לא נקי, לא רע"
MANY = 100_000                      # ~1.5% מהבלוקים — מעל הסף


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


MAC_A = "aa:bb:cc:00:00:21"          # מחשב שיכפול 1
MAC_B = "aa:bb:cc:00:00:22"          # מחשב שיכפול 2


def _drive(serial, model="EVO 870", mac=MAC_A, port=1):
    return {"serial": serial, "model": model, "mac": mac, "port": port}


def _kept(conn, session, serial, rate_bps, cap_bps, model="EVO 870"):
    conn.execute(
        "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
        " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
        (serial, model, session, rate_bps, cap_bps, rate_bps * 1000, 1000,
         "2026-10-11T10:00:00+00:00"))
    conn.commit()


STREAM_FULL = 0.95                  # udp-sender מגיע ל-95.7% מהתקרה (06/09)


def _wave(conn, session, serials=("S1", "S2"), ceiling=None, loss=CLEAN,
          sent=SENT, kept_up=True, measured=True, stream=STREAM_FULL,
          stream_s=600, mac=MAC_A, rcvbuf=0):
    """גל שלם של מנוע החתיכות: השרת בחר קצב ויצא (`engine: chunk`), וכל כונן השאיר דגימה.
    #3173: udpcast נמחק, ואין יותר מונה של udp-sender (`record_stream`) — `loss`, `sent`, `measured`, `stream`
    ו-`stream_s` נשארו בחתימה ואינם נקראים. הנלמד מהגל הוא של `chunk_rate` (`test_chunk_rate_3163`).
    `mac` — מחשב השיכפול של כל הכוננים בגל (#1471).
    `rcvbuf` — מה שהמקלט דיווח שהקרנל שלו זרק (הכרעת נדב 17/10; None = לא דיווח)."""
    del loss, sent, measured, stream, stream_s
    rate = drive_rates.decide(conn, session,
                              lambda: [_drive(s, mac=mac) for s in serials], ceiling,
                              adjust=lambda chosen, wave: chosen)
    cap = drive_rates.parse_bitrate(rate) // 8
    for serial in serials:
        _kept(conn, session, serial, cap if kept_up else cap // 2, cap)
    drive_rates.record_wave(conn, session, [{
        "mac": mac, "targets_json": "[]",
        "udp_drops_json": None if rcvbuf is None
        else json.dumps({"rcvbuf": rcvbuf, "inerrors": 0})}])
    return rate


def _next(conn, serials=("S1", "S2"), ceiling=None, macs=None):
    """`macs` — המכונה של כל כונן, באותו סדר (None = כולם על מחשב 1)."""
    macs = macs or [MAC_A] * len(serials)
    return drive_rates.choose(conn, [_drive(s, mac=m) for s, m in zip(serials, macs)],
                              ceiling)


# --- הפתיחה -----------------------------------------------------------------


def test_without_a_configured_cap_the_wave_opens_at_600m(conn):
    """שרת שהותקן מה-ISO אינו מגדיר `IMAGESRV_MAX_BITRATE`. עד היום — אין
    `--max-bitrate` בכלל; מעכשיו — קצב הפתיחה, והגג הוא 900m."""
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "start"
    assert got["ceiling"] == "900m"
    assert got["previous"] is None


def test_a_configured_cap_below_600m_is_where_it_opens(conn):
    got = _next(conn, ceiling="200m")
    assert got["max_bitrate"] == "200m"
    assert got["basis"] == "start"
    assert got["ceiling"] == "200m"


def test_a_configured_cap_above_900m_raises_the_roof_not_the_start(conn):
    # 15/10 (ב): מחשב שיכפול שמהירותו לא ידועה מונח 1Gb/s — ואז הגג 900m גם
    # מתחת ל-"1g". כשהמקבל הוגדר 10G — הגג המוגדר הוא שקובע, כמו קודם.
    from server.rounds import nic_speed                                # noqa: PLC0415
    assert _next(conn, ceiling="1g")["ceiling"] == "900m"
    nic_speed.set_manual(conn, _drive("S1")["mac"], 10000)
    got = _next(conn, ceiling="1g")
    assert got["max_bitrate"] == "600m"
    assert got["ceiling"] == "1000m" and got["cap_by"] == "configured"


# --- למעלה: רק על ראיה --------------------------------------------------------


def test_an_unmeasured_wave_does_not_raise(conn):
    """בקרה שלילית — עלייה בלי ראיה: השולח לא דיווח (לוג שלא נקרא, סימולציה,
    גל שנעצר לפני הסוף). "לא נמדד" אינו "נקי" — נשארים במקום."""
    _wave(conn, "ses_1", measured=False)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "unmeasured"


# --- מחשב בנייה כמשדר (#715) ---------------------------------------------------


def test_a_direct_wave_opens_at_the_start_rate_and_never_climbs(conn):
    """המונה של udp-sender במחשב הבנייה, לא בשרת — אין ראיה לגל נקי. גם
    אחרי גל נקי מספרייה עם אותם כוננים: פתיחה."""
    _wave(conn, "ses_1")
    got = drive_rates.choose(conn, [_drive("S1"), _drive("S2")], None, direct=True)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "start" and got["previous"] is None
    assert got["direct"] is True


def test_a_direct_wave_respects_the_cap_and_a_known_slow_drive(conn):
    assert drive_rates.choose(conn, [_drive("S1")], "200m", direct=True)["max_bitrate"] == "200m"
    for n in range(2):
        _kept(conn, f"ses_old{n}", "S1", 50 * MB, 75 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], None, direct=True)
    assert got["max_bitrate"] == "360m" and got["basis"] == "learned"


def test_a_direct_wave_teaches_nothing_and_is_nobodys_previous(conn):
    """המקור (דיסק חי + דחיסה) יכול להיות האיטי — כוננים שכתבו לאט בגל
    כזה אינם "כוננים איטיים", וגל כזה אינו נקודת מוצא לגל מספרייה."""
    drive_rates.decide(conn, "ses_d", lambda: [_drive("S1")], None, direct=True)
    member = {"mac": "aa:bb:cc:00:00:21", "targets_json": json.dumps([{
        "dev": "sda", "state": "done", "serial": "S1", "bytes_written": 15_000 * MB,
        "first_moved_at": "2026-10-11T10:00:00+00:00",
        "moved_at": "2026-10-11T10:16:40+00:00"}])}
    assert drive_rates.record_wave(conn, "ses_d", [member]) == 0
    got = _next(conn, serials=("S1",))
    assert got["basis"] == "start" and got["previous"] is None


def test_the_build_machine_gets_the_decided_rate_or_the_start(conn):
    assert drive_rates.direct_bitrate(conn, None, None) == "600m"
    assert drive_rates.direct_bitrate(conn, "ses_d", "200m") == "200m"
    assert drive_rates.direct_bitrate(conn, "ses_d", "fast") == "fast"
    for n in range(2):
        _kept(conn, f"ses_old{n}", "S1", 50 * MB, 75 * MB)
    drive_rates.decide(conn, "ses_d", lambda: [_drive("S1")], None, direct=True)
    assert drive_rates.direct_bitrate(conn, "ses_d", None) == "360m"


# --- למטה, רצפה וגג ------------------------------------------------------------


def test_a_clean_wave_never_goes_above_the_configured_cap(conn):
    """בקרה שלילית — עלייה מעל הגג: `IMAGESRV_MAX_BITRATE` הוא מה שהשרת מכבד."""
    seen = [_wave(conn, f"ses_{n}", ceiling="200m") for n in range(3)]
    assert seen == ["200m", "200m", "200m"]
    got = _next(conn, ceiling="200m")
    assert got["max_bitrate"] == "200m" and got["basis"] == "unmeasured"


def test_a_lowered_cap_wins_over_what_worked_before(conn):
    for n in range(3):
        _wave(conn, f"ses_{n}")                   # 600 כל פעם: הגל הקודם מוריש את הקצב שלו
    got = _next(conn, ceiling="300m")
    assert got["max_bitrate"] == "300m" and got["basis"] == "ceiling"


def test_a_known_slow_drive_still_lowers_the_room(conn):
    """#447 נשאר: כונן שהגביל בשני גלים שונים מוריד את הגל אליו × 0.9.
    הגל הקודם (600m) היה נקי בזרם, אבל שני הכוננים כתבו בו חצי מהקצב —
    ול-S2 יש גל מגביל נוסף מקודם (50MB/s). S1 הגביל פעם אחת בלבד."""
    _kept(conn, "ses_old", "S2", 50 * MB, 75 * MB)
    _wave(conn, "ses_1", kept_up=False)
    got = _next(conn)
    assert got["max_bitrate"] == "360m"           # 50MB/s × 8 × 0.9
    assert got["basis"] == "learned"
    assert got["by"]["serial"] == "S2"


# --- זיכרון בין סבבים -----------------------------------------------------------


def test_the_last_wave_of_the_machine_is_the_starting_point(conn):
    _wave(conn, "ses_room", serials=("S1", "S2"))                    # 600
    _wave(conn, "ses_room2", serials=("S1", "S2"), ceiling="400m")   # 400
    _wave(conn, "ses_other", serials=("X1",), ceiling="200m", mac=MAC_B)   # מכונה אחרת, 200
    got = _next(conn, serials=("S1", "S2"))
    assert got["max_bitrate"] == "400m"
    assert got["previous"]["session_id"] == "ses_room2"
    assert got["previous"]["mac"] == MAC_A


def test_the_next_wave_of_a_round_with_fewer_drives_continues(conn):
    _wave(conn, "ses_1", serials=("S1", "S2", "S3"), ceiling="400m")
    got = _next(conn, serials=("S3",))
    assert got["max_bitrate"] == "400m"                      # מיותר מגירות — כמות שהוא (#1842)
    assert got["previous"]["session_id"] == "ses_1"


def test_new_drawers_on_a_known_machine_continue_from_its_last_wave(conn):
    """#1471, הכרעת נדב 16/10 — בקרה שלילית: בחדר מחליפים מגירות בכל גל.
    עד היום כונן שלא היה בגל הקודם החזיר את כל הגל לפתיחה (600m), ולכן
    הקצב לא טיפס לעולם. הזיכרון הוא של המכונה: הגל הקודם שלה הוא נקודת המוצא."""
    _wave(conn, "ses_1", serials=("S1", "S2"), ceiling="400m")
    got = _next(conn, serials=("N1", "N2"))
    assert got["max_bitrate"] == "400m"
    assert got["basis"] == "unmeasured"
    assert got["previous"]["session_id"] == "ses_1"
    assert got["previous"]["mac"] == MAC_A


def test_a_machine_never_seen_starts_the_whole_wave_at_the_opening_rate(conn):
    """מחשב שיכפול חדש (או שהוחלף — MAC חדש): אין לו ראיה שהוא עומד ב-700m.
    כל הגל יוצא בפתיחה, נמדד, ובפעם הבאה הוא מוכר."""
    _wave(conn, "ses_1", serials=("S1",), ceiling="400m")
    got = _next(conn, serials=("S1", "B1"), macs=[MAC_A, MAC_B])
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "start" and got["previous"] is None
    _wave(conn, "ses_2", serials=("S1", "B1"), ceiling="500m", mac=MAC_B)   # יצא — עכשיו מוכר
    got = _next(conn, serials=("S2", "B2"), macs=[MAC_A, MAC_B])
    assert got["max_bitrate"] == "400m" and got["basis"] == "unmeasured", got


def test_each_machine_is_judged_by_its_own_last_wave(conn):
    _wave(conn, "ses_1", serials=("S1",), ceiling="300m")    # A: 300
    _wave(conn, "ses_2", serials=("S2",))                    # A: 300 → 300 (הגל הקודם שלה)
    _wave(conn, "ses_3", serials=("B1",), ceiling="500m", mac=MAC_B)   # B: 500
    _wave(conn, "ses_4", serials=("S4",), ceiling="700m")    # A: 300 → 300 (מתחת לגג)
    got = _next(conn, serials=("S5",))
    assert got["max_bitrate"] == "300m" and got["previous"]["session_id"] == "ses_4"
    got = _next(conn, serials=("S5", "B2"), macs=[MAC_A, MAC_B])
    assert got["max_bitrate"] == "300m" and got["previous"]["mac"] == MAC_A


def test_a_drive_without_a_machine_is_an_unknown_machine(conn):
    """עיקרון 5: כונן בלי MAC אינו "מכונה מוכרת" — פתיחה, לא התעלמות. גם
    כשגל קודם נרשם עם כונן בלי MAC: None אינו מכונה שאפשר לזכור."""
    _wave(conn, "ses_0", serials=("S0",), mac=None)
    _wave(conn, "ses_1", serials=("S1",))
    got = drive_rates.choose(conn, [_drive("S2"), _drive("S3", mac=None)], None)
    assert got["max_bitrate"] == "600m" and got["basis"] == "start"


def test_a_known_slow_drawer_still_lowers_a_remembered_machine(conn):
    """ההגנה לכל כונן (#447) נשארת: המכונה מורישה 700m, אבל מגירה שהגבילה
    בשני גלים שונים (50MB/s) מורידה את הגל אליה × 0.9."""
    for n in range(2):
        _kept(conn, f"ses_old{n}", "SLOW", 50 * MB, 75 * MB)
    _wave(conn, "ses_1")                  # שתי מגירות, כמו הגל הבא (#1842)
    got = _next(conn, serials=("N1", "SLOW"))
    assert got["max_bitrate"] == "360m"
    assert got["basis"] == "learned" and got["by"]["serial"] == "SLOW"


def test_a_wave_is_not_its_own_previous(conn):
    """החלטה חוזרת לאותו גל נשענת על הגל שלפניו — לא על עצמה (אחרת גל
    נקי שנבחר מחדש היה מטפס מעצמו)."""
    assert _wave(conn, "ses_1", ceiling="400m") == "400m"
    assert _wave(conn, "ses_2", ceiling="300m") == "300m"
    again = drive_rates.decide(conn, "ses_2", lambda: [_drive("S1"), _drive("S2")], None,
                               adjust=lambda chosen, wave: chosen)
    assert again == "400m"                           # מ-ses_1, לא מעצמו (300m)


# --- המתג, ההחלטה והרישום -------------------------------------------------------


def test_with_the_switch_off_the_ceiling_is_fixed(conn):
    """#3036 (הכרעת נדב 12/11): כבוי = התקרה הקבועה, כמו שהנוסח בקונסולה אומר -- לא קצב הפתיחה."""
    _wave(conn, "ses_1")
    db.set_setting(conn, drive_rates.SETTING, "false")
    got = _next(conn)
    assert got["max_bitrate"] == "900m"
    assert got["basis"] == "disabled"


def test_the_journal_says_why(conn):
    _wave(conn, "ses_1", ceiling="400m")
    drive_rates.decide(conn, "ses_2", lambda: [_drive("S1"), _drive("S2")], None)
    detail = conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                          " ORDER BY id DESC").fetchone()[0]
    assert "400m" in detail and "basis=unmeasured" in detail and "ses_1" in detail


def test_a_broken_decision_falls_back_to_the_start_rate(conn):
    def broken():
        raise RuntimeError("boom")
    assert drive_rates.decide(conn, "ses_1", broken, None) == "600m"
    assert drive_rates.decide(conn, "ses_2", broken, "200m") == "200m"


