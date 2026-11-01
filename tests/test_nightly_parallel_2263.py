"""#2263: nightly used 142/150 min serially; keep the PR workflow's two phases."""

import re
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def _workflow(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _full_suite():
    match = re.search(
        r"^      - name: Full suite\n(.*?)(?=^      - name:|\Z)",
        _workflow("nightly.yml"),
        re.MULTILINE | re.DOTALL,
    )
    assert match, "nightly Full suite step not found"
    lines = [line for line in match[1].splitlines() if not line.lstrip().startswith("#")]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines))


def test_nightly_installs_same_xdist_pin_as_pr_workflow():
    pin = "pytest-xdist==3.6.1"
    for name in ("nightly.yml", "tests.yml"):
        installs = re.findall(r"^\s*pip install .+$", _workflow(name), re.MULTILINE)
        assert any(pin in line.split() for line in installs), name


def test_nightly_runs_parallel_then_serial():
    commands = [line for line in _full_suite().splitlines() if "python -m pytest " in line]
    assert len(commands) == 2
    parallel, serial = commands
    assert '-m "$base and not serial"' in parallel
    assert "-n auto --dist load" in parallel
    assert '-m "$base and serial"' in serial
    assert not re.search(r"(?:^|\s)-n(?:\s|\d|=|$)", serial)


def test_nightly_collects_and_gates_both_phases():
    step = _full_suite()
    collect = "python tools/git/lab_gate.py collect"
    verdict = "python tools/git/lab_gate.py verdict"
    assert collect in step
    assert verdict in step
    assert step.index(collect) < step.index("python -m pytest ") < step.index(verdict)
    verdict_command = next(line for line in step.splitlines() if verdict in line)
    assert "--allowed .github/ci-gate-allowed.json" in verdict_command
    assert '--collected "$gate/collected.txt"' in verdict_command
    for phase, rc in (("parallel", "rc1"), ("serial", "rc2")):
        assert f'--junit "$gate/run-{phase}.xml"' in verdict_command
        assert f'--problems "$gate/problems-{phase}.json"' in verdict_command
        assert f'--pytest-rc "${rc}"' in verdict_command


def test_nightly_uses_same_marker_base_as_pr_workflow():
    base = "not uinput and not vm_it"
    for text in (_full_suite(), _workflow("tests.yml")):
        assert f"base='{base}'" in text
        assert f'args=(-p no:cacheprovider -m "{base}")' in text
