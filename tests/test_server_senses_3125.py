"""#3125 (V2, V6-V10, R8): the server's own senses in a wave -- one reading, a window between two, and the rows
`Senses` keeps next to the wave in `server_io`.

The heart of it is principle 5: **a sense that was not read is `null`, never 0** -- a `/proc` without pressure
files, a card that is not there, a counter that went down, a `chunkcast` that ended inside the window, a ledger row
whose start was not seen. Each has a test here, and the null reaches the database as null.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.brain.served import Served
from server.learn import server_io, server_senses as S

PID = 4242
HZ = 100


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _stat(pid, comm, ppid, utime, stime, cutime=0, cstime=0, start=0) -> str:
    # fields 1-2, then 3 (state) .. 22 (starttime): the parser counts from field 3
    rest = ["S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime), str(cutime), str(cstime)] + ["0"] * 4 + [str(start)]
    return f"{pid} ({comm}) " + " ".join(rest) + "\n"


def _proc(root: Path, *, cpu_some=None, mem=None, load=None, server=None, chunkcast=None, uptime=None) -> Path:
    """A fake /proc. `server` = (utime, stime, cutime, cstime); `chunkcast` = {pid: (ticks, starttime)}. None = the
    file is not there (not read)."""
    proc = root / "proc"
    if proc.exists():
        for p in sorted(proc.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    (proc / "pressure").mkdir(parents=True)
    if cpu_some is not None:
        (proc / "pressure" / "cpu").write_text(f"some avg10=0.00 avg60=0.00 avg300=0.00 total={cpu_some}\n")
    if mem is not None:
        (proc / "pressure" / "memory").write_text(f"some avg10=0.00 avg60=0.00 avg300=0.00 total={mem[0]}\n"
                                                  f"full avg10=0.00 avg60=0.00 avg300=0.00 total={mem[1]}\n")
    if load is not None:
        (proc / "loadavg").write_text(f"{load} 0.50 0.25 2/300 9999\n")
    if uptime is not None:
        (proc / "uptime").write_text(f"{uptime} 1.00\n")
    if server is not None:
        (proc / str(PID)).mkdir()
        (proc / str(PID) / "stat").write_text(_stat(PID, "python3", 1, *server))
        (proc / str(PID) / "comm").write_text("python3\n")
    for pid, (ticks, start) in (chunkcast or {}).items():
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "stat").write_text(_stat(pid, "chunkcast", PID, ticks, 0, start=start))
        (proc / str(pid) / "comm").write_text("chunkcast\n")
    return proc


def _nic(root: Path, tx_bytes=None, tx_dropped=None) -> Path:
    sys_net = root / "net"
    stats = sys_net / "eth1" / "statistics"
    stats.mkdir(parents=True, exist_ok=True)
    for name, value in (("tx_bytes", tx_bytes), ("tx_dropped", tx_dropped)):
        (stats / name).unlink(missing_ok=True)
        if value is not None:
            (stats / name).write_text(f"{value}\n")
    return sys_net


def _read(tmp_path, at, monkeypatch, **kw):
    sys_net = kw.pop("sys_net", tmp_path / "nonet")
    served = kw.pop("served", None)
    proc = _proc(tmp_path, **kw)
    monkeypatch.setattr(S.os, "sysconf", lambda name: HZ, raising=False)
    got = S.reading(root=tmp_path, interface="eth1", served=served, proc=proc, sys_net=sys_net, pid=PID)
    got["at"] = at
    return got


# --- one window ----------------------------------------------------------------------------------------------------


def test_a_window_measures_the_cpu_and_memory_pressure_the_load_the_card_and_the_processes(tmp_path, monkeypatch):
    """10 s: CPU pressure 2.5 s (0.25), memory some 1 s / full 0.5 s, the server 1.5 cores, its reaped children 0.02,
    one chunkcast that ran all of it at 0.8 cores, and 125 MB out of the card (100 Mbps) with 3 drops."""
    net = _nic(tmp_path, 0, 7)
    a = _read(tmp_path, 1000.0, monkeypatch, cpu_some=0, mem=(0, 0), load=1.0, server=(100, 0, 10, 0),
              chunkcast={77: (50, 100)}, uptime=500.0, sys_net=net)
    _nic(tmp_path, 125_000_000, 10)
    b = _read(tmp_path, 1010.0, monkeypatch, cpu_some=2_500_000, mem=(1_000_000, 500_000), load=3.5,
              server=(1100, 500, 30, 0), chunkcast={77: (850, 100)}, uptime=510.0, sys_net=net)
    got = S.window(a, b)
    assert (got["psi_cpu_some"], got["psi_mem_some"], got["psi_mem_full"], got["load1"]) == (0.25, 0.1, 0.05, 3.5)
    assert (got["server_cpu"], got["children_cpu"], got["chunkcast_cpu"], got["chunkcast"]) == (1.5, 0.02, 0.8, 1)
    assert (got["nic_tx_mbps"], got["nic_tx_dropped"], got["seconds"]) == (100.0, 3, 10.0)


def test_a_sense_that_was_not_read_is_null_not_zero(tmp_path, monkeypatch):
    """Principle 5: no pressure files, no loadavg, no stat of the server, no card -- every one of them null."""
    a = _read(tmp_path, 1000.0, monkeypatch)
    b = _read(tmp_path, 1010.0, monkeypatch)
    got = S.window(a, b)
    for key in ("psi_cpu_some", "psi_mem_some", "psi_mem_full", "load1", "server_cpu", "children_cpu",
                "nic_tx_mbps", "nic_tx_dropped", "http", "downloads", "http_mbps", "ledger"):
        assert got[key] is None, (key, got[key])
    assert got["chunkcast"] == 0 and got["chunkcast_cpu"] is None    # none running; the uptime was not read


def test_a_counter_that_went_down_is_not_measured(tmp_path, monkeypatch):
    """A card reset (tx_bytes smaller than before) is not "nothing was sent" and not a negative rate."""
    net = _nic(tmp_path, 9_000_000, 5)
    a = _read(tmp_path, 1000.0, monkeypatch, sys_net=net)
    _nic(tmp_path, 1_000, 0)
    b = _read(tmp_path, 1010.0, monkeypatch, sys_net=net)
    got = S.window(a, b)
    assert got["nic_tx_mbps"] is None and got["nic_tx_dropped"] is None


def test_a_chunkcast_that_ended_inside_the_window_is_not_measured_and_one_that_started_counts_from_zero(
        tmp_path, monkeypatch):
    a = _read(tmp_path, 1000.0, monkeypatch, server=(0, 0), chunkcast={77: (100, 100)}, uptime=500.0)
    ended = _read(tmp_path, 1010.0, monkeypatch, server=(0, 0), chunkcast={}, uptime=510.0)
    assert S.window(a, ended)["chunkcast_cpu"] is None              # its last seconds were not seen
    began = _read(tmp_path, 1010.0, monkeypatch, server=(0, 0), chunkcast={77: (300, 100), 78: (400, 50_300)},
                  uptime=510.0)                                     # 78 started at 503 s, inside the window
    assert S.window(a, began)["chunkcast_cpu"] == 0.6               # (200 + 400) ticks / 100 Hz / 10 s


def test_the_http_rate_of_each_address_and_the_downloads_open_now(tmp_path, monkeypatch):
    """V6: 25 MB to one cloner in 10 s (20 Mbps), two of its downloads open; another cloner first seen inside the
    window had 0 before it."""
    served = Served(clock=lambda: 0.0)
    served.note("10.0.0.5", 1_000)
    a = _read(tmp_path, 1000.0, monkeypatch, served=served)
    served.note("10.0.0.5", 25_000_000)
    served.note("10.0.0.6", 1_250_000)
    served.opened("10.0.0.5")
    served.opened("10.0.0.5")
    served.opened("10.0.0.6")
    served.closed("10.0.0.6")
    b = _read(tmp_path, 1010.0, monkeypatch, served=served)
    got = S.window(a, b)
    assert got["http"] == {"10.0.0.5": {"mbps": 20.0, "active": 2}, "10.0.0.6": {"mbps": 1.0, "active": 0}}
    assert (got["downloads"], got["http_mbps"]) == (2, 21.0)


def test_a_closed_download_leaves_the_count_and_never_goes_below_zero():
    served = Served()
    served.opened("10.0.0.5")
    served.closed("10.0.0.5")
    served.closed("10.0.0.5")
    assert served.snapshot()["active"] == {}


def _ledger(conn, mac, part, ranges, serial="S1"):
    conn.execute("INSERT OR REPLACE INTO chunk_ledger (serial, size_bytes, part, round_id, mac, image_id, index_xxh64,"
                 " chunks, ranges_json, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (serial, 1000, part, "ses_1", mac, "img", "0" * 16, 1000, json.dumps(ranges), db.now_iso()))
    conn.commit()


def test_each_cloner_s_durable_rate_from_the_ledger_and_a_row_whose_start_was_not_seen(conn):
    """V7: 50 chunks durable in 10 s = 21 MB/s. A row that appeared inside the window (adopted from another round,
    perhaps) has no measured start: that cloner's rate is null, not a burst."""
    _ledger(conn, "aa", 3, [[0, 99]])
    a = {"at": 1000.0, "ledger": S.durable(conn, "ses_1")}
    _ledger(conn, "aa", 3, [[0, 149]])
    _ledger(conn, "bb", 3, [[0, 499]], serial="S2")
    b = {"at": 1010.0, "ledger": S.durable(conn, "ses_1")}
    got = S._ledger(a["ledger"], b["ledger"], 10.0)
    assert got["aa"] == {"chunks": 50, "mbps": 21.0}
    assert got["bb"] == {"chunks": None, "mbps": None}


