"""#3064: חושי הסוכן בשרת (`server/brain/agentsenses.py`) ושער ה-AHCI (B23, R16), בצל.

מה נבדק:
- **ניתוח:** שדה חסר, פגום או `null` = `None` ("לא נמדד"); `bool` אינו מונה; מספר שלילי נדחה.
- **הפסק:** `NOT_READ` כשלא נקרא דבר -- לעולם לא `OBSERVED` ולא 0; `LINK_DROPPED`/`LINK_TROUBLE` רק על ראיה שעלתה בגל.
- **הרישום:** ב-`brain_decisions` עם `mode=shadow`, שורה למכונה ושורה לכל יעד; כלום כשהמתג `off`; אינו זורק.
- **זהות האימג' (S64, B31):** יומן הכתיבה של אימג' אחר נמחק כשדיסק נכתב מחדש (השומר הקיים), ושארית שלו -- אם יש --
  נקראת `MIXED`.
- **B23/R16:** `ide`/`raid` = `WOULD_BLOCK_BAND` ואזהרה כתומה, `ahci` = תקין, לא נקרא = `NOT_READ`; נרשם רק כשהשתנה;
  hello של סוכן ישן (בלי `sata_mode`) אינו מוחק החלטה.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import pytest

from server import db
from server.agent import firmware_image
from server.brain import agentsenses as A, memory, veto
from server.rounds import chunkledger as L

MAC = "aa:bb:cc:00:00:21"
SIZE = 256060514304
MANIFEST = {"partitions": [{"index": 3, "index_xxh64": "a" * 16, "readback_chunks": 100}]}


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "senses.db")
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("INSERT INTO sessions (id,group_id,image_id,prefix,expected_clients,wait_seconds,state,opened_by,created_at,last_join_at)"
              " VALUES ('ses_1','g','img_aaaaaa','p',1,1,'running','owner','2026-11-15T00:00:00+00:00',0)")
    c.commit()
    yield c
    veto.forget()


MACHINE = {"carrier": {"iface": "eth0", "changes": 5, "delta": 0}, "psi": {"samples": 3, "peak": {"cpu_some": 1.5, "io_some": 2, "io_full": 0, "mem_some": 0, "mem_full": 0}},
           "ctrl": [{"mode": "ahci", "driver": "ahci"}],
           "rx": {"passes": 2, "read": 2, "pkts": 10, "foreign": 0, "stray": None, "bad_pkt": 0, "dup": 0, "evicted": 0, "busy": 0,
                  "bad_chunk": 0, "good": 9, "skipped": 0, "conflict": 0, "version": 0},
           "http": None}
TARGET = {"phy": {"total": 0, "ids": {}}, "resets": {"boot": 1, "delta": 0}, "conn": {"ata": 1, "sata": 0, "drawer": 2},
          "temp": {"before_c": 30, "peak_during_c": 41, "after_c": 38, "samples": 4}, "ready_s": 12, "at_boot": True}


def rows(conn, **kw):
    return memory.decisions(conn, limit=100, **kw)


# --- parsing --------------------------------------------------------------------------------------------------------


def test_what_the_agent_did_not_say_is_none_never_zero():
    got = A.well_formed_machine({"carrier": None, "rx": {"passes": 1}, "psi": "x", "ctrl": {"a": 1}})
    assert got == {"carrier": None, "psi": None, "ctrl": None, "rx": None, "http": None}
    assert A.well_formed_machine("senses") is None and A.well_formed_target(None) is None
    t = A.well_formed_target({})
    assert t == {"phy": None, "resets": None, "conn": None, "temp": None, "ready_s": None, "at_boot": None}


@pytest.mark.parametrize("bad", [True, -1, 1.5, "7", None, 10 ** 17])
def test_a_counter_that_is_not_a_count_is_not_read(bad):
    got = A.well_formed_machine({"carrier": {"iface": "eth0", "changes": bad, "delta": bad}})
    assert got["carrier"] == {"iface": "eth0", "changes": None, "delta": None}
    t = A.well_formed_target({"ready_s": bad, "at_boot": bad if isinstance(bad, bool) else "yes"})
    assert t["ready_s"] is None and (t["at_boot"] is True if bad is True else t["at_boot"] is None)


def test_a_well_formed_report_passes_through_and_a_foreign_word_is_dropped():
    assert A.well_formed_target(TARGET) == TARGET
    got = A.well_formed_machine({**MACHINE, "ctrl": [{"mode": "weird", "driver": "a b;"}]})
    assert got["ctrl"] == [{"mode": None, "driver": None}]
    assert A.well_formed_target({"conn": {"ata": 2, "sata": 1, "drawer": "unmapped"}})["conn"]["drawer"] == "unmapped"


# --- the verdicts ---------------------------------------------------------------------------------------------------------


def test_nothing_read_is_not_read_not_observed():
    assert A.machine_verdict(None)[0] == A.NOT_READ
    assert A.machine_verdict(A.well_formed_machine({}))[0] == A.NOT_READ
    assert A.target_verdict(None)[0] == A.NOT_READ
    assert A.target_verdict(A.well_formed_target({}))[0] == A.NOT_READ


def test_a_dropped_link_and_phy_events_or_resets_are_named_for_what_they_are():
    m = {**MACHINE, "carrier": {"iface": "eth0", "changes": 7, "delta": 2}}
    verdict, why, ev = A.machine_verdict(A.well_formed_machine(m))
    assert verdict == A.LINK_DROPPED and "2" in why and "carrier=eth0:7:delta=2" in ev
    assert A.machine_verdict(A.well_formed_machine(MACHINE))[0] == A.OBSERVED
    unread = {**MACHINE, "carrier": {"iface": "eth0", "changes": 7, "delta": None}}
    assert A.machine_verdict(A.well_formed_machine(unread))[0] == A.OBSERVED, "a delta that could not be taken is not a drop"
    for field, value, text in (("phy", {"total": 3, "ids": {"0x0001": 3}}, "phy"), ("resets", {"boot": 4, "delta": 2}, "איפוס")):
        verdict, why, _ev = A.target_verdict(A.well_formed_target({**TARGET, field: value}), "img_aaaaaa")
        assert verdict == A.LINK_TROUBLE and text in why and "לא פסק דין על הדיסק" in why
    assert A.target_verdict(A.well_formed_target(TARGET), "img_aaaaaa")[0] == A.OBSERVED


# --- the record -----------------------------------------------------------------------------------------------------------


def test_a_final_report_records_a_shadow_row_for_the_machine_and_one_per_drawer(conn):
    A.note_final(conn, MAC, "ses_1", MACHINE, [{"dev": "sda", "senses": {**TARGET, "resets": {"boot": 4, "delta": 2}}}, {"dev": "sdb"}])
    got = rows(conn, round_id="ses_1")
    by_point = {(r["rule"], r["point"]): r for r in got}
    m = by_point[(A.RULE, f"{MAC}/machine")]
    assert (m["mode"], m["verdict"], m["entity_kind"], m["entity_key"]) == ("shadow", A.OBSERVED, "machine", MAC)
    a, b = by_point[(A.RULE, f"{MAC}/sda/?")], by_point[(A.RULE, f"{MAC}/sdb/?")]
    assert a["verdict"] == A.LINK_TROUBLE and b["verdict"] == A.NOT_READ, "a drawer that said nothing is NOT_READ"
    assert "image_id=img_aaaaaa" in a["evidence"] and "resets=boot=4,delta=2" in a["evidence"]
    assert a["outcome_state"] == "closed" and A.SHADOW_HE in a["outcome"], "shadow: nothing was done"


def test_the_switch_off_records_nothing(conn):
    db.set_setting(conn, "brain_veto_mode", "off")
    A.note_final(conn, MAC, "ses_1", MACHINE, [{"dev": "sda", "senses": TARGET}])
    A.note_hello(conn, MAC, {"sata_mode": "ide"})
    assert rows(conn) == []


def test_a_report_that_breaks_the_record_does_not_break_the_caller(conn, monkeypatch):
    monkeypatch.setattr(memory, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    A.note_final(conn, MAC, "ses_1", MACHINE, [{"dev": "sda"}])      # no raise
    A.note_hello(conn, MAC, {"sata_mode": "ide"})                    # no raise


# --- S64 / B31: the image's identity ------------------------------------------------------------------------------------


def _ledger(conn, image_id, serial="S1", round_id="ses_1"):
    L.record(conn, round_id=round_id, mac=MAC, serial=serial, size_bytes=SIZE, image_id=image_id, manifest=MANIFEST,
             durable=[{"part": 3, "ranges": [[0, 9]]}])


def test_the_ledger_of_another_image_does_not_survive_the_disk_being_written_again(conn):
    _ledger(conn, "img_old")
    assert L.of_disk(conn, "S1", SIZE)[3]["image_id"] == "img_old"
    _ledger(conn, "img_aaaaaa", round_id="ses_2")
    got = L.of_disk(conn, "S1", SIZE)
    assert {p["image_id"] for p in got.values()} == {"img_aaaaaa"}, "a chunk of the old image is not a chunk of this one"
    assert A._mixed_image(conn, "S1", "img_aaaaaa") == []


def test_a_ledger_of_this_rounds_image_only_is_same_one_with_a_leftover_is_mixed_and_none_is_no_ledger(conn, monkeypatch):
    monkeypatch.setattr("server.disks.smart_history.serial_of", lambda c, mac, dev: {"sda": "S1", "sdb": "S2", "sdc": "S3"}[dev])
    _ledger(conn, "img_aaaaaa", serial="S1")
    _ledger(conn, "img_aaaaaa", serial="S2")
    conn.execute("UPDATE chunk_ledger SET image_id = 'img_old' WHERE serial = 'S2'")     # a leftover the guard should never leave
    conn.commit()
    A.note_final(conn, MAC, "ses_1", MACHINE, [{"dev": "sda"}, {"dev": "sdb"}, {"dev": "sdc"}])
    got = {r["point"].split("/")[1]: r for r in rows(conn, rule=A.IDENT_RULE)}
    assert [got[d]["verdict"] for d in ("sda", "sdb", "sdc")] == [A.SAME, A.MIXED, A.NO_LEDGER]
    assert "img_old" in got["sdb"]["reason"] and got["sdb"]["entity_kind"] == "disk"


# --- B23 / R16 ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode, verdict", [("ahci", A.AHCI_OK), ("ide", A.WOULD_BLOCK_BAND), ("raid", A.WOULD_BLOCK_BAND), (None, A.NOT_READ), ("x", A.NOT_READ)])
def test_without_ahci_the_band_would_be_blocked_and_an_unread_mode_is_neither(mode, verdict):
    assert A.ahci_gate(mode, "ata_piix")[0] == verdict


def test_the_orange_warning_names_the_mode_and_the_driver_and_exists_only_when_it_is_not_ahci():
    w = A.controller_warning("ide", "ata_piix")
    assert w["severity"] == "warn" and "IDE" in w["text"] and "ata_piix" in w["text"] and "AHCI" in w["text"] and "צל" not in w["text"]
    assert A.controller_warning("ahci", "ahci") is None and A.controller_warning(None) is None


def test_the_gate_is_recorded_in_shadow_once_per_change_and_an_old_agent_leaves_it_be(conn):
    A.note_hello(conn, MAC, {"sata_mode": "ide", "sata_driver": "ata_piix"})
    A.note_hello(conn, MAC, {"sata_mode": "ide", "sata_driver": "ata_piix"})             # the same: not again
    A.note_hello(conn, MAC, {"disks": []})                                               # an older agent: nothing
    one = rows(conn, rule=A.GATE_RULE)
    assert [(r["mode"], r["verdict"]) for r in one] == [("shadow", A.WOULD_BLOCK_BAND)]
    assert one[0]["evidence"] == ["mode=ide", "driver=ata_piix"] and A.SHADOW_HE in one[0]["outcome"]
    A.note_hello(conn, MAC, {"sata_mode": "ahci", "sata_driver": "ahci"})
    assert [r["verdict"] for r in rows(conn, rule=A.GATE_RULE)] == [A.AHCI_OK, A.WOULD_BLOCK_BAND]


def test_the_firmware_image_carries_the_driver_of_the_mode_it_was_reported_with(conn):
    cur = firmware_image.build(None, {"sata_mode": "ide", "sata_driver": "ata_piix"})
    assert (cur["sata_mode"], cur["sata_driver"]) == ("ide", "ata_piix")
    assert firmware_image.build(cur, {"sata_mode": "ahci"})["sata_driver"] is None, "the old mode's driver is not the new mode's"
    assert firmware_image.build(cur, {})["sata_driver"] == "ata_piix", "a hello without the mode keeps the image"
    assert firmware_image.build(None, {"sata_mode": "ide", "sata_driver": "a b"})["sata_driver"] is None
    firmware_image.record(conn, MAC, {"sata_mode": "ide", "sata_driver": "ata_piix"})
    view = firmware_image.view(conn, MAC)
    assert view["controller"]["mode"] == "ide" and view["controller"]["driver"] == "ata_piix"
    firmware_image.record(conn, MAC, {"sata_mode": "ahci", "sata_driver": "ahci"})
    assert firmware_image.view(conn, MAC)["controller"] is None
