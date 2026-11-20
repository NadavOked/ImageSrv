"""14/11: the ResourceWarning recorder (tests/resource_leaks.py) and its place in nightly.yml.

The canary: a planted test that opens a file and never closes it must come back
as a site in this repo, with the source line that opened it -- otherwise "no
new leaks" in the nightly would be the absence of a report, not evidence.
"""

import re
import subprocess
import sys
from pathlib import Path

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


def _run(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess:
    canary = tmp_path / "canary"
    canary.mkdir()
    (canary / "test_planted_leak.py").write_text(LEAKY, encoding="utf-8")
    (canary / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")   # not the repo's conftest
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.resource_leaks",
         "-c", str(canary / "pytest.ini"), *extra, str(canary)],
        cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)


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
