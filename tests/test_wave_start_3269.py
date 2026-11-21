"""#3269: a chunk wave opened at the floor (150m) from an old learned rate, on SSDs that wrote 179-209MB/s.

The spiral of 13-14/11 (brain-cases-internal-1114, case 11): 700m -> 525m -> 242m -> 150m, a round 10:44 -> 33 min.
A wave the server held back (PSI 46%) taught the drive memory "21MB/s" (`drive_rates.record_wave`, `_limit_of`), and
every wave after it started from that memory. Two things are tested here:

1. **Where a wave starts** (`chunk_rate.start`, after the write check of the wave's drawers): a learned start that is
   stale, from another cloner, from another number of drawers, or whose write check is far from today's, gives way to
   min(today's write check, the ceiling). A fresh and matching one is kept. Each decision is in `wave_rates.decision`
   (`start_check`) and in `brain_decisions` (K8); a changed rate is a `wave_rate` line in the journal.
2. **The server is not the drives' fault**: a wave the library disk held back (`server_io` basis `server_disk`: util and
   await of the disk the library sits on, #3365) teaches no network cut (`chunk_rate.record`) and no slow drive
   (`drive_rates.record_wave`), and says so (`unlearned`, with the reason). #3367: the system-wide PSI alone decides
   nothing -- a wave at PSI `some` 46% with an idle library disk teaches as usual.

**Negative control:** `server/rounds/chunk_rate.py`, `server/rounds/drive_rates.py`, `server/learn/server_io.py`,
`server/rounds/chunksend.py`, `server/app.py` and `server/console/journal_he.py` back to the base -- the start tests
fail (`AttributeError: start`), and so do the behavioural ones that count: the lossy wave under pressure cuts the next
one, the drive memory learns the 21MB/s, and the simulated spiral goes down to the floor.
No test here sleeps; the clock of `start` is a parameter.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6
MAC, OTHER = "aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22"
SAMSUNG = "SAMSUNG MZ7LN256"
NOW = datetime(2026, 11, 14, 20, 33, tzinfo=timezone.utc)
CHUNK = 4 * 1024 * 1024
CHUNKS = 5000


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _drive(serial: str, mac: str = MAC, port: int = 1) -> dict:
    return {"serial": serial, "model": SAMSUNG, "mac": mac, "port": port}


def _history(conn, hours_ago: float, roster=("D1", "D2")):
    """Two past waves of 242m on `MAC` (the roster), in which drive D1 wrote 21MB/s -- the memory of 13/11."""
    for sid in ("ses_past1", "ses_past2"):
        conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?, ?, ?)",
                     (sid, json.dumps({"max_bitrate": "242m", "basis": "climb",
                                       "roster": [{"mac": MAC, "serial": s, "model": SAMSUNG} for s in roster]}),
                      _iso(hours_ago)))
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     ("D1", SAMSUNG, sid, 21 * MB, 242_000_000 // 8, 21 * MB * 1000, 1000, _iso(hours_ago)))
    conn.commit()


def _now_wave(conn, drives) -> int:
    rate = drive_rates.decide(conn, "ses_now", lambda: drives, None)
    return drive_rates.parse_bitrate(rate)


def _start(conn, bits, probes) -> int:
    return chunk_rate.start(conn, "ses_now", bits, probes, now=NOW.timestamp())


def _brain(conn, verdict=None):
    # #3462: the shadow row of the learned network cap (`WAVE_NETCAP`) is K8 too, but it is not a decision about the rate
    rows = conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K8' AND action != 'WAVE_NETCAP' ORDER BY id").fetchall()
    return [r for r in rows if verdict is None or r["verdict"] == verdict]


def _cap(conn) -> int:
    return chunk_rate.cap_of(conn, drive_rates.decision(conn, "ses_now"))[0]


# --- 1. where the wave starts ----------------------------------------------------------------------------------


def test_the_spiral_of_14_11_a_stale_drive_memory_gives_way_to_the_write_check(conn):
    _history(conn, hours_ago=32)
    bits = _now_wave(conn, [_drive("D1"), _drive("D2", port=2)])
    assert drive_rates.decision(conn, "ses_now")["basis"] == "learned" and bits < 160 * MB   # the floor, as on metal
    got = _start(conn, bits, {(MAC, "D1"): 179, (MAC, "D2"): 209})
    assert _cap(conn) >= 600 * MB                             # the test means something: the ceiling is far above
    assert got == min(179 * 8 * MB, _cap(conn))
    decision = drive_rates.decision(conn, "ses_now")
    assert decision["basis"] == "probe" and drive_rates.parse_bitrate(decision["max_bitrate"]) == got
    check = decision["start_check"]
    assert check["was_basis"] == "learned" and {"stale", "probe"} <= set(check["why"])
    assert check["probe_mbps"] == 179 and check["from"]["session_id"] == "ses_past2"
    (line,) = [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'wave_rate' AND detail LIKE '%basis=probe%'")]
    assert "why=stale" in line and "was=" in line
    (row,) = _brain(conn, "PROBE")
    assert row["round_id"] == "ses_now" and "stale" in row["reason"]


def test_a_fresh_matching_learned_start_is_kept(conn):
    _history(conn, hours_ago=1)
    bits = _now_wave(conn, [_drive("D1"), _drive("D2", port=2)])
    assert _start(conn, bits, {(MAC, "D1"): 22, (MAC, "D2"): 23}) == bits
    decision = drive_rates.decision(conn, "ses_now")
    assert decision["basis"] == "learned" and decision["start_check"]["why"] == ["fresh_matching"]
    assert [r["verdict"] for r in _brain(conn)] == ["KEEP"]
    assert not conn.execute("SELECT 1 FROM journal WHERE detail LIKE '%basis=probe%'").fetchall()


def test_a_fresh_memory_far_from_todays_write_check_is_not_this_drive(conn):
    _history(conn, hours_ago=1)
    bits = _now_wave(conn, [_drive("D1"), _drive("D2", port=2)])
    got = _start(conn, bits, {(MAC, "D1"): 179, (MAC, "D2"): 209})
    assert got == min(179 * 8 * MB, _cap(conn))
    assert drive_rates.decision(conn, "ses_now")["start_check"]["why"] == ["probe"]


def test_the_drive_in_another_cloner_is_not_matching(conn):
    _history(conn, hours_ago=1)
    bits = _now_wave(conn, [_drive("D1", mac=OTHER), _drive("D2", mac=OTHER, port=2)])
    assert drive_rates.decision(conn, "ses_now")["basis"] == "learned"
    got = _start(conn, bits, {(OTHER, "D1"): 22, (OTHER, "D2"): 23})
    assert got == min(22 * 8 * MB, _cap(conn)) and got > bits
    assert drive_rates.decision(conn, "ses_now")["start_check"]["why"] == ["cloner"]


def test_another_number_of_drawers_is_not_matching(conn):
    _history(conn, hours_ago=1)
    bits = _now_wave(conn, [_drive("D1"), _drive("D2", port=2), _drive("D3", port=3)])
    assert drive_rates.decision(conn, "ses_now")["basis"] == "learned"
    got = _start(conn, bits, {(MAC, "D1"): 22, (MAC, "D2"): 23, (MAC, "D3"): 24})
    assert got == min(22 * 8 * MB, _cap(conn))
    assert drive_rates.decision(conn, "ses_now")["start_check"]["why"] == ["drawers"]


def test_a_drawer_without_a_write_check_keeps_the_rate_and_says_why(conn):
    _history(conn, hours_ago=32)
    bits = _now_wave(conn, [_drive("D1"), _drive("D2", port=2)])
    assert _start(conn, bits, {(MAC, "D1"): 179, (MAC, "D2"): None}) == bits
    decision = drive_rates.decision(conn, "ses_now")
    assert decision["basis"] == "learned" and decision["start_check"]["why"] == ["no_probe"]
    assert [r["verdict"] for r in _brain(conn)] == ["KEEP"]


def test_a_start_that_was_not_learned_is_not_touched(conn):
    bits = _now_wave(conn, [_drive("D1"), _drive("D2", port=2)])
    assert drive_rates.decision(conn, "ses_now")["basis"] == "start"
    assert _start(conn, bits, {(MAC, "D1"): 179, (MAC, "D2"): 209}) == bits
    assert drive_rates.decision(conn, "ses_now")["start_check"]["why"] == ["basis=start"]


def test_a_broken_start_keeps_the_rate_and_is_journaled(conn, monkeypatch):
    _history(conn, hours_ago=32)
    bits = _now_wave(conn, [_drive("D1"), _drive("D2", port=2)])

    def broken(*_a, **_kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(chunk_rate, "_evidence", broken)
    assert _start(conn, bits, {(MAC, "D1"): 179, (MAC, "D2"): 209}) == bits
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'chunk_rate_failed' AND detail LIKE '%start%'").fetchall()


# --- 2. the server is not the drives' fault ---------------------------------------------------------------------


def _pressure(conn, sid, held: bool = True, psi_some: float = 0.46):
    """The wave's `server_io` row: the library disk `held` the stream back (busy, 20ms a request: `server_disk`), or it
    was fine (`ok`) while the system-wide PSI `some` was `psi_some` (on LabRoot, the root disk's)."""
    basis = "server_disk" if held else "ok"
    result = {"util": 0.4 if held else 0.01, "await_ms": 20.0 if held else 0.5,
              "psi_some": psi_some, "psi_full": 0.01, "basis": basis}
    conn.execute("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis)"
                 " VALUES ('wave', ?, 'x', '{}', 'x', ?, ?)", (sid, json.dumps(result), basis))
    conn.commit()


def _chunk_wave(conn, sid, *, held=None, lost=0) -> int:
    rate = drive_rates.parse_bitrate(drive_rates.decide(
        conn, sid, lambda: [_drive("D1"), _drive("D2", mac=OTHER)], None,
        adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives)))
    if held is not None:
        _pressure(conn, sid, held)
    size = CHUNKS * CHUNK
    chunk_rate.record(conn, sid, {"chunks": CHUNKS, "bytes": size, "first_pass_s": size * 8 / rate,
                                  "lost_chunks": lost, "away": 0, "cloners": 2, "repair_rounds": 0,
                                  "ended": "send_done"})
    return rate


