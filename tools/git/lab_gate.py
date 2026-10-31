#!/usr/bin/env python3
"""#2121: the lab gate's verdict -- "green" means every collected test ran, and
nothing failed except what is declared.

Two subcommands, both run by lab-run-tests-remote.sh from the checkout:

  collect --out FILE -- <pytest args>
      `pytest --collect-only` of this very tree with the run's own arguments,
      one node id per line -- what SHOULD run.
  verdict --collected FILE --junit FILE... --problems FILE... --allowed FILE
          --pytest-rc N... [--canary NODEID]
      Reads what DID run (the run's --junitxml) and decides.

#2118: a run may be several pytest invocations -- the parallel phase
(`-m "not serial"`) and the serial one (`-m serial`, tests that are load
sensitive). `collect` is the whole tree; `--junit`, `--problems` and
`--pytest-rc` are given once per phase. Green needs every collected id in the
UNION of the junits, each id in exactly one of them (an id that ran in two
phases means the marker split is broken), and every phase's problems file
present. Exit 5 of a phase (no tests selected) is not by itself an error: a
test that a phase should have run and did not is `missing`, by name.

Why not pytest's exit code (#2121, review of 24/10 on the testrunner): it is 1
on every testrunner run (six uinput errors, user `test` is not root), so a
seventh failure does not move it; and it is 0 when a test silently did not run
(xdist swallowing a node, a deselecting plugin). Green here is positive
evidence: the junit has every collected id, its FAILED/ERROR/SKIPPED list is
empty except the declared set, and the session's own checks (orphans, real
sender) were recorded -- and empty.

Ids are never split on `::` (parametrize ids carry IPv6 addresses such as
`[::1]`). Each collected node id is mapped FORWARD with pytest's own
`mangle_test_address` to the (classname, name) pair junitxml writes for it,
and the pairs are compared. Exit: 0 green (canary mode: the canary was seen
failing), 1 red, 2 the check itself could not be made (principle 5).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
# #2164: the only XML read here is the run's own --junitxml, written by pytest
# on the same machine a moment earlier -- never input from outside. No XXE
# source, so no defusedxml dependency (semgrep blocks on it in the public CI).
import xml.etree.ElementTree as ET  # nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
from pathlib import Path

GREEN, RED, CANNOT = 0, 1, 2

#: tests/native.py -- a skip whose reason carries it is declared (#295).
DECLARED_PREFIX = "תחנת פיתוח בלבד"

#: the canary (#2121): fails only under IMAGESRV_GATE_CANARY=1.
CANARY = "tests/test_gate_canary.py::test_the_gate_canary_fails_only_when_asked"


def junit_key(nodeid: str) -> tuple[str, str]:
    """The (classname, name) junitxml writes for this node id -- pytest's own code."""
    from _pytest.junitxml import bin_xml_escape, mangle_test_address
    names = mangle_test_address(nodeid)
    return ".".join(names[:-1]), bin_xml_escape(names[-1])


# --- collect -----------------------------------------------------------------

class _Recorder:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def pytest_collection_finish(self, session) -> None:
        self.ids = [item.nodeid for item in session.items]


def collect(out: Path, args: list[str]) -> int:
    import pytest
    sys.path.insert(0, os.getcwd())          # as `python3 -m pytest` does
    rec = _Recorder()
    rc = pytest.main(["tests", "--collect-only", "-qq", *args], plugins=[rec])
    if rc != 0 or not rec.ids:
        print(f"gate: --collect-only failed (rc={int(rc)}, {len(rec.ids)} ids) -- "
              "nothing to compare the run with", file=sys.stderr)
        return CANNOT
    out.write_text("".join(f"{i}\n" for i in rec.ids), encoding="utf-8")
    print(f"gate: collected {len(rec.ids)} ids")
    return GREEN


# --- verdict -----------------------------------------------------------------

def load_allowed(path: Path) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    for e in entries:
        if e.get("kind") not in ("error", "failure", "skip") or not e.get("nodeid") \
                or not e.get("message") or not e.get("why"):
            raise ValueError(f"declared entry needs kind/nodeid/message/why: {e}")
    return entries


