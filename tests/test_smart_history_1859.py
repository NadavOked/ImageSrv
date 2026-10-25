"""#1859 -- the server side of the full SMART: the history per serial, what it learns,
and the console's view in Hebrew (server/smart_history.py, server/smart_names.py).

Nadav, 20/10 00:20: the server keeps every attribute per serial each round; a rise
since the previous round in a critical attribute adds "replace the disk"; 183/188
that rose is orange; hot while writing is orange; with no history -- "no comparison
yet", never "stable". A cumulative counter that did not rise is information only (#872).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db, disk_events, housekeeping, smart_history, smart_names

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom  # noqa: E402


MAC_A = "b4:2e:99:07:1a:c4"
MAC_B = "b4:2e:99:07:1a:c5"


def reported(conn, mac: str, disks: dict[str, str]) -> None:
    """The last hello of `mac` reported `disks` (dev -> serial) -- what record() checks."""
    conn.execute("INSERT OR REPLACE INTO net_devices (mac, disks_json) VALUES (?, ?)",
                 (mac, json.dumps([{"dev": d, "serial": s} for d, s in disks.items()])))
    conn.commit()


@pytest.fixture()
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "t.db")
    reported(c, MAC_A, {"sda": "S1"})
    reported(c, MAC_B, {"sda": "SB"})
    return c


def attrs(_names: dict | None = None, **raw) -> dict:
    """An 860 EVO's table as smartattrs.sh sends it; raw values by id (r5=, r183=...)."""
    _names = _names or {}
    rows = {5: 0, 9: 12871, 177: 5, 183: 0, 187: 0, 188: 0, 199: 0, 235: 87}
    rows.update({int(k[1:]): v for k, v in raw.items()})
    names = {5: "Reallocated_Sector_Ct", 9: "Power_On_Hours", 177: "Wear_Leveling_Count",
             183: "SATA_Downshift_Count", 187: "Uncorrectable_Error_Cnt", 188: "Command_Timeout",
             199: "CRC_Error_Count", 235: "Not_A_Name_In_Any_Table"}   # #1921: 235 unknown by name
    names.update(_names)
    return {"ata": [[i, 100, 100, 10 if i == 5 else 0, v, names[i]] for i, v in sorted(rows.items())],
            "nvme": None}


def event(session: str, a: dict, serial: str = "S1", mac: str = MAC_A) -> dict:
    return {"session_id": session, "mac": mac, "disk": "sda", "port": 1,
            "serial": serial, "write_state": "pending", "decision": None,
            "smart": {"verdict": "ok", "reason": "ok", "realloc": 0, "pending": 0,
                      "uncorrectable": 0, "crc": 0, "attrs": a}}


# --- the history ----------------------------------------------------------------------


def test_every_round_keeps_every_attribute_per_serial(conn):
    assert disk_events.ingest(conn, event("ses1", attrs()))["ok"]
    assert disk_events.ingest(conn, event("ses2", attrs(r5=2)))["ok"]
    rows = conn.execute("SELECT session_id FROM smart_history WHERE serial = 'S1' ORDER BY at").fetchall()
    assert [r["session_id"] for r in rows] == ["ses1", "ses2"]


def test_another_machine_cannot_write_this_disks_history(conn):
    """Security review (#1859): MAC B sends attributes under the serial of the disk in MAC A.
    A's history does not change -- and the rise A's disk really had is not hidden."""
    assert disk_events.ingest(conn, event("ses1", attrs()))["ok"]
    assert disk_events.ingest(conn, event("ses2", attrs(r5=9), serial="S1", mac=MAC_B))["ok"]   # B's sda is SB
    rows = conn.execute("SELECT session_id, mac FROM smart_history WHERE serial = 'S1'").fetchall()
    assert [(r["session_id"], r["mac"]) for r in rows] == [("ses1", MAC_A)]
    assert smart_history.learned_for(conn, [{"serial": "S1", "smart_attrs": attrs(r5=9)}]) == \
        [{"serial": "S1", "reason": "critical_rose"}], "B's forged snapshot would have hidden A's rise"


def test_a_machine_cannot_overwrite_another_machines_row_of_the_round(conn):
    """Even a serial both hellos reported (a lying hello): one row per machine, never overwritten."""
    reported(conn, MAC_B, {"sda": "S1"})
    disk_events.ingest(conn, event("ses1", attrs(r5=3)))
    disk_events.ingest(conn, event("ses1", attrs(r5=0), mac=MAC_B))
    raw = {r["mac"]: json.loads(r["attrs_json"])["ata"][0][4]
           for r in conn.execute("SELECT mac, attrs_json FROM smart_history WHERE serial = 'S1'")}
    assert raw == {MAC_A: 3, MAC_B: 0}


