"""The report of .github/workflows/order-and-repeat.yml: which tests failed when run in a
random order, several times each.

14/11: four tests failed once under load and passed alone on the same day (#3176,
two in #3179, test_lc_hello_1367). A test like that is not green (#3136) -- it is
waiting for the gate it will fail. The nightly runs the whole suite with
pytest-randomly (a new order every night, the seed in the log) and pytest-repeat
(each test N times), and this script reads its junit files.

    python tests/flaky_report.py JUNIT... [--seed N]

Prints one line per test that failed at least once, with how many of its runs
failed, and writes the same to $GITHUB_STEP_SUMMARY. Exit 0 -- it is a report;
every test it lists gets an Issue by hand. Exit 2 when a junit file is missing,
unreadable or holds no test at all: "nothing ran" is not "nothing failed"
(principle 5).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

#: pytest-repeat's suffix: test_x[1-3] alone, or after a parameter: test_x[param-2-3]
REPEAT_ALONE = re.compile(r"\[\d+-\d+\]$")
REPEAT_AFTER = re.compile(r"-\d+-\d+\]$")


def base_id(classname: str, name: str) -> str:
    """The test id without pytest-repeat's run number."""
    name = REPEAT_AFTER.sub("]", REPEAT_ALONE.sub("", name))
    return f"{classname.replace('.', '/')}.py::{name}" if classname else name


def read(paths: list[Path]) -> tuple[Counter, Counter]:
    runs: Counter = Counter()
    failed: Counter = Counter()
    for path in paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            if case.find("skipped") is not None:
                continue
            tid = base_id(case.get("classname", ""), case.get("name", ""))
            runs[tid] += 1
            if case.find("failure") is not None or case.find("error") is not None:
                failed[tid] += 1
    return runs, failed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("junit", nargs="+", type=Path)
    ap.add_argument("--seed", default="?")
    a = ap.parse_args(argv)
    try:
        runs, failed = read(a.junit)
    except (OSError, ET.ParseError) as exc:
        print(f"flaky-report: CANNOT VERIFY -- {exc}")
        return 2
    if not runs:
        print(f"flaky-report: CANNOT VERIFY -- no test ran in {', '.join(map(str, a.junit))}")
        return 2
    lines = [f"## Random order + repeats (seed {a.seed}) -- {len(failed)} of {len(runs)} tests "
             f"failed at least once, {sum(runs.values())} runs", ""]
    for tid, n in sorted(failed.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{tid}` -- {n} of {runs[tid]} runs failed")
    text = "\n".join(lines) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
