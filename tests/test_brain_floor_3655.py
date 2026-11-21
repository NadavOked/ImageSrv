"""#3655 (אפיק #3653): רצפת הכתיבה לדיסק וציפיית ה-HDD (S34, K-FLOOR) -- המוח מחליט (`server/brain/floorrule.py`), `writefloor.py` ו-
`hdd_policy.py` מבצעים.

- **ההתנהגות זהה לזו של לפני #3655.** `OLD_*` הם העתקים מילוליים של ההחלטה כפי שישבה ב-`disks/writefloor.py` וב-`disks/hdd_policy.py`
  בבסיס (`lab/vm-1114f`, `13828c0e4`), בתור אורקל בטסט בלבד: רשת של הגדרות, תפקידים, מתגים והיסטוריות, והתוצאה חייבת להיות זהה.
  הטסטים של #2338 ושל X16 (`test_writecheck_floor_2338`, `test_hdd_policy_x16`) לא השתנו חוץ מהמקום שבו הקבועים יושבים.
- **הקבועים עברו למוח, לא אליאסים:** `writefloor.DEFAULT/MINIMUM/MAXIMUM/parse` ו-`hdd_policy.INITIAL_MBPS/MARGIN/WINDOW` אינם עוד.
- **כל החלטה לדיסק נרשמת** ב-`brain_decisions` (ישות `disk`). `off` מחליט ואינו רושם. **אין החלטה = הרצפה של דיסק מוצק**, ושורת
  `UNDECIDED` אחת לדיסק.

**בקרה שלילית:** בבסיס אין `floorrule.py`; הטסטים שעוברים דרך `hdd_policy.decision` נופלים ב-assert על `brain_decisions` ריק, והטסטים של
המודול ב-`assert floorrule is not None`.
"""

from __future__ import annotations

import json
from statistics import median

import pytest

from server import db
from server.brain import silence
from server.disks import hdd_policy as hdd, writefloor

try:                                    # בקרה שלילית: בלי המודול ההתנהגות נבדקת, לא ה-import
    from server.brain import floorrule
except ImportError:                     # pragma: no cover
    floorrule = None

# הערכים של הקוד שהוסר
OLD_DEFAULT, OLD_MIN, OLD_MAX, OLD_INITIAL, OLD_MARGIN, OLD_WINDOW = 50, 10, 500, 20, 0.5, 10
H = {"serial": "WD-H1", "model": "WDC WD10EZEX", "rotational": 1}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _fresh_registers():
    silence.forget()
    if floorrule is not None:
        floorrule.forget()
    yield
    silence.forget()
    if floorrule is not None:
        floorrule.forget()


def OLD_PARSE(raw):
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() and not raw.startswith("0") and len(raw) <= 3:
        value = int(raw)
    else:
        return None
    return value if OLD_MIN <= value <= OLD_MAX else None


def OLD_MBPS(raw):
    if raw is None:
        return OLD_DEFAULT
    value = OLD_PARSE(raw)
    return OLD_DEFAULT if value is None else value


def OLD_EXPECT(rows):
    rows = [r for r in rows if r[0] > 0 and r[1] > 0][:OLD_WINDOW]
    if not rows:
        return None
    return max(1, int(median(r[0] / r[1] / 1_000_000 for r in rows) * OLD_MARGIN))


def OLD_DECISION(raw, rotational, role, support, rows):
    """`hdd_policy.decision` מילה במילה: (סיבת סירוב או None, הרצפה)."""
    if role == "source" or not rotational:
        return None, OLD_MBPS(raw)
    if not support:
        return hdd.REFUSED, None
    learned = OLD_EXPECT(rows)
    return (None, OLD_INITIAL) if learned is None else (None, learned)


def rows_of(n, mbps=100):
    return [(mbps * 10_000_000 + i, 10) for i in range(n)]


def decisions(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K-FLOOR' ORDER BY id")]


def _waves(conn, serial, model, mbps_list):
    for i, mbps in enumerate(mbps_list):
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?, ?, ?, 0, 0, ?, 10, '2026-11-15T00:00:00Z')",
                     (serial, model, f"s{serial}{model}{i}", mbps * 10_000_000))
    conn.commit()


def test_the_module_exists_and_its_values_are_the_old_ones():
    assert floorrule is not None, "server/brain/floorrule.py חסר"
    assert (floorrule.DEFAULT, floorrule.MINIMUM, floorrule.MAXIMUM) == (OLD_DEFAULT, OLD_MIN, OLD_MAX)
    assert (floorrule.INITIAL_MBPS, floorrule.EXPECT_MARGIN, floorrule.WINDOW) == (OLD_INITIAL, OLD_MARGIN, OLD_WINDOW)


def test_the_floor_is_exactly_what_the_two_files_decided_before():
    assert floorrule is not None, "server/brain/floorrule.py חסר"
    raws = (None, "50", "abc", "0", "9", "10", "500", "501", "070", "70", 70, 9, 500, 501, True, "", " 70", "٧٠", "1000")
    checked = 0
    for raw in raws:
        assert floorrule.parse(raw) == OLD_PARSE(raw), raw
        assert floorrule.setting_floor(raw)[0] == OLD_MBPS(raw), raw
        for rotational in (0, 1):
            for role in ("source", "target"):
                for support in (False, True):
                    for n, mbps in ((0, 100), (1, 100), (3, 120), (10, 60), (14, 400)):
                        rows = rows_of(n, mbps)
                        got = floorrule.judge_floor(raw, rotational=rotational, role=role, support=support, rates=lambda rows=rows: rows)
                        want = OLD_DECISION(raw, rotational, role, support, rows)
                        assert ((hdd.REFUSED if got.verdict == floorrule.REFUSED else None), got.mbps) == want, (raw, rotational, role, support, n)
                        checked += 1
    assert checked == len(raws) * 2 * 2 * 2 * 5


