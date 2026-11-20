"""#3638 -- ה-ETA של הגל במסך החדר: קצב ממוצע מהבייט הראשון, ושלב האימות בפנים.

ברזל J3b (18/11/2026, `room_cc308e4c` / `ses_9b54c930`, משכפל 1 לבד, גל תקין): ה-`eta_s` של הקונסולה הציג 526 ש' כשנשארו
333 (+58%), 454 כשנשארו 267 (+70%), ו-34 כשנשארו 78 (-56%). הוא היה `max` על המגירות של `(נותר) / (קצב חלון של 10 ש')`,
וקצב חלון של מגירה בריאה נע בגל הזה בין 25 ל-90MB/s; ונגמר ב-`send_done`, 33 ש' לפני `room_done`.

הדגימות כאן הן שורות `poll.txt` של J3b, כמות שהן: זמן, בייטים והקצב של שתי המגירות (פורטים 1 ו-2), והמספר שהקונסולה
הציגה. "בפועל" = הזמן שנשאר עד `room_done` (03:14:33, `journal.txt`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server.rounds import room as room_mod  # noqa: E402

TOTAL = 18_545_476_822                       # `bytes_total` של שתי המגירות (poll.txt)
STARTED = "2026-11-18T03:08:02+00:00"        # send_start
ROOM_DONE = datetime(2026, 11, 18, 3, 14, 33, tzinfo=timezone.utc)
#: הבייט הראשון נכתב בין 03:08:11 (0 בייטים) ל-03:08:26 (15.5MB); אמצע החלון. הקצוות נבדקים בנפרד.
FIRST_BYTE = "2026-11-18T03:08:19+00:00"
THRESHOLD_S = 90                             # `room_mod.ETA_MIN_SAMPLE_S`, נשמר כאן במפורש -- ראו `test_the_threshold_is_90_seconds`
EARLIEST, LATEST = "2026-11-18T03:08:11+00:00", "2026-11-18T03:08:26+00:00"
#: `restore_stages` של `ses_fb4da9fd` (17/11, `lab/bld1117/argus1d/db-restore1.txt`): שלב `readback` 21.4 ו-35.6 ש' בשתי
#: מגירות; לא של J3b עצמו (שלב האימות לא נשמר בראיות שלו).
READBACK_S = 30
FORECAST = {"machines": [{"mac": "a0:48:1c:8a:18:40", "stages_s": {"readback": float(READBACK_S)}}]}

#: (שעה, בייטים מ1, קצב חלון מ1, בייטים מ2, קצב חלון מ2, `eta_s` שהקונסולה הציגה) -- `poll.txt`, שני פורטים `writing` עם קצב.
J3B = [
    ("03:08:26", 15558351, 1414395, 15558351, 1414395, 13100),
    ("03:08:42", 455872034, 7796633, 687458729, 23778264, 2320),
    ("03:08:58", 1101985732, 64611369, 1335430020, 32679464, 526),
    ("03:09:14", 1867093926, 26262167, 2256557009, 61328466, 635),
    ("03:09:30", 2677371884, 46164301, 3254797214, 63179593, 343),
    ("03:09:48", 3716422256, 86587531, 4252948652, 63583161, 224),
    ("03:10:04", 4855028381, 30134819, 5313977973, 51517719, 454),
    ("03:10:20", 5749668707, 81330938, 6273140708, 61818414, 198),
    ("03:10:37", 6862249081, 63129617, 7097272663, 53585834, 213),
    ("03:10:52", 7543190851, 52380136, 7909684053, 44625676, 238),
    ("03:11:08", 8216863142, 56139357, 8892532040, 62540021, 183),
    ("03:11:24", 9189379843, 54986225, 9734612437, 51300146, 171),
    ("03:11:40", 10172302825, 89356634, 10601839039, 56349240, 140),
    ("03:11:56", 11124496385, 36289189, 11671473979, 73912736, 204),
    ("03:12:11", 12292822760, 70613918, 12658292258, 75141134, 88),
    ("03:12:27", 12979937569, 62464982, 13594611612, 51007809, 97),
    ("03:12:42", 13895677186, 25181321, 14282746034, 69155773, 184),
    ("03:12:58", 15382791647, 86681250, 15333775068, 82365939, 38),
    ("03:13:13", 16225155291, 76733440, 16316333580, 64084635, 34),
    ("03:13:29", 17431926773, 91085190, 17352307504, 70150915, 17),
    ("03:13:44", 17846519259, 34549373, 17846519259, 41184312, 20),
    ("03:14:00", 18126480564, 9961636, 18302518748, 9961636, 42),
]
#: החמש שבהן `J3b.md` משווה הוצג מול בפועל: (שעה, הוצג, בפועל לפי `J3b.md`).
CITED = [("03:08:58", 526, 333), ("03:10:04", 454, 267), ("03:11:08", 183, 203),
         ("03:12:11", 88, 140), ("03:13:13", 34, 78)]


def _at(hms: str) -> datetime:
    return datetime.fromisoformat(f"2026-11-18T{hms}+00:00")


def _actual(hms: str) -> int:
    return int((ROOM_DONE - _at(hms)).total_seconds())


def _rate_stamp() -> str:
    # חותמת הקצב נבדקת מול שעון הקיר (`_stalled_s`), לא מול `_utcnow`: טרייה.
    return (datetime.now(timezone.utc) - timedelta(seconds=4)).isoformat(timespec="seconds")


def _drawers_at(row, monkeypatch, first_byte: str = FIRST_BYTE) -> list[dict]:
    """שתי המגירות של J3b ברגע הדגימה, אחרי `_pace` -- מה ש-`drawer_list` נושא."""
    hms, w1, r1, w2, r2, _shown = row
    monkeypatch.setattr(room_mod, "_utcnow", lambda: _at(hms))
    drawers = [{"state": "writing", "bytes_total": TOTAL, "bytes_written": written,
                "recent_bps": rate, "rate_at": _rate_stamp(), "first_moved_at": first_byte}
               for written, rate in ((w1, r1), (w2, r2))]
    return [{**d, **room_mod._pace(d, STARTED)} for d in drawers]


def _wave_at(row, monkeypatch, first_byte: str = FIRST_BYTE, forecast: dict | None = FORECAST) -> dict:
    drawers = _drawers_at(row, monkeypatch, first_byte)
    return room_mod._wave_pace(STARTED, [{"drawer_list": drawers}], forecast)


def _error(row, monkeypatch, **kw) -> float:
    eta = _wave_at(row, monkeypatch, **kw)["eta_s"]
    assert eta is not None, row[0]
    return (eta - _actual(row[0])) / _actual(row[0])


def _after_threshold(first_byte: str = FIRST_BYTE):
    first = datetime.fromisoformat(first_byte)
    return [r for r in J3B if (_at(r[0]) - first).total_seconds() >= THRESHOLD_S]


def test_the_threshold_is_90_seconds():
    assert room_mod.ETA_MIN_SAMPLE_S == THRESHOLD_S


def test_the_fixture_is_what_the_console_showed_and_what_was_left():
    """שומר על הדגימות עצמן, לא על הקוד: ה-`eta_s` שהוצג שווה ל-`max` של (נותר / קצב חלון), ו"בפועל" שווה (עד 3 ש')
    למה ש-`J3b.md` מדווח. אם זה נשבר -- הדגימות הועתקו לא נכון, והטסטים שמתחתיו אינם מדברים על J3b."""
    by_time = {r[0]: r for r in J3B}
    for hms, shown, actual in CITED:
        _, w1, r1, w2, r2, console = by_time[hms]
        assert console == shown
        assert max(int((TOTAL - w1) / r1), int((TOTAL - w2) / r2)) == shown
        assert abs(_actual(hms) - actual) <= 3, (hms, _actual(hms), actual)


@pytest.mark.parametrize("hms", ["03:08:26", "03:08:42", "03:08:58", "03:09:14", "03:09:30"])
def test_no_eta_in_the_first_90_seconds_of_writing(hms, monkeypatch):
    """לפני `ETA_MIN_SAMPLE_S` מהבייט הראשון -- "לא נמדד". הקונסולה אז נופלת להערכה לפי היסטוריה. ב-J3b הוצג כאן
    13100 / 2320 / 526 / 635 / 343 ש'; 526 מול 333 בפועל (+58%) ו-13100 מול 367."""
    row = next(r for r in J3B if r[0] == hms)
    assert (_at(hms) - datetime.fromisoformat(FIRST_BYTE)).total_seconds() < THRESHOLD_S
    drawers = _drawers_at(row, monkeypatch)
    assert [d["eta_s"] for d in drawers] == [None, None], drawers
    assert all(d["rate_bps"] is not None for d in drawers), "הקצב עצמו (חלון) עדיין נמדד ומוצג"
    wave = room_mod._wave_pace(STARTED, [{"drawer_list": drawers}], FORECAST)
    assert wave["eta_s"] is None, wave
    assert wave["rate_bps"] is not None


def test_after_90_seconds_the_eta_tracks_what_was_left_until_room_done(monkeypatch):
    """J3b: בכל דגימה שאחרי הסף, `eta_s` (כתיבה + אימות) בטווח של -25%..+25% מהזמן שנשאר עד `room_done`. לפני התיקון:
    +70% ב-03:10:04, -37% ב-03:12:11, -56% ב-03:13:13."""
    rows = _after_threshold()
    assert len(rows) >= 15, "הטסט אינו בודק כמעט כלום"
    errors = {r[0]: round(_error(r, monkeypatch) * 100) for r in rows}
    assert all(-25 <= e <= 25 for e in errors.values()), errors


@pytest.mark.parametrize("first_byte", [EARLIEST, LATEST])
def test_the_band_holds_at_both_ends_of_the_unknown_first_byte(first_byte, monkeypatch):
    """הבייט הראשון בין שתי דגימות; בשני הקצוות התחלה מוקדמת נותנת קצב ממוצע נמוך וחיזוי גבוה, ולא יותר מ-+50%."""
    errors = {r[0]: round(_error(r, monkeypatch, first_byte=first_byte) * 100) for r in _after_threshold(first_byte)}
    assert errors and all(-25 <= e <= 50 for e in errors.values()), errors


def test_the_cited_samples_are_no_longer_off_by_more_than_a_third(monkeypatch):
    """הדגימות ש-`J3b.md` מצטט שאחרי הסף: 454 מול 267 (+70%), 88 מול 140 (-37%), 34 מול 78 (-56%)."""
    for hms, _shown, actual in CITED[1:]:
        row = next(r for r in J3B if r[0] == hms)
        eta = _wave_at(row, monkeypatch)["eta_s"]
        assert abs(eta - actual) / actual <= 0.33, (hms, eta, actual)


def test_the_verification_is_in_the_eta_and_the_wave_says_so(monkeypatch):
    """ב-J3b ה-ETA נגמר עם `send_done`, ו-33 ש' אחריו עוד היו אימות. עם אומדן מההיסטוריה: `eta_s` כולל אותו,
    ו-`eta_verify_s` אומר כמה."""
    row = next(r for r in J3B if r[0] == "03:13:13")
    with_it = _wave_at(row, monkeypatch)
    without = _wave_at(row, monkeypatch, forecast=None)
    assert with_it["eta_verify_s"] == READBACK_S
    assert without["eta_verify_s"] is None
    assert with_it["eta_s"] == without["eta_s"] + READBACK_S
    assert abs(with_it["eta_s"] - 78) < abs(without["eta_s"] - 78)


@pytest.mark.parametrize("forecast", [
    None, {}, {"machines": []}, {"seconds": None, "why": "no_history", "machines": [], "missing": ["a0"]},
    {"machines": [{"stages_s": None}]}, {"machines": [{"stages_s": {"gpt": 3.0}}]},
    {"machines": [{"stages_s": {"readback": 0}}]}, {"machines": [{"stages_s": {"readback": True}}]},
    {"machines": [{"stages_s": {"readback": 30.0}}, {"stages_s": None}]},      # מכונה אחת בלי שלב מדוד
])
def test_no_measured_verification_is_not_a_guess(forecast, monkeypatch):
    """בלי אומדן מההיסטוריה (או כשמכונה אחת בלעדיו) אין תוספת ואין קבוע מומצא: `eta_verify_s` None, ו-`eta_s`
    הוא הכתיבה בלבד -- וזה כתוב בשדה."""
    row = next(r for r in J3B if r[0] == "03:11:08")
    wave = _wave_at(row, monkeypatch, forecast=forecast)
    assert wave["eta_verify_s"] is None
    assert wave["eta_s"] == _wave_at(row, monkeypatch, forecast=None)["eta_s"] is not None


def test_the_slowest_machine_sets_the_verification(monkeypatch):
    row = next(r for r in J3B if r[0] == "03:11:08")
    forecast = {"machines": [{"stages_s": {"readback": 12.0}}, {"stages_s": {"readback": 41.6}}]}
    assert _wave_at(row, monkeypatch, forecast=forecast)["eta_verify_s"] == 41


def test_a_drawer_that_has_not_reached_the_threshold_keeps_the_wave_eta_unknown(monkeypatch):
    """הכללה של #3598: מגירה אחת נמדדת (200 ש' מהבייט הראשון) והשנייה התחילה לכתוב לפני 30 ש' -- לא נעלמת מה-`max`."""
    monkeypatch.setattr(room_mod, "_utcnow", lambda: _at("03:12:00"))
    old = {"state": "writing", "bytes_total": TOTAL, "bytes_written": 9_000_000_000, "recent_bps": 50_000_000,
           "rate_at": _rate_stamp(), "first_moved_at": "2026-11-18T03:08:40+00:00"}
    new = {**old, "bytes_written": 1_500_000_000, "first_moved_at": "2026-11-18T03:11:30+00:00"}
    drawers = [{**d, **room_mod._pace(d, STARTED)} for d in (old, new)]
    assert drawers[0]["eta_s"] is not None and drawers[1]["eta_s"] is None
    assert drawers[1]["rate_bps"] == 50_000_000
    assert room_mod._wave_pace(STARTED, [{"drawer_list": drawers}], FORECAST)["eta_s"] is None


@pytest.mark.parametrize("broken", [
    {"first_moved_at": None}, {"first_moved_at": "yesterday"}, {"first_moved_at": "2026-11-18T03:08:19"},   # בלי אזור זמן
    {"first_moved_at": "2026-11-18T04:00:00+00:00"},                                                    # בעתיד: שעון שחזר אחורה
    {"bytes_written": 0},
])
def test_an_average_that_cannot_be_measured_is_none_not_a_number(broken, monkeypatch):
    monkeypatch.setattr(room_mod, "_utcnow", lambda: _at("03:12:00"))
    target = {"state": "writing", "bytes_total": TOTAL, "bytes_written": 9_000_000_000, "recent_bps": 50_000_000,
              "rate_at": _rate_stamp(), "first_moved_at": FIRST_BYTE, **broken}
    assert room_mod._pace(target, STARTED)["eta_s"] is None


def test_the_rate_on_screen_is_still_the_window_and_the_eta_comes_from_the_average(monkeypatch):
    """`rate_bps` נשאר הקצב הנוכחי (חלון 10 ש'); רק `eta_s` נגזר מהממוצע. 9GB ב-220 ש' = 40.9MB/s, נשארו 9.5GB."""
    monkeypatch.setattr(room_mod, "_utcnow", lambda: _at("03:12:00"))
    target = {"state": "writing", "bytes_total": TOTAL, "bytes_written": 9_000_000_000, "recent_bps": 75_000_000,
              "rate_at": _rate_stamp(), "first_moved_at": "2026-11-18T03:08:20+00:00"}
    pace = room_mod._pace(target, STARTED)
    assert pace["rate_bps"] == 75_000_000
    assert pace["eta_s"] == int((TOTAL - 9_000_000_000) / (9_000_000_000 / 220))
