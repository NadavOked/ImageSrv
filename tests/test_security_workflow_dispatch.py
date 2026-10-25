"""Security scanners can be run on a private security-fix branch."""

from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def test_private_security_scanners_allow_manual_dispatch():
    for name in ("codeql.yml", "gitleaks.yml"):
        workflow = (WORKFLOWS / name).read_text(encoding="utf-8")
        on_block = workflow.split("on:", 1)[1].split("permissions:", 1)[0]
        assert "workflow_dispatch:" in on_block, f"{name} cannot scan a private fix branch"
