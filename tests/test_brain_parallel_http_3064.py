"""#3064 (B5, X13): would HTTP in parallel with the stream open for a drawer -- by how fast its write queue drains
(S46/S47 of #3324: ``inflight`` x ``w_ms``). The decision only, in shadow: the executor (one writer that takes chunks
from the stream and from HTTP) is a change in `agent/chunkcast.c`, not built here.

**Negative control:** with `server/agent/reports.py` back at #3324 (`3a6346179`) and `server/brain/parallelhttp.py`
removed, the test marked (neg) fails on behaviour: a progress report carries the senses, and no `B5` row is written.
The tests that import `parallelhttp` fail there on the import -- that is NOT a negative control, and they are not
counted as one.
"""

from __future__ import annotations

import pytest

from server import db
from server.agent import reports
from server.brain import memory, veto

MAC = "78:ac:c0:9b:18:85"


def _forget():
    veto.forget()
    try:
        from server.brain import parallelhttp
        parallelhttp.forget()
    except ImportError:                     # the negative control: the module is not there
        pass


@pytest.fixture()
def conn(tmp_path):
    _forget()
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at, started_at) VALUES ('ses_w', 'grp_CLONERS', 'img', 'p', 1, 300,"
              " 'running', 't', ?, 0, ?)", (db.now_iso(), db.now_iso()))
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('ses_w', ?, 'writing', ?)",
              (MAC, db.now_iso()))
    c.commit()
    yield c
    _forget()


def _senses(**disks):
    return {"window_s": 5, "windows": 3, "cpu": None, "nic": None, "disks": disks}


def _disk(inflight, w_ms, util=50):
    return {"util": util, "util_max": util, "inflight": inflight, "inflight_max": inflight, "w_ms": w_ms,
            "w_ms_max": w_ms}


def _report(conn, senses):
    assert reports.ingest(conn, {"session_id": "ses_w", "mac": MAC, "state": "writing", "targets": [],
                                 "wave_senses": senses})["ok"]


def _rows(conn):
    return list(reversed([d for d in memory.decisions(conn, rule="B5", limit=100) if d["action"] == "PARALLEL_HTTP"]))


def test_a_report_with_the_senses_records_whether_parallel_http_would_open(conn):
    """(neg) sda drains in 3 x 2.5ms: it would open. sdb drains in 40 x 31ms: it would stay closed."""
    _report(conn, _senses(sda=_disk(3, 2.5), sdb=_disk(40, 31.0)))
    got = {next(e for e in r["evidence"] if e.startswith("drawer=")): r for r in _rows(conn)}
    assert got["drawer=sda"]["verdict"] == "OPEN" and "drain_ms=8" in got["drawer=sda"]["evidence"], got
    assert got["drawer=sdb"]["verdict"] == "CLOSE" and "drain_ms=1240" in got["drawer=sdb"]["evidence"], got
    assert all(r["entity_kind"] == "machine" and r["entity_key"] == MAC and r["round_id"] == "ses_w" for r in got.values())
    assert all("צל" in (r["outcome"] or "") for r in got.values())


def test_a_row_only_when_a_drawer_s_state_changes_and_between_the_two_it_holds(conn):
    """X13's hysteresis: 250ms opens, 1 s closes, and in between the state stays."""
    _report(conn, _senses(sda=_disk(3, 2.5)))                    # 8ms: OPEN
    _report(conn, _senses(sda=_disk(4, 2.5)))                    # 10ms: still OPEN -- no row
    _report(conn, _senses(sda=_disk(50, 10.0)))                  # 500ms: between -- OPEN holds, no row
    assert [r["verdict"] for r in _rows(conn)] == ["OPEN"]
    _report(conn, _senses(sda=_disk(50, 20.0)))                  # 1000ms: CLOSE
    _report(conn, _senses(sda=_disk(50, 10.0)))                  # 500ms: between -- CLOSE holds
    _report(conn, _senses(sda=_disk(10, 2.0)))                   # 20ms: OPEN again
    assert [r["verdict"] for r in _rows(conn)] == ["OPEN", "CLOSE", "OPEN"]


def test_a_queue_that_was_not_read_is_unmeasured_never_open(conn):
    """Principle 5: no completed write in the window (w_ms null), or no diskstats line, decides nothing."""
    _report(conn, _senses(sda=_disk(0, None), sdb=None))
    assert {r["verdict"] for r in _rows(conn)} == {"UNMEASURED"}
    assert any("w_ms=unmeasured" in r["evidence"] for r in _rows(conn))


def test_the_brain_switched_off_records_nothing(conn):
    db.set_setting(conn, veto.SETTING, "off")
    _report(conn, _senses(sda=_disk(3, 2.5)))
    assert _rows(conn) == []


