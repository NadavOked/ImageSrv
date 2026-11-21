"""#3525: the read ceiling of the library disk is learned from a wave in which the disk was SATURATED, not from a wave that was merely busy.

Metal 16/11 (`lab/metal-1116-r12`, LabRoot, `sdb`): K8 logged "the library disk is limited to 40 / 49 MB/s" (`WAVE_SERVER_CAP`, `CAP`) in all six
chunk waves and checked 669m against 318m / 394m. 40-49MB/s is what the new sender itself read (44-47MB/s at util 42-46%, asleep 68-71% of the
time for its pacing); udpcast read 62.6-62.9MB/s from the same disk at util 73%. Neither wave had the disk saturated. In `enforce` that number
would have been a lock: the sender reads slowly, the brain learns "the disk is slow", cuts, the sender reads slower.

`server_cap.learn` takes a wave only with positive evidence that the disk was saturated -- `util >= SATURATED_UTIL` next to `basis = server_disk`
(await >= 10ms, and for a chunk wave the sender waiting for the library file for more than `LIBRARY_WAIT_MAX` of its first pass, #3508/#3553). Without it: no ceiling (None), no `CAP` row.

**Negative control:** `server/learn/server_cap.py` back at the base -- `saturated` does not exist and the busy waves of the metal teach a ceiling
of 28-49MB/s; every test but the guards (marked) fails on behaviour (the guards pass in both states).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.learn import server_cap, server_io
from server.rounds import chunk_rate

MB = 10 ** 6
BASE = datetime(2026, 11, 16, 2, 0, tzinfo=timezone.utc)
WALL = 400.0
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _put(conn, read, util, *, basis="server_disk", device="sdb", await_ms=23.5) -> str:
    """A measured wave as `server_io.measure` wrote it: what the metal's table has per wave."""
    n = next(_seq)
    ref = f"ses_{n:05d}"
    stamp = (BASE + timedelta(minutes=n)).isoformat(timespec="seconds")
    result = {"device": device, "seconds": 640.0, "read_mbps": read, "write_mbps": 0.0, "util": util, "await_ms": await_ms,
              "psi_some": 0.0, "psi_full": 0.0, "basis": basis}
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis) VALUES ('wave', ?, ?, '{}', ?, ?, ?)",
                 (ref, stamp, stamp, json.dumps(result), basis))
    conn.commit()
    return ref


# --- the metal ----------------------------------------------------------------------------------------------------

UDPCAST = (62.6, 62.9, 62.9)                # a1-a3: util 73%, 1.54-1.66 deep, 23.3-23.8 ms a read
CHUNK = (44.2, 47.2, 45.0, 46.1, 46.8, 44.9)       # a1-b3: util 42-46%, 0.86-0.95 deep, 16.3-17.1 ms; the sender asleep 68-71%


def test_the_waves_of_the_metal_taught_a_ceiling_of_the_sender_and_now_teach_nothing(conn):
    """A window like the one of a1 (udpcast waves and chunk waves of the same `sdb`, all `server_disk` by the thresholds)."""
    for read in UDPCAST:
        _put(conn, read, 0.73)
    for read, util in zip(CHUNK, (0.42, 0.46, 0.44, 0.45, 0.43, 0.46)):
        _put(conn, read, util, await_ms=16.5)
    assert server_cap.learn(conn) is None


def test_with_no_ceiling_there_is_no_cap_row_and_the_rate_is_untouched(conn):
    """The six `CAP` rows of the metal: asked 669m against a "ceiling" of 318m. Now `_server_cap` writes nothing -- "not learned" is not "fits"."""
    for read in UDPCAST[:2] + CHUNK[:3]:
        _put(conn, read, 0.73 if read in UDPCAST else 0.44)
    db.set_setting(conn, server_cap.SETTING, "true")                 # even with the switch on: nothing to cut to
    assert chunk_rate._server_cap(conn, "ses_now", 669 * MB, 150 * MB) == 669 * MB
    assert conn.execute("SELECT 1 FROM brain_decisions WHERE action = 'WAVE_SERVER_CAP'").fetchall() == []


def test_a_disk_that_was_really_saturated_teaches_its_ceiling(conn):
    """Guard (both states): the evidence is still evidence. Two waves at util 0.93 / 0.97 reading 85 and 88MB/s: the ceiling is their median."""
    _put(conn, 85.0, 0.93)
    _put(conn, 88.0, 0.97)
    cap = server_cap.learn(conn)
    assert cap is not None and cap.read_mbps == 86.5 and cap.device == "sdb" and len(cap.waves) == 2
    assert cap.bps == int(86.5 * 8 * MB)


@pytest.mark.parametrize("util, learned", [
    (0.89, False),                      # just under
    (0.90, True),                       # exactly the threshold: counts
    (1.0, True),
    (None, False),                      # not measured is not saturated
    (0, False),
    ("0.95", False),                    # not a number
    (True, False),
])
def test_the_saturation_threshold_and_what_is_not_a_number(conn, util, learned):
    for _ in range(2):
        _put(conn, 80.0, util)
    assert (server_cap.learn(conn) is not None) is learned


def test_a_wave_without_a_util_field_is_not_evidence_of_saturation(conn):
    for _ in range(2):
        ref = _put(conn, 80.0, 0.95)
        row = json.loads(conn.execute("SELECT result_json FROM server_io WHERE ref = ?", (ref,)).fetchone()[0])
        del row["util"]
        conn.execute("UPDATE server_io SET result_json = ? WHERE ref = ?", (json.dumps(row), ref))
    conn.commit()
    assert server_cap.learn(conn) is None


