"""#3287 (S63) -- the agent names the chunks its read-back found different.

Until #3287 a drawer that failed the read-back said so only in a sentence, and the
brain's K4 (server/brain/integrity.py, #3285) could never call a bad image a bad
image: ``SOURCE_FAILURE`` needs the same (partition, chunk) from two serials, in the
optional ``readback_failed_chunks`` of each target (interfaces.md, section 4).

What is pinned here (agent/lib/restore/rbchunks.sh, wired in readback.sh and
datareport.sh):
  * sample: every drawn chunk that differs, not only the first; full: the image's list
    fetched only after the XXH64 of the whole list differed, then chunk by chunk;
  * ``readback_first_bad_offset``: the first of them, K x 4MiB of that partition's stream;
  * principle 5: ``[]`` only after a check that passed (verified/sampled); a read that
    broke, a map that differs, a list that did not come down, nothing to compare with --
    no field at all, never an empty list;
  * the verdict and the error of the drawer are what they were.

The stand-ins are test_readback_agent_1805.py's.
"""

from __future__ import annotations

import json

import pytest

from agentlib import flat_lib
from test_agent import BASH, posix
from test_readback_agent_1805 import CH, FAIL, NOT_RUN, box_for, chunk_hex, disk_lines, log_of, manifest, prelude, run
from test_timeouts import run_sh
from native import requires_native

pytestmark = requires_native(("bash", BASH), "jq", why="the manifest is read with jq, as on the machine")


def _src() -> str:
    lib = posix(flat_lib())
    # not there (a tree before #3287) = the agent sends nothing: the tests below fail on what is sent
    return f". {lib}/datareport.sh\n[ ! -f {lib}/rbchunks.sh ] || . {lib}/rbchunks.sh\n"


def _drawers(box, m: dict, env: str = "") -> dict:
    (box / "m.json").write_text(json.dumps(m), encoding="utf-8", newline="\n")
    return run(box, f'{_src()}{env}\nRB_SERVER=http://s RB_IMAGE=img_x readback_drawers {posix(box / "m.json")!r} sdb sdc; echo "RC $?"')


def _entries(box) -> dict:
    out = run_sh(prelude(box) + _src() + "build_progress ses_1 aa:bb")
    return {t["dev"]: t for t in json.loads(out.strip().splitlines()[-1])["targets"]}


def _with_bad(box, name: str, i: int, bad, picks=None) -> None:
    """The disk's lines for partition ``i`` with the chunks in ``bad`` changed."""
    lines = []
    for line in disk_lines(i, picks=picks).splitlines():
        f = line.split()
        lines.append(f"c {f[1]} {chunk_hex(i, int(f[1]), 'x')}" if f[0] == "c" and int(f[1]) in bad else line)
    (box / name).write_text("\n".join(lines) + "\n", newline="\n")


def _chunks(entry: dict):
    """(partition, chunk_index) pairs, or "absent" -- the field not sent."""
    v = entry.get("readback_failed_chunks", "absent")
    return [(c["partition"], c["chunk_index"]) for c in v] if isinstance(v, list) else v


# --- sample ---------------------------------------------------------------------------


def test_sample_names_every_drawn_chunk_that_differs_and_the_first_offset(tmp_path):
    box = box_for(tmp_path)
    _with_bad(box, "rbs.sdb3", 3, {2, 4}, picks=[0, 2, 4])
    res = _drawers(box, manifest("sample"))
    assert res["t"]["sdb"]["state"] == "failed", res["raw"]
    assert res["t"]["sdb"]["error"] == f"מחיצה 3: {FAIL} (חתיכה 2 של 4MiB מתוך 5)"   # the verdict as it was
    e = _entries(box)
    assert e["sdb"].get("readback_failed_chunks") == [{"partition": 3, "chunk_index": 2},
                                                      {"partition": 3, "chunk_index": 4}], e["sdb"]   # the contract's shape
    assert e["sdb"].get("readback_first_bad_offset") == {"partition": 3, "offset": 2 * CH}, e["sdb"]


def test_a_sample_that_passed_sends_an_empty_list_and_no_offset(tmp_path):
    box = box_for(tmp_path)
    _with_bad(box, "rbs.sdb3", 3, {2}, picks=[0, 2, 4])
    res = _drawers(box, manifest("sample"))
    assert (res["t"]["sdc"]["state"], res["t"]["sdc"]["readback"]) == ("verifying", "sampled"), res["raw"]
    e = _entries(box)
    assert e["sdc"].get("readback_failed_chunks", "absent") == [], e["sdc"]   # checked, and nothing differed
    assert "readback_first_bad_offset" not in e["sdc"], e["sdc"]
    assert e["sdc"]["readback"] == "sampled"                                    # [] is not "verified": readback says what


