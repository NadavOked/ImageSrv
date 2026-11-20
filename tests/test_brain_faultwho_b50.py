"""#3064 (B50): מי אשם בכשל של מגירה -- החריץ או הכבל, או הדיסק (`server/brain/faultwho.py`), בצל.

מה נבדק:
- **הפסק (`judge`, טהורה):** חריץ שאין בו ראיה בגל הזה אינו נשפט, גם כשההיסטוריה חשודה; ``SLOT`` על חריץ חשוד או על דיסקים שונים
  שנכשלו בו; ``DISK`` על דיסק שבעייתי בחריצים שונים; שניהם יחד = ``UNDECIDED`` ולא בחירה; חוש שלא נמדד אינו ראיה (עיקרון 5);
  חום בדיוק על הסף אינו חום.
- **הגל:** שורה אחת לחריץ עם ראיה, בצל, עם הראיות כמו שהן (``null`` = לא נמדד). כשל של **כל** המגירות בגל הוא הזרם ולא החריץ.
  אירועי phy/איפוס מהסוכן (S52, S53) הם ראיה לקו; הגל נשפט פעם אחת; המתג ``off`` אינו מחשב; כשל בקריאה אינו זורק.
- **הקטלוג:** B50 מצביע על קוד שקיים.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import faultwho as F, memory, veto
from server.disks import smart_history
from server.learn import ports as learn_ports

M = "aa:bb:cc:00:00:50"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "faultwho.db")
    yield c
    veto.forget()


_AT = iter(range(10, 99))


def hist(conn, wave, port, serial, *, failed=0, crc=None, link=None, link_max=None, cable=0, mac=M):
    """שורה אחת של `port_history`; `at` עולה, אז הגל האחרון שנכתב הוא החדש."""
    conn.execute("INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps, link_max_gbps, cable_fail, at, failed)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (wave, mac, port, serial, crc, link, link_max, cable, f"2026-11-15T{next(_AT):02d}:00:00+00:00", failed))
    conn.commit()


def member(targets, mac=M):
    return {"mac": mac, "targets_json": json.dumps(targets)}


def rows(conn, **kw):
    return memory.decisions(conn, rule=F.RULE, limit=100, **kw)


# --- the verdict ----------------------------------------------------------------------------------------------------------------


def test_a_slot_with_nothing_in_this_wave_is_not_judged_whatever_its_history_says():
    assert F.judge(F.Seen(port_suspect=True, port_disks=3, disk_suspect=True, failed_disks=5)) is None
    assert F.judge(F.Seen(failed=False)) is None


def test_a_suspect_slot_is_the_slot_and_not_the_disk():
    verdict, why = F.judge(F.Seen(failed=True, port_suspect=True, port_disks=2, disk_suspect=False))
    assert verdict == F.SLOT and "2 דיסקים שונים" in why and "לא הדיסק" in why


def test_two_different_disks_failing_in_one_slot_is_the_slot():
    verdict, why = F.judge(F.Seen(failed=True, port_suspect=False, failed_disks=learn_ports.MIN_DISKS))
    assert verdict == F.SLOT and "נכשלו בחריץ" in why
    assert F.judge(F.Seen(failed=True, failed_disks=learn_ports.MIN_DISKS - 1))[0] == F.UNDECIDED, "דיסק אחד אינו מכריע"


def test_a_disk_bad_in_several_slots_is_the_disk():
    verdict, why = F.judge(F.Seen(failed=True, disk_suspect=True, disk_ports=2, port_suspect=False))
    assert verdict == F.DISK and "2 חריצים" in why


def test_slot_and_disk_both_suspect_is_undecided_and_not_a_pick():
    verdict, why = F.judge(F.Seen(failed=True, port_suspect=True, port_disks=2, disk_suspect=True, disk_ports=2))
    assert verdict == F.UNDECIDED and "שום ראיה לא מפרידה" in why


def test_link_evidence_without_history_is_named_and_not_blamed():
    verdict, _ = F.judge(F.Seen(link_events=2))
    assert verdict == F.LINK_NOW
    assert F.judge(F.Seen(problem=True))[0] == F.LINK_NOW
    assert F.judge(F.Seen(link_events=0)) is None, "מדדנו ואין אירועים: אין ראיה"
    assert F.judge(F.Seen(link_events=None)) is None, "לא נמדד אינו ראיה לאף כיוון"


def test_heat_is_said_but_only_over_the_threshold():
    assert F.judge(F.Seen(peak_c=smart_history.TEMP_WARN_C)) is None
    verdict, why = F.judge(F.Seen(peak_c=smart_history.TEMP_WARN_C + 1))
    assert verdict == F.HOT and "לא מוכח כסיבה" in why
    assert F.judge(F.Seen(failed=True, peak_c=80, failed_disks=2))[0] == F.SLOT, "ראיה לחריץ גוברת על חום"


def test_a_failure_with_no_evidence_is_undecided_and_says_when_the_history_was_not_read():
    verdict, why = F.judge(F.Seen(failed=True, port_suspect=False))
    assert verdict == F.UNDECIDED and "לא נקראה" not in why
    assert "ההיסטוריה של החריץ לא נקראה" in F.judge(F.Seen(failed=True, port_suspect=None))[1]


# --- the wave ----------------------------------------------------------------------------------------------------------------------


def test_two_disks_failing_in_the_same_slot_while_the_neighbour_held_is_recorded_once_as_the_slot(conn):
    for wave, serial, mate in (("w1", "D1", "K1"), ("w2", "D2", "K2")):
        hist(conn, wave, 2, serial, failed=1)
        hist(conn, wave, 1, mate, failed=0)
    assert F.after_wave(conn, "w2", "room_x") == 1
    (got,) = rows(conn)
    assert (got["entity_kind"], got["entity_key"], got["verdict"], got["mode"]) == ("slot", f"{M}/2", F.SLOT, "shadow")
    assert got["point"] == "w2" and got["round_id"] == "room_x" and got["outcome"] == F.OUTCOME
    assert "failed=1" in got["evidence"] and "slot_failed_disks=2" in got["evidence"]
    assert "החריץ או הכבל" in got["line_he"] and "צל" in got["line_he"]
    assert F.after_wave(conn, "w2", "room_x") == 0 and len(rows(conn)) == 1, "הגל נשפט פעם אחת"


def test_every_drawer_failing_in_the_wave_is_the_stream_and_not_the_slot(conn):
    for wave, a, b in (("w1", "D1", "E1"), ("w2", "D2", "E2")):
        hist(conn, wave, 2, a, failed=1)
        hist(conn, wave, 1, b, failed=1)
    assert F.after_wave(conn, "w2", "room_x") == 2
    assert {r["verdict"] for r in rows(conn)} == {F.UNDECIDED}


def test_a_drawer_that_failed_without_a_recorded_state_is_not_called_failed(conn):
    hist(conn, "w1", 2, "D1", failed=None)
    hist(conn, "w1", 1, "K1", failed=0)
    assert F.after_wave(conn, "w1", "room_x") == 0 and rows(conn) == []


def test_a_disk_bad_in_two_slots_is_the_disk(conn):
    hist(conn, "w1", 1, "D1", crc=4, mac=M)
    hist(conn, "w2", 2, "D1", crc=3, mac=M)
    assert F.after_wave(conn, "w2", "room_x") == 1
    (got,) = rows(conn)
    assert got["verdict"] == F.DISK and "disk_bad_slots=2" in got["evidence"]


def test_phy_and_reset_counts_from_the_agent_are_link_evidence_and_unmeasured_ones_are_not(conn):
    hist(conn, "w1", 2, "D1", failed=0)
    hist(conn, "w1", 1, "K1", failed=0)
    targets = [{"dev": "sda", "serial": "D1", "state": "done",
                "senses": {"phy": {"total": 3, "ids": {"0x0001": 3}}, "resets": {"boot": 1, "delta": 1},
                           "conn": {"ata": 3, "sata": 2, "drawer": 2}, "temp": {"before_c": 30, "peak_during_c": 44, "after_c": 40, "samples": 3}}},
               {"dev": "sdb", "serial": "K1", "state": "done", "senses": {"phy": None, "resets": None, "temp": None, "conn": None}}]
    assert F.after_wave(conn, "w1", "room_x", [member(targets)]) == 1
    (got,) = rows(conn)
    assert got["entity_key"] == f"{M}/2" and got["verdict"] == F.LINK_NOW
    assert "link_events=4" in got["evidence"] and "peak_c=44" in got["evidence"] and "conn=ata3/SATA 2/drawer=2" in got["evidence"]
    assert "mac=" + M in got["evidence"]


def test_a_hot_disk_and_a_missing_senses_report_are_each_what_they_are(conn):
    hist(conn, "w1", 2, "D1", failed=0)
    hist(conn, "w1", 1, "K1", failed=0)
    hot = [{"dev": "sda", "serial": "D1", "senses": {"temp": {"before_c": 40, "peak_during_c": 71, "after_c": 65, "samples": 5}}}]
    assert F.after_wave(conn, "w1", "room_x", [member(hot)]) == 1
    assert rows(conn)[0]["verdict"] == F.HOT
    assert F.after_wave(conn, "w1", "room_y", [member("not json")]) == 0, "דיווח פגום: אין חושים, ואין ראיה"


def test_the_end_of_a_wave_in_the_room_runs_it(conn):
    """החיבור ב-`room._brain_after_wave`: בלעדיו הכלל נכתב ואף אחד לא קורא לו."""
    from server.rounds import room
    for wave, serial, mate in (("w1", "D1", "K1"), ("w2", "D2", "K2")):
        hist(conn, wave, 2, serial, failed=1)
        hist(conn, wave, 1, mate, failed=0)
    room._brain_after_wave(conn, {"wave_session_id": "w2", "id": "room_x", "target_slots_json": None}, [], 0)
    (got,) = rows(conn)
    assert got["verdict"] == F.SLOT and got["round_id"] == "room_x"


def test_the_switch_off_computes_nothing(conn):
    for wave, serial, mate in (("w1", "D1", "K1"), ("w2", "D2", "K2")):
        hist(conn, wave, 2, serial, failed=1)
        hist(conn, wave, 1, mate, failed=0)
    db.set_setting(conn, "brain_veto_mode", "off")
    assert F.after_wave(conn, "w2", "room_x") == 0 and rows(conn) == []


def test_a_failing_read_is_not_raised_into_the_end_of_the_wave(conn):
    conn.execute("DROP TABLE port_history")
    assert F.after_wave(conn, "w1", "room_x") == 0


def test_the_catalog_points_b50_at_the_code():
    import tomllib
    from pathlib import Path
    item = next(i for i in tomllib.loads(Path(F.__file__).with_name("catalog.toml").read_text(encoding="utf-8"))["item"]
                if i["id"] == "B50")
    assert (item["file"], item["symbol"]) == ("server/brain/faultwho.py", "judge")
    assert "def judge" in Path(F.__file__).read_text(encoding="utf-8")
