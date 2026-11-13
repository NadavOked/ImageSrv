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


# #199: the characters ruff PLE2502 flags. Source files spell them as \uXXXX escapes.
BIDI_CONTROLS = {chr(c) for c in (0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))}


def test_lint_gates_bidi_controls_in_every_python_file_199():
    """PLE2502 is a blocking step and, unlike the F step, does not skip tests/."""
    code = _code("lint.yml")
    step = code.split("ruff PLE2502", 1)[1]
    assert "--select PLE2502" in step
    assert "--extend-exclude" not in step, "tests/ must be inside the PLE2502 gate"
    assert "--show-files" in step


def test_python_sources_have_no_bidi_control_characters_199():
    """A raw RLM/LRM/isolate in a .py file is invisible in review; write it as an escape."""
    root = WORKFLOWS.parent.parent
    found = []
    for path in root.rglob("*.py"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        hits = sorted({hex(ord(c)) for c in text if c in BIDI_CONTROLS})
        if hits:
            found.append(f"{path.relative_to(root).as_posix()}: {', '.join(hits)}")
    assert not found, "bidirectional control characters in: " + "; ".join(found[:20])


def test_nightly_grants_issues_write_to_the_report_job_only():
    code = _code("nightly.yml")
    top, jobs = code.split("\njobs:", 1)
    assert "issues: write" not in top
    full, report = jobs.split("\n  report:", 1)
    assert "issues: write" not in full
    assert "issues: write" in report
