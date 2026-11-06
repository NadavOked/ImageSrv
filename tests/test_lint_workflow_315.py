"""#315: the free static-analysis workflow stays honest, and nightly stays least-privilege.

lint.yml gates only what is clean today. A gate that can be red-but-hidden
(`continue-on-error`, `|| true`) is "looks like success" (principle 5), so
the file must not contain one; the tool version is pinned, because an
unpinned ruff changes its rule set under a green run.
"""
import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _code(name):
    """The workflow without comment lines (comments may mention the banned words)."""
    return "\n".join(line for line in _text(name).splitlines() if not line.lstrip().startswith("#"))


def test_lint_cannot_hide_a_finding():
    code = _code("lint.yml")
    assert "continue-on-error" not in code
    assert "|| true" not in code


def test_lint_pins_ruff_and_is_read_only():
    code = _code("lint.yml")
    assert re.search(r'RUFF_VERSION:\s*"\d+\.\d+\.\d+"', code), "ruff version is not pinned"
    assert 'pip install "ruff==${RUFF_VERSION}"' in code
    top = code.split("\njobs:", 1)[0]
    assert re.search(r"^permissions:\n  contents: read\n(?!  \S)", top, re.M), \
        "top-level permissions must be exactly `contents: read`"


def test_lint_checks_that_it_saw_files():
    """ruff exits 0 on zero files; the workflow must count them."""
    assert "--show-files" in _code("lint.yml")


def test_nightly_grants_issues_write_to_the_report_job_only():
    code = _code("nightly.yml")
    top, jobs = code.split("\njobs:", 1)
    assert "issues: write" not in top
    full, report = jobs.split("\n  report:", 1)
    assert "issues: write" not in full
    assert "issues: write" in report
