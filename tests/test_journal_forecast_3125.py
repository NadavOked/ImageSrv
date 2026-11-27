"""#3125 (Refs #3321): היומן והתחזיות של המוח, כשדות -- לא כטקסט שאי אפשר לחשב ממנו.

מה נבדק (הפריטים בקטלוג `server/brain/catalog.toml`):

- R3: ``brain_decisions`` נושא ``predicted``/``actual``/``unit``/``matched`` לצד ``outcome`` החופשי. ``matched`` הוא
  שלושה מצבים: 1 התאים, 0 לא התאים, NULL **לא נמדד** -- גל שנכשל, זמן התחלה שלא נקרא או החלטה שנסגרה כיתומה אינם
  "לא התאימו". הטקסט נשאר כמו שהוא (מי שקורא אותו לא נשבר).
- R7, U2: ``chunk_pass_rates`` -- ה-`--write-kbps` של כל מעבר ולפי איזה דיסק נקבע; דיסק שלא נמדד הוא `floor`, לא מספר.
- R6, U1, U8: ``chunk_pass_counters`` -- מוני המעבר של כל מחשב; מה שהסוכן לא אמר הוא NULL ולא אפס.
- U3, R10: מי לא התייצב, ואחרי כמה זמן ויתרו עליו -- בשורה ביומן ובהודעת הכשל.
- R15: ``port_history.failed`` ו-`slot_failed`/`slot_seen`.

בקרה שלילית: קובצי הייצור שהשתנו מוחזרים לבסיס; ``chunkpass.py`` ו-`forecast.py` הם קבצים חדשים והם נשארים -- בלי
החיווט שלהם (`chunksend`, `linkwarn`, `memory`, `ports`, `db`) אף שורה לא נכתבת, והטסטים נכשלים על ערכים.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import forecast, linkwarn, memory, writerate
from server.learn import ports as learn_ports
from server.rounds import chunkpass, chunksend

from test_chunksend_2533 import ALL, SID, rig, wait_for     # noqa: F401 -- rig הוא fixture

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _rows(conn, table: str) -> list[dict]:
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


# --- R3: תחזית מול מה שקרה ----------------------------------------------------------------------------------------

def test_judge_is_three_states_and_a_rule_without_a_definition_is_not_measured():
    assert forecast.judge("K3", 600, 700) is True            # inside +-25%
    assert forecast.judge("K3", 600, 1200) is False
    assert forecast.judge("K3", 600, 300) is False
    assert forecast.judge("K3", None, 700) is None           # no forecast
    assert forecast.judge("K3", 600, None) is None           # not measured is not a miss
    assert forecast.judge("K3", 600, 0) is None              # zero seconds is a broken read, not a fast wave
    assert forecast.judge("K3", True, 5) is None             # a bool is not a number
    assert forecast.judge("K2", 600, 600) is None            # a destructive rule is not judged by a rate (#3321)


def _decision(conn, eta=600):
    memory.record(conn, mode="shadow", rule="K3", action="LINK_SLOW", verdict="WARN", reason="r",
                  evidence=(f"eta_s={eta}",), round_id="ses_1", entity=("network", "aa"),
                  predicted=eta, unit="s")


def _finish(conn, seconds, *, started=True, session_state=None, wave="ses_1"):
    if session_state is not None:
        conn.execute("INSERT OR IGNORE INTO groups (id, label, role) VALUES ('g', 'g', 'cloner')")
        conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                     " opened_by, created_at, last_join_at) VALUES (?, 'g', 'i', 'ROOM', 1, 1, ?, 'x', ?, 0)",
                     (wave, session_state, db.now_iso()))
    now = datetime.now(timezone.utc)
    linkwarn.finished(conn, wave, (now - timedelta(seconds=seconds)).isoformat() if started else None, now=now)
    [d] = memory.decisions(conn, round_id=wave, rule="K3")
    return d


def test_the_forecast_and_the_actual_are_numbers_beside_the_free_text(conn):
    _decision(conn, 600)
    [open_] = memory.decisions(conn, rule="K3")
    assert (open_["predicted"], open_["unit"], open_["actual"], open_["matched"]) == (600, "s", None, None)
    d = _finish(conn, 640)
    assert (d["predicted"], d["actual"], d["unit"], d["matched"]) == (600, 640, "s", 1)
    assert "ETA חזוי" in d["outcome"] and "בפועל" in d["outcome"]        # the text is as it was


def test_a_wave_far_from_its_forecast_did_not_match(conn):
    _decision(conn, 600)
    d = _finish(conn, 1800)
    assert (d["actual"], d["matched"]) == (1800, 0)


def test_an_unread_start_time_is_not_measured_not_a_miss(conn):
    _decision(conn, 600)
    d = _finish(conn, 0, started=False)
    assert d["actual"] is None and d["matched"] is None and "לא נמדד" in d["outcome"]


def test_a_wave_that_failed_is_not_measured_not_a_miss(conn):
    _decision(conn, 600)
    d = _finish(conn, 60, session_state="failed")
    assert d["actual"] is None and d["matched"] is None, d
    assert "הגל נכשל" in d["outcome"]


def test_an_unknown_image_size_leaves_the_forecast_null_and_the_match_not_measured(conn):
    memory.record(conn, mode="shadow", rule="K3", action="LINK_SLOW", verdict="WARN", reason="r",
                  evidence=("eta_s=None",), round_id="ses_1", entity=("network", "aa"), predicted=None, unit="s")
    d = _finish(conn, 500)
    assert d["predicted"] is None and d["actual"] == 500 and d["matched"] is None


def test_a_decision_closed_as_an_orphan_has_no_actual_and_no_match(conn):
    _decision(conn, 600)
    memory.resolve(conn, [d["id"] for d in memory.decisions(conn, rule="K3")], memory.ORPHAN_HE)
    [d] = memory.decisions(conn, rule="K3")
    assert d["outcome_state"] == "unknown" and d["predicted"] == 600 and d["actual"] is None and d["matched"] is None


def test_a_decision_that_was_closed_is_not_overwritten(conn):
    _decision(conn, 600)
    _finish(conn, 640)
    assert memory.resolve(conn, [d["id"] for d in memory.decisions(conn, rule="K3")], "later", actual=1, matched=False) == 0
    [d] = memory.decisions(conn, rule="K3")
    assert (d["actual"], d["matched"]) == (640, 1)


def test_the_match_rate_can_be_computed_from_the_table_alone(conn):
    """#3321: "כמה החלטות נבדקו, באיזה שיעור התאמה" -- מ-SQL, בלי לקרוא טקסט עברי; `לא נמדד` אינו במונה ואינו במכנה."""
    for index, took in enumerate([620, 2000, None, 590]):
        memory.record(conn, mode="shadow", rule="K3", action="LINK_SLOW", verdict="WARN", reason="r",
                      round_id=f"ses_{index}", entity=("network", "aa"), predicted=600, unit="s")
        _finish(conn, took or 0, started=took is not None, wave=f"ses_{index}")
    measured, matched = conn.execute("SELECT COUNT(matched), COALESCE(SUM(matched), 0) FROM brain_decisions"
                                     " WHERE rule = 'K3'").fetchone()
    assert (measured, matched) == (3, 2)        # three measured, two matched; the unread one is in neither count
    assert conn.execute("SELECT COUNT(*) FROM brain_decisions WHERE matched IS NULL").fetchone()[0] == 1


