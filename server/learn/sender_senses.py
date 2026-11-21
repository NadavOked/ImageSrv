"""#3125 (V4, V5): what the sender of the live pass says about itself -- the counters of `chunkcast send` (V4) and
the place in the stream, chunk k of N (V5) -- read from the log the engine itself reads.

`chunkcast send` prints `sent <chunks> <bytes>` once a second and `done <chunks> <bytes> <ms>` at the end
(`agent/chunkcast.c`, `cc_send`). Its output goes to `chunklog.log_path(<--file>)`: the same function the engine
reads the sender's own sentence with (#3261), and since #3340 also the file the production runner writes. There is no
second path here.

**Which log is the live pass's:** the log of a part is truncated by the next pass and stays on the disk after a wave,
so a log is read only when a `chunkcast` that this server started is alive, and the part, the pass and the file come
from that process's own command line (`--part`, `--pass`, `--file`). A log with nobody writing to it is nobody's.
**N** is the number of chunks the pass sends: the whole index in pass 1 (`chunkformat.read_index`), the chunks the
repair list holds in a repair pass (`--chunks-file`, which the engine removes when the pass ends).

**Every field is a number that was read, or `null` = not measured -- never 0** (principle 5): no `sent` line yet
(the first one is printed after a second), a log that was not read, an unfinished last line, an index or a list that
was not read, a counter that went down or a process that is not the one the window began with. The counters lag the
sender by up to the second between two `sent` lines.

**Who slowed the stream (#3505):** `paced <wire_ms> <disk_ms>` (`agent/chunkcast.c`, since #3358) is how long the sender
has slept for `--kbps` and for `--write-kbps` since it began. The last one is read like `sent`, and a window says how much
each grew in it. That is a sleep, not a verdict: the sender sleeps for a pacing that was asked for, and which rate is the
limit is for K8 to decide (`rate_outcome`, #3498), not for this module.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from server.library import chunkformat
from server.rounds import chunklog

log = logging.getLogger("imagesrv.sender_senses")

_SENT = re.compile(r"sent (\d+) (\d+)")
_DONE = re.compile(r"done (\d+) (\d+) (-?\d+)")
_RANGE = re.compile(r"(\d+)-(\d+)")
#: the end of the log is read, not the whole of it: a `sent` line is ~20 bytes, and the last one is in the last lines.
TAIL_BYTES = 8192


def parse_log(text: str, *, whole: bool = True) -> dict:
    """The last `sent` and the `done` of a log. Only a line that ended with a newline counts (the last one may be
    half written), and the first line of a tail read from the middle is dropped (`whole=False`)."""
    lines = text.split("\n")[:-1]
    if not whole and lines:
        lines = lines[1:]
    sent = done = None
    for line in lines:
        if (m := _SENT.fullmatch(line.strip())):
            sent = {"chunks": int(m[1]), "bytes": int(m[2])}
        elif (m := _DONE.fullmatch(line.strip())):
            done = {"chunks": int(m[1]), "bytes": int(m[2]), "ms": int(m[3])}
    return {"sent": sent, "done": done}


def parse_paced(text: str, *, whole: bool = True) -> dict | None:
    """The last `paced <wire_ms> <disk_ms>` of a log (cumulative sleeps), or None when there is none yet: the one reader
    of that line, `chunklog.parse_paced` (K8 reads the same line, #3498), as the dict V4 keeps."""
    got = chunklog.parse_paced(text, whole=whole)
    return {"wire_ms": got[0], "disk_ms": got[1]} if got else None


def read_log(path: Path, tail: int = TAIL_BYTES) -> dict | None:
    """`parse_log` of the end of the log (and its `paced`), or None when it was not read ("not read" is not "nothing said")."""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - tail))
            text = handle.read().decode("utf-8", errors="replace")
        whole = size <= tail
        return {**parse_log(text, whole=whole), "paced": parse_paced(text, whole=whole)}
    except OSError as exc:
        log.warning("chunkcast send log %s unreadable: %s", path, exc)
        return None


