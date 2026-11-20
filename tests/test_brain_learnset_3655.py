"""#3655 (אפיק #3653): הלמידה האוטומטית שכותבת הגדרות (S37, K-LEARN) -- המוח מחליט (`server/brain/learnset.py`), `learn/tuning.py` אוסף ראיות ומבצע.

- **ההתנהגות זהה לזו של לפני #3655.** `OLD_*` הם העתקים מילוליים של ההחלטה כפי שישבה ב-`tuning._floor_change` / `_start_change` / `_bounded`
  בבסיס (`lab/vm-1114f`, `13828c0e4`), בתור אורקל בטסט בלבד. הטסטים של #2028 (`test_learning_2028`) לא השתנו ובודקים את הביצוע.
- **הלמידה רק כלפי מעלה** (נדב 18/11, "כמו היום, MAX_STEP 1.5"): הצעד חסום, ו"לא מעל הנוכחי" אינו שינוי.
- **אין כתיבה שקטה להגדרות:** כל החלטה היא שורה ב-`brain_decisions` (ישות `settings`) **לפני** הכתיבה, והתוצאה ("נכתב: learning_changes#N") אחריה.
- **אין החלטה = לא משנים הגדרה.** `off` מחליט ואינו רושם.
- `FLOOR_OF_MEDIAN`, `MAX_STEP` ו-`MODEL_OUTLIER` הוסרו מ-`tuning.py` (לא אליאסים). `tuning.REQUIRED_WAVES` נשאר שם אחד לתצוגה בלבד, כי `console/api.py` קורא אותו.

**בקרה שלילית:** בבסיס אין `learnset.py` וההחלטה יושבת ב-`tuning.py`; הטסטים שעוברים דרך `record_wave` נופלים ב-assert על `brain_decisions` ריק, והטסטים
של המודול ב-`assert learnset is not None`. השומרים שעוברים בשני המצבים מסומנים (guard).
"""

from __future__ import annotations

import statistics

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import silence
from server.disks import writefloor
from server.learn import tuning
from server.rounds import drive_rates

try:                                    # בקרה שלילית: בלי המודול ההתנהגות נבדקת, לא ה-import
    from server.brain import learnset
except ImportError:                     # pragma: no cover
    learnset = None

from test_learning_2028 import _on, _wave   # noqa: E402
from test_server_room import CLONER1        # noqa: E402

OLD_STEP, OLD_OF_MEDIAN, OLD_OUTLIER = 1.5, 0.5, 3


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "imagesrv.db")
    connection.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'משכפלים', 'cloner')")
    connection.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '01', 'g', ?)",
                       (CLONER1, db.now_iso()))
    connection.commit()
    return connection


@pytest.fixture(autouse=True)
def _fresh_registers():
    silence.forget()
    if learnset is not None:
        learnset.forget()
    yield
    silence.forget()
    if learnset is not None:
        learnset.forget()


def decisions(conn, verdict=None):
    rows = [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K-LEARN' ORDER BY id")]
    return [r for r in rows if verdict is None or r["verdict"] == verdict]


# --- אורקל: הקוד שהוסר ---------------------------------------------------------------------------------------------


def OLD_BOUNDED(current, target):
    return min(target, int(current * OLD_STEP))


def OLD_FLOOR(current, speeds, serials, streams, maximum, margin, min_samples, min_serials):
    """`tuning._floor_change` בבסיס: הערך החדש, או None (לא משנים)."""
    if len(speeds) < min_samples or serials < min_serials:
        return None
    if not streams or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in streams):
        return None
    median, slowest, stream = statistics.median(speeds), min(speeds), int(min(streams) / 10 ** 6)
    target = OLD_BOUNDED(current, min(int(median * OLD_OF_MEDIAN), int(slowest * margin), stream, maximum))
    return target if target > current else None


def OLD_START(current, proven, waves, worst, limit, step):
    if not waves or None in proven:
        return None
    if worst > limit:
        return None
    target = OLD_BOUNDED(current, min(proven)) // step * step
    return target if target > current else None


