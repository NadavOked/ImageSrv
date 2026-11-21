"""#3662 (S41 ו-S28, B71 ו-B72): ההכרעות של ה-probe ושל האזהרות לפני "התחל" עברו למוח, וההתנהגות זהה.

- **B71 (`server/brain/probejudge.py`):** מה דורש עין ליד המכונה. הגוף והספים הועברו כמו שהם מ-`server/agent/probe.py`; `probe.verdicts` הוא
  מעטפת. הטסט נועל את הטבלה (כל כלל, עם הטקסט והחומרה שלו), שהספים לא זזו (300 ו-90), שאין להם בית שני, ושהמודול עלה.
- **B72 (`server/brain/precheck.py`):** מה מצדיק אזהרה (`causes`) הוא אותו תנאי שהיה ב-`room_precheck.warnings`; ההכרעה המתועדת "אזהרה בלבד,
  לעולם לא חסימה" נעולה; וכשגל יוצא לדרך למרות אזהרה נרשמת שורה.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import ast
import itertools
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.agent import probe
from server.brain import memory, precheck, probejudge, say, veto
from server.db import set_setting
from server.rounds import room, room_precheck
from test_server_room import CLONER1, room_body, room_server  # noqa: F401 -- room_server הוא fixture

ROOT = Path(__file__).resolve().parent.parent
RID = "room_test3662b"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "precheck.db")
    yield c
    veto.forget()


# --- B71: מה דורש עין --------------------------------------------------------------------------------------


def v(probe_json):
    return [(i["key"], i["level"], i["text_he"]) for i in probe.verdicts(probe_json)]


def test_the_on_battery_verdict_is_an_error():
    assert v({"power": {"on_battery": True}}) == [("battery", "err", "המחשב על סוללה — שיכפול/קליטה עלולים להיקטע")]


@pytest.mark.parametrize("skew, expected", [
    (300, []), (-300, []), (301, [("rtc", "warn", "שעון החומרה סוטה ב-5 דק' — סוללת BIOS חשודה")]),
    (-7200, [("rtc", "warn", "שעון החומרה סוטה ב-2 שעות — סוללת BIOS חשודה")]),
    (2 * 86400 + 5, [("rtc", "warn", "שעון החומרה סוטה ב-2 ימים — סוללת BIOS חשודה")]),
    (0, []),
])
def test_the_clock_skew_threshold_is_strictly_above_300_seconds(skew, expected):
    assert v({"rtc": {"skew_seconds": skew}}) == expected


@pytest.mark.parametrize("smart, expected", [
    ({"critical_warning": 1}, [("nvme:nvme0", "err", "NVMe nvme0: critical_warning=1")]),
    ({"media_errors": 3}, [("nvme:nvme0", "err", "NVMe nvme0: 3 שגיאות מדיה")]),
    ({"percentage_used": 90}, [("nvme:nvme0", "warn", "NVMe nvme0: 90% מהבלאי המובטח נוצל")]),
    ({"percentage_used": 89}, []),
    ({"critical_warning": 0, "media_errors": 0, "percentage_used": 3}, []),
    ({"error": "smartctl"}, []),
])
def test_the_nvme_verdicts(smart, expected):
    assert v({"disks": [{"name": "nvme0", "nvme_smart": smart}]}) == expected


def test_the_other_verdicts():
    assert v({"pstore": {"crashed": True}}) == [("pstore", "warn", "המחשב קרס לפני האתחול הזה")]
    assert v({"nic": [{"name": "eth0", "ip_conflict": {"duplicate": True}}]}) == [
        ("ip_conflict:eth0", "err", "כפילות IP על eth0 — מחשב אחר ברשת עונה על הכתובת")]
    assert v({"nic": [{"name": "eth0", "stats": {"rx_crc_errors": 12}}]}) == [
        ("crc:eth0", "warn", "12 שגיאות CRC על eth0 — כבל או פורט חשודים")]
    assert v({"nic": [{"name": "eth0", "stats": {"rx_crc_errors": 0}}]}) == []
    assert v({"netprobe": {"cable": {"status": "open", "pairs": [{"code": "Open", "pair": "B", "length_m": 3}]}}}) == [
        ("cable", "err", "כבל פגום: זוג B פתוח ב-3 מטר")]
    assert v({"netprobe": {"cable": {"status": "ok"}}}) == []


@pytest.mark.parametrize("value", [None, [], "x", {}, {"power": None}, {"power": {"error": "x"}}, {"rtc": {"error": "x"}},
                                   {"nic": [None, 3, {"stats": {"error": "x"}}]}, {"disks": ["junk", {"nvme_smart": None}]}])
def test_what_was_not_measured_makes_no_item_and_is_not_a_verdict_of_ok(value):
    assert probe.verdicts(value) == []


def test_the_thresholds_did_not_move_and_have_one_home():
    assert (probejudge.RTC_SKEW_SECONDS, probejudge.NVME_USED_WARN) == (300, 90)
    homes = []
    for path in sorted((ROOT / "server").rglob("*.py")):
        if re.search(r"^(RTC_SKEW_SECONDS|NVME_USED_WARN)\s*=", path.read_text(encoding="utf-8"), re.M):
            homes.append(path.relative_to(ROOT).as_posix())
    assert homes == ["server/brain/probejudge.py"], homes


def test_probe_py_decides_nothing_it_only_wraps():
    src = (ROOT / "server/agent/probe.py").read_text(encoding="utf-8")
    assert "def _measured" not in src and "def _skew_text" not in src and "RTC_SKEW_SECONDS" not in src and "NVME_USED_WARN" not in src
    assert probe.verdicts({"power": {"on_battery": True}}) == probejudge.verdicts({"power": {"on_battery": True}})


def test_the_judge_is_a_leaf_it_imports_nothing_from_the_server():
    tree = ast.parse((ROOT / "server/brain/probejudge.py").read_text(encoding="utf-8"))
    imported = {n.module if isinstance(n, ast.ImportFrom) else a.name
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (getattr(n, "names", None) or [None])}
    assert imported <= {"__future__", "math"}, imported


# --- B72: מה מצדיק אזהרה ------------------------------------------------------------------------------------


def drawer(**kw):
    return {"fresh": True, "selected": True, "port": 1, "serial": "S1", **kw}


@pytest.mark.parametrize("kw, nic, learned, expected", [
    ({}, None, None, ()),
    ({"crc_rise": 0}, None, None, ()),
    ({"crc_rise": None}, None, None, ()),                       # לא נמדד אינו עלייה
    ({"crc": 60000}, None, None, ()),                           # מונה מוחלט הוא היסטוריה (#2846)
    ({"crc_rise": 1}, None, None, ("crc_rise",)),
    ({"port_drop": True}, None, None, ("port_drop",)),
    ({"port_drop": False}, None, None, ()),
    ({"port_drop": "yes"}, None, None, ()),                     # רק True בדיוק
    ({}, None, "ה-SMART השתנה", ("smart_learned",)),
    ({"learned_he": "כנראה כבל"}, None, None, ("port_learned",)),
    ({"model_speed": {"slow": True}}, None, None, ("model_slow",)),
    ({"model_speed": {"slow": False}}, None, None, ()),
    ({"model_speed": "slow"}, None, None, ()),
    ({}, {"mbps": 100, "text_he": "כרטיס איטי"}, None, ("nic_slow",)),
    ({"crc_rise": 2, "port_drop": True}, {"mbps": 100}, "x", ("port_drop", "crc_rise", "smart_learned", "nic_slow")),
])
def test_what_warrants_a_warning(kw, nic, learned, expected):
    assert precheck.causes(drawer(**kw), nic=nic, learned=learned) == expected


def old_predicate(d, nic, learned) -> bool:
    """התנאי שהיה ב-`room_precheck.warnings` לפני #3662, כמו שהיה (כאן, בטסט בלבד, כהפניה)."""
    rise = d.get("crc_rise") or 0
    dropped = d.get("port_drop") is True
    ported = d.get("learned_he")
    speed = d.get("model_speed") if isinstance(d.get("model_speed"), dict) else {}
    model_slow = speed.get("slow") is True
    return not (rise <= 0 and not dropped and not learned and not ported and not model_slow and nic is None)


