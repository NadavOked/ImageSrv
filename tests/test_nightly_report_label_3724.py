"""#3724: the nightly `report` job must create its label before it uses it.

`nightly-failure` existed in the private repo only. In the public repo
`gh issue create --label nightly-failure` died with "could not add label", so
the nightly was red for five nights (14-18/11) and no issue was opened.

Negative control (see the PR body): the `gh label create` step removed from
nightly.yml -> the first test fails by name.
"""

from __future__ import annotations

import re
from pathlib import Path

NIGHTLY = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "nightly.yml"
LABEL = "nightly-failure"


def _report_script() -> str:
    text = NIGHTLY.read_text(encoding="utf-8")
    assert "\n  report:\n" in text, "nightly.yml has no `report` job"
    job = text.split("\n  report:\n", 1)[1]
    lines = [line for line in job.splitlines() if not line.lstrip().startswith("#")]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines))


def test_the_report_job_creates_the_label_before_it_files_an_issue():
    script = _report_script()
    create = re.search(r"gh label create " + LABEL + r"\b[^\n]*", script)
    issue = re.search(r"gh issue create\b", script)
    assert create, f"`gh label create {LABEL}` is missing from the report job: a repo without the label gets no issue"
    assert issue, "the report job no longer files an issue"
    assert create.start() < issue.start(), "the label must be created before `gh issue create` uses it"
    assert "--force" in create.group(0), "`gh label create` without --force fails on the second night, when the label exists"


def test_a_failure_to_report_is_not_swallowed():
    """Principle 5: `|| true` on the create calls would turn 'could not report' into 'nothing to report'."""
    script = _report_script()
    for command in ("gh label create", "gh issue create"):
        for line in (ln for ln in script.splitlines() if command in ln):
            assert "|| true" not in line and "2>/dev/null" not in line, f"{command} must stay loud: {line.strip()}"
