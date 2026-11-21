"""#3064 (K9), #3275 -- **סיווג סיבת הכשל של דיסק לפי ראיה** (`server/brain/cause.py`).

- **ניתוק בקרנל** (כבל, מגירה שנשלפה): ``LINK``, לא נגד הדיסק.
- **skipped=0 עם client_lost**, או חלקים שלא הגיעו: ``NETWORK``.
- **כשל תוכנה או זרם** (זרם ריק, הזרם לא הגיע, הכתיבה לא התחילה): ``SOFTWARE``, לא נגד הדיסק.
- **רק שגיאת כונן אמיתית מהקרנל:** ``DRIVE``, נגד הדיסק.
- **"לא סווג"** הוא מצב בשם משלו, ולא מתקפל לאף סיבה (עיקרון 5): היעדר ראיה אינו ראיה.
- **צל:** ההחלטה נרשמת ב-``brain_decisions`` (כלל K9), והכשל, הסיבה שלו והצבע לא משתנים.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import cause as K, memory, veto, repairrule
from server.disks import disk_failures
from server.rounds import repair

from test_chunksend_2533 import ALL, rig, wait_for   # noqa: F401 — rig is a fixture
from test_disk_failures import CABLE_LINES, DISK_LINES, failed_target

MAC = "b4:2e:99:07:1a:c4"
#: #3008/#1602: מגירה שנשלפה -- הקרנל דחה כתיבה להתקן שאינו זמין, בלי טיפול שגיאות של ATA.
PULLED_LINES = ["[  211.000001] blk_update_request: I/O error, dev sdb, sector 4096 op 0x1:(WRITE)",
                "[  211.000002] sd 1:0:0:0: rejecting I/O to offline device",
                "[  211.000003] blk_update_request: device offline error, dev sdb, sector 4096"]


def ev(**kw) -> K.Verdict:
    return K.classify(K.Evidence(**kw))


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "k9.db")
    yield c
    veto.forget()


def k9(conn, **kw) -> list[dict]:
    return memory.decisions(conn, rule="K9", limit=100, **kw)


# --- הסיווג: פונקציה טהורה ----------------------------------------------------------------------------------------


def test_a_cable_in_the_kernel_is_the_link_not_the_disk():
    """#420: 4 איפוסי link ו-"drive too slow". הקרנל אמר כבל -- גם כשהמקלט אמר שהדיסק פיגר."""
    v = ev(kernel=tuple(CABLE_LINES), skipped=9, wrote=True)
    assert v.cause == K.LINK and not v.against_disk, v
    assert "cable" in v.reason


@pytest.mark.parametrize("kernel, error", [
    (tuple(PULLED_LINES), None),                        # device offline, בלי exception Emask
    (None, "הכונן נעלם מהמערכת (sdb)"),                  # הסוכן ראה את /sys/block/<d> נעלם, בלי שורות
])
def test_a_drawer_that_left_is_the_link_not_the_disk(kernel, error):
    v = ev(kernel=kernel, error=error)
    assert v.cause == K.LINK and not v.against_disk and "removed" in v.reason, v


def test_a_media_error_is_the_drive_and_only_it_is_against_the_disk():
    v = ev(kernel=tuple(DISK_LINES), wrote=True)
    assert v.cause == K.DRIVE and v.against_disk and "disk" in v.reason, v


def test_skipped_zero_and_a_lost_client_is_the_network():
    """#3141 (מעבדת ה-VM 14/11): ניתוק של כ-60 שניות, `211 of 525 durable, 0 skipped`, והדיסק נכשל "לא עמד בקצב"."""
    v = ev(skipped=0, client_lost=True, lost_in_net=314)
    assert v.cause == K.NETWORK and not v.against_disk and "איבד את המחשב" in v.reason, v


def test_skipped_zero_and_chunks_that_never_arrived_is_the_network():
    v = ev(skipped=0, lost_in_net=7)
    assert v.cause == K.NETWORK and "7 חלקים לא הגיעו" in v.reason, v


@pytest.mark.parametrize("kw", [
    {"error": "fanout: empty stream rc=1", "wrote": True},        # #1728: אתחול באמצע זרם, שני דיסקים תקינים
    {"error": "פג הזמן -- הזרם לא הגיע", "kernel": ()},              # #1580
    {"error": "השרת סגר את הסבב"},
    {"wrote": False},                                              # #1429: כשל תוכנה לפני כתיבה
])
def test_a_software_or_stream_failure_is_not_against_the_disk(kw):
    v = ev(**kw)
    assert v.cause == K.SOFTWARE and not v.against_disk, v


def test_the_kernel_comes_before_the_network():
    """ניתוק בקרנל הוא ההסבר גם כשהמחשב נותק: הוא ראיה על הדיסק עצמו."""
    assert ev(kernel=tuple(CABLE_LINES), skipped=0, client_lost=True).cause == K.LINK
    assert ev(kernel=tuple(DISK_LINES), skipped=0, client_lost=True).cause == K.DRIVE