def test_a_disk_the_machine_did_not_report_on_that_dev_is_not_recorded(conn):
    assert disk_events.ingest(conn, {**event("ses1", attrs()), "disk": "sdb"})["ok"]   # A reported only sda
    assert conn.execute("SELECT COUNT(*) FROM smart_history").fetchone()[0] == 0


def test_no_serial_or_no_attributes_is_no_history(conn):
    assert disk_events.ingest(conn, event("ses1", attrs(), serial=None))["ok"]
    assert disk_events.ingest(conn, event("ses1", None, serial="S2"))["ok"]
    assert conn.execute("SELECT COUNT(*) FROM smart_history").fetchone()[0] == 0


# --- what it learns ---------------------------------------------------------------------


@pytest.mark.parametrize("now,word", [
    (attrs(r5=2), "critical_rose"),
    (attrs(r187=1), "critical_rose"),
    (attrs(r183=3), "downshift_rose"),
    (attrs(r188=1), "timeout_rose"),
])
def test_a_rise_since_the_previous_round_is_learned(conn, now, word):
    disk_events.ingest(conn, event("ses1", attrs()))
    assert smart_history.learned_for(conn, [{"serial": "S1", "smart_attrs": now}]) == [{"serial": "S1", "reason": word}]


def test_a_counter_that_did_not_rise_is_information_only(conn):
    """#872: the cumulative number says nothing on its own -- 199 at 55,242 and steady."""
    disk_events.ingest(conn, event("ses1", attrs(r5=4, r199=55242)))
    assert smart_history.learned_for(conn, [{"serial": "S1", "smart_attrs": attrs(r5=4, r199=55242)}]) == []


def test_a_rise_is_judged_by_id_and_name_not_by_the_id_alone(conn):
    """#1921/#1922: 183 on a Samsung SSD is Runtime_Bad_Block -- bad blocks, critical (ס3);
    198 on an OCZ Intrepid is Host_Reads_GiB -- it rises every round and is nothing."""
    samsung = {183: "Runtime_Bad_Block"}
    disk_events.ingest(conn, event("ses1", attrs(samsung)))
    assert smart_history.learned_for(conn, [{"serial": "S1", "smart_attrs": attrs(samsung, r183=2)}]) == \
        [{"serial": "S1", "reason": "critical_rose"}]
    ocz = {198: "Host_Reads_GiB"}
    disk_events.ingest(conn, event("ses2", attrs(ocz, r198=48000), serial="SB", mac=MAC_B))
    assert smart_history.learned_for(conn, [{"serial": "SB", "smart_attrs": attrs(ocz, r198=48211)}]) == []


def test_a_cable_rise_is_not_replace_the_disk(conn):
    disk_events.ingest(conn, event("ses1", attrs()))
    assert smart_history.learned_for(conn, [{"serial": "S1", "smart_attrs": attrs(r199=40)}]) == []


def test_without_a_previous_round_nothing_is_learned_and_the_view_says_so(conn):
    assert smart_history.learned_for(conn, [{"serial": "S1", "smart_attrs": attrs(r5=9)}]) == []
    view = smart_history.describe(conn, {"serial": "S1", "dev": "sda", "smart_attrs": attrs(r5=9)})
    assert view["compared"] is False and view["learned"] is None
    assert all(a["rose_by"] is None for a in view["attributes"])
    assert smart_history.NO_HISTORY_HE == "אין עדיין השוואה"


def test_hot_while_writing_last_round_is_learned(conn):
    disk_events.ingest(conn, event("ses1", attrs()))
    assert smart_history.record_temp(conn, "S1", "ses1", smart_history.TEMP_WARN_C + 4, MAC_A)
    assert smart_history.learned_for(conn, [{"serial": "S1", "smart_attrs": attrs()}]) == [{"serial": "S1", "reason": "hot"}]


