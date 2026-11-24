"""#3571: הקצוות של שלושת מישורי התקרה -- מה שלא נמדד, מה שפג, מה שלא מספר, ומה שנכשל בדרך לרישום.

הטסטים ב-`test_brain_planes_3571.py` בודקים את הנתיב המרכזי. כאן הקצוות שבהם מדידה חסרה או שגויה, ובכל אחד
הפסק נקרא בשמו (`NOT_MEASURED` / `UNDECIDED` / `STALE` / `SUPPRESSED`) ולא "הכול תקין":

- **רשת:** RTT שאינו מספר חיובי, ושדה `same` שאינו בוליאני, אינם דגימה. RTT זעיר עד כדי שהנוסחה מתאפסת אינו תקרה.
  דיווח זהה לקודם מדוכא גם כשהוא נמדד.
- **אחסון:** דיסק בלי סידורי, חלון שאינו מספר סופי, דיווח ישן, ודיווח בלי דיסק יעד -- לא נמדד, ולא "קצב".
  מחצית דיסקים (חציון זוגי) קובעת מי נשאר מאחור לפי הממוצע של שני האמצעיים.
- **הפצה:** דיווח ישן או זהה.
- **המחמיר:** תיקו בין שני מישורים נרשם בשמו; שלושה מישורים נמדדים שאף אחד אינו מגביל = `CLEAR`.
- **הרישום:** כשל בצילום, כשל בפסק, כשל ברישום וכשל ב-`note_send` -- ההחלטה (או השליחה) ממשיכה, והכשל נרשם בלוג.

**בקרה שלילית:** מוטציה אחת בקוד הייצור לכל קבוצה (ראו את גוף ה-PR) מפילה את הטסטים על הפסק או על הרישום.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from server import db
from server.brain import memory, planes

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
FAST_BPS = 100_000_000


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _disk(serial="FAST", durable_bps=FAST_BPS, backlog_bytes=1_000_000, slope_bps=0, window_s=3.0, behind_ticks=0):
    return planes.Disk(serial, durable_bps, backlog_bytes, slope_bps, window_s, behind_ticks)


def _store(*disks, library_bps=200_000_000, age_ms=100, same=False, current_kbps=400_000):
    return planes.Store(tuple(disks) or (_disk(),), library_bps, age_ms, same, current_kbps)


def _obs(*, net=None, store=None, dist=None):
    """שלושה מישורים שנמדדו: רשת נקיה, דיסק אחד של 100MB/s, והפצה בלי חוסר."""
    net = planes.Net(0, 20.0, 0, 100, False) if net is None else net
    store = _store() if store is None else store
    dist = planes.Dist(0, 2, 2, 500_000, False, 100, False) if dist is None else dist
    return planes.Observe(net, store, dist, NOW)


def _kbps(bps):
    return int(bps * 8 / 1000)


# --- רשת ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("rtt", [0, 0.0, -5.0, float("nan"), float("inf"), True, "20", None])
def test_an_rtt_that_is_not_a_positive_finite_number_is_not_a_measurement(rtt):
    found = planes.judge(_obs(net=planes.Net(50, rtt, 4, 100, False)))
    assert found.net.verdict == "NOT_MEASURED" and found.net.ceiling_kbps is None and not found.net.counts
    assert "rtt_ms=unmeasured" in found.net.evidence
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None


def test_an_rtt_so_small_the_formula_underflows_is_not_a_ceiling():
    """5e-324ms הוא מספר חיובי, אבל `rtt/1000` מתאפס: הנוסחה לא מחזירה מספר, ולכן אין תקרה (ולא חלוקה באפס)."""
    found = planes.judge(_obs(net=planes.Net(50, 5e-324, 4, 100, False)))
    assert found.net.verdict == "NOT_MEASURED" and found.net.ceiling_kbps is None and not found.net.counts
    assert "TFMCC" in found.net.reason
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None


def test_the_formula_returns_no_number_for_zero_loss_or_zero_rtt():
    assert planes._tfrc_kbps(0, 20.0) is None, "אבדן 0 אינו נכנס לנוסחה: אין תקרה, לא אינסוף"
    assert planes._tfrc_kbps(50, 0.0) is None
    assert isinstance(planes._tfrc_kbps(50, 20.0), int) and planes._tfrc_kbps(50, 20.0) > 0


@pytest.mark.parametrize("same", ["yes", 1, 0, "True"])
def test_a_same_flag_that_is_not_a_bool_is_not_a_measured_age(same):
    found = planes.judge(_obs(net=planes.Net(50, 30.0, 4, 100, same)))
    assert found.net.verdict == "NOT_MEASURED" and not found.net.counts and found.net.ceiling_kbps is None
    assert found.verdict == "UNDECIDED"


def test_a_measured_network_report_that_repeats_the_last_one_is_suppressed_but_keeps_its_ceiling():
    fresh = planes.judge(_obs(net=planes.Net(50, 30.0, 4, 100, False)))
    again = planes.judge(_obs(net=planes.Net(50, 30.0, 4, 100, True)))
    assert fresh.net.verdict == "CAPPED" and again.net.verdict == "SUPPRESSED"
    assert again.net.counts and again.net.ceiling_kbps == fresh.net.ceiling_kbps
    assert again.net.reason.startswith("דיווח רשת זהה לקודם") and fresh.net.reason in again.net.reason


# --- אחסון -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    _disk(serial=""),
    _disk(serial=None),
    _disk(window_s=None),
    _disk(window_s=True),
    _disk(window_s="3"),
    _disk(window_s=float("nan")),
    _disk(window_s=float("inf")),
    _disk(window_s=-1.0),
])
def test_a_disk_that_was_not_fully_measured_leaves_the_store_undecided(bad):
    found = planes.judge(_obs(store=_store(_disk("OTHER"), bad)))
    assert found.store.verdict == "UNDECIDED" and found.store.ceiling_kbps is None and not found.store.counts
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None


def test_a_serial_that_repeats_is_not_two_disks():
    found = planes.judge(_obs(store=_store(_disk("SAME"), _disk("SAME"))))
    assert found.store.verdict == "UNDECIDED" and not found.store.counts


def test_a_stale_store_report_expires_and_is_not_a_ceiling():
    found = planes.judge(_obs(store=_store(age_ms=planes.REPORT_STALE_MS + 1)))
    assert found.store.verdict == "STALE" and found.store.ceiling_kbps is None and not found.store.counts
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None


@pytest.mark.parametrize("age_ms", [None, -1, True, "100"])
def test_a_store_report_whose_age_was_not_measured_cannot_be_called_fresh(age_ms):
    found = planes.judge(_obs(store=_store(age_ms=age_ms)))
    assert found.store.verdict == "NOT_MEASURED" and not found.store.counts and found.store.ceiling_kbps is None


def test_a_store_report_with_the_library_read_but_no_target_disk_is_not_a_write_rate():
    found = planes.judge(_obs(store=planes.Store((), 200_000_000, 100, False, 400_000)))
    assert found.store.verdict == "NOT_MEASURED" and found.store.ceiling_kbps is None and not found.store.counts
    assert found.store.evidence == ("library_bps=200000000",)
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None


@pytest.mark.parametrize("current", [None, 0, -5, True, "400000"])
def test_without_a_known_current_rate_the_step_is_undecided_and_proposes_nothing(current):
    found = planes.judge(_obs(store=_store(current_kbps=current)))
    assert found.store.verdict == "PACE" and found.store.step == "undecided" and found.store.proposed_kbps is None
    assert found.store.counts and found.store.ceiling_kbps == _kbps(FAST_BPS)


def test_a_disk_that_wrote_nothing_in_the_window_proposes_no_step():
    """דיסק שלא כתב בייט בחלון תקין (durable=0): הקצב הנוכחי אינו משנה לאן יורדים, ולכן אין הצעה -- לא חלוקה באפס."""
    stalled = _disk("STALLED", durable_bps=0, backlog_bytes=5_000_000)
    found = planes.judge(_obs(store=_store(_disk(), stalled)))
    assert found.store.step == "undecided" and found.store.proposed_kbps is None


def test_the_median_of_an_even_count_is_the_mean_of_the_middle_pair():
    assert planes._median([100]) == 100.0
    assert planes._median([100, 80]) == 90.0
    assert planes._median([4, 1, 3, 2]) == 2.5


def _three(slow_bps):
    """שלושה דיסקים: שני מהירים (100M, 80M -- חציון 90M) ואחד איטי עם backlog שגדל, כבר הגיע לסף ההשארות מאחור."""
    slow = planes.Disk("SLOW", slow_bps, 80_000_000, 1_000_000, 3.0, planes.STORE_BEHIND_MAX)
    return _store(planes.Disk("A", 100_000_000, 1_000_000, 0, 3.0, 0), planes.Disk("B", 80_000_000, 1_000_000, 0, 3.0, 0), slow)


def test_a_disk_is_left_behind_only_below_half_of_the_median_of_the_others():
    # חציון של שניים = ממוצע האמצעיים = 90M, והסף 45M: 44M מאחור, 46M עדיין קובע את הקצב
    behind = planes.judge(_obs(store=_three(44_000_000)))
    assert "behind=SLOW" in behind.store.evidence
    assert behind.store.ceiling_kbps == _kbps(80_000_000)
    pacing = planes.judge(_obs(store=_three(46_000_000)))
    assert not any(e.startswith("behind=") for e in pacing.store.evidence)
    assert pacing.store.ceiling_kbps == _kbps(46_000_000)


# --- הפצה --------------------------------------------------------------------------------------------------------------


def test_a_stale_distribution_report_expires_and_is_not_a_ceiling():
    found = planes.judge(_obs(dist=planes.Dist(4, 1, 2, 3000, False, planes.REPORT_STALE_MS + 1, False)))
    assert found.dist.verdict == "STALE" and found.dist.ceiling_kbps is None and not found.dist.counts
    assert found.verdict == "UNDECIDED" and found.ceiling_kbps is None


@pytest.mark.parametrize("same", ["yes", 1])
def test_a_distribution_same_flag_that_is_not_a_bool_is_not_a_measured_age(same):
    found = planes.judge(_obs(dist=planes.Dist(4, 1, 2, 3000, False, 100, same)))
    assert found.dist.verdict == "NOT_MEASURED" and not found.dist.counts and found.dist.ceiling_kbps is None


def test_a_distribution_report_that_repeats_the_last_one_is_suppressed_but_keeps_its_ceiling():
    found = planes.judge(_obs(dist=planes.Dist(4, 1, 2, 3000, False, 100, True)))
    assert found.dist.verdict == "SUPPRESSED" and found.dist.counts and found.dist.ceiling_kbps == 3000
    assert found.dist.reason.startswith("דיווח הפצה זהה לקודם")
    assert found.verdict == "DIST" and found.ceiling_kbps == 3000


# --- המחמיר ------------------------------------------------------------------------------------------------------------


def test_a_tie_between_two_planes_is_recorded_by_name_and_the_earlier_plane_takes_the_verdict():
    ceiling = 100_000
    store = _store(library_bps=ceiling * 1000 // 8)                # תקרת האחסון: 100000 kbps
    found = planes.judge(_obs(store=store, dist=planes.Dist(4, 1, 2, ceiling, False, 100, False)))
    assert found.store.ceiling_kbps == ceiling and found.dist.ceiling_kbps == ceiling
    assert "tie=store,dist" in found.evidence
    assert found.verdict == "STORE" and found.ceiling_kbps == ceiling


def test_no_tie_is_recorded_when_one_plane_is_strictly_lower():
    found = planes.judge(_obs(dist=planes.Dist(4, 1, 2, 3000, False, 100, False)))
    assert found.verdict == "DIST" and not any(e.startswith("tie=") for e in found.evidence)


def test_three_measured_planes_that_none_of_them_limit_are_clear(monkeypatch):
    """היום מישור האחסון הנמדד תמיד נושא מספר, ולכן הענף הזה אינו נגיש מקלט אמיתי. מחליפים אותו במישור נמדד בלי תקרה
    (כמו מישור רשת נקי) ובודקים את ההכרעה של `judge` על שלושה כאלה: הם נספרים, אין מספר -- `CLEAR`, ולא `UNDECIDED`."""
    clear_store = planes._plane("store", planes.CLEAR, None, True, "נמדד ואינו מגביל", ())
    monkeypatch.setattr(planes, "_store", lambda observed: clear_store)
    found = planes.judge(_obs())
    assert found.net.counts and found.store.counts and found.dist.counts
    assert found.verdict == "CLEAR" and found.ceiling_kbps is None
    assert planes.obey("enforce", "on", found) is None, "פסק בלי מספר אינו נוגע בשולח"


# --- הרישום ------------------------------------------------------------------------------------------------------------


def _rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = ? ORDER BY id", (planes.RULE,))]


def test_a_decision_without_a_clock_is_recorded_without_a_snapshot(conn, caplog):
    with caplog.at_level(logging.ERROR, logger="imagesrv.brain"):
        found = planes.decide(conn, planes.Observe(), round_id="w-noclock")
    assert caplog.records == [], "אין שעון זה מצב רגיל של צילום, לא חריגה שנבלעה"
    assert found.verdict == "UNDECIDED"
    assert [r["action"] for r in _rows(conn)] == ["PLANE_NET", "PLANE_STORE", "PLANE_DIST", "PLANE_BIND"]
    shown = memory.decisions(conn, rule=planes.RULE)
    assert shown and all((row.get("snapshot") or {}).get("id") is None for row in shown), "אין שעון: אין צילום, וההחלטה עדיין נרשמת"


def test_a_snapshot_that_cannot_be_built_does_not_lose_the_decision(conn, monkeypatch, caplog):
    def broken(obs, round_id):
        raise RuntimeError("no snapshot today")
    monkeypatch.setattr(planes, "_shot", broken)
    with caplog.at_level(logging.ERROR, logger="imagesrv.brain"):
        found = planes.decide(conn, _obs(), round_id="w-snap")
    assert found.verdict == "STORE"
    assert [r["action"] for r in _rows(conn)] == ["PLANE_NET", "PLANE_STORE", "PLANE_DIST", "PLANE_BIND"]
    assert all((row.get("snapshot") or {}).get("id") is None for row in memory.decisions(conn, rule=planes.RULE))
    assert "w-snap" in caplog.text and "snapshot" in caplog.text


def test_a_judge_that_crashes_is_none_and_not_a_clear_verdict(conn, monkeypatch, caplog):
    def broken(obs):
        raise RuntimeError("boom")
    monkeypatch.setattr(planes, "judge", broken)
    with caplog.at_level(logging.ERROR, logger="imagesrv.brain"):
        assert planes.decide(conn, _obs(), round_id="w-crash") is None
    assert _rows(conn) == [], "אין פסק, ולכן אין מה לרשום"
    assert "w-crash" in caplog.text and "not judged" in caplog.text


def test_without_a_connection_the_verdict_is_returned_and_nothing_is_recorded(caplog):
    with caplog.at_level(logging.ERROR, logger="imagesrv.brain"):
        found = planes.decide(None, _obs(), round_id="w-noconn")
    assert found.verdict == "STORE" and found.ceiling_kbps == _kbps(FAST_BPS)
    assert caplog.records == [], "בלי חיבור לא מנסים לרשום, ולכן גם אין כשל רישום שנבלע"


def test_a_recording_that_fails_still_returns_the_verdict(conn, monkeypatch, caplog):
    def broken(conn_):
        raise RuntimeError("the mode could not be read")
    monkeypatch.setattr(planes, "_mode", broken)
    with caplog.at_level(logging.ERROR, logger="imagesrv.brain"):
        found = planes.decide(conn, _obs(), round_id="w-norecord")
    assert found.verdict == "STORE"
    assert _rows(conn) == []
    assert "w-norecord" in caplog.text and "not recorded" in caplog.text


def test_note_send_never_raises_and_says_nothing_was_recorded(conn, monkeypatch, caplog):
    def broken(conn_):
        raise RuntimeError("the mode could not be read")
    monkeypatch.setattr(planes, "_mode", broken)
    with caplog.at_level(logging.ERROR, logger="imagesrv.brain"):
        assert planes.note_send(conn, round_id="w-send") is None
    assert _rows(conn) == []
    assert "w-send" in caplog.text and "not recorded" in caplog.text


def test_note_send_records_once_per_wave_and_not_at_all_without_a_connection_or_a_wave(conn):
    assert planes.note_send(None, round_id="w-1") is None
    assert planes.note_send(conn, round_id="") is None
    first = planes.note_send(conn, round_id="w-1")
    assert first is not None and first.verdict == "UNDECIDED"
    assert len(_rows(conn)) == 4
    assert planes.note_send(conn, round_id="w-1") is None, "גל שכבר נרשם אינו נרשם פעם שנייה"
    assert len(_rows(conn)) == 4
