"""#3041 (LR24): the brain learns each cloner's capacity by itself, from every wave -- and only from what was the MACHINE's.

Nadav 15/11 on #3041: "this should be part of the brain's learning." No "test capacity" button. After a wave the brain
asks who held it back -- the machine (lost packets, or every core above the learned CPU line, while the disks were idle)
or the disk -- and writes a capability signal only for the first. The highest rate with no loss, the CPU load, the card
and link speed and the date are kept for ever per MAC (`brain_memory`, source `capability`); a changed card or link speed
resets it. The learned capacity replaces "card x 90%" in the wave split (B48), the link warning (K3) and the wave ETA
(K3), in shadow: the rate of the wave itself is only recorded.

The numbers here are numbers for the test (a 900Mb/s wave, a 400Mb/s capacity), not measurements.

**Negative control:** with `server/app.py`, `server/agent/reports.py`, `server/brain/{linkwarn,waveeta,memory}.py` back
at the base of this PR and `server/brain/capability.py` removed, the tests marked (neg) fail on behaviour: no
`LR24` row is written when a cloner finishes a wave, and the wave split and the ETA still go by the card. The pure tests
and the ones that import `capability` fail there on the import -- that is NOT a negative control, and they are not counted.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.agent import reports
from server.brain import memory, veto
from server.rounds import nic_speed

MAC = "78:ac:c0:9b:18:85"
BPS = 900 * 10 ** 6


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    veto.forget()


# --- the pure judgement -------------------------------------------------------------------------------------------------


def _e(**kw):
    from server.brain import capability as C
    base = dict(stream_bps=BPS, rcvbuf=0, abandoned=0, rx_missed=0, rx_no_buffer=0, cpu_floor=40.0, disks_behind=False,
                achieved_bps=BPS)
    return C.Evidence(**{**base, **kw})


def test_lost_packets_with_idle_disks_are_the_machines():
    from server.brain import capability as C
    v = C.judge(_e(rcvbuf=40))
    assert v.verdict == C.LIMITED and v.lost and "rcvbuf=40" in v.reason


def test_lost_packets_while_the_disks_were_behind_teach_nothing():
    from server.brain import capability as C
    assert C.judge(_e(rcvbuf=40, disks_behind=True)).verdict == C.DISK_BOUND


def test_not_knowing_whether_the_disks_were_behind_is_not_decided():
    from server.brain import capability as C
    assert C.judge(_e(rcvbuf=40, disks_behind=None)).verdict == C.UNDECIDED


def test_every_core_above_the_line_is_the_machines_even_with_no_loss():
    from server.brain import capability as C
    v = C.judge(_e(cpu_floor=88.0))
    assert v.verdict == C.LIMITED and not v.lost
    assert C.judge(_e(cpu_floor=84.0)).verdict == C.CLEAN


def test_a_cpu_line_the_machine_taught_is_lower_than_the_start_and_never_below_the_floor():
    from server.brain import capability as C
    assert C.cpu_threshold({}) == C.CPU_START
    assert C.cpu_threshold({"num": {"cpu_at_loss": {"min": 70.0}}}) == 70.0
    assert C.cpu_threshold({"num": {"cpu_at_loss": {"min": 5.0}}}) == C.CPU_FLOOR
    assert C.judge(_e(cpu_floor=72.0), 70.0).verdict == C.LIMITED


def test_a_wave_that_was_not_fully_read_is_never_clean():
    """Principle 5: "nothing was lost" needs the kernel's counter read -- not its absence."""
    from server.brain import capability as C
    assert C.judge(_e(rcvbuf=None)).verdict == C.UNDECIDED
    assert C.judge(_e(stream_bps=None)).verdict == C.UNDECIDED
    assert C.judge(_e(cpu_floor=None)).verdict == C.CLEAN      # the CPU unread: it says so, the loss counter is the proof


# --- the memory --------------------------------------------------------------------------------------------------------