# --- the rows next to the wave ---------------------------------------------------------------------------------------


def _wave_row(conn, ref="ses_1"):
    server_io.begin(conn, "wave", ref, ".")
    conn.execute("UPDATE server_io SET result_json = ?, ended_at = ? WHERE kind = 'wave' AND ref = ?",
                 (json.dumps({"read_mbps": 50.0, "basis": "ok"}), db.now_iso(), ref))
    conn.commit()


def _readings(*readings):
    it = iter(readings)
    return lambda session_id: next(it)


def _bare(at, server=None):
    return {"at": at, "disk": None, "cpu_psi": {"some": None, "full": None}, "mem_psi": {"some": None, "full": None},
            "load1": None, "proc": {"hz": HZ, "uptime": None, "server": server, "children": None, "chunkcast": None},
            "nic": {"tx_bytes": None, "tx_dropped": None}, "http": None, "ledger": None, "engine": None}


def test_the_start_every_window_and_the_end_are_kept_next_to_the_wave_and_unread_stays_null(conn):
    _wave_row(conn)
    senses = S.Senses(conn, _readings(_bare(0.0, 0), _bare(30.0, 3000), _bare(60.0, None)), lambda: "ses_1")
    senses.begin("ses_1")
    senses.tick()
    summary = senses.end("ses_1")
    rows = conn.execute("SELECT ref, result_json FROM server_io WHERE kind = 'window' ORDER BY ref").fetchall()
    assert [r["ref"] for r in rows] == ["ses_1/0", "ses_1/1", "ses_1/2"]
    assert rows[0]["result_json"] is None                            # the start: a reading, no window yet
    assert json.loads(rows[1]["result_json"])["server_cpu"] == 1.0
    last = json.loads(rows[2]["result_json"])
    assert "server_cpu" in last and last["server_cpu"] is None         # stored as null, not 0 (principle 5)
    result = json.loads(conn.execute("SELECT result_json FROM server_io WHERE kind = 'wave'").fetchone()[0])
    assert result["read_mbps"] == 50.0 and result["server"] == summary
    assert (summary["windows"], summary["windows_dropped"]) == (2, 0)
    assert summary["peaks"]["server_cpu"] == 1.0 and summary["peaks"]["nic_tx_mbps"] is None
    assert summary["server_cpu"] is None                              # the last reading did not read it


def test_a_wave_whose_start_was_not_read_gets_no_windows_and_the_windows_have_a_ceiling(conn):
    _wave_row(conn)
    senses = S.Senses(conn, _readings(*[_bare(30.0 * n, n) for n in range(6)]), lambda: "ses_1", max_windows=2)
    senses.tick()                                                     # no `begin`: nothing is claimed
    assert conn.execute("SELECT COUNT(*) FROM server_io WHERE kind = 'window'").fetchone()[0] == 0
    senses.begin("ses_1")
    senses.tick()
    senses.tick()
    senses.tick()
    summary = senses.end("ses_1")
    assert conn.execute("SELECT COUNT(*) FROM server_io WHERE kind = 'window'").fetchone()[0] == 3   # 0, 1, 2
    assert (summary["windows"], summary["windows_dropped"]) == (2, 2)


def test_a_sense_that_raises_is_a_log_line_not_a_wave_that_stops(conn):
    def broken(session_id):
        raise OSError("boom")
    senses = S.Senses(conn, broken, lambda: "ses_1")
    senses.begin("ses_1")
    senses.tick()
    assert senses.end("ses_1") is None
