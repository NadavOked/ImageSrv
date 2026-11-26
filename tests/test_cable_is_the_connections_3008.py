"""#3008 (נדב 12/11, הוכרע 23/11): כשל שסיבתו כבל/חריץ הוא של **החיבור** -- הדיסק לא מואשם.

לפני: רשומת כשל עם ``cause=cable`` נספרה כ-``failed_last`` ("נכשל בשיכפול הקודם"), ולכן כרטיס המשכפל בקונסולה (``bayChip`` /
``bayLines`` מתוך ``slotRule``, שהוא המוח) כתב על דיסק תקין שנכנס לחריץ בעייתי שהוא נכשל. אחרי: המוח מסווג אותו ``cable_failed`` --
**אותו צבע (צהוב) ואותן הרשאות** (לא מקור, כן יעד), ומילים אחרות: "חיבור לא תקין", כמו שבב הטבלה והכותרת "חיבור של <שם> לא תקין".

מה נבדק: (1) הפיצול ב-`failures_of`; (2) הסיווג: אותו צבע והרשאות, מילים של חיבור; (3) כשל של הדיסק עצמו גובר; (4) מה שלא כבל לא זז
(אדום, קריאה בלבד, חריץ של דיסק אחר); (5) הראיה שנרשמת; (6) המילים שהקונסולה כותבת הן המילים שהמוח שולח.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server import db
from server.brain import diskcolor, memory
from server.disks import disk_rule

ROOT = Path(__file__).resolve().parents[1]
CABLE = {"serial": "S1", "port": 1, "cause": "cable"}
DISK_HIS = {"serial": "S1", "port": 1, "cause": "disk"}
DISK = {"serial": "S1", "port": 1, "smart": "ok"}
CABLE_HE = "חיבור לא תקין — לא מעתיקים ממנו; אפשר לכתוב עליו"
FAILED_LAST_HE = "נכשל בשיכפול הקודם — לא מעתיקים ממנו; אפשר לכתוב עליו"


def test_failures_of_splits_a_cable_cause_from_the_disks_own():
    assert disk_rule.failures_of([CABLE], DISK) == {"failed_last": False, "readonly": False, "cable_failed": True}
    assert disk_rule.failures_of([DISK_HIS], DISK) == {"failed_last": True, "readonly": False, "cable_failed": False}
    assert disk_rule.failures_of([{**DISK_HIS, "cause": "readonly"}], DISK) == {"failed_last": False, "readonly": True, "cable_failed": False}
    assert disk_rule.failures_of([], DISK) == {"failed_last": False, "readonly": False, "cable_failed": False}


def test_a_cable_cause_is_yellow_in_the_connections_words_not_the_disks():
    got = diskcolor.classify(DISK, [CABLE])
    assert (got["color"], got["reason"], got["source_reason"]) == ("yellow", "cable_failed", "cable_failed")
    assert got["source_reason_he"] == CABLE_HE and got["source_reason_short_he"] == "חיבור לא תקין"
    assert "נכשל בשיכפול הקודם" not in got["source_reason_he"] + got["source_reason_short_he"]


def test_only_the_words_changed_not_the_colour_and_not_the_permissions():
    """Group (ג), hard safety: the cable cause is yellow -- not a source, still a target -- exactly as when it was `failed_last`."""
    cable, disk = diskcolor.classify(DISK, [CABLE]), diskcolor.classify(DISK, [DISK_HIS])
    assert disk["source_reason_he"] == FAILED_LAST_HE
    for key in ("color", "source", "target", "verdict"):
        assert cable[key] == disk[key], key
    assert (cable["source"], cable["target"]) == (False, True)


def test_a_failure_of_the_disk_itself_wins_over_a_cable_one():
    got = diskcolor.classify(DISK, [CABLE, DISK_HIS])
    assert (got["reason"], got["source_reason_he"]) == ("failed_last", FAILED_LAST_HE)
    assert diskcolor.classify(DISK, [DISK_HIS, CABLE])["reason"] == "failed_last"      # not the order of the records


def test_what_is_not_a_cable_cause_did_not_move():
    red = diskcolor.classify({**DISK, "smart": "fail"}, [CABLE])
    assert (red["color"], red["reason"], red["target"]) == ("red", "smart_fail", False)   # a failing SMART beats the connection
    ro = diskcolor.classify(DISK, [{**DISK_HIS, "cause": "readonly"}])
    assert (ro["color"], ro["reason"]) == ("red", "smart_fail")
    other = diskcolor.classify(DISK, [{"serial": "OTHER", "port": 2, "cause": "cable"}])   # another serial in another drawer: nothing here
    assert (other["color"], other["reason"]) == ("green", None)
    none = diskcolor.classify(DISK, [])
    assert (none["color"], none["reason"]) == ("green", None)


def test_the_table_has_the_connection_row_and_its_words_are_the_table_chip():
    assert disk_rule.YELLOW == ("failed_last", "cable_failed", "write_incomplete")
    assert disk_rule.REASON_SHORT_HE["cable_failed"] == "חיבור לא תקין"
    js = (ROOT / "server/static/console.js").read_text(encoding="utf-8")
    pill = re.search(r'cause === "cable"\) return chip\("y", "([^"]+)"\)', js)
    assert pill and pill.group(1) == disk_rule.REASON_SHORT_HE["cable_failed"]          # the disk tab says the same word


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c


def test_the_brain_records_the_connection_verdict_with_its_evidence(conn):
    got = diskcolor.decide(conn, DISK, [CABLE], point="test", mac="aa:bb")
    assert got["source_reason"] == "cable_failed"
    (row,) = [r for r in memory.decisions(conn, kind="disk", key="S1", rule="K3") if r["action"] == "DISK_COLOR"]
    assert (row["verdict"], row["reason"]) == ("DISK_YELLOW", CABLE_HE)
    (facts,) = row["evidence"]
    assert facts["cable_failed"] is True and facts["failed_last"] is False and facts["reasons"] == ["cable_failed"]