# --- full -----------------------------------------------------------------------------


def test_full_fetches_the_list_only_after_a_mismatch_and_names_the_chunks(tmp_path):
    box = box_for(tmp_path)
    _with_bad(box, "rbx.sdb3", 3, {1, 3})
    res = _drawers(box, manifest("full"))
    assert res["t"]["sdb"]["state"] == "failed" and res["t"]["sdb"]["error"] == f"מחיצה 3: {FAIL}", res["raw"]
    assert res["t"]["sdc"]["readback"] == "verified", res["raw"]
    assert [c for c in res["calls"] if c.startswith("get ")] == ["get img_x/readback/3"], res["calls"]   # sdb's, once
    e = _entries(box)
    assert _chunks(e["sdb"]) == [(3, 1), (3, 3)], e["sdb"]
    assert e["sdb"].get("readback_first_bad_offset") == {"partition": 3, "offset": CH}, e["sdb"]
    assert e["sdc"].get("readback_failed_chunks", "absent") == [], e["sdc"]


def test_full_whose_list_did_not_come_down_names_nothing_never_an_empty_list(tmp_path):
    box = box_for(tmp_path)
    _with_bad(box, "rbx.sdb3", 3, {1})
    (box / "served.3").unlink()
    res = _drawers(box, manifest("full"), "RB_LIST_TRIES=1")
    assert res["t"]["sdb"]["error"] == f"מחיצה 3: {FAIL}", res["raw"]            # still failed, by the same words
    e = _entries(box)
    assert "readback_failed_chunks" not in e["sdb"] and "readback_first_bad_offset" not in e["sdb"], e["sdb"]
    assert "the chunks that differ are not named" in log_of(box)


def test_the_list_is_bounded_and_the_log_says_how_many_differed(tmp_path):
    box = box_for(tmp_path)
    _with_bad(box, "rbx.sdb3", 3, {0, 1, 2, 3, 4})
    _drawers(box, manifest("full"), "RB_BAD_MAX=2")
    assert _chunks(_entries(box)["sdb"]) == [(3, 0), (3, 1)]
    assert "partition 3: 5 chunk(s) differ from the image's list (the report names 2)" in log_of(box)


# --- principle 5: a check that did not run is not an empty list ------------------------


@pytest.mark.parametrize("how", ["map_unread", "flush_refused", "read_error", "map_differs"])
def test_a_read_back_that_could_not_compare_sends_no_field(tmp_path, how):
    box = box_for(tmp_path)
    if how == "map_unread":
        (box / "rb.sdb1.rc").write_text("3", newline="\n")          # fanout: the map was not read
    elif how == "flush_refused":
        (box / "flush_fail.sdb2").write_text("", newline="\n")
    elif how == "read_error":
        (box / "rb.sdb3.rc").write_text("4", newline="\n")          # the disk did not give the bytes back
    else:
        (box / "rbx.sdb1").write_text(disk_lines(1, n_bytes=8192), newline="\n")
    res = _drawers(box, manifest("full"))
    assert res["t"]["sdb"]["state"] == "failed", res["raw"]
    if how in ("map_unread", "flush_refused"):
        assert res["t"]["sdb"]["error"].startswith(NOT_RUN), res["t"]["sdb"]
    e = _entries(box)
    assert "readback_failed_chunks" not in e["sdb"], e["sdb"]
    assert e["sdc"].get("readback_failed_chunks", "absent") == [], e["sdc"]     # the same round, a check that passed


def test_nothing_to_compare_with_sends_no_field(tmp_path):
    box = box_for(tmp_path)
    res = _drawers(box, manifest("full", kinds={}))
    assert res["t"]["sdb"]["readback"] == "unverified", res["raw"]
    assert "readback_failed_chunks" not in _entries(box)["sdb"]


# --- the server reads what the agent sends (K4, #3285) ---------------------------------


def test_two_disks_with_the_same_bad_chunk_are_a_source_failure_at_the_brain(tmp_path):
    from server.brain import integrity
    box = box_for(tmp_path)
    for d in ("sdb", "sdc"):
        _with_bad(box, f"rbs.{d}3", 3, {2}, picks=[0, 2, 4])
    _drawers(box, manifest("sample"))
    e = _entries(box)
    targets = tuple(integrity.target("aa:bb", {**e[d], "serial": f"S-{d}"}) for d in ("sdb", "sdc"))
    assert [t.failed_chunks for t in targets] == [((3, 2),), ((3, 2),)], targets
    assert [d.verdict for d in integrity.integrity(targets)] == [integrity.SOURCE_FAILURE] * 2
