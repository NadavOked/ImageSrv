"""Resource leaks as evidence: every ResourceWarning of a run, by where the object was made.

#3180: the coverage run of 3.13 pushed a file descriptor past 1024, and
`select()` failed in storage_client -- a leak of descriptors, and the run showed
ResourceWarnings on unclosed sqlite connections next to it. A ResourceWarning is
a resource nobody closed; Python ignores it by default and pytest only lists it.

Two roles in one file, so the key that records a leak and the report that reads
it cannot drift apart:

  pytest plugin   python -m pytest ... -p tests.resource_leaks --resource-leaks DIR
      Turns ResourceWarning on ("always"), starts tracemalloc, runs gc at the end
      of each test (so a leak is charged to the test that made it), and writes
      one JSON line per warning to DIR/leaks-<worker>.jsonl. Each worker also
      writes DIR/active-<worker>: the evidence that the plugin was on.

  report          python tests/resource_leaks.py report DIR --baseline FILE [--block-new]
      Groups the warnings by SITE -- the innermost frame under the rootdir (the repo) in
      the allocation traceback, as `path::source line` (not the line number:
      an edit above it must not make an old leak look new). Prints every site,
      marks those not in the baseline, and writes the same to
      $GITHUB_STEP_SUMMARY. Exit 0; 1 with --block-new and a new site; 2 when
      no worker wrote its `active` file (the run proves nothing, principle 5).

The baseline is one site a line, each under a `#` line that names its Issue.
"""

from __future__ import annotations

import argparse
import gc
import json
import linecache
import os
import sys
import tracemalloc
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRAMES = 25
_STATE: dict[str, object] = {}


def site_of(frames: list[tuple[str, int]], root: Path = ROOT) -> str | None:
    """The innermost frame under `root` (not this file), as `path::source line`."""
    for filename, lineno in reversed(frames):
        path = Path(filename)
        try:
            rel = path.resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        text = linecache.getline(filename, lineno).strip()
        return f"{rel.as_posix()}::{text or f'line {lineno}'}"
    return None


def _allocation_witnesses(source) -> list:
    """The leaked object, then what it wraps. On Python 3.12 a TextIOWrapper's own allocation traceback
    lacks the innermost frame -- the line that called `open()` -- while its buffer and its raw file carry it
    (3.13 has it on all three): a site found only on 3.13 made "no new leaks" mean "a different Python"."""
    if source is None:
        return []
    buffer = getattr(source, "buffer", None)
    return [source, *(o for o in (buffer, getattr(buffer, "raw", None)) if o is not None)]


def pytest_addoption(parser) -> None:
    parser.addoption("--resource-leaks", metavar="DIR", default=None,
                     help="record every ResourceWarning with its allocation site into DIR")


def pytest_configure(config) -> None:
    out = config.getoption("--resource-leaks")
    if not out:
        return
    is_worker = hasattr(config, "workerinput")
    if not is_worker and config.pluginmanager.hasplugin("dsession"):
        return                      # the xdist controller: the workers record
    if not tracemalloc.is_tracing():
        tracemalloc.start(FRAMES)
    config.addinivalue_line("filterwarnings", "always::ResourceWarning")
    worker = config.workerinput["workerid"] if is_worker else "main"
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"active-{worker}").write_text(f"{os.getpid()}\n", encoding="utf-8")
    _STATE["file"] = Path(out) / f"leaks-{worker}.jsonl"
    _STATE["root"] = Path(config.rootpath).resolve()   # the repo in a real run


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item) -> None:
    if "file" in _STATE:
        gc.collect()                # inside the test's warning capture: the leak is charged to it


def pytest_warning_recorded(warning_message, when, nodeid, location) -> None:
    out = _STATE.get("file")
    if out is None or not issubclass(warning_message.category, ResourceWarning):
        return
    root = _STATE["root"]
    tb, frames, site = None, [], None
    for obj in _allocation_witnesses(warning_message.source):
        tb = tracemalloc.get_object_traceback(obj)
        frames = [(f.filename, f.lineno) for f in tb] if tb is not None else []
        site = site_of(frames, root)  # type: ignore[arg-type]
        if site:
            break
    site = (site or site_of([(warning_message.filename, warning_message.lineno)], root)  # type: ignore[arg-type]
            or "?")
    row = {"site": site, "nodeid": nodeid, "when": when,
           "message": str(warning_message.message).splitlines()[0][:200],
           "traced": tb is not None,
           "frames": [f"{Path(f).as_posix()}:{n}" for f, n in frames[-8:]]}
    with open(out, "a", encoding="utf-8") as fh:  # type: ignore[arg-type]
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# --- report ------------------------------------------------------------------


def load_baseline(path: Path) -> set[str]:
    sites = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            sites.add(line.rstrip())
    return sites


def report(directory: Path, baseline: set[str], block_new: bool) -> int:
    active = sorted(directory.glob("active-*")) if directory.is_dir() else []
    if not active:
        print(f"resource-leaks: CANNOT VERIFY -- no active-* file in {directory}: the plugin did not run")
        return 2
    by_site: dict[str, list[dict]] = defaultdict(list)
    for f in sorted(directory.glob("leaks-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            by_site[row["site"]].append(row)
    new = sorted(set(by_site) - baseline)
    gone = sorted(baseline - set(by_site))
    lines = [f"## ResourceWarning -- {sum(map(len, by_site.values()))} warnings, {len(by_site)} sites, "
             f"{len(new)} not in the baseline ({len(active)} workers recorded)", ""]
    for site in sorted(by_site, key=lambda s: (s in baseline, -len(by_site[s]), s)):
        rows = by_site[site]
        tests = sorted({r["nodeid"] for r in rows})
        mark = "baseline" if site in baseline else "**NEW**"
        lines.append(f"- {mark} `{site}` -- {len(rows)}x in {len(tests)} test(s), e.g. `{tests[0]}`")
        lines.append(f"  - {rows[0]['message']}")
        if rows[0]["frames"]:
            lines.append(f"  - allocated at: {' <- '.join(reversed(rows[0]['frames'][-4:]))}")
    for site in gone:
        lines.append(f"- gone (remove from the baseline): `{site}`")
    text = "\n".join(lines) + "\n"
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 1 if block_new and new else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("dir", type=Path)
    r.add_argument("--baseline", type=Path, required=True)
    r.add_argument("--block-new", action="store_true")
    a = ap.parse_args(argv)
    return report(a.dir, load_baseline(a.baseline), a.block_new)


if __name__ == "__main__":
    sys.exit(main())
