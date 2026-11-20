"""B59 (#3041 / #3064): what limited the rate on the receiver's side -- the verdict, in shadow, per machine per wave.

`server/brain/rxlimit.py` reads what the agent and the server already collect on the receiver (the kernel's drops, the
SO_RCVBUF it granted, the pass word, the per-core CPU, the NIC and drive counters, the HTTP fetches) and records ONE
`B59`/`RX_LIMIT` row when the machine's terminal report arrives. Nothing acts on it.

What is tested:
- the pure `diagnose`: each verdict, several signals together (the priority, and every signal in the reason), and
  principle 5 for every sense -- "not measured" (None / missing / a flag / a negative) is never 0 and never "fine";
- the report path through `reports.ingest`: a terminal report writes the row from what the DB holds; a progress report
  writes nothing; the switch `off` writes nothing; a corrupt report or a missing table does not raise.

**Negative control:** with `server/agent/reports.py` and `server/brain/rxlimit.py` back at the base commit
(`grep -c rxlimit` and `grep -c RX_LIMIT` on the production files = 0), the tests marked (neg) fail on BEHAVIOUR: a terminal
report is ingested and no `B59` row is written. The tests that import `rxlimit` fail there on the import -- that is NOT a
negative control, and they are not counted as one. The integration tests deliberately never import `rxlimit`.
"""

from __future__ import annotations

import pytest

from server import db
from server.agent import reports
from server.brain import memory, veto

MAC = "78:ac:c0:9b:18:85"
CLEAN_DROPS = {"rcvbuf": 0, "inerrors": 0, "abandoned": 0}
CLEAN_WAVE = {"cpu_busy_min_core": 40.0, "rx_missed": 0.0, "disk_util_max": 30.0}


def _diagnose(**kw):
    from server.brain import rxlimit
    args = {"drops": None, "wave": None, "senses": None, "cpu": None, **kw}
    return rxlimit.diagnose(args["drops"], args["wave"], args["senses"], args["cpu"])


# --- the pure verdict ------------------------------------------------------------------------------------------------


def test_nothing_measured_is_not_measured_never_fine():
    for args in ({}, {"drops": "x", "wave": 5, "senses": [], "cpu": 1}, {"drops": {"rcvbuf": None, "inerrors": None}, "wave": {}}):
        verdict, reason, evidence = _diagnose(**args)
        assert verdict == "NOT_MEASURED", (args, verdict)
        assert "unmeasured=socket,nic,cpu,disk" in evidence and "rcvbuf=unmeasured" in evidence


def test_every_sense_measured_and_clean_is_no_limit_seen():
    verdict, reason, evidence = _diagnose(drops=CLEAN_DROPS, wave=CLEAN_WAVE)
    assert verdict == "NO_LIMIT_SEEN" and "unmeasured=none" in evidence, (verdict, reason)


@pytest.mark.parametrize("missing", ["socket", "nic", "cpu", "disk"])
def test_no_limit_is_never_said_when_a_sense_that_would_see_it_was_not_measured(missing):
    """Principle 5: a clean reading of three senses is not 'nothing limited the rate'."""
    drops, wave = dict(CLEAN_DROPS), dict(CLEAN_WAVE)
    if missing == "socket":
        drops = {"rcvbuf": None, "inerrors": 0, "abandoned": 0}
    else:
        del wave[{"nic": "rx_missed", "cpu": "cpu_busy_min_core", "disk": "disk_util_max"}[missing]]
    verdict, reason, evidence = _diagnose(drops=drops, wave=wave)
    assert verdict == "NO_LIMIT_PARTIAL", verdict
    assert f"unmeasured={missing}" in evidence and "לא נמדדו" in reason


def test_a_zero_that_was_measured_differs_from_one_that_was_not():
    measured = _diagnose(drops=CLEAN_DROPS, wave=CLEAN_WAVE)
    unread = _diagnose(drops={"rcvbuf": None}, wave=CLEAN_WAVE)
    assert measured[0] == "NO_LIMIT_SEEN" and unread[0] == "NO_LIMIT_PARTIAL" and "rcvbuf=0" in measured[2] and "rcvbuf=unmeasured" in unread[2]