# --- R7, U2: הקצב שהדיסקים נדרשים לכתוב, ולפי מי --------------------------------------------------------------------

def test_write_basis_names_the_slowest_measured_disk():
    got = chunkpass.write_basis({("a", "A1"): 140, ("b", "B1"): 120, ("c", "C1"): 200})
    assert got == {"basis": "probe", "probe_mbps": 120, "slow_mac": "b", "slow_serial": "B1"}


def test_write_basis_says_an_unmeasured_disk_is_not_a_number():
    got = chunkpass.write_basis({("a", "A1"): 140, ("b", "B1"): None})
    assert got["basis"] == "floor" and (got["slow_mac"], got["slow_serial"]) == ("b", "B1") and got["probe_mbps"] == 140
    none = chunkpass.write_basis({})
    assert none == {"basis": "unmeasured", "probe_mbps": None, "slow_mac": None, "slow_serial": None}


def test_each_pass_leaves_its_rates_and_who_set_them(rig, conn):
    engine = rig.make()
    engine.passlog.conn = conn
    rig.tell("a", 1, "listening", {"A1": []}, mbps=140)
    rig.tell("b", 1, "listening", {"B1": []}, mbps=120)
    rig.tell("c", 1, "listening", {"C1": []}, mbps=200)
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert wait_for(lambda: _rows(conn, "chunk_pass_rates"))
    [row] = _rows(conn, "chunk_pass_rates")
    assert (row["session_id"], row["part"], row["pass"]) == (SID, 3, 1)
    assert row["kbps"] == 700000 and row["write_kbps"] == int(120 * writerate.MARGIN) * 8000
    assert (row["basis"], row["probe_mbps"], row["slow_mac"], row["slow_serial"], row["floor_mbps"]) == ("probe", 120, "b", "B1", 50)
    [said] = rig.named("chunk_write_rate")
    assert "B1 של b" in said and "120MB/s" in said, said