def test_remember_keeps_the_extremes_for_ever_and_a_value_not_read_is_not_counted(conn):
    assert memory.remember(conn, "machine", MAC, "capability", {"clean_bps": 5.0, "x": None}, {"iface": "eth0"},
                           at="2026-11-01T00:00:00+00:00")
    memory.remember(conn, "machine", MAC, "capability", {"clean_bps": 9.0}, at="2026-11-02T00:00:00+00:00")
    memory.remember(conn, "machine", MAC, "capability", {"clean_bps": 7.0}, at="2026-11-03T00:00:00+00:00")
    got = memory.summary(conn, "machine", MAC)["capability"]
    assert got["rows"] == 3 and got["num"]["clean_bps"]["max"] == 9.0 and got["num"]["clean_bps"]["last"] == 7.0
    assert "x" not in got["num"] and got["text"]["iface"]["value"] == "eth0"
    memory.remember(conn, "machine", MAC, "capability", {"clean_bps": 1.0}, replace=True)
    again = memory.summary(conn, "machine", MAC)["capability"]
    assert again["rows"] == 1 and again["num"]["clean_bps"]["max"] == 1.0 and "iface" not in again.get("text", {})


def test_remember_refuses_an_unreadable_summary_and_a_bad_kind_without_raising(conn):
    conn.execute("INSERT INTO brain_memory VALUES ('machine', 'aa', 'capability', 1, 'x', 'x', 'not json', 'x')")
    conn.commit()
    assert memory.remember(conn, "machine", "aa", "capability", {"clean_bps": 1.0}) is False
    assert conn.execute("SELECT stats_json FROM brain_memory WHERE key = 'aa'").fetchone()[0] == "not json"
    assert memory.remember(conn, "nothing", "aa", "capability", {"clean_bps": 1.0}) is False


# --- a wave, as the server sees it ------------------------------------------------------------------------------------


def _cpu(*peaks):
    return [{"core": i, "busy": p, "busy_max": p, "irq": 0, "softirq": 0, "softirq_max": 0, "net_rx_ps": 0,
             "net_rx_ps_max": 0} for i, p in enumerate(peaks)]


def _wave(conn, session, *, rcvbuf=0, busy=0, abandoned=0, cpu=(40, 40), util=30, iface="eth0", bps=BPS,
          state="done", drops=True, senses=True, achieved=-1, stream=None):
    """One cloner's final report of one chunk wave. ``None`` for a field = the agent did not send it. #3554: ``achieved`` = what the engine
    recorded the wave reached (`wave_rates.stream.chunk.achieved_bps`): the whole ask by default, ``None`` = no stream recorded.
    ``stream``: the raw `wave_rates.stream` instead (a udpcast wave's)."""
    # One open multicast round at a time (db.UNIQUE_ACTIVES, `one_active_broadcast`): the earlier wave is over. Without this
    # the second INSERT OR IGNORE below is dropped silently and the member row fails on its foreign key.
    conn.execute("UPDATE sessions SET state = 'closed' WHERE id != ? AND state IN ('open', 'running')", (session,))
    conn.execute("INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at, started_at) VALUES (?, 'grp_CLONERS', 'img', 'p', 1,"
                 " 300, 'running', 't', ?, 0, ?)", (session, db.now_iso(), db.now_iso()))
    conn.execute("INSERT OR IGNORE INTO session_members (session_id, mac, state, updated_at) VALUES (?, ?, 'writing', ?)",
                 (session, MAC, db.now_iso()))
    reached = bps if achieved == -1 else achieved
    conn.execute("INSERT OR REPLACE INTO wave_rates (session_id, decision, stream, at) VALUES (?, ?, ?, ?)",
                 (session, json.dumps({"max_bitrate": f"{bps // 10 ** 6}m"}),
                  stream if stream is not None else None if achieved is None
                  else json.dumps({"chunk": {"asked_bps": bps, "achieved_bps": reached}}), db.now_iso()))
    conn.commit()
    body = {"session_id": session, "mac": MAC, "state": state, "targets": []}
    if senses:
        body["wave_senses"] = {"window_s": 5, "windows": 3, "cpu": _cpu(*cpu),
                               "nic": {"iface": iface, "ethtool": True, "rx_missed": 0, "rx_no_buffer": 0,
                                       "ring_rx": 256, "ring_rx_max": 4096},
                               "disks": {"sda": {"util": util, "util_max": util, "inflight": 1, "inflight_max": 2,
                                                 "w_ms": 1.0, "w_ms_max": 2.0}}}
        body["senses"] = {"rx": {"passes": 1, "read": 1, "pkts": 9100, "foreign": 0, "stray": 0, "bad_pkt": 0, "dup": 0,
                                 "evicted": abandoned, "busy": busy, "bad_chunk": 0, "good": 800, "skipped": 0,
                                 "conflict": 0, "version": 0}}
    if drops:
        body["udp_drops"] = {"rcvbuf": rcvbuf, "inerrors": 0, "abandoned": abandoned}
    assert reports.ingest(conn, body)["ok"]


