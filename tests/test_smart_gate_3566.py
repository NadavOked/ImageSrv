"""#3566: שער SMART לפי 5/187/197/198 -- כל מדד עם 0, מעל 0 ועלייה; מדד שחסר = אפור ("לא ידוע"); וב**צל**: הצבע המוצג אינו משתנה."""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import memory

IDS = (5, 187, 197, 198)
NAMES = {5: "Reallocated_Sector_Ct", 187: "Reported_Uncorrect", 197: "Current_Pending_Sector", 198: "Offline_Uncorrectable"}


def gate():
    from server.brain import smartgate      # noqa: PLC0415 -- not at the top: the file collects with the fix reverted
    return smartgate


def attrs(**raw):
    """`smart_attrs` של hello: כל ארבעת המדדים 0 (בלי שם יצרן, כמו ה-hello), חוץ ממה שנמסר; `None` = לא הודפס."""
    rows = [[i, 100, 100, 0, raw.get(f"a{i}", 0)] for i in IDS if raw.get(f"a{i}", 0) is not None]
    return {"ata": rows + [[9, 90, 90, 0, 1234]], "nvme": None}


@pytest.mark.parametrize("i", IDS)
def test_each_metric_at_zero_is_green_when_all_four_are_read(i):
    got = gate().judge(attrs())
    assert got["color"] == "green" and got["metrics"][i] == "zero"


@pytest.mark.parametrize("i", IDS)
def test_each_metric_above_zero_is_orange(i):
    got = gate().judge(attrs(**{f"a{i}": 3}))
    assert got["color"] == "orange" and got["metrics"][i] == "positive"


@pytest.mark.parametrize("i", IDS)
def test_each_metric_that_rose_since_the_previous_round_is_orange(i):
    got = gate().judge(attrs(**{f"a{i}": 7}), attrs(**{f"a{i}": 4}))
    assert got["color"] == "orange" and got["metrics"][i] == "rose"


@pytest.mark.parametrize("i", IDS)
def test_a_metric_that_did_not_rise_stays_what_it_is(i):
    got = gate().judge(attrs(**{f"a{i}": 4}), attrs(**{f"a{i}": 4}))
    assert got["metrics"][i] == "positive"           # above zero, but not "rose"
    assert gate().judge(attrs(), attrs())["color"] == "green"


@pytest.mark.parametrize("i", IDS)
def test_a_missing_metric_is_grey_never_green(i):
    got = gate().judge(attrs(**{f"a{i}": None}))
    assert got["color"] == "grey" and got["metrics"][i] == "missing"


def test_an_orange_sign_beats_a_missing_metric():
    assert gate().judge(attrs(a5=1, a198=None))["color"] == "orange"


def test_a_samsung_860_that_prints_only_5_and_187_is_not_known():
    got = gate().judge({"ata": [[5, 100, 100, 10, 0], [187, 100, 100, 0, 0]], "nvme": None})
    assert got["color"] == "grey" and got["metrics"] == {5: "zero", 187: "zero", 197: "missing", 198: "missing"}


def test_nvme_has_no_mapping_to_these_metrics_so_all_four_are_missing():
    assert gate().judge({"ata": [], "nvme": {"media_errors": 0}})["color"] == "grey"


def test_the_vendors_name_must_be_that_metric_198_of_an_ocz_is_bytes_read():
    a = attrs(a198=None)
    a["ata"].append([198, 100, 100, 0, 999, "Host_Reads_GiB"])
    assert gate().judge(a)["metrics"][198] == "missing"
    a["ata"][-1][5] = NAMES[198]
    assert gate().judge(a)["metrics"][198] == "positive"


def test_a_disk_that_was_not_read_here_is_not_judged():
    assert gate().judge(None) is None and gate().judge("junk") is None


# --- shadow: the colour on screen does not move -----------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "brain.db")
    yield c
    c.close()


def gate_rows(conn, serial="S1"):
    return [r for r in memory.decisions(conn, kind="disk", key=serial, rule="K3") if r["action"] == "SMART_GATE"]


def decide(conn, **disk):
    from server.brain import diskcolor      # noqa: PLC0415
    return diskcolor.decide(conn, {"serial": "S1", "smart": "ok", **disk}, [])


def test_a_green_disk_with_a_missing_metric_stays_green_and_the_gate_is_recorded_in_shadow(conn):
    got = decide(conn, smart_attrs=attrs(a198=None))
    assert got["color"] == "green"
    (row,) = gate_rows(conn)
    assert (row["verdict"], row["mode"]) == ("GATE_NOT_KNOWN", "shadow")
    assert row["evidence"][0]["metrics"]["198"] == "missing" and row["evidence"][0]["displayed"] == "green"


def test_a_green_disk_whose_counter_rose_stays_green_and_is_recorded_as_orange_in_shadow(conn):
    conn.execute("INSERT INTO smart_history (serial, session_id, mac, at, attrs_json) VALUES ('S1','r1','m','2026-11-01T00:00:00Z',?)",
                 (json.dumps(attrs(a187=1)),))
    conn.commit()
    assert decide(conn, smart_attrs=attrs(a187=5))["color"] == "green"
    (row,) = gate_rows(conn)
    assert row["verdict"] == "GATE_ORANGE" and row["evidence"][0]["metrics"]["187"] == "rose"


def test_it_is_recorded_once_while_nothing_changes(conn):
    for _ in range(3):
        decide(conn, smart_attrs=attrs(a197=None))
    assert len(gate_rows(conn)) == 1


def test_a_fully_read_clean_disk_records_no_gate_row(conn):
    decide(conn, smart_attrs=attrs())
    assert gate_rows(conn) == []


def test_a_disk_without_smart_attrs_in_the_record_is_not_judged(conn):
    decide(conn)                                    # the drawer did not carry them: not read here is not "missing"
    assert gate_rows(conn) == []


def test_a_disk_the_table_already_colours_is_left_alone(conn):
    got = decide(conn, smart="warn", smart_reason="pending_sectors", smart_attrs=attrs(a198=None))
    assert got["color"] == "orange" and gate_rows(conn) == []