def test_an_unmeasured_disk_is_written_as_the_floor_and_said_so(rig, conn):
    engine = rig.make()
    engine.passlog.conn = conn
    rig.tell("a", 1, "listening", {"A1": []}, mbps=140)
    rig.tell("b", 1, "listening", {"B1": []}, mbps=None)
    rig.tell("c", 1, "listening", {"C1": []}, mbps=200)
    assert wait_for(lambda: _rows(conn, "chunk_pass_rates"))
    [row] = _rows(conn, "chunk_pass_rates")
    assert row["basis"] == "floor" and row["write_kbps"] == 50 * 8000 and (row["slow_mac"], row["slow_serial"]) == ("b", "B1")
    assert row["probe_mbps"] == 140                           # the slowest that WAS measured; not the unmeasured one's
    [said] = rig.named("chunk_write_rate")
    assert "לא נמדד" in said and "B1" in said, said


# --- R6, U1, U8: מוני המעבר של כל מחשב -----------------------------------------------------------------------------

def test_a_drive_that_fell_behind_is_a_field_and_one_journal_line(rig, conn):
    engine = rig.make()
    engine.passlog.conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0}, stray=0)
    rig.tell("c", 1, "ended", {"C1": ALL})
    rig.ask("b", 1, {"B1": [[0, 4]], "B2": ALL}, skipped={"B1": 3, "B2": 0})
    by_mac = {r["mac"]: r for r in _rows(conn, "chunk_pass_counters") if r["pass"] == 1}
    assert by_mac["b"]["skipped"] == 3 and json.loads(by_mac["b"]["skipped_json"]) == {"B1": 3, "B2": 0}
    assert by_mac["a"]["skipped"] == 0 and by_mac["a"]["stray"] == 0                   # said 0: a measurement
    assert by_mac["c"]["skipped"] is None and by_mac["c"]["stray"] is None             # not said: NOT zero
    assert by_mac["c"]["skipped_json"] is None
    behind = rig.named("chunk_drive_behind")
    assert len(behind) == 1 and behind[0].startswith("b B1: 3 חתיכות") and "מחיצה 3" in behind[0], behind
    rig.tell("b", 1, "ended", {"B1": [[0, 4]], "B2": ALL}, skipped={"B1": 3, "B2": 0})      # `ended` repeats until answered
    assert len(rig.named("chunk_drive_behind")) == 1


def test_a_fetch_pass_leaves_its_rate_from_the_first_ended(rig, conn):
    engine = rig.make()
    engine.passlog.conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": ALL})
    assert rig.ask("c", 1, {"C1": [[0, 8]]})["action"] == "fetch"
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    row = next(r for r in _rows(conn, "chunk_pass_counters") if r["mac"] == "c" and r["pass"] == 2)
    assert row["kind"] == "fetch" and row["fetch_s"] > 0 and row["fetch_mbps"] > 0, row
    [said] = rig.named("chunk_fetch_rate")
    assert said.startswith("c: ההורדה ב-HTTP") and "MB/s" in said, said
    first = (row["fetch_s"], row["fetch_mbps"])
    rig.tell("c", 2, "ended", {"C1": ALL})                       # a later `ended` does not slow the number it measured
    again = next(r for r in _rows(conn, "chunk_pass_counters") if r["mac"] == "c" and r["pass"] == 2)
    assert (again["fetch_s"], again["fetch_mbps"]) == first


def test_the_fetch_rate_is_chunks_over_seconds_and_unknown_when_nothing_moved(conn):
    said = []
    log = chunkpass.PassLog(lambda *a: said.append(a), 4.0)
    log.conn = conn
    log.reported("S", 3, 2, "c", "fetch", {"C1": None}, None, None, None, now=110.0, held=19, began=(100.0, 9))
    [row] = _rows(conn, "chunk_pass_counters")
    assert row["fetch_s"] == 10.0 and row["fetch_mbps"] == pytest.approx(4.0)       # 10 chunks x 4MB / 10 s
    log.reported("S", 3, 3, "c", "fetch", {"C1": None}, None, None, None, now=110.0, held=9, began=(100.0, 9))
    row = next(r for r in _rows(conn, "chunk_pass_counters") if r["pass"] == 3)
    assert row["fetch_s"] == 10.0 and row["fetch_mbps"] is None                     # nothing durable moved: not 0MB/s
    assert any("לא נמדד" in detail for _event, detail, _sid in said)


# --- U3, R10: מי לא התייצב ----------------------------------------------------------------------------------------