@pytest.mark.parametrize("kw, says", [
    ({}, "אין ראיה"),
    # המקלט אמר שפיגר, ואין ראיה מהקרנל: לא "כונן" -- אי אפשר לשלול כבל (#420)
    ({"skipped": 9}, "אי אפשר לשלול כבל"),
    # ניתוק בלי skipped: אין לדעת אם הדיסק פיגר
    ({"client_lost": True, "lost_in_net": 300}, "skipped"),
    # skipped=0 ושום דבר לא אבד: אין ראיה לרשת
    ({"skipped": 0, "lost_in_net": 0, "client_lost": None}, "אין ראיה"),
    # אין שורות קרנל אינו "הקרנל נקי", ובוודאי לא "כונן"
    ({"kernel": (), "wrote": True}, "אין ראיה"),
    ({"kernel": ("[  1.0] ata3: something we have never seen",), "wrote": True}, "אינן מתאימות"),
])
def test_unclassified_is_a_state_of_its_own_and_never_a_guess(kw, says):
    v = ev(**kw)
    assert v.cause == K.UNCLASSIFIED and not v.against_disk, v
    assert says in v.reason, v


def test_only_the_drive_is_against_the_disk():
    assert [c for c in K.CAUSES if K.Verdict(c, "").against_disk] == [K.DRIVE]


# --- הרישום: צל, ב-brain_decisions ---------------------------------------------------------------------------------


def test_the_cause_is_recorded_in_shadow_with_what_the_existing_code_did(conn):
    v = K.judge(conn, point="D11", round_id="room_1", mac=MAC, serial="S1", existing="נכשל: לא עמד בקצב",
                skipped=0, client_lost=True)
    assert v is not None and v.cause == K.NETWORK
    (row,) = k9(conn)
    assert (row["mode"], row["point"], row["action"], row["verdict"]) == ("shadow", "D11", K.ACTION, K.NETWORK)
    assert (row["entity_kind"], row["entity_key"], row["round_id"]) == ("disk", "S1", "room_1")
    assert row["outcome"].startswith(K.SHADOW_HE) and "לא עמד בקצב" in row["outcome"]
    assert f"mac={MAC}" in row["evidence"] and "skipped=0" in row["evidence"]


def test_unclassified_is_recorded_by_its_own_name(conn):
    K.judge(conn, point="D15", round_id="room_1", mac=MAC, serial="S1", existing="נכשל")
    assert [r["verdict"] for r in k9(conn)] == [K.UNCLASSIFIED]


def test_off_computes_and_records_nothing(conn):
    db.set_setting(conn, veto.SETTING, "off")
    assert K.judge(conn, point="D11", round_id="r", mac=MAC, serial="S1", existing="x", skipped=0) is None
    assert k9(conn) == []


def test_a_brain_that_fails_never_throws_into_the_point(monkeypatch):
    monkeypatch.setattr(memory, "_orphans_closed", True)       # the broken connection does not spend the process's once

    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("the disk of the server is gone")

    K.judge(Broken(), point="D11", round_id="r", mac=MAC, serial="S1", existing="x", skipped=1)   # no raise
    assert K.judge(object(), point="D11", round_id="r", mac=MAC, serial="S1", existing="x",
                   no_such_field=1) is None


# --- הנקודה: רישום disk_failure ------------------------------------------------------------------------------------


def test_a_cable_failure_is_recorded_once_as_the_link_and_the_failure_itself_does_not_change(conn):
    row = disk_failures.record(conn, "ses1", MAC, failed_target())
    assert row["cause"] == "cable"                                              # כמו לפני K9
    assert disk_failures.record(conn, "ses1", MAC, failed_target()) is None    # הדיווח החוזר
    (d,) = k9(conn)
    assert (d["point"], d["verdict"], d["entity_key"], d["round_id"]) == ("disk_failure", K.LINK, "S3TWNE0JB04745",
                                                                           "ses1")
    assert "disk_failure cause=cable" in d["outcome"] and "wrote=True" in d["evidence"]


def test_a_drive_failure_is_the_drive(conn):
    disk_failures.record(conn, "ses1", MAC, failed_target(ata_log=DISK_LINES, error="partition 3: fsync errno=5"))
    assert [d["verdict"] for d in k9(conn)] == [K.DRIVE]


def test_a_stream_that_did_not_arrive_is_software_and_paints_nothing(conn):
    row = disk_failures.record(conn, "ses1", MAC, failed_target(ata_log=[], error="פג הזמן -- הזרם לא הגיע"))
    assert row is not None and row["cause"] == "unclassified"                   # כמו לפני K9
    (d,) = k9(conn)
    assert d["verdict"] == K.SOFTWARE and "אינו צובע" in d["outcome"], d


def test_a_stream_fault_marks_nothing_and_so_k9_records_nothing(conn):
    """#1728: כשל של הזרם בלי ראיה מהקרנל אינו נרשם כ-disk_failure -- אין סימון, ואין מה לסווג."""
    assert disk_failures.record(conn, "ses1", MAC, failed_target(ata_log=[], error="fanout: empty stream")) is None
    assert k9(conn) == []


