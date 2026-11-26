"""#3125 (V2, V6-V10, R8): what the server itself sees while a wave runs -- at its start, in timed windows, and at
its end. Measurement only: nothing here decides or holds anything back. The one reader is `server/brain/sendlimit.py` (B58,
shadow): at the end of a wave it reads the summary and records what limited the rate -- a row, no action.

**What one reading holds** (`reading`), all from the server's own kernel, read only:

* V2 -- the library disk and the IO pressure: `server_io.snapshot` (the same reading `server_io.begin` takes).
* V8 -- the CPU of the server process (uvicorn and every thread of it), of its reaped children, and of every live
  `chunkcast` that the server started (`/proc/<pid>/stat`, utime + stime).
* V9 -- the card of the wave: `tx_bytes`, `tx_dropped` (`netsense.nic_counters`).
* V16 (#3064, K12) -- the CPU of the whole unit: `usage_usec` of its cgroup (`cpucap.unit_usage_usec`). V8 sees a child only
  once it is reaped; the cgroup sees it while it runs. `null` outside a cgroup (development, Windows).
* V10 -- PSI of the CPU (`some`) and of memory (`some`, `full`), and `/proc/loadavg`.
* V4, V5 -- the sender of the live pass: its counters and its place in the stream (chunk k of N), from the log of
  `chunkcast send` (`sender_senses`).
* V6 -- the bytes the image-file HTTP served to each address, and how many of those downloads are open right now
  (`Served.snapshot`).
* V7 -- what each cloner has durable in `chunk_ledger` for the wave, by ledger row.

**A window** (`window`) is two readings apart. **Every field is a number that was read, or `null` = not measured --
never 0** (principle 5): a counter that was not read in one of the two readings, that went down (a card reset, a
process that left), or a `chunkcast` that ended inside the window (its last seconds were not seen) is `null`. A
ledger row that appeared inside the window has no measured start (it may have been adopted from an earlier round),
so its cloner's rate in that window is `null`.

**Where it is kept:** next to the wave, in `server_io` -- the window rows are `kind = "window"`, `ref = "<wave>/<n>"`
(`n = 0` is the reading at the start, `result_json` NULL), and at the end the wave's own row (`kind = "wave"`) gets
`result_json.server`: the whole wave (first reading to last), the highest value of each field in any window
(`peaks`), and how many windows were taken and dropped. The long-term memory folds the wave rows (`memory.SOURCES`,
`network/server`) and reads both kinds (`memory.knowledge`) -- no table of its own, no schema change.

Never raises: a sense that failed is a log line and `null`s, not a wave that stopped.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from server.brain import cpucap, netsense
from server.core.logtext import one_line
from server.db import _settle, _write_lock, now_iso, writing
from server.learn import sender_senses, server_io
from server.rounds import chunkledger

log = logging.getLogger("imagesrv.server_senses")

PROC = Path("/proc")
KIND = "window"
#: one window every this many seconds while a wave is live. An estimate: a few windows a minute show a stall that a
#: start/end pair hides, and 120 rows an hour is small.
WINDOW_S = 30.0
#: the windows kept a wave (4 hours at `WINDOW_S`); beyond them a window is counted as dropped, not written.
MAX_WINDOWS = 480
CHUNKCAST = "chunkcast"
CHUNK_BYTES = 4 * 1024 * 1024
#: the window fields whose highest value in the wave goes into the wave's summary (`result_json.server.peaks`)
PEAKS = ("server_cpu", "chunkcast_cpu", "children_cpu", "unit_cpu", "psi_cpu_some", "psi_mem_some", "psi_mem_full", "load1",
         "nic_tx_mbps", "downloads", "http_mbps", "disk_util", "disk_psi_full", "sent_mbps")
#: the window fields that belong to one window and not to the wave as a whole (`result_json.server` does not have them)
WINDOW_ONLY = ("engine", "http", "ledger", "disk", "disk_util", "disk_psi_full", "sender", "sent_mbps")


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None


def _psi(proc: Path, name: str) -> dict:
    out: dict[str, int | None] = {"some": None, "full": None}
    for line in (_text(proc / "pressure" / name) or "").splitlines():
        kind, *fields = line.split() or [""]
        total = next((f.split("=", 1)[1] for f in fields if f.startswith("total=")), None)
        if kind in out and total is not None and total.isdigit():
            out[kind] = int(total)
    return out


def _loadavg(proc: Path) -> float | None:
    fields = (_text(proc / "loadavg") or "").split()
    try:
        return float(fields[0])
    except (IndexError, ValueError):
        return None


def _stat(proc: Path, pid: int | str) -> list[str] | None:
    """The fields of `/proc/<pid>/stat` after the name: [0] = field 3 (state). None = not read."""
    text = _text(proc / str(pid) / "stat")
    if not text or ")" not in text:
        return None
    fields = text.rsplit(")", 1)[1].split()
    return fields if len(fields) >= 20 and all(fields[i].lstrip("-").isdigit() for i in (1, 11, 12, 13, 14, 19)) \
        else None


def processes(proc: Path = PROC, pid: int | None = None) -> dict:
    """V8: clock ticks of the server process (utime+stime), of its reaped children (cutime+cstime), and of each live
    `chunkcast` it started ({pid: [starttime, ticks]}). Each is None when it was not read."""
    pid = os.getpid() if pid is None else pid
    own = _stat(proc, pid)
    try:
        hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    except (ValueError, OSError):
        hz = 100
    uptime = (_text(proc / "uptime") or "").split()
    out: dict[str, float | int | dict[str, list[int]] | None] = {"hz": hz, "uptime": float(uptime[0]) if uptime and uptime[0].replace(".", "", 1).isdigit() else None,
           "server": int(own[11]) + int(own[12]) if own else None,
           "children": int(own[13]) + int(own[14]) if own else None, "chunkcast": None}
    try:
        entries = [e for e in proc.iterdir() if e.name.isdigit()]
    except OSError:
        return out                                  # the list of processes was not read: "not measured"
    found: dict[str, list[int]] = {}
    for entry in entries:
        if (_text(entry / "comm") or "").strip() != CHUNKCAST:
            continue
        st = _stat(proc, entry.name)
        if st is not None and int(st[1]) == pid:    # ours: started by this server (nice/ionice exec in place)
            found[entry.name] = [int(st[19]), int(st[11]) + int(st[12])]
    out["chunkcast"] = found
    return out


def durable(conn, session_id: str) -> dict | None:
    """V7: {mac: {"<serial>/<size>/<part>": durable chunks}} of the wave in `chunk_ledger`. None = not read."""
    try:
        rows = conn.execute("SELECT mac, serial, size_bytes, part, chunks, ranges_json FROM chunk_ledger"
                            " WHERE round_id = ?", (session_id,)).fetchall()
    except Exception:                               # noqa: BLE001 -- a sense that failed is "not read"
        log.exception("server senses: the ledger of %s was not read", one_line(session_id))
        return None
    out: dict[str, dict] = {}
    for row in rows:
        try:
            ranges = chunkledger.well_formed(json.loads(row["ranges_json"]), row["chunks"])
            got = sum(hi - lo + 1 for lo, hi in ranges)
        except (TypeError, ValueError, chunkledger.LedgerProblem):
            got = None
        out.setdefault(row["mac"], {})[f"{row['serial']}/{row['size_bytes']}/{row['part']}"] = got
    return out


def reading(*, root: str | Path, interface: str | None, served=None, conn=None, session_id: str | None = None,
            engine: Callable[[], dict | None] | None = None, proc: Path = PROC, sys_net: Path = netsense.SYS_NET,
            pid: int | None = None, cgroup: Path = cpucap.CGROUP) -> dict:
    """One reading of every sense (see the head of the module). Never raises: a sense that failed is None."""
    def safe(fn, what):
        try:
            return fn()
        except Exception:                           # noqa: BLE001
            log.exception("server senses: %s was not read", what)
            return None
    state = safe(engine, "the engine's state") if engine is not None else None
    procs = safe(lambda: processes(proc, pid), "the processes")
    return {"at": time.time(),
            "disk": safe(lambda: server_io.snapshot(root, proc), "the library disk"),
            "cpu_psi": safe(lambda: _psi(proc, "cpu"), "the CPU pressure"),
            "mem_psi": safe(lambda: _psi(proc, "memory"), "the memory pressure"),
            "load1": safe(lambda: _loadavg(proc), "the load average"),
            "proc": procs,
            "unit_us": safe(lambda: cpucap.unit_usage_usec(proc, cgroup), "the unit's CPU"),
            "sender": safe(lambda: sender_senses.read(proc, procs), "the senders' logs"),
            "nic": safe(lambda: netsense.nic_counters(interface, sys_net), "the card"),
            "http": safe(served.snapshot, "the HTTP counter") if served is not None else None,
            "ledger": durable(conn, session_id) if conn is not None and session_id else None,
            "engine": ({k: state.get(k) for k in ("engine", "partition", "round", "state")}
                       if isinstance(state, dict) else None)}


def _delta(a, b) -> int | None:
    ok = all(isinstance(v, int) and not isinstance(v, bool) for v in (a, b))
    return b - a if ok and b >= a else None


def _get(d, *keys):
    for k in keys:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _chunkcast_ticks(a: dict | None, b: dict | None) -> int | None:
    """The ticks every `chunkcast` spent inside the window -- None when one of them ended inside it (its last part was
    not seen), or when one in `b` was already running at `a` and `a` did not see it."""
    if not isinstance(a, dict) or not isinstance(b, dict) or not isinstance(a.get("chunkcast"), dict) \
            or not isinstance(b.get("chunkcast"), dict) or a.get("uptime") is None:
        return None
    before, after, hz = a["chunkcast"], b["chunkcast"], b["hz"]
    if any(p not in after or after[p][0] != before[p][0] for p in before):
        return None
    total = 0
    for p, (start, ticks) in after.items():
        if p in before:
            total += ticks - before[p][1]
        elif start / hz >= a["uptime"] - 1:         # started inside the window: all of it is in the window
            total += ticks
        else:
            return None
    return total


def window(a: dict, b: dict) -> dict:
    """Two readings -> what the window measured (see the head of the module). None = not measured."""
    dt = b["at"] - a["at"]
    ok = dt > 0

    def rate(ticks):                                # cores busy: 1.0 = one core all the window
        return round(ticks / b["proc"]["hz"] / dt, 3) if ok and ticks is not None else None

    def frac(us):
        return round(min(1.0, us / 1e6 / dt), 3) if ok and us is not None else None

    pa, pb = a.get("proc"), b.get("proc")
    disk = server_io.measure(a["disk"], b["disk"]) if a.get("disk") and b.get("disk") else None
    tx = _delta(_get(a, "nic", "tx_bytes"), _get(b, "nic", "tx_bytes"))
    unit_ticks = _delta(a.get("unit_us"), b.get("unit_us"))
    chunkcast = _get(pb, "chunkcast")
    out = {"seconds": round(dt, 1) if ok else None,
           "server_cpu": rate(_delta(_get(pa, "server"), _get(pb, "server"))),
           "children_cpu": rate(_delta(_get(pa, "children"), _get(pb, "children"))),
           "chunkcast_cpu": rate(_chunkcast_ticks(pa, pb)),
           "unit_cpu": None if not ok or unit_ticks is None else round(unit_ticks / 1e6 / dt, 3),
           "chunkcast": len(chunkcast) if isinstance(chunkcast, dict) else None,
           "psi_cpu_some": frac(_delta(_get(a, "cpu_psi", "some"), _get(b, "cpu_psi", "some"))),
           "psi_mem_some": frac(_delta(_get(a, "mem_psi", "some"), _get(b, "mem_psi", "some"))),
           "psi_mem_full": frac(_delta(_get(a, "mem_psi", "full"), _get(b, "mem_psi", "full"))),
           "load1": b.get("load1"),
           "nic_tx_mbps": round(tx * 8 / dt / 1e6, 1) if ok and tx is not None else None,
           "nic_tx_dropped": _delta(_get(a, "nic", "tx_dropped"), _get(b, "nic", "tx_dropped")),
           "disk": disk, "disk_util": _get(disk, "util"), "disk_psi_full": _get(disk, "psi_full"),
           "engine": b.get("engine")}
    out["sender"] = sender_senses.step(a.get("sender"), b.get("sender"), dt) if ok else None
    out["sent_mbps"] = sender_senses.mbps(out["sender"])
    out.update(_http(a.get("http"), b.get("http"), dt))
    out["ledger"] = _ledger(a.get("ledger"), b.get("ledger"), dt)
    return out


def _http(a: dict | None, b: dict | None, dt: float) -> dict:
    """V6: per address, the Mbps served in the window and the downloads open at its end."""
    if not isinstance(a, dict) or not isinstance(b, dict) or dt <= 0:
        return {"http": None, "downloads": None, "http_mbps": None}
    per = {}
    total: float | None = 0.0
    for ip in sorted(set(b["bytes"]) | set(b["active"])):
        # an address first seen inside the window had 0 before it (`Served` counts from the first byte)
        got = _delta(a["bytes"].get(ip, 0), b["bytes"].get(ip, 0))
        mbps = round(got * 8 / dt / 1e6, 1) if got is not None else None
        per[ip] = {"mbps": mbps, "active": b["active"].get(ip, 0)}
        total = None if total is None or mbps is None else total + mbps
    return {"http": per, "downloads": sum(b["active"].values()),
            "http_mbps": round(total, 1) if total is not None else None}


def _ledger(a: dict | None, b: dict | None, dt: float) -> dict | None:
    """V7: per cloner, the chunks that became durable in the window and the MB/s they are (4 MiB a chunk)."""
    if not isinstance(a, dict) or not isinstance(b, dict) or dt <= 0:
        return None
    out = {}
    for mac, rows in b.items():
        before = a.get(mac, {})
        steps = [_delta(before.get(key), got) if key in before else None for key, got in rows.items()]
        counted = [s for s in steps if s is not None]
        chunks = sum(counted) if steps and len(counted) == len(steps) else None
        out[mac] = {"chunks": chunks,
                    "mbps": round(chunks * CHUNK_BYTES / dt / 1e6, 1) if chunks is not None else None}
    return out


def _rate_step(state: dict, chunks: int | None, seconds: float | None) -> None:
    """B58 (V7): one window of one cloner into its running figure for the wave. The figure is the rate **while it was
    writing**: from the first window with durable chunks to the last one (zero windows inside it count, the lead-in
    and the tail after its last chunk do not -- a cloner that finished first is not "slow"). A window that was not
    measured (`None`) is counted in `gaps` and is in neither the chunks nor the seconds."""
    if chunks is None or seconds is None or seconds <= 0:
        state["gaps"] += 1
        return
    state["measured"] += 1
    if chunks > 0:
        state["chunks"] += chunks
        state["seconds"] += state["tail"] + seconds
        state["tail"], state["started"] = 0.0, True
    elif state["started"]:
        state["tail"] += seconds


def _wave_ledger(states: dict, read: bool) -> dict | None:
    """B58: per cloner, what `_rate_step` kept -- `{chunks, seconds, mbps, idle, gaps}`. `mbps` is None when no window
    of it was measured (`idle` False) or when none of its measured windows had a durable chunk (`idle` True: it may
    have been complete already, or not in the stream -- not "slow", not "fast"). None = the ledger was never read."""
    if not read:
        return None
    out = {}
    for mac, st in states.items():
        rate = round(st["chunks"] * CHUNK_BYTES / st["seconds"] / 1e6, 1) if st["started"] and st["seconds"] > 0 else None
        out[mac] = {"chunks": st["chunks"] if st["measured"] else None,
                    "seconds": round(st["seconds"], 1) if st["started"] else None, "mbps": rate,
                    "idle": bool(st["measured"] and not st["started"]), "gaps": st["gaps"]}
    return out


def _note_wave(conn, session_id: str, summary: dict | None) -> None:
    """B58: the shadow row of what limited the wave (`server/brain/sendlimit.py`). Never raises."""
    try:
        from server.brain import sendlimit              # noqa: PLC0415 -- the brain reads what is measured here
        sendlimit.note_wave(conn, session_id, summary)
    except Exception:                                   # noqa: BLE001 -- a decision never stops a wave
        log.exception("server senses: the B58 note of %s failed", one_line(session_id))


class Senses:
    """The windows of the live wave. `read(session_id)` -> `reading(...)`; `live()` -> the wave that streams now, or
    None. `begin` / `end` from the engines' hooks, `tick` from the clock. Never raises."""

    def __init__(self, conn, read: Callable[[str], dict], live: Callable[[], str | None], *,
                 max_windows: int = MAX_WINDOWS):
        self.conn, self.read, self.live, self.max_windows = conn, read, live, max_windows
        self._lock = threading.Lock()
        self._waves: dict[str, dict] = {}

    def _write(self, sql: str, params: tuple) -> None:
        _settle(self.conn)
        with _write_lock, writing(self.conn):
            self.conn.execute(sql, params)

    def _row(self, session_id: str, n: int, got: dict, result: dict | None) -> None:
        self._write("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json, ended_at, result_json)"
                    " VALUES (?,?,?,?,?,?)", (KIND, f"{session_id}/{n}", now_iso(), json.dumps(got),
                                              None if result is None else now_iso(),
                                              None if result is None else json.dumps(result)))

    def begin(self, session_id: str) -> None:
        try:
            got = self.read(session_id)
            with self._lock:
                self._waves[session_id] = {"first": got, "last": got, "n": 0, "dropped": 0, "peaks": {}, "passes": {},
                                         "ledger": {}, "ledger_read": False}
                self._row(session_id, 0, got, None)
        except Exception:                           # noqa: BLE001 -- a measurement never stops a wave
            log.exception("server senses: the start of %s failed", one_line(session_id))

    def _step(self, session_id: str, w: dict, got: dict) -> None:
        win = window(w["last"], got)
        w["last"] = got
        for key in PEAKS:
            v = win.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                w["peaks"][key] = max(v, w["peaks"].get(key, v))
        for sender in (win.get("sender") or {}).values():       # V4: the last place each pass was seen at
            if sender["part"] is not None and sender["pass"] is not None:
                w["passes"][f"p{sender['part']}/r{sender['pass']}"] = {
                    k: sender[k] for k in ("chunks", "of", "bytes", "elapsed_s", "done")}
        self._ledger_step(w, win)                               # B58 (V7): before the ceiling, like the peaks
        if w["n"] >= self.max_windows:
            w["dropped"] += 1
            return
        w["n"] += 1
        self._row(session_id, w["n"], got, win)

    @staticmethod
    def _ledger_step(w: dict, win: dict) -> None:
        got, seconds = win.get("ledger"), win.get("seconds")
        if isinstance(got, dict):
            w["ledger_read"] = True
        for mac in {*w["ledger"], *(got if isinstance(got, dict) else ())}:
            state = w["ledger"].setdefault(mac, {"chunks": 0, "seconds": 0.0, "tail": 0.0, "started": False,
                                                 "measured": 0, "gaps": 0})
            entry = got.get(mac) if isinstance(got, dict) else None
            _rate_step(state, entry.get("chunks") if isinstance(entry, dict) else None, seconds)

    def tick(self) -> None:
        try:
            session_id = self.live()
            with self._lock:
                if not session_id or session_id not in self._waves:
                    return                          # no wave, or one whose start was not read: nothing is claimed
            got = self.read(session_id)
            with self._lock:
                if session_id in self._waves:
                    self._step(session_id, self._waves[session_id], got)
        except Exception:                           # noqa: BLE001
            log.exception("server senses: a window failed")

    def end(self, session_id: str) -> dict | None:
        """The last window, and the whole wave into the wave's `server_io` row (`result_json.server`)."""
        with self._lock:
            w = self._waves.pop(session_id, None)       # first: a wave whose end failed is not kept for ever
        if w is None:
            return None
        try:
            got = self.read(session_id)
            with self._lock:
                self._step(session_id, w, got)
            whole = window(w["first"], w["last"])
            summary = {**{k: v for k, v in whole.items()
                          if k not in WINDOW_ONLY},
                       "windows": w["n"], "windows_dropped": w["dropped"], "passes": w["passes"],
                       "peaks": {k: w["peaks"].get(k) for k in PEAKS},
                       "ledger": _wave_ledger(w["ledger"], w["ledger_read"])}       # B58 (V7): per cloner, the wave
            row = self.conn.execute("SELECT result_json FROM server_io WHERE kind = 'wave' AND ref = ?",
                                    (session_id,)).fetchone()
            if row is None or not row["result_json"]:
                log.warning("server senses: %s has no server_io result to add the server's senses to",
                            one_line(session_id))
                _note_wave(self.conn, session_id, summary)
                return summary
            result = {**json.loads(row["result_json"]), "server": summary}
            self._write("UPDATE server_io SET result_json = ? WHERE kind = 'wave' AND ref = ?",
                        (json.dumps(result), session_id))
            _note_wave(self.conn, session_id, summary)      # B58: after the summary is written. Never raises.
            return summary
        except Exception:                           # noqa: BLE001
            log.exception("server senses: the end of %s failed", one_line(session_id))
            _note_wave(self.conn, session_id, None)         # B58: a wave that was begun and has no summary: "not measured"
            return None