def _rows(conn, session=None):
    return [d for d in memory.decisions(conn, kind="machine", key=MAC, rule="LR24", limit=50, round_id=session)
            if d["action"] == "MACHINE_CAPABILITY"]


def _cap(conn):
    return memory.summary(conn, "machine", MAC).get("capability")


def test_a_cloner_that_lost_packets_with_idle_disks_teaches_its_capacity(conn):
    """(neg) The machine held the wave back: the signal is written -- a row in `brain_decisions` and the memory."""
    from server.brain import capability as C
    _wave(conn, "ses_a", rcvbuf=40, busy=0, util=30)
    [row] = _rows(conn, "ses_a")
    assert row["verdict"] == "LIMITED" and row["mode"] == "shadow" and "צל" in row["reason"], row
    assert "stream_bps=900000000" in row["evidence"] and "disks_behind=False" in row["evidence"], row["evidence"]
    cap = _cap(conn)
    assert cap["num"]["limited_bps"]["min"] == BPS and "clean_bps" not in cap["num"]
    assert C.learned(conn, MAC) == BPS, "the rate it lost at is the ceiling when nothing cleaner is known"


def test_a_slowdown_that_was_the_disks_teaches_nothing_about_the_cloner(conn):
    """(neg) The same lost packets, but the receiver says the disks were behind (`busy`): not the cloner's."""
    from server.brain import capability as C
    _wave(conn, "ses_a", rcvbuf=40, busy=12, util=95)
    [row] = _rows(conn, "ses_a")
    assert row["verdict"] == "DISK_BOUND" and "לא נלמד דבר" in row["outcome"], row
    assert _cap(conn) is None and C.learned(conn, MAC) is None


def test_a_wave_that_was_not_read_teaches_nothing_and_says_why(conn):
    """(neg) No kernel counter (a udpcast wave, or an old agent) and no senses: not "clean", not "limited"."""
    _wave(conn, "ses_a", drops=False, senses=False)
    [row] = _rows(conn, "ses_a")
    assert row["verdict"] == "UNDECIDED", row
    assert _cap(conn) is None


def test_the_highest_clean_rate_below_the_one_it_lost_at_is_the_capacity(conn):
    from server.brain import capability as C
    _wave(conn, "ses_1", bps=300 * 10 ** 6)
    _wave(conn, "ses_2", bps=400 * 10 ** 6, cpu=(55, 61))
    assert C.learned(conn, MAC) is None, "kept up at everything it was given: a floor, not a ceiling"
    _wave(conn, "ses_3", bps=500 * 10 ** 6)
    _wave(conn, "ses_4", bps=600 * 10 ** 6, rcvbuf=9)
    _wave(conn, "ses_5", bps=900 * 10 ** 6, rcvbuf=300)
    assert C.learned(conn, MAC) == 500 * 10 ** 6
    cap = _cap(conn)
    assert cap["num"]["clean_bps"]["max"] == 500 * 10 ** 6 and cap["num"]["limited_bps"]["min"] == 600 * 10 ** 6
    assert cap["num"]["cpu_at_best"]["last"] == 40 and cap["text"]["best_at"]["value"], "the load and the date of the best"
    assert cap["text"]["iface"]["value"] == "eth0"


def test_a_clean_wave_above_a_rate_it_lost_at_is_noise_and_changes_nothing_learned(conn):
    from server.brain import capability as C
    _wave(conn, "ses_1", bps=400 * 10 ** 6, rcvbuf=5)
    _wave(conn, "ses_2", bps=800 * 10 ** 6)
    assert C.learned(conn, MAC) is None


