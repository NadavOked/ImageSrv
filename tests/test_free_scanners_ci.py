"""13/11: the free public scanners (Scorecard, zizmor, OSV-Scanner, actionlint,
Lighthouse) and the Codecov upload in tests.yml.

Nadav, 13/11: everything that is free and helps us is approved. The private repo
runs no CI: its workflows are switched off one by one (`disabled_manually`), and a
NEW workflow file is born active there. So every job of a new workflow carries a
`github.repository` condition -- without it the first push of this branch would
spend the private repo's minutes. These tests read the YAML as text, like
test_ci_hardening_2813_2814.py (no YAML parser in the requirements).
"""

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
NEW = ("scorecard.yml", "zizmor.yml", "osv-scanner.yml", "actionlint.yml", "lighthouse.yml")
PUBLIC = "github.repository == 'NadavOked/ImageSrv'"
PINNED = re.compile(r"^\s*(?:- )?uses: ([\w.-]+/[\w./-]+)@([0-9a-f]{40}) # v?\d+\.\d+\.\d+\s*$")


def _text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _jobs(text):
    """{job id: job body}, split on the two-space job keys under `jobs:`."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([\w-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


@pytest.mark.parametrize("name", NEW)
def test_every_job_of_a_new_workflow_runs_only_in_the_public_repo(name):
    jobs = _jobs(_text(name))
    assert jobs, f"{name}: no jobs found"
    for job, body in jobs.items():
        assert re.search(rf"^    if: {re.escape(PUBLIC)}$", body, re.MULTILINE), (
            f"{name}:{job} has no `if: {PUBLIC}` -- it would run in the private repo")


# #3087: the workflows that predate the rule are held to it too -- a floating
# `@v7` tag is movable, so the code CI runs is not necessarily the code that was reviewed.
PUBLIC_OLD = ("codeql.yml", "gitleaks.yml", "lint.yml", "nightly.yml", "semgrep.yml",
              "shellcheck.yml", "tests.yml")


@pytest.mark.parametrize("name", (*NEW, *PUBLIC_OLD))
def test_every_action_of_a_public_workflow_is_pinned_to_a_full_sha(name):
    lines = [line for line in _text(name).splitlines()
             if re.match(r"^\s*(?:- )?uses:", line)]
    assert lines, f"{name}: no `uses:` lines found -- the check would pass on nothing"
    for line in lines:
        assert PINNED.match(line), f"{name}: not pinned to a full SHA with a version comment: {line.strip()}"


@pytest.mark.parametrize("name", NEW)
def test_a_new_workflow_asks_for_no_write_permission_at_the_top(name):
    text = _text(name)
    top = re.search(r"^permissions:[ \t]*\n((?: +[^\n]*\n)*)", text, re.MULTILINE)
    assert top, f"{name}: no top-level permissions block"
    assert re.search(r"^  contents: read[ \t]*$", top.group(1), re.MULTILINE)
    assert "write" not in top.group(1), f"{name}: write permission at the workflow level"


@pytest.mark.parametrize("name", (*NEW, *PUBLIC_OLD))
def test_every_checkout_of_a_public_workflow_drops_the_token(name):
    steps = re.split(r"^ *- (?=(?:name|uses):)", _text(name), flags=re.MULTILINE)[1:]
    checkouts = [s for s in steps if "uses: actions/checkout@" in s]
    assert checkouts or name in NEW, f"{name}: no checkout step found -- the check would pass on nothing"
    for step in checkouts:
        assert re.search(r"^ +persist-credentials: false\s*$", step, re.MULTILINE), name


def test_dependabot_waits_a_week_before_proposing_a_new_release():
    text = (WORKFLOWS.parent / "dependabot.yml").read_text(encoding="utf-8")
    assert re.search(r"^    cooldown:\n      default-days: ([7-9]|\d\d+)$", text, re.MULTILINE)


def test_the_actionlint_binary_is_verified_and_shellcheck_must_be_present():
    text = _text("actionlint.yml")
    sha = re.search(r'^  ACTIONLINT_SHA256: "([0-9a-f]{64})"$', text, re.MULTILINE)
    assert sha, "no pinned sha256 for the actionlint tarball"
    assert 'sha256sum -c -' in text
    # Without shellcheck on PATH actionlint skips every `run:` block and says clean.
    assert "command -v shellcheck\n" in text
    # #3086: no exemptions at all -- SC2209 was fixed at the root, so any `-ignore`
    # would hide a new finding in every workflow.
    assert "-ignore" not in text
    assert re.findall(r"-ignore '([^']*)'", text) == []


def test_shellcheck_yml_quotes_the_dialect_it_assigns():
    """`sh=bash` reads to shellcheck (SC2209) as an attempt to run bash; it is a string."""
    text = _text("shellcheck.yml")
    assert "case \"$first\" in *bash*) sh='bash' ;; *) sh='sh' ;; esac" in text
    assert not re.search(r"sh=(bash|sh)(?!')", text)


def test_osv_does_not_fail_on_the_documented_pins_and_does_not_guess_versions():
    text = _text("osv-scanner.yml")
    assert re.search(r"^      fail-on-vuln: false$", text, re.MULTILINE)
    assert "--no-resolve" in text and "--recursive" in text


def test_zizmor_is_pinned_and_uploads_sarif():
    text = _text("zizmor.yml")
    assert re.search(r'^          version: "\d+\.\d+\.\d+"$', text, re.MULTILINE), "zizmor floats on `latest`"
    assert "advanced-security: true" in text


def test_scorecard_keeps_the_publish_restrictions():
    """publish_results forbids env/defaults and `run:` steps in the job."""
    text = _text("scorecard.yml")
    assert "publish_results: true" in text
    assert not re.search(r"^(env|defaults):", text, re.MULTILINE)
    assert "run:" not in text.split("\njobs:\n", 1)[1]
    assert "id-token: write" in _jobs(text)["scorecard"]


def _summary_script():
    text = _text("lighthouse.yml")
    step = text.split("- name: Summary", 1)[1]
    body = step.split("python - <<'EOF'", 1)[1].split("\n", 1)[1].split("\n          EOF", 1)[0]
    return textwrap.dedent(body)


def test_a_lighthouse_run_without_reports_is_red_not_an_empty_table(tmp_path):
    """Principle 5: "not measured" must not read as a good score."""
    script = tmp_path / "summary.py"
    script.write_text(_summary_script(), encoding="utf-8", newline="\n")
    env = dict(os.environ, RESULTS=str(tmp_path / "none"))
    proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)
    assert proc.returncode != 0
    assert "found 0" in proc.stderr


def test_a_lighthouse_run_with_reports_prints_their_scores(tmp_path):
    import json
    results = tmp_path / "res"
    results.mkdir()
    for i, url in enumerate(("http://x/console/", "http://x/console/monitor.html")):
        cats = {c: {"score": 0.5 + i / 10} for c in ("performance", "accessibility", "best-practices", "seo")}
        (results / f"lhr-{i}.json").write_text(json.dumps({"finalDisplayedUrl": url, "categories": cats}),
                                              encoding="utf-8")
    script = tmp_path / "summary.py"
    script.write_text(_summary_script(), encoding="utf-8", newline="\n")
    env = dict(os.environ, RESULTS=str(results))
    proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert "| http://x/console/ | 50 | 50 | 50 | 50 |" in proc.stdout
    assert "| http://x/console/monitor.html | 60 | 60 | 60 | 60 |" in proc.stdout


def test_coverage_never_decides_the_tests_verdict():
    """The Codecov steps may fail visibly, but not colour a shard or the gate."""
    text = _text("tests.yml")
    shard = _jobs(text)["shard"]
    for name in ("Coverage report (3.13)", "Coverage upload to Codecov (3.13)"):
        step = shard.split(f"- name: {name}\n", 1)
        assert len(step) == 2, f"tests.yml: step {name!r} is gone"
        head = step[1].split("\n      - ", 1)[0]
        assert "continue-on-error: true" in head, name
        assert "if: matrix.python-version == '3.13'" in head, name
    # The coverage steps come after the shard's evidence is handed to the merge.
    assert shard.index("Hand the evidence to the merge") < shard.index("Coverage report (3.13)")
    assert "fail_ci_if_error: true" in shard
    # The merge job (`pytest (3.12)` / `pytest (3.13)`) knows nothing of coverage.
    assert "cov" not in _jobs(text)["pytest"]
