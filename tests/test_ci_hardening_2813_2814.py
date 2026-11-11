"""#2813/#2814: כשל סריקה אינו ירוק, והרשאות CI מוגבלות לקריאה."""

import re
import shlex
import subprocess
import textwrap
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _steps(text):
    return re.split(r"^ *- (?=(?:name|uses):)", text, flags=re.MULTILINE)[1:]


def github_shell_argv(step_text):
    shell = re.search(r"^ *shell:[ \t]*(.*)$", step_text, re.MULTILINE)
    if shell is None:
        return ["bash", "-e"]
    assert shell.group(1).strip() == "bash", "Unexpected scan shell"
    return ["bash", "--noprofile", "--norc", "-eo", "pipefail"]


def _run_script(step_text):
    run = re.search(r"^( +)run:[ \t]*(.*)$", step_text, re.MULTILINE)
    assert run, "Scan full history has no run script"
    # גם הצורה המקורית נשלפת, כדי שהכשל יהיה בקוד היציאה ולא בפרסור.
    if run.group(2).strip() != "|":
        return run.group(2) + "\n"
    lines = []
    for line in step_text[run.end():].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= len(run.group(1)):
            break
        lines.append(line)
    script = textwrap.dedent("\n".join(lines)).strip() + "\n"
    assert script.strip(), "Scan full history has an empty run script"
    return script


def test_gitleaks_history_scan_propagates_failure_and_allows_clean_scan(tmp_path):
    text = (WORKFLOWS / "gitleaks.yml").read_text(encoding="utf-8")
    steps = [step for step in _steps(text)
             if step.splitlines()[0] == "name: Scan full history"]
    assert len(steps) == 1, "Expected one Scan full history step"
    step = steps[0]
    script = tmp_path / "scan.sh"
    log = tmp_path / "gl.log"
    script.write_text(
        _run_script(step).replace("/tmp/gl.log", shlex.quote(log.as_posix())),
        encoding="utf-8", newline="\n",
    )
    gitleaks = tmp_path / "gitleaks"
    results = {}
    for exit_code, message in ((1, "leaks found: 1"), (0, "no leaks found")):
        gitleaks.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{message}'\nexit {exit_code}\n",
            encoding="utf-8", newline="\n",
        )
        gitleaks.chmod(0o755)
        result = subprocess.run(
            github_shell_argv(step) + [str(script)], cwd=tmp_path,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert log.read_text(encoding="utf-8").strip() == message
        results[exit_code] = result.returncode
    assert results[0] == 0, "A clean scan must succeed"
    assert results[1] != 0, "tee masked the failing gitleaks exit code"


def test_tests_workflow_permissions_are_read_only():
    text = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    permissions = re.search(
        r"^permissions:[ \t]*\n((?: +[^\n]*\n|\n)*)", text, re.MULTILINE,
    )
    assert permissions, "tests.yml has no top-level permissions block"
    assert re.search(r"^  contents: read[ \t]*$", permissions.group(1), re.MULTILINE)
    assert not re.search(r"\bwrite\b", text), "tests.yml must not request write permissions"


def test_tests_workflow_checkouts_do_not_persist_credentials():
    text = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    checkouts = [step for step in _steps(text)
                 if re.search(r"^ *uses: actions/checkout@", step, re.MULTILINE)]
    assert checkouts, "tests.yml has no checkout steps"
    for step in checkouts:
        settings = re.search(
            r"^ {8}with:[ \t]*\n((?: {10}[^\n]*\n|\n)*)", step, re.MULTILINE,
        )
        assert settings, "Checkout has no with block disabling persisted credentials"
        assert re.search(
            r"^ {10}persist-credentials: false[ \t]*$", settings.group(1), re.MULTILINE,
        ), "Checkout must disable persisted credentials"
