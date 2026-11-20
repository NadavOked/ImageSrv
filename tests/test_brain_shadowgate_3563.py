"""#3563: שער המעבר מצל לאכיפה -- מדידה לכל כלל מתוך `brain_decisions`, בלי מעבר אוטומטי.

מה נבדק (שורות `brain_decisions` סינתטיות, כמה כללים, והתוצאה הצפויה לכל אחד):
- מעט דגימות, הסכמה גבוהה -> `NOT_READY` / `few_samples`.
- הרבה דגימות, הסכמה נמוכה -> `NOT_READY` / `low_agreement`.
- עומד בשער (וגם בדיוק על הסף) -> `READY`, והוא המלצה בלבד: שום שורה לא נכתבת ושום מצב לא משתנה.
- **#3750: החלטה בלי `matched` (בלי תוצאה שנמדדה) אינה הסכמה** -- לא במונה, לא במכנה, ונספרת כ-`unmeasured`.
  כלל שאין לו אף שורה שנמדדה הוא `NOT_MEASURED`, לא `READY` ולא `NOT_READY`.
- דגימה = אירוע בלתי תלוי: מאה שורות באותו גל הן דגימה אחת.
- שני הספים בהגדרות: שינוי בהגדרה משנה את התוצאה בלי שינוי קוד.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import memory, shadowgate


def rows(conn, rule, n, *, agreed=None, mode="shadow", wave=None, outcome=True):
    """`n` החלטות של `rule`. `agreed`: כמה מהן נסגרו עם `matched=1`; השאר `matched=0`. `agreed=None` = נסגרו בתוצאה
    בטקסט בלבד, בלי `matched` (לא נמדד). `outcome=False` = נשארו פתוחות (בלי תוצאה בכלל)."""
    wave = wave or (lambda i: f"{rule}-w{i}")
    for i in range(n):
        did = memory.record(conn, mode=mode, rule=rule, action="A", verdict="V", reason="r", round_id=wave(i),
                            predicted=100.0, unit="s")
        if not outcome:
            continue
        if agreed is None:
            memory.resolve(conn, [did], "נסגר, לא נמדד")
        else:
            memory.resolve(conn, [did], "נמדד", actual=100.0, matched=i < agreed)


def seed(conn):
    rows(conn, "T-FEW", 5, agreed=5)                                    # הסכמה מלאה, אבל 5 דגימות
    rows(conn, "T-LOW", 40, agreed=20)                                  # 50%
    rows(conn, "T-OK", 40, agreed=38)                                   # 95%
    rows(conn, "T-EDGE", 30, agreed=27)                                 # בדיוק 90% ובדיוק 30
    rows(conn, "T-BELOW", 30, agreed=26)                                # 86.7%
    rows(conn, "T-UNMEASURED", 100, agreed=None)                        # נסגרו, אף אחת לא נמדדה
    rows(conn, "T-OPEN", 100, outcome=False)                            # בלי תוצאה בכלל
    rows(conn, "T-MIX", 40, agreed=40)                                  # 40 נמדדו והסכימו...
    rows(conn, "T-MIX", 200, agreed=None, wave=lambda i: f"T-MIX-x{i}")  # ...ועוד 200 שלא נמדדו
    rows(conn, "T-ONEWAVE", 100, agreed=100, wave=lambda i: "one-wave")  # מאה שורות, גל אחד
    rows(conn, "T-ENF", 40, agreed=40)
    rows(conn, "T-ENF", 1, mode="enforce")


def by_rule(rep):
    return {r["rule"]: r for r in rep["rules"]}


@pytest.fixture()
def conn(server):
    return server["ctx"].conn


def test_each_synthetic_rule_gets_the_expected_state(conn):
    seed(conn)
    got = by_rule(shadowgate.report(conn))

    def shape(rule):
        r = got[rule]
        return r["state"], r["reasons"], r["samples"], r["measured"], r["unmeasured"], r["agreement_pct"]

    assert shape("T-FEW") == ("NOT_READY", ["few_samples"], 5, 5, 0, 100.0)
    assert shape("T-LOW") == ("NOT_READY", ["low_agreement"], 40, 40, 0, 50.0)
    assert shape("T-OK") == ("READY", [], 40, 40, 0, 95.0)
    assert shape("T-EDGE") == ("READY", [], 30, 30, 0, 90.0)
    assert shape("T-BELOW") == ("NOT_READY", ["low_agreement"], 30, 30, 0, 86.7)
    assert shape("T-UNMEASURED") == ("NOT_MEASURED", ["no_measured_decisions"], 0, 0, 100, None)
    assert shape("T-OPEN") == ("NOT_MEASURED", ["no_measured_decisions"], 0, 0, 100, None)
    assert shape("T-ONEWAVE") == ("NOT_READY", ["few_samples"], 1, 100, 0, 100.0)
    assert got["T-ENF"]["state"] == "ENFORCE" and got["T-ENF"]["mode"] == "enforce"
    assert shadowgate.report(conn)["ready_for_canary"] == ["T-EDGE", "T-MIX", "T-OK"]


def test_a_decision_without_a_measured_result_is_neither_agreement_nor_disagreement(conn):
    """#3750: 200 שורות שלא נמדדו לא מורידות את ההסכמה (אינן במכנה) ולא מעלות אותה (אינן במונה), ונספרות בנפרד."""
    seed(conn)
    mix = by_rule(shadowgate.report(conn))["T-MIX"]
    assert (mix["measured"], mix["unmeasured"], mix["shadow_rows"], mix["agreement_pct"]) == (40, 200, 240, 100.0)
    only_unmeasured = by_rule(shadowgate.report(conn))["T-UNMEASURED"]
    assert only_unmeasured["agreement_pct"] is None and only_unmeasured["state"] != "READY"


