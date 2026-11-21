"""#3192: the journal's `image_readback_derived` counts a partition only when its index was written.

lab2, 14/11 (#3178, P3): an image copy with one flipped byte in p15. Two journal lines:
`image_readback_derived -- p1, p14, p15` and, a second later, `image_readback_underived -- p15`.
Nothing was derived for p15 -- its zstd is damaged -- but `record_index` returns every index it
touched, and a partition marked `index_error` is one of them, so the reporter listed it as hashed.
The operator reading the journal was told p15 was derived (principle 5: "derived" without
positive evidence).

`result["written"]` keeps its meaning (the test_chunk_format_2533 tests hold it); the reporter
names as derived only the partitions whose value carries an index.
"""

from __future__ import annotations

import pytest

from server import app as server_app
from server.agent import readbackhash as RB
from test_chunk_format_2533 import _derive, _image


@pytest.fixture
def lines(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(server_app, "journal", lambda conn, kind, text: seen.append((kind, text)))
    return seen


GOOD = {"index": "x", "list": "y", "list_xxh64": "z", "bytes": 1}


def test_a_partition_whose_derivation_failed_is_not_listed_as_derived(lines):
    """The journal of the issue, p1 p14 p15: p15 failed and is named only in the underived line."""
    report = server_app._readback_reporter(None)
    report({"image_id": "img_x", "written": {1: GOOD, 14: GOOD, 15: {"error": "no zstd frame starts here"}},
            "problems": ["p15: no zstd frame starts here"]})
    derived = [text for kind, text in lines if kind == "image_readback_derived"]
    underived = [text for kind, text in lines if kind == "image_readback_underived"]
    assert len(derived) == 1 and "p1, p14" in derived[0] and "p15" not in derived[0], lines
    assert len(underived) == 1 and "p15" in underived[0], lines


def test_an_image_whose_only_partition_failed_has_no_derived_line_at_all(lines):
    server_app._readback_reporter(None)(
        {"image_id": "img_x", "written": {15: {"error": "bad"}}, "problems": ["p15: bad"]})
    assert [kind for kind, _ in lines] == ["image_readback_underived"], lines


def test_a_damaged_file_in_the_library_is_reported_as_underived_only(tmp_path, lines):
    """Through the real `fill`: p3 holds bytes that are not zstd frames at all."""
    lib = _image(tmp_path, blob=b"not zstd frames at all")
    RB.fill(lib, digest=_derive, report=server_app._readback_reporter(None))
    assert [kind for kind, _ in lines] == ["image_readback_underived"], lines
    assert "p3" in lines[0][1]


def test_a_good_file_is_reported_as_derived_and_not_as_underived(tmp_path, lines):
    lib = _image(tmp_path)
    RB.fill(lib, digest=_derive, report=server_app._readback_reporter(None))
    assert [kind for kind, _ in lines] == ["image_readback_derived"], lines
    assert "p3" in lines[0][1]
