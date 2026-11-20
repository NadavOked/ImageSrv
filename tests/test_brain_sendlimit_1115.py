"""#3471 (B58): what limited the rate on the server and sender side -- one shadow row per wave, `B58`/`SEND_LIMIT`, and
one row per wave from LR16 (the loss on the distribution network against its baseline), `B58`/`NET_LOSS`.

**Negative control:** with `server/learn/server_senses.py`, `server/learn/netloss.py`, `server/brain/say.py` and
`server/brain/catalog.toml` back at the base (`lab/vm-1114f`, `771ddfe2b`) and `server/brain/sendlimit.py` removed, every test marked (neg) fails on
**behaviour**: `Senses.end` / `netloss.evaluate_chunk` run, and the `B58` row is not in `brain_decisions` (or, for the
wave's ledger, `result_json.server` has no `ledger`). The tests marked (unit) import `sendlimit` inside the test, so
they fail on the import there -- that is NOT a negative control, and they are not counted as one. The tests marked
(guard) pass at the base too ("switch off = no row" is true when nothing writes rows): they hold the switch and the
existing journal line in place, and are NOT counted as a negative control either. No test imports
`sendlimit` at module level, so a missing module cannot fail the whole file at collection.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from server import db
from server.brain import memory, say, veto
from server.learn import netloss as learn_netloss, server_io, server_senses as S

HZ = 100
MAC_A, MAC_B, MAC_C, MAC_D = "78:ac:c0:9b:18:01", "78:ac:c0:9b:18:02", "78:ac:c0:9b:18:03", "78:ac:c0:9b:18:04"
CHUNK_MB = 4 * 1024 * 1024 / 1e6                    # a durable chunk is 4 MiB; V7's `mbps` is MB/s


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    return db.connect(tmp_path / "imagesrv.db")


def _rows(conn, action="SEND_LIMIT"):
    return list(reversed([d for d in memory.decisions(conn, rule="B58", limit=200) if d["action"] == action]))


def _one(conn, action="SEND_LIMIT") -> dict:
    rows = _rows(conn, action)
    assert len(rows) == 1, rows
    return rows[0]


# --- fabricated readings: the shape `server_senses.reading` returns ------------------------------------------------

def _disk(at, busy_ms=0, psi_full_us=0):
    return {"at": at, "device": "sda", "read_sectors": 0, "write_sectors": 0, "io_ms": busy_ms, "read_ios": 0,
            "write_ios": 0, "ios_ms": 0, "psi_some_us": 0, "psi_full_us": psi_full_us}


def _sender(chunks, of=400, nbytes=0, elapsed=10.0, done=None):
    return {"1234": {"start": 7, "part": 0, "pass": 1, "chunks": chunks, "bytes": nbytes, "of": of,
                     "elapsed_s": elapsed, "done": done}}


def _bare(at):
    """Nothing was read: every sense `None` (principle 5)."""
    return {"at": at, "disk": None, "cpu_psi": {"some": None, "full": None}, "mem_psi": {"some": None, "full": None},
            "load1": None, "proc": {"hz": HZ, "uptime": None, "server": None, "children": None, "chunkcast": None},
            "nic": {"tx_bytes": None, "tx_dropped": None}, "http": None, "ledger": None, "engine": None,
            "sender": None}


def _full(at, step, *, cpu_ticks=0, busy_ms=0, dropped=0, counts=None, sender=None):
    """Every group read. `step` = windows since the start; the server's ticks, the disk's busy ms and the cloners'
    durable chunks are cumulative, so a value per window is the difference of two readings."""
    return {**_bare(at),
            "disk": _disk(at, busy_ms=busy_ms),
            "cpu_psi": {"some": 0, "full": None}, "mem_psi": {"some": 0, "full": 0},
            "load1": 1.0,
            "proc": {"hz": HZ, "uptime": None, "server": cpu_ticks, "children": 0, "chunkcast": None},
            "nic": {"tx_bytes": 0, "tx_dropped": dropped},
            "ledger": None if counts is None else {m: {"S/4/0": c} for m, c in counts.items()},
            "sender": sender}


def _wave_row(conn, ref="ses_1"):
    server_io.begin(conn, "wave", ref, ".")
    conn.execute("UPDATE server_io SET result_json = ?, ended_at = ? WHERE kind = 'wave' AND ref = ?",
                 (json.dumps({"read_mbps": 50.0, "basis": "ok"}), db.now_iso(), ref))
    conn.commit()


def _end_a_wave(conn, readings, ref="ses_1"):
    """begin, a tick for each reading in the middle, end: the real `Senses`, on a real database."""
    _wave_row(conn, ref)
    it = iter(readings)
    senses = S.Senses(conn, lambda session_id: next(it), lambda: ref)
    senses.begin(ref)
    for _ in readings[1:-1]:
        senses.tick()
    return senses, senses.end(ref)


def _calm(n=4, *, counts_per_window=None, **kw):
    """`n` windows of 30 s, every group read, nothing near a limit; the cloners write `counts_per_window` each."""
    per = counts_per_window or {MAC_A: 300, MAC_B: 300, MAC_C: 300}
    return [_full(30.0 * i, i, cpu_ticks=3000 * i // 10, busy_ms=3000 * i, counts={m: c * i for m, c in per.items()},
                  **kw) for i in range(n + 1)]


# --- (neg) the wave's end writes the row ---------------------------------------------------------------------------

def test_neg_a_wave_that_strained_the_server_records_server_pressure_with_its_evidence(conn):
    """The server process used 1.2 cores in every window (3600 ticks / 30 s): SERVER_PRESSURE, and the senders' state
    (V4/V5) is in the evidence. The senders were last seen at chunk 360 of 400: 90%."""
    readings = [_full(30.0 * i, i, cpu_ticks=3600 * i, busy_ms=3000 * i, counts={MAC_A: 300 * i, MAC_B: 300 * i,
                                                                                   MAC_C: 300 * i},
                      sender=_sender(120 * i, nbytes=50_000_000 * i, elapsed=10.0 * i)) for i in range(4)]
    _, summary = _end_a_wave(conn, readings)
    row = _one(conn)
    assert row["verdict"] == "SERVER_PRESSURE" and row["mode"] == "shadow", row
    assert "מעבד השרת" in row["reason"] and "1.2" in row["reason"], row["reason"]
    assert (row["entity_kind"], row["entity_key"], row["round_id"]) == ("round", "ses_1", "ses_1")
    assert "צל" in (row["outcome"] or "")
    assert "server_cpu=1.2" in row["evidence"], row["evidence"]
    assert any(e.startswith("pass=p0/r1 chunks=360/400 pct=90 ") for e in row["evidence"]), row["evidence"]
    assert summary["peaks"]["server_cpu"] == 1.2


def test_neg_a_cloner_far_below_the_median_of_the_others_is_recorded_as_slow(conn):
    """Four cloners: three at 300 chunks a window (41.9 MB/s), the fourth at 60 (8.4 MB/s, 20%)."""
    _, summary = _end_a_wave(conn, _calm(4, counts_per_window={MAC_A: 300, MAC_B: 300, MAC_C: 300, MAC_D: 60}))
    row = _one(conn)
    assert row["verdict"] == "SLOW_CLONER", row
    assert MAC_D in row["reason"] and "8.4" in row["reason"] and "41.9" in row["reason"], row["reason"]
    assert MAC_A not in row["reason"]
    assert f"cloner={MAC_A} mbps=41.9 chunks=1200 seconds=120" in row["evidence"], row["evidence"]
    assert summary["ledger"][MAC_D]["mbps"] == round(60 * CHUNK_MB / 30, 1)


def test_neg_a_calm_wave_where_everything_was_read_records_no_limit_seen(conn):
    _end_a_wave(conn, _calm(4))
    row = _one(conn)
    assert row["verdict"] == "NO_LIMIT_SEEN", row
    assert "--write-kbps" in row["reason"]                 # even then: who slowed the stream was not measured


def test_neg_a_wave_nothing_was_read_in_records_not_measured_never_no_limit(conn):
    _end_a_wave(conn, [_bare(0.0), _bare(30.0), _bare(60.0)])
    row = _one(conn)
    assert row["verdict"] == "NOT_MEASURED", row
    for group in ("מעבד", "PSI", "כרטיס ההפצה", "דיסק הספרייה", "המשכפלים"):
        assert group in row["reason"], (group, row["reason"])


def test_neg_a_group_that_was_not_read_stops_a_calm_wave_from_being_called_clean(conn):
    """The disk of the library was not read: the rest is calm, and the verdict is not NO_LIMIT_SEEN."""
    readings = [{**r, "disk": None} for r in _calm(4)]
    _end_a_wave(conn, readings)
    row = _one(conn)
    assert row["verdict"] == "NOT_MEASURED" and "דיסק הספרייה" in row["reason"], row


def test_neg_the_card_dropping_packets_is_server_pressure(conn):
    readings = _calm(4)
    readings[-1] = {**readings[-1], "nic": {"tx_bytes": 0, "tx_dropped": 9}}
    _end_a_wave(conn, readings)
    row = _one(conn)
    assert row["verdict"] == "SERVER_PRESSURE" and "9" in row["reason"] and "nic_tx_dropped=9" in row["evidence"], row


def test_neg_a_wave_whose_end_failed_is_recorded_as_not_measured_and_still_never_raises(conn):
    _wave_row(conn)
    reads = iter([_bare(0.0)])

    def read(session_id):
        try:
            return next(reads)
        except StopIteration:
            raise OSError("the last reading failed") from None
    senses = S.Senses(conn, read, lambda: "ses_1")
    senses.begin("ses_1")
    assert senses.end("ses_1") is None
    assert _one(conn)["verdict"] == "NOT_MEASURED"


def test_neg_the_wave_that_ends_twice_is_one_row(conn):
    senses, _ = _end_a_wave(conn, _calm(2))
    assert senses.end("ses_1") is None                     # the second end: the wave is gone, nothing is written
    assert len(_rows(conn)) == 1


def test_guard_the_switch_off_records_nothing_and_the_wave_still_ends_with_its_summary(conn):
    db.set_setting(conn, veto.SETTING, "off")
    _, summary = _end_a_wave(conn, _calm(2))
    assert summary is not None and _rows(conn) == []


def test_neg_the_ledger_of_the_wave_is_kept_in_the_wave_row_so_the_brain_and_the_console_can_read_it(conn):
    """The summary used to leave `ledger` out (`WINDOW_ONLY`): it was only in the window rows."""
    _end_a_wave(conn, _calm(3))
    result = json.loads(conn.execute("SELECT result_json FROM server_io WHERE kind = 'wave'").fetchone()[0])
    ledger = result["server"]["ledger"]
    assert set(ledger) == {MAC_A, MAC_B, MAC_C}
    assert ledger[MAC_A] == {"chunks": 900, "seconds": 90.0, "mbps": 41.9, "idle": False, "gaps": 0}


def test_neg_a_cloner_that_finished_first_is_not_slow_and_one_that_wrote_nothing_is_idle_not_ranked(conn):
    """A writes 300 chunks in the first window and then nothing (finished): its rate is the rate while it wrote. D never
    wrote a chunk (complete already, or not in the stream): idle -- not slow, not in the median."""
    steps = [_full(30.0 * i, i, cpu_ticks=300 * i, busy_ms=3000 * i,
                   counts={MAC_A: 300 * min(i, 1), MAC_B: 300 * i, MAC_C: 300 * i, MAC_D: 0}) for i in range(5)]
    _, summary = _end_a_wave(conn, steps)
    assert summary["ledger"][MAC_A]["mbps"] == 41.9 and summary["ledger"][MAC_A]["seconds"] == 30.0
    assert summary["ledger"][MAC_D] == {"chunks": 0, "seconds": None, "mbps": None, "idle": True, "gaps": 0}
    row = _one(conn)
    assert row["verdict"] == "NO_LIMIT_SEEN", row
    assert f"cloner={MAC_D} idle (no chunk became durable)" in row["evidence"], row["evidence"]
    assert f"{MAC_D} (לא כתב חתיכה)" in row["reason"]


def test_neg_a_window_the_ledger_was_not_read_in_is_a_gap_not_a_zero(conn):
    steps = _calm(4)
    steps[2] = {**steps[2], "ledger": None}                # the middle reading failed: two windows touch it
    _, summary = _end_a_wave(conn, steps)
    entry = summary["ledger"][MAC_A]
    assert entry["gaps"] == 2 and entry["seconds"] == 60.0 and entry["mbps"] == 41.9, entry


# --- (neg) LR16: the loss on the distribution network (the two (guard) tests below pass at the base too) --------------------------------------------------------------

def _chunk_waves():
    from test_chunk_netloss_3164 import _base, _wave       # noqa: PLC0415 -- the same fabricated waves
    return _base, _wave


def test_neg_a_jump_in_the_network_loss_is_recorded_once_with_its_baseline(conn):
    base, wave = _chunk_waves()
    base(conn)
    sid = wave(conn, 2.0)
    got = learn_netloss.evaluate_chunk(conn, sid)
    assert got["state"] == "jump"
    row = _one(conn, "NET_LOSS")
    assert row["verdict"] == "JUMP" and row["round_id"] == sid and row["entity_key"] == sid, row
    assert "קפץ" in row["reason"] and "0.12/GB" in row["reason"] and "2.00/GB" in row["reason"], row["reason"]
    assert "state=jump" in row["evidence"] and "waves=3" in row["evidence"], row["evidence"]
    learn_netloss.evaluate_chunk(conn, sid)                # the wave is closed twice (send_done, then the room)
    assert len(_rows(conn, "NET_LOSS")) == 1


def test_neg_a_wave_without_a_baseline_yet_is_unchecked_never_ok(conn):
    _, wave = _chunk_waves()
    sid = wave(conn, 0.0)
    learn_netloss.evaluate_chunk(conn, sid)
    row = _one(conn, "NET_LOSS")
    assert row["verdict"] == "UNCHECKED" and "אין בסיס" in row["reason"] and "0 מתוך 3" in row["reason"], row
    assert row["verdict"] != "OK"


def test_neg_a_wave_within_its_baseline_is_ok(conn):
    base, wave = _chunk_waves()
    base(conn)
    sid = wave(conn, 0.3)
    learn_netloss.evaluate_chunk(conn, sid)
    assert _one(conn, "NET_LOSS")["verdict"] == "OK"


def test_neg_a_cut_wave_is_net_gap_and_a_wave_the_server_disk_held_back_is_server_disk(conn):
    _, wave = _chunk_waves()
    gap = wave(conn, 0.0, gap=True)
    learn_netloss.evaluate_chunk(conn, gap)
    assert _one(conn, "NET_LOSS")["verdict"] == "NET_GAP"
    conn.execute("DELETE FROM brain_decisions")
    held = wave(conn, 0.0)
    server_io.begin(conn, "wave", held, ".")
    conn.execute("UPDATE server_io SET basis = 'server_disk' WHERE kind = 'wave' AND ref = ?", (held,))
    conn.commit()
    learn_netloss.evaluate_chunk(conn, held)
    row = _one(conn, "NET_LOSS")
    assert row["verdict"] == "SERVER_DISK" and "דיסק הספרייה" in row["reason"], row


def test_guard_the_switch_off_records_no_net_loss_row_and_the_journal_still_gets_its_jump(conn):
    base, wave = _chunk_waves()
    db.set_setting(conn, veto.SETTING, "off")
    base(conn)
    sid = wave(conn, 2.0)
    learn_netloss.evaluate_chunk(conn, sid)
    assert _rows(conn, "NET_LOSS") == []
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'net_loss_jump'").fetchone()[0] == 1


# --- (neg) the console reads the rows in Hebrew --------------------------------------------------------------------

def test_neg_the_rows_read_in_hebrew_words_not_in_codes(conn):
    """`say.line_he` is the line the console shows. An action or verdict without a label stays a code (`SEND_LIMIT`)."""
    _end_a_wave(conn, _calm(4))
    line = _one(conn)["line_he"]
    assert "מה הגביל את הקצב: לא נראה גורם מגביל" in line and "SEND_LIMIT" not in line and "NO_LIMIT_SEEN" not in line, line
    _, wave = _chunk_waves()
    learn_netloss.evaluate_chunk(conn, wave(conn, 0.0))
    line = _one(conn, "NET_LOSS")["line_he"]
    assert "אבדן ברשת ההפצה: לא נבדק (אין בסיס עדיין)" in line and "NET_LOSS" not in line, line


def test_neg_every_verdict_of_b58_has_a_hebrew_label():
    verdicts = ("SERVER_PRESSURE", "SLOW_CLONER", "NO_LIMIT_SEEN", "NOT_MEASURED",
                "JUMP", "OK", "UNCHECKED", "SERVER_DISK", "NET_GAP")
    assert [v for v in verdicts if v not in say.VERDICT_HE] == []
    assert [a for a in ("SEND_LIMIT", "NET_LOSS") if a not in say.ACTION_HE] == []


# --- (unit) the pure decision --------------------------------------------------------------------------------------

def _summary(**over):
    peaks = {"server_cpu": 0.3, "chunkcast_cpu": 0.4, "psi_cpu_some": 0.0, "psi_mem_some": 0.0, "psi_mem_full": 0.0,
             "disk_util": 0.2, "disk_psi_full": 0.0, "sent_mbps": 800.0}
    peaks.update(over.pop("peaks", {}))
    out = {"windows": 3, "windows_dropped": 0, "nic_tx_dropped": 0, "peaks": peaks,
           "passes": {"p0/r1": {"chunks": 400, "of": 400, "bytes": 1_000_000_000, "elapsed_s": 20.0,
                                "done": {"chunks": 400, "bytes": 1_000_000_000, "ms": 10_000}}},
           "ledger": {MAC_A: {"chunks": 900, "seconds": 90.0, "mbps": 40.0, "idle": False, "gaps": 0},
                      MAC_B: {"chunks": 900, "seconds": 90.0, "mbps": 42.0, "idle": False, "gaps": 0},
                      MAC_C: {"chunks": 900, "seconds": 90.0, "mbps": 41.0, "idle": False, "gaps": 0}}}
    out.update(over)
    return out


def _mbps(**by_mac):
    return {"ledger": {m: {"mbps": v, "idle": False} for m, v in by_mac.items()}}


def test_unit_a_calm_measured_wave_is_no_limit_seen_and_zero_is_a_measurement():
    from server.brain import sendlimit as L
    assert L.diagnose(_summary())[0] == "NO_LIMIT_SEEN"
    zero = _summary(peaks={k: 0.0 for k in ("server_cpu", "chunkcast_cpu", "disk_util")})
    assert L.diagnose(zero)[0] == "NO_LIMIT_SEEN"                      # a measured 0.0 is not "not measured"


@pytest.mark.parametrize("peak,value", [("server_cpu", 1.0), ("chunkcast_cpu", 0.95), ("psi_cpu_some", 0.2),
                                        ("psi_mem_some", 0.2), ("psi_mem_full", 0.1), ("disk_util", 0.95),
                                        ("disk_psi_full", 0.06)])
def test_unit_every_server_signal_over_its_limit_is_server_pressure(peak, value):
    from server.brain import sendlimit as L
    verdict, reason, evidence = L.diagnose(_summary(peaks={peak: value}))
    assert verdict == "SERVER_PRESSURE", reason
    assert f"{peak}={value:g}" in evidence


def test_unit_just_under_every_limit_is_not_pressure():
    from server.brain import sendlimit as L
    under = {"server_cpu": 0.89, "chunkcast_cpu": 0.89, "psi_cpu_some": 0.09, "psi_mem_some": 0.09,
             "psi_mem_full": 0.09, "disk_util": 0.89, "disk_psi_full": 0.04}
    assert L.diagnose(_summary(peaks=under))[0] == "NO_LIMIT_SEEN"


def test_unit_signals_together_the_first_in_the_order_is_the_verdict_and_all_are_in_the_reason():
    from server.brain import sendlimit as L
    s = _summary(peaks={"server_cpu": 1.4, "disk_util": 0.97}, nic_tx_dropped=5, **_mbps(a=40.0, b=41.0, c=42.0, d=5.0))
    verdict, reason, _ = L.diagnose(s)
    assert verdict == "SERVER_PRESSURE"
    for part in ("מעבד השרת", "דיסק הספרייה עסוק", "5 חבילות", "משכפל d"):
        assert part in reason, (part, reason)


def test_unit_slow_cloner_is_against_the_median_of_the_others_and_needs_three_measured():
    from server.brain import sendlimit as L
    assert L.diagnose(_summary(**_mbps(a=40.0, b=40.0, c=19.0)))[0] == "SLOW_CLONER"
    assert L.diagnose(_summary(**_mbps(a=40.0, b=40.0, c=21.0)))[0] == "NO_LIMIT_SEEN"
    verdict, reason, _ = L.diagnose(_summary(**_mbps(a=40.0, b=4.0)))     # two: no median worth judging by
    assert verdict == "NOT_MEASURED" and "2 נמדדו מתוך 3 לפחות" in reason, reason
    assert L.diagnose(_summary(**_mbps(a=40.0, b=40.0, c=0.0)))[0] == "SLOW_CLONER"    # a measured zero is slow


def test_unit_a_cloner_that_was_not_measured_is_neither_slow_nor_fast_and_is_listed():
    from server.brain import sendlimit as L
    s = _summary(ledger={MAC_A: {"mbps": 40.0}, MAC_B: {"mbps": 40.0}, MAC_C: {"mbps": 41.0},
                         MAC_D: {"mbps": None, "idle": False}})
    verdict, reason, evidence = L.diagnose(s)
    assert verdict == "NO_LIMIT_SEEN" and f"{MAC_D} (לא נמדד)" in reason, reason
    assert f"cloner={MAC_D} unmeasured" in evidence


def test_unit_nothing_measured_or_a_broken_summary_is_not_measured_and_never_raises():
    from server.brain import sendlimit as L
    assert L.diagnose(None)[0] == "NOT_MEASURED" and L.diagnose("x")[0] == "NOT_MEASURED"
    empty = {"peaks": {}, "nic_tx_dropped": None, "ledger": None, "passes": {}}
    assert L.diagnose(empty)[0] == "NOT_MEASURED"
    garbage = {"peaks": {"server_cpu": "high", "disk_util": True, "psi_cpu_some": -1, "chunkcast_cpu": float("nan")},
               "nic_tx_dropped": "9", "ledger": {MAC_A: "x", MAC_B: None}, "passes": {"p0/r1": 5}, "windows": []}
    verdict, reason, _ = L.diagnose(garbage)
    assert verdict == "NOT_MEASURED", reason                 # a flag, a negative, NaN, a string: not a reading


def test_unit_a_pass_that_was_seen_gives_its_place_and_average_rate():
    from server.brain import sendlimit as L
    s = _summary(passes={"p0/r1": {"chunks": 120, "of": 400, "bytes": 75_000_000, "elapsed_s": 10.0, "done": None},
                         "p1/r1": {"chunks": None, "of": None, "bytes": None, "elapsed_s": None, "done": None}})
    evidence = L.diagnose(s)[2]
    assert "pass=p0/r1 chunks=120/400 pct=30 bytes=75000000 elapsed_s=10 avg_mbps=60 done=no" in evidence
    assert ("pass=p1/r1 chunks=unmeasured/unmeasured pct=unmeasured bytes=unmeasured elapsed_s=unmeasured"
            " avg_mbps=unmeasured done=no") in evidence


class _Broken:
    def execute(self, *args, **kw):
        raise sqlite3.OperationalError("database is locked")

    commit = rollback = execute


def test_unit_note_wave_is_one_row_and_never_raises(conn):
    from server.brain import sendlimit as L
    assert isinstance(L.note_wave(conn, "ses_9", _summary()), int)
    assert L.note_wave(conn, "ses_9", _summary()) is None            # already noted
    assert [r["verdict"] for r in _rows(conn)] == ["NO_LIMIT_SEEN"]
    broken = _Broken()
    assert L.note_wave(broken, "ses_10", _summary()) is None          # a database that fails: a log line, no exception
    assert L.note_netloss(broken, "ses_10", {"state": "ok"}) is None


def test_unit_note_netloss_states_and_a_state_nobody_knows(conn):
    from server.brain import sendlimit as L
    base = {"kind": "udpcast", "interface": "eth1", "drawers": 2, "rate": "400m", "loss": 0.02, "baseline": 0.002,
            "waves": 3, "local": None}
    assert L.note_netloss(conn, "s1", {**base, "state": "jump"}) is not None
    assert L.note_netloss(conn, "s2", {**base, "state": "weird"}) is not None
    assert L.note_netloss(conn, "s3", "not a dict") is None
    rows = {r["round_id"]: r for r in _rows(conn, "NET_LOSS")}
    assert rows["s1"]["verdict"] == "JUMP" and "2.00%" in rows["s1"]["reason"] and "0.20%" in rows["s1"]["reason"]
    assert rows["s2"]["verdict"] == "NOT_MEASURED" and "לא מוכר" in rows["s2"]["reason"]


def test_unit_the_ledger_and_the_passes_never_reach_the_long_term_memory():
    """`memory._server_io` folds flat numbers only: the objects (`ledger`, `passes`) are not in it. This is why the
    brain reads them from the wave's summary at its end, not from the memory afterwards."""
    result = {"read_mbps": 50.0, "server": {"server_cpu": 0.5, "windows": 3, "peaks": {"server_cpu": 1.2},
                                            "ledger": {MAC_A: {"chunks": 900, "mbps": 40.0}},
                                            "passes": {"p0/r1": {"chunks": 4, "of": 5}}}}
    got = memory._server_io({"result_json": json.dumps(result)})
    assert got["peak_server_cpu"] == 1.2 and got["windows"] == 3
    assert not [k for k in got if "ledger" in k or "pass" in k or MAC_A in k], got
