"""#3041 / #3125: the machine's own senses during a chunk wave (S46-S50), kept per wave for the brain to learn from.

Nadav, 15/11 on #3041: the brain learns each cloner's ceiling by itself, from every wave -- "the highest rate at
which no packet was lost, and how loaded the CPU was then". It may count only a slowdown that was the MACHINE's
(lost packets, every core above 85%) and not the disk's. These are the senses that tell the two apart. **This
module only keeps them**: nothing here decides anything. The learning reads them (`server/brain/capability.py`, LR24: the
cores' highest busy%, the card's `rx_missed`/`rx_no_buffer`, the drawers' `util_max`).

**What arrives** (`agent/lib/restore/wavesense.sh`, in the progress report as ``wave_senses``): the summary of the
wave so far, refreshed every ``window_s`` seconds while the receiver runs --

- ``cpu``: per core, the latest window's ``busy``/``irq``/``softirq`` (percent of the window), ``net_rx_ps``
  (NET_RX softirqs a second), and the highest window of the wave (``busy_max``, ``softirq_max``,
  ``net_rx_ps_max``). S48, S49.
- ``nic``: ``rx_missed`` and ``rx_no_buffer`` since the wave began, ``ring_rx``/``ring_rx_max``, and ``ethtool``
  (false = the machine has no ethtool: what only it reads is "not available", not 0). S50. #3815: ``rx_bytes`` the card took in
  since the wave began (sysfs), ``tx_xon``/``tx_xoff``/``rx_xon``/``rx_xoff`` the pause frames it sent and got since then, and
  ``pause_rx``/``pause_tx`` the pause setting (`ethtool -a`; true/false, null = not said).
- ``disks``: per drawer, ``util``/``util_max`` (percent), ``inflight``/``inflight_max``, ``w_ms``/``w_ms_max``
  (ms a write). S46, S47.
- ``kernel`` (K9, #3064): per drawer, the kernel's own ATA lines about its port since its target began (at most 20, as
  `failmark.sh`'s ``ata_capture``). A list, even an empty one = dmesg was read; null = **not read** (never "clean").
  `kernel_lines` hands them to `cause.judge` at the engine's points, which see ``skipped`` and not the kernel.

**Not read is null, never 0** (principle 5), and it stays null here: a value of the wrong type, negative, or above
what the kernel can say (a percent above 100) is stored as null -- that ONE value, not the whole summary. A summary
that is not an object at all is not stored, and the wave's last good one stays.

**Where it is kept:** ``wave_senses``, one row per (wave, machine), replaced by each report. Old rows are folded into
the brain's long-term memory (``brain_memory``, kind ``machine``) by the daily clean-up, not deleted
(`memory.SOURCES`, `housekeeping._rules`), and read through `memory.knowledge` like every other source (#3185).

**Never raises** into the progress report: a sense that was not kept is a log line.
"""

from __future__ import annotations

import json
import logging

from server.db import _settle, _write_lock, now_iso, SqlConnection, writing

log = logging.getLogger("imagesrv.brain")

#: (field, the highest value the agent can honestly say). A percent above 100 is a broken read, not a busy machine.
PCT = 100
BIG = 10 ** 12
CPU_FIELDS = (("busy", PCT), ("busy_max", PCT), ("irq", PCT), ("softirq", PCT), ("softirq_max", PCT),
              ("net_rx_ps", BIG), ("net_rx_ps_max", BIG))
NIC_FIELDS = (("rx_missed", BIG), ("rx_no_buffer", BIG), ("ring_rx", 1 << 20), ("ring_rx_max", 1 << 20),
              ("rx_bytes", 10 ** 18), ("tx_xon", BIG), ("tx_xoff", BIG), ("rx_xon", BIG), ("rx_xoff", BIG))
#: #3815: pause frames, which only `ethtool` reads: the two counters of what the card sent and got, and the setting (`ethtool -a`)
PAUSE_FIELDS = ("tx_xon", "tx_xoff", "rx_xon", "rx_xoff", "pause_rx", "pause_tx")
DISK_FIELDS = (("util", PCT), ("util_max", PCT), ("inflight", 1 << 20), ("inflight_max", 1 << 20),
               ("w_ms", 10 ** 7), ("w_ms_max", 10 ** 7))