def test_the_module_exists_and_its_values_are_the_old_ones():
    assert learnset is not None, "server/brain/learnset.py חסר"
    assert (learnset.REQUIRED_WAVES, learnset.FLOOR_OF_MEDIAN, learnset.MAX_STEP, learnset.MODEL_OUTLIER) == (2, OLD_OF_MEDIAN, OLD_STEP, OLD_OUTLIER)


def test_the_floor_decision_is_exactly_what_tuning_decided_before():
    assert learnset is not None, "server/brain/learnset.py חסר"
    checked = 0
    for current in (10, 50, 75, 100, 333, 500):
        for speeds in ([140, 150], [140, 150, 200], [60, 400, 410], [20, 25], [500, 520, 540], [3, 600]):
            for serials in (1, 2, 3):
                for streams in ([], [None], [112_000_000], [700_000_000 // 8, 500_000_000], [10 ** 9, True]):
                    got = learnset.judge_floor(key="k", current=current, speeds=speeds, serials=serials, streams=streams, rejected=0, maximum=500,
                                               margin=0.9, min_samples=2, min_serials=2)
                    want = OLD_FLOOR(current, speeds, serials, streams, 500, 0.9, 2, 2)
                    assert (got.verdict == "RAISE", got.value) == (want is not None, want), (current, speeds, serials, streams)
                    checked += 1
    assert checked == 6 * 6 * 3 * 5


def test_the_start_decision_is_exactly_what_tuning_decided_before():
    assert learnset is not None
    checked = 0
    for current in (600_000_000, 700_000_000, 900_000_000):
        for proven in ([700_000_000, 800_000_000], [900_000_000], [None, 700_000_000], [650_000_000, 650_000_000], [10 ** 9, 10 ** 9]):
            for waves in (0, 1, 2):
                for worst in (0.0, 0.0005, 0.001, 0.0011, 0.02):
                    got = learnset.judge_start(key="k", current=current, proven=proven, waves=waves, worst_loss=worst, loss_limit=0.001, step=100_000_000)
                    want = OLD_START(current, proven, waves, worst, 0.001, 100_000_000)
                    assert (got.verdict == "RAISE", got.value) == (want is not None, want), (current, proven, waves, worst)
                    checked += 1
    assert checked == 3 * 5 * 3 * 5


def test_the_step_and_the_measurement_filter_are_the_old_ones():
    assert learnset is not None
    for current in (10, 50, 100, 600):
        for target in (1, 60, 75, 76, 10_000):
            assert learnset.bounded(current, target) == OLD_BOUNDED(current, target)
    for mbps in (10, 100, 600, 601):
        for limit in (100, 600, 8000):
            for base in (None, 50, 150, 200):
                assert learnset.measurement_sane(mbps, limit, base) == (mbps <= limit and (base is None or mbps <= base * OLD_OUTLIER))


def test_the_learning_never_lowers_a_setting():
    assert learnset is not None
    got = learnset.judge_floor(key="k", current=100, speeds=[60, 70], serials=2, streams=[10 ** 9], rejected=0, maximum=500, margin=0.9,
                               min_samples=2, min_serials=2)
    assert got.verdict == "UNCHANGED" and got.value is None
    got = learnset.judge_start(key="k", current=900_000_000, proven=[700_000_000], waves=1, worst_loss=0.0, loss_limit=0.001, step=100_000_000)
    assert got.verdict == "UNCHANGED" and got.value is None


def test_the_old_constants_are_removed_from_tuning_not_aliased():
    for name in ("FLOOR_OF_MEDIAN", "MAX_STEP", "MODEL_OUTLIER", "_bounded"):
        assert not hasattr(tuning, name), name
    # נשאר שם אחד לתצוגה בלבד: `console/api.py` קורא אותו, והערך הוא של המוח
    assert learnset is not None and tuning.REQUIRED_WAVES is learnset.REQUIRED_WAVES


# --- דרך השרת: כל החלטה נרשמת לפני הכתיבה ונסגרת אחריה ----------------------------------------------------------------


def test_a_learning_run_records_every_decision_with_its_evidence_and_what_became_of_it(conn):
    _on(conn)
    tuning.record_wave(conn, _wave(conn, speeds=(140, 150, 200)))
    assert decisions(conn) == [], "גל אחד אינו סבב: אין מה להחליט"
    tuning.record_wave(conn, _wave(conn, speeds=(140, 150, 200)))
    assert writefloor.mbps(conn) == 75 and drive_rates.learned_start(conn) == 700_000_000
    run, floor, start = decisions(conn)
    assert (run["verdict"], run["action"], run["point"], run["mode"], run["entity_kind"]) == ("RUN_DONE", "LEARN_RUN", "S37", "shadow", "settings")
    assert (floor["verdict"], floor["entity_key"], floor["action"]) == ("RAISE", writefloor.SETTING, "LEARN_SETTING")
    assert (start["verdict"], start["entity_key"]) == ("RAISE", drive_rates.START_SETTING)
    assert "median=150" in floor["evidence_json"] and "slowest=140" in floor["evidence_json"] and "max_step=1.5" in floor["evidence_json"]
    changes = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM learning_changes")}
    assert floor["outcome"] == f"נכתב: learning_changes#{changes[writefloor.SETTING]}, 50 MB/s -> 75 MB/s"
    assert start["outcome"].startswith(f"נכתב: learning_changes#{changes[drive_rates.START_SETTING]}, 600 Mb/s -> 700 Mb/s")
    assert run["outcome"] == "הסבב נסגר: למד"
    # הסדר: ההחלטה נרשמה לפני הכתיבה
    assert run["id"] < floor["id"] < start["id"]


def test_a_short_run_changes_nothing_and_says_so(conn):
    _on(conn)
    tuning.record_wave(conn, _wave(conn))
    tuning.record_wave(conn, _wave(conn, ended="failed"))
    assert writefloor.mbps(conn) == 50 and drive_rates.learned_start(conn) is None
    [run] = decisions(conn)
    assert run["verdict"] == "RUN_SHORT" and "לא נלמד: 1 מתוך 2" in run["reason"] and run["outcome"].startswith("הסבב נסגר: לא נלמד")


def test_a_setting_that_does_not_rise_is_a_decision_too_and_nothing_is_written(conn):
    db.set_setting(conn, writefloor.SETTING, "100")
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, speeds=(140, 150)))
    assert writefloor.mbps(conn) == 100
    [floor] = [r for r in decisions(conn) if r["entity_key"] == writefloor.SETTING]
    assert floor["verdict"] == "UNCHANGED" and floor["outcome"] == "לא נכתב דבר: ההגדרה נשארה כמות שהיא"


