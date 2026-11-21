"""#3595 -- B50 (`server/brain/faultwho.py`) אמר ``UNDECIDED`` "אין ראיה שמבדילה" על חריץ ש-K9 (`cause.py`) סימן באותו גל, על אותו
סידורי, ``LINK`` ("ניתוק בקרנל (removed): כבל או מגירה, לא הדיסק"). דיסק שנותק בקרנל אינו מדווח חושים (``conn=null``,
``link_events=null``), ו-B50 לא קרא את K9. נמצא ב-LabRoot 17/11: ids 5842/5861 ו-5890/5905, חריץ ``78:ac:c0:9b:11:c2/3``.

מה נבדק:

- **שני הרצפים** (K9 ואז B50 באותו גל, אותו סידורי, ``failed=1``, אותן ``null``), דרך K9 האמיתי (`cause.judge`) ו-`after_wave`:
  הפסק הוא ``LINK_NOW`` שמצטט את K9, והראיות נושאות ``k9=LINK``.
- **רק ``LINK`` משנה את הפסק**, ורק כשיש מה לשפוט בגל: K9 אחר (``DRIVE``), K9 של גל אחר או של דיסק אחר, או היעדר שורה -- הפסק כמו
  קודם, והראיות אומרות ``k9=...`` או ``k9=none``. היסטוריה (``SLOT``/``DISK``) עדיין גוברת: B50 אינו מכריע "כבל" לבדו.
- **עיקרון 5:** K9 שלא נקרא (השאילתה נכשלה) הוא ``k9=unread`` והסיבה אומרת זאת, לא ``none``, ולא זורק אל סיום הגל.
- B50 נשאר בצל (``enforced`` לא משתנה), ו-K9 לא נגע.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import cause, faultwho as F, memory, veto

M = "78:ac:c0:9b:11:c2"
SAMSUNG = "naa.5002538d42a58a2c"        # the disk K9 called LINK (5842/5890), slot 3
APS = "t10.ATA     APS-SL3N-512 SICSD40302WL"
KERNEL = ("blk_update_request: device offline error, dev sdb, sector 0",)   # `ata_cause` -> removed -> K9 LINK


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "faultwho_k9.db")
    yield c
    veto.forget()


_AT = iter(range(10, 99))


def hist(conn, wave, port, serial, *, failed=0, mac=M):
    conn.execute("INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps, link_max_gbps, cable_fail, at, failed)"
                 " VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (wave, mac, port, serial, None, None, None, 0, f"2026-11-17T{next(_AT):02d}:00:00+00:00", failed))
    conn.commit()


def k9_says_link(conn, wave, serial=SAMSUNG, mac=M):
    """K9 האמיתי, כמו ב-`disk_failures._brain_cause`: שורות הקרנל של הדיסק שנותק."""
    got = cause.judge(conn, point="disk_failure", round_id=wave, mac=mac, serial=serial, existing="disk_failure cause=removed",
                      kernel=KERNEL, error="device offline error, dev sdb", wrote=True)
    assert got is not None and got.cause == cause.LINK, got
    return got


def slot3(conn, wave, **kw):
    """החריץ של 5861/5905: `failed=1`, ושום חוש (אין `members`): `link_events=null`, `conn=null`."""
    hist(conn, wave, 3, SAMSUNG, failed=1, **kw)
    hist(conn, wave, 2, APS, failed=0)


def rows(conn, **kw):
    return memory.decisions(conn, rule=F.RULE, limit=100, **kw)


# --- הרצפים מ-LabRoot --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("wave", ["w-5842", "w-5890"])
def test_k9_said_link_on_the_same_disk_in_the_same_wave_so_b50_does_not_say_there_is_no_evidence(conn, wave):
    slot3(conn, wave)
    k9_says_link(conn, wave)
    assert F.after_wave(conn, wave, "room_x") == 1
    (got,) = rows(conn)
    assert (got["entity_key"], got["verdict"], got["mode"]) == (f"{M}/3", F.LINK_NOW, "shadow"), got["line_he"]
    assert "k9=LINK" in got["evidence"] and "link_events=null" in got["evidence"] and "conn=null" in got["evidence"]
    assert "K9" in got["reason"] and "ניתוק בקרנל (removed)" in got["reason"], "the reason quotes K9"
    assert "אין ראיה" not in got["reason"] and got["outcome"] == F.OUTCOME


def test_the_two_waves_of_the_same_slot_are_each_judged_by_their_own_k9_row(conn):
    slot3(conn, "w1")
    k9_says_link(conn, "w1")
    slot3(conn, "w2")                                   # no K9 row in this wave
    assert F.after_wave(conn, "w1", "room_x") == 1 and F.after_wave(conn, "w2", "room_x") == 1
    by_wave = {r["point"]: r for r in rows(conn)}
    assert by_wave["w1"]["verdict"] == F.LINK_NOW and "k9=LINK" in by_wave["w1"]["evidence"]
    assert by_wave["w2"]["verdict"] == F.UNDECIDED and "k9=none" in by_wave["w2"]["evidence"], "K9 of another wave is not this wave's"


def test_k9_of_another_disk_in_the_wave_is_not_this_slots(conn):
    slot3(conn, "w1")
    k9_says_link(conn, "w1", serial="OTHER-DISK")
    F.after_wave(conn, "w1", "room_x")
    (got,) = rows(conn)
    assert got["verdict"] == F.UNDECIDED and "k9=none" in got["evidence"]


# --- הפסק ----------------------------------------------------------------------------------------------------------------------------


def test_a_k9_link_with_nothing_else_is_link_now_and_quotes_k9():
    verdict, why = F.judge(F.Seen(failed=True, port_suspect=False, k9="LINK", k9_reason="ניתוק בקרנל (removed): כבל או מגירה, לא הדיסק"))
    assert verdict == F.LINK_NOW and "ניתוק בקרנל (removed)" in why and "לא הדיסק" in why and "אין ראיה" not in why


def test_k9_link_adds_to_other_line_evidence_instead_of_replacing_it():
    verdict, why = F.judge(F.Seen(failed=True, link_events=2, k9="LINK", k9_reason="r"))
    assert verdict == F.LINK_NOW and "אירועי phy" in why and "K9" in why


@pytest.mark.parametrize("k9", ["DRIVE", "NETWORK", "SOFTWARE", "UNCLASSIFIED", "none"])
def test_only_a_link_verdict_of_k9_changes_the_verdict(k9):
    assert F.judge(F.Seen(failed=True, port_suspect=False, k9=k9, k9_reason="x"))[0] == F.UNDECIDED


def test_history_still_wins_over_k9_and_b50_does_not_pick_the_cable_alone():
    assert F.judge(F.Seen(failed=True, port_suspect=True, port_disks=2, k9="LINK", k9_reason="r"))[0] == F.SLOT
    assert F.judge(F.Seen(failed=True, disk_suspect=True, disk_ports=2, k9="LINK", k9_reason="r"))[0] == F.DISK


def test_k9_link_alone_is_not_a_reason_to_judge_a_slot_with_nothing_in_the_wave():
    assert F.judge(F.Seen(failed=False, k9="LINK", k9_reason="r")) is None


def test_a_k9_that_was_not_read_is_said_in_the_undecided_reason():
    assert "K9 לא נקרא" in F.judge(F.Seen(failed=True, port_suspect=False))[1]
    assert "K9 לא נקרא" not in F.judge(F.Seen(failed=True, port_suspect=False, k9="none"))[1]


# --- הקריאה: לא נקרא אינו "אין שורה" -----------------------------------------------------------------------------------------


def test_a_k9_read_that_fails_is_unread_not_none_and_is_not_raised_into_the_end_of_the_wave(conn, monkeypatch):
    slot3(conn, "w1")
    k9_says_link(conn, "w1")
    real = memory.decisions

    def boom(conn_, **kw):
        if kw.get("rule") == "K9":
            raise RuntimeError("db is busy")
        return real(conn_, **kw)

    monkeypatch.setattr(F.memory, "decisions", boom)
    assert F.after_wave(conn, "w1", "room_x") == 1
    monkeypatch.setattr(F.memory, "decisions", real)
    (got,) = rows(conn)
    assert "k9=unread" in got["evidence"] and "k9=none" not in got["evidence"]
    assert got["verdict"] == F.UNDECIDED and "K9 לא נקרא" in got["reason"]


def test_a_slot_row_without_a_serial_cannot_be_tied_to_a_k9_row_and_is_unread(conn):
    hist(conn, "w1", 3, "", failed=1)
    hist(conn, "w1", 2, APS, failed=0)
    F.after_wave(conn, "w1", "room_x")
    (got,) = rows(conn)
    assert "k9=unread" in got["evidence"]


# --- #3619: K9 אחר מ-LINK: הפסק נשאר, והנימוק אינו אומר "אין ראיה" -------------------------------------------------------------------


@pytest.mark.parametrize("k9", ["DRIVE", "NETWORK", "SOFTWARE", "UNCLASSIFIED"])
def test_undecided_quotes_k9_when_k9_classified_the_failure_and_does_not_say_there_is_no_evidence(k9):
    verdict, why = F.judge(F.Seen(failed=True, port_suspect=False, k9=k9, k9_reason="שגיאת כונן מהקרנל (disk)"))
    assert verdict == F.UNDECIDED, "one failure is not a history of slots: the verdict does not move"
    assert f'K9 סיווג את הכשל באותו גל על אותו דיסק {k9}: "שגיאת כונן מהקרנל (disk)"' in why
    assert "אין ראיה שמבדילה" not in why and "ואינו מכריע ממנו לבד" in why, why


def test_undecided_without_a_k9_row_or_with_an_unread_k9_keeps_the_text_it_had():
    assert "אין ראיה שמבדילה" in F.judge(F.Seen(failed=True, port_suspect=False, k9="none"))[1]
    unread = F.judge(F.Seen(failed=True, port_suspect=False))[1]
    assert "אין ראיה שמבדילה" in unread and "K9 לא נקרא" in unread and "K9 סיווג" not in unread


def test_a_drive_error_in_k9_reaches_the_recorded_reason_end_to_end(conn):
    """K9 האמיתי (`cause.judge`) מסווג שגיאת כונן מהקרנל, ו-B50 אחרי הגל רושם UNDECIDED שמצטט אותו."""
    slot3(conn, "w1")
    got = cause.judge(conn, point="disk_failure", round_id="w1", mac=M, serial=SAMSUNG, existing="disk_failure",
                      kernel=("blk_update_request: critical medium error, dev sdb, sector 0", "ata3.00: status: { DRDY ERR }",
                              "ata3.00: error: { UNC }"), error="", wrote=True)
    assert got is not None and got.cause == cause.DRIVE, got
    assert F.after_wave(conn, "w1", "room_x") == 1
    (row,) = rows(conn)
    assert row["verdict"] == F.UNDECIDED and "k9=DRIVE" in row["evidence"]
    assert "K9 סיווג" in row["reason"] and "DRIVE" in row["reason"] and "אין ראיה שמבדילה" not in row["reason"]

