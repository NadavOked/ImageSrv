"""#3666 (אפיק #3653, הכרעת נדב 18/11): הסיווג של צבע הדיסק עבר למוח (K3/DISK_COLOR) -- והטבלה נשארה בדיוק מה שהייתה.

שני חצאים, ושניהם נבדקים:

1. **שקילות** -- כל צירוף של עובדות נותן אותו צבע, אותה הרשאת מקור ויעד ואותה סיבה (ואותו משפט) **לפני השינוי ואחריו**. ה"לפני"
   הוא `tests/fixtures/disk_rule_golden_3666.json`: הפסקים של הטבלה כפי שהייתה ב-`02d209257` (lab/vm-1114f r33), מופקים מהקוד הישן
   לפני שנגעו בו. הטבלה היא קבוצה (ג), בטיחות קשיחה: דיסק אדום לא נכתב. הטסט הזה עובר בשני המצבים בכוונה -- הוא הרשת נגד
   "תיקון" שמחליש אותה.
2. **המקום שמחליט הוא אחד** -- המוח מסווג, רושם החלטה עם הראיות, ומגיש צבע וסיבה מוכנים: ל-`drawer_list` של החדר, ל-`/machines`
   של הקונסולה, ולסירוב לפתוח סבב. אלה הטסטים שנופלים על הקוד של לפני השינוי (בקרה שלילית).

**הטקסטים כתובים כאן במפורש** ולא נקראים מהקובץ: בלי זה טבלה שהתרוקנה הייתה "עוברת" מול עצמה.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from server import db
from server.brain import memory

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/fixtures/disk_rule_golden_3666.json").read_text(encoding="utf-8"))

SMART_FAIL_HE = "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת"
WRITE_INCOMPLETE_HE = "כתיבה לא הושלמה — לא מעתיקים ממנו; אפשר לכתוב עליו"
SENTENCES = {
    "smart_fail": SMART_FAIL_HE,
    "sata_issue": "בעיית SATA (הקישור התאפס או המגירה התנתקה) — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו",
    "write_slow": "נפסל כאיטי בבדיקת הכתיבה בסבב הזה — לא כותבים עליו בסבב הזה; אפשר להעתיק ממנו",
    "failed_last": "נכשל בשיכפול הקודם — לא מעתיקים ממנו; אפשר לכתוב עליו",
    "write_incomplete": WRITE_INCOMPLETE_HE,
    "cable_failed": "חיבור לא תקין — לא מעתיקים ממנו; אפשר לכתוב עליו",   # #3008 (23/11): after the golden was cut
}


def brain():
    from server.brain import diskcolor      # noqa: PLC0415 -- not at the top: the file collects with the fix reverted
    return diskcolor


# --- the golden inputs: the same generator as the one that produced the fixture (base 02d209257) ---------------

SMARTS = ("ok", "warn", "fail", "failed_last", "unchecked", None, "something_new")
FLAGS = ("failed_last", "write_incomplete", "sata_issue", "write_slow")
FAILS = ([], [{"serial": "S1", "port": 1, "cause": "disk"}], [{"serial": "S1", "port": 1, "cause": "readonly"}],
         [{"serial": "OTHER", "port": 2, "cause": "disk"}], [{"serial": None, "port": 1, "cause": "cable"}])


def facts_cases() -> list[dict]:
    out = []
    for sm in SMARTS:
        for bits in itertools.product((False, True), repeat=len(FLAGS)):
            f = dict(zip(FLAGS, bits))
            if sm is not None:
                f["smart"] = sm
            out.append(f)
    return out


def disk_cases() -> list[tuple[dict, list, bool]]:
    out = []
    for sm in ("ok", "warn", "fail", "failed_last", "unchecked", None):
        for mark in (None, "clean", "incomplete", "unchecked"):
            for sata in (None, [], ["link_reset"], ["drawer_gone"], ["junk"]):
                for fails in FAILS:
                    for slow in (False, True):
                        d = {"serial": "S1", "port": 1}
                        if sm is not None:
                            d["smart"] = sm
                        if mark is not None:
                            d["write_mark"] = mark
                        if sata is not None:
                            d["sata_issues"] = sata
                        out.append((d, fails, slow))
    return out


def sig(v: dict) -> str:
    return "|".join(str(int(v[k])) if isinstance(v[k], bool) else (v[k] or "") for k in
                    ("color", "source", "target", "reason", "source_reason", "target_reason"))


def sig_before(v: dict) -> str:
    """`sig`, with the one word #3008 (23/11) changed put back: a cable cause was `failed_last` when the golden was cut, and is `cable_failed`
    now -- same colour, same source, same target. Everything else must still be exactly what the golden says."""
    return sig(v).replace("cable_failed", "failed_last")


# --- 1. equivalence ------------------------------------------------------------------------------------------------

def test_the_golden_fixture_is_whole():
    assert len(GOLDEN["facts"]) == len(facts_cases()) == 112
    assert len(GOLDEN["disks"]) == len(disk_cases()) == 1200
    assert GOLDEN["reason_he"] == {k: v for k, v in SENTENCES.items() if k != "cable_failed"}
    colors = {s.split("|")[0] for s in GOLDEN["facts"] + GOLDEN["disks"]}
    assert colors == {"red", "yellow", "orange", "green", "grey"}      # every colour is in the data, so no colour is untested


def test_every_combination_of_facts_gives_the_same_verdict_as_before():
    from server.disks import disk_rule
    got = [sig_before(disk_rule.judge(f)) for f in facts_cases()]
    bad = [(f, g, w) for f, g, w in zip(facts_cases(), got, GOLDEN["facts"]) if g != w]
    assert not bad, bad[:3]


def test_every_disk_the_brain_classifies_gets_the_verdict_it_got_before():
    d = brain()
    got = [d.classify(disk, fails, write_slow=slow) for disk, fails, slow in disk_cases()]
    bad = [(c, sig_before(g), w) for c, g, w in zip(disk_cases(), got, GOLDEN["disks"]) if sig_before(g) != w]
    assert not bad, bad[:3]
    for g in got:                                                      # the words come with the verdict, the table's own
        for k in ("reason", "source_reason", "target_reason"):
            assert g[k + "_he"] == (SENTENCES[g[k]] if g[k] else None)


def test_red_is_never_a_target_in_any_combination():
    """The hard rule (group ג, #1732): a red disk is not written -- in ANY of the 1200 disks."""
    d = brain()
    red = [(disk, f, s) for disk, f, s in disk_cases() if d.classify(disk, f, write_slow=s)["color"] == "red"]
    assert red and all(not d.classify(disk, f, write_slow=s)["target"] for disk, f, s in red)
    for disk, f, s in disk_cases():                                   # ...and nothing but red refuses a target
        got = d.classify(disk, f, write_slow=s)
        assert got["target"] == (got["color"] != "red"), (disk, f, s, got)


def test_a_disk_that_was_not_read_is_never_green():
    d = brain()
    for disk in ({}, {"smart": "unchecked"}, {"smart": None}, {"smart": "something_new"}):
        got = d.classify({"serial": "S1", **disk})
        assert (got["color"], got["verdict"], got["by"]) == ("grey", "DISK_NOT_CHECKED", "K3/DISK_COLOR"), disk


# --- the table is one file, and a broken one is loud ---------------------------------------------------------------

def test_a_table_without_the_not_checked_row_does_not_load(tmp_path):
    from server.disks import disk_rule
    text = (ROOT / "agent/lib/data/disk-rule.tsv").read_text(encoding="utf-8")
    broken = tmp_path / "disk-rule.tsv"
    broken.write_text("\n".join(l for l in text.splitlines() if not l.startswith("smart\tunchecked")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not read is not green"):
        disk_rule._load(broken)
    broken.write_text(text.replace("smart\tok\tsmart=ok\tgreen", "smart\tok\tsmart=ok"), encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        disk_rule._load(broken)


def test_the_tables_sentences_are_the_ones_the_screens_say():
    from server.disks import disk_rule
    assert disk_rule.REASON_HE == SENTENCES
    assert disk_rule.RED == ("smart_fail", "sata_issue", "write_slow") and disk_rule.YELLOW == ("failed_last", "cable_failed", "write_incomplete")
    assert disk_rule.REASON_SHORT_HE == {"smart_fail": "SMART נכשל", "sata_issue": "בעיית SATA", "write_slow": "איטי בבדיקת הכתיבה",
                                         "failed_last": "נכשל בשיכפול הקודם", "cable_failed": "חיבור לא תקין",
                                         "write_incomplete": "כתיבה לא הושלמה"}


# --- 2. the brain records the decision, with its evidence -----------------------------------------------------------

@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c


def rows(conn, serial="S1"):
    return [r for r in memory.decisions(conn, kind="disk", key=serial, rule="K3") if r["action"] == "DISK_COLOR"]


def test_the_classification_is_recorded_as_a_decision_with_its_evidence(conn):
    got = brain().decide(conn, {"serial": "S1", "port": 2, "smart": "fail", "smart_reason": "health_failed", "sata_issues": []},
                         [], point="test", mac="aa:bb")
    assert (got["color"], got["target"], got["source"], got["by"]) == ("red", False, True, "K3/DISK_COLOR")
    (row,) = rows(conn)
    assert (row["verdict"], row["reason"], row["mode"], row["point"]) == ("DISK_RED", SMART_FAIL_HE, "shadow", "test")
    assert row["entity_kind"] == "disk" and row["outcome"] and "(צל" not in row["line_he"]      # served, not a shadow
    (facts,) = row["evidence"]
    assert facts["smart"] == "fail" and facts["smart_reason"] == "health_failed" and facts["sata_issues"] == []
    assert facts["table"] == "disk-rule.tsv" and facts["mac"] == "aa:bb" and facts["port"] == 2


def test_a_fact_that_was_not_read_is_null_in_the_evidence_not_false(conn):
    brain().decide(conn, {"serial": "S1", "smart": "ok"}, [])                    # an old agent: no sata_issues, no write_mark
    (facts,) = rows(conn)[0]["evidence"]
    assert facts["sata_issues"] is None and facts["write_mark"] is None and facts["smart_reason"] is None


def test_it_records_only_when_the_classification_changes(conn):
    d = brain()
    for _ in range(3):
        d.decide(conn, {"serial": "S1", "smart": "ok"}, [])
    assert [r["verdict"] for r in rows(conn)] == ["DISK_GREEN"]
    d.decide(conn, {"serial": "S1", "smart": "warn", "smart_reason": "pending_sectors"}, [])
    d.decide(conn, {"serial": "S1", "smart": "warn", "smart_reason": "pending_sectors"}, [])
    d.decide(conn, {"serial": "S1", "smart": "ok"}, [{"serial": "S1", "port": 1, "cause": "disk"}])
    assert [r["verdict"] for r in rows(conn)][::-1] == ["DISK_GREEN", "DISK_ORANGE", "DISK_YELLOW"]
    assert "pending_sectors" in rows(conn)[1]["reason"]
    assert rows(conn, "OTHER") == []                                                   # one disk's row is not another's


def test_a_disk_without_a_serial_is_classified_and_not_recorded(conn):
    got = brain().decide(conn, {"smart": "fail"}, [])
    assert got["color"] == "red" and memory.decisions(conn, kind="disk") == []


def test_the_switch_governs_the_record_never_the_classification(conn):
    """off = nothing is recorded -- and a red disk is still red, still not a target (group ג, hard safety)."""
    db.set_setting(conn, "brain_veto_mode", "off")
    got = brain().decide(conn, {"serial": "S1", "smart": "fail"}, [])
    assert (got["color"], got["target"]) == ("red", False)
    assert rows(conn) == []
    db.set_setting(conn, "brain_veto_mode", "enforce")
    brain().decide(conn, {"serial": "S1", "smart": "fail"}, [])
    assert [r["mode"] for r in rows(conn)] == ["enforce"]


def test_a_memory_that_fails_never_changes_the_classification(conn, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("the memory is down")
    monkeypatch.setattr(memory, "record", boom)
    got = brain().decide(conn, {"serial": "S1", "smart": "fail"}, [])
    assert (got["color"], got["target"]) == ("red", False)
    assert "was not recorded" in caplog.text


def test_the_hebrew_labels_exist_for_every_verdict_the_rule_writes():
    from server.brain import say
    d = brain()
    assert "DISK_COLOR" in say.ACTION_HE and set(d.VERDICT.values()) <= set(say.VERDICT_HE)


def test_say_gives_each_rule_its_own_text_for_red_and_neither_overwrites_the_other():
    """`VERDICT_HE` is one global map: B62's "RED" (a slow disk, #3268) and this rule's red disk are two different texts.
    The test does not pin B62's wording (it changes: #3728) -- only that the two stay apart."""
    from server.brain import say
    base = {"entity_kind": "disk", "entity_key": "S1", "reason": "", "mode": "shadow"}
    mine_he = {"DISK_RED": "אדום", "DISK_YELLOW": "צהוב", "DISK_ORANGE": "כתום", "DISK_GREEN": "ירוק", "DISK_NOT_CHECKED": "לא נבדק"}
    assert {k: say.VERDICT_HE[k] for k in mine_he} == mine_he          # this rule's texts are its own
    assert say.ACTION_HE["DISK_COLOR"] == "צבע הדיסק"
    b62_text = say.VERDICT_HE["RED"]                                    # whatever B62 says today
    assert b62_text and b62_text != say.VERDICT_HE["DISK_RED"] and b62_text not in mine_he.values()
    b62 = say.line_he({**base, "rule": "B62", "action": "SLOW_DISK", "verdict": "RED"})
    mine = say.line_he({**base, "rule": "K3", "action": "DISK_COLOR", "verdict": "DISK_RED"})
    assert b62_text in b62 and b62_text not in mine                     # B62's line says B62's text, this rule's line does not
    assert "→ צבע הדיסק: אדום" in mine and "SLOW_DISK" not in b62
    assert "(צל" not in mine and "(צל" in b62                           # this rule is served; B62 is a shadow row


# --- 3. over HTTP: the three places the brain's verdict is served ---------------------------------------------------

pytest.importorskip("fastapi")
from conftest import hello_body                                              # noqa: E402
from test_server_room import CLONER1, room, room_body, room_server           # noqa: E402,F401


def _hello(room_server, disks: list[dict]) -> None:
    body = hello_body(CLONER1)
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD", "removable": False,
                      "scheme": "gpt", "has_data": False, "port": i + 1, "smart": "ok", **d} for i, d in enumerate(disks)]
    assert room_server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def _decisions(room_server, serial):
    return [r for r in memory.decisions(room_server["ctx"].conn, kind="disk", key=serial, rule="K3")
            if r["action"] == "DISK_COLOR"]


def test_the_consoles_machines_list_carries_the_brains_verdict_for_every_disk(room_server):
    """What the console used to work out for itself (diskRule) arrives ready: colour, permissions, reason, words."""
    _hello(room_server, [{"serial": "G1"}, {"serial": "R1", "smart": "fail", "smart_reason": "health_failed"},
                         {"serial": "Y1", "write_mark": "incomplete"}, {"serial": "N1", "smart": "unchecked"}])
    (machine,) = [m for m in room_server["admin"].get("/api/console/machines").json() if m["mac"] == CLONER1]
    by = {d["serial"]: d.get("rule") or {} for d in machine["disks"]}
    assert {s: (r.get("color"), r.get("target"), r.get("source"), r.get("by")) for s, r in by.items()} == {
        "G1": ("green", True, True, "K3/DISK_COLOR"), "R1": ("red", False, True, "K3/DISK_COLOR"),
        "Y1": ("yellow", True, False, "K3/DISK_COLOR"), "N1": ("grey", True, True, "K3/DISK_COLOR")}
    assert (by["R1"].get("reason_he"), by["R1"].get("target_reason_short_he")) == (SMART_FAIL_HE, "SMART נכשל")
    assert (by["Y1"].get("source_reason_he"), by["N1"].get("color_he")) == (WRITE_INCOMPLETE_HE, "לא נבדק")
    assert {s: [r["verdict"] for r in _decisions(room_server, s)] for s in by} == {
        "G1": ["DISK_GREEN"], "R1": ["DISK_RED"], "Y1": ["DISK_YELLOW"], "N1": ["DISK_NOT_CHECKED"]}


def test_the_consoles_machines_list_applies_the_open_failure_record(room_server):
    """"נכשל בשיכפול הקודם" -- the console joined /disk-failures itself; now the server does, with the same match."""
    _hello(room_server, [{"serial": "F1"}])
    from server.disks import disk_failures
    room_server["ctx"].conn.execute(
        "INSERT INTO disk_failures (session_id, mac, dev, serial, port, ata_port, at, cause, error, ata_log)"
        " VALUES ('s1', ?, 'sda', 'F1', 1, 0, '2026-11-17T10:00:00+00:00', 'disk', 'x', '[\"I/O error\"]')", (CLONER1,))
    room_server["ctx"].conn.commit()                # a write left open would hold the lock against the request's own writes
    assert disk_failures.open_for(room_server["ctx"].conn, CLONER1, [{"serial": "F1"}])
    (machine,) = [m for m in room_server["admin"].get("/api/console/machines").json() if m["mac"] == CLONER1]
    rule = machine["disks"][0].get("rule") or {}
    assert (rule.get("color"), rule.get("source")) == ("yellow", False)


def test_the_rooms_drawer_list_is_the_brains_verdict_and_is_recorded(room_server):
    room_body(room_server, 2, {CLONER1: ["X", "Y"]})
    _hello(room_server, [{"serial": "X"}, {"serial": "Y", "smart": "warn", "smart_reason": "pending_sectors"}])
    assert room_server["deploy"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2, "target_slots": [{"mac": CLONER1, "ports": [1, 2]}]}).status_code == 200
    drawers = {d["serial"]: d for d in room(room_server["deploy"])["machines"][0]["drawer_list"]}
    assert {s: (d["rule"].get("by"), d["rule"]["color"]) for s, d in drawers.items()} == {
        "X": ("K3/DISK_COLOR", "green"), "Y": ("K3/DISK_COLOR", "orange")}
    assert [r["verdict"] for r in _decisions(room_server, "Y")] == ["DISK_ORANGE"]
    assert "pending_sectors" in _decisions(room_server, "Y")[0]["reason"]


def test_a_red_target_refusal_comes_from_the_brains_verdict_and_leaves_its_record(room_server):
    room_body(room_server, 2, {CLONER1: ["X", "Y"]})
    _hello(room_server, [{"serial": "X", "smart": "fail"}, {"serial": "Y"}])
    r = room_server["deploy"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2, "target_slots": [{"mac": CLONER1, "ports": [1, 2]}]})
    assert r.status_code == 400 and SMART_FAIL_HE in r.json()["detail"], r.text
    assert [(r["verdict"], r["point"]) for r in _decisions(room_server, "X")] == [("DISK_RED", "open_round")]