def test_the_report_only_reads_and_a_ready_rule_stays_in_shadow(conn):
    seed(conn)
    before = conn.execute("SELECT COUNT(*), SUM(mode = 'enforce'), SUM(matched) FROM brain_decisions").fetchone()
    shadowgate.report(conn)
    shadowgate.report(conn)
    assert tuple(conn.execute("SELECT COUNT(*), SUM(mode = 'enforce'), SUM(matched) FROM brain_decisions").fetchone()) \
        == tuple(before)
    assert by_rule(shadowgate.report(conn))["T-OK"]["mode"] == "shadow"


def test_the_thresholds_are_settings_and_move_the_result(server):
    admin, conn = server["admin"], server["ctx"].conn
    seed(conn)
    assert admin.get("/api/console/settings").json()["brain_gate_min_samples"] == str(shadowgate.SAMPLES_DEFAULT)
    assert by_rule(shadowgate.report(conn))["T-FEW"]["state"] == "NOT_READY"
    assert admin.post("/api/console/settings", json={"brain_gate_min_samples": "5"}).status_code == 200
    assert by_rule(shadowgate.report(conn))["T-FEW"]["state"] == "READY"
    assert admin.post("/api/console/settings", json={"brain_gate_min_agreement": "40"}).status_code == 200
    assert by_rule(shadowgate.report(conn))["T-LOW"]["state"] == "READY"
    assert admin.post("/api/console/settings", json={"brain_gate_min_agreement": "100"}).status_code == 200
    rep = shadowgate.report(conn)
    assert rep["thresholds"] == {"min_samples": 5, "min_agreement_pct": 100}
    assert by_rule(rep)["T-OK"]["state"] == "NOT_READY" and by_rule(rep)["T-MIX"]["state"] == "READY"


def test_a_corrupt_stored_threshold_falls_back_to_the_start_value(server):
    from server.db import set_setting
    conn = server["ctx"].conn
    set_setting(conn, shadowgate.SAMPLES_SETTING, "lots")
    set_setting(conn, shadowgate.AGREEMENT_SETTING, "0")
    assert shadowgate.thresholds(conn) == {"min_samples": shadowgate.SAMPLES_DEFAULT,
                                           "min_agreement_pct": shadowgate.AGREEMENT_DEFAULT}


def test_the_console_endpoint_returns_samples_agreement_and_state_per_rule(server):
    seed(server["ctx"].conn)
    body = server["admin"].get("/api/console/brain/shadow-gate").json()
    assert body["thresholds"] == {"min_samples": 30, "min_agreement_pct": 90}
    assert by_rule(body)["T-OK"] == {"rule": "T-OK", "mode": "shadow", "state": "READY", "reasons": [],
                                     "shadow_rows": 40, "measured": 40, "unmeasured": 0, "samples": 40,
                                     "agreement_pct": 95.0, "last_at": by_rule(body)["T-OK"]["last_at"]}
    assert body["ready_for_canary"] == ["T-EDGE", "T-MIX", "T-OK"]
    assert server["deploy"].get("/api/console/brain/shadow-gate").status_code == 403
    assert server["anon"].get("/api/console/brain/shadow-gate").status_code == 401
