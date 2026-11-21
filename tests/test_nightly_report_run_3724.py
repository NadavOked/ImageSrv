"""#3724 -- the nightly `report` job, RUN against a fake `gh` that behaves like the public repo.

tests/test_nightly_report_label_3724.py reads the YAML text. This one executes the job's
`run:` script on a real bash, with a `gh` on PATH that models the failure from the log
of run 44264962184: `gh issue create --label X` dies with "could not add label" when X
does not exist in the repo, and `gh issue list --label X` is empty for a missing label.
The assertions are on what the repo holds afterwards (issues, comments, labels) and on
the exit code, never on the script's wording.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from native import requires_native
from test_agent import BASH

pytestmark = requires_native(("bash", BASH))

NIGHTLY = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "nightly.yml"
LABEL = "nightly-failure"

# A repo in a directory: labels, issues, comments are files. Real gh behaviour, as far as this job uses it.
FAKE_GH = r"""#!/bin/sh
st="$GH_STATE"; sub="$1 $2"; shift 2
label=""; prev=""
for a in "$@"; do [ "$prev" = --label ] && label=$a; [ "$prev" = create ] && name=$a; prev=$a; done
has() { grep -qx -- "$1" "$st/labels" 2>/dev/null; }
case "$sub" in
  "label create")
    name=$1
    if has "$name"; then case " $* " in *" --force "*) exit 0 ;; *) echo "label already exists" >&2; exit 1 ;; esac; fi
    [ -n "${GH_LABEL_CREATE_FAILS:-}" ] && { echo "HTTP 403: label create refused" >&2; exit 1; }
    echo "$name" >> "$st/labels" ;;
  "issue list")
    has "$label" && [ -s "$st/issues" ] && head -n 1 "$st/issues"; exit 0 ;;
  "issue create")
    has "$label" || { echo "could not add label: '$label' not found" >&2; exit 1; }
    n=$(( $(wc -l < "$st/issues" 2>/dev/null || echo 0) + 1 )); echo "$n" >> "$st/issues"
    echo "https://example.test/issues/$n" ;;
  "issue comment") echo "$1" >> "$st/comments" ;;
  *) echo "fake gh: unexpected $sub" >&2; exit 97 ;;
esac
"""


def _script() -> str:
    """The report job's one `run: |` block, as YAML hands it to bash. Cut out of the text, not parsed:
    the public runner never installs PyYAML (tools/git/public-manifest.list, #3180), and an
    `import yaml` here stopped the collection of every public shard (#3891)."""
    job = NIGHTLY.read_text(encoding="utf-8").split("\n  report:\n", 1)[1]
    job = re.split(r"^  [\w-]+:\n", job, maxsplit=1, flags=re.MULTILINE)[0]     # up to the next job, if any
    assert len(re.findall(r"^ +(?:- )?run:", job, re.MULTILINE)) == 1, "the report job is expected to have one run step"
    block = re.search(r"^( +)run: \|\n((?:\1  .*\n|[ \t]*\n)*)", job, re.MULTILINE)
    assert block, "the report job's run step is expected to be a `run: |` block"
    cut = len(block.group(1)) + 2
    lines = [line[cut:] if line.strip() else "" for line in block.group(2).splitlines()]
    return "\n".join(lines).rstrip("\n") + "\n"


def _night(state: Path, **env: str) -> subprocess.CompletedProcess:
    """One run of the report job's script against the repo held in `state`."""
    bindir = state / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    (state / "run.sh").write_text(_script(), encoding="utf-8", newline="\n")
    # cwd is the repo-in-a-directory, so every path below is relative or $PWD: a Windows "C:/..." in PATH splits at the colon
    full = {"GITHUB_REPOSITORY": "owner/public", "RUN": "https://example.test/run/1", "GH_TOKEN": "x", **env}
    exports = "".join(f"export {k}={v!r}; " for k, v in full.items())
    return subprocess.run([BASH, "-c", f'export PATH="$PWD/bin:/usr/bin:$PATH" GH_STATE="$PWD"; chmod +x bin/gh; {exports}bash run.sh'],
                          cwd=str(state), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)


def _lines(state: Path, name: str) -> list[str]:
    p = state / name
    return p.read_text().split() if p.exists() else []


def test_a_repo_without_the_label_still_gets_the_issue(tmp_path):
    # public repo on 14-18/11: the label does not exist
    r = _night(tmp_path)
    assert r.returncode == 0, r.stderr
    assert _lines(tmp_path, "issues") == ["1"]
    assert LABEL in _lines(tmp_path, "labels")


def test_the_second_night_comments_and_does_not_open_a_second_issue(tmp_path):
    assert _night(tmp_path).returncode == 0
    r = _night(tmp_path)   # the label now exists: --force must not fail, and the open issue is found
    assert r.returncode == 0, r.stderr
    assert _lines(tmp_path, "issues") == ["1"]
    assert _lines(tmp_path, "comments") == ["1"]


def test_a_label_that_cannot_be_created_fails_the_job_and_opens_nothing(tmp_path):
    # principle 5: "could not report" must not look like "nothing to report"
    r = _night(tmp_path, GH_LABEL_CREATE_FAILS="1")
    assert r.returncode != 0
    assert _lines(tmp_path, "issues") == []