def test_only_the_saturated_waves_of_the_window_make_the_median(conn):
    """Three saturated (90, 86, 88) and two busy ones (62, 63): the busy ones neither enter the median nor the waves it names."""
    refs = [_put(conn, 90.0, 0.95), _put(conn, 62.0, 0.73), _put(conn, 86.0, 0.92), _put(conn, 63.0, 0.70), _put(conn, 88.0, 0.99)]
    cap = server_cap.learn(conn)
    assert cap is not None and cap.read_mbps == 88.0 and cap.waves == (refs[4], refs[2], refs[0])


def test_the_last_wave_that_read_more_than_the_ceiling_without_saturating_the_disk_cancels_it(conn):
    """Evidence the ceiling is wrong -- as the `ok` of #3464 does: the disk gave 63MB/s while 27% idle. (Before #3525 the busy wave was
    part of the median; now it is the proof that 50 is not a ceiling.)"""
    _put(conn, 50.0, 0.95)
    _put(conn, 52.0, 0.96)
    assert server_cap.learn(conn).read_mbps == 51.0
    _put(conn, 62.9, 0.73)
    assert server_cap.learn(conn) is None


def test_the_last_wave_that_read_less_than_the_ceiling_does_not_move_it(conn):
    """A busy wave under the ceiling proves only "at least this" -- it is not in the median either."""
    _put(conn, 50.0, 0.95)
    _put(conn, 52.0, 0.96)
    _put(conn, 30.0, 0.73)
    assert server_cap.learn(conn).read_mbps == 51.0


# --- a wave, from `end` to the learning: the sender's own evidence (#3508) next to the saturation -------------------


def _snap(at, io=0, r=0, rios=0, ios_ms=0):
    return {"at": at, "device": "sdb", "read_sectors": r, "write_sectors": 0, "io_ms": io, "read_ios": rios, "write_ios": 0,
            "ios_ms": ios_ms, "psi_some_us": 0, "psi_full_us": 0}


def _end(conn, tmp_path, monkeypatch, util: float, read_mbps: float, waited: float | None):
    """A real chunk-wave end (`server_io.end`, `need_sender=True`): `util` busy over 400s, 20ms a request, `read_mbps` read, the sender waiting
    `waited` of the first pass for the library file (an inline reader's `read_ms`; None = no `sendstats`). The sender sleeps 74% of it for its
    pacing in every wave: since #3553 that says nothing about the library disk."""
    sid = f"ses_end{next(_seq)}"
    start = _snap(0)
    end = _snap(WALL, io=int(util * WALL * 1000), r=int(read_mbps * MB * WALL / 512), rios=10_000, ios_ms=200_000)
    monkeypatch.setattr(server_io, "snapshot", lambda root, proc=server_io.PROC: end)
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json) VALUES ('wave', ?, ?, ?)", (sid, db.now_iso(), json.dumps(start)))
    conn.commit()
    sender = {"first_pass_s": WALL, "paced_ms": {"wire": 0, "disk": int(WALL * 1000 * 0.74)},
              "sendstats": None if waited is None else {"read_ms": int(WALL * 1000 * waited), "readq_empty_ms": 0, "reader": "inline"}}
    got = server_io.end(conn, "wave", sid, tmp_path, seconds=WALL, sender=sender, need_sender=True)
    assert got is not None and got["read_mbps"] == pytest.approx(read_mbps, abs=0.1) and got["util"] == pytest.approx(util, abs=0.001)
    return got


def test_a_chunk_wave_the_sender_did_not_wait_for_teaches_no_ceiling_even_with_a_saturated_disk(conn, tmp_path, monkeypatch):
    """Guard (both states, #3508 meets #3525, #3553): the sender waited 5% of the first pass for the library file: `basis` is `ok` whatever the disk
    showed -- no ceiling, from any number of them. No `sendstats`: not decided."""
    for _ in range(3):
        got = _end(conn, tmp_path, monkeypatch, 0.95, 45.0, waited=0.05)
        assert got["basis"] == "ok" and got["looked"] == "server_disk"
    assert server_cap.learn(conn) is None
    for _ in range(3):
        got = _end(conn, tmp_path, monkeypatch, 0.95, 45.0, waited=None)
        assert got["basis"] is None and got["undecided"] == "sender_not_measured"
    assert server_cap.learn(conn) is None


def test_the_waves_of_the_metal_through_the_real_end_teach_nothing_and_a_saturating_sender_does(conn, tmp_path, monkeypatch):
    """Util 44%, the sender waited 40% for the file: `server_disk` by the evidence, and still no ceiling -- the disk was not saturated."""
    for _ in range(3):
        assert _end(conn, tmp_path, monkeypatch, 0.44, 45.0, waited=0.40)["basis"] == "server_disk"
    assert server_cap.learn(conn) is None
    for read in (84.0, 86.0):
        assert _end(conn, tmp_path, monkeypatch, 0.96, read, waited=0.40)["basis"] == "server_disk"
    cap = server_cap.learn(conn)
    assert cap is not None and cap.read_mbps == 85.0 and len(cap.waves) == 2
    assert "מוגבל ל-85 MB/s" in server_io.health_checks(conn)[0]["detail"]