def test_a_cpu_line_taught_by_a_loss_makes_the_next_wave_at_that_load_the_machines(conn):
    _wave(conn, "ses_1", rcvbuf=9, cpu=(66, 70))                       # lost at 66%: this machine's line is 66
    _wave(conn, "ses_2", cpu=(67, 90))                                 # no loss, but every core is over it
    last = _rows(conn, "ses_2")[0]
    assert last["verdict"] == "LIMITED" and "cpu_threshold=66" in last["evidence"], last


def test_a_wave_that_did_not_end_done_teaches_nothing(conn):
    _wave(conn, "ses_a", rcvbuf=40, state="failed")
    assert _rows(conn) == [] and _cap(conn) is None


def test_the_brain_off_learns_nothing(conn):
    db.set_setting(conn, "brain_veto_mode", "off")
    _wave(conn, "ses_a", rcvbuf=40)
    assert _rows(conn) == [] and _cap(conn) is None


def test_a_changed_card_resets_what_was_learned_and_the_journal_says_so(conn):
    """(neg) Nadav: the learned capacity resets when the card is replaced -- and the brain learns it again."""
    from server.brain import capability as C
    _wave(conn, "ses_1", bps=500 * 10 ** 6)
    _wave(conn, "ses_2", bps=600 * 10 ** 6, rcvbuf=9)
    assert C.learned(conn, MAC) == 500 * 10 ** 6
    _wave(conn, "ses_3", bps=700 * 10 ** 6, iface="enp3s0")
    reset = [r for r in _rows(conn) if r["verdict"] == "RESET"]
    assert len(reset) == 1 and any(e.startswith("was_bps=500000000") for e in reset[0]["evidence"]), reset
    cap = _cap(conn)
    assert cap["num"]["clean_bps"]["max"] == 700 * 10 ** 6 and "limited_bps" not in cap["num"]
    assert cap["text"]["iface"]["value"] == "enp3s0"
    assert C.learned(conn, MAC) is None
    journal = conn.execute("SELECT detail, machine FROM journal WHERE event = 'capability_reset'").fetchall()
    assert len(journal) == 1 and "eth0 -> enp3s0" in journal[0]["detail"] and journal[0]["machine"] == MAC


def test_a_changed_link_speed_resets_too_but_an_assumed_one_never_does(conn):
    _wave(conn, "ses_1", rcvbuf=9)
    assert _cap(conn) is not None and "link_mbps" not in _cap(conn)["num"], "an assumed speed is not kept as a measurement"
    nic_speed.set_manual(conn, MAC, 1000)
    _wave(conn, "ses_2", rcvbuf=9)
    assert not [r for r in _rows(conn) if r["verdict"] == "RESET"], "from not-measured to measured is not a change"
    nic_speed.set_manual(conn, MAC, 2500)
    _wave(conn, "ses_3", rcvbuf=9)
    [reset] = [r for r in _rows(conn) if r["verdict"] == "RESET"]
    assert "1000 -> 2500Mb/s" in reset["reason"], reset
    assert _cap(conn)["num"]["link_mbps"]["last"] == 2500


def test_what_was_learned_survives_the_daily_cleanup(conn):
    from server.system import housekeeping
    _wave(conn, "ses_1", rcvbuf=9)
    housekeeping.purge_tables(conn)
    assert _cap(conn)["num"]["limited_bps"]["min"] == BPS


# --- the shadow decision about the wave's rate ---------------------------------------------------------------------------


def test_the_wave_rate_is_recorded_not_changed(conn):
    from server.brain import capability as C
    assert C.wave_rate(conn, "ses_w", BPS, [MAC]) is None
    memory.remember(conn, "machine", MAC, "capability", {"limited_bps": 600e6, "clean_bps": 400e6})
    assert C.wave_rate(conn, "ses_x", BPS, [MAC]) == 400 * 10 ** 6
    assert C.wave_rate(conn, "ses_y", 300 * 10 ** 6, [MAC]) is None
    rows = {d["round_id"]: d for d in memory.decisions(conn, rule="LR24", limit=20) if d["action"] == "WAVE_RATE_LEARNED"}
    assert rows["ses_w"]["verdict"] == "NOT_LEARNED"
    assert rows["ses_x"]["verdict"] == "WOULD_LOWER" and "צל" in rows["ses_x"]["reason"]
    assert rows["ses_y"]["verdict"] == "NO_CHANGE"
    assert all(r["mode"] == "shadow" for r in rows.values())


