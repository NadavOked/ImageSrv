"""#3659 (אפיק #3653, נקודה A27) -- הגבולות של ההשלמה הם החלטה של המוח (`fill.limits`, כלל `K-FILL`), והסוכן מבצע.

מה שהיה קבוע בסוכן (`NF_BAD_MAX`, `PF_SPEED_BPS`/`PF_SPEED_S`, `FILL_WORD_S`) הוא עכשיו ערך שהמוח אומר: נרשם ב-`brain_decisions`
(`K-FILL`/`FILL_LIMITS`, צל כברירת מחדל), וב-`enforce` חוזר לסוכן בתוכנית (`fill`) ובהוראת `peer_fill`. **אין החלטה** = התוכנית אינה
נושאת אותם, והסוכן משתמש בקבוע שלו (הצד של הסוכן: `test_fill_limits_agent_3659.py`).

ההתנהגות נבדקת דרך ה-API (hello, progress, התשובה והטבלה), כמו `test_prodline_3181.py` ו-`test_brain_peer_b29.py`.

**בקרה שלילית:** `server/brain/fill.py`, `server/brain/peer.py`, `say.py`, `forecast.py` ו-`catalog.toml` חוזרים לבסיס. לפני השינוי אין
`fill.limits`; הטסטים הטהורים נופלים ב-AttributeError, ולכן הטסטים של ה-API כתובים כך שגם בלעדיו הם נופלים בהתנהגות: אין שורת
`K-FILL` ב-`brain_decisions` והתוכנית אינה נושאת `bad_max`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from test_brain_peer_b29 import _busy_server, _enforce, _peer_report, _rejoin, _setup  # noqa: E402
from test_prodline_3181 import _nine, _open, _progress, _t, _wave  # noqa: E402
from test_server_room import CLONER1, room_server  # noqa: E402,F401

REPO = Path(__file__).resolve().parent.parent
MIB = 1024 * 1024


def _fill():
    from server.brain import fill                                            # noqa: PLC0415
    return fill


def _plan(fill, source, budget=100 * MIB):
    return fill.Plan("FEW", source, ("sdb",) if source != fill.HTTP else (), budget, budget, "r")


def _rows(rs, rule="K-FILL"):
    return rs["ctx"].conn.execute("SELECT * FROM brain_decisions WHERE rule = ? ORDER BY id", (rule,)).fetchall()


def _rejoin_report(rs, wave, missing=30, neighbors=True):
    targets = [_t("sda", "A1", 1, "writing", rejoin={"part": 3, "missing": missing, "chunks": 18254, "away_s": 2})]
    if neighbors:
        targets += [_t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)]
    return _progress(rs, wave, CLONER1, targets)


def _open_wave(rs):
    _nine(rs)
    assert _open(rs["deploy"], 10).status_code == 200
    return _wave(rs)


# --- ההחלטה (טהורה) --------------------------------------------------------------------------------------------------


def test_the_limits_are_the_values_the_agent_had_and_the_ceiling_is_k5s_budget():
    fill = _fill()
    for source, verdict in ((fill.NEIGHBOR, "NEIGHBORS_THEN_HTTP"), (fill.NEIGHBORS, "NEIGHBORS_THEN_HTTP"),
                            (fill.HTTP, "HTTP_ONLY")):
        got = fill.limits(_plan(fill, source, budget=40 * MIB))
        assert got.verdict == verdict
        assert (got.bad_max, got.peer_speed_kibs, got.peer_speed_s, got.word_s) == (3, 1024, 30, 6)
        assert got.ceiling_bps == 40 * MIB, "the ceiling is the K5 budget the plan carries, not a second number"
        assert any(e.startswith("bad_max=") for e in got.evidence) and any(e.startswith("word_s=") for e in got.evidence)


def test_a_pass_or_nothing_missing_is_no_decision():
    """מעבר שידור נוסף (PASS) ו'לא חסר כלום' (NONE) אינם השלמה מאיפשהו: אין גבולות, והסוכן בקבועים שלו."""
    fill = _fill()
    assert fill.limits(_plan(fill, fill.PASS)) is None
    assert fill.limits(_plan(fill, fill.NONE)) is None


def test_the_brains_values_are_the_agents_own_defaults():
    """'אין שינוי התנהגות': הערכים שהמוח אומר הם אלה שהיו קבועים בסוכן, ושניהם בקוד הנוכחי. קבוע שזז בצד אחד נכשל כאן בשמו."""
    fill = _fill()

    def number(path, pattern):
        found = re.search(pattern, (REPO / path).read_text(encoding="utf-8"), re.M)
        assert found, f"{path}: {pattern}"
        return int(found.group(1))

    nf, pf, fw = ("agent/lib/restore/neighborfill.sh", "agent/lib/restore/peerfill.sh", "agent/lib/restore/fillword.sh")
    assert number(nf, r'^NF_BAD_MAX="\$\{NF_BAD_MAX:-(\d+)\}"') == fill.BAD_MAX
    assert number(nf, r"^NF_CAP_MAX_MIBS=(\d+)") * MIB == fill.CEILING_BPS
    assert number(pf, r'^PF_SPEED_BPS="\$\{PF_SPEED_BPS:-(\d+)\}"') == fill.PEER_SPEED_KIBS * 1024
    assert number(pf, r'^PF_SPEED_S="\$\{PF_SPEED_S:-(\d+)\}"') == fill.PEER_SPEED_S
    assert number(fw, r'^FILL_WORD_S="\$\{FILL_WORD_S:-(\d+)\}"') == fill.WORD_S


def test_the_rule_is_registered_where_the_console_and_the_catalog_read():
    from server.brain import forecast, say                                   # noqa: PLC0415

    assert say.ACTION_HE["FILL_LIMITS"] and say.VERDICT_HE["NEIGHBORS_THEN_HTTP"] and say.VERDICT_HE["HTTP_ONLY"]
    line = say.line_he({"entity_kind": "disk", "entity_key": "A1", "action": "FILL_LIMITS",
                        "verdict": "NEIGHBORS_THEN_HTTP", "reason": "r", "mode": "shadow"})
    assert "FILL_LIMITS" not in line and "NEIGHBORS_THEN_HTTP" not in line, line
    assert forecast.predicts("K-FILL") is not None
    # שורת הקטלוג של K-FILL נבדקת ב-test_brain_catalog.py: הקטלוג פרטי, וטסט ציבורי אינו נוקב בו (test_publish_to_public)


# --- דרך ה-API ---------------------------------------------------------------------------------------------------------


def test_in_shadow_the_limits_are_recorded_once_and_the_agent_hears_nothing(room_server):
    wave = _open_wave(room_server)
    got = _rejoin_report(room_server, wave)
    assert "fill" not in got, "בצל הסוכן לא שומע"
    rows = _rows(room_server)
    assert len(rows) == 1, [dict(r) for r in rows]
    row = rows[0]
    assert (row["mode"], row["point"], row["action"], row["verdict"], row["entity_key"]) == \
        ("shadow", "A27", "FILL_LIMITS", "NEIGHBORS_THEN_HTTP", "A1")
    assert row["outcome"].startswith("n/a: "), "#3750: in shadow the row is closed as not applicable, not left empty"
    evidence = row["evidence_json"]
    assert '"bad_max=3"' in evidence and '"word_s=6"' in evidence and "1024KiB/s for 30s" in evidence, evidence
    _rejoin_report(room_server, wave)
    assert len(_rows(room_server)) == 1, "the same decision again is not a new row: one per change, not one every 2 seconds"
    assert len(_rows(room_server, "K5")) >= 1, "K5's own source decision is still recorded"


def test_in_enforce_the_plan_carries_the_limits(room_server):
    from server.db import set_setting                                        # noqa: PLC0415

    _nine(room_server)
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "enforce")
    assert _open(room_server["deploy"], 10).status_code == 200
    wave = _wave(room_server)
    plan = _rejoin_report(room_server, wave, missing=900)["fill"]["A1"]
    assert (plan["source"], plan["bad_max"], plan["word_s"]) == ("NEIGHBORS", 3, 6), plan
    assert plan["budget_bps"] > 0 and "speed_kibs" not in plan, "the neighbour cloner's floor goes in peer_fill, not here"
    [row] = _rows(room_server)
    assert (row["mode"], row["verdict"]) == ("enforce", "NEIGHBORS_THEN_HTTP")
    assert "bad_max=3" in row["outcome"] and "word_s=6" in row["outcome"], "#3750: closed with the limits the cloner was handed"
    assert all(r["outcome"] for r in _rows(room_server, "K5")), "#3750: K5 rows are closed too"


def test_http_only_is_a_decision_too(room_server):
    from server.db import set_setting                                        # noqa: PLC0415

    _nine(room_server)
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "enforce")
    assert _open(room_server["deploy"], 10).status_code == 200
    wave = _wave(room_server)
    plan = _rejoin_report(room_server, wave, neighbors=False)["fill"]["A1"]
    assert (plan["source"], plan["bad_max"], plan["word_s"]) == ("HTTP", 3, 6), plan
    assert [r["verdict"] for r in _rows(room_server)] == ["HTTP_ONLY"]


def test_a_rule_that_fell_is_no_decision_and_the_plan_stands(room_server, monkeypatch, caplog):
    """הכלל נפל: אין שורת K-FILL, והתוכנית (המקור) חוזרת בלי `bad_max` ו-`word_s` -- הסוכן בקבועים שלו. לא דיווח שנכשל."""
    from server.db import set_setting                                        # noqa: PLC0415

    fill = _fill()

    def boom(plan):
        raise RuntimeError("the rule fell")

    monkeypatch.setattr(fill, "limits", boom)
    caplog.set_level(logging.ERROR, logger="imagesrv.brain")
    _nine(room_server)
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "enforce")
    assert _open(room_server["deploy"], 10).status_code == 200
    wave = _wave(room_server)
    plan = _rejoin_report(room_server, wave, missing=900)["fill"]["A1"]
    assert plan["source"] == "NEIGHBORS" and "bad_max" not in plan and "word_s" not in plan, plan
    assert _rows(room_server) == [], "no decision, no row"
    assert "completion limits" in caplog.text and "the rule fell" in caplog.text
    assert len(_rows(room_server, "K5")) >= 1


# --- שכן-משכפל: הרצפה בהוראה (peer.py) ------------------------------------------------------------------------------------


def test_the_instruction_to_a_neighbour_cloner_carries_the_speed_floor(room_server):
    fill = _fill()
    wave = _setup(room_server)
    _busy_server(room_server)
    _enforce(room_server)
    assert "peer_fill" not in _rejoin(room_server, wave)
    assert len(_peer_report(room_server, wave)["peer_serve"]) == 1
    instruction = _rejoin(room_server, wave)["peer_fill"]["A1"]
    assert (instruction["speed_kibs"], instruction["speed_s"]) == (fill.PEER_SPEED_KIBS, fill.PEER_SPEED_S), instruction
    assert (instruction["speed_kibs"], instruction["speed_s"]) == (1024, 30)
