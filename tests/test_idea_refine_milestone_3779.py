"""#3779: idea-refine's "opened without a milestone" check must fail on an empty milestone.

The old parse (`set -- $META` over "labels milestone items") collapsed the empty milestone
field, so `$2` became the project-item count and the check could never fail. The shell
fragment is lifted out of the YAML and run for real with a fake `gh` (the workflow itself
cannot run: private CI is off).
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "idea-refine.yml"
BASH = shutil.which("bash")


def _fragment(text):
    """From `META=$(gh issue view` to the milestone check, as the workflow writes it."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("META=$(gh issue view"))
    end = next(i for i in range(start, len(lines)) if "בלי milestone" in lines[i])
    return "\n".join(ln.strip() for ln in lines[start:end + 1])


def _run(text, fake_output):
    """Run the fragment; the fake `gh` prints what `--jq` would have produced."""
    assert BASH, "bash is required to run the workflow fragment"
    with tempfile.TemporaryDirectory() as d:
        gh = Path(d) / "gh"
        gh.write_text('#!/bin/sh\nprintf "%s\n" "$FAKE_GH_OUT"\n', encoding="utf-8", newline="\n")
        gh.chmod(0o755)
        script = "set -e\nNUM=7\nREPO=o/r\n" + _fragment(text) + "\necho PASSED\n"
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], FAKE_GH_OUT=fake_output)
        return subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, timeout=60)


# What the workflow's own jq produces: 1 label, 1 board item, and an empty / a real milestone.
# The current format is labels|items|milestone; the old one was space-separated.
def _jq_out(text, milestone):
    return f"1|1|{milestone}" if "IFS='|'" in text else f"1 {milestone} 1"


def test_empty_milestone_fails():
    text = WORKFLOW.read_text(encoding="utf-8")
    r = _run(text, _jq_out(text, ""))
    assert r.returncode != 0 and "PASSED" not in r.stdout, r.stdout + r.stderr
    assert "בלי milestone" in r.stdout


def test_full_metadata_passes():
    text = WORKFLOW.read_text(encoding="utf-8")
    r = _run(text, _jq_out(text, "v1.0"))
    assert r.returncode == 0 and "PASSED" in r.stdout, r.stdout + r.stderr


def test_no_label_still_fails():
    text = WORKFLOW.read_text(encoding="utf-8")
    out = "0|1|v1.0" if "IFS='|'" in text else "0 v1.0 1"
    r = _run(text, out)
    assert r.returncode != 0 and "בלי תווית" in r.stdout


def test_jq_expression_matches_the_parse():
    """The jq output and the parser must agree on the delimiter."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '"\(.labels|length)|\(.projectItems|length)|\(.milestone.title // "")"' in text