def test_the_serial_of_a_reported_dev_is_found_for_the_temperature(server):
    """reports.py hands record_temp the serial of the target's dev, from the last hello."""
    from server import registry
    ids = setup_classroom(server)
    conn = server["ctx"].conn
    body = hello_body(ids["mac1"]); body["disks"][0]["smart_attrs"] = attrs()
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    mac = registry.normalize_mac(ids["mac1"])
    disk_events.ingest(conn, {**event("ses9", attrs()), "mac": mac})
    assert smart_history.serial_of(conn, mac, "sda") == "S1"
    assert smart_history.serial_of(conn, mac, "sdz") is None
    assert not smart_history.record_temp(conn, "S1", "ses9", 63, "aa:aa:aa:aa:aa:aa"), "another machine's row"
    assert smart_history.record_temp(conn, "S1", "ses9", 63, mac)
    row = conn.execute("SELECT temp_after FROM smart_history WHERE serial = 'S1' AND session_id = 'ses9'").fetchone()
    assert row["temp_after"] == 63
    assert not smart_history.record_temp(conn, "S1", "ses9", 0, mac), "0 is not a temperature (principle 5)"


# --- the console's view: Hebrew names, critical, unknown --------------------------------


def test_every_attribute_is_named_in_hebrew_and_an_unknown_one_by_its_vendor_name(conn):
    disk_events.ingest(conn, event("ses1", attrs()))
    view = smart_history.describe(conn, {"serial": "S1", "dev": "sda", "smart_attrs": attrs(r5=2)})
    by = {a["id"]: a for a in view["attributes"]}
    assert by[5]["name_he"] == "סקטורים שהועברו" and by[5]["critical"] and by[5]["rose_by"] == 2
    assert by[177]["known"] and by[177]["vendor"] == "Wear_Leveling_Count"
    assert by[235]["known"] is False and by[235]["name_he"] == "Not_A_Name_In_Any_Table"
    assert by[235]["note_he"] == smart_names.UNKNOWN_HE == "לא מוכר"
    assert view["compared"] is True and view["learned"] == "critical_rose"


def test_nadavs_critical_list_is_marked_critical():
    assert smart_names.CRITICAL == {5, 197, 198, 187, 10, 196, 199}


def test_the_names_cover_nadavs_table():
    """Every id in Nadav's table (20/10), the ranges expanded, has a Hebrew name -- now per
    (id, name) in the knowledge tables (#1921): at least one name of each id."""
    table = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 22, *range(170, 178), 178, 179, 180, 181, 182, 183, 184,
             *range(187, 202), *range(202, 210), *range(220, 229), 230, 231, 240, 241, 242, 250]
    ids = {i for i, _ in smart_names.ATA}
    missing = [i for i in table if i not in ids]
    assert not missing, missing
    for r in smart_names.ATA.values():
        assert r["name_he"] and r["explain_he"], r


def test_the_learned_words_say_what_smart_sh_and_the_console_say():
    sh = (Path(__file__).resolve().parent.parent / "agent" / "lib" / "smart.sh").read_text(encoding="utf-8")
    for word, he in smart_history.LEARNED_HE.items():
        assert f"{word}) printf '{he}'" in sh, word


def test_the_endpoint_returns_every_disk_of_the_machine(server):
    ids = setup_classroom(server)
    body = hello_body(ids["mac1"]); body["disks"][0]["smart_attrs"] = attrs(r5=1)
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    got = server["admin"].get(f"/api/console/machines/{ids['mac1']}/smart").json()
    (disk,) = got["disks"]
    assert disk["serial"] == "S1" and disk["read"] and disk["compared"] is False
    assert got["no_history_he"] == "אין עדיין השוואה"


def test_the_hello_answer_carries_what_was_learned(server):
    from server import registry
    ids = setup_classroom(server)
    first = hello_body(ids["mac1"]); first["disks"][0]["smart_attrs"] = attrs()   # round 1: the hello, then its disk_event
    assert server["anon"].post("/api/v1/agent/hello", json=first).json()["smart_learned"] == []
    disk_events.ingest(server["ctx"].conn, {**event("ses1", attrs()), "mac": registry.normalize_mac(ids["mac1"])})
    body = hello_body(ids["mac1"]); body["disks"][0]["smart_attrs"] = attrs(r183=2)
    answer = server["anon"].post("/api/v1/agent/hello", json=body).json()
    assert answer["smart_learned"] == [{"serial": "S1", "reason": "downshift_rose"}]


def test_old_rounds_are_purged_but_the_last_one_of_each_serial_stays(conn):
    for ses, at in (("a", "2024-01-01T00:00:00+00:00"), ("b", "2024-01-02T00:00:00+00:00")):
        disk_events.ingest(conn, event(ses, attrs()))
        conn.execute("UPDATE smart_history SET at = ? WHERE session_id = ?", (at, ses)); conn.commit()
    housekeeping.purge_tables(conn)
    assert [r[0] for r in conn.execute("SELECT session_id FROM smart_history")] == ["b"]
