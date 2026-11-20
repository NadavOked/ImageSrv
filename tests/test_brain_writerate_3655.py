"""#3655 (אפיק #3653): הקצב שהדיסקים נדרשים לכתוב (S3, K-WRITE) וגבולות הבקר של השולח (S4, K-RATE-BOUNDS) -- המוח מחליט
(`server/brain/writerate.py`), `ChunkEngine` מבצע.

- **ההתנהגות זהה לזו של לפני #3655.** `OLD_*` הם העתקים מילוליים של הלוגיקה כפי שישבה ב-`chunksend.py` בבסיס (`lab/vm-1114f`,
  `13828c0e4`), בתור אורקל בטסט בלבד: רשת של בדיקות, היסטוריות ורצפות, והתוצאה חייבת להיות זהה.
- **המנוע אינו מחשב.** `WRITE_MARGIN` ו-`FEEDBACK_MAX_FACTOR` הוסרו מהקוד (לא אליאסים): אין ב-`chunksend.py` ובשרת אף קבוע כזה.
- **כל החלטה נרשמת** ב-`brain_decisions` עם הראיות. `off` מחליט ואינו רושם. **אין החלטה = הרצפה** (S3), או קצב מקובע (S4).

**בקרה שלילית:** בבסיס אין `writerate.py` והמנוע מחשב בעצמו. הטסטים שעוברים דרך המנוע נופלים ב-assert על `brain_decisions` ריק;
הטסטים של המודול נופלים ב-`assert writerate is not None`. השומרים שעוברים בשני המצבים מסומנים (guard).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import silence
from server.rounds import chunk_rate, chunksend

try:                                    # בקרה שלילית: בלי המודול ההתנהגות נבדקת, לא ה-import
    from server.brain import writerate
except ImportError:                     # pragma: no cover
    writerate = None

from test_chunksend_2533 import SID, rig, wait_for   # noqa: F401 -- rig is a fixture

REPO = Path(__file__).resolve().parent.parent
OLD_MARGIN, OLD_FACTOR = 0.9, 2            # `chunksend.WRITE_MARGIN`, `chunksend.FEEDBACK_MAX_FACTOR` בבסיס


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


@pytest.fixture(autouse=True)
def _fresh_rule_registers():
    silence.forget()
    yield
    silence.forget()


def decisions(conn, rule):
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = ? ORDER BY id", (rule,))]


# --- אורקל: הקוד שהוסר ---------------------------------------------------------------------------------------------


def OLD_PROBE(probes, floor_mbs):
    if not probes or any(v is None for v in probes):
        return floor_mbs * 8000
    return max(floor_mbs, int(min(probes) * OLD_MARGIN)) * 8000


def OLD_LEFT_OUT(measured, history, floor_mbs):
    """`ChunkEngine._slow_by_history`, מילה במילה (ההיסטוריה כבר נאספה: serial -> (MB/s, גל))."""
    if not history or any(s not in history for s, _v in measured):
        return {s: (v, history[s]) for s, v in measured if s in history}
    pace = OLD_PROBE([v for _s, v in measured], floor_mbs)
    return {s: (v, history[s]) for s, v in measured if int(history[s][0] * 8000 * OLD_MARGIN) < pace}


def OLD_BASE(measured, history, floor_mbs):
    """`ChunkEngine._write_base` אחרי איסוף העובדות: (הקצב, מי נספר)."""
    if not measured or any(v is None for _s, v in measured):
        return floor_mbs * 8000, []
    behind = OLD_LEFT_OUT(measured, {s: history[s] for s, _v in measured if s in history}, floor_mbs)
    fast = [v for s, v in measured if s not in behind]
    counted = [s for s, _v in measured if not fast or s not in behind]
    return OLD_PROBE(fast or [v for _s, v in measured], floor_mbs), sorted(counted)


def OLD_BOUNDS(kbps, write_kbps, floor_mbs, learned_max=None):
    if write_kbps:
        high = write_kbps * OLD_FACTOR
        if learned_max and learned_max > high:
            high = learned_max
        return max(1, min(floor_mbs * 8000, write_kbps)), high
    return kbps, kbps


def OLD_CAP(kbps, learned_max, file_bytes, chunks, chunk_bytes):
    if learned_max or not kbps or not file_bytes or not chunks or file_bytes <= 0 or chunks <= 0:
        return None
    return kbps * chunks * chunk_bytes // file_bytes


def test_the_module_exists_and_the_margin_and_the_factor_are_the_old_values():
    assert writerate is not None, "server/brain/writerate.py חסר"
    assert (writerate.MARGIN, writerate.RATE_MAX_FACTOR) == (OLD_MARGIN, OLD_FACTOR)


def test_the_base_is_exactly_what_the_engine_computed_before():
    assert writerate is not None, "server/brain/writerate.py חסר"
    checks = (None, 30, 49, 50, 55, 60, 120, 125, 209, 400)
    wrote = (None, 20, 48, 61, 248, 500)
    checked = 0
    for a in checks:
        for b in checks:
            for floor in (10, 50, 74):
                for ha in wrote:
                    for hb in wrote:
                        measured = [("A1", a), ("B1", b)]
                        history = {s: (h, "ses_old") for s, h in (("A1", ha), ("B1", hb)) if h is not None}
                        want_kbps, want_counted = OLD_BASE(measured, history, floor)
                        got = writerate.judge_base(measured, history, floor)
                        assert (got.kbps, list(got.counted)) == (want_kbps, want_counted), (measured, history, floor)
                        assert writerate.left_out(measured, history, floor) == OLD_LEFT_OUT(
                            measured, {s: history[s] for s, _v in measured if s in history}, floor)
                        checked += 1
    assert checked == 10 * 10 * 3 * 6 * 6


def test_the_probe_alone_is_the_slowest_times_the_margin_and_never_under_the_floor():
    assert writerate is not None
    for probes in ([209, 125], [60], [209, None], [], [50], [55, 400, 75]):
        for floor in (10, 50, 74, 100):
            assert writerate.probe_kbps(probes, floor) == OLD_PROBE(probes, floor), (probes, floor)
    assert writerate.probe_kbps([209, 125], 74) == 112 * 8000              # #3599, J1: 125 x 0.9
    assert chunksend.probe_write_kbps([209, 125], 74) == 112 * 8000         # K3 (`brain.waveeta`) reads the same number


def test_the_bounds_and_the_cap_are_exactly_what_the_engine_computed_before():
    assert writerate is not None
    for kbps in (0, 100_000, 892_000):
        for write in (0, 160_000, 896_000):
            for floor in (10, 50, 74):
                for learned in (None, 0, 300_000, 900_000, 5_000_000):
                    assert writerate.bounds(kbps, write, floor, learned) == OLD_BOUNDS(kbps, write, floor, learned)
                    assert chunksend.feedback_bounds(kbps, write, floor, learned) == OLD_BOUNDS(kbps, write, floor, learned)
                    for file_bytes, chunks, full in ((None, 100, True), (10 ** 9, None, True), (10 ** 10, 2400, True), (10 ** 10, 2400, False)):
                        got = writerate.judge_bounds(kbps, write, floor, learned, file_bytes=file_bytes, chunks=chunks, full_pass=full,
                                                     chunk_bytes=chunksend.CHUNK_BYTES)
                        high = OLD_BOUNDS(kbps, write, floor, learned)[1]
                        cap = OLD_CAP(kbps, learned, file_bytes, chunks, chunksend.CHUNK_BYTES) if full else None
                        want_cap = cap if (write and cap is not None and cap > high) else None
                        assert (got.low, got.high, got.cap) == (*OLD_BOUNDS(kbps, write, floor, learned), want_cap)
                        assert chunksend.feedback_grow_cap(kbps, learned, file_bytes, chunks) == OLD_CAP(
                            kbps, learned, file_bytes, chunks, chunksend.CHUNK_BYTES)


def test_the_old_constants_are_removed_from_the_code_not_aliased():
    """guard (בשני המצבים לא רלוונטי: בבסיס הם קיימים -- זה הטסט ש**נופל** שם)."""
    code = [REPO / "server" / "rounds" / n for n in ("chunksend.py", "chunk_rate.py")]
    assert not hasattr(chunksend, "WRITE_MARGIN") and not hasattr(chunksend, "FEEDBACK_MAX_FACTOR")
    assert not hasattr(chunk_rate, "WRITE_MARGIN")
    for path in code:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^(WRITE_MARGIN|FEEDBACK_MAX_FACTOR)\b", text, re.M), path.name
        assert not re.search(r"(?<![\w.])(WRITE_MARGIN|FEEDBACK_MAX_FACTOR)(?!\w)", re.sub(r'(?s)""".*?"""|#.*', "", text)), path.name


# --- דרך המנוע --------------------------------------------------------------------------------------------------------


def _first_command(rig, checks):
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=checks[mac])
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    return rig.runner.commands[0]


def _flag(cmd, name):
    return int(cmd[cmd.index(name) + 1])


def test_the_engine_asks_the_brain_and_the_decision_is_recorded_with_its_evidence(rig, conn):
    engine = rig.make()
    engine.brain_conn = conn
    cmd = _first_command(rig, {"a": 140, "b": 120, "c": 200})
    assert _flag(cmd, "--write-kbps") == int(120 * 0.9) * 8000
    [row] = decisions(conn, "K-WRITE")
    assert (row["point"], row["action"], row["verdict"], row["round_id"], row["mode"]) == ("S3", "WRITE_BASE", "PACE", SID, "shadow")
    assert "check[B1]=120MB/s" in row["evidence_json"] and "margin=0.9" in row["evidence_json"] and "part=3" in row["evidence_json"]
    [bounds] = decisions(conn, "K-RATE-BOUNDS")
    assert (bounds["point"], bounds["action"], bounds["verdict"]) == ("S4", "FEEDBACK_BOUNDS", "SET")
    assert f"--rate-max {_flag(cmd, '--rate-max')}" in bounds["reason"] and f"--rate-min {_flag(cmd, '--rate-min')}" in bounds["reason"]


def test_an_unmeasured_disk_is_the_floor_and_the_row_says_so(rig, conn):
    engine = rig.make()
    engine.brain_conn = conn
    cmd = _first_command(rig, {"a": 140, "b": None, "c": 200})
    assert _flag(cmd, "--write-kbps") == 50 * 8000
    [row] = decisions(conn, "K-WRITE")
    assert row["verdict"] == "FLOOR" and "לא נמדדה" in row["reason"]


def test_off_decides_and_does_not_record(rig, conn):
    db.set_setting(conn, "brain_veto_mode", "off")
    engine = rig.make()
    engine.brain_conn = conn
    cmd = _first_command(rig, {"a": 140, "b": 120, "c": 200})
    assert _flag(cmd, "--write-kbps") == int(120 * 0.9) * 8000
    assert decisions(conn, "K-WRITE") == [] and decisions(conn, "K-RATE-BOUNDS") == []


def test_no_decision_is_the_floor_and_the_bounds_are_pinned_not_computed_by_the_engine(rig, conn, monkeypatch):
    """המוח נפל: המנוע לא מחשב כלום בעצמו. `--write-kbps` = הרצפה (50), והבקר ננעל על מה שהתבקש. שורה UNDECIDED אחת לכל כלל."""
    def boom(*a, **k):
        raise RuntimeError("brain down")
    monkeypatch.setattr(writerate, "judge_base", boom)
    monkeypatch.setattr(writerate, "judge_bounds", boom)
    engine = rig.make()
    engine.brain_conn = conn
    cmd = _first_command(rig, {"a": 140, "b": 120, "c": 200})
    assert _flag(cmd, "--write-kbps") == 50 * 8000
    assert _flag(cmd, "--rate-min") == _flag(cmd, "--rate-max") == _flag(cmd, "--write-kbps")
    assert "--rate-cap" not in cmd
    assert [r["verdict"] for r in decisions(conn, "K-WRITE")] == ["UNDECIDED"]
    assert [r["verdict"] for r in decisions(conn, "K-RATE-BOUNDS")] == ["UNDECIDED"]


def test_a_drive_left_out_by_history_is_named_in_the_decision(rig, conn):
    engine = rig.make()
    engine.brain_conn = conn
    engine.write_history = lambda serial: (61, "ses_old") if serial == "B1" else None
    cmd = _first_command(rig, {"a": 400, "b": 209, "c": 400})
    assert _flag(cmd, "--write-kbps") == int(400 * 0.9) * 8000
    [row] = decisions(conn, "K-WRITE")
    assert "behind[B1]=check 209MB/s, wrote 61MB/s in ses_old" in row["evidence_json"]
    assert rig.named("chunk_write_behind") == [f'{SID} "B1" check=209MB/s wrote=61MB/s in=ses_old left_out']


def test_the_brain_rules_are_registered_where_the_console_and_the_forecast_look_for_them():
    from server.brain import forecast, say
    assert writerate is not None
    for action in (writerate.ACTION_BASE, writerate.ACTION_BOUNDS):
        assert action in say.ACTION_HE
    assert {writerate.PACE, writerate.FLOOR, writerate.SET} <= set(say.VERDICT_HE)
    assert {writerate.RULE_BASE, writerate.RULE_BOUNDS} <= say.EXECUTED
    assert forecast.predicts(writerate.RULE_BASE) is not None and forecast.predicts(writerate.RULE_BOUNDS) is not None


def test_the_engine_is_still_wired_to_the_brain_in_the_app():
    """guard: `server/app.py` מחבר את החיבור שדרכו המוח רושם."""
    assert "chunk.brain_conn = conn" in (REPO / "server" / "app.py").read_text(encoding="utf-8")