def test_the_kernel_dropping_blocks_is_rx_drops_and_the_reason_says_how_many():
    verdict, reason, evidence = _diagnose(drops={**CLEAN_DROPS, "rcvbuf": 189}, wave=CLEAN_WAVE)
    assert verdict == "RX_DROPS" and "189" in reason and "rcvbuf=189" in evidence


def test_chunks_the_receiver_gave_up_are_rx_drops():
    assert _diagnose(drops={**CLEAN_DROPS, "abandoned": 12}, wave=CLEAN_WAVE)[0] == "RX_DROPS"


def test_evicted_counts_only_when_abandoned_was_not_read():
    """`abandoned` is `evicted` summed over the passes: both would count the same loss twice -- and a measured 0 stands."""
    senses = {"rx": {"passes": 1, "read": 1, "evicted": 9}}
    assert _diagnose(drops={"rcvbuf": 0, "abandoned": None}, wave=CLEAN_WAVE, senses=senses)[0] == "RX_DROPS"
    assert _diagnose(drops={"rcvbuf": 0, "abandoned": 0}, wave=CLEAN_WAVE, senses=senses)[0] == "NO_LIMIT_SEEN"


def test_udp_input_errors_beyond_the_rcvbuf_ones_are_rx_drops():
    """`InErrors` includes `RcvbufErrors`: only the excess says something else was wrong."""
    assert _diagnose(drops={"rcvbuf": 5, "inerrors": 5}, wave=CLEAN_WAVE)[1].count("InErrors") == 0
    assert "InErrors=8" in _diagnose(drops={"rcvbuf": 5, "inerrors": 8}, wave=CLEAN_WAVE)[1]


def test_a_socket_buffer_the_kernel_cut_is_rx_drops_even_with_no_drop_yet():
    """Asked 8MB, the kernel gave 4MB: the reason says by how much."""
    verdict, reason, evidence = _diagnose(drops={**CLEAN_DROPS, "rcvbuf_asked": 8 << 20, "rcvbuf_bytes": 4 << 20}, wave=CLEAN_WAVE)
    assert verdict == "RX_DROPS" and "8MB" in reason and "4MB" in reason and "פי 2.0 פחות" in reason
    assert "rcvbuf_asked=8388608(reported)" in evidence


def test_a_chunk_wave_does_not_report_the_ask_so_the_code_s_default_is_used_and_said():
    verdict, reason, evidence = _diagnose(drops={**CLEAN_DROPS, "rcvbuf_bytes": 425984}, wave=CLEAN_WAVE)
    assert verdict == "RX_DROPS" and "16MB" in reason and "416KB" in reason and "ברירת המחדל" in reason
    assert "rcvbuf_asked=16777216(default)" in evidence


def test_a_buffer_at_least_as_big_as_the_ask_is_no_signal_and_no_grant_is_unmeasured():
    assert _diagnose(drops={**CLEAN_DROPS, "rcvbuf_asked": 1 << 20, "rcvbuf_bytes": 2 << 20}, wave=CLEAN_WAVE)[0] == "NO_LIMIT_SEEN"
    verdict, _, evidence = _diagnose(drops={**CLEAN_DROPS, "rcvbuf_asked": 1 << 20, "rcvbuf_bytes": None}, wave=CLEAN_WAVE)
    assert verdict == "NO_LIMIT_SEEN" and "rcvbuf_bytes=unmeasured" in evidence


def test_foreign_packets_are_evidence_not_a_loss():
    senses = {"rx": {"passes": 1, "read": 1, "foreign": 400}}
    verdict, reason, evidence = _diagnose(drops=CLEAN_DROPS, wave=CLEAN_WAVE, senses=senses)
    assert verdict == "NO_LIMIT_SEEN" and "rx.foreign=400" in evidence and "400 חבילות זרות" in reason


@pytest.mark.parametrize("missed,no_buffer", [(3, 0), (0, 2)])
def test_the_card_dropping_is_nic_drops(missed, no_buffer):
    verdict, reason, _ = _diagnose(drops=CLEAN_DROPS, wave={**CLEAN_WAVE, "rx_missed": float(missed), "rx_no_buffer": float(no_buffer)})
    assert verdict == "NIC_DROPS" and f"rx_missed={missed}" in reason


def test_every_core_above_85_is_cpu_bound_and_exactly_85_is_not():
    assert _diagnose(drops=CLEAN_DROPS, wave={**CLEAN_WAVE, "cpu_busy_min_core": 86.0})[0] == "CPU_BOUND"
    assert _diagnose(drops=CLEAN_DROPS, wave={**CLEAN_WAVE, "cpu_busy_min_core": 85.0})[0] == "NO_LIMIT_SEEN"