def test_the_expectation_is_half_the_median_of_the_last_window_and_none_without_a_wave():
    assert floorrule is not None
    for rows in ([], [(0, 10)], [(10, 0)], rows_of(1, 7), rows_of(3, 120), rows_of(14, 400), [(None, 5), (5_000_000, 5)]):
        clean = [r for r in rows if isinstance(r[0], (int, float)) and isinstance(r[1], (int, float))]
        assert floorrule.expect(rows) == OLD_EXPECT(clean), rows


def test_the_old_constants_are_removed_from_the_disk_files_not_aliased():
    for name in ("DEFAULT", "MINIMUM", "MAXIMUM", "parse"):
        assert not hasattr(writefloor, name), name
    for name in ("INITIAL_MBPS", "MARGIN", "WINDOW"):
        assert not hasattr(hdd, name), name


def test_a_disk_decision_is_recorded_with_its_evidence_per_verdict(conn):
    db.set_setting(conn, "hdd_support", "true")
    _waves(conn, "WD-H1", "WDC WD10EZEX", [100, 120, 80])
    assert hdd.decision(conn, H) == (None, 50)
    assert hdd.decision(conn, {**H, "serial": "WD-H9"}) == (None, 20)
    db.set_setting(conn, "hdd_support", "false")
    assert hdd.decision(conn, H) == (hdd.REFUSED, None)
    assert hdd.decision(conn, {"serial": "SSD1", "model": "X", "rotational": 0}) == (None, 50)
    got = decisions(conn)
    assert [(r["verdict"], r["entity_kind"], r["entity_key"]) for r in got] == [
        ("LEARNED", "disk", "WD-H1"), ("ESTIMATE", "disk", "WD-H9"), ("REFUSED", "disk", "WD-H1"), ("FROM_SETTING", "disk", "SSD1")]
    assert all((r["point"], r["action"], r["mode"]) == ("S34", "WRITE_FLOOR", "shadow") for r in got)
    assert "hdd_support=on" in got[0]["evidence_json"] and "window=10" in got[0]["evidence_json"]
    assert "hdd_support=off" in got[2]["evidence_json"]


def test_a_source_is_never_refused_and_says_so(conn):
    assert hdd.decision(conn, H, role="source") == (None, 50)
    [row] = decisions(conn)
    assert row["verdict"] == "FROM_SETTING" and "מקור אינו נפסל" in row["reason"]


def test_off_decides_and_does_not_record(conn):
    db.set_setting(conn, "brain_veto_mode", "off")
    assert hdd.decision(conn, H) == (hdd.REFUSED, None)
    assert decisions(conn) == []


def test_the_repair_floor_asks_the_brain_once_per_disk_and_the_number_is_the_setting(conn):
    db.set_setting(conn, writefloor.SETTING, "80")
    db.set_setting(conn, "hdd_support", "true")
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)",
                 ("aa:bb:cc:dd:ee:01", json.dumps([{"serial": "SSD1", "model": "X", "rotational": 0}])))
    conn.commit()
    floor = hdd.RepairFloor(conn)
    assert int(floor) == 80
    assert floor.for_disk("aa:bb:cc:dd:ee:01", "SSD1") == (None, 80)
    assert floor.for_disk("aa:bb:cc:dd:ee:01", "SSD1") == (None, 80)         # memo: the same answer, not another decision
    assert [r["verdict"] for r in decisions(conn)] == ["FROM_SETTING"]


def test_no_decision_is_the_floor_of_a_solid_disk_and_one_undecided_row_per_disk(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("brain down")
    monkeypatch.setattr(floorrule, "judge_floor", boom)
    db.set_setting(conn, "hdd_support", "true")
    for _ in range(3):
        assert hdd.decision(conn, H) == (None, 50)                            # not a silent refusal, not "no floor"
    assert [(r["verdict"], r["entity_key"]) for r in decisions(conn)] == [("UNDECIDED", "WD-H1")]


def test_the_manifest_carries_the_brains_start_value(conn):
    assert hdd.manifest(conn)["hdd_floor_mbps"] == floorrule.INITIAL_MBPS == 20


def test_a_damaged_setting_is_the_default_with_a_log_line(conn, caplog):
    db.set_setting(conn, writefloor.SETTING, "zero")
    assert writefloor.mbps(conn) == 50
    assert "is not 10-500 -- using 50" in caplog.text


def test_the_rule_is_registered_where_the_console_and_the_forecast_look_for_it():
    from server.brain import forecast, say
    assert floorrule is not None
    assert floorrule.ACTION in say.ACTION_HE and {floorrule.FROM_SETTING, floorrule.LEARNED, floorrule.ESTIMATE, floorrule.REFUSED} <= set(say.VERDICT_HE)
    assert floorrule.RULE in say.EXECUTED and forecast.predicts(floorrule.RULE) is not None