#: a report is a few KB: more cores or drawers than this is not a cloner, and is cut rather than kept whole
MAX_CORES = 256
MAX_DISKS = 32


def _value(raw: object, top: int, *, fraction: bool = False) -> int | float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float) if fraction else int):
        return None
    return raw if 0 <= raw <= top else None


def _fields(raw: object, fields, **kw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {key: _value(raw.get(key), top, **kw) for key, top in fields}


def _cpu(raw: object) -> list | None:
    if not isinstance(raw, list):
        return None
    out = []
    for core in raw[:MAX_CORES]:
        if isinstance(core, dict) and _value(core.get("core"), 4095) is not None:
            out.append({"core": core["core"], **_fields(core, CPU_FIELDS)})
    return out


def _nic(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    iface = raw.get("iface")
    out = {"iface": iface[:32] if isinstance(iface, str) else None,
           "ethtool": raw["ethtool"] if isinstance(raw.get("ethtool"), bool) else None,
           **_fields(raw, NIC_FIELDS),
           **{key: raw[key] if type(raw.get(key)) is bool else None for key in ("pause_rx", "pause_tx")}}
    if out["ethtool"] is False:          # no ethtool on the machine: what only ethtool reads was not read
        out["rx_no_buffer"] = out["ring_rx"] = out["ring_rx_max"] = None
        out.update({key: None for key in PAUSE_FIELDS})
    return out


def _disks(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {}
    for name, disk in list(raw.items())[:MAX_DISKS]:
        if isinstance(name, str) and 0 < len(name) <= 32:
            out[name] = _fields(disk, DISK_FIELDS, fraction=True) if isinstance(disk, dict) else None
    return out


#: K9 (#3064): the kernel lines kept per drawer (the agent sends at most 20; `failmark.sh`'s `ata_capture` keeps 40)
MAX_KERNEL_LINES = 20
MAX_KERNEL_CHARS = 300


def _kernel(raw: object) -> dict | None:
    """``{drawer: [lines] | None}``. A list (even an empty one) = the agent read dmesg and these are the drawer's ATA lines;
    **None = not read**, and it stays None -- a drawer whose value is not a list of strings is not "a clean kernel" (principle
    5). Not an object at all = the agent did not say (an older agent): None."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for name, lines in list(raw.items())[:MAX_DISKS]:
        if isinstance(name, str) and 0 < len(name) <= 32:
            out[name] = ([line[:MAX_KERNEL_CHARS] for line in lines[:MAX_KERNEL_LINES]]
                         if isinstance(lines, list) and all(isinstance(line, str) for line in lines) else None)
    return out


def well_formed(raw: object) -> dict | None:
    """``wave_senses`` of a progress report as it is kept, or None -- not sent, or not an object."""
    if not isinstance(raw, dict):
        return None
    out = {"window_s": _value(raw.get("window_s"), 3600), "windows": _value(raw.get("windows"), BIG),
           "cpu": _cpu(raw.get("cpu")), "nic": _nic(raw.get("nic")), "disks": _disks(raw.get("disks"))}
    if "kernel" in raw:                  # an agent that does not send it leaves the summary as it always was
        out["kernel"] = _kernel(raw["kernel"])
    return out


def kernel_lines(conn: SqlConnection, session_id: str, mac: str, serial: str) -> tuple[str, ...] | None:
    """K9 (#3064): the kernel's ATA lines about the drawer holding ``serial`` on ``mac``, as the machine last reported them in
    this wave -- for `cause.judge` at the engine's points (D11, D12, D14, D15), which see ``skipped`` but not the kernel.

    ``()`` = the agent read dmesg and it says nothing about the drawer's port; ``None`` = **not known**: no row, an agent
    that does not send the lines, a dmesg that was not read, or a drawer we cannot tie to the serial. The drawer is found the
    way `reports._bound_serial` binds a target: the serial the machine itself reported for that device name (hello), or the
    one its `disk_event` of this wave carried -- a device name alone is not an identity. Never raises."""
    try:
        from server.disks import smart_history                 # noqa: PLC0415 -- the same binding `reports` uses
        row = conn.execute("SELECT senses_json FROM wave_senses WHERE session_id = ? AND mac = ?",
                           (session_id, mac)).fetchone()
        kernel = (json.loads(row["senses_json"]) or {}).get("kernel") if row is not None else None
        if not isinstance(kernel, dict) or not serial:
            return None
        mine = [dev for dev in kernel if smart_history.serial_of(conn, mac, dev) == serial or conn.execute(
            "SELECT 1 FROM disk_events WHERE session_id = ? AND mac = ? AND disk = ? AND serial = ?",
            (session_id, mac, dev, serial)).fetchone() is not None]
        lines = kernel[mine[0]] if len(mine) == 1 else None
        return tuple(lines) if isinstance(lines, list) else None
    except Exception:                    # noqa: BLE001 -- the cause is recorded without it, as before
        log.exception("kernel lines of %s on %s in %s were not read", serial, mac, session_id)
        return None


def record(conn: SqlConnection, session_id: str, mac: str, senses: dict | None) -> bool:
    """The wave's summary of one machine, replacing the one before it. True = kept. Never raises."""
    if senses is None:
        return False
    try:
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute("INSERT INTO wave_senses (session_id, mac, at, senses_json) VALUES (?, ?, ?, ?)"
                         " ON CONFLICT (session_id, mac) DO UPDATE SET at = excluded.at,"
                         " senses_json = excluded.senses_json",
                         (session_id, mac, now_iso(), json.dumps(senses)))
        return True
    except Exception:                    # noqa: BLE001
        log.exception("wave senses of %s in %s were not kept", mac, session_id)
        return False


def _peak(values) -> float | None:
    got = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return max(got) if got else None


def numbers(senses_json: str | None) -> dict:
    """The numbers of one row for the long-term memory (`memory.SOURCES`): the wave's highest of each sense over the
    cores and the drawers. A sense that was not read is absent -- never a 0 (principle 5)."""
    try:
        s = json.loads(senses_json or "null")
    except ValueError:
        return {}
    if not isinstance(s, dict):
        return {}
    raw_cpu, raw_nic, raw_disks = s.get("cpu"), s.get("nic"), s.get("disks")
    cpu = raw_cpu if isinstance(raw_cpu, list) else []
    nic = raw_nic if isinstance(raw_nic, dict) else {}
    disks = [d for d in raw_disks.values() if isinstance(d, dict)] if isinstance(raw_disks, dict) else []
    out = {"cpu_busy_max": _peak(c.get("busy_max") for c in cpu if isinstance(c, dict)),
           "cpu_busy_min_core": None, "softirq_max": _peak(c.get("softirq_max") for c in cpu if isinstance(c, dict)),
           "net_rx_ps_max": _peak(c.get("net_rx_ps_max") for c in cpu if isinstance(c, dict)),
           "rx_missed": _peak([nic.get("rx_missed")]), "rx_no_buffer": _peak([nic.get("rx_no_buffer")]),
           "ring_rx": _peak([nic.get("ring_rx")]), "rx_bytes": _peak([nic.get("rx_bytes")]),
           "ring_rx_max": _peak([nic.get("ring_rx_max")]),
           # #3815: pause frames (the counters) and the pause setting (`ethtool -a`: on = 1, off = 0; not said = absent)
           **{k: _peak([nic.get(k)]) for k in ("tx_xon", "tx_xoff", "rx_xon", "rx_xoff")},
           **{k: None if not isinstance(nic.get(k), bool) else float(nic[k]) for k in ("pause_rx", "pause_tx")},
           "disk_util_max": _peak(d.get("util_max") for d in disks),
           "disk_inflight_max": _peak(d.get("inflight_max") for d in disks),
           "disk_w_ms_max": _peak(d.get("w_ms_max") for d in disks)}
    busy = [c.get("busy") for c in cpu if isinstance(c, dict)]
    if busy and all(isinstance(v, int) and not isinstance(v, bool) for v in busy):
        out["cpu_busy_min_core"] = float(min(v for v in busy if isinstance(v, int)))   # the least busy core of the last window: "every core above 85%"
    return {k: v for k, v in out.items() if v is not None}
