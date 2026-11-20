"""#3064 (B16, B34, B30, R5) -- מגירה שמשלימה, מדיניות הצטרפות עם שם, ומגירה חשודה שמאומתת ולא נמחקת.

* **B16:** מגירה שחזרה ועוד משלימה (`targets[].rejoin`) נרשמת **פעם אחת** כהחלטה `HOLD_COMPLETING` (`K6`) ב-`brain_decisions`,
  ונסגרת מהדיווח שבו השדה ירד, בשם מה שהדיווח אומר. ההחזקה עצמה נאכפת בסוכן (`drawerback.sh`) ואינה נכתבת שוב.
* **B34:** `join=CURRENT` כשלא חסר דבר, `join=ALL` כשחסר, ובלי ספירה אין שם.
* **B30:** `readback_failed_chunks` -> `VERIFY_REPAIR`. התשובה נושאת `verify` רק ב-`enforce` **וגם** כשהמתג `brain_verify_repair` דלוק.
* **R5:** `drawer_list[].rejoin` בקונסולה, ושורת הפס שאומרת "מצטרף ומשלים".

ההתנהגות נבדקת דרך ה-API (hello, progress, GET /room, POST /settings), כדי שהבקרה השלילית תיפול על התנהגות.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from test_prodline_3181 import _nine, _open, _progress, _t, _wave  # noqa: E402
from test_server_room import CLONER1, room, room_server  # noqa: E402,F401

from server.brain import completing, gates  # noqa: E402
from server.db import set_setting  # noqa: E402


def _rows(rs, action):
    return rs["ctx"].conn.execute("SELECT * FROM brain_decisions WHERE action = ? ORDER BY id", (action,)).fetchall()


def _line(rs):
    _nine(rs)
    assert _open(rs["deploy"], 10).status_code == 200
    # the ten round gates write their decisions from a background thread that `open_round` starts (`gates.start`); let it
    # finish, or it writes them after the report under test, and a test that reads the last 12 decisions loses its own
    assert gates.wait_idle(30.0), "תהליכון הרקע של שערי הסבב לא הסתיים"
    return _wave(rs)


def _failed_with(chunks, **extra):
    return _t("sdc", "A3", 3, "failed", error="הקריאה החוזרת לא תאמה", readback_failed_chunks=chunks, **extra)


GOOD = [{"partition": 2, "chunk_index": 7}, {"partition": 2, "chunk_index": 3}, {"partition": 4, "chunk_index": 0}]


# --- B34: השם ----------------------------------------------------------------------------------------------


def test_the_join_policy_has_a_name_and_no_count_has_none():
    assert completing.join_policy(0) == "CURRENT"
    assert completing.join_policy(1) == "ALL"
    assert completing.join_policy(18254) == "ALL"
    assert completing.join_policy(None) is None, "הסוכן לא ספר: אין שם (לא CURRENT)"


def test_the_fill_decision_carries_the_policy_in_its_evidence(room_server):
    wave = _line(room_server)
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, rejoin={"part": 3, "missing": 30, "chunks": 100}),
                                           _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    evidence = json.loads(_rows(room_server, "FILL_SOURCE")[0]["evidence_json"])
    assert "join=ALL" in evidence
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, rejoin={"part": 4, "missing": 0, "chunks": 100}),
                                           _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    assert "join=CURRENT" in json.loads(_rows(room_server, "FILL_SOURCE")[-1]["evidence_json"])


# --- B16 ----------------------------------------------------------------------------------------------------


def test_a_completing_drawer_is_one_hold_in_the_memory_with_its_count_and_policy(room_server):
    wave = _line(room_server)
    targets = [_t("sda", "A1", 1, rejoin={"part": 3, "missing": 412, "chunks": 1000, "away_s": 21.5, "step": "neighbor"}),
               _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)]
    for _ in range(3):
        _progress(room_server, wave, CLONER1, targets)
    rows = _rows(room_server, "HOLD_COMPLETING")
    assert len(rows) == 1, "החלטה אחת לכל מגירה ומחיצה, לא שורה לכל דיווח"
    row = rows[0]
    assert (row["rule"], row["verdict"], row["mode"], row["entity_kind"], row["entity_key"]) == (
        "K6", "HOLD", "shadow", "disk", "A1")
    assert row["outcome"] is None, "עוד משלימה: ההחלטה פתוחה"
    evidence = json.loads(row["evidence_json"])
    for want in ("join=ALL", "missing=412", "chunks=1000", "part=3", "missing_share=41%", "away_s=22"):
        assert want in evidence, (want, evidence)
    assert "412" in row["reason"] and "drawerback.sh" in row["reason"]
    assert any(d["action"] == "HOLD_COMPLETING" for d in room(room_server["deploy"])["round"]["production_line"]["decisions"]), \
        "החלון 'החלטות המוח' רואה אותה"


def test_the_hold_closes_by_what_the_next_report_says_and_never_by_a_guess(room_server):
    wave = _line(room_server)
    rejoin = {"part": 3, "missing": 30, "chunks": 100}
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, rejoin=rejoin), _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    # השדה ירד והמגירה עדיין כותבת: זה מה שהדיווח אומר, ולא "הושלם"
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1), _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    out = _rows(room_server, "HOLD_COMPLETING")[0]["outcome"]
    assert "rejoin ירד" in out and "writing" in out and "הושלמה" not in out, out
    # מגירה אחרת, ההשלמה שלה נכשלה
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, rejoin=rejoin), _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "failed", error="חתיכה לא תאמה"),
                                           _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    assert _rows(room_server, "HOLD_COMPLETING")[1]["outcome"] == "ההשלמה נכשלה: חתיכה לא תאמה"


def test_a_new_partition_is_a_new_hold_and_the_old_one_is_closed(room_server):
    wave = _line(room_server)
    for part in (3, 4):
        _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, rejoin={"part": part, "missing": 9, "chunks": 90}),
                                               _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    rows = _rows(room_server, "HOLD_COMPLETING")
    assert len(rows) == 2 and rows[0]["outcome"] == "הסוכן עבר למחיצה 4" and rows[1]["outcome"] is None


@pytest.mark.parametrize("bad", [{"missing": -1}, {"missing": "5"}, {"missing": True}, {"missing": None}, {"part": 3}, "x", []])
def test_a_rejoin_without_a_count_is_no_decision(room_server, bad):
    wave = _line(room_server)
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, rejoin=bad), _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    assert _rows(room_server, "HOLD_COMPLETING") == [], "ספירה שלא נאמרה אינה אפס, ואין החלטה"


def test_with_the_brain_off_nothing_is_decided(room_server):
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "off")
    wave = _line(room_server)
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, rejoin={"part": 3, "missing": 30}), _t("sdb", "A2", 2, "done"),
                                           _t("sdc", "A3", 3)])
    assert _rows(room_server, "HOLD_COMPLETING") == []


# --- B30 ----------------------------------------------------------------------------------------------------


def test_a_suspect_drawer_is_decided_in_shadow_and_the_agent_hears_nothing(room_server):
    wave = _line(room_server)
    got = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done"), _t("sdb", "A2", 2, "done"), _failed_with(GOOD)])
    assert "verify" not in got
    rows = _rows(room_server, "VERIFY_REPAIR")
    assert len(rows) == 1
    assert (rows[0]["rule"], rows[0]["verdict"], rows[0]["mode"], rows[0]["entity_key"]) == ("K5", "REPAIR", "shadow", "A3")
    evidence = json.loads(rows[0]["evidence_json"])
    assert "bad_chunks=3" in evidence and "partitions=2" in evidence and "first=p2#3,p2#7,p4#0" in evidence
    assert "execution=off" in evidence
    again = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done"), _t("sdb", "A2", 2, "done"), _failed_with(GOOD)])
    assert "verify" not in again and len(_rows(room_server, "VERIFY_REPAIR")) == 1, "אותה רשימה: לא נרשמת שוב"


def test_enforce_alone_does_not_run_it_the_switch_does(room_server):
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "enforce")
    wave = _line(room_server)
    targets = [_t("sda", "A1", 1, "done"), _t("sdb", "A2", 2, "done"), _failed_with(GOOD)]
    assert "verify" not in _progress(room_server, wave, CLONER1, targets), "המתג כבוי: הביצוע כבוי גם ב-enforce"
    set_setting(room_server["ctx"].conn, "brain_verify_repair", "on")
    got = _progress(room_server, wave, CLONER1, targets)
    assert got["verify"] == {"A3": {"verdict": "REPAIR", "partitions": {"2": [3, 7], "4": [0]}}}
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "shadow")
    assert "verify" not in _progress(room_server, wave, CLONER1, targets), "המתג דלוק, והמוח בצל: הסוכן לא שומע"


@pytest.mark.parametrize("chunks", [[], None, "x", [{"partition": 2}], [{"partition": -1, "chunk_index": 0}],
                                    [{"partition": 2, "chunk_index": True}], [{"partition": 2, "chunk_index": 1}, 5]])
def test_a_missing_empty_or_broken_list_is_no_decision(room_server, chunks):
    wave = _line(room_server)
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done"), _t("sdb", "A2", 2, "done"), _failed_with(chunks)])
    assert _rows(room_server, "VERIFY_REPAIR") == [], "רשימה ריקה היא 'נבדק ותקין', ופגומה היא 'לא נקרא': אף אחת אינה החלטה"


def test_the_switch_is_a_setting_that_takes_on_and_off_only(room_server):
    admin = room_server["admin"]
    assert admin.get("/api/console/settings").json()["brain_verify_repair"] == "off", "כבוי כשלא נקבע"
    assert admin.post("/api/console/settings", json={"brain_verify_repair": "on"}).status_code == 200
    assert admin.get("/api/console/settings").json()["brain_verify_repair"] == "on"
    assert completing.verify_enabled(room_server["ctx"].conn)
    for bad in ("yes", "ON", "1", True, None, ""):
        assert admin.post("/api/console/settings", json={"brain_verify_repair": bad}).status_code == 400, bad
    assert admin.get("/api/console/settings").json()["brain_verify_repair"] == "on"


# --- R5 ----------------------------------------------------------------------------------------------------


def _drawers(rs):
    machine = next(m for m in room(rs["deploy"])["machines"] if m["mac"] == CLONER1)
    return {d["dev"]: d for d in machine["drawer_list"]}


def test_the_console_row_gets_the_count_of_a_completing_drawer_and_only_that(room_server):
    wave = _line(room_server)
    _progress(room_server, wave, CLONER1, [
        _t("sda", "A1", 1, rejoin={"part": 3, "missing": 412, "chunks": 18254, "away_s": 2, "step": "http", "x": 1}),
        _t("sdb", "A2", 2, "done", rejoin={"part": 3, "missing": 5}),
        _t("sdc", "A3", 3, rejoin={"part": "3", "missing": 7, "chunks": -4, "step": "carrier-pigeon"})])
    drawers = _drawers(room_server)
    assert drawers["sda"]["rejoin"] == {"missing": 412, "chunks": 18254, "part": 3, "step": "http"}
    assert drawers["sdb"]["rejoin"] is None, "רק על מגירה שכותבת"
    assert drawers["sdc"]["rejoin"] == {"missing": 7, "chunks": None, "part": None, "step": None}, "שדה שלא נאמר אינו 0"
    _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1), _t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)])
    assert _drawers(room_server)["sda"]["rejoin"] is None
