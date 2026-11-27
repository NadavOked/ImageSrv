"""#4200: server/brain/invariants/README.md does not name an internal lab or handoff path.

The public repo has no `lab/` or `handoffs/`; the README names the source as an internal document of
#3653, and the ids stay readable in the `point` field of the catalog.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "server" / "brain" / "invariants" / "README.md"
# `lab/` or `handoffs/` at the start of a path; `tools/lab/` is a different path and is not caught.
INTERNAL = re.compile(r"(?<![\w./-])(?:lab|handoffs)/[\w./-]*")


def test_the_invariants_readme_names_no_internal_path():
    found = sorted({m.group(0) for m in INTERNAL.finditer(README.read_text(encoding="utf-8"))})
    assert not found, f"internal paths in {README.relative_to(ROOT).as_posix()}: {found}"


def test_the_ids_still_say_where_they_live():
    assert "`point`" in README.read_text(encoding="utf-8")


def test_the_pattern_catches_the_old_path_and_not_a_nested_one():
    assert INTERNAL.search("(`lab/decisions-map-1118.md`)")
    assert not INTERNAL.search("tools/lab/example.c")