def test_a_throttled_cpu_is_cpu_bound_and_the_cpu_is_measured_by_it_alone():
    verdict, reason, _ = _diagnose(drops=CLEAN_DROPS, wave={"rx_missed": 0.0, "disk_util_max": 1.0}, cpu={"throttle_delta": 4})
    assert verdict == "CPU_BOUND" and "throttle +4" in reason
    assert _diagnose(drops=CLEAN_DROPS, wave={"rx_missed": 0.0, "disk_util_max": 1.0}, cpu={"throttle_delta": 0})[0] == "NO_LIMIT_SEEN"


def test_a_drive_above_90_percent_or_behind_the_stream_is_disk_bound():
    assert _diagnose(drops=CLEAN_DROPS, wave={**CLEAN_WAVE, "disk_util_max": 91.0})[0] == "DISK_BOUND"
    assert _diagnose(drops=CLEAN_DROPS, wave={**CLEAN_WAVE, "disk_util_max": 90.0})[0] == "NO_LIMIT_SEEN"
    senses = {"rx": {"passes": 1, "read": 1, "busy": 17}}
    verdict, reason, _ = _diagnose(drops=CLEAN_DROPS, wave=CLEAN_WAVE, senses=senses)
    assert verdict == "DISK_BOUND" and "17" in reason


def test_a_stalled_pass_is_stalled_and_any_other_word_is_evidence_only():
    verdict, reason, evidence = _diagnose(drops={**CLEAN_DROPS, "pass_word": "stalled"}, wave=CLEAN_WAVE)
    assert verdict == "STALLED_PASS" and "pass_word=stalled" in evidence
    for word in ("complete", "ended", "no-stream"):
        assert _diagnose(drops={**CLEAN_DROPS, "pass_word": word}, wave=CLEAN_WAVE)[0] == "NO_LIMIT_SEEN", word


def test_several_signals_give_the_nearest_to_the_wire_and_all_are_in_the_reason():
    drops = {"rcvbuf": 5, "inerrors": 5, "abandoned": 1, "pass_word": "stalled"}
    wave = {"cpu_busy_min_core": 99.0, "rx_missed": 2.0, "disk_util_max": 99.0}
    verdict, reason, _ = _diagnose(drops=drops, wave=wave)
    assert verdict == "NIC_DROPS"
    assert [reason.index(v) for v in ("NIC_DROPS", "RX_DROPS", "CPU_BOUND", "DISK_BOUND", "STALLED_PASS")] == sorted(
        reason.index(v) for v in ("NIC_DROPS", "RX_DROPS", "CPU_BOUND", "DISK_BOUND", "STALLED_PASS"))
    assert _diagnose(drops={**CLEAN_DROPS, "rcvbuf": 5}, wave={**CLEAN_WAVE, "cpu_busy_min_core": 99.0})[0] == "RX_DROPS"
    assert _diagnose(drops=CLEAN_DROPS, wave={**CLEAN_WAVE, "cpu_busy_min_core": 99.0, "disk_util_max": 99.0})[0] == "CPU_BOUND"


def test_a_signal_with_a_sense_that_was_not_measured_still_names_what_was_not():
    verdict, reason, evidence = _diagnose(drops={"rcvbuf": 3}, wave={})
    assert verdict == "RX_DROPS" and "unmeasured=nic,cpu,disk" in evidence
    assert "לא נמדדו: הכרטיס, המעבד, הדיסק" in reason


def test_failed_http_downloads_add_a_sentence_and_no_verdict_of_their_own():
    senses = {"http": {"fetches": 4, "curl_failed": 2}}
    assert _diagnose(senses=senses)[0] == "NOT_MEASURED"
    verdict, reason, _ = _diagnose(drops=CLEAN_DROPS, wave=CLEAN_WAVE, senses=senses)
    assert verdict == "NO_LIMIT_SEEN" and "הורדות HTTP נכשלו: 2 מתוך 4" in reason
    assert "HTTP" not in _diagnose(drops=CLEAN_DROPS, wave=CLEAN_WAVE, senses={"http": {"fetches": 4, "curl_failed": 0}})[1]
    assert "HTTP" not in _diagnose(drops=CLEAN_DROPS, wave=CLEAN_WAVE, senses={"http": {"fetches": 4, "curl_failed": None}})[1]


