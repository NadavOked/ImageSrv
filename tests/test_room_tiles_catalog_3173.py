"""#3173: the brain's catalog and its layers' READMEs point at no item that #3173 removed.

Moved out of `test_room_stream_tiles_3173.py` (section 5): the catalog and the READMEs are private
(`tools/git/public-manifest.list`), and a public test may not name them.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _catalog() -> list[dict]:
    return tomllib.loads((ROOT / "server/brain/catalog.toml").read_text(encoding="utf-8"))["item"]


def test_every_item_the_catalog_uses_or_senses_exists():
    items = _catalog()
    ids = {item["id"] for item in items}
    dangling = {(item["id"], key, ref) for item in items for key in ("uses", "senses")
                for ref in item.get(key) or [] if ref not in ids}
    assert dangling == set(), sorted(dangling)


def test_no_readme_of_the_layers_keeps_a_row_of_a_removed_item():
    ids = {item["id"] for item in _catalog()}
    stale = []
    for readme in sorted((ROOT / "server/brain").glob("*/README.md")):
        for n, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
            row = re.match(r"\| ([A-Z]+\d+) \|", line)
            if row and row.group(1) not in ids:
                stale.append(f"{readme.parent.name}:{n}:{row.group(1)}")
    assert stale == [], stale