def test_a_machine_that_never_showed_is_named_when_the_rest_go_on(rig):
    engine = rig.make()
    rig.tell("a", 1, "listening", {"A1": []})
    rig.tell("b", 1, "listening", {"B1": []})
    assert wait_for(lambda: rig.named("chunk_no_show"), 8)
    [said] = rig.named("chunk_no_show")
    assert "(c)" in said and "a" not in said.split("(")[1].split(")")[0] and "שניות" in said, said
    assert engine.status()["state"] != "failed"


def test_the_failure_of_a_wave_nobody_joined_names_who_was_waited_for(rig):
    engine = rig.make()
    assert wait_for(lambda: rig.named("send_failed"), 8) and engine.status()["state"] == "failed"
    [why] = rig.named("send_failed")
    assert "אף מחשב לא התייצב" in why and "תוך 1 שניות (a, b, c)." in why, why


# --- R15: מגירה שנכשלת שוב ושוב, לפי חריץ ---------------------------------------------------------------------------

def _wave(conn, session, state, serial="S1", port=1, mac="aa:bb:cc:00:00:31"):
    conn.execute("INSERT OR REPLACE INTO net_devices (mac, disks_json) VALUES (?, ?)",
                 (mac, json.dumps([{"dev": "sda", "serial": serial, "port": port}])))
    conn.commit()
    member = {"mac": mac, "targets_json": json.dumps([{"dev": "sda", "serial": serial, **({"state": state} if state else {})}])}
    return learn_ports.record_wave(conn, session, [member])


def test_a_slot_that_failed_again_and_again_is_counted_from_the_table(conn):
    for n, (state, serial) in enumerate([("failed", "S1"), ("done", "S1"), ("failed", "S2"), ("failed", "S3")]):
        assert _wave(conn, f"ses_{n}", state, serial) == 1
    got = learn_ports.verdict(conn, "aa:bb:cc:00:00:31", 1, "S9")
    assert (got["slot_failed"], got["slot_seen"]) == (3, 4)
    assert got["port_suspect"] is False and got["text_he"] is None                  # what the operator sees is unchanged


def test_a_wave_with_no_state_is_in_neither_count_of_the_slot(conn):
    _wave(conn, "ses_0", "failed")
    _wave(conn, "ses_1", None)
    got = learn_ports.verdict(conn, "aa:bb:cc:00:00:31", 1, "S1")
    assert (got["slot_failed"], got["slot_seen"]) == (1, 1)
    assert conn.execute("SELECT failed FROM port_history WHERE session_id = 'ses_1'").fetchone()["failed"] is None


def test_a_slot_that_was_never_recorded_is_none_not_zero(conn):
    got = learn_ports.verdict(conn, "aa:bb:cc:00:00:31", 7, "S1")
    assert got["slot_seen"] == 0 and got["slot_failed"] == 0                         # asked, nothing observed
    assert learn_ports.verdict(conn, None, None, None)["slot_seen"] is None          # not asked: not known


# --- R13: זנב היומן של הסוכן ----------------------------------------------------------------------------------------

def test_the_agent_log_tail_is_five_times_longer_than_it_was():
    from server.agent import logs
    assert logs.MAX_TEXT == 256 * 1024
    assert "AGENT_LOG_LINES:-2000" in open("agent/lib/core/datareport.sh", encoding="utf-8").read()


# --- ההגירה -------------------------------------------------------------------------------------------------------

def test_a_database_on_23_gains_the_forecast_columns_the_pass_tables_and_is_stamped_24(tmp_path):
    path = tmp_path / "imagesrv.db"
    db.connect(path).close()
    raw = sqlite3.connect(path)
    for column in ("predicted", "actual", "unit", "matched"):
        raw.execute(f"ALTER TABLE brain_decisions DROP COLUMN {column}")
    raw.execute("ALTER TABLE port_history DROP COLUMN failed")
    raw.execute("DROP TABLE chunk_pass_rates")
    raw.execute("DROP TABLE chunk_pass_counters")
    raw.execute("PRAGMA user_version = 23")
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 27
    cols = {r[1] for r in conn.execute("PRAGMA table_info(brain_decisions)")}
    assert {"predicted", "actual", "unit", "matched"} <= cols
    assert "failed" in {r[1] for r in conn.execute("PRAGMA table_info(port_history)")}
    assert {"chunk_pass_rates", "chunk_pass_counters"} <= {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_matched_is_only_zero_one_or_null(conn):
    memory.record(conn, mode="shadow", rule="K3", action="A", verdict="V", reason="r")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE brain_decisions SET matched = 2")


def test_every_event_the_pass_log_writes_has_a_hebrew_label():
    from server.console import journal_he
    for event in ("chunk_write_rate", "chunk_drive_behind", "chunk_no_show", "chunk_fetch_rate"):
        assert event in journal_he.EVENTS_HE, event