def test_the_brains_condition_equals_the_old_inline_condition_on_every_combination():
    n = 0
    for rise, drop, learned_he, speed, nic, learned in itertools.product(
            (None, 0, 1, 7), (None, False, True), (None, "", "כנראה כבל"), (None, {}, {"slow": True}, {"slow": False}, "x"),
            (None, {"mbps": 90}), (None, "", "שונה")):
        d = drawer(crc_rise=rise, port_drop=drop, learned_he=learned_he, model_speed=speed)
        assert bool(precheck.causes(d, nic=nic, learned=learned)) == old_predicate(d, nic, learned), (d, nic, learned)
        n += 1
    assert n == 4 * 3 * 3 * 5 * 2 * 3


def machine(**dkw):
    return {"mac": CLONER1, "name": 1, "display_name": "משכפל-1", "joined": True, "drawer_list": [drawer(**dkw)], "slot_names": {}}


def test_room_precheck_still_warns_exactly_when_the_brain_says_so():
    assert room_precheck.warnings([machine(crc_rise=0)], True) == []
    assert room_precheck.warnings([machine(crc=60000, crc_rise=None)], True) == []
    [w] = room_precheck.warnings([machine(crc_rise=5, crc=9)], True)
    assert w["crc_rise"] == 5 and "מונה CRC עלה ב-5 מאז הסבב הקודם" in w["text_he"] and w["port"] == 1


def test_there_is_no_second_copy_of_the_condition_in_room_precheck():
    src = (ROOT / "server/rounds/room_precheck.py").read_text(encoding="utf-8")
    assert "precheck.causes(" in src and "not dropped and not learned" not in src