@pytest.mark.parametrize("bad", [True, False, -1, "3", 1.5, [3], {"n": 3}])
def test_a_value_that_is_not_a_count_is_unmeasured_not_zero_and_not_a_signal(bad):
    verdict, reason, evidence = _diagnose(drops={"rcvbuf": bad, "inerrors": bad, "abandoned": bad, "rcvbuf_bytes": bad, "pass_word": bad})
    assert verdict == "NOT_MEASURED" and "rcvbuf=unmeasured" in evidence, (bad, verdict)


def test_a_cpu_or_a_wave_that_is_not_an_object_is_not_measured():
    assert _diagnose(drops=CLEAN_DROPS, wave={"cpu_busy_min_core": True, "rx_missed": -1.0, "disk_util_max": "9"}, cpu=[1])[0] == "NO_LIMIT_PARTIAL"


def test_the_console_has_a_hebrew_word_for_the_action_and_every_verdict():
    """`say.line_he` shows an unlabelled action or verdict as its code (`NIC_DROPS`): B59's eight verdicts and its action must have a word."""
    from server.brain import rxlimit, say
    verdicts = {rxlimit.NIC_DROPS, rxlimit.RX_DROPS, rxlimit.CPU_BOUND, rxlimit.DISK_BOUND, rxlimit.STALLED_PASS,
                rxlimit.NO_LIMIT_SEEN, rxlimit.NO_LIMIT_PARTIAL, rxlimit.NOT_MEASURED}
    assert rxlimit.ACTION in say.ACTION_HE and verdicts <= set(say.VERDICT_HE), (verdicts - set(say.VERDICT_HE))
    line = say.line_he({"entity_kind": "machine", "entity_key": MAC, "action": "RX_LIMIT", "verdict": "NIC_DROPS", "reason": "x", "mode": "shadow"})
    assert "NIC_DROPS" not in line and "RX_LIMIT" not in line, line


# --- through the report -----------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at, started_at) VALUES ('ses_w', 'grp_CLONERS', 'img', 'p', 1, 300,"
              " 'running', 't', ?, 0, ?)", (db.now_iso(), db.now_iso()))
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('ses_w', ?, 'writing', ?)",
              (MAC, db.now_iso()))
    c.commit()
    yield c
    veto.forget()


def _core(n, busy):
    return {"core": n, "busy": busy, "busy_max": busy, "irq": 1, "softirq": 10, "softirq_max": 20, "net_rx_ps": 1000, "net_rx_ps_max": 2000}


def _wave(busy=(40, 50), missed=0, util=30):
    return {"window_s": 5, "windows": 3, "cpu": [_core(i, b) for i, b in enumerate(busy)],
            "nic": {"iface": "eth0", "ethtool": True, "rx_missed": missed, "rx_no_buffer": 0, "ring_rx": 256, "ring_rx_max": 512},
            "disks": {"sda": {"util": util, "util_max": util, "inflight": 2, "inflight_max": 3, "w_ms": 2.0, "w_ms_max": 3.0}}}


def _senses(**rx):
    return {"carrier": None, "psi": None, "ctrl": None, "http": {"fetches": 3, "curl_failed": 1},
            "rx": {"passes": 2, "read": 2, "pkts": 100, "foreign": 0, "stray": None, "bad_pkt": 0, "dup": 0, "evicted": 0,
                   "busy": 0, "bad_chunk": 0, "good": 90, "skipped": 0, "conflict": 0, "version": 0, **rx}}


def _report(conn, state="done", **extra):
    assert reports.ingest(conn, {"session_id": "ses_w", "mac": MAC, "state": state, "targets": [], **extra})["ok"]


def _rows(conn):
    return list(reversed([d for d in memory.decisions(conn, rule="B59", limit=100) if d["action"] == "RX_LIMIT"]))


