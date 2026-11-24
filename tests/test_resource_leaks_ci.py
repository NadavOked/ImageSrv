"""14/11: the ResourceWarning recorder (tests/resource_leaks.py) and its place in nightly.yml.

The canary: a planted test that opens a file and never closes it must come back
as a site in this repo, with the source line that opened it -- otherwise "no
new leaks" in the nightly would be the absence of a report, not evidence.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import resource_leaks

ROOT = Path(__file__).resolve().parent.parent
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
BASELINE = ROOT / ".github" / "resource-leaks-baseline.txt"

LEAKY = '''
def test_leaks_a_file(tmp_path):
    p = tmp_path / "x"
    p.write_text("x")
    fh = open(p)  # the planted leak
    assert fh.read() == "x"


def test_closes_its_file(tmp_path):
    p = tmp_path / "y"
    p.write_text("y")
    with open(p) as fh:
        assert fh.read() == "y"
'''


def _run(tmp_path: Path, *extra: str, target: str | None = None) -> subprocess.CompletedProcess:
    canary = tmp_path / "canary"
    canary.mkdir(exist_ok=True)
    (canary / "test_planted_leak.py").write_text(LEAKY, encoding="utf-8")
    (canary / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")   # not the repo's conftest
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.resource_leaks",
         "-c", str(canary / "pytest.ini"), *extra, target or str(canary)],
        cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)


def _rows(directory: Path) -> list[dict]:
    return [json.loads(line) for f in sorted(directory.glob("leaks-*.jsonl"))
            for line in f.read_text(encoding="utf-8").splitlines()]


def _nodeids(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tests" / "resource_leaks.py"), "nodeids", str(directory)],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)


def _report(directory: Path, baseline: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tests" / "resource_leaks.py"), "report", str(directory),
                           "--baseline", str(baseline), *extra],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)


def test_the_planted_leak_is_recorded_at_the_line_that_opened_it(tmp_path):
    out = tmp_path / "leaks"
    run = _run(tmp_path, "--resource-leaks", str(out))
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    assert list(out.glob("active-*")), "the plugin left no evidence that it ran"
    rows = [line for f in out.glob("leaks-*.jsonl") for line in f.read_text().splitlines()]
    assert len(rows) == 1, rows
    assert "test_planted_leak.py::fh = open(p)  # the planted leak" in rows[0]
    assert "test_leaks_a_file" in rows[0]


def test_a_new_site_is_reported_and_blocks_only_when_asked(tmp_path):
    out = tmp_path / "leaks"
    _run(tmp_path, "--resource-leaks", str(out))
    empty = tmp_path / "baseline.txt"
    empty.write_text("# nothing yet\n", encoding="utf-8")
    soft = _report(out, empty)
    assert soft.returncode == 0, soft.stdout
    assert "**NEW**" in soft.stdout and "fh = open(p)" in soft.stdout
    assert _report(out, empty, "--block-new").returncode == 1


def test_a_site_in_the_baseline_is_not_new(tmp_path):
    out = tmp_path / "leaks"
    _run(tmp_path, "--resource-leaks", str(out))
    site = next(iter({line.split('"site": "')[1].split('"')[0]
                      for f in out.glob("leaks-*.jsonl") for line in f.read_text().splitlines()}))
    known = tmp_path / "baseline.txt"
    known.write_text(f"# #0000: the canary\n{site}\n", encoding="utf-8")
    run = _report(out, known, "--block-new")
    assert run.returncode == 0, run.stdout
    assert "**NEW**" not in run.stdout


def test_no_evidence_that_the_plugin_ran_is_not_a_clean_run(tmp_path):
    (tmp_path / "leaks").mkdir()
    run = _report(tmp_path / "leaks", BASELINE)
    assert run.returncode == 2
    assert "CANNOT VERIFY" in run.stdout


def test_without_the_option_the_plugin_does_nothing(tmp_path):
    run = _run(tmp_path)
    assert run.returncode == 0
    assert not list(tmp_path.rglob("active-*"))


def test_the_site_is_the_innermost_repo_frame_by_source_line_not_line_number():
    here = Path(resource_leaks.__file__).resolve().parent / "conftest.py"
    site = resource_leaks.site_of([("/usr/lib/python3/x.py", 1), (str(here), 1)])
    assert site is not None and site.startswith("tests/conftest.py::")
    assert not re.search(r"::line \d", site)
    assert resource_leaks.site_of([("/usr/lib/python3/x.py", 1)]) is None


def test_every_baseline_site_names_its_issue():
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip() and not line.startswith("#"):
            assert i > 0 and re.match(r"^# #\d+", lines[i - 1]), f"baseline line {i + 1} has no Issue above it: {line}"


def test_nightly_records_on_3_13_and_reports_without_blocking():
    text = NIGHTLY.read_text(encoding="utf-8")
    assert '-p tests.resource_leaks --resource-leaks "$RUNNER_TEMP/gate/leaks"' in text
    step = re.search(r"- name: Resource leaks \(report\)\n(.*?)(?=\n      - |\Z)", text, re.DOTALL)
    assert step, "no report step"
    assert "matrix.python-version == '3.13'" in step.group(1)
    assert "--block-new" not in step.group(1), "it starts as a report (14/11); blocking is a later decision"
    assert ".github/resource-leaks-baseline.txt" in step.group(1)


# --- #99 (public): detect everywhere, trace only what leaked ----------------------------------------------------
# tracemalloc on every test made the nightly's 3.13 shards ~6x slower than 3.12 (measured on tr1, 23/11: a
# 4229-test file 16.5s plain, 177s traced, 61.5s detect-only) -- they hit the 60-minute wall three nights running.


def test_detect_only_records_the_test_that_leaked_and_no_allocation_site(tmp_path):
    out = tmp_path / "leaks"
    run = _run(tmp_path, "--resource-leaks", str(out), "--resource-leaks-no-trace")
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    assert list(out.glob("active-*")), "no evidence that the plugin ran"
    rows = _rows(out)
    assert len(rows) == 1 and rows[0]["traced"] is False, rows
    assert "test_leaks_a_file" in rows[0]["nodeid"]


def test_nodeids_names_a_detected_leak_until_a_traced_run_locates_it(tmp_path):
    out = tmp_path / "leaks"
    _run(tmp_path, "--resource-leaks", str(out), "--resource-leaks-no-trace")
    first = _nodeids(out)
    assert first.returncode == 0, first.stderr
    ids = first.stdout.split()
    assert len(ids) == 1 and ids[0].endswith("::test_leaks_a_file"), first.stdout
    again = _run(tmp_path, "--resource-leaks", str(out), target=f"{tmp_path / 'canary'}/{ids[0]}")
    assert again.returncode == 0, again.stdout[-2000:]
    located = _nodeids(out)
    assert located.returncode == 0 and located.stdout.strip() == "", located.stdout


def test_nodeids_without_evidence_that_the_plugin_ran_is_not_an_empty_list(tmp_path):
    (tmp_path / "leaks").mkdir()
    run = _nodeids(tmp_path / "leaks")
    assert run.returncode == 2 and "CANNOT VERIFY" in run.stderr, (run.returncode, run.stderr)


def test_the_report_lists_a_detected_leak_by_its_file_and_drops_it_once_located(tmp_path):
    out = tmp_path / "leaks"
    empty = tmp_path / "baseline.txt"
    empty.write_text("# nothing yet\n", encoding="utf-8")
    _run(tmp_path, "--resource-leaks", str(out), "--resource-leaks-no-trace")
    detected = _report(out, empty)
    assert detected.returncode == 0, detected.stdout
    assert "?::test_planted_leak.py" in detected.stdout and "fh = open(p)" not in detected.stdout
    _run(tmp_path, "--resource-leaks", str(out), target=f"{tmp_path / 'canary'}/{_rows(out)[0]['nodeid']}")
    located = _report(out, empty)
    assert "fh = open(p)" in located.stdout and "?::test_planted_leak.py" not in located.stdout, located.stdout


def test_the_xdist_controller_does_not_record_what_the_workers_already_did(tmp_path):
    pytest.importorskip("xdist")
    out = tmp_path / "leaks"
    run = _run(tmp_path, "--resource-leaks", str(out), "-n", "2", "--dist", "load")
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    assert not (out / "active-main").exists() and not (out / "leaks-main.jsonl").exists(), sorted(p.name for p in out.iterdir())
    assert len(_rows(out)) == 1, _rows(out)    # 23/11: the controller wrote each worker's warning a second time, untraced


def test_nightly_detects_in_the_run_and_traces_only_the_tests_that_leaked():
    text = NIGHTLY.read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.strip().startswith('if [ "$PY" = "3.13" ]; then leaks='))
    assert "--resource-leaks-no-trace" in line, "tracemalloc on every test is the 6x slowdown of #99"
    step = re.search(r"- name: Locate the leaks \(3\.13\)\n(.*?)(?=\n      - |\n      # |\Z)", text, re.DOTALL)
    assert step, "no step that traces the tests that leaked"
    body = step.group(1)
    assert "matrix.python-version == '3.13'" in body and "if: always()" in body
    assert "tests/resource_leaks.py nodeids" in body and "-p tests.resource_leaks --resource-leaks" in body
    assert "--resource-leaks-no-trace" not in body, "the second run is the traced one"
    assert "exit 2" in body, "a pytest that broke is not 'no leaks' (principle 5)"
    assert text.index("Locate the leaks") < text.index("Resource leaks (report)"), "locate first, then report"