def outcomes(junit: Path) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(classname, name) -> [(kind, message)] -- kind failure/error/skip; [] = passed."""
    ran: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for tc in ET.parse(junit).getroot().iter("testcase"):
        found = ran.setdefault((tc.get("classname", ""), tc.get("name", "")), [])
        for child in tc:
            if child.tag in ("failure", "error"):
                found.append((child.tag, child.get("message", "")))
            elif child.tag == "skipped" and child.get("type") != "pytest.xfail":
                found.append(("skip", child.get("message", "")))   # an xfail ran
    return ran


def verdict(collected: Path, junits: list[Path], problems: list[Path], allowed: Path,
            pytest_rcs: list[int], canary: str | None) -> int:
    say = print
    try:
        ids = [l for l in collected.read_text(encoding="utf-8").splitlines() if l]
        declared = load_allowed(allowed)
        phases = [outcomes(j) for j in junits]
        session = [p for f in problems for p in json.loads(f.read_text(encoding="utf-8"))]
    except (OSError, ValueError, ET.ParseError) as exc:
        say(f"gate: CANNOT VERIFY -- {exc}")
        return CANNOT
    if not (len(junits) == len(problems) == len(pytest_rcs)):
        say("gate: CANNOT VERIFY -- one --junit, --problems and --pytest-rc per phase "
            f"({len(junits)}/{len(problems)}/{len(pytest_rcs)})")
        return CANNOT
    by_key: dict[tuple[str, str], str] = {}
    for nodeid in ids:
        key = junit_key(nodeid)
        if key in by_key:
            say(f"gate: CANNOT VERIFY -- two ids map to one junit name: {by_key[key]} / {nodeid}")
            return CANNOT
        by_key[key] = nodeid
    ran: dict[tuple[str, str], list[tuple[str, str]]] = {}
    twice: list[str] = []
    for phase in phases:
        twice += [f"{c}::{n}" for (c, n) in phase if (c, n) in ran]
        ran.update(phase)
    red: list[str] = []
    for rc in pytest_rcs:
        if rc not in (0, 1, 5):
            red.append(f"pytest exited {rc} (interrupted, internal error or usage)")
    if twice:
        red.append(f"{len(twice)} junit cases ran in more than one phase:")
        red += [f"    {n}" for n in twice]
    missing = [n for k, n in by_key.items() if k not in ran]
    extra = [f"{c}::{n}" for (c, n) in ran if (c, n) not in by_key]
    if missing:
        red.append(f"{len(missing)} collected ids did not run:")
        red += [f"    {n}" for n in missing]
    if extra:
        red.append(f"{len(extra)} junit cases were never collected:")
        red += [f"    {n}" for n in extra]
    failed: list[str] = []
    used: set[int] = set()
    for key, results in ran.items():
        nodeid = by_key.get(key, f"{key[0]}::{key[1]}")
        for kind, message in results:
            if kind == "skip" and DECLARED_PREFIX in message:
                continue
            hit = next((i for i, e in enumerate(declared) if e["nodeid"] == nodeid
                        and e["kind"] == kind and e["message"] in message), None)
            if hit is not None:
                used.add(hit)
                continue
            failed.append(f"    {kind.upper()} {nodeid}  <-  {message[:200]}")
    say(f"gate: {len(ids)} collected, {len(ran)} in junit"
        f"{' (' + ' + '.join(str(len(p)) for p in phases) + ' by phase)' if len(phases) > 1 else ''}"
        f", pytest rc={','.join(map(str, pytest_rcs))}")
    if used:
        say(f"gate: {len(used)} declared (tools/git/lab-gate-allowed.json), not counted:")
        say("\n".join(f"    {declared[i]['kind']} {declared[i]['nodeid']}" for i in sorted(used)))
    if canary is not None:
        seen = any(by_key.get(k) == canary and any(r[0] == "failure" for r in res)
                   for k, res in ran.items())
        say(f"gate: canary {'CONFIRMED -- the run failed on it' if seen else 'NOT SEEN FAILING'}: {canary}")
        for line in red + failed:
            say(line)
        return GREEN if seen else RED
    if failed:
        red.append(f"{len(failed)} FAILED/ERROR/SKIPPED outside the declared set:")
        red += failed
    if session:
        red.append("the session's own checks (conftest):")
        red += [f"    {p}" for p in session]
    if red:
        say("gate: RED")
        for line in red:
            say(line)
        return RED
    say("gate: GREEN -- every collected id ran; nothing outside the declared set")
    return GREEN


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("args", nargs=argparse.REMAINDER)
    v = sub.add_parser("verdict")
    v.add_argument("--collected", type=Path, required=True)
    v.add_argument("--allowed", type=Path, required=True)
    v.add_argument("--junit", type=Path, action="append", required=True)
    v.add_argument("--problems", type=Path, action="append", required=True)
    v.add_argument("--pytest-rc", type=int, action="append", required=True)
    v.add_argument("--canary", nargs="?", const=CANARY, default=None)
    a = p.parse_args(argv)
    if a.cmd == "collect":
        rest = a.args[1:] if a.args[:1] == ["--"] else a.args
        return collect(a.out, rest)
    return verdict(a.collected, a.junit, a.problems, a.allowed, a.pytest_rc, a.canary)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
