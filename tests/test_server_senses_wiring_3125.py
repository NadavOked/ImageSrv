"""#3125: the server's senses are wired into the real server -- a chunk wave gets its start reading, a window from the
clock, and at its end the whole wave in `result_json.server` of its `server_io` row; the long-term memory folds the
wave's row and reads its windows (#3185).

No import of `server.learn.server_senses` here, on purpose: these tests go through `server.app`, `housekeeping` and
`memory`, and fail on what is stored -- not on a module that is not there.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from test_server_room import room_server  # noqa: F401 -- shared fixture

from server import db
from server.brain import memory
from server.system import housekeeping

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def _wave_result(ctx, wave):
    row = ctx.conn.execute("SELECT result_json FROM server_io WHERE kind = 'wave' AND ref = ?", (wave,)).fetchone()
    return json.loads(row["result_json"]) if row is not None and row["result_json"] else None


def _windows(ctx, wave):
    return ctx.conn.execute("SELECT ref, start_json, result_json FROM server_io WHERE kind = 'window'"
                            " AND ref LIKE ? ORDER BY rowid", (f"{wave}/%",)).fetchall()


def test_a_chunk_wave_keeps_its_start_a_window_and_the_whole_wave_as_the_server_saw_it(room_server):
    from test_chunk_nic_gate_2533 import _sending, _stopped      # noqa: PLC0415
    from test_chunksend_2533 import Runner, wait_for             # noqa: PLC0415

    ctx, wave, process = _sending(room_server, Runner(block=True))
    try:
        rows = _windows(ctx, wave)
        assert [r["ref"] for r in rows] == [f"{wave}/0"], "wave_bitrate did not take the start reading"
        start = json.loads(rows[0]["start_json"])
        assert {"disk", "cpu_psi", "mem_psi", "load1", "proc", "nic", "http", "ledger"} <= set(start)
        ctx.senses.tick()                                    # what the clock does every WINDOW_S
        window = json.loads(_windows(ctx, wave)[1]["result_json"])
        assert window["engine"]["engine"] == "chunk" and window["engine"]["partition"] is not None
        assert window["downloads"] == 0 and window["http"] == {}      # measured: nothing was downloading
        ctx.chunk.stop(wave)
        assert _stopped(process)
        assert wait_for(lambda: "server" in (_wave_result(ctx, wave) or {})), _wave_result(ctx, wave)
    finally:
        ctx.chunk.stop()
    server = _wave_result(ctx, wave)["server"]
    assert server["windows"] == 2 and server["windows_dropped"] == 0
    assert {"server_cpu", "chunkcast_cpu", "nic_tx_mbps", "downloads", "load1"} <= set(server["peaks"])


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _old_wave(conn, ref, server):
    at = (NOW - timedelta(days=100)).isoformat()
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis)"
                 " VALUES ('wave', ?, ?, '{}', ?, ?, 'ok')",
                 (ref, at, at, json.dumps({"read_mbps": 40.0, "psi_full": 0.01, "basis": "ok", "server": server})))
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json)"
                 " VALUES ('window', ?, ?, '{}', ?, ?)", (f"{ref}/1", at, at, json.dumps({"server_cpu": 0.4})))
    conn.commit()


def test_the_wave_s_server_senses_are_summarised_for_ever_and_a_null_is_not_counted(conn):
    _old_wave(conn, "ses_a", {"server_cpu": 0.5, "nic_tx_mbps": None, "peaks": {"chunkcast_cpu": 0.9}})
    _old_wave(conn, "ses_b", {"server_cpu": 1.5, "nic_tx_mbps": 800.0, "peaks": {"chunkcast_cpu": 0.7}})
    housekeeping.purge_tables(conn, NOW)
    assert conn.execute("SELECT COUNT(*) FROM server_io").fetchone()[0] == 0       # out of the 90-day window
    stats = memory.summary(conn, "network", "server").get("server_io")
    assert stats is not None, "the wave rows left server_io without a summary"
    num = stats["num"]
    assert stats["rows"] == 2 and num["read_mbps"]["n"] == 2
    assert (num["server_cpu"]["n"], num["server_cpu"]["sum"]) == (2, 2.0)
    assert num["peak_chunkcast_cpu"]["max"] == 0.9
    # one wave measured the card and one did not: the null is not counted as a 0
    assert (num["nic_tx_mbps"]["n"], num["nic_tx_mbps"]["min"]) == (1, 800.0)


def test_the_memory_reads_a_wave_and_its_windows(conn):
    at = NOW.isoformat()
    for kind, ref in (("wave", "ses_c"), ("window", "ses_c/0"), ("window", "ses_c/1"), ("window", "ses_cc/0")):
        conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json) VALUES (?, ?, ?, '{}')",
                     (kind, ref, at))
    conn.commit()
    got = memory.knowledge(conn, "round", "ses_c")["recent"].get("server_io")
    assert got is not None and [r["ref"] for r in got] == ["ses_c", "ses_c/0", "ses_c/1"]
    assert [r["ref"] for r in memory.knowledge(conn, "network", "server")["recent"].get("server_io", [])] == ["ses_c"]