# --- הנקודה: מנוע החתיכות ------------------------------------------------------------------------------------------


def _a_laggard_that_never_completes(rig) -> dict:
    """#3373 (#3170 מול #3295, נדב 15/11): דיסק שפיגר בגל הראשון **אינו נכשל** -- התשובה הראשונה היא `fetch` בלי `fail`.
    כשל נשאר רק למי שעדיין חסר לו אחרי `MAX_REPAIRS` סבבי השלמה: כאן B1 מדווח כל פעם שחסרה לו אותה חתיכה, והתשובה
    שחוזרת היא האחרונה (`done` עם `fail`)."""
    first = rig.ask("b", 1, {"B1": [[0, 0]], "B2": ALL}, skipped={"B1": 9, "B2": 0})
    assert first["action"] == "fetch" and "fail" not in first, first
    for n in range(2, repairrule.MAX_ROUNDS + 3):
        last = rig.ask("b", n, {"B1": [[0, 0]], "B2": ALL})
        if last["action"] != "fetch":
            return last
    raise AssertionError("B1 was never failed, however many repair rounds went by")


def test_a_disk_that_lagged_and_never_completes_is_unclassified_without_the_kernel_and_fails_after_the_repairs(rig, conn):
    """D12 (עד #3373: D11): המקלט אמר שהדיסק פיגר (skipped=9), והחלקים החסרים לא הושלמו גם אחרי `MAX_REPAIRS` סבבים --
    השרת מכשיל אותו בשמו כמו לפני K9. K9 רושם "לא סווג": המנוע אינו רואה את הקרנל, ופיגור לבדו אינו שגיאת כונן."""
    engine = rig.make()
    engine.cause = lambda **kw: K.judge(conn, **kw)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("c", 1, "ended", {"C1": ALL})
    b = _a_laggard_that_never_completes(rig)
    assert b["action"] == "done" and "לא הצליח לקבל את כל הנתונים" in b["fail"]["B1"], b              # ההתנהגות לא השתנתה
    assert wait_for(lambda: k9(conn))
    (d,) = k9(conn)
    assert (d["point"], d["entity_key"], d["verdict"]) == ("D12", "B1", K.UNCLASSIFIED), d
    assert "skipped=9" in d["evidence"] and "אי אפשר לשלול כבל" in d["reason"]
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_a_disk_that_lags_but_completes_is_not_failed_and_K9_has_nothing_to_classify(rig, conn):
    """#3373: דיסק שפיגר ומשלים אינו כשל -- אין ל-K9 מה לסווג, והוא אינו רושם דבר."""
    engine = rig.make()
    engine.cause = lambda **kw: K.judge(conn, **kw)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("c", 1, "ended", {"C1": ALL})
    b = rig.ask("b", 1, {"B1": [[0, 0]], "B2": ALL}, skipped={"B1": 9, "B2": 0})
    assert b["action"] == "fetch" and "fail" not in b, b
    done = rig.ask("b", 2, {"B1": ALL, "B2": ALL})
    assert done["action"] == "done" and "fail" not in done, done
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert k9(conn) == []


def test_a_machine_cut_off_in_the_first_pass_then_silent_is_the_network(rig, conn):
    """D15 על המקרה של #3141: המשכפל נותק במעבר הראשון וחזר עם `0 skipped`, ואז שתק בהורדה. הדיסק נכשל בשמו -- כמו
    לפני K9 -- ו-K9 רושם "רשת": השרת איבד את המחשב, והדיסק לא פיגר."""
    engine = rig.make()
    engine.cause = lambda **kw: K.judge(conn, **kw)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.members = ["a", "b"]                                # c is lost: the server no longer counts it alive
    time.sleep(0.15)
    rig.members = ["a", "b", "c"]
    rig.tell("b", 1, "ended", {"B1": ALL})
    c = rig.ask("c", 1, {"C1": [[0, 2]]}, skipped={"C1": 0})
    assert c["action"] == "fetch", c
    assert wait_for(lambda: engine.status()["state"] == "done", 4), engine.status()      # c says nothing again
    assert any(d.startswith("c C1:") for d in rig.named("chunk_disk_failed"))           # failed by name, as before
    (d,) = k9(conn)
    assert (d["point"], d["entity_key"], d["verdict"]) == ("D15", "C1", K.NETWORK), d
    assert {"skipped=0", "client_lost=True", "lost_in_net=7"} <= set(d["evidence"]), d


def test_a_cause_that_throws_does_not_stop_the_wave(rig):
    def boom(**_kw):
        raise RuntimeError("brain down")

    engine = rig.make()
    engine.cause = boom
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("c", 1, "ended", {"C1": ALL})
    assert "B1" in _a_laggard_that_never_completes(rig)["fail"]
    assert wait_for(lambda: engine.status()["state"] == "done")