def test_a_terminal_report_records_what_limited_the_receiver_from_what_the_db_holds(conn):
    """(neg) the card missed 7 packets and the receiver gave up 120 chunks: the nearest to the wire wins, both are said."""
    _report(conn, udp_drops={"rcvbuf": 0, "inerrors": 0, "abandoned": 120, "rcvbuf_bytes": 425984, "pass_word": "complete"},
            wave_senses=_wave(missed=7), senses=_senses(), cpu={"busy_pct": 70, "iowait_pct": 3, "throttle_delta": 0, "temp_max_c": 55, "seconds": 300})
    [row] = _rows(conn)
    assert row["verdict"] == "NIC_DROPS" and "RX_DROPS" in row["reason"] and "120" in row["reason"], row
    assert "rx_missed=7" in row["evidence"] and "abandoned=120" in row["evidence"] and "rcvbuf_bytes=425984" in row["evidence"]
    assert "הורדות HTTP נכשלו: 1 מתוך 3" in row["reason"]
    assert row["entity_kind"] == "machine" and row["entity_key"] == MAC and row["round_id"] == "ses_w" and "צל" in (row["outcome"] or "")


def test_the_cpu_of_the_machine_is_read_from_the_wave_the_db_kept(conn):
    """(neg) every core above 85% in the last window, nothing lost: the wave's senses alone say CPU_BOUND."""
    _report(conn, udp_drops={"rcvbuf": 0, "inerrors": 0, "abandoned": 0}, wave_senses=_wave(busy=(92, 97)), senses=_senses())
    [row] = _rows(conn)
    assert row["verdict"] == "CPU_BOUND" and "cpu_busy_min_core=92" in row["evidence"], row


def test_a_throttled_cpu_comes_from_the_cpu_the_report_carried(conn):
    """(neg)"""
    _report(conn, udp_drops={"rcvbuf": 0, "inerrors": 0, "abandoned": 0}, wave_senses=_wave(), cpu={"throttle_delta": 6, "busy_pct": 80})
    assert [r["verdict"] for r in _rows(conn)] == ["CPU_BOUND"]


def test_a_progress_report_writes_no_row_and_the_terminal_one_writes_exactly_one(conn):
    """(neg) the row is the terminal transition's, once -- a second `done` is idempotent and adds nothing."""
    _report(conn, state="writing", udp_drops={"rcvbuf": 9, "inerrors": 9}, wave_senses=_wave(), senses=_senses())
    assert _rows(conn) == []
    _report(conn, udp_drops={"rcvbuf": 9, "inerrors": 9, "abandoned": 0}, wave_senses=_wave(), senses=_senses())
    _report(conn, udp_drops={"rcvbuf": 9, "inerrors": 9, "abandoned": 0}, wave_senses=_wave(), senses=_senses())
    assert [r["verdict"] for r in _rows(conn)] == ["RX_DROPS"]


def test_an_old_agent_that_sent_no_senses_is_recorded_as_not_measured(conn):
    """(neg) not a clean bill: the row exists and says so."""
    _report(conn)
    assert [r["verdict"] for r in _rows(conn)] == ["NOT_MEASURED"]


def test_the_brain_switched_off_records_nothing(conn):
    db.set_setting(conn, veto.SETTING, "off")
    _report(conn, udp_drops={"rcvbuf": 9, "inerrors": 9}, wave_senses=_wave(), senses=_senses())
    assert _rows(conn) == []


def test_a_corrupt_report_does_not_raise_and_is_not_measured(conn):
    """(neg) every field the wrong shape: the report is accepted and the row says nothing was measured."""
    _report(conn, udp_drops="garbage", wave_senses=5, senses="x", cpu=[1, 2])
    assert [r["verdict"] for r in _rows(conn)] == ["NOT_MEASURED"]


def test_a_table_that_cannot_be_read_is_not_measured_and_does_not_raise(conn):
    """(neg) the wave's table is gone: that sense is not measured, the rest of the verdict stands, the report is accepted."""
    conn.execute("DROP TABLE wave_senses")
    conn.commit()
    _report(conn, udp_drops={"rcvbuf": 4, "inerrors": 4, "abandoned": 0}, senses=_senses())
    [row] = _rows(conn)
    assert row["verdict"] == "RX_DROPS" and "cpu_busy_min_core=unmeasured" in row["evidence"], row


def test_the_decision_failing_does_not_fail_the_report(conn, monkeypatch):
    from server.brain import rxlimit
    monkeypatch.setattr(rxlimit, "diagnose", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rxlimit.note_final(conn, MAC, "ses_w", {}) is None
    _report(conn, udp_drops={"rcvbuf": 9, "inerrors": 9}, senses=_senses())
    assert _rows(conn) == []