def _chunk_sample(conn, sid) -> dict:
    return json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?",
                                   (sid,)).fetchone()["stream"])["chunk"]


def test_a_lossy_wave_under_server_pressure_teaches_no_cut(conn):
    first = _chunk_wave(conn, "w1", held=True, lost=50)
    sample = _chunk_sample(conn, "w1")
    assert sample["teaches"] is False and sample["skip"] == "server_disk"
    assert _chunk_wave(conn, "w2") == first                  # nothing taught: today's rate, not a cut
    (row,) = _brain(conn, "UNLEARNED")
    assert row["round_id"] == "w1" and row["reason"] == "server_disk"


def test_a_healthy_library_disk_under_a_high_system_pressure_teaches_the_cut(conn):
    """#3367: LabRoot, 15/11 -- PSI `some` 46% from the root disk, the library disk idle. Not the library's fault."""
    first = _chunk_wave(conn, "w1", held=False, lost=50)
    assert _chunk_sample(conn, "w1")["teaches"] is True
    assert _chunk_wave(conn, "w2") < first
    assert not _brain(conn, "UNLEARNED")


def _member(targets, mac=MAC):
    return {"mac": mac, "targets_json": json.dumps(targets)}


def _done(serial, written):
    return {"dev": "sda", "state": "done", "serial": serial, "bytes_written": written,
            "first_moved_at": "2026-11-14T10:00:00+00:00", "moved_at": "2026-11-14T10:16:40+00:00"}


