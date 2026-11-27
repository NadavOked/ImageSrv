"""Dependabot waits a week before proposing a new release (#3087)."""

import re
from pathlib import Path

DEPENDABOT = Path(__file__).resolve().parent.parent / ".github" / "dependabot.yml"


def test_dependabot_waits_a_week_before_proposing_a_new_release():
    text = DEPENDABOT.read_text(encoding="utf-8")
    assert re.search(r"^    cooldown:\n      default-days: ([7-9]|\d\d+)$", text, re.MULTILINE)