def test_no_decision_is_no_change_and_one_undecided_row(conn, monkeypatch):
    """המוח נפל בסף: הסף לא זז, והמנוע לא מחשב אותו בעצמו. קצב הפתיחה, שהמוח החליט עליו, כן."""
    def boom(**kw):
        raise RuntimeError("brain down")
    monkeypatch.setattr(learnset, "judge_floor", boom)
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, speeds=(140, 150, 200)))
    assert writefloor.mbps(conn) == 50, "אין החלטה: ההגדרה לא השתנתה"
    assert drive_rates.learned_start(conn) == 700_000_000
    assert [(r["verdict"], r["entity_key"]) for r in decisions(conn, "UNDECIDED")] == [("UNDECIDED", writefloor.SETTING)]
    row = next(c for c in tuning.status(conn)["run"]["unchanged"] if c["key"] == writefloor.SETTING)
    assert "המוח לא הצליח להחליט" in row["why_he"]


def test_off_decides_and_does_not_record(conn):
    db.set_setting(conn, "brain_veto_mode", "off")
    _on(conn)
    for _ in range(2):
        tuning.record_wave(conn, _wave(conn, speeds=(140, 150, 200)))
    assert writefloor.mbps(conn) == 75 and drive_rates.learned_start(conn) == 700_000_000
    assert decisions(conn) == []


def test_the_rule_is_registered_where_the_console_and_the_forecast_look_for_it():
    from server.brain import forecast, say
    assert learnset is not None
    assert {learnset.ACTION, learnset.RUN_ACTION} <= set(say.ACTION_HE)
    assert {learnset.RAISE, learnset.UNCHANGED, learnset.RUN_DONE, learnset.RUN_SHORT} <= set(say.VERDICT_HE)
    assert learnset.RULE in say.EXECUTED and forecast.predicts(learnset.RULE) is not None