def test_a_drive_that_wrote_slowly_under_server_pressure_is_not_learned_slow(conn):
    drive_rates.decide(conn, "ses_w1", lambda: [_drive("D1")], "200m")
    _pressure(conn, "ses_w1")
    assert drive_rates.record_wave(conn, "ses_w1", [_member([_done("D1", 15_000 * MB)])]) == 0   # 15MB/s under 200m
    assert not conn.execute("SELECT 1 FROM drive_rates").fetchall()
    stream = json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = 'ses_w1'").fetchone()["stream"])
    assert stream["drives_unlearned"] == {"reason": "server_disk", "serials": ["D1"]}
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'drive_rates_unlearned'").fetchall()
    assert [r["verdict"] for r in _brain(conn)] == ["UNLEARNED"]


def test_a_slow_drive_under_a_high_system_pressure_but_a_healthy_library_disk_is_learned(conn):
    """#3367: the system-wide PSI alone no longer unlearns a drive: the library disk was fine, so the 15MB/s is the drive's."""
    drive_rates.decide(conn, "ses_w1", lambda: [_drive("D1")], "200m")
    _pressure(conn, "ses_w1", held=False)
    assert drive_rates.record_wave(conn, "ses_w1", [_member([_done("D1", 15_000 * MB)])]) == 1
    assert not conn.execute("SELECT 1 FROM journal WHERE event = 'drive_rates_unlearned'").fetchall()


def test_a_drive_that_kept_up_under_server_pressure_still_counts(conn):
    drive_rates.decide(conn, "ses_w1", lambda: [_drive("D1")], "200m")
    _pressure(conn, "ses_w1")
    assert drive_rates.record_wave(conn, "ses_w1", [_member([_done("D1", 24_000 * MB)])]) == 1   # 24MB/s: kept up


def test_a_simulated_spiral_stops(conn):
    """Four waves in a row, each held back by the library disk: the drives write what the server feeds them,
    21MB/s. Before #3269 the second held sample made the drive's memory 151m and the waves fell to the floor."""
    rates = []
    for n in range(4):
        sid = f"ses_s{n}"
        rate = drive_rates.parse_bitrate(drive_rates.decide(conn, sid, lambda: [_drive("D1")], None))
        rates.append(rate)
        _pressure(conn, sid)
        drive_rates.record_wave(conn, sid, [_member([_done("D1", min(rate // 8, 21 * MB) * 1000)])])
    assert rates[-1] >= rates[0] > 300 * MB, rates
    assert not conn.execute("SELECT 1 FROM drive_rates WHERE rate_bps < cap_bps * 0.8").fetchall()
