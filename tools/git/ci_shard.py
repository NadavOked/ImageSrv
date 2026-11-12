#!/usr/bin/env python3
"""#2952: the public `tests` run split across N runners -- which test runs where, and the merge.

Two roles, one file, so the rule that assigns a test and the check that counts
it cannot drift apart:

  pytest plugin   `python -m pytest ... -p tools.git.ci_shard --ci-shard K/N`
      Keeps the items of shard K, deselects the rest (reported as deselected,
      like `-m`). A `serial` test (#2118) always goes to shard 1, so the serial
      phase stays on one runner; any other test goes to shard
      1 + sha256(nodeid) mod N. That depends on the id alone -- not on the
      order of collection, not on the other ids -- so two runners that collect
      the same id put it in the same shard. No new dependency (no
      pytest-split): `hashlib` is the whole rule.

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
import hashlib
import sys
from pathlib import Path

GREEN, RED, CANNOT = 0, 1, 2

#: what each shard uploads (tests.yml / nightly.yml, step "Full suite").
PHASES = ("parallel", "serial")
#: only the last shard collects the whole tree without the plugin (#2952).
WHOLE = "collected-all.txt"
FINISHED = "finished"


def shard_of(nodeid: str, serial: bool, shards: int) -> int:
    """1-based shard of one test id."""
    if serial:
        return 1
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


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--dir", type=Path, required=True)
    m.add_argument("--name", required=True)
    m.add_argument("--shards", type=int, required=True)
    m.add_argument("--sha", required=True)
    m.add_argument("--allowed", type=Path, required=True)
    a = p.parse_args(argv)
    if a.shards < 1:
        p.error("--shards must be >= 1")
    return merge(a.dir, a.name, a.shards, a.sha, a.allowed)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
