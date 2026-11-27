#!/usr/bin/env python3
"""#2952: the public `tests` run split across N runners -- which test runs where, and the merge.

Two roles, one file, so the rule that assigns a test and the check that counts
it cannot drift apart:

  pytest plugin   `python -m pytest ... -p tools.git.ci_shard --ci-shard K/N`
      Keeps the items of shard K, deselects the rest (reported as deselected,
      like `-m`). A `serial` test (#2118) always goes to shard 1, so the serial
      phase stays on one runner. Any other test goes WHERE ITS FILE GOES (#4117):
      `ci_shard_weights.json` holds the measured seconds of every test file, and
      the files are dealt out longest-first onto the least loaded shard (LPT),
      shard 1 starting with the serial phase's seconds already on it -- so the
      shards finish together instead of by luck of a hash. A file the weights
      do not know (a new one) goes to 1 + sha256(nodeid) mod N, as before. Both
      rules depend on the id and on the committed weights file alone -- not on
      the order of collection, not on the other ids -- so two runners that
      collect the same id put it in the same shard. No new dependency (no
      pytest-split).

  weigh --dir D --name P [--name P2 ...] --shards N --workers W --out FILE
      Rebuilds the weights from the gate artifacts of a full run (what
      `gh run download` leaves: D/P-K/run-*.xml + collected.txt). With several
      --name (one per Python) a file weighs the mean of its totals.

  merge --dir D --name P --shards N --sha S --allowed FILE
      The job that reports `pytest (3.x)`. Each shard uploaded its gate
      directory as artifact P-K. Green needs every shard's evidence present,
      each shard marked finished on this commit, and `lab_gate.verdict` green
      on the WHOLE tree: the last shard's plain `--collect-only` (no plugin)
      against the union of all the shards' junits -- every collected id ran,
      and in exactly one shard (principle 5: "not checked" is not "passed").

Exit (merge): 0 green, 1 red, 2 the check itself could not be made.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import sys
from pathlib import Path

GREEN, RED, CANNOT = 0, 1, 2

#: what each shard uploads (tests.yml / nightly.yml, step "Full suite").
PHASES = ("parallel", "serial")
#: only the last shard collects the whole tree without the plugin (#2952).
WHOLE = "collected-all.txt"
FINISHED = "finished"
#: #4117: seconds per test file, measured on a full CI run (see `weigh`).
WEIGHTS = Path(__file__).resolve().with_name("ci_shard_weights.json")


@functools.lru_cache(maxsize=None)
def _weights(path: Path = WEIGHTS) -> tuple[dict[str, float], float, int]:
    """(file -> seconds of its parallel tests, wall seconds of the serial phase, workers).

    A weights file that is missing or unreadable is an error, not a quiet return
    to the hash: "could not weigh" and "weighed" must not look alike (principle 5).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        files = {str(k): float(v) for k, v in raw["files"].items()}
        return files, float(raw["serial_s"]), int(raw["workers"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{path.name}: cannot read the shard weights ({exc!r}); "
                         "rebuild them with `ci_shard.py weigh`") from None


def plan(weights: dict[str, float], serial_s: float, workers: int, shards: int) -> dict[str, int]:
    """LPT: heaviest file first onto the least loaded shard (ties: lower shard).

    Loads are in worker-seconds. Shard 1 starts with the serial phase, which runs
    one test at a time, so its wall seconds count `workers` times.
    """
    load = [0.0] * shards
    load[0] = serial_s * workers
    out: dict[str, int] = {}
    for name, secs in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        k = min(range(shards), key=lambda i: (load[i], i))
        out[name] = k + 1
        load[k] += secs
    return out


@functools.lru_cache(maxsize=None)
def _plan(shards: int, path: Path = WEIGHTS) -> dict[str, int]:
    files, serial_s, workers = _weights(path)
    return plan(files, serial_s, workers, shards)


def shard_of(nodeid: str, serial: bool, shards: int) -> int:
    """1-based shard of one test id."""
    if serial:
        return 1
    placed = _plan(shards).get(nodeid.split("::", 1)[0])
    if placed is not None:
        return placed
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return 1 + int.from_bytes(digest[:8], "big") % shards


def parse_spec(spec: str) -> tuple[int, int]:
    """'K/N' -> (K, N), 1 <= K <= N; anything else is a usage error."""
    try:
        k, n = (int(part) for part in spec.split("/"))
    except ValueError:
        raise ValueError(f"--ci-shard wants K/N, got {spec!r}") from None
    if not 1 <= k <= n:
        raise ValueError(f"--ci-shard wants 1 <= K <= N, got {spec!r}")
    return k, n


# --- the pytest plugin ---------------------------------------------------------

def pytest_addoption(parser) -> None:
    parser.addoption("--ci-shard", default=None, metavar="K/N",
                     help="#2952: run only shard K of N (tools/git/ci_shard.py)")


def pytest_collection_modifyitems(config, items) -> None:
    spec = config.getoption("ci_shard")
    if spec is None:
        return
    import pytest
    try:
        k, n = parse_spec(spec)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None
    try:
        _weights()
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from None
    keep, drop = [], []
    for item in items:
        serial = item.get_closest_marker("serial") is not None
        (keep if shard_of(item.nodeid, serial, n) == k else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


# --- merge ---------------------------------------------------------------------

def merge(root: Path, name: str, shards: int, sha: str, allowed: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lab_gate

    dirs = [root / f"{name}-{k}" for k in range(1, shards + 1)]
    cannot: list[str] = []
    red: list[str] = []
    junits: list[Path] = []
    problems: list[Path] = []
    rcs: list[int] = []
    for k, d in enumerate(dirs, 1):
        tag = f"shard {k}/{shards}"
        if not d.is_dir():
            cannot.append(f"{tag}: no evidence ({d.name}) -- it never ran, or died before the upload")
            continue
        need = [d / f"run-{p}.xml" for p in PHASES] + [d / f"problems-{p}.json" for p in PHASES]
        need.append(d / "rc")
        lost = [p.name for p in need if not p.is_file()]
        if lost:
            cannot.append(f"{tag}: evidence incomplete, missing {', '.join(lost)}")
            continue
        try:
            shard_rcs = [int(x) for x in (d / "rc").read_text(encoding="utf-8").split()]
        except ValueError:
            shard_rcs = []
        if len(shard_rcs) != len(PHASES):
            cannot.append(f"{tag}: rc file does not hold one exit code per phase")
            continue
        junits += need[:len(PHASES)]
        problems += need[len(PHASES):2 * len(PHASES)]
        rcs += shard_rcs
        mark = d / FINISHED
        if not mark.is_file():
            red.append(f"{tag}: did not finish green (no '{FINISHED}' marker) -- see that job")
        elif mark.read_text(encoding="utf-8").strip() != sha:
            red.append(f"{tag}: evidence is for {mark.read_text(encoding='utf-8').strip()!r}, not {sha}")
    whole = dirs[-1] / WHOLE
    if dirs[-1].is_dir() and not whole.is_file():
        cannot.append(f"shard {shards}/{shards}: no {WHOLE} -- nothing to compare the union with")
    if cannot:
        print("merge: CANNOT VERIFY")
        print("\n".join(f"    {line}" for line in cannot + red))
        return CANNOT
    print(f"merge: {shards} shards present; verdict on the whole tree ({WHOLE}) "
          f"against {len(junits)} junits")
    rc = lab_gate.verdict(whole, junits, problems, allowed, rcs, None)
    if red:
        print("merge: RED")
        print("\n".join(f"    {line}" for line in red))
        return max(rc, RED)
    return rc


# --- weigh ---------------------------------------------------------------------

def weigh(root: Path, names: list[str], shards: int, workers: int, source: str) -> dict:
    """The weights document of one or more full runs (one name = one Python).

    A junit testcase that cannot be tied to a collected file stops the job: a
    weight table that quietly drops some of the seconds is a wrong table.
    """
    import xml.etree.ElementTree as ET  # nosemgrep: python.lang.security.use-defused-xml.use-defused-xml

    per_py: list[tuple[dict[str, float], float]] = []
    for name in names:
        dirs = [root / f"{name}-{k}" for k in range(1, shards + 1)]
        mods: dict[str, str] = {}
        for d in dirs:
            for line in (d / "collected.txt").read_text(encoding="utf-8").splitlines():
                if line:
                    path = line.split("::", 1)[0]
                    mods[path[:-3].replace("/", ".")] = path
        files: dict[str, float] = {}
        serial = 0.0
        for d in dirs:
            for phase in PHASES:
                for case in ET.parse(d / f"run-{phase}.xml").getroot().iter("testcase"):
                    parts = case.get("classname", "").split(".")
                    while parts and ".".join(parts) not in mods:
                        parts.pop()
                    if not parts:
                        raise ValueError(f"{d.name}: no collected file for classname {case.get('classname')!r}")
                    secs = float(case.get("time", 0))
                    if phase == "serial":
                        serial += secs
                    else:
                        path = mods[".".join(parts)]
                        files[path] = files.get(path, 0.0) + secs
        per_py.append((files, serial))
    every = sorted({f for files, _ in per_py for f in files})
    return {
        "source": source,
        "workers": workers,
        "serial_s": round(sum(s for _, s in per_py) / len(per_py), 1),
        "files": {f: round(sum(fl.get(f, 0.0) for fl, _ in per_py) / len(per_py), 1) for f in every},
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weigh")
    w.add_argument("--dir", type=Path, required=True)
    w.add_argument("--name", action="append", required=True)
    w.add_argument("--shards", type=int, required=True)
    w.add_argument("--workers", type=int, required=True)
    w.add_argument("--out", type=Path, required=True)
    w.add_argument("--source", default="")
    m = sub.add_parser("merge")
    m.add_argument("--dir", type=Path, required=True)
    m.add_argument("--name", required=True)
    m.add_argument("--shards", type=int, required=True)
    m.add_argument("--sha", required=True)
    m.add_argument("--allowed", type=Path, required=True)
    a = p.parse_args(argv)
    if a.shards < 1:
        p.error("--shards must be >= 1")
    if a.cmd == "weigh":
        doc = weigh(a.dir, a.name, a.shards, a.workers, a.source)
        a.out.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                         encoding="utf-8", newline="\n")
        print(f"weigh: {len(doc['files'])} files, serial {doc['serial_s']}s -> {a.out}")
        return GREEN
    return merge(a.dir, a.name, a.shards, a.sha, a.allowed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