def _args(proc: Path, pid: str) -> list[str] | None:
    try:
        raw = (proc / pid / "cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode("utf-8", errors="replace") for a in raw.split(b"\0") if a] or None


def _flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def _int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def _listed(path: str) -> int | None:
    """The chunks a repair list names (`0-3,7-7`), or None when the list is not there (the pass is over) or odd."""
    try:
        text = Path(path).read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return None
    spans = [_RANGE.fullmatch(part) for part in text.split(",")] if text else []
    if not spans or None in spans:
        return None
    counts = [int(m[2]) - int(m[1]) + 1 for m in spans]
    return sum(counts) if all(c > 0 for c in counts) else None


def total(args: list[str]) -> int | None:
    """N: the chunks this pass sends -- the repair list when it has one, the whole index in a first pass."""
    listed = _flag(args, "--chunks-file")
    if listed is not None:
        return _listed(listed)
    file = _flag(args, "--file")
    index = chunkformat.read_index(Path(file)) if file else None
    return index.chunks if index is not None else None


def read(proc: Path, procs: dict | None) -> dict | None:
    """{pid: what its pass shows} for every `chunkcast` the server started (`server_senses.processes()["chunkcast"]`).
    None = not read (the list of processes was not); {} = none is running."""
    found = procs.get("chunkcast") if isinstance(procs, dict) else None
    if not isinstance(found, dict):
        return None
    uptime, hz = procs.get("uptime"), procs.get("hz")
    out = {}
    for pid, (start, _ticks) in found.items():
        args = _args(proc, pid)
        file = _flag(args, "--file") if args else None
        got = read_log(chunklog.log_path(file)) if file else None
        sent = got["sent"] if got else None
        paced = got["paced"] if got else None
        elapsed = round(uptime - start / hz, 1) if uptime is not None and hz else None
        out[pid] = {"start": start,
                    "part": _int(_flag(args, "--part")) if args else None,
                    "pass": _int(_flag(args, "--pass")) if args else None,
                    "chunks": sent["chunks"] if sent else None,
                    "bytes": sent["bytes"] if sent else None,
                    "of": total(args) if args else None,
                    "elapsed_s": elapsed if elapsed is not None and elapsed >= 0 else None,
                    "done": got["done"] if got else None,
                    "wire_ms": paced["wire_ms"] if paced else None,
                    "disk_ms": paced["disk_ms"] if paced else None}
    return out


def _delta(a, b) -> int | None:
    ok = all(isinstance(v, int) and not isinstance(v, bool) for v in (a, b))
    return b - a if ok and b >= a else None


def step(a: dict | None, b: dict | None, dt: float) -> dict | None:
    """Two readings -> per sender alive at the end of the window: its place (V5: `chunks` of `of`, `pct`), its counters
    (V4: `bytes`, `elapsed_s`, `done`) and what it did inside the window (`chunks_in_window`, `mbps` of the stream's
    own bytes). The window values are None for a sender that was not the same process at the start of it."""
    if not isinstance(b, dict):
        return None
    out = {}
    for pid, now in b.items():
        before = a.get(pid) if isinstance(a, dict) else None
        same = isinstance(before, dict) and before.get("start") == now["start"] and dt > 0
        moved = _delta(before.get("chunks"), now["chunks"]) if same else None
        wire = _delta(before.get("bytes"), now["bytes"]) if same else None
        k, n = now["chunks"], now["of"]
        slept = {f"{name}_wait_ms": _delta(before.get(f"{name}_ms"), now.get(f"{name}_ms")) if same else None
                 for name in ("wire", "disk")}
        out[pid] = {**{key: now[key] for key in ("part", "pass", "chunks", "of", "bytes", "elapsed_s", "done")},
                    "wire_ms": now.get("wire_ms"), "disk_ms": now.get("disk_ms"), **slept,
                    "pct": round(100 * k / n, 1) if k is not None and n and k <= n else None,
                    "chunks_in_window": moved,
                    "mbps": round(wire * 8 / dt / 1e6, 1) if wire is not None else None}
    return out


def mbps(senders: dict | None) -> float | None:
    """The stream's Mbps in the window, all senders together. None = none alive, or one of them was not measured."""
    if not senders:
        return None
    parts = [s["mbps"] for s in senders.values()]
    return round(sum(parts), 1) if all(p is not None for p in parts) else None