# --- B72: ההכרעה המתועדת, והרישום ---------------------------------------------------------------------------


def item(n=1, **kw):
    return {"mac": CLONER1, "port": n, "serial": f"S{n}", "text_he": f"משכפל-1 · דיסק {n}: מונה CRC עלה ב-{n}", **kw}


def test_the_documented_decision_is_warn_and_never_block():
    assert precheck.SEVERITY == "WARN"
    for warned in ([item()], [item(n) for n in range(1, 12)], [{"mac": None}], [{}]):
        assert precheck.judge(warned)[0] == "WARN"
    assert not hasattr(precheck, "BLOCK")                                     # אין אפילו שם של חסימה במודול


def test_the_reason_is_the_screens_text_and_a_long_list_is_counted():
    _v, reason, evidence = precheck.judge([item(n) for n in range(1, 6)])
    assert reason.startswith("5 אזהרות לפני התחלת הגל, והוא יוצא בכל זאת: ") and "דיסק 1: מונה CRC עלה ב-1" in reason
    assert reason.endswith("ועוד 2") and reason.count("מונה CRC") == precheck.SHOWN
    assert "warnings=5" in evidence and f"item={CLONER1}:1" in evidence and f"item={CLONER1}:5" in evidence


def test_an_item_without_a_port_is_named_by_its_serial():
    assert f"item={CLONER1}:SER9" in precheck.judge([{"mac": CLONER1, "port": None, "serial": "SER9", "text_he": "x"}])[2]


@pytest.mark.parametrize("manual, who", [(True, "operator"), (False, "auto")])
def test_a_wave_that_starts_despite_a_warning_is_recorded_once(conn, manual, who):
    row = precheck.record(conn, RID, [item(1), item(2)], manual=manual)
    [d] = memory.decisions(conn, round_id=RID, limit=5)
    assert d["id"] == row and (d["rule"], d["action"], d["point"], d["mode"], d["verdict"]) == ("B72", "PRECHECK", "start", "shadow", "WARN")
    assert (d["entity_kind"], d["entity_key"], d["outcome"]) == ("round", RID, "צל: הגל יצא לדרך כמו קודם")
    assert {f"started_by={who}", "legacy=started", "warnings=2"} <= set(d["evidence"])


def test_nothing_is_recorded_without_a_warning_or_with_the_brain_off(conn):
    assert precheck.record(conn, RID, [], manual=True) is None
    set_setting(conn, veto.SETTING, veto.OFF)
    assert precheck.record(conn, RID, [item()], manual=True) is None
    assert memory.decisions(conn, limit=5) == []


def test_a_recorder_that_fails_does_not_raise(conn, monkeypatch):
    monkeypatch.setattr(memory, "record", lambda *a, **k: 1 / 0)
    assert precheck.record(conn, RID, [item()], manual=True) is None


def test_the_line_in_hebrew():
    line = say.line_he({"entity_kind": "round", "entity_key": RID, "action": "PRECHECK", "verdict": "WARN", "reason": "x", "mode": "shadow"})
    assert line.startswith(f"סבב {RID} → אזהרות לפני התחלת הגל: אזהרה") and "PRECHECK" not in line, line


def test_journal_precheck_writes_the_brains_row_next_to_the_journal_row(room_server):
    ctx = room_server["ctx"]
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    round_row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (opened.json()["id"],)).fetchone()
    warned = room_precheck.warnings([machine(crc_rise=4, crc=8)], True)
    assert len(warned) == 1
    room.journal_precheck(ctx.conn, round_row, warned, "noc")
    room.journal_precheck(ctx.conn, round_row, [], "noc")                       # בלי אזהרה: לא נכתב דבר
    room.journal_precheck(ctx.conn, round_row, warned, "")                       # גל שיצא לבד
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'room_precheck_warned'").fetchone()[0] == 2
    rows = memory.decisions(ctx.conn, rule="B72", round_id=round_row["id"], limit=10)
    assert sorted(("started_by=operator" in d["evidence"], "started_by=auto" in d["evidence"]) for d in rows) == [(False, True), (True, False)]
    assert all(d["verdict"] == "WARN" and "מונה CRC עלה ב-4" in d["reason"] for d in rows)


# --- התיעוד ---------------------------------------------------------------------------------------------------


def test_the_rule_and_the_action_are_in_the_interfaces_doc():
    # הקטלוג עצמו פרטי, וטסט ציבורי אינו נוקב בו (`test_publish_to_public`): `test_brain_catalog` בודק ש-B71 ו-B72 מצביעים על קוד קיים.
    doc = (ROOT / "docs/architecture/interfaces.md").read_text(encoding="utf-8")
    assert (precheck.RULE, precheck.ACTION) == ("B72", "PRECHECK") and "B72" in doc and "PRECHECK" in doc and "B71" in doc