def test_the_pure_decision():
    from server.brain import parallelhttp as P
    assert P.drain_ms({"inflight": 3, "w_ms": 2.5}) == 7.5
    assert P.drain_ms({"inflight": None, "w_ms": 2.5}) is None and P.drain_ms(None) is None
    assert P.drain_ms({"inflight": True, "w_ms": 2.5}) is None                 # a flag is not a count
    assert P.decide(P.START_MS, None) == P.OPEN and P.decide(P.STOP_MS, P.OPEN) == P.CLOSE
    assert P.decide(500.0, None) == P.CLOSE and P.decide(500.0, P.OPEN) == P.OPEN
    assert P.decide(None, P.OPEN) == P.UNMEASURED


# --- #3440: a window with no completed write still has the wave's own w_ms_max (S47) --------------------------------


def test_a_window_without_a_completed_write_takes_the_waves_w_ms_max(conn):
    """(neg) LabRoot 15/11: the last window of a wave has no completed write (w_ms null, inflight 0), but the wave's
    own `w_ms_max` is in the same report (and in `wave_senses`, as `disk_w_ms_max`). B5 said `UNMEASURED` for both
    drawers, with the sense in the DB. Now: 4 x 31ms = 124ms, from the wave's maximum, and the evidence says so."""
    last = {"util": 0, "util_max": 90, "inflight": 4, "inflight_max": 9, "w_ms": None, "w_ms_max": 31.0}
    _report(conn, _senses(sda=last))
    row = _rows(conn)[0]
    assert row["verdict"] == "OPEN" and "drain_ms=124" in row["evidence"], row
    assert "w_ms=31" in row["evidence"] and "w_ms_from=wave_max" in row["evidence"], row


def test_the_windows_own_w_ms_wins_over_the_waves_maximum(conn):
    window = {"util": 50, "util_max": 90, "inflight": 3, "inflight_max": 9, "w_ms": 2.5, "w_ms_max": 31.0}
    _report(conn, _senses(sda=window))
    row = _rows(conn)[0]
    assert row["verdict"] == "OPEN" and "drain_ms=8" in row["evidence"] and "w_ms_from=window" in row["evidence"], row


def test_no_w_ms_in_the_window_and_none_in_the_wave_is_still_unmeasured(conn):
    """Principle 5 holds at the fallback: when the wave never completed a write either, nothing is known."""
    _report(conn, _senses(sda={"util": 0, "util_max": 0, "inflight": 4, "inflight_max": 4, "w_ms": None,
                               "w_ms_max": None}))
    row = _rows(conn)[0]
    assert row["verdict"] == "UNMEASURED" and "w_ms_from=none:no_writes" in row["evidence"], row


def test_a_drawer_whose_counters_were_not_read_says_no_counters(conn):
    """#3819 (LabRoot 19/11, rows 12794/12796): the whole disk was null -- inflight, util and w_ms all unmeasured. That is
    not "no writes in the window" (there inflight/util would read 0), and the evidence must say which one it is."""
    _report(conn, _senses(sda=None))
    row = _rows(conn)[0]
    assert row["verdict"] == "UNMEASURED" and "w_ms_from=none:no_counters" in row["evidence"], row
    assert "inflight=unmeasured" in row["evidence"], row


def test_write_ms_says_why_it_is_empty():
    from server.brain import parallelhttp as P
    assert P.write_ms(None) == (None, "none:no_counters")
    assert P.write_ms({"inflight": None, "w_ms": None, "w_ms_max": None}) == (None, "none:no_counters")
    assert P.write_ms({"inflight": 0, "w_ms": None, "w_ms_max": None}) == (None, "none:no_writes")


def test_write_ms_pure():
    from server.brain import parallelhttp as P
    assert P.write_ms({"w_ms": 2.5, "w_ms_max": 9.0}) == (2.5, "window")
    assert P.write_ms({"w_ms": None, "w_ms_max": 9.0}) == (9.0, "wave_max")
    assert P.write_ms({"w_ms": None, "w_ms_max": None}) == (None, "none:no_counters") and P.write_ms(None) == (None, "none:no_counters")
    assert P.write_ms({"w_ms": True, "w_ms_max": -1}) == (None, "none:no_counters")        # a flag, and a negative, are not a time
    assert P.drain_ms({"inflight": 4, "w_ms": None, "w_ms_max": 31.0}) == 124.0


# --- #3446: the decision's own number is `predicted` ------------------------------------------------------------------


def test_b5_records_its_drain_time_as_the_prediction(conn):
    """(neg) 3 x 2.5ms = 7.5ms and 40 x 31ms = 1240ms are numbers the rule computed; they were only in the text."""
    _report(conn, _senses(sda=_disk(3, 2.5), sdb=_disk(40, 31.0), sdc=_disk(0, None)))
    got = {next(e for e in r["evidence"] if e.startswith("drawer=")): r for r in _rows(conn)}
    assert (got["drawer=sda"]["predicted"], got["drawer=sda"]["unit"]) == (7.5, "ms")
    assert (got["drawer=sdb"]["predicted"], got["drawer=sdb"]["unit"]) == (1240.0, "ms")
    assert got["drawer=sdc"]["verdict"] == "UNMEASURED" and got["drawer=sdc"]["predicted"] is None   # not read: not 0