# --- K3: the link warning --------------------------------------------------------------------------------------------


def _nic(mbps, source="reported"):
    return {"speed_mbps": mbps, "source": source, "why": None}


def test_the_link_warning_goes_by_the_learned_capacity_when_there_is_one():
    from server.brain import linkwarn
    # 70Mb/s of learned capacity = a ~78Mb/s link (the 90% ceiling): slow, on a card that says 1Gb/s
    [f] = linkwarn.judge({"aa": _nic(1000), "bb": _nic(1000)}, {"aa": "A", "bb": "B"}, 10 ** 10, {"aa": 70_000_000})
    assert f.mac == "aa" and f.learned and f.mbps == 78 and f.verdict == linkwarn.EXCLUDE and f.faster == ("B",)
    assert "למד" in linkwarn.text_he(f) and "78Mb/s" in linkwarn.text_he(f)
    # a machine with no learned capacity keeps the card; one learned above its card does not change anything
    assert linkwarn.judge({"aa": _nic(1000)}, {}, 10 ** 10) == []
    assert linkwarn.judge({"aa": _nic(1000)}, {}, 10 ** 10, {"aa": 5_000_000_000}) == []
    [old] = linkwarn.judge({"aa": _nic(100)}, {}, 10 ** 10, {"aa": 5_000_000_000})
    assert not old.learned and old.mbps == 100


def test_a_learned_capacity_counts_as_measured_even_when_the_card_was_only_assumed():
    from server.brain import linkwarn
    assert linkwarn.judge({"aa": _nic(1000, "assumed")}, {}, 10 ** 10) == []
    [f] = linkwarn.judge({"aa": _nic(1000, "assumed")}, {}, 10 ** 10, {"aa": 70_000_000})
    assert f.learned and f.mbps == 78


# --- the wiring: the server, with a learned capacity --------------------------------------------------------------------

pytest.importorskip("fastapi")

from conftest import MANIFEST_256                                            # noqa: E402
from test_chunksend_2533 import CLONER1, _index_the_image, _room_wave, wait_for   # noqa: E402
from test_server_room import room_server  # noqa: E402,F401 -- shared fixture


def test_the_wave_split_the_eta_and_the_wave_rate_go_by_the_learned_capacity(room_server):
    """(neg) A cloner whose learned capacity is 1Mb/s: B48 sees it as a learned cap, the ETA is also predicted at it, and
    the wave's own rate is recorded as one the brain WOULD lower -- not lowered."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    memory.remember(ctx.conn, "machine", CLONER1, "capability", {"limited_bps": 2e6, "clean_bps": 1e6})
    _index_the_image(ctx, chunks=10)
    wave, _hello = _room_wave(room_server)
    part = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    told = anon.post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": part, "pass": 1, "state": "listening", "targets": [
            {"serial": "S1", "write_mbps": 400}, {"serial": "S2", "write_mbps": 400}]})
    assert told.status_code == 200 and told.json()["action"] == "receive", told.text

    def split():
        return [d for d in memory.decisions(ctx.conn, rule="B48", limit=50) if d["round_id"] == wave]

    assert wait_for(split, 10)
    assert any(e.startswith(f"cloner={CLONER1}:1Mb/s:learned:2") for e in split()[0]["evidence"]), split()[0]["evidence"]

    def eta():
        return [d for d in memory.decisions(ctx.conn, rule="K3", limit=50)
                if d["action"] == "WAVE_ETA" and d["round_id"] == wave]

    assert wait_for(eta, 10)
    assert "learned_stream_mbps=1" in eta()[0]["evidence"], eta()
    rate = [d for d in memory.decisions(ctx.conn, rule="LR24", limit=50) if d["action"] == "WAVE_RATE_LEARNED"]
    assert len(rate) == 1 and rate[0]["verdict"] == "WOULD_LOWER" and rate[0]["round_id"] == wave, rate
    assert "הגל יוצא כמו היום" in rate[0]["reason"]
    ctx.chunk.stop(wave)
